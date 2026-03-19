#!/usr/bin/env python3
"""
generate_report.py — Main orchestrator for automated microbiome report JSON

Reads pipeline outputs, computes scores and deterministic fields, generates
LLM narratives via AWS Bedrock, and outputs:
  - {sample_id}_microbiome_analysis.json  — THE master file (all metrics,
    scores, both scientific & simple interpretations, narratives, root causes,
    action plan, debug info)
  - {sample_id}_platform.json             — platform-ready API payload (5 tabs,
    client-facing only)

Usage:
  python generate_report.py --sample-dir /path/to/analysis/batch/sample/
  python generate_report.py --sample-dir /path/to/sample/ --no-llm
  python generate_report.py --batch-dir /path/to/analysis/batch/
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
from narratives import generate_all_narratives, generate_placeholder_narratives
from root_causes_fields import compute_root_causes_fields
from action_plan_fields import compute_action_plan_fields
from platform_mapping import build_platform_json

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def _merge_guild_interpretations(bacterial_groups: dict, guild_interps: dict) -> dict:
    """Merge LLM-generated dual interpretations into bacterial group data."""
    result = {}
    for gname, gdata in bacterial_groups.items():
        entry = dict(gdata)  # copy deterministic data
        interp = guild_interps.get(gname, {})
        entry['scientific_interpretation'] = interp.get('scientific', '[LLM skipped]')
        entry['client_interpretation'] = interp.get('client', '[LLM skipped]')
        result[gname] = entry
    return result


def _extract_narratives_from_existing(sample_dir: str, sample_id: str) -> dict | None:
    """
    Pull LLM-generated narrative text from an already-existing master JSON.

    Returns a narratives dict (same shape as generate_all_narratives output) if a
    valid master JSON exists with non-placeholder narrative content, else None.

    Use case: re-running deterministic pipeline (new thresholds, scoring changes,
    dial recalibration) without paying for LLM calls when the narrative text hasn't
    changed.  Pass --reuse-narratives to enable.
    """
    master_path = os.path.join(
        sample_dir, 'reports', 'reports_json',
        f'microbiome_analysis_master_{sample_id}.json'
    )
    if not os.path.exists(master_path):
        logger.info(f"  No existing master JSON found at {master_path} — will call LLM")
        return None

    try:
        with open(master_path) as f:
            existing = json.load(f)
    except Exception as e:
        logger.warning(f"  Could not read existing master JSON: {e} — will call LLM")
        return None

    # Validate that narratives are real (not placeholders or empty)
    exec_sum = existing.get('executive_summary', {})
    overall_pattern = exec_sum.get('overall_pattern', {})
    sci_text = overall_pattern.get('scientific', '') if isinstance(overall_pattern, dict) else str(overall_pattern)
    if not sci_text or sci_text.startswith('[LLM') or sci_text.startswith('[placeholder'):
        logger.info(f"  Existing master JSON has placeholder narratives — will call LLM")
        return None

    # Extract narrative fields back into the standard narratives dict shape
    narratives = {}

    # summary_sentence / whats_happening_summary
    narratives['summary_sentence'] = exec_sum.get('overall_pattern', '')
    narratives['whats_happening_summary'] = exec_sum.get('key_finding', '')
    narratives['can_this_be_fixed'] = exec_sum.get('recovery_potential', '')

    # key_messages fields
    km = existing.get('key_messages', {})
    narratives['good_news'] = km.get('good_news', {})
    narratives['possible_impacts'] = km.get('possible_impacts', [])
    narratives['is_something_wrong'] = km.get('is_something_wrong', '')
    if not narratives['can_this_be_fixed']:
        narratives['can_this_be_fixed'] = km.get('can_this_be_fixed', '')

    # metabolic + vitamin interpretation
    mf = existing.get('metabolic_function', {})
    narratives['metabolic_interpretation'] = mf.get('interpretation', '')
    vs = existing.get('vitamin_synthesis', {})
    narratives['vitamin_interpretation'] = vs.get('interpretation', '')

    # guild interpretations (scientific + client per guild)
    guild_interps = {}
    for gname, gdata in existing.get('bacterial_groups', {}).items():
        guild_interps[gname] = {
            'scientific': gdata.get('scientific_interpretation', ''),
            'client': gdata.get('client_interpretation', ''),
        }
    narratives['guild_interpretations'] = guild_interps

    # root causes diagnosis
    rc = existing.get('root_causes', {})
    narratives['root_causes_diagnosis'] = rc.get('primary_diagnosis', '') if isinstance(rc, dict) else ''

    logger.info(f"  ✓ Reusing existing LLM narratives (0 API calls)")
    return narratives


def build_all(sample_dir: str, no_llm: bool = False,
              reuse_narratives: bool = False,
              model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
              region: str = 'eu-west-1') -> tuple:
    """
    Build both output JSONs for a single sample.

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

    # ── Step 4: LLM narratives ──
    # Three modes:
    #   --no-llm:           Skip entirely, use placeholder strings (for dev/testing)
    #   --reuse-narratives: Extract from existing master JSON if it has valid content.
    #                       Falls back to LLM call if no valid JSON found.
    #                       Use for: re-running deterministic changes (new thresholds,
    #                       scoring, dial recalibration) without paying for API calls.
    #   (default):          Call LLM via Bedrock.
    if no_llm:
        logger.info("  Skipping LLM narratives (--no-llm)")
        narratives = generate_placeholder_narratives()
    elif reuse_narratives:
        logger.info("  --reuse-narratives: trying to extract from existing master JSON...")
        narratives = _extract_narratives_from_existing(sample_dir, sample_id)
        if narratives is None:
            # No valid existing JSON — fall back to LLM
            logger.info(f"  Fallback: generating narratives via Bedrock ({model_id})...")
            narratives = generate_all_narratives(data, score_result, fields, model_id, region)
    else:
        logger.info(f"  Generating narratives via Bedrock ({model_id})...")
        narratives = generate_all_narratives(data, score_result, fields, model_id, region)

    # ── Step 5: Root causes & action plan ──
    logger.info("  Computing root causes & action plan...")
    root_causes = compute_root_causes_fields(data, score_result['total'], fields=fields)
    action_plan = compute_action_plan_fields(
        data, score_result['total'], fields.get('vitamin_risks', {}),
        sample_dir=sample_dir, sample_id=sample_id
    )

    # ── Step 6: Assemble _microbiome_analysis.json ──
    logger.info("  Assembling microbiome analysis JSON...")

    microbiome_analysis = {
        'report_metadata': {
            'sample_id': sample_id,
            'report_date': datetime.now().strftime('%Y-%m-%d'),
            'algorithm_version': '3.0',
            'generated_at': datetime.now().isoformat(),
            'llm_model': model_id if not no_llm else None,
        },

        'executive_summary': {
            'overall_pattern': narratives.get('summary_sentence', ''),
            'key_finding': narratives.get('whats_happening_summary', ''),
            'priority_interventions': fields['key_opportunities'],
            'recovery_potential': narratives.get('can_this_be_fixed', ''),
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
            'interpretation': narratives.get('metabolic_interpretation', '[LLM skipped]'),
        },

        'vitamin_synthesis': {
            **fields['vitamin_risks'],
            'interpretation': narratives.get('vitamin_interpretation', '[LLM skipped]'),
        },

        'bacterial_groups': _merge_guild_interpretations(
            fields['bacterial_groups'],
            narratives.get('guild_interpretations', {})
        ),

        'key_messages': {
            'strengths': fields['key_strengths'],
            'opportunities': fields['key_opportunities'],
            'good_news': narratives.get('good_news', {}),
            'possible_impacts': narratives.get('possible_impacts', []),
            'is_something_wrong': narratives.get('is_something_wrong', ''),
            'can_this_be_fixed': narratives.get('can_this_be_fixed', ''),
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

    # ── Step 7: Build _platform.json ──
    logger.info("  Building platform JSON...")

    # Build the overview-format structure the platform mapper expects
    analysis_for_platform = {
        'report_metadata': microbiome_analysis['report_metadata'],
        'overview': {
            'gut_health_glance': {
                'summary_sentence': narratives.get('summary_sentence', ''),
                'overall_score': microbiome_analysis['overall_score'],
            },
            'whats_happening': {
                'overall_balance': fields['overall_balance'],
                'diversity_resilience': fields['diversity_resilience'],
                'key_strengths': fields['key_strengths'],
                'key_opportunities': fields['key_opportunities'],
                'summary_sentence': narratives.get('whats_happening_summary', ''),
            },
            'metabolic_dials': microbiome_analysis['metabolic_function']['dials'],
            'what_this_means': microbiome_analysis['key_messages'],
        },
        'bacterial_groups': microbiome_analysis['bacterial_groups'],
        'vitamin_synthesis': microbiome_analysis['vitamin_synthesis'],
        'key_messages': microbiome_analysis['key_messages'],
    }

    platform_json = build_platform_json(
        analysis_for_platform,
        data=data,
        root_causes=root_causes,
        action_plan=action_plan,
        narratives=narratives,
    )

    logger.info(f"  Done! Score: {score_result['total']}/100 [{score_result['band']}]")
    return microbiome_analysis, platform_json, data


def process_sample(sample_dir: str, output_path: str = None, **kwargs) -> str:
    """Process a single sample and save JSON outputs to sample directory."""
    microbiome_analysis, platform_json, data = build_all(sample_dir, **kwargs)

    sample_id = microbiome_analysis['report_metadata']['sample_id']

    # Save to sample directory
    if output_path is None:
        output_dir = os.path.join(sample_dir, 'reports', 'reports_json')
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f'microbiome_analysis_master_{sample_id}.json')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Save _microbiome_analysis.json (THE master file)
    with open(output_path, 'w') as f:
        json.dump(microbiome_analysis, f, indent=2)
    logger.info(f"  Saved: {output_path}")

    # Save _platform.json in same directory
    platform_path = os.path.join(
        os.path.dirname(output_path), f'microbiome_platform_{sample_id}.json'
    )
    with open(platform_path, 'w') as f:
        json.dump(platform_json, f, indent=2)
    logger.info(f"  Saved: {platform_path}")

    # Generate client-facing health report HTML
    try:
        from generate_health_report import process_sample as generate_health_report
        health_report_path = generate_health_report(
            sample_dir,
            no_llm=kwargs.get('no_llm', False),
            model_id=kwargs.get('model_id', 'eu.anthropic.claude-sonnet-4-20250514-v1:0'),
            region=kwargs.get('region', 'eu-west-1'),
        )
        logger.info(f"  Saved: {health_report_path}")
    except Exception as e:
        logger.warning(f"  Health report generation failed: {e}")

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
        description='Generate structured microbiome report JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--output', help='Custom output path')
    parser.add_argument('--no-llm', action='store_true',
                        help='Skip LLM narrative generation (placeholders used — dev/testing only)')
    parser.add_argument('--reuse-narratives', action='store_true',
                        help=(
                            'Reuse LLM narratives from existing master JSON instead of calling '
                            'Bedrock. Safe for reruns after deterministic-only changes (new '
                            'thresholds, scoring, dial recalibration). Falls back to Bedrock '
                            'if no valid existing JSON is found for a sample.'
                        ))
    parser.add_argument('--model-id', default='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                        help='Bedrock inference profile ID')
    parser.add_argument('--region', default='eu-west-1', help='AWS region')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.no_llm and args.reuse_narratives:
        parser.error('--no-llm and --reuse-narratives are mutually exclusive')

    kwargs = {
        'no_llm': args.no_llm,
        'reuse_narratives': args.reuse_narratives,
        'model_id': args.model_id,
        'region': args.region,
    }

    if args.sample_dir:
        process_sample(args.sample_dir, output_path=args.output, **kwargs)
    elif args.batch_dir:
        process_batch(args.batch_dir, **kwargs)


if __name__ == '__main__':
    main()
