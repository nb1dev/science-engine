#!/usr/bin/env python3
"""
update_population_thresholds.py — Calculate population quartiles from all samples

Scans analysis/ directory for all processed samples, calculates quartiles
for key metrics (Shannon, Pielou, guild abundances, CLR ratios), and writes
to knowledge_base/population_thresholds.json.

Designed to run:
  - As a background watcher (recalculates every 7 days)
  - Or manually: python3 update_population_thresholds.py --once

All scripts that use diversity/abundance thresholds read from the output file.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

WORK_DIR = os.environ.get("WORK_DIR", "/Users/pnovikova/Documents/work")
ANALYSIS_DIR = os.path.join(WORK_DIR, 'analysis')
OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'knowledge_base', 'population_thresholds.json'
)

# Recalculate every 7 days (in seconds)
INTERVAL_SECONDS = 7 * 24 * 60 * 60


def _percentile(sorted_vals, pct):
    """Calculate percentile from sorted list."""
    if not sorted_vals:
        return 0
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def _parse_metrics_file(filepath):
    """Parse key metrics from a _only_metrics.txt file."""
    metrics = {}
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if ':' not in line:
                    continue
                key, _, val = line.partition(':')
                key = key.strip()
                val = val.strip()
                try:
                    metrics[key] = float(val)
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"Error parsing {filepath}: {e}")
    return metrics


def _parse_guild_csv(filepath):
    """Parse guild abundances from _functional_guild.csv."""
    guilds = {}
    try:
        with open(filepath) as f:
            header = f.readline().strip().split(',')
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 3:
                    guild_name = parts[0].strip('"').strip()
                    try:
                        abundance = float(parts[1])
                        guilds[guild_name] = abundance
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return guilds


def collect_all_metrics():
    """Scan all samples and collect metric values."""
    all_shannon = []
    all_pielou = []
    all_cur = []
    all_fcr = []
    all_mdr = []
    all_ppr = []
    guild_values = {}
    sample_count = 0

    for batch in sorted(os.listdir(ANALYSIS_DIR)):
        batch_dir = os.path.join(ANALYSIS_DIR, batch)
        if not os.path.isdir(batch_dir) or batch.startswith('.') or 'knowledge' in batch or 'prototype' in batch:
            continue

        for sample in sorted(os.listdir(batch_dir)):
            if sample.startswith('.') or sample.startswith('SRS') or not sample[0].isdigit():
                continue

            metrics_file = os.path.join(batch_dir, sample, 'only_metrics', f'{sample}_only_metrics.txt')
            if not os.path.exists(metrics_file):
                continue

            metrics = _parse_metrics_file(metrics_file)
            if not metrics:
                continue

            sample_count += 1

            if 'Shannon' in metrics:
                all_shannon.append(metrics['Shannon'])
            if 'Pielou evenness' in metrics:
                all_pielou.append(metrics['Pielou evenness'])
            elif 'Pielou' in metrics:
                all_pielou.append(metrics['Pielou'])
            if 'CUR' in metrics:
                all_cur.append(metrics['CUR'])
            if 'FCR' in metrics:
                all_fcr.append(metrics['FCR'])
            if 'MDR' in metrics:
                all_mdr.append(metrics['MDR'])
            if 'PPR' in metrics:
                all_ppr.append(metrics['PPR'])

            # Guild abundances from CSV
            guild_csv = os.path.join(batch_dir, sample, 'only_metrics', f'{sample}_functional_guild.csv')
            guilds = _parse_guild_csv(guild_csv)
            for gname, abund in guilds.items():
                if gname not in guild_values:
                    guild_values[gname] = []
                guild_values[gname].append(abund)

    return {
        'sample_count': sample_count,
        'shannon': sorted(all_shannon),
        'pielou': sorted(all_pielou),
        'cur': sorted(all_cur),
        'fcr': sorted(all_fcr),
        'mdr': sorted(all_mdr),
        'ppr': sorted(all_ppr),
        'guilds': {k: sorted(v) for k, v in guild_values.items()},
    }


def compute_thresholds(data):
    """Compute quartiles and write thresholds JSON."""
    def _quartiles(sorted_vals):
        if not sorted_vals:
            return {'q25': 0, 'q50': 0, 'q75': 0, 'min': 0, 'max': 0, 'n': 0}
        return {
            'q25': round(_percentile(sorted_vals, 25), 3),
            'q50': round(_percentile(sorted_vals, 50), 3),
            'q75': round(_percentile(sorted_vals, 75), 3),
            'min': round(min(sorted_vals), 3),
            'max': round(max(sorted_vals), 3),
            'n': len(sorted_vals),
        }

    thresholds = {
        '_doc': 'Population quartiles computed from all processed samples. Updated weekly. Used by scoring, overview_fields, root_causes_fields.',
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'sample_count': data['sample_count'],
        'shannon': _quartiles(data['shannon']),
        'pielou': _quartiles(data['pielou']),
        'clr_ratios': {
            'CUR': _quartiles(data['cur']),
            'FCR': _quartiles(data['fcr']),
            'MDR': _quartiles(data['mdr']),
            'PPR': _quartiles(data['ppr']),
        },
        'guild_abundances': {
            gname: _quartiles(vals)
            for gname, vals in data['guilds'].items()
        },
    }

    return thresholds


def update_thresholds():
    """Main function: collect metrics, compute quartiles, write JSON."""
    logger.info("Scanning samples...")
    data = collect_all_metrics()
    logger.info(f"  Found {data['sample_count']} samples with metrics")

    if data['sample_count'] == 0:
        logger.warning("  No samples found — skipping update")
        return

    thresholds = compute_thresholds(data)

    # Write to JSON
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(thresholds, f, indent=2)

    logger.info(f"  Saved: {OUTPUT_PATH}")
    logger.info(f"  Shannon: Q25={thresholds['shannon']['q25']}, Q50={thresholds['shannon']['q50']}, Q75={thresholds['shannon']['q75']}")
    logger.info(f"  Pielou:  Q25={thresholds['pielou']['q25']}, Q50={thresholds['pielou']['q50']}, Q75={thresholds['pielou']['q75']}")

    return thresholds


def run_watcher():
    """Run in watch mode — recalculate every 7 days."""
    logger.info("Population Thresholds Watcher started")
    logger.info(f"  Analysis dir: {ANALYSIS_DIR}")
    logger.info(f"  Output: {OUTPUT_PATH}")
    logger.info(f"  Interval: {INTERVAL_SECONDS // 86400} days")

    while True:
        try:
            update_thresholds()
        except Exception as e:
            logger.error(f"Error updating thresholds: {e}")

        logger.info(f"Next update in {INTERVAL_SECONDS // 86400} days")
        time.sleep(INTERVAL_SECONDS)


if __name__ == '__main__':
    if '--once' in sys.argv:
        update_thresholds()
    else:
        run_watcher()
