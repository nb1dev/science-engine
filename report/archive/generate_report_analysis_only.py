#!/usr/bin/env python3
"""
generate_report_analysis_only.py — Analysis-only orchestrator (no LLM calls)

Reads pipeline outputs, computes scores and deterministic fields, and outputs:
  - microbiome_analysis_master_{sample_id}.json  — master file (all metrics,
    scores, root causes, action plan, debug info; narrative fields are empty
    and will be populated by generate_health_report.py)
  - microbiome_platform_{sample_id}.json         — platform-ready API payload

This script is the first step in the split pipeline:
  Step 1 (this script): generate_report_analysis_only.py  → analysis JSONs
  Step 2 (unchanged):   generate_formulation.py            → formulation JSON
  Step 3:               generate_health_report.py          → interpretation JSON + HTML

No LLM calls are made. The output JSONs are clean, complete, and reusable by
downstream scripts without incurring any API cost.

Usage:
  python generate_report_analysis_only.py --sample-dir /path/to/analysis/batch/sample/
  python generate_report_analysis_only.py --batch-dir /path/to/analysis/batch/
"""

import argparse
import json
import logging
import os
import sys
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parse_metrics import parse_all
from scoring import compute_score
from overview_fields import compute_overview_fields
from root_causes_fields import compute_root_causes_fields
from action_plan_fields import compute_action_plan_fields
from platform_mapping import build_platform_json

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


# Sentinel value used to mark all narrative fields — downstream scripts
# (generate_health_report.py) detect this string to know a field needs to
# be populated via LLM.
_NARRATIVE_PENDING = ''


def _merge_guild_interpretations_empty(bacterial_groups: dict) -> dict:
    """
    Return guild data with empty narrative fields.
    scientific_interpretation and client_interpretation are intentionally empty —
    they are populated by generate_health_report.py in the interpretation phase.
    """
    result = {}
    for gname, gdata in bacterial_groups.items():
        entry = dict(gdata)
        entry['scientific_interpretation'] = _NARRATIVE_PENDING
        entry['client_interpretation'] = _NARRATIVE_PENDING
        result[gname] = entry
    return result


