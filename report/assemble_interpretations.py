#!/usr/bin/env python3
"""
assemble_interpretations.py — Build the single-source-of-truth JSON for the health report.

Reads 3 intermediate files:
  1. microbiome_analysis_master_{id}.json   (bioinformatics output)
  2. formulation_master_{id}.json           (formulation pipeline output)
  3. questionnaire_{id}.json                (client questionnaire)

Runs computation + LLM calls, then writes:
  health_report_interpretations_{id}.json  (schema v3.0)

This JSON is the ONLY input to generate_html(). No other files are needed at render time.

Usage:
  python assemble_interpretations.py --sample-dir /path/to/sample/
  python assemble_interpretations.py --sample-dir /path/to/sample/ --no-llm
  python assemble_interpretations.py --batch-dir /path/to/batch/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import glob
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Import existing computation functions from generate_health_report.py
# These are the same functions — we just call them from a cleaner orchestration layer.
from generate_health_report import (
    compute_circle_scores,
    compute_strengths_challenges,
    extract_profile,
    extract_good_news,
    build_timeline_phases,
    build_supplement_cards,
    build_goal_cards,
    build_root_cause_section,
    _extract_questionnaire_context,
    _generate_lifestyle_recommendations,
)


# ════════════════════════════════════════════════════════════════════════════════
#  FILE LOADING
# ════════════════════════════════════════════════════════════════════════════════

def load_source_files(sample_dir: str) -> dict:
    """Load the 3 source files for a sample. Returns dict with analysis, formulation, questionnaire."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    reports_json_dir = os.path.join(sample_dir, 'reports', 'reports_json')

    # 1. Microbiome analysis master (required)
    analysis_path = os.path.join(reports_json_dir, f'microbiome_analysis_master_{sample_id}.json')
    if not os.path.exists(analysis_path):
        raise FileNotFoundError(f"Microbiome analysis not found: {analysis_path}")
    with open(analysis_path) as f:
        analysis = json.load(f)
    logger.info(f"  ✓ Loaded microbiome_analysis_master ({os.path.getsize(analysis_path)} bytes)")

    # 2. Formulation master (required for full report, optional for microbiome-only)
    formulation_path = os.path.join(reports_json_dir, f'formulation_master_{sample_id}.json')
    formulation = None
    if os.path.exists(formulation_path):
        with open(formulation_path) as f:
            formulation = json.load(f)
        logger.info(f"  ✓ Loaded formulation_master ({os.path.getsize(formulation_path)} bytes)")
    else:
        logger.warning(f"  ⚠ Formulation master not found — supplement sections will be empty")

    # 3. Questionnaire (required for full report)
    questionnaire_path = os.path.join(sample_dir, 'questionnaire', f'questionnaire_{sample_id}.json')
    questionnaire = None
    if os.path.exists(questionnaire_path):
        with open(questionnaire_path) as f:
            questionnaire = json.load(f)
        logger.info(f"  ✓ Loaded questionnaire ({os.path.getsize(questionnaire_path)} bytes)")
    else:
        logger.warning(f"  ⚠ Questionnaire not found — profile section will be minimal")

    return {
        'sample_id': sample_id,
        'analysis': analysis,
        'formulation': formulation,
        'questionnaire': questionnaire,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  EXTRACTION — Pull flat fields from nested source files
# ════════════════════════════════════════════════════════════════════════════════

def extract_report_date(analysis: dict) -> str:
    """Extract report date from analysis metadata."""
    return analysis.get('report_metadata', {}).get('report_date', datetime.now().strftime('%Y-%m-%d'))


def extract_overall_score(analysis: dict) -> dict:
    """Extract overall score block — total, band, pillars, score_drivers."""
    score = analysis.get('overall_score', {})
    return {
        'total': score.get('total', 0),
        'band': score.get('band', ''),
        'pillars': score.get('pillars', {}),
        'score_drivers': score.get('score_drivers', {}),
    }


def extract_bacterial_groups(analysis: dict) -> dict:
    """Extract the 6 bacterial groups with all fields needed for guild bars + SVG."""
    bg = analysis.get('bacterial_groups', {})
    result = {}
    for gname, gdata in bg.items():
        result[gname] = {
            'abundance': gdata.get('abundance', 0),
            'healthy_range': gdata.get('healthy_range', ''),
            'status': gdata.get('status', ''),
            'clr': gdata.get('clr'),
            'evenness': gdata.get('evenness'),
            'evenness_status': gdata.get('evenness_status', ''),
            'client_interpretation': gdata.get('client_interpretation', ''),
        }
    return result


def extract_metabolic_dials(analysis: dict) -> dict:
    """Extract the 4 metabolic dials — state, label, value for each."""
    dials = analysis.get('metabolic_function', {}).get('dials', {})
    result = {}
    for dial_key, dial_data in dials.items():
        result[dial_key] = {
            'state': dial_data.get('state', ''),
            'label': dial_data.get('label', ''),
            'value': dial_data.get('value', 0),
        }
    return result


def extract_ecological_metrics(analysis: dict) -> dict:
    """Extract Shannon diversity, Pielou evenness, diversity state."""
    eco = analysis.get('ecological_metrics', {})
    debug = analysis.get('_debug', {})
    raw = debug.get('raw_metrics', {})

    shannon = eco.get('diversity', {}).get('shannon', {}).get('value') or raw.get('Shannon') or 0
    pielou = eco.get('diversity', {}).get('pielou_evenness', {}).get('value') or raw.get('Pielou') or 0
    div_state = eco.get('state', {}).get('diversity_resilience', {}).get('state', '')

    return {
        'shannon': shannon,
        'pielou_evenness': pielou,
        'diversity_state': div_state,
    }


def extract_safety_profile(analysis: dict) -> dict:
    """Extract dysbiosis markers and compute any_detected flag."""
    safety = analysis.get('safety_profile', {})
    dysbiosis = safety.get('dysbiosis_markers', {})

    any_detected = any(
        v.get('abundance', 0) > 0.1 if isinstance(v, dict) else False
        for v in dysbiosis.values()
    )

    # Flatten marker abundances for simpler JSON
    markers = {}
    for taxon, v in dysbiosis.items():
        markers[taxon] = v.get('abundance', 0) if isinstance(v, dict) else 0

    return {
        'dysbiosis_markers': markers,
        'any_detected': any_detected,
    }


def build_guild_timepoints(analysis: dict, report_date: str) -> list:
    """Build the guild_timepoints array for the JavaScript evolution slider."""
    bg = analysis.get('bacterial_groups', {})
    debug = analysis.get('_debug', {})
    gs = debug.get('guild_summary', {})

    def _guild_val(keyword: str) -> float:
        """Find guild abundance by keyword, return as decimal fraction."""
        for gname, gdata in bg.items():
            if keyword.lower() in gname.lower():
                return round(gdata.get('abundance', 0) / 100.0, 4)
        for gname, gdata in gs.items():
            if keyword.lower() in gname.lower():
                return round(gdata.get('abundance', 0) / 100.0, 4)
        return 0.0

    # Format label
    try:
        label = 'Baseline — ' + datetime.strptime(report_date, '%Y-%m-%d').strftime('%b %Y')
    except Exception:
        label = f'Baseline — {report_date}'

    # Extract overall score to include in timepoint (used by banner slider — avoids guild approximation)
    overall_score = analysis.get('overall_score', {}).get('total')

    return [{
        'label': label,
        'score': overall_score,  # actual overall score — JS uses this, not computeBannerScore()
        'guilds': {
            'fd': _guild_val('fiber'),
            'bb': _guild_val('bifidobacter') or _guild_val('hmo'),
            'cf': _guild_val('cross'),
            'bp': _guild_val('butyrate'),
            'pg': _guild_val('proteolytic'),
            'md': _guild_val('mucin'),
        },
    }]


def compute_score_summary(total_score: float, distinct_areas: list) -> str:
    """Pre-render the cover page score summary sentence."""
    def _format_area_list(areas: list) -> str:
        bold = [f'<strong>{a}</strong>' for a in areas]
        if len(bold) == 0:
            return ''
        if len(bold) == 1:
            return bold[0]
        if len(bold) == 2:
            return f'{bold[0]} and {bold[1]}'
        return ', '.join(bold[:-1]) + f', and {bold[-1]}'

    if not distinct_areas:
        return f'Your overall score is <strong>{total_score}</strong> out of 100. Your gut ecosystem is in good shape.'
    elif len(distinct_areas) == 1:
        return f'Your overall score is <strong>{total_score}</strong> out of 100. The main area to focus on is {_format_area_list(distinct_areas)}.'
    else:
        return f'Your overall score is <strong>{total_score}</strong> out of 100. The main areas to focus on are {_format_area_list(distinct_areas)}.'


def compute_bottom_line(challenges: list) -> str:
    """Pre-render the strengths/challenges bottom line quote."""
    if not challenges:
        return "Your gut is in excellent shape across all key areas. Your protocol is about maintaining and optimising these gains."
    elif len(challenges) <= 2:
        return "The main issue is not total collapse — it is imbalance in a few high-impact areas. Your gut still has strong foundations to build on."
    else:
        return "Despite these imbalances, the pattern is addressable. Your protocol targets each of these areas specifically and methodically."


def extract_protocol_summary(formulation: dict) -> dict:
    """Extract protocol summary from formulation."""
    if not formulation:
        return {}
    ps = formulation.get('formulation', {}).get('protocol_summary', {})
    return {
        'synbiotic_mix': ps.get('synbiotic_mix', {}),
        'morning_solid_units': ps.get('morning_solid_units', 0),
        'morning_jar_units': ps.get('morning_jar_units', 0),
        'evening_solid_units': ps.get('evening_solid_units', 0),
        'total_daily_units': ps.get('total_daily_units', 0),
        'total_daily_weight_g': ps.get('total_daily_weight_g', 0),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  MAIN ASSEMBLY
# ════════════════════════════════════════════════════════════════════════════════

def assemble(sample_dir: str, no_llm: bool = False,
             model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
             region: str = 'eu-west-1',
             elicit_key: str = '') -> str:
    """
    Assemble the complete health_report_interpretations JSON for one sample.

    Phase 1: Load 3 source files
    Phase 2: Extract flat fields (no computation)
    Phase 3: Compute derived fields (deterministic)
    Phase 4: Run LLM sections (Section 3 root causes, lifestyle recs)
    Phase 5: Write JSON

    Returns path to the written JSON file.
    """
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    logger.info(f"Assembling health report interpretations: {sample_id}")

    # ── Phase 1: Load source files ────────────────────────────────────────
    logger.info("  Phase 1: Loading source files...")
    sources = load_source_files(sample_dir)
    analysis = sources['analysis']
    formulation = sources['formulation']
    questionnaire = sources['questionnaire']

    # Resolve Elicit key
    resolved_elicit_key = elicit_key or os.environ.get('ELICIT_API_KEY', '')
    if not resolved_elicit_key:
        _dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(_dotenv_path):
            with open(_dotenv_path) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith('#') and '=' in _line:
                        _k, _v = _line.split('=', 1)
                        if _k.strip() == 'ELICIT_API_KEY':
                            resolved_elicit_key = _v.strip()
                            break

    # ── Phase 2: Extract flat fields (direct copy from source) ────────────
    logger.info("  Phase 2: Extracting flat fields...")
    report_date = extract_report_date(analysis)
    overall_score = extract_overall_score(analysis)
    bacterial_groups = extract_bacterial_groups(analysis)
    metabolic_dials = extract_metabolic_dials(analysis)
    ecological_metrics = extract_ecological_metrics(analysis)
    safety_profile = extract_safety_profile(analysis)
    guild_timepoints = build_guild_timepoints(analysis, report_date)
    good_news = extract_good_news(analysis)
    protocol_summary = extract_protocol_summary(formulation)

    # ── Phase 3: Compute derived fields (deterministic) ───────────────────
    logger.info("  Phase 3: Computing derived fields...")

    profile = extract_profile(questionnaire, formulation)
    logger.info(f"    Profile: {profile.get('first_name', '?')}, {profile.get('age', '?')}y")

    circle_scores = compute_circle_scores(analysis)
    logger.info(f"    Circle scores: {circle_scores}")

    sw = compute_strengths_challenges(analysis, circle_scores)
    logger.info(f"    Strengths: {len(sw['strengths'])}  Challenges: {len(sw['challenges'])}")

    # Add bottom_line to strengths_challenges
    sw['bottom_line'] = compute_bottom_line(sw.get('challenges', []))

    # Pre-compute score summary
    score_summary = compute_score_summary(
        overall_score.get('total', 0),
        sw.get('distinct_areas', [])
    )

    timeline_phases = build_timeline_phases(analysis, formulation)
    logger.info(f"    Timeline: {len(timeline_phases)} phases")

    supplement_cards = build_supplement_cards(formulation, analysis)
    logger.info(f"    Supplement cards: {len(supplement_cards)}")

    goal_cards = build_goal_cards(questionnaire, formulation, analysis)
    logger.info(f"    Goal cards: {len(goal_cards)}")

    # ── Phase 4: LLM sections ────────────────────────────────────────────
    logger.info("  Phase 4: LLM sections...")

    # Section 3 — Root causes
    root_cause_data = build_root_cause_section(
        questionnaire, analysis,
        no_llm=no_llm, model_id=model_id, region=region,
        elicit_key=resolved_elicit_key,
    )
    n_dev = len(root_cause_data.get('deviation_cards', []))
    n_aw = len(root_cause_data.get('awareness_chips', []))
    logger.info(f"    Section 3: {n_dev} deviation card(s), {n_aw} awareness chip(s)")

    # Lifestyle recommendations
    lifestyle_recs = []
    lifestyle_cited_papers = []
    if not no_llm:
        questionnaire_context = _extract_questionnaire_context(questionnaire)
        lifestyle_recs, lifestyle_cited_papers = _generate_lifestyle_recommendations(
            deviations=root_cause_data.get('deviation_cards', []),
            questionnaire_context=questionnaire_context,
            analysis=analysis,
            elicit_key=resolved_elicit_key,
            model_id=model_id,
            region=region,
        )
        logger.info(f"    Lifestyle: {len(lifestyle_recs)} recs, {len(lifestyle_cited_papers)} cited papers")

    # Merge cited papers (Section 3 + lifestyle, deduplicated)
    all_cited_papers = list(root_cause_data.get('cited_papers', []))
    seen_titles = {p.get('title', '') for p in all_cited_papers}
    for p in lifestyle_cited_papers:
        if p.get('title') and p['title'] not in seen_titles:
            all_cited_papers.append(p)
            seen_titles.add(p['title'])

    # ── Phase 5: Assemble and write JSON ──────────────────────────────────
    logger.info("  Phase 5: Assembling final JSON...")

    interpretations = {
        'sample_id': sample_id,
        'generated_at': datetime.now().isoformat(),
        'schema_version': '3.0',
        'report_date': report_date,

        # Cover page
        'overall_score': overall_score,
        'score_summary': score_summary,
        'profile': profile,

        # Section 1
        'circle_scores': circle_scores,
        'bacterial_groups': bacterial_groups,
        'metabolic_dials': metabolic_dials,
        'ecological_metrics': ecological_metrics,
        'safety_profile': safety_profile,
        'guild_timepoints': guild_timepoints,

        # Section 2
        'strengths_challenges': sw,
        'good_news': good_news,

        # Section 3
        'root_cause_data': root_cause_data,

        # Section 4
        'timeline_phases': timeline_phases,
        'lifestyle_recommendations': lifestyle_recs,

        # Section 5
        'supplement_cards': supplement_cards,

        # Section 6
        'goal_cards': goal_cards,

        # References
        'cited_papers': all_cited_papers,

        # Protocol metadata
        'protocol_summary': protocol_summary,
    }

    # Write
    out_dir = os.path.join(sample_dir, 'reports', 'reports_json')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'health_report_interpretations_{sample_id}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(interpretations, f, indent=2, ensure_ascii=False)

    file_size = os.path.getsize(out_path)
    logger.info(f"  ✓ Written: {out_path} ({file_size:,} bytes)")
    logger.info(f"  ✓ Schema v3.0 — {len(supplement_cards)} supplement cards, {len(goal_cards)} goal cards")

    return out_path


# ════════════════════════════════════════════════════════════════════════════════
#  BATCH PROCESSING
# ════════════════════════════════════════════════════════════════════════════════

def assemble_batch(batch_dir: str, **kwargs):
    """Assemble interpretations for all samples in a batch directory."""
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, '*')))
    sample_dirs = [d for d in sample_dirs if os.path.isdir(d) and not d.endswith('.DS_Store')]

    logger.info(f"Batch assembly: {len(sample_dirs)} samples in {batch_dir}")
    results = []

    for sample_dir in sample_dirs:
        sample_id = os.path.basename(sample_dir)
        reports_dir = os.path.join(sample_dir, 'reports', 'reports_json')
        if not os.path.exists(reports_dir):
            logger.warning(f"  Skipping {sample_id} — no reports directory")
            continue
        try:
            path = assemble(sample_dir, **kwargs)
            results.append({'sample_id': sample_id, 'status': 'success', 'output': path})
        except Exception as e:
            logger.error(f"  Failed {sample_id}: {e}")
            results.append({'sample_id': sample_id, 'status': 'error', 'error': str(e)})

    success = sum(1 for r in results if r['status'] == 'success')
    errors = sum(1 for r in results if r['status'] == 'error')
    logger.info(f"\nBatch complete: {success} success, {errors} errors out of {len(results)} samples")
    return results


# ════════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Assemble health_report_interpretations.json (schema v3.0)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--no-llm', action='store_true',
                        help='Skip LLM calls (Section 3 uses KB-only fallback)')
    parser.add_argument('--model-id', default='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                        help='Bedrock model ID')
    parser.add_argument('--region', default='eu-west-1', help='AWS region')
    parser.add_argument('--elicit-key', default='',
                        help='Elicit API key for scientific literature enrichment')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    kwargs = {
        'no_llm': args.no_llm,
        'model_id': args.model_id,
        'region': args.region,
        'elicit_key': args.elicit_key,
    }

    if args.sample_dir:
        out = assemble(args.sample_dir, **kwargs)
        print(f"\n✅ Interpretations JSON assembled: {out}")
    elif args.batch_dir:
        assemble_batch(args.batch_dir, **kwargs)


if __name__ == '__main__':
    main()