def build_all(sample_dir: str) -> tuple:
    """
    Build both output JSONs for a single sample (analysis only, no LLM).

    Returns:
        (microbiome_analysis, platform_json, data)
    """
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    logger.info(f"Processing sample: {sample_id}")

    # ── Step 1: Parse ──
    logger.info("  Parsing metrics...")
    data = parse_all(sample_dir)

    # ── Step 2: Score ──
    logger.info("  Computing overall score...")
    score_result = compute_score(data)

    # ── Step 3: Deterministic fields ──
    logger.info("  Computing deterministic fields...")
    fields = compute_overview_fields(data)

    # ── Step 4: Root causes & action plan ──
    logger.info("  Computing root causes & action plan...")
    root_causes = compute_root_causes_fields(data, score_result['total'], fields=fields)
    action_plan = compute_action_plan_fields(
        data, score_result['total'], fields.get('vitamin_risks', {}),
        sample_dir=sample_dir, sample_id=sample_id
    )

    # ── Step 5: Assemble microbiome_analysis_master.json ──
    # All narrative text fields are set to empty string.
    # generate_health_report.py reads this JSON and fills those fields
    # via its own LLM calls, persisting results to a separate
    # health_report_interpretations_{id}.json cache.
    logger.info("  Assembling microbiome analysis JSON...")

    microbiome_analysis = {
        'report_metadata': {
            'sample_id': sample_id,
            'report_date': datetime.now().strftime('%Y-%m-%d'),
            'algorithm_version': '3.0',
            'generated_at': datetime.now().isoformat(),
            'llm_model': None,  # No LLM used in this script
            'narrative_populated': False,  # Flag: narratives not yet generated
        },

        'executive_summary': {
            'overall_pattern': _NARRATIVE_PENDING,
            'key_finding': _NARRATIVE_PENDING,
            'priority_interventions': fields['key_opportunities'],
            'recovery_potential': _NARRATIVE_PENDING,
        },

        'overall_score': {
            'total': score_result['total'],
            'band': score_result['band'],
            'description': score_result['band_description'],
            'pillars': score_result['pillars'],
            'score_drivers': score_result.get('score_drivers', {}),
            'details': score_result['details'],
        },

        'ecological_metrics': {
            'diversity': {
                'shannon': {
                    'value': data.get('Shannon'),
                    'interpretation': f"{'High' if (data.get('Shannon') or 0) >= 3.0 else 'Moderate' if (data.get('Shannon') or 0) >= 2.0 else 'Low'} species diversity",
                },
                'pielou_evenness': {
                    'value': data.get('Pielou'),
                    'zone': 'green' if (data.get('Pielou') or 0) >= 0.70 else 'amber' if (data.get('Pielou') or 0) >= 0.40 else 'red',
                    'interpretation': fields['diversity_resilience']['label'] + ' community resilience',
                },
            },
            'resilience': {
                'avg_guild_evenness': score_result['details']['P2']['avg_guild_J'],
                'guilds_counted': score_result['details']['P2']['guilds_counted'],
            },
            'state': {
                'overall_balance': fields['overall_balance'],
                'diversity_resilience': fields['diversity_resilience'],
            },
        },

        'safety_profile': {
            'dysbiosis_markers': {
                taxon: {
                    'abundance': abund,
                    'status': 'Not detected' if abund == 0 else f'Detected at {abund:.2f}%',
                }
                for taxon, abund in data.get('dysbiosis', {}).items()
            },
            'M_smithii_abundance': data.get('smithii_abundance', 0),
            'bcfa_pathways_detected': data.get('bcfa_pathway_count', 0),
        },

        'metabolic_function': {
            'dials': {
                dial_key: {
                    'state': dial_data.get('state', ''),
                    'label': dial_data.get('label', ''),
                    'value': dial_data.get('value'),
                    'raw_value': dial_data.get('raw_value'),
                    'metric': dial_data.get('metric', ''),
                    'description': dial_data.get('description', ''),
                }
                for dial_key, dial_data in fields['metabolic_dials'].items()
            },
            'interpretation': _NARRATIVE_PENDING,
        },

        'vitamin_synthesis': {
            **fields['vitamin_risks'],
            'interpretation': _NARRATIVE_PENDING,
        },

        'bacterial_groups': _merge_guild_interpretations_empty(
            fields['bacterial_groups']
        ),

        'key_messages': {
            'strengths': fields['key_strengths'],
            'opportunities': fields['key_opportunities'],
            'good_news': _NARRATIVE_PENDING,
            'possible_impacts': [],
            'is_something_wrong': _NARRATIVE_PENDING,
            'can_this_be_fixed': _NARRATIVE_PENDING,
        },

        'guild_scenarios': fields.get('guild_scenarios', []),
        'root_causes': root_causes,
        'action_plan': action_plan,

        '_debug': {
            'files_found': data.get('files_found', {}),
            'raw_metrics': {
                k: data.get(k) for k in [
                    'GMWI2', 'HF', 'wGMWI2', 'BR', 'SB', 'z_score',
                    'Shannon', 'Pielou', 'CUR', 'FCR', 'MDR', 'PPR',
                    'CUR_label', 'FCR_label', 'MDR_label', 'PPR_label',
                    'FB_ratio', 'smithii_abundance', 'bcfa_pathway_count',
                ]
            },
            'guild_summary': {
                gname: {
                    'abundance': gdata['abundance'],
                    'redundancy': gdata['redundancy'],
                    'clr': gdata.get('clr'),
                }
                for gname, gdata in data.get('guilds', {}).items()
            },
            'dysbiosis': data.get('dysbiosis', {}),
        },
    }

    # ── Step 6: Build platform JSON ──
    logger.info("  Building platform JSON...")

    analysis_for_platform = {
        'report_metadata': microbiome_analysis['report_metadata'],
        'overview': {
            'gut_health_glance': {
                'summary_sentence': _NARRATIVE_PENDING,
                'overall_score': microbiome_analysis['overall_score'],
            },
            'whats_happening': {
                'overall_balance': fields['overall_balance'],
                'diversity_resilience': fields['diversity_resilience'],
                'key_strengths': fields['key_strengths'],
                'key_opportunities': fields['key_opportunities'],
                'summary_sentence': _NARRATIVE_PENDING,
            },
            'metabolic_dials': microbiome_analysis['metabolic_function']['dials'],
            'what_this_means': microbiome_analysis['key_messages'],
        },
        'bacterial_groups': microbiome_analysis['bacterial_groups'],
        'vitamin_synthesis': microbiome_analysis['vitamin_synthesis'],
        'key_messages': microbiome_analysis['key_messages'],
    }

    # Build minimal narratives dict (no LLM text — platform fields requiring
    # narrative text will be empty; platform_mapping must tolerate empty strings)
    empty_narratives = {
        'summary_sentence': _NARRATIVE_PENDING,
        'whats_happening_summary': _NARRATIVE_PENDING,
        'good_news': {},
        'possible_impacts': [],
        'is_something_wrong': _NARRATIVE_PENDING,
        'can_this_be_fixed': _NARRATIVE_PENDING,
        'metabolic_interpretation': _NARRATIVE_PENDING,
        'vitamin_interpretation': _NARRATIVE_PENDING,
        'root_causes_diagnosis': _NARRATIVE_PENDING,
        'root_causes_insights': [],
        'root_causes_conclusion': _NARRATIVE_PENDING,
        'guild_interpretations': {},
    }

    platform_json = build_platform_json(
        analysis_for_platform,
        data=data,
        root_causes=root_causes,
        action_plan=action_plan,
        narratives=empty_narratives,
    )

    logger.info(f"  Done! Score: {score_result['total']}/100 [{score_result['band']}]")
    return microbiome_analysis, platform_json, data


def process_sample(sample_dir: str, output_path: str = None) -> str:
    """Process a single sample and save JSON outputs to sample directory."""
    microbiome_analysis, platform_json, data = build_all(sample_dir)

    sample_id = microbiome_analysis['report_metadata']['sample_id']

    # Determine output directory
    if output_path is None:
        output_dir = os.path.join(sample_dir, 'reports', 'reports_json')
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f'microbiome_analysis_master_{sample_id}.json')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Save microbiome_analysis_master.json
    with open(output_path, 'w') as f:
        json.dump(microbiome_analysis, f, indent=2)
    logger.info(f"  Saved: {output_path}")

    # Save platform.json alongside
    platform_path = os.path.join(
        os.path.dirname(output_path), f'microbiome_platform_{sample_id}.json'
    )
    with open(platform_path, 'w') as f:
        json.dump(platform_json, f, indent=2)
    logger.info(f"  Saved: {platform_path}")

    return output_path


def process_batch(batch_dir: str, **kwargs):
    """Process all samples in a batch directory."""
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, '*')))
    sample_dirs = [d for d in sample_dirs if os.path.isdir(d) and not d.endswith('.DS_Store')]

    logger.info(f"Batch processing: {len(sample_dirs)} samples in {batch_dir}")
    results = []

    for sample_dir in sample_dirs:
        sample_id = os.path.basename(sample_dir)
        metrics_file = os.path.join(sample_dir, 'bioinformatics', 'only_metrics', f'{sample_id}_only_metrics.txt')
        metrics_file_legacy = os.path.join(sample_dir, 'only_metrics', f'{sample_id}_only_metrics.txt')
        if not os.path.exists(metrics_file) and not os.path.exists(metrics_file_legacy):
            logger.warning(f"  Skipping {sample_id} — no metrics file")
            continue
        try:
            path = process_sample(sample_dir, **kwargs)
            results.append({'sample_id': sample_id, 'status': 'success', 'output': path})
        except Exception as e:
            logger.error(f"  Failed {sample_id}: {e}")
            results.append({'sample_id': sample_id, 'status': 'error', 'error': str(e)})

    success = sum(1 for r in results if r['status'] == 'success')
    errors = sum(1 for r in results if r['status'] == 'error')
    logger.info(f"\nBatch complete: {success} success, {errors} errors out of {len(results)} samples")
    return results


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Generate structured microbiome analysis JSON (analysis only, no LLM).\n\n'
            'This is Step 1 of the split pipeline:\n'
            '  Step 1: generate_report_analysis_only.py  → microbiome_analysis_master + platform JSON\n'
            '  Step 2: generate_formulation.py            → formulation_master JSON\n'
            '  Step 3: generate_health_report.py          → interpretation JSON cache + HTML\n\n'
            'No LLM calls are made. All narrative fields in the output JSONs are empty\n'
            'and will be populated by generate_health_report.py in Step 3.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--output', help='Custom output path (single sample only)')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.sample_dir:
        path = process_sample(args.sample_dir, output_path=args.output)
        print(f"\n✅ Analysis JSON saved: {path}")
    elif args.batch_dir:
        process_batch(args.batch_dir)


if __name__ == '__main__':
    main()
