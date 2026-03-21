#!/usr/bin/env python3
from __future__ import annotations
"""
generate_health_report.py — Premium client-facing personalized microbiome health report

Reads:
  - microbiome_analysis_master_{sample_id}.json  (scores, guilds, dials, root causes)
  - formulation_master_{sample_id}.json          (formula components and rationale)
  - questionnaire_{sample_id}.json               (profile, goals, symptoms, lifestyle)

Outputs:
  - {sample_dir}/reports/reports_json/health_report_interpretations_{sample_id}.json
      Intermediate cache of ALL LLM outputs and derived data. Reusable downstream.
      Re-running HTML from this cache (no LLM cost): use --use-cached flag.
  - {sample_dir}/reports/reports_html/health_report_{sample_id}.html
      Final client-facing HTML report.

Two-phase architecture:
  Phase 1 — LLM + computation: build all derived data, generate LLM interpretations,
             persist everything to health_report_interpretations_{id}.json
  Phase 2 — Rendering: load JSON, call generate_html(), write HTML file

Usage:
  python generate_health_report.py --sample-dir /path/to/analysis/batch/sample/
  python generate_health_report.py --sample-dir /path/to/sample/ --no-llm
  python generate_health_report.py --sample-dir /path/to/sample/ --use-cached
  python generate_health_report.py --batch-dir /path/to/analysis/batch/
"""

import argparse
import json
import logging
import math
import os
import sys
import glob
from datetime import datetime

from formulation_bridge import get_formulation_context

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
#  INTERMEDIATE JSON — save / load
# ════════════════════════════════════════════════════════════════════════════════

def _interpretations_json_path(sample_dir: str, sample_id: str) -> str:
    """Return the canonical path for the intermediate interpretations JSON."""
    return os.path.join(
        sample_dir, 'reports', 'reports_json',
        f'health_report_interpretations_{sample_id}.json'
    )


def save_interpretations_json(data: dict, sample_dir: str) -> str:
    """
    Persist the complete data bundle (all LLM outputs + derived data) to
    health_report_interpretations_{sample_id}.json.

    This JSON is the single source of truth for Phase 2 (HTML rendering).
    Re-running HTML without LLM: load this file and call generate_html().

    The bundle is JSON-serialisable as-is — all values are primitive types,
    dicts, or lists.  The cited_papers list may contain paper dicts with string
    fields; those serialise cleanly.

    Returns the path where the file was saved.
    """
    sample_id = data['sample_id']
    out_path = _interpretations_json_path(sample_dir, sample_id)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Build a serialisable snapshot — exclude raw analysis/formulation/questionnaire
    # objects (those already live in their own master JSONs).  We store only the
    # derived / LLM-generated layer so the file stays compact.
    #
    # Schema v3.0: also persist flat microbiome fields so the cached JSON is
    # self-contained and generate_html() can render without loading raw files.
    snapshot = {
        # ── Identity ──────────────────────────────────────────────────────────
        'sample_id': sample_id,
        'generated_at': datetime.now().isoformat(),
        'schema_version': '3.0',
        'report_date': data.get('report_date', ''),
        # ── Microbiome flat fields (v3.0) ─────────────────────────────────────
        'overall_score': data.get('overall_score', {}),
        'score_summary': data.get('score_summary', ''),
        'bacterial_groups': data.get('bacterial_groups', {}),
        'metabolic_dials': data.get('metabolic_dials', {}),
        'ecological_metrics': data.get('ecological_metrics', {}),
        'safety_profile': data.get('safety_profile', {}),
        'guild_timepoints': data.get('guild_timepoints', []),
        'protocol_summary': data.get('protocol_summary', {}),
        # ── LLM / derived layer ───────────────────────────────────────────────
        'profile': data.get('profile', {}),
        'circle_scores': data.get('circle_scores', {}),
        'strengths_challenges': data.get('strengths_challenges', {}),
        'good_news': data.get('good_news', ''),
        'timeline_phases': data.get('timeline_phases', []),
        'supplement_cards': data.get('supplement_cards', []),
        'goal_cards': data.get('goal_cards', []),
        'root_cause_data': data.get('root_cause_data', {}),
        'cited_papers': data.get('cited_papers', []),
        'lifestyle_recommendations': data.get('lifestyle_recommendations', []),
        'supplement_why_texts': data.get('supplement_why_texts', {}),
    }
    # Also snapshot factor-first fields if present in root_cause_data
    rcd = snapshot.get('root_cause_data', {})
    if rcd and not rcd.get('factor_cards') and data.get('root_cause_data', {}).get('factor_cards'):
        snapshot['root_cause_data'] = data['root_cause_data']  # include full rcd with factor_cards

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    return out_path


def load_interpretations_json(sample_dir: str, sample_id: str) -> dict | None:
    """
    Load the interpretations snapshot from disk and merge it with the raw data
    sources (analysis, formulation, questionnaire) to reconstruct the full data
    bundle expected by generate_html().

    Returns the full data bundle, or None if the cache file does not exist.
    """
    cache_path = _interpretations_json_path(sample_dir, sample_id)
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, encoding='utf-8') as f:
            snapshot = json.load(f)
    except Exception as e:
        logger.warning(f"  Could not read interpretations cache: {e}")
        return None

    # Re-load raw data sources (needed for generate_html)
    try:
        raw = load_data(sample_dir)
    except Exception as e:
        logger.error(f"  Could not load raw data sources: {e}")
        return None

    # Merge: raw sources + cached derived/LLM layer
    # Schema v3.0 fields are passed through directly from the snapshot (no raw file needed).
    # Schema v1/v2 fields fall back to raw analysis/formulation/questionnaire.
    data = {
        **raw,
        # Derived / LLM layer (all schema versions)
        'profile': snapshot.get('profile', {}),
        'circle_scores': snapshot.get('circle_scores', {}),
        'strengths_challenges': snapshot.get('strengths_challenges', {}),
        'good_news': snapshot.get('good_news', ''),
        'timeline_phases': snapshot.get('timeline_phases', []),
        'supplement_cards': snapshot.get('supplement_cards', []),
        'goal_cards': snapshot.get('goal_cards', []),
        'root_cause_data': snapshot.get('root_cause_data', {}),
        'cited_papers': snapshot.get('cited_papers', []),
        'lifestyle_recommendations': snapshot.get('lifestyle_recommendations', []),
        # Schema v3.0 flat microbiome fields — override raw analysis if present
        **({
            'schema_version': snapshot['schema_version'],
            'report_date': snapshot.get('report_date', ''),
            'overall_score': snapshot.get('overall_score', {}),
            'score_summary': snapshot.get('score_summary', ''),
            'bacterial_groups': snapshot.get('bacterial_groups', {}),
            'metabolic_dials': snapshot.get('metabolic_dials', {}),
            'ecological_metrics': snapshot.get('ecological_metrics', {}),
            'safety_profile': snapshot.get('safety_profile', {}),
            'guild_timepoints': snapshot.get('guild_timepoints', []),
            'protocol_summary': snapshot.get('protocol_summary', {}),
        } if snapshot.get('schema_version', '1.0') >= '3.0' else {}),
    }
    return data


# ════════════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ════════════════════════════════════════════════════════════════════════════════

def load_data(sample_dir: str) -> dict:
    """Load all three data sources for a sample."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    reports_json_dir = os.path.join(sample_dir, 'reports', 'reports_json')

    # Microbiome analysis master
    analysis_path = os.path.join(reports_json_dir, f'microbiome_analysis_master_{sample_id}.json')
    if not os.path.exists(analysis_path):
        raise FileNotFoundError(f"Microbiome analysis not found: {analysis_path}")
    with open(analysis_path) as f:
        analysis = json.load(f)

    # Formulation master (optional — report can be generated without it)
    formulation_path = os.path.join(reports_json_dir, f'formulation_master_{sample_id}.json')
    formulation = None
    if os.path.exists(formulation_path):
        with open(formulation_path) as f:
            formulation = json.load(f)
    else:
        logger.warning(f"Formulation master not found: {formulation_path} — supplement sections will be empty")

    # Questionnaire
    questionnaire_path = os.path.join(sample_dir, 'questionnaire', f'questionnaire_{sample_id}.json')
    questionnaire = None
    if os.path.exists(questionnaire_path):
        with open(questionnaire_path) as f:
            questionnaire = json.load(f)
    else:
        logger.warning(f"Questionnaire not found: {questionnaire_path} — profile section will be minimal")

    return {
        'sample_id': sample_id,
        'analysis': analysis,
        'formulation': formulation,
        'questionnaire': questionnaire,
        'formulation_context': get_formulation_context(formulation),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  FOUR CIRCLE SCORE CALCULATIONS
# ════════════════════════════════════════════════════════════════════════════════

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _abundance_score(abundance: float, range_min: float, range_max: float,
                     optimal: float, direction: str = 'beneficial') -> float:
    """
    Normalize guild abundance to 0-100 score.
    direction='beneficial': higher within range = better
    direction='contextual_mucin': specific curve for mucin degraders
    direction='contextual_proteo': inverted — lower is better
    """
    if direction == 'beneficial':
        if abundance <= 0:
            return 0.0
        if abundance < range_min:
            # Below range: linear from 0 at 0% to 50 at range_min
            return _clamp(50.0 * abundance / range_min)
        if abundance <= optimal:
            # Lower half of range: 50 → 100
            return _clamp(50.0 + 50.0 * (abundance - range_min) / (optimal - range_min))
        if abundance <= range_max:
            # Upper half: 100 → 90 (slight cap)
            return _clamp(100.0 - 10.0 * (abundance - optimal) / (range_max - optimal))
        # Above range: drop gradually
        excess = (abundance - range_max) / range_max
        return _clamp(90.0 - 40.0 * excess)

    elif direction == 'contextual_mucin':
        # Mucin: below 1% = mild concern (60), 1-4% = healthy (100), >4% = drops
        if abundance < 0.5:
            return 55.0
        if abundance < range_min:  # below 1%
            return _clamp(55.0 + 45.0 * (abundance - 0.5) / (range_min - 0.5))
        if abundance <= range_max:  # 1-4% healthy
            # Peak at optimal (2.5%), slight drop at edges
            dist = abs(abundance - optimal) / max(optimal - range_min, range_max - optimal)
            return _clamp(100.0 - 20.0 * dist)
        # Above 4%
        excess = (abundance - range_max) / range_max
        return _clamp(80.0 - 60.0 * excess)  # drops to 20 at ~2× range_max

    elif direction == 'contextual_proteo':
        # Proteolytic: inverted — lower is better
        if abundance <= 0:
            return 100.0
        if abundance < range_min:  # below 1%
            return 100.0
        if abundance <= range_max:  # 1-5% healthy
            # 1% → 90, 5% → 60
            return _clamp(90.0 - 30.0 * (abundance - range_min) / (range_max - range_min))
        # Above range
        excess = (abundance - range_max) / range_max
        return _clamp(60.0 - 60.0 * excess)


def _dial_score_3state(state: str, good: float = 100.0, mid: float = 55.0, bad: float = 15.0,
                       good_state: str = '', mid_state: str = '', bad_state: str = '') -> float:
    """Map a 3-state dial to a 0-100 score."""
    mapping = {good_state: good, mid_state: mid, bad_state: bad}
    return mapping.get(state, mid)


def compute_circle_scores(analysis: dict) -> dict:
    """
    Compute the four composite circle scores (0-100 each).
    Returns dict with keys: gut_lining, inflammation, fiber_processing, bifidobacteria
    """
    bg = analysis.get('bacterial_groups', {})
    dials = analysis.get('metabolic_function', {}).get('dials', {})
    debug = analysis.get('_debug', {})
    guild_summary = debug.get('guild_summary', {})
    raw = debug.get('raw_metrics', {})

    # Helper to get guild abundance by keyword
    def _guild_abund(keyword: str) -> float:
        for gname, gdata in bg.items():
            if keyword.lower() in gname.lower():
                return gdata.get('abundance', 0.0)
        for gname, gdata in guild_summary.items():
            if keyword.lower() in gname.lower():
                return gdata.get('abundance', 0.0)
        return 0.0

    def _dial_state(dial_key: str) -> str:
        return dials.get(dial_key, {}).get('state', '')

    # ── CIRCLE 1: Gut Lining Protection ──────────────────────────────────────
    butyrate_abund = _guild_abund('butyrate')
    mucin_abund = _guild_abund('mucin')
    mdr_state = _dial_state('mucus_dependency')

    butyrate_s = _abundance_score(butyrate_abund, 10.0, 25.0, 17.5, 'beneficial')
    mucin_s = _abundance_score(mucin_abund, 1.0, 4.0, 2.5, 'contextual_mucin')
    mdr_s = _dial_score_3state(mdr_state, 100, 55, 15,
                                'diet_fed', 'backup', 'heavy_mucus')

    gut_lining = _clamp(butyrate_s * 0.40 + mucin_s * 0.35 + mdr_s * 0.25)

    # ── CIRCLE 2: Inflammation Control ──────────────────────────────────────
    proteo_abund = _guild_abund('proteolytic')
    ppr_state = _dial_state('putrefaction_pressure')

    proteo_s = _abundance_score(proteo_abund, 1.0, 5.0, 3.0, 'contextual_proteo')
    ppr_s = _dial_score_3state(ppr_state, 100, 55, 15,
                                'scfa_dominant', 'balanced', 'protein_pressure')
    mucin_inf_s = _abundance_score(mucin_abund, 1.0, 4.0, 2.5, 'contextual_mucin')

    inflammation = _clamp(proteo_s * 0.40 + ppr_s * 0.40 + mucin_inf_s * 0.20)

    # ── CIRCLE 3: Fiber Processing ───────────────────────────────────────────
    fiber_abund = _guild_abund('fiber')
    cross_abund = _guild_abund('cross')
    cur_state = _dial_state('main_fuel')
    fcr_state = _dial_state('fermentation_efficiency')

    fiber_s = _abundance_score(fiber_abund, 30.0, 50.0, 40.0, 'beneficial')
    cross_s = _abundance_score(cross_abund, 6.0, 12.0, 9.0, 'beneficial')
    cur_s = _dial_score_3state(cur_state, 100, 55, 15,
                                'carb_driven', 'balanced', 'protein_driven')
    fcr_s = _dial_score_3state(fcr_state, 100, 55, 15,
                                'efficient', 'ok', 'sluggish')

    fiber_processing = _clamp(fiber_s * 0.35 + cross_s * 0.25 + cur_s * 0.20 + fcr_s * 0.20)

    # ── CIRCLE 4: Bifidobacteria Presence ────────────────────────────────────
    bifido_abund = _guild_abund('bifidobacter')
    if bifido_abund == 0:
        bifido_abund = _guild_abund('hmo')

    bifido_score = _abundance_score(bifido_abund, 2.0, 10.0, 6.0, 'beneficial')
    # Cap at 88 for above-range (above 10% is fine but not perfect)
    if bifido_abund > 10.0:
        bifido_score = _clamp(88.0 - 5.0 * (bifido_abund - 10.0) / 10.0, 75, 90)

    bifidobacteria = _clamp(bifido_score)

    return {
        'gut_lining': round(gut_lining),
        'inflammation': round(inflammation),
        'fiber_processing': round(fiber_processing),
        'bifidobacteria': round(bifidobacteria),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  STRENGTHS AND CHALLENGES
# ════════════════════════════════════════════════════════════════════════════════

def compute_strengths_challenges(analysis: dict, circle_scores: dict) -> dict:
    """
    Compute strengths and challenges as deterministic boolean flags.
    Returns {strengths: [...], challenges: [...]}
    """
    bg = analysis.get('bacterial_groups', {})
    dials = analysis.get('metabolic_function', {}).get('dials', {})
    score_data = analysis.get('overall_score', {})
    eco = analysis.get('ecological_metrics', {})
    safety = analysis.get('safety_profile', {})
    vit = analysis.get('vitamin_synthesis', {})
    debug = analysis.get('_debug', {})
    guild_summary = debug.get('guild_summary', {})
    raw = debug.get('raw_metrics', {})

    # --- helpers ---
    def _guild(keyword: str) -> dict:
        """Get guild data dict by keyword."""
        for gname, gdata in bg.items():
            if keyword.lower() in gname.lower():
                return gdata
        return {}

    def _dial(key: str) -> str:
        return dials.get(key, {}).get('state', '')

    def _gs(keyword: str) -> dict:
        """Get guild summary data."""
        for gname, gdata in guild_summary.items():
            if keyword.lower() in gname.lower():
                return gdata
        return {}

    butyrate = _guild('butyrate')
    fiber = _guild('fiber')
    cross = _guild('cross')
    bifido = _guild('bifidobacter') or _guild('hmo')
    mucin = _guild('mucin')
    proteo = _guild('proteolytic')

    shannon = eco.get('diversity', {}).get('shannon', {}).get('value') or raw.get('Shannon') or 0
    pielou = eco.get('diversity', {}).get('pielou_evenness', {}).get('value') or raw.get('Pielou') or 0
    div_state = eco.get('state', {}).get('diversity_resilience', {}).get('state', '')
    avg_j = score_data.get('details', {}).get('P2', {}).get('avg_guild_J', 0.5)

    # Population thresholds (matching thresholds.py fallbacks)
    SHANNON_HIGH = 3.44  # Q75 — high diversity (strength)
    SHANNON_MED  = 3.29  # Q50 — median; below this = moderate diversity (challenge)
    SHANNON_LOW  = 2.79  # Q25 — below this = low diversity (challenge)
    PIELOU_HIGH = 0.76   # Q75
    PIELOU_LOW = 0.66    # Q25

    dysbiosis = safety.get('dysbiosis_markers', {})
    any_dysbiosis = any(
        v.get('abundance', 0) > 0.1 if isinstance(v, dict) else False
        for v in dysbiosis.values()
    )

    strengths = []
    challenges = []

    # ── STRENGTHS ─────────────────────────────────────────────────────────────

    # 1. Strong fiber processing (only when Fiber Processing dial also confirms it)
    fiber_abund = fiber.get('abundance', 0)
    fiber_status = fiber.get('status', '')
    if fiber_abund >= 30.0 and circle_scores['fiber_processing'] >= 65:
        strengths.append({
            'icon': '🌿',
            'title': 'Strong fiber processing',
            'text': f'Your fiber-processing bacteria are within their healthy range at {fiber_abund:.1f}% — this is the foundation of an efficient fermentation chain and means you get good value from the plant foods you eat.',
        })

    # 2. Low inflammatory pressure
    ppr_state = _dial('putrefaction_pressure')
    proteo_abund = proteo.get('abundance', 0)
    if ppr_state == 'scfa_dominant' and proteo_abund <= 5.0:
        strengths.append({
            'icon': '🛡️',
            'title': 'Low inflammatory pressure',
            'text': f'Your beneficial bacteria are producing mostly gentle, protective short-chain fatty acids with minimal harsh protein-fermentation byproducts. This is an important marker of gut health.',
        })

    # 3. High diversity and resilience
    if shannon >= SHANNON_HIGH and pielou >= PIELOU_HIGH:
        strengths.append({
            'icon': '🌱',
            'title': 'High diversity and ecosystem resilience',
            'text': f'Your gut has a rich variety of bacterial species (Shannon {shannon:.2f}, top 25% of population) with strong community balance (Pielou {pielou:.2f}). A diverse, even ecosystem bounces back faster from disruption.',
        })
    elif shannon >= SHANNON_MED:
        strengths.append({
            'icon': '🌱',
            'title': 'Good diversity',
            'text': f'Your gut shows good species diversity (Shannon {shannon:.2f}, above the population median of 3.29). A varied bacterial community provides a more stable and adaptable foundation for gut health.',
        })

    # 4. Strong butyrate support
    butyrate_abund = butyrate.get('abundance', 0)
    butyrate_clr = butyrate.get('clr')
    butyrate_in_range = 10.0 <= butyrate_abund <= 25.0
    if butyrate_in_range and (butyrate_clr is None or butyrate_clr >= -0.5):
        strengths.append({
            'icon': '⚡',
            'title': 'Strong butyrate support',
            'text': f'Your butyrate-producing bacteria are at {butyrate_abund:.1f}% — within the healthy 10-25% range. These bacteria produce the primary fuel for your gut lining cells, supporting barrier integrity and reducing inflammation.',
        })

    # 5. Healthy bifidobacteria presence
    bifido_abund = bifido.get('abundance', 0)
    if 2.0 <= bifido_abund <= 10.0:
        strengths.append({
            'icon': '✨',
            'title': 'Healthy bifidobacteria presence',
            'text': f'Your Bifidobacteria are at {bifido_abund:.1f}%, within the healthy 2-10% range. These beneficial bacteria support immune function, produce protective compounds, and amplify the fermentation process for other beneficial guilds.',
        })
    elif bifido_abund > 10.0:
        strengths.append({
            'icon': '✨',
            'title': 'Abundant bifidobacteria',
            'text': f'Your Bifidobacteria are thriving at {bifido_abund:.1f}%, above the typical reference range. Strong Bifidobacteria presence supports immune function and helps amplify beneficial short-chain fatty acid production.',
        })

    # 6. Balanced guild relationships (all 6 within range, no CLR extremes)
    guilds_in_range = 0
    guilds_checked = 0
    for gname, gdata in bg.items():
        status = gdata.get('status', '')
        clr = gdata.get('clr')
        guilds_checked += 1
        if 'Within range' in status or 'Above range' in status:
            guilds_in_range += 1
    if guilds_checked > 0 and guilds_in_range >= guilds_checked - 1:
        cross_clr = cross.get('clr')
        if cross_clr is None or abs(cross_clr) <= 1.5:
            strengths.append({
                'icon': '⚖️',
                'title': 'Balanced guild relationships',
                'text': 'Most of your bacterial groups are within their healthy ranges with no extreme competitive imbalances. This reflects a well-organised gut ecosystem where different microbial teams are coexisting in proportion.',
            })

    # 7. Healthy cross-feeding ecology
    cross_abund = cross.get('abundance', 0)
    cross_clr = cross.get('clr')
    if 6.0 <= cross_abund <= 12.0 and (cross_clr is None or cross_clr >= -0.5):
        strengths.append({
            'icon': '🔗',
            'title': 'Healthy cross-feeding ecology',
            'text': f'Your cross-feeding bacteria are at {cross_abund:.1f}% (healthy range 6-12%). These connectors convert fermentation intermediates into beneficial compounds, linking the different stages of your gut\'s production chain.',
        })

    # 8. Strong gut lining support
    mdr_state = _dial('mucus_dependency')
    mucin_abund = mucin.get('abundance', 0)
    if butyrate_in_range and mdr_state == 'diet_fed' and 1.0 <= mucin_abund <= 4.0:
        strengths.append({
            'icon': '🏗️',
            'title': 'Strong gut lining support',
            'text': 'Your gut lining has good protection: butyrate producers are active, your bacteria are fed primarily by dietary fiber rather than mucus, and mucin degradation is controlled. This combination supports a well-maintained gut barrier.',
        })

    # 9. Clean safety profile
    if not any_dysbiosis:
        strengths.append({
            'icon': '✅',
            'title': 'Clean safety profile',
            'text': 'None of the key dysbiosis-associated bacteria (Fusobacterium nucleatum, Streptococcus gallolyticus, Peptostreptococcus anaerobius, Escherichia-Shigella) were detected at concerning levels. Your gut has a reassuring safety baseline.',
        })

    # 10. Diet-fed ecosystem
    if mdr_state == 'diet_fed':
        strengths.append({
            'icon': '🥗',
            'title': 'Diet-fed ecosystem',
            'text': 'Your bacteria are primarily fuelled by what you eat rather than by eroding your gut\'s protective mucus layer. This is the ecologically stable pattern and reflects a gut that has adequate dietary substrate.',
        })

    # 11. Efficient fermentation
    fcr_state = _dial('fermentation_efficiency')
    if fcr_state == 'efficient':
        strengths.append({
            'icon': '⚙️',
            'title': 'Efficient fermentation',
            'text': 'Your gut\'s fermentation assembly line is running well — intermediate fermentation compounds are being fully converted into beneficial short-chain fatty acids rather than accumulating. This means your bacteria are cooperating efficiently.',
        })

    # 12. Strong competitive position in a key guild
    for gname, gdata in bg.items():
        clr = gdata.get('clr')
        status = gdata.get('status', '')
        is_beneficial = any(k in gname.lower() for k in ['fiber', 'butyrate', 'cross', 'bifido', 'hmo'])
        if is_beneficial and clr is not None and clr > 0.5 and 'Within range' in status:
            strengths.append({
                'icon': '💪',
                'title': f'Strong competitive position — {gname}',
                'text': f'Your {gname.lower()} hold a strong competitive position in your gut ecosystem (CLR {clr:+.2f}), meaning they are winning the competition for ecological space. This is a positive sign for long-term stability.',
            })
            break  # Only flag the most prominent one

    # ── CHALLENGES ────────────────────────────────────────────────────────────

    # ── Guild-to-area mapping helper ───────────────────────────────────────────
    def _guild_area(gname: str) -> tuple:
        """Return (area_key, area_label) for a guild name."""
        n = gname.lower()
        if 'fiber' in n:
            return ('fiber_processing', 'fiber processing')
        if 'butyrate' in n:
            return ('gut_lining', 'gut lining protection')
        if 'cross' in n:
            return ('fiber_processing', 'fiber processing')
        if 'bifido' in n or 'hmo' in n or 'oligosaccharide' in n:
            return ('bifidobacteria', 'bifidobacteria')
        if 'mucin' in n:
            return ('mucin_degradation', 'mucin degradation')
        if 'proteolytic' in n:
            return ('inflammatory_pressure', 'inflammatory pressure')
        return ('gut_ecology', 'gut ecology')

    # Track which area_keys already have a challenge flagged (for deduplication in rules 11+12)
    flagged_area_keys = set()

    # 1. Inability to process prebiotics / fiber below range
    if fiber_abund < 30.0 and fcr_state == 'sluggish':
        challenges.append({
            'icon': '🌾',
            'title': 'Limited prebiotic processing',
            'area_key': 'fiber_processing',
            'area_label': 'fiber processing',
            'severity': 'high',
            'text': f'Your fiber-processing bacteria are below their healthy range at {fiber_abund:.1f}% (target 30-50%), and fermentation efficiency is reduced. This means prebiotic fibers aren\'t being fully converted into the beneficial compounds your gut needs.',
        })
        flagged_area_keys.add('fiber_processing')
    elif fiber_abund < 30.0:
        challenges.append({
            'icon': '🌾',
            'title': 'Fiber-processing bacteria below range',
            'area_key': 'fiber_processing',
            'area_label': 'fiber processing',
            'severity': 'moderate',
            'text': f'Your fiber-processing bacteria are at {fiber_abund:.1f}% — below their healthy range of 30-50%. This is the entry point for the entire fermentation chain, so supporting this group is a primary target.',
        })
        flagged_area_keys.add('fiber_processing')

    # 2. Inflammatory pressure
    ppr_state = _dial('putrefaction_pressure')
    if ppr_state == 'protein_pressure' or proteo_abund > 5.0:
        challenges.append({
            'icon': '🔥',
            'title': 'Elevated inflammatory pressure',
            'area_key': 'inflammatory_pressure',
            'area_label': 'inflammatory pressure',
            'severity': 'high',
            'text': f'Your protein-fermenting bacteria are elevated{f" at {proteo_abund:.1f}% (above the healthy 1-5% range)" if proteo_abund > 5.0 else ""}, and your fermentation byproduct profile is shifted toward harsh protein-fermentation compounds. This creates low-grade inflammatory signals in the gut.',
        })
        flagged_area_keys.add('inflammatory_pressure')

    # 3. Excess protein fermentation (when multiple markers align — merged with rule 2)
    cur_state = _dial('main_fuel')
    if proteo_abund > 5.0 and ppr_state == 'protein_pressure' and cur_state == 'protein_driven':
        # This intensifies rule 2 rather than adding a separate area
        challenges.append({
            'icon': '🥩',
            'title': 'Excess protein fermentation pattern',
            'area_key': 'inflammatory_pressure',
            'area_label': 'inflammatory pressure',
            'severity': 'high',
            'text': 'Multiple markers — elevated proteolytic bacteria, protein-dominant fermentation balance, and harsh byproduct profile — all point in the same direction. Protein fermentation is dominating where carbohydrate fermentation should be.',
        })
        # area already in flagged_area_keys

    # 4. Absence or depletion of beneficial guilds
    for gname, gdata in bg.items():
        is_beneficial = any(k in gname.lower() for k in ['fiber', 'butyrate', 'cross', 'bifido', 'hmo'])
        abund = gdata.get('abundance', 0)
        clr = gdata.get('clr')
        status = gdata.get('status', '')
        if is_beneficial and (abund == 0 or (abund < 2.0 and 'below' in status.lower())):
            ak, al = _guild_area(gname)
            clr_txt = f' and is being outcompeted (CLR {clr:+.2f})' if clr is not None and clr < -0.5 else ''
            challenges.append({
                'icon': '⚠️',
                'title': f'{gname} — absent or critically low',
                'area_key': ak,
                'area_label': al,
                'severity': 'critical',
                'text': f'Your {gname.lower()} are{"completely absent" if abund == 0 else f" at only {abund:.1f}%"}{clr_txt}. This is a significant gap — this bacterial group plays an essential role in your gut\'s fermentation network.',
            })
            flagged_area_keys.add(ak)

    # 5. Thin or poorly protected gut lining (≥2 of 3 signals)
    signals = 0
    if butyrate_abund < 10.0: signals += 1
    if mdr_state == 'heavy_mucus': signals += 1
    if mucin_abund > 4.0: signals += 1
    if signals >= 2:
        challenges.append({
            'icon': '🧱',
            'title': 'Reduced gut lining protection',
            'area_key': 'gut_lining',
            'area_label': 'gut lining protection',
            'severity': 'high',
            'text': 'Multiple markers suggest your gut lining is under pressure: ' + (
                f'butyrate producers are low ({butyrate_abund:.1f}%), ' if butyrate_abund < 10.0 else ''
            ) + (
                'bacteria are relying heavily on mucus for fuel, ' if mdr_state == 'heavy_mucus' else ''
            ) + (
                f'and mucin degraders are elevated ({mucin_abund:.1f}%). ' if mucin_abund > 4.0 else ''
            ) + 'The barrier is being eroded faster than it is repaired.',
        })
        flagged_area_keys.add('gut_lining')

    # 6. Monocultures (J < 0.40 on any guild)
    for gname, gdata in bg.items():
        j = gdata.get('evenness', gdata.get('redundancy', 1.0))
        abund = gdata.get('abundance', 0)
        if j < 0.40 and abund >= 1.0:
            challenges.append({
                'icon': '🎲',
                'title': f'Monoculture risk — {gname}',
                'area_key': 'monoculture',
                'area_label': 'monoculture risk',
                'severity': 'moderate',
                'text': f'Your {gname.lower()} show low internal diversity (evenness score {j:.2f}) — most of the functional capacity is concentrated in a single species. This is fragile: if that species is disrupted, the guild loses its function with little backup.',
            })
            flagged_area_keys.add('monoculture')

    # 7. Fragile ecosystem / poor resilience
    if shannon < SHANNON_LOW or avg_j < 0.50:
        challenges.append({
            'icon': '📉',
            'title': 'Fragile ecosystem',
            'area_key': 'resilience',
            'area_label': 'ecosystem resilience',
            'severity': 'moderate',
            'text': f'{"Low species diversity (Shannon " + str(round(shannon, 2)) + ") " if shannon < SHANNON_LOW else ""}{"Low average guild evenness (" + str(round(avg_j, 2)) + ") " if avg_j < 0.50 else ""}suggest your gut ecosystem is more vulnerable to disruption. A less diverse community has fewer functional backups.',
        })
        flagged_area_keys.add('resilience')

    # 8a. Moderate diversity (Q25–Q50) — new challenge band
    if SHANNON_LOW <= shannon < SHANNON_MED and 'diversity' not in flagged_area_keys:
        challenges.append({
            'icon': '🌿',
            'title': 'Moderate diversity',
            'area_key': 'diversity',
            'area_label': 'microbial diversity',
            'severity': 'moderate',
            'text': f'Your species diversity (Shannon {shannon:.2f}) is in the lower half of the normal range — below the population median of 3.29 but above the 25th percentile. Building more diversity over time is a worthwhile goal and one of the indirect benefits of your prebiotic protocol.',
        })
        flagged_area_keys.add('diversity')

    # 8b. Low diversity (below Q25)
    elif shannon < SHANNON_LOW and 'diversity' not in flagged_area_keys:
        challenges.append({
            'icon': '🌵',
            'title': 'Low microbial diversity',
            'area_key': 'diversity',
            'area_label': 'microbial diversity',
            'severity': 'moderate',
            'text': f'Your species diversity score (Shannon {shannon:.2f}) places you below the 25th percentile of the reference population. Limited diversity means fewer types of bacteria available to handle different tasks — building diversity is a key long-term goal.',
        })
        flagged_area_keys.add('diversity')

    # 9. Low bifidobacteria
    if bifido_abund < 2.0:
        challenges.append({
            'icon': '🔻',
            'title': 'Low bifidobacteria',
            'area_key': 'bifidobacteria',
            'area_label': 'bifidobacteria',
            'severity': 'high' if bifido_abund == 0 else 'moderate',
            'text': f'Your Bifidobacteria are {"absent" if bifido_abund == 0 else f"at only {bifido_abund:.1f}%"} — below the healthy 2-10% range. Bifidobacteria are important for immune function, producing protective compounds, and amplifying short-chain fatty acid production throughout the fermentation chain.',
        })
        flagged_area_keys.add('bifidobacteria')

    # 10. Excessive mucin degradation
    if mucin_abund > 4.0 and mdr_state == 'heavy_mucus':
        challenges.append({
            'icon': '🕳️',
            'title': 'Excessive mucin degradation',
            'area_key': 'mucin_degradation',
            'area_label': 'mucin degradation',
            'severity': 'high',
            'text': f'Your mucin-degrading bacteria are elevated at {mucin_abund:.1f}% (above the 1-4% healthy range) and your bacteria are relying heavily on mucus for fuel. This combination means your gut\'s protective lining is being actively eroded.',
        })
        flagged_area_keys.add('mucin_degradation')

    # 11. Diet-dependent bacteria under substrate stress
    # Only flag if the area hasn't already been covered by an earlier rule
    for gname, gdata in bg.items():
        is_beneficial = any(k in gname.lower() for k in ['fiber', 'butyrate', 'cross', 'bifido', 'hmo'])
        abund = gdata.get('abundance', 0)
        clr = gdata.get('clr')
        status = gdata.get('status', '')
        if is_beneficial and clr is not None and clr > 0.3 and 'Below range' in status:
            ak, al = _guild_area(gname)
            if ak not in flagged_area_keys:
                challenges.append({
                    'icon': '🥲',
                    'title': f'Substrate-starved bacteria — {gname}',
                    'area_key': ak,
                    'area_label': al,
                    'severity': 'moderate',
                    'text': f'Your {gname.lower()} are trying to grow (positive competitive position, CLR {clr:+.2f}) but are below their target range at {abund:.1f}%. They\'re winning the competition but don\'t have enough substrate to fuel the expansion. This is a substrate-limitation pattern, not a colonization problem.',
                })
                flagged_area_keys.add(ak)

    # 12. Weak competitive position despite adequate abundance
    # Only flag if the area hasn't already been covered by an earlier rule
    for gname, gdata in bg.items():
        is_beneficial = any(k in gname.lower() for k in ['fiber', 'butyrate', 'cross', 'bifido', 'hmo'])
        abund = gdata.get('abundance', 0)
        clr = gdata.get('clr')
        status = gdata.get('status', '')
        if is_beneficial and clr is not None and clr < -1.0 and 'Within range' in status:
            ak, al = _guild_area(gname)
            if ak not in flagged_area_keys:
                challenges.append({
                    'icon': '⬇️',
                    'title': f'Competitive disadvantage — {gname}',
                    'area_key': ak,
                    'area_label': al,
                    'severity': 'moderate',
                    'text': f'Your {gname.lower()} are at {abund:.1f}% (within range) but are being outcompeted by other bacteria (CLR {clr:+.2f}). Despite their adequate numbers, they\'re under competitive pressure and may struggle to maintain their position over time.',
                })
                flagged_area_keys.add(ak)

    # 13. Dysbiosis marker detected
    if any_dysbiosis:
        detected = [
            taxon for taxon, v in dysbiosis.items()
            if isinstance(v, dict) and v.get('abundance', 0) > 0.1
        ]
        challenges.append({
            'icon': '🚨',
            'title': 'Dysbiosis marker detected',
            'area_key': 'safety',
            'area_label': 'safety profile',
            'severity': 'critical',
            'text': f'The following dysbiosis-associated bacteria were detected at concerning levels: {", ".join(detected).replace("_", " ")}. These organisms are associated with gut inflammation and require monitoring.',
        })

    # Sort: critical first, then high, then moderate
    sev_order = {'critical': 0, 'high': 1, 'moderate': 2}
    challenges.sort(key=lambda x: sev_order.get(x.get('severity', 'moderate'), 2))

    # ── Compute distinct areas for cover summary ──────────────────────────────
    seen_areas = {}
    for c in challenges:
        ak = c.get('area_key', '')
        if ak and ak not in seen_areas:
            seen_areas[ak] = c.get('area_label', ak.replace('_', ' '))

    distinct_areas = list(seen_areas.values())  # ordered by first occurrence

    return {
        'strengths': strengths[:6],
        'challenges': challenges[:6],
        'all_strengths': strengths,
        'all_challenges': challenges,
        'distinct_areas': distinct_areas,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  PROFILE EXTRACTION FROM QUESTIONNAIRE
# ════════════════════════════════════════════════════════════════════════════════

def extract_profile(questionnaire: dict, formulation: dict) -> dict:
    """Extract client-facing profile summary from questionnaire."""
    if not questionnaire:
        return {
            'first_name': 'Client',
            'age': None,
            'sex': None,
            'diet': None,
            'stress': None,
            'sleep': None,
            'goals': [],
            'sensitivity': None,
        }

    qdata = questionnaire.get('questionnaire_data', {})
    basic = qdata.get('step_1', {}).get('basic', {})
    goals_data = qdata.get('step_1', {}).get('goals', {})
    step5 = qdata.get('step_5', {})
    step4 = qdata.get('step_4', {})

    # Goals to display labels
    GOAL_LABELS = {
        'strengthen_immune_resilience': 'Immune resilience',
        'improve_mood_reduce_anxiety': 'Mood & anxiety',
        'improve_sleep_quality': 'Sleep quality',
        'improve_digestive_comfort': 'Digestive comfort',
        'improve_energy_levels': 'Energy',
        'weight_management': 'Weight management',
        'metabolic_support': 'Metabolic health',
        'reduce_bloating': 'Less bloating',
        'improve_bowel_regularity': 'Bowel regularity',
        'other': goals_data.get('other_goal_details', 'Other goal'),
    }

    goals_raw = goals_data.get('main_goals_ranked', [])
    goals_display = []
    for g in goals_raw[:3]:
        label = GOAL_LABELS.get(g, g.replace('_', ' ').title())
        goals_display.append(label)

    # Sensitivity from formulation if available
    sensitivity = None
    if formulation:
        sensitivity = formulation.get('input_summary', {}).get('questionnaire_driven', {}).get('sensitivity_classification')
    if not sensitivity:
        # Infer from bloating severity
        bloating_sev = qdata.get('step_2', {}).get('bloating_severity', 5)
        if bloating_sev and int(bloating_sev) >= 7:
            sensitivity = 'high'
        elif bloating_sev and int(bloating_sev) >= 4:
            sensitivity = 'moderate'
        else:
            sensitivity = 'low'

    # Diet display
    DIET_LABELS = {
        'omnivore': 'Omnivore',
        'vegetarian': 'Vegetarian',
        'vegan': 'Vegan',
        'pescatarian': 'Pescatarian',
        'mediterranean': 'Mediterranean',
        'low_carb': 'Low-carb',
        'keto': 'Keto',
        'paleo': 'Paleo',
    }
    diet_raw = step4.get('diet_pattern', basic.get('primary_regional_diet_pattern', ''))
    diet_display = DIET_LABELS.get(diet_raw, diet_raw.replace('_', ' ').title() if diet_raw else None)

    sex_raw = questionnaire.get('biological_sex', basic.get('biological_sex', ''))
    SEX_LABELS = {'female': 'Female', 'male': 'Male', 'other': 'Other'}

    return {
        'first_name': questionnaire.get('first_name', 'Client'),
        'age': questionnaire.get('age') or basic.get('age'),
        'sex': SEX_LABELS.get(sex_raw, sex_raw.title() if sex_raw else None),
        'diet': diet_display,
        'stress': step5.get('overall_stress_level_1_10'),
        'sleep': step5.get('sleep_quality_rating_1_10'),
        'goals': goals_display,
        'sensitivity': sensitivity.title() if sensitivity else None,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  GOOD NEWS BOX
# ════════════════════════════════════════════════════════════════════════════════

def extract_good_news(analysis: dict) -> str:
    """Extract the most relevant good news sentence from existing data."""
    km = analysis.get('key_messages', {})
    good_news = km.get('good_news', {})

    if isinstance(good_news, dict):
        # Try non_expert versions in order
        for key in ['resilience', 'adaptation_capacity', 'reversibility']:
            item = good_news.get(key, {})
            if isinstance(item, dict) and item.get('non_expert'):
                return item['non_expert']
            if isinstance(item, str) and item:
                return item

    if isinstance(good_news, str) and good_news:
        return good_news

    # Fallback: use recovery potential
    exec_sum = analysis.get('executive_summary', {})
    rp = exec_sum.get('recovery_potential', '')
    if isinstance(rp, dict):
        return rp.get('non_expert', rp.get('scientific', ''))
    if rp:
        return str(rp)

    return "Your gut has strong potential to respond well to targeted support. The imbalances identified here are addressable with the right approach."


# ════════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — The Story Behind Your Results
#  Microbiome-first: start from observed deviations, explain them using
#  the evidence map + LLM reasoning grounded in scientific literature.
# ════════════════════════════════════════════════════════════════════════════════

def _get_nested(obj: dict, path: str, default=None):
    """Safely resolve a dot-separated path in a nested dict."""
    keys = path.split('.')
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _evaluate_condition(questionnaire: dict, condition: dict) -> bool:
    """Evaluate a single trigger condition against questionnaire data."""
    if not questionnaire:
        return False
    operator = condition.get('operator', '==')
    field_path = condition.get('field_path', '')
    expected = condition.get('value')

    # Strip leading "questionnaire_data." since it may or may not be present
    # depending on questionnaire structure variant
    qdata = questionnaire.get('questionnaire_data', questionnaire)
    stripped_path = field_path.replace('questionnaire_data.', '', 1) if field_path.startswith('questionnaire_data.') else field_path
    actual = _get_nested(qdata, stripped_path)

    if actual is None:
        return False

    if operator == '==':
        return actual == expected
    elif operator == '!=':
        return actual != expected
    elif operator == '>=':
        try:
            return float(actual) >= float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == '<=':
        try:
            return float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == '>':
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == '<':
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == 'in':
        if isinstance(expected, list):
            return actual in expected
        return actual == expected
    elif operator == 'not_in':
        if isinstance(expected, list):
            return actual not in expected
        return actual != expected
    elif operator == 'contains':
        if isinstance(actual, list):
            return expected in actual
        if isinstance(actual, str):
            return expected in actual
        return False
    elif operator == 'contains_any':
        if isinstance(actual, list):
            return any(v in actual for v in (expected if isinstance(expected, list) else [expected]))
        if isinstance(actual, str):
            return any(v in actual for v in (expected if isinstance(expected, list) else [expected]))
        return False
    elif operator == 'exists':
        return actual is not None and actual != '' and actual != []
    return False


def _check_guild_match(analysis: dict, guild_keys: list, match_requires: str) -> bool:
    """Check whether the predicted guild deviation actually exists in this sample."""
    if match_requires == 'context_only':
        return True  # Always include as context, no guild check needed
    if match_requires == 'any_deviation':
        return True  # Any microbiome deviation counts
    if not guild_keys:
        return True

    bg = analysis.get('bacterial_groups', {})
    for guild_key in guild_keys:
        for gname, gdata in bg.items():
            if guild_key.lower() in gname.lower() or gname.lower() in guild_key.lower():
                status = gdata.get('status', '')
                abund = gdata.get('abundance', 0)
                if match_requires == 'below_range':
                    if 'Below' in status or 'Absent' in status or abund == 0:
                        return True
                elif match_requires == 'above_range':
                    if 'Above' in status:
                        return True
                elif match_requires == 'low_diversity':
                    return True  # checked separately via shannon
    return False


def _check_diversity_match(analysis: dict, match_requires: str) -> bool:
    """Check diversity-based match conditions."""
    if match_requires != 'low_diversity':
        return False
    eco = analysis.get('ecological_metrics', {})
    shannon = eco.get('diversity', {}).get('shannon', {}).get('value') or 0
    debug = analysis.get('_debug', {})
    if shannon == 0:
        shannon = debug.get('raw_metrics', {}).get('Shannon', 0) or 0
    return shannon < 2.79  # Q25 cutoff (matches SHANNON_LOW)


# ── Guild client-facing names (for deviation display) ────────────────────────
_GUILD_CLIENT_NAMES = {
    'Fiber Degraders': 'Fiber-Processing Bacteria',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Butyrate Producers': 'Gut-Lining Energy Producers',
    'Cross-Feeders': 'Intermediate Processors',
    'Mucin Degraders': 'Mucus-Layer Bacteria',
    'Proteolytic Dysbiosis Guild': 'Protein-Fermenting Bacteria',
    'Proteolytic Guild': 'Protein-Fermenting Bacteria',
}
_GUILD_ICONS = {
    'Fiber Degraders': '🌾',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': '✨',
    'Butyrate Producers': '⚡',
    'Cross-Feeders': '🔗',
    'Mucin Degraders': '🛡️',
    'Proteolytic Dysbiosis Guild': '🔥',
    'Proteolytic Guild': '🔥',
}
_BENEFICIAL_GUILDS = {'Fiber Degraders', 'HMO/Oligosaccharide-Utilising Bifidobacteria',
                      'Butyrate Producers', 'Cross-Feeders'}
_CONTEXTUAL_GUILDS = {'Mucin Degraders', 'Proteolytic Dysbiosis Guild', 'Proteolytic Guild'}


def _detect_microbiome_deviations(analysis: dict) -> list:
    """
    Detect active microbiome deviations in this sample.
    Returns list of deviation dicts:
      {key, type, guild_key, client_label, icon, value_str, range_str, description}
    Covers: guild abundance deviations, diversity, and metabolic dial states.
    """
    deviations = []
    bg = analysis.get('bacterial_groups', {})
    eco = analysis.get('ecological_metrics', {})
    dials = analysis.get('metabolic_function', {}).get('dials', {})
    debug = analysis.get('_debug', {})
    raw = debug.get('raw_metrics', {})

    GUILD_RANGES = {
        'Fiber Degraders': (30, 50),
        'HMO/Oligosaccharide-Utilising Bifidobacteria': (2, 10),
        'Butyrate Producers': (10, 25),
        'Cross-Feeders': (6, 12),
        'Mucin Degraders': (1, 4),
        'Proteolytic Dysbiosis Guild': (1, 5),
        'Proteolytic Guild': (1, 5),
    }

    for gname, gdata in bg.items():
        abund = gdata.get('abundance', 0)
        status = gdata.get('status', '')
        client_name = _GUILD_CLIENT_NAMES.get(gname, gname)
        icon = _GUILD_ICONS.get(gname, '🔬')
        r_min, r_max = GUILD_RANGES.get(gname, (0, 100))

        if gname in _BENEFICIAL_GUILDS:
            if 'Below' in status or 'Absent' in status or abund == 0:
                deviations.append({
                    'key': f'{gname}__below',
                    'type': 'below_range',
                    'guild_key': gname,
                    'client_label': client_name,
                    'icon': icon,
                    'value_str': 'Absent' if abund == 0 else f'{abund:.1f}%',
                    'range_str': f'{r_min}–{r_max}%',
                    'description': (
                        f'Your {client_name} are {"absent" if abund == 0 else f"at {abund:.1f}%"} '
                        f'— below the healthy range of {r_min}–{r_max}%.'
                    ),
                })

        elif gname in _CONTEXTUAL_GUILDS:
            if 'Above' in status:
                deviations.append({
                    'key': f'{gname}__above',
                    'type': 'above_range',
                    'guild_key': gname,
                    'client_label': client_name,
                    'icon': icon,
                    'value_str': f'{abund:.1f}%',
                    'range_str': f'{r_min}–{r_max}%',
                    'description': (
                        f'Your {client_name} are elevated at {abund:.1f}% '
                        f'(above the healthy maximum of {r_max}%).'
                    ),
                })

    # Diversity deviations — Q25 (low) and Q25–Q50 (moderate)
    shannon = eco.get('diversity', {}).get('shannon', {}).get('value') or raw.get('Shannon') or 0
    if shannon > 0 and shannon < 2.79:
        deviations.append({
            'key': 'diversity__low',
            'type': 'low_diversity',
            'guild_key': None,
            'client_label': 'Microbial Diversity',
            'icon': '🌿',
            'value_str': f'Shannon {shannon:.2f}',
            'range_str': '> 2.79 (population Q25)',
            'description': (
                f'Your gut microbial diversity is low (Shannon {shannon:.2f}), '
                f'placing you below the 25th percentile of the reference population.'
            ),
        })
    elif shannon > 0 and shannon < 3.29:
        # Moderate diversity: between Q25 and Q50 — below the population median
        deviations.append({
            'key': 'diversity__moderate',
            'type': 'moderate_diversity',
            'guild_key': None,
            'client_label': 'Microbial Diversity',
            'icon': '🌿',
            'value_str': f'Shannon {shannon:.2f}',
            'range_str': '> 3.29 (population median)',
            'description': (
                f'Your gut microbial diversity (Shannon {shannon:.2f}) is in the lower half of '
                f'the reference population — below the population median of 3.29.'
            ),
        })

    # Metabolic dial deviations
    ppr_state = dials.get('putrefaction_pressure', {}).get('state', '')
    mdr_state = dials.get('mucus_dependency', {}).get('state', '')
    fcr_state = dials.get('fermentation_efficiency', {}).get('state', '')

    if ppr_state == 'protein_pressure':
        deviations.append({
            'key': 'dial__protein_pressure',
            'type': 'above_range',
            'guild_key': 'Proteolytic Dysbiosis Guild',
            'client_label': 'Elevated Protein Fermentation',
            'icon': '🔥',
            'value_str': 'Protein-dominant',
            'range_str': 'SCFA-dominant (optimal)',
            'description': (
                'Your fermentation is dominated by protein breakdown rather than '
                'fiber fermentation, producing harsher byproducts.'
            ),
        })

    if mdr_state == 'heavy_mucus':
        deviations.append({
            'key': 'dial__heavy_mucus',
            'type': 'above_range',
            'guild_key': 'Mucin Degraders',
            'client_label': 'Gut Barrier Under Pressure',
            'icon': '🧱',
            'value_str': 'Mucus-reliant',
            'range_str': 'Diet-fed (optimal)',
            'description': (
                'Your gut bacteria are relying heavily on your protective mucus layer '
                'for fuel instead of dietary fiber — putting the gut barrier under sustained pressure.'
            ),
        })

    if fcr_state == 'sluggish':
        deviations.append({
            'key': 'dial__sluggish_fermentation',
            'type': 'below_range',
            'guild_key': 'Cross-Feeders',
            'client_label': 'Sluggish Fermentation Efficiency',
            'icon': '⚙️',
            'value_str': 'Inefficient',
            'range_str': 'Efficient (optimal)',
            'description': (
                'Your fermentation assembly line is running slowly — intermediate '
                'compounds are not being fully converted into the protective compounds your gut needs.'
            ),
        })

    return deviations


def _signal_explains_deviation(signal: dict, deviation: dict) -> bool:
    """
    Check whether a KB signal can explain a detected microbiome deviation.
    context_only signals are excluded — they are background info, not explanations of specific deviations.
    """
    mr = signal.get('match_requires', 'context_only')
    if mr == 'context_only':
        return False  # not a specific deviation predictor

    dev_type = deviation.get('type', '')
    dev_guild = deviation.get('guild_key', '')

    if mr == 'low_diversity' and dev_type == 'low_diversity':
        return True

    if mr == 'any_deviation':
        return True

    sig_guilds = signal.get('guild_keys', [])
    sig_metrics = signal.get('metric_keys', [])

    if mr == 'below_range' and dev_type == 'below_range':
        if not sig_guilds and not sig_metrics:
            return True
        for sg in sig_guilds:
            if sg.lower() in (dev_guild or '').lower() or (dev_guild or '').lower() in sg.lower():
                return True
        if 'shannon' in sig_metrics and dev_type == 'low_diversity':
            return True

    if mr == 'above_range' and dev_type == 'above_range':
        if not sig_guilds:
            return True
        for sg in sig_guilds:
            if sg.lower() in (dev_guild or '').lower() or (dev_guild or '').lower() in sg.lower():
                return True

    return False


def _extract_questionnaire_context(questionnaire: dict) -> dict:
    """
    Extract a rich, human-readable context object from the questionnaire.
    Used to give the LLM concrete personal details to reference.
    """
    if not questionnaire:
        return {}

    qdata = questionnaire.get('questionnaire_data', {})
    first_name = questionnaire.get('first_name', 'the client')
    age = questionnaire.get('age') or qdata.get('step_1', {}).get('basic', {}).get('age')
    sex = questionnaire.get('biological_sex', '')

    step3 = qdata.get('step_3', {})
    step4 = qdata.get('step_4', {})
    step5 = qdata.get('step_5', {})
    step7 = qdata.get('step_7', {})

    # Antibiotic courses — full detail
    ab_courses = step3.get('antibiotic_courses', [])
    antibiotic_summary = []
    for ab in ab_courses:
        name = ab.get('name', 'unknown')
        condition = ab.get('condition', '')
        when = ab.get('when_month_year', '')
        duration = ab.get('duration_days', '')
        lingering = ab.get('lingering_issues', '')
        entry = f"{name} for {condition} ({when}, {duration} days)"
        if lingering and lingering.lower() not in ('no', 'none', ''):
            entry += f" — lingering: {lingering}"
        antibiotic_summary.append(entry)

    # Lifestyle context
    stress = step5.get('overall_stress_level_1_10')
    stress_symptoms = step5.get('stress_symptoms', [])
    sleep_quality = step5.get('sleep_quality_rating_1_10')
    sleep_issues = step5.get('sleep_issues', [])
    activity_level = qdata.get('step_1', {}).get('basic', {}).get('average_daily_activity_level', '')
    vigorous_days = step5.get('vigorous_days_per_week', 0)
    steps = step5.get('average_daily_step_count', '')

    # Diet
    fiber_intake = step4.get('fiber_intake', '')
    diet_pattern = step4.get('diet_pattern', '')
    alcohol_freq = step4.get('alcohol_frequency', '')
    alcohol_drinks = step4.get('alcohol_drinks_per_week', '')
    tobacco = step4.get('tobacco_use', '')
    processed_foods = step4.get('processed_foods_frequency', step4.get('processed_foods', ''))
    fermented_foods = step4.get('fermented_foods_frequency', '')
    protein_pattern = step4.get('protein_pattern', '')

    # Goals
    goals = qdata.get('step_1', {}).get('goals', {}).get('main_goals_ranked', [])
    other_goal = qdata.get('step_1', {}).get('goals', {}).get('other_goal_details', '')

    # Health context
    diagnoses = step3.get('diagnoses', [])
    vitamin_deficiencies = step7.get('vitamin_deficiencies', [])
    skin_concerns = step7.get('skin_concerns', [])

    return {
        'first_name': first_name,
        'age': age,
        'sex': sex,
        'antibiotics': antibiotic_summary,
        'antibiotics_frequency_lifetime': step3.get('antibiotics_frequency', ''),
        'stress_level': stress,
        'stress_symptoms': stress_symptoms,
        'sleep_quality': sleep_quality,
        'sleep_issues': sleep_issues,
        'activity_level': activity_level,
        'vigorous_days_per_week': vigorous_days,
        'daily_steps': steps,
        'fiber_intake': fiber_intake,
        'diet_pattern': diet_pattern,
        'alcohol_frequency': alcohol_freq,
        'alcohol_drinks_per_week': alcohol_drinks,
        'tobacco_use': tobacco,
        'processed_foods_frequency': processed_foods,
        'fermented_foods_frequency': fermented_foods,
        'protein_pattern': protein_pattern,
        'goals': goals,
        'other_goal': other_goal,
        'diagnoses': diagnoses,
        'vitamin_deficiencies': vitamin_deficiencies,
        'skin_concerns': skin_concerns,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  ELICIT API — Scientific literature enrichment for Section 3
# ════════════════════════════════════════════════════════════════════════════════

# Domain → focused search query for Elicit
ELICIT_DOMAIN_QUERIES = {
    'antibiotic_use': 'antibiotics deplete beneficial gut bacteria fiber-processing humans',
    'low_dietary_fiber': 'low dietary fiber reduces gut bacteria diversity health',
    'high_protein_diet': 'high protein low carbohydrate diet shifts gut microbiome fermentation',
    'alcohol_consumption': 'regular alcohol consumption gut microbiome bacteria health effects',
    'smoking': 'tobacco smoking gut microbiome bacterial diversity human',
    'stress_anxiety': 'chronic stress anxiety gut bacteria microbiome bidirectional',
    'poor_sleep': 'poor sleep quality gut microbiome bacteria circadian',
    'bloating_ibs': 'gut bacteria fermentation imbalance bloating IBS digestive symptoms',
    'low_physical_activity': 'physical activity exercise gut microbiome bacteria diversity',
    'ultra_processed_diet': 'ultra-processed food gut microbiome bacteria diversity health',
    'fatigue_energy': 'gut microbiome bacteria fatigue energy chronic fatigue',
    'skin_symptoms': 'gut bacteria skin health gut skin axis inflammation',
    'immune_gut_axis': 'gut bacteria immune system butyrate barrier immune function',
    'metabolic_risk': 'gut microbiome metabolic health obesity diabetes gut bacteria',
}


def _format_citation(paper: dict) -> str:
    """Format a paper as 'FirstAuthorSurname et al., Year' or 'Surname, Year'."""
    authors = paper.get('authors', [])
    year = paper.get('year')
    title = paper.get('title', '')

    if not authors:
        return f'Unknown, {year}' if year else title[:60]

    # Extract first author surname (last word of first author's name)
    first_author_full = authors[0] if isinstance(authors[0], str) else str(authors[0])
    parts = first_author_full.strip().split()
    first_surname = parts[-1] if parts else first_author_full

    if len(authors) > 1:
        citation = f'{first_surname} et al.'
    else:
        citation = first_surname

    if year:
        citation += f', {year}'

    return citation


def _query_elicit(question: str, api_key: str, n_results: int = 3) -> list:
    """
    Query Elicit API for relevant papers.
    Returns list of {citation, title, abstract, venue, year} dicts.
    Empty list on any error — always fails gracefully.
    """
    if not api_key:
        return []

    try:
        import urllib.request
        import urllib.error

        url = 'https://elicit.com/api/v1/search'
        payload = json.dumps({'query': question, 'numResults': n_results}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'User-Agent': 'NB1Health-Report/1.0',
            },
            method='POST',
        )

        import socket
        socket.setdefaulttimeout(5)

        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode('utf-8'))

        papers = body.get('papers', [])
        results = []
        for paper in papers[:n_results]:
            if not paper.get('title'):
                continue
            results.append({
                'citation': _format_citation(paper),
                'title': paper.get('title', ''),
                'abstract': (paper.get('abstract') or '')[:800],
                'venue': paper.get('venue', ''),
                'year': paper.get('year'),
                'doi': paper.get('doi', ''),
                'authors': paper.get('authors', []),
            })
        return results

    except Exception as e:
        logger.debug(f"Elicit query failed (non-critical): {e}")
        return []


def _format_apa_citation(paper: dict) -> str:
    """Format a paper as APA style: Author(s) (Year). Title. Journal. DOI."""
    authors = paper.get('authors', [])
    year = paper.get('year', 'n.d.')
    title = paper.get('title', '')
    venue = paper.get('venue', '')
    doi = paper.get('doi', '')

    # Format authors: up to 6, then et al.
    def _fmt_author(name: str) -> str:
        parts = name.strip().split()
        if len(parts) <= 1:
            return name
        surname = parts[-1]
        initials = '. '.join(p[0] for p in parts[:-1] if p) + '.'
        return f'{surname}, {initials}'

    if not authors:
        author_str = 'Unknown author'
    elif len(authors) == 1:
        author_str = _fmt_author(authors[0])
    elif len(authors) <= 6:
        formatted = [_fmt_author(a) for a in authors]
        author_str = ', '.join(formatted[:-1]) + ', & ' + formatted[-1]
    else:
        formatted = [_fmt_author(a) for a in authors[:6]]
        author_str = ', '.join(formatted) + ', et al.'

    apa = f'{author_str} ({year}). {title}.'
    if venue:
        apa += f' *{venue}*.'
    if doi:
        apa += f' https://doi.org/{doi.lstrip("https://doi.org/")}'
    return apa


def _generate_elicit_queries_llm(
    deviations: list,
    questionnaire_factors: list,
    model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
    region: str = 'eu-west-1',
) -> list:
    """
    Step 1: Ask Claude to generate targeted Elicit search queries.
    Returns list of query strings — one per (deviation × factor) pair.
    Falls back to empty list if unavailable.
    """
    try:
        import boto3
        client = boto3.client('bedrock-runtime', region_name=region)
    except Exception:
        return []

    dev_text = '; '.join(f"{d['client_label']} at {d['value_str']}" for d in deviations)
    factor_text = '\n'.join(f"- {f}" for f in questionnaire_factors)

    prompt = f"""You are a biomedical researcher designing a literature search.

A gut microbiome test found this deviation: {dev_text}

The client's health history includes these factors:
{factor_text}

For each factor listed above, generate exactly one search query that would find peer-reviewed papers showing how that factor links to this specific gut bacteria finding. Output one query per factor.

Rules:
- Each query must be short keyword-style (5-10 words), not a full sentence
- Focus on the specific bacteria or gut function, not generic terms
- Include directionality where meaningful (e.g. "depletion" or "reduction")
- No medical disease names (no cancer, IBD, etc.)
- Example format: "effects of antibiotics on fiber-degrading gut bacteria humans"

Return ONLY a JSON array of strings — one string per factor, no other text."""

    try:
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 400,
                'temperature': 0.5,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        raw = result_body['content'][0]['text'].strip()
        # Strip code fences if present
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1] if '\n' in raw else raw[3:]
            if raw.endswith('```'):
                raw = raw[:-3]
        queries = json.loads(raw)
        if isinstance(queries, list):
            return [q for q in queries if isinstance(q, str) and q.strip()]
    except Exception as e:
        logger.debug(f"Query generation LLM call failed: {e}")
    return []


def _fetch_elicit_for_queries(queries: list, elicit_key: str) -> list:
    """
    Fetch Elicit papers for a list of query strings in parallel.
    Returns flat list of paper dicts (deduplicated by title).
    """
    if not elicit_key or not queries:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    seen_titles = set()
    all_papers = []

    def _fetch_one(q):
        return _query_elicit(q, elicit_key, n_results=3)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_fetch_one, q): q for q in queries}
        for future in as_completed(futures, timeout=15):
            try:
                papers = future.result()
                for p in papers:
                    title = p.get('title', '')
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_papers.append(p)
            except Exception:
                pass

    return all_papers


def _call_section3_llm(
    deviations: list,
    questionnaire_context: dict,
    triggered_domains: list,
    evidence_kb: dict,
    elicit_papers: dict = None,
    model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
    region: str = 'eu-west-1',
) -> list:
    """
    Call the LLM to generate personalised Section 3 explanation for each deviation.

    Returns a list of dicts — one per deviation — with:
      {
        'deviation_key': str,
        'health_meaning': str,   # what this deviation means for this person's health
        'drivers': [             # what explains it — personal + evidence-grounded
          {'icon': str, 'label': str, 'text': str, 'evidence_label': str,
           'citations': [str]}   # Elicit citation strings
        ],
        'personal_synthesis': str  # 2-3 sentence personal conclusion
      }
    """
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not available — using deterministic fallback for Section 3")
        return []

    if elicit_papers is None:
        elicit_papers = {}

    # Build deviation summaries
    dev_lines = []
    for dev in deviations:
        dev_lines.append(
            f"- {dev['client_label']}: {dev['value_str']} (healthy range: {dev['range_str']}). "
            f"{dev['description']}"
        )

    # Build relevant evidence map context — KB signals + Elicit abstracts
    evidence_context_lines = []
    for domain_key in triggered_domains:
        domain_data = evidence_kb.get('domains', {}).get(domain_key, {})
        label = domain_data.get('domain_label', domain_key)
        directionality = domain_data.get('directionality', 'associative')
        summary = domain_data.get('root_cause_summary', {}).get('non_expert', '')
        # Collect the most relevant signal text
        best_signal_text = ''
        for sig in domain_data.get('signals', [])[:2]:
            t = sig.get('section3_text', {}).get('non_expert', '') or sig.get('mechanism', {}).get('non_expert', '')
            if t and len(t) > len(best_signal_text):
                best_signal_text = t
        domain_block = (
            f"Domain: {label} | Relationship: {directionality}\n"
            f"Summary: {summary}\n"
            f"Key finding: {best_signal_text}"
        )
        # Append Elicit paper abstracts if available for this domain
        papers = elicit_papers.get(domain_key, [])
        if papers:
            paper_lines = []
            for p in papers[:2]:
                abstract = p.get('abstract', '')
                citation = p.get('citation', '')
                if abstract:
                    paper_lines.append(f"[{citation}]: {abstract[:500]}")
            if paper_lines:
                domain_block += '\nRecent research abstracts:\n' + '\n'.join(paper_lines)
        evidence_context_lines.append(domain_block)

    # Build client context summary
    ctx = questionnaire_context
    first_name = ctx.get('first_name', 'the client')

    ab_text = ', '.join(ctx.get('antibiotics', [])) or 'none reported'
    goals_text = ', '.join(ctx.get('goals', [])) or 'not specified'

    context_summary = f"""Client: {first_name}, {ctx.get('age', '?')} years old, {ctx.get('sex', '')}
Diet: {ctx.get('diet_pattern', '?')}, fiber intake: {ctx.get('fiber_intake', '?')}, protein pattern: {ctx.get('protein_pattern', '?')}
Alcohol: {ctx.get('alcohol_frequency', '?')} ({ctx.get('alcohol_drinks_per_week', '?')} drinks/week)
Tobacco: {ctx.get('tobacco_use', '?')}
Stress: {ctx.get('stress_level', '?')}/10, symptoms: {', '.join(ctx.get('stress_symptoms', [])) or 'none'}
Sleep: {ctx.get('sleep_quality', '?')}/10, issues: {', '.join(ctx.get('sleep_issues', [])) or 'none'}
Activity: {ctx.get('activity_level', '?')}, vigorous exercise {ctx.get('vigorous_days_per_week', '?')} days/week
Antibiotics taken: {ab_text}
Antibiotic history (lifetime): {ctx.get('antibiotics_frequency_lifetime', '?')}
Fermented foods: {ctx.get('fermented_foods_frequency', '?')}
Goals: {goals_text}
Vitamin deficiencies noted: {', '.join(ctx.get('vitamin_deficiencies', [])) or 'none'}"""

    deviations_text = '\n'.join(dev_lines)
    evidence_text = '\n\n'.join(evidence_context_lines)

    # Include flat Elicit papers in evidence context if available
    flat_papers = elicit_papers.get('_flat', []) if isinstance(elicit_papers, dict) else []
    elicit_section = ''
    if flat_papers:
        paper_summaries = []
        for p in flat_papers[:8]:
            cit = p.get('citation', '')
            abstract = p.get('abstract', '')
            if abstract:
                paper_summaries.append(f"[{cit}]: {abstract[:400]}")
        if paper_summaries:
            elicit_section = '\n\nSCIENTIFIC LITERATURE — recent peer-reviewed papers retrieved for this specific client context:\n' + '\n'.join(paper_summaries)

    prompt = f"""You are writing Section 3 of a personalised gut microbiome health report for {first_name}.

This section is called "The Story Behind Your Results". The goal: the client reads this and thinks "that makes complete sense for me personally."

For each microbiome deviation found, write a free-flowing explanation answering the question: **"Why did this happen for {first_name} specifically?"**

CRITICAL WRITING RULES:
- Write in plain, warm, friendly English — like a knowledgeable friend explaining it over coffee.
- No jargon. No Latin names. No technical terms. No "microbiota", "dysbiosis", "CLR", "SCFA", "Lachnospiraceae", "taxa", "genus", "phylum".
- Say "gut bacteria", "fiber-processing bacteria", "protective bacteria", "the beneficial bacteria that were affected", "the compounds your gut makes to protect its lining".
- Be personal and specific — use {first_name}'s actual antibiotic names, stress level number, specific lifestyle factors.
- Ground every claim in the evidence provided. Do not invent connections not supported by the evidence.
- Keep it short and clear. 2-4 short paragraphs per deviation. No bullet points.
- End with a brief summary paragraph for all deviations together.
- Do NOT mention research papers directly in the text — the citations go to a separate References section.

CLIENT PROFILE:
{context_summary}

MICROBIOME DEVIATIONS FOUND:
{deviations_text}

EVIDENCE MAP — scientifically supported links between lifestyle factors and this microbiome pattern:
{evidence_text}{elicit_section}

Return ONLY valid JSON, no other text:

{{
  "deviations": [
    {{
      "deviation_key": "<exact key from the deviation list above, e.g. Fiber Degraders__below>",
      "narrative": "<free-flowing explanation of why {first_name} has this deviation. 2-4 short paragraphs. Cover: what this bacterial group does and why having fewer of them matters for {first_name}'s day-to-day health. DO NOT explain individual factors here — that goes in factor_explanations below. Friendly, warm, no jargon.>",
      "summary_line": "<1 sentence: the single most important thing {first_name} should take away from this deviation. Upbeat and forward-looking.>",
      "factor_explanations": [
        {{
          "domain_key": "<domain key exactly as listed in EVIDENCE MAP, e.g. antibiotic_use>",
          "explanation": "<2-4 sentences personalised to {first_name}. Explain specifically how THIS factor from their history contributed to THIS bacterial deviation. Use their actual details — antibiotic names, stress score, smoking frequency. Ground in the evidence provided. E.g. 'The biggest factor here is almost certainly your recent antibiotic courses. Taking both Furadantine and Croxilex in September was necessary, but antibiotics are like a reset button for your gut...' Warm, specific, no jargon.>"
        }}
      ]
    }}
  ],
  "section_summary": "<2-3 sentences summarising the overall picture across all deviations. What is the likely underlying story? What does this mean for recovery? Warm and personal.>",
  "cited_paper_keys": ["<citation string>", ...]
}}

For factor_explanations: include one entry per domain_key listed in the EVIDENCE MAP. Use the exact domain_key string.
For cited_paper_keys: list only the citation strings (e.g. 'Maier et al., 2021') from the scientific literature that you actually used."""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 2000,
                'temperature': 0.5,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        raw_text = result_body['content'][0]['text'].strip()

        # Strip markdown code fences if present
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]

        parsed = json.loads(raw_text)
        # Return full parsed result — caller extracts deviations + section_summary + cited_paper_keys
        return parsed

    except Exception as e:
        logger.warning(f"Section 3 LLM call failed: {e}")
        return {}



def _lifestyle_fallback() -> list:
    """Sensible default recommendations when LLM is unavailable."""
    return [
        {'emoji': '🥬', 'title': 'Increase fiber variety',
         'text': 'Aim for 30g of fiber daily from diverse sources to nourish your beneficial gut bacteria.'},
        {'emoji': '🏃', 'title': 'Stay physically active',
         'text': 'Regular moderate exercise (30 minutes, 5 days/week) has been shown to increase beneficial gut bacteria diversity.'},
        {'emoji': '😴', 'title': 'Prioritize sleep quality',
         'text': 'Maintain a consistent sleep schedule and aim for 7-9 hours — disrupted sleep patterns are linked to reduced gut microbial diversity.'},
    ]


# ── Lifestyle-domain Elicit queries ──────────────────────────────────────────
_LIFESTYLE_ELICIT_QUERIES = {
    'fiber_diversity': 'dietary fiber diversity gut microbiota composition humans',
    'exercise_microbiome': 'physical exercise gut microbiome butyrate diversity humans',
    'sleep_gut': 'sleep quality duration gut microbiome bacteria circadian',
    'stress_gut': 'chronic psychological stress gut bacteria short-chain fatty acids',
    'hydration_gut': 'water intake hydration gut mucus layer microbiome',
    'fermented_foods': 'fermented food consumption gut microbiota diversity humans',
    'alcohol_gut': 'alcohol consumption gut barrier permeability microbiome',
    'processed_food': 'ultra-processed food gut microbiome diversity reduction',
}


def _generate_lifestyle_recommendations(
    deviations: list,
    questionnaire_context: dict,
    analysis: dict,
    elicit_key: str = '',
    model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
    region: str = 'eu-west-1',
) -> tuple:
    """
    Generate personalized, evidence-based lifestyle & diet recommendations.

    Uses Elicit to retrieve scientific papers for lifestyle topics relevant to
    the client's profile, then passes those papers + client context to Claude
    for nuanced, personalised recommendations.

    Returns tuple: (recommendations_list, cited_papers_list)
      recommendations_list: [{emoji, title, text}, ...]
      cited_papers_list: [paper_dict, ...] — papers cited, to be added to References section
    """
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not available — using fallback lifestyle recommendations")
        return _lifestyle_fallback(), []

    # ── Step 1: Determine which lifestyle domains are relevant ────────────
    ctx = questionnaire_context
    relevant_domains = []

    # Always include fiber (core to microbiome)
    relevant_domains.append('fiber_diversity')

    # Conditionally add domains based on questionnaire
    stress = ctx.get('stress_level')
    if stress and (isinstance(stress, (int, float)) and stress >= 5 or str(stress).isdigit() and int(stress) >= 5):
        relevant_domains.append('stress_gut')

    sleep = ctx.get('sleep_quality')
    if sleep and (isinstance(sleep, (int, float)) and sleep <= 6 or str(sleep).isdigit() and int(sleep) <= 6):
        relevant_domains.append('sleep_gut')

    activity = ctx.get('activity_level', '').lower()
    vigorous = ctx.get('vigorous_days_per_week', 0)
    if activity in ('sedentary', 'low', 'lightly_active') or (isinstance(vigorous, (int, float)) and vigorous <= 1):
        relevant_domains.append('exercise_microbiome')

    alcohol = ctx.get('alcohol_frequency', '').lower()
    if alcohol and alcohol not in ('never', 'rarely', 'none', ''):
        relevant_domains.append('alcohol_gut')

    processed = ctx.get('processed_foods_frequency', '').lower()
    if processed and processed not in ('never', 'rarely', 'none', ''):
        relevant_domains.append('processed_food')

    fermented = ctx.get('fermented_foods_frequency', '').lower()
    if not fermented or fermented in ('never', 'rarely', 'none', ''):
        relevant_domains.append('fermented_foods')

    # Always include hydration as a safe universal recommendation
    relevant_domains.append('hydration_gut')

    # Deduplicate and cap at 6
    relevant_domains = list(dict.fromkeys(relevant_domains))[:6]

    # ── Step 2: Fetch Elicit papers for relevant domains ──────────────────
    elicit_papers_flat = []
    if elicit_key and relevant_domains:
        queries = [_LIFESTYLE_ELICIT_QUERIES[d] for d in relevant_domains if d in _LIFESTYLE_ELICIT_QUERIES]
        if queries:
            logger.info(f"  Lifestyle: fetching Elicit papers for {len(queries)} domains...")
            elicit_papers_flat = _fetch_elicit_for_queries(queries, elicit_key)
            logger.info(f"  Lifestyle: {len(elicit_papers_flat)} unique papers retrieved")

    # ── Step 3: Build LLM prompt with papers + client context ─────────────
    dev_text = '; '.join(
        f"{d['client_label']}: {d['value_str']} (range: {d['range_str']})"
        for d in (deviations if isinstance(deviations, list) else [])
        if isinstance(d, dict) and 'client_label' in d
    ) if deviations else 'No significant deviations detected.'

    # Handle deviations that are wrapped in cards
    if deviations and isinstance(deviations[0], dict) and 'deviation' in deviations[0]:
        dev_text = '; '.join(
            f"{d['deviation']['client_label']}: {d['deviation']['value_str']} (range: {d['deviation']['range_str']})"
            for d in deviations if isinstance(d, dict) and 'deviation' in d
        )

    first_name = ctx.get('first_name', 'the client')
    diet = ctx.get('diet_pattern', 'unknown')
    goals = ', '.join(ctx.get('goals', [])) or 'general wellbeing'
    conditions = ', '.join(ctx.get('diagnoses', [])) or 'none reported'

    exec_sum = analysis.get('executive_summary', {})
    summary_text = exec_sum.get('opening_paragraph', '')

    # Format paper abstracts for the prompt
    paper_context = ''
    if elicit_papers_flat:
        paper_lines = []
        for p in elicit_papers_flat[:10]:
            cit = p.get('citation', '')
            abstract = p.get('abstract', '')
            if abstract:
                paper_lines.append(f"[{cit}]: {abstract[:400]}")
        if paper_lines:
            paper_context = '\n\nSCIENTIFIC LITERATURE — peer-reviewed papers on lifestyle interventions and gut microbiome:\n' + '\n'.join(paper_lines)

    prompt = f"""You are a clinical nutritionist creating personalized lifestyle and diet recommendations for a microbiome health report.

CLIENT PROFILE:
- Name: {first_name}
- Diet: {diet}
- Stress level: {ctx.get('stress_level', '?')}/10
- Sleep quality: {ctx.get('sleep_quality', '?')}/10
- Physical activity: {ctx.get('activity_level', '?')}
- Health goals: {goals}
- Reported conditions: {conditions}

MICROBIOME STATUS:
{dev_text}

ANALYSIS SUMMARY:
{summary_text}{paper_context}

Generate exactly 5 lifestyle and diet recommendations. Each must be:
1. Personalized to this client's specific microbiome pattern and questionnaire data
2. Practical and immediately actionable
3. Short (1-2 sentences max for the recommendation text)
4. Evidence-based — grounded in the scientific literature provided above
5. Connected to their stated health goals where possible
6. Do NOT name specific foods (no "lentils", "oats", "kimchi" etc.) — keep advice at the category/behavior level
7. Be nuanced — reference their actual scores, stress level, sleep rating, specific deviations where relevant

For each recommendation, also specify which paper from the literature above best supports it (use the citation key, e.g. "Wastyk et al., 2021"). If no paper matches, write "general_evidence".

Return valid JSON — an array of objects with keys: emoji, title, text, cited_paper_key.

Example format:
[
  {{"emoji": "🥬", "title": "Diversify your plant fiber intake", "text": "Aim for at least 3 different categories of plant-based fiber daily — variety feeds different bacterial communities and supports the fiber-processing guild your test flagged as below range.", "cited_paper_key": "Wastyk et al., 2021"}},
  ...
]

Return ONLY the JSON array, no markdown fences or explanation."""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 1200,
                'temperature': 0.5,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        raw_text = result_body['content'][0]['text'].strip()

        # Strip markdown code fences if present
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]

        parsed = json.loads(raw_text)
        if isinstance(parsed, list) and len(parsed) >= 3:
            recs = parsed[:6]

            # Collect cited papers — match cited_paper_key back to Elicit paper objects
            cited_keys = [r.get('cited_paper_key', '') for r in recs if r.get('cited_paper_key') and r.get('cited_paper_key') != 'general_evidence']
            lifestyle_cited = [
                p for p in elicit_papers_flat
                if p.get('citation') in cited_keys
            ]

            # Strip cited_paper_key from output (not rendered in the panel)
            clean_recs = [{'emoji': r['emoji'], 'title': r['title'], 'text': r['text']} for r in recs]

            return clean_recs, lifestyle_cited

        return _lifestyle_fallback(), []

    except Exception as e:
        logger.warning(f"Lifestyle recommendations LLM call failed: {e}")
        return _lifestyle_fallback(), []


def _call_factor_first_llm(
    relevant_triggered: list,
    active_deviations: list,
    questionnaire_context: dict,
    factor_guild_impacts: dict,
    evidence_kb: dict,
    elicit_papers_flat: list,
    model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
    region: str = 'eu-west-1',
) -> dict:
    """
    Generate one focused explanation per contributing factor using Elicit papers.

    Rules:
    - One paragraph (3-5 sentences) per factor — NOT per guild
    - Reference client's relevant score where applicable (stress 7/10, sleep 6/10, etc.)
    - Explain the causal mechanism from the FACTOR's own logic, not from each guild separately
    - Factor-specific framing:
        bloating/IBS: frame as the root of the fermentation cascade affecting ALL guilds
        stress/anxiety: explain gut-brain bidirectionality
        sleep: explain the SCFA → sleep architecture mechanism
        low activity: frame as the ONE independent lever that doesn't require breaking other loops
        antibiotics: one-time disruption with lasting ecology
        alcohol/smoking/diet: exposure → ecosystem shift
    - Nuanced but plain language — no jargon, no Latin names, no technical terms
    - Grounded in Elicit papers where available
    """
    try:
        import boto3
    except ImportError:
        return {}

    ctx = questionnaire_context
    first_name = ctx.get('first_name', 'the client')

    # Build factor blocks — one per relevant triggered domain
    factor_blocks = []
    for domain_key, conf in relevant_triggered[:6]:
        domain_data = evidence_kb.get('domains', {}).get(domain_key, {})
        label = domain_data.get('domain_label', domain_key)
        icon = domain_data.get('icon', '🔍')
        evidence_strength = max(
            (s.get('evidence_strength', 'weak') for s in domain_data.get('signals', [])),
            key=lambda x: {'strong': 4, 'moderate': 3, 'weak_to_moderate': 2, 'weak': 1}.get(x, 1),
            default='weak'
        )

        # Guilds this factor affects
        guild_impacts = factor_guild_impacts.get(domain_key, [])
        guild_summary = '; '.join(
            f"{g['client_label']} ({g['value_str']}, {g['impact']})"
            for g in guild_impacts
        ) if guild_impacts else 'various bacterial imbalances'

        # Best KB text for grounding
        best_kb = ''
        for sig in domain_data.get('signals', [])[:2]:
            t = sig.get('section3_text', {}).get('non_expert', '') or sig.get('mechanism', {}).get('non_expert', '')
            if t and len(t) > len(best_kb):
                best_kb = t

        # Relevant Elicit paper abstracts for this domain
        paper_lines = []
        for p in elicit_papers_flat[:12]:
            abstract = p.get('abstract', '')
            citation = p.get('citation', '')
            if abstract and len(paper_lines) < 3:
                paper_lines.append(f"[{citation}]: {abstract[:400]}")

        block = (
            f"FACTOR: {icon} {label}\n"
            f"Evidence level: {evidence_strength}\n"
            f"Guilds affected: {guild_summary}\n"
            f"KB mechanism: {best_kb[:300]}"
        )
        if paper_lines:
            block += '\nElicit abstracts:\n' + '\n'.join(paper_lines[:2])
        factor_blocks.append({'domain_key': domain_key, 'block': block, 'label': label, 'icon': icon})

    if not factor_blocks:
        return {}

    # Client context
    context_lines = [
        f"Client: {first_name}, {ctx.get('age', '?')}y, stress {ctx.get('stress_level', '?')}/10, sleep {ctx.get('sleep_quality', '?')}/10",
        f"Activity: {ctx.get('activity_level', '?')}, vigorous {ctx.get('vigorous_days_per_week', '?')} days/week",
        f"Diet: {ctx.get('diet_pattern', '?')}, fiber intake: {ctx.get('fiber_intake', '?')}",
        f"Antibiotics: {', '.join(ctx.get('antibiotics', [])) or 'none'}",
        f"Alcohol: {ctx.get('alcohol_frequency', '?')}, tobacco: {ctx.get('tobacco_use', '?')}",
    ]
    context_str = '\n'.join(context_lines)

    factors_text = '\n\n'.join(b['block'] for b in factor_blocks)

    # Factor-specific framing rules injected into prompt
    framing_rules = """FRAMING RULES — apply these to the specific factor type:
- Bloating / IBS / digestive discomfort: Frame as the ROOT of the fermentation cascade. The bloating is both a symptom AND a perpetuating cause. Explain how fermentation disruption suppresses fiber-processing bacteria and creates a substrate vacuum that mucin-degraders fill. All 4 imbalances trace back to this.
- Chronic stress / anxiety: Explain the gut-brain BIDIRECTIONALITY — stress hormones alter gut pH and motility disadvantaging beneficial bacteria, AND depleted beneficial bacteria reduce the compounds that support brain chemistry. Mention the client's actual stress score.
- Poor sleep: Explain that short-chain fatty acids (the compounds your beneficial bacteria make) directly cross into the brain and regulate sleep architecture — when butyrate producers are low, this signal drops. Poor sleep then feeds back to suppress the same bacteria overnight. Mention the client's actual sleep score.
- Low physical activity: Frame this as the ONE INDEPENDENT LEVER that doesn't require breaking the bloating-stress-sleep loop first. Exercise directly increases butyrate producers and diversity via intestinal transit and bile acid changes, working in parallel with other interventions.
- Antibiotics: Frame as a one-time ecological disruption with long-lasting effects — like a reset that removed the keystone species, allowing opportunistic bacteria to fill the space.
- Alcohol / smoking / processed foods: Frame as chronic exposure shifting the bacterial ecosystem toward less beneficial communities."""

    prompt = f"""You are writing Section 3 of a personalised gut health report for {first_name}.

The section is called "What is behind this imbalance?" and is now organised by CONTRIBUTING FACTOR rather than by individual bacterial guild.

Your task: Write ONE explanation paragraph (3-5 sentences) for EACH contributing factor listed below.

Each paragraph must:
1. Reference {first_name}'s relevant score where applicable (e.g. "your stress level of 7/10", "your sleep score of 6/10")
2. Explain the causal mechanism linking this factor to the SPECIFIC guilds it affects — from the FACTOR'S OWN LOGIC, not guild by guild
3. Be warm, plain English — no jargon, no Latin, no "microbiota", "dysbiosis", "SCFA" (say "protective compounds your gut makes"), "Lachnospiraceae", etc.
4. Be grounded in the Elicit research provided — do not invent connections
5. Be nuanced: the explanation should feel specific to {first_name}, not generic

{framing_rules}

CLIENT CONTEXT:
{context_str}

CONTRIBUTING FACTORS AND THEIR EVIDENCE:
{factors_text}

Return ONLY valid JSON, no other text:
{{
  "factor_explanations": [
    {{
      "domain_key": "<exact domain_key>",
      "explanation": "<3-5 sentence paragraph for this factor. Plain English. References client's actual scores. Explains causal mechanism covering all guilds this factor affects. Grounded in research. Warm but specific.>"
    }}
  ],
  "section_summary": "<2-3 sentences: the overall causal story across all factors for {first_name}. What converged to create this pattern? What does this mean for recovery? Warm, forward-looking.>",
  "cited_paper_keys": ["<citation string used>", ...]
}}"""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 2500,
                'temperature': 0.5,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        raw_text = result_body['content'][0]['text'].strip()
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]
        return json.loads(raw_text)
    except Exception as e:
        logger.warning(f"Factor-first LLM call failed: {e}")
        return {}


def build_root_cause_section(questionnaire: dict, analysis: dict,
                              no_llm: bool = False,
                              model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
                              region: str = 'eu-west-1',
                              elicit_key: str = '') -> dict:
    """
    Build Section 3: The Story Behind Your Results.

    Microbiome-first approach:
    1. Detect active deviations in this sample
    2. Find triggered lifestyle/health domains from questionnaire
    3. Match domains to deviations using the evidence map
    4. Call LLM to explain deviations in plain, personal language
    5. Fallback to deterministic text if LLM unavailable

    Returns:
      {
        'deviation_cards': [  # one card per active deviation with explanations
          {
            'deviation': {...},
            'health_meaning': str,
            'drivers': [{icon, label, text, evidence_label}],
            'personal_synthesis': str,
          }
        ],
        'awareness_chips': [  # triggered factors with no matching deviation
          {domain_label, icon, summary_text}
        ]
      }
    """
    import os as _os

    kb_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'knowledge_base')
    evidence_path = _os.path.join(kb_dir, 'questionnaire_microbiome_evidence.json')
    rules_path = _os.path.join(kb_dir, 'root_cause_domain_rules.json')

    try:
        with open(evidence_path) as f:
            evidence_kb = json.load(f)
        with open(rules_path) as f:
            rules_kb = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load root cause KB files: {e}")
        return {'deviation_cards': [], 'awareness_chips': []}

    domains_evidence = evidence_kb.get('domains', {})
    strength_scores = {'strong': 4, 'moderate': 3, 'weak_to_moderate': 2, 'weak': 1}

    # ── Step 1: Detect active deviations ──────────────────────────────────
    active_deviations = _detect_microbiome_deviations(analysis)

    # ── Step 2: Evaluate questionnaire triggers for all domains ───────────
    triggered_domains = []    # [(domain_key, confidence)]
    triggering_domain_keys = set()

    for domain_key, domain_data in domains_evidence.items():
        rules = rules_kb.get(domain_key)
        if not rules:
            continue
        conditions = rules.get('trigger_conditions', [])
        triggered = False
        best_conf = None
        for cond in conditions:
            if _evaluate_condition(questionnaire, cond):
                triggered = True
                contrib = cond.get('confidence_contribution', 'possible')
                priority = {'confirmed': 3, 'likely': 2, 'possible': 1}
                if best_conf is None or priority.get(contrib, 0) > priority.get(best_conf, 0):
                    best_conf = contrib
                break  # or-logic: first match wins
        if triggered:
            triggered_domains.append((domain_key, best_conf or rules.get('default_confidence', 'possible')))
            triggering_domain_keys.add(domain_key)

    # ── Step 3: For each deviation, find which triggered domains explain it ──
    # domain_key → set of deviation keys it explains
    domain_explains = {}
    for domain_key, confidence in triggered_domains:
        domain_data = domains_evidence.get(domain_key, {})
        signals = domain_data.get('signals', [])
        for dev in active_deviations:
            if any(_signal_explains_deviation(s, dev) for s in signals):
                if domain_key not in domain_explains:
                    domain_explains[domain_key] = set()
                domain_explains[domain_key].add(dev['key'])

    # Domains that explain at least one deviation
    explaining_domain_keys = set(domain_explains.keys())

    # Triggered domains that explain deviations — sorted by strength
    relevant_triggered = [
        (dk, conf) for dk, conf in triggered_domains
        if dk in explaining_domain_keys
    ]
    relevant_triggered.sort(
        key=lambda x: -(max(
            strength_scores.get(s.get('evidence_strength', 'weak'), 1)
            for s in domains_evidence.get(x[0], {}).get('signals', [{'evidence_strength': 'weak'}])
        ))
    )

    # ── Step 4a: Build questionnaire factors list for Elicit query generation
    questionnaire_context = _extract_questionnaire_context(questionnaire)

    # Extract human-readable factor labels from triggered explaining domains
    questionnaire_factors = []
    for dk, _ in relevant_triggered[:6]:
        domain_data = domains_evidence.get(dk, {})
        label = domain_data.get('domain_label', dk)
        questionnaire_factors.append(label)
    # Add personal details to make queries more targeted
    ctx = questionnaire_context
    if ctx.get('antibiotics'):
        for ab in ctx['antibiotics']:
            questionnaire_factors = [f"{ab} (antibiotic)" if 'antibiotic' in questionnaire_factors else ab] + [f for f in questionnaire_factors if 'antibiotic' not in f.lower()]
            break
    questionnaire_factors = list(dict.fromkeys(questionnaire_factors))[:6]  # deduplicate, max 6

    # ── Step 4b: Get Elicit search queries from Claude (targeted per deviation+factors) ──
    elicit_papers_flat = []  # flat list of all retrieved papers (deduplicated)
    elicit_queries = []

    if elicit_key and not no_llm and active_deviations and questionnaire_factors:
        logger.info(f"  Generating targeted Elicit queries via Claude...")
        elicit_queries = _generate_elicit_queries_llm(
            deviations=active_deviations,
            questionnaire_factors=questionnaire_factors,
            model_id=model_id,
            region=region,
        )
        if elicit_queries:
            logger.info(f"  Generated {len(elicit_queries)} Elicit queries, fetching papers...")
            elicit_papers_flat = _fetch_elicit_for_queries(elicit_queries, elicit_key)
            logger.info(f"  Elicit: {len(elicit_papers_flat)} unique papers retrieved")
        else:
            logger.info("  Query generation returned empty — skipping Elicit")

    # ── Step 4c: Generate personalised narrative explanation ──────────────
    llm_results = []
    if not no_llm and active_deviations and relevant_triggered:
        relevant_domain_keys = [dk for dk, _ in relevant_triggered[:6]]
        llm_results = _call_section3_llm(
            deviations=active_deviations,
            questionnaire_context=questionnaire_context,
            triggered_domains=relevant_domain_keys,
            evidence_kb=evidence_kb,
            elicit_papers={'_flat': elicit_papers_flat},
            model_id=model_id,
            region=region,
        )

    # ── Step 5: Merge LLM results back into deviation cards ──────────────
    # llm_results is a dict: {deviations: [...], section_summary: str, cited_paper_keys: [...]}
    # If LLM failed or was skipped, results are empty — cards will render with KB factors only.
    llm_deviation_items = []
    section_summary = ''
    cited_paper_keys = []

    if isinstance(llm_results, dict):
        llm_deviation_items = llm_results.get('deviations', [])
        section_summary = llm_results.get('section_summary', '')
        cited_paper_keys = llm_results.get('cited_paper_keys', [])

    llm_by_key = {}
    for item in llm_deviation_items:
        raw_key = item.get('deviation_key', '')
        for dev in active_deviations:
            if dev['key'] == raw_key or dev['client_label'] in raw_key or raw_key in dev['client_label']:
                llm_by_key[dev['key']] = item
                break

    deviation_cards = []
    for dev in active_deviations:
        llm = llm_by_key.get(dev['key'], {})
        deviation_cards.append({
            'deviation': dev,
            # LLM narrative path (new schema): narrative + summary_line
            'narrative': llm.get('narrative', ''),
            'summary_line': llm.get('summary_line', ''),
            # Deterministic fallback path: health_meaning + drivers + personal_synthesis
            'health_meaning': llm.get('health_meaning', dev.get('description', '')),
            'drivers': llm.get('drivers', []),
            'personal_synthesis': llm.get('personal_synthesis', ''),
            # KB-driven factor cards: always built regardless of LLM path
            'kb_drivers': [],
        })

    # ── Always build KB drivers for factor cards (runs alongside LLM) ────────
    # The LLM returns factor_explanations per deviation — personalised text per domain_key.
    # These replace the static KB mechanism text in the accordion body.
    _ev_labels = {
        'strong': 'Well established',
        'moderate': 'Research supported',
        'weak_to_moderate': 'Emerging research',
        'weak': 'Emerging research',
    }
    _ev_strength_scores = {'strong': 4, 'moderate': 3, 'weak_to_moderate': 2, 'weak': 1}

    # Build LLM factor_explanations lookup: {deviation_key: {domain_key: explanation_text}}
    llm_factor_expl = {}
    for item in llm_deviation_items:
        raw_key = item.get('deviation_key', '')
        factor_expl_list = item.get('factor_explanations', [])
        # Find the matching deviation key
        for dev in active_deviations:
            if dev['key'] == raw_key or dev['client_label'] in raw_key or raw_key in dev['client_label']:
                llm_factor_expl[dev['key']] = {
                    fe.get('domain_key', ''): fe.get('explanation', '')
                    for fe in factor_expl_list
                    if fe.get('domain_key') and fe.get('explanation')
                }
                break

    for card in deviation_cards:
        dev = card['deviation']
        dev_key = dev['key']
        factor_expl_for_dev = llm_factor_expl.get(dev_key, {})
        kb_drivers = []
        for domain_key, q_confidence in relevant_triggered:
            domain_data = domains_evidence.get(domain_key, {})
            signals = domain_data.get('signals', [])
            matching = [s for s in signals if _signal_explains_deviation(s, dev)]
            if not matching:
                continue
            best = max(matching, key=lambda s: _ev_strength_scores.get(s.get('evidence_strength', 'weak'), 1))
            # Use LLM personalised explanation if available, fall back to KB static text
            llm_text = factor_expl_for_dev.get(domain_key, '')
            kb_text = best.get('section3_text', {}).get('non_expert', '') or \
                      best.get('mechanism', {}).get('non_expert', '')
            text = llm_text or kb_text
            if not text:
                continue
            kb_drivers.append({
                'icon': domain_data.get('icon', '🔍'),
                'label': domain_data.get('domain_label', domain_key),
                'text': text,           # LLM-personalised explanation (shown in card body)
                'kb_text': kb_text,     # KB static science text (shown in "What does science say")
                'is_llm_text': bool(llm_text),
                'directionality': domain_data.get('directionality', 'associative'),
                'directionality_arrow': domain_data.get('directionality_arrow', ''),
                'evidence_label': _ev_labels.get(best.get('evidence_strength', 'weak'), 'Emerging research'),
                'evidence_strength': best.get('evidence_strength', 'weak'),
                'domain_key': domain_key,
            })
        card['kb_drivers'] = kb_drivers

    # ── Collect cited papers: match cited_paper_keys back to full Elicit paper objects ──
    cited_papers = [
        p for p in elicit_papers_flat
        if p.get('citation') in cited_paper_keys
    ]

    # Sort: absent first, then below range, then above range
    def _dev_sort(card):
        v = card['deviation'].get('value_str', '')
        t = card['deviation'].get('type', '')
        if v == 'Absent': return 0
        if t == 'below_range': return 1
        if t == 'above_range': return 2
        return 3
    deviation_cards.sort(key=_dev_sort)

    # ── Step 7: Awareness chips — triggered but no matching deviation ──────
    awareness_chips = []
    for domain_key, confidence in triggered_domains:
        if domain_key in explaining_domain_keys:
            continue
        domain_data = domains_evidence.get(domain_key, {})
        awareness_chips.append({
            'domain_key': domain_key,
            'domain_label': domain_data.get('domain_label', domain_key),
            'icon': domain_data.get('icon', '🔍'),
            'directionality_arrow': domain_data.get('directionality_arrow', ''),
            'summary_text': domain_data.get('root_cause_summary', {}).get('non_expert', ''),
        })

    # ── Step 8: Factor-first data (new Section 3 layout) ─────────────────
    # Per-guild severity — replaces flat _impact_map.
    # Logic mirrors the severity already used in compute_strengths_challenges():
    #   - Mucin Degraders elevated (above range) → crit  (barrier erosion — worst)
    #   - Proteolytic elevated (above range)     → high  (inflammatory pressure)
    #   - Beneficial guild absent (0%)           → crit  (complete functional gap)
    #   - Beneficial guild below range           → low   (reduced, recoverable)
    #   - Low diversity                          → crit  (ecosystem fragility)
    #   - Metabolic dial deviation (sluggish / heavy_mucus / protein_pressure)
    #       heavy_mucus  → crit  (barrier under active pressure)
    #       protein_pressure → high
    #       sluggish_fermentation → high
    def _deviation_severity(dev: dict) -> str:
        guild = dev.get('guild_key', '') or ''
        dtype = dev.get('type', '')
        val_str = dev.get('value_str', '')
        dev_key = dev.get('key', '')

        if dtype == 'low_diversity':
            return 'crit'

        if dtype == 'above_range':
            g = guild.lower()
            if 'mucin' in g or 'mucus' in dev_key.lower():
                return 'crit'   # Mucin Degraders elevated → worst
            if 'heavy_mucus' in dev_key:
                return 'crit'
            if 'protein_pressure' in dev_key:
                return 'high'
            if 'proteolytic' in g:
                return 'high'
            return 'high'       # any other above-range → amber

        if dtype == 'below_range':
            if val_str == 'Absent' or val_str == '0.0%' or val_str == '0%':
                return 'crit'   # complete absence → dark red
            if 'sluggish' in dev_key:
                return 'high'
            return 'low'        # below range but present → red

        return 'low'

    factor_guild_impacts = {}
    for domain_key, _ in relevant_triggered:
        impacts = []
        for dev in active_deviations:
            dev_key_full = dev.get('key', '')
            if dev_key_full in domain_explains.get(domain_key, set()):
                impacts.append({
                    'client_label': dev.get('client_label', ''),
                    'value_str': dev.get('value_str', ''),
                    'impact': _deviation_severity(dev),
                })
        factor_guild_impacts[domain_key] = impacts

    # Sort relevant_triggered by guilds_affected_count DESC (breadth ordering)
    relevant_triggered_sorted = sorted(
        relevant_triggered,
        key=lambda x: -len(factor_guild_impacts.get(x[0], [])),
    )

    # Call factor-first LLM (Elicit-enriched, one paragraph per factor)
    factor_llm_result = {}
    if not no_llm and relevant_triggered_sorted and elicit_papers_flat:
        logger.info(f"  Calling factor-first LLM ({len(relevant_triggered_sorted)} factors)...")
        factor_llm_result = _call_factor_first_llm(
            relevant_triggered=relevant_triggered_sorted,
            active_deviations=active_deviations,
            questionnaire_context=questionnaire_context,
            factor_guild_impacts=factor_guild_impacts,
            evidence_kb=evidence_kb,
            elicit_papers_flat=elicit_papers_flat,
            model_id=model_id,
            region=region,
        )
        if factor_llm_result:
            logger.info(f"  Factor-first LLM: {len(factor_llm_result.get('factor_explanations', []))} factor explanations")

    # Update section_summary from factor-first if richer
    if factor_llm_result.get('section_summary'):
        section_summary = factor_llm_result['section_summary']

    # Merge factor_llm_result into cited_paper_keys
    for ck in factor_llm_result.get('cited_paper_keys', []):
        if ck not in cited_paper_keys:
            cited_paper_keys.append(ck)

    # Build factor_cards (one per relevant factor, sorted breadth-first)
    _ev_str_map = {'strong': 'Well established', 'moderate': 'Research supported',
                   'weak_to_moderate': 'Emerging research', 'weak': 'Emerging research'}
    factor_expl_lookup = {
        fe['domain_key']: fe['explanation']
        for fe in factor_llm_result.get('factor_explanations', [])
        if fe.get('domain_key') and fe.get('explanation')
    }
    factor_cards = []
    for domain_key, conf in relevant_triggered_sorted:
        domain_data = domains_evidence.get(domain_key, {})
        best_strength = max(
            (_ev_strength_scores.get(s.get('evidence_strength', 'weak'), 1)
             for s in domain_data.get('signals', [])),
            default=1
        )
        ev_key = {4: 'strong', 3: 'moderate', 2: 'weak_to_moderate', 1: 'weak'}.get(best_strength, 'weak')
        guild_impacts = factor_guild_impacts.get(domain_key, [])
        explanation = factor_expl_lookup.get(domain_key, '')
        if not explanation:
            # Fall back to KB text
            for sig in domain_data.get('signals', [])[:1]:
                explanation = sig.get('section3_text', {}).get('non_expert', '') or sig.get('mechanism', {}).get('non_expert', '')
        factor_cards.append({
            'domain_key': domain_key,
            'icon': domain_data.get('icon', '🔍'),
            'label': domain_data.get('domain_label', domain_key),
            'subtitle': f"Affects {len(guild_impacts)} bacterial imbalance{'s' if len(guild_impacts) != 1 else ''}",
            'evidence_label': _ev_str_map.get(ev_key, 'Emerging research'),
            'evidence_strength': ev_key,
            'guilds_affected_count': len(guild_impacts),
            'guild_impacts': guild_impacts,
            'explanation': explanation,
            'directionality': domain_data.get('directionality', 'driver'),
        })

    # Build cascade_guilds (guild → driving factors emoji shorthand)
    # Invert factor_guild_impacts: guild_client_label → [factor emojis]
    guild_factor_map: dict = {}
    for domain_key, impacts in factor_guild_impacts.items():
        domain_data = domains_evidence.get(domain_key, {})
        icon = domain_data.get('icon', '🔍')
        for g in impacts:
            lbl = g['client_label']
            if lbl not in guild_factor_map:
                guild_factor_map[lbl] = {'value_str': g['value_str'], 'impact': g['impact'], 'icons': []}
            guild_factor_map[lbl]['icons'].append(icon)

    cascade_guilds = [
        {
            'client_label': lbl,
            'value_str': info['value_str'],
            'impact': info['impact'],
            'driving_factor_emojis': info['icons'],
        }
        for lbl, info in guild_factor_map.items()
    ]

    # Build metrics_strip — top N active deviations for the metrics strip at top of section
    # Sort by severity so the worst always appear first
    _sev_order = {'crit': 0, 'high': 1, 'low': 2}
    sorted_devs = sorted(active_deviations, key=lambda d: _sev_order.get(_deviation_severity(d), 2))
    metrics_strip = []
    for dev in sorted_devs[:4]:
        metrics_strip.append({
            'client_label': dev.get('client_label', ''),
            'value_str': dev.get('value_str', ''),
            'range_str': dev.get('range_str', ''),
            'impact': _deviation_severity(dev),
            'icon': dev.get('icon', '🔬'),
        })

    # Update cited_papers with factor-first keys
    all_paper_keys = set(cited_paper_keys)
    cited_papers = [p for p in elicit_papers_flat if p.get('citation') in all_paper_keys]

    return {
        'deviation_cards': deviation_cards,
        'awareness_chips': awareness_chips[:6],
        'section_summary': section_summary,
        'cited_papers': cited_papers,
        # Factor-first data (new Section 3 layout)
        'factor_cards': factor_cards,
        'cascade_guilds': cascade_guilds,
        'metrics_strip': metrics_strip,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  TIMELINE PHASES
# ════════════════════════════════════════════════════════════════════════════════

def build_timeline_phases(analysis: dict, formulation: dict) -> list:
    """Build 4-phase 16-week timeline descriptions, personalised per formulation."""

    # Extract key formula info for personalized sentences
    mix_name = ''
    primary_guild = ''
    has_ashwagandha = False
    has_magnesium = False
    has_omega = False
    has_vit_d = False
    has_prebiotic = False
    has_lp815 = False
    prebiotic_fodmap_g = 0.0

    if formulation:
        mix = formulation.get('decisions', {}).get('mix_selection', {})
        mix_name = mix.get('mix_name', '')

        # Primary intervention guild
        interventions = formulation.get('priority_interventions', [])
        for iv in interventions:
            if iv.get('priority_level') in ('1A', '1B', 'CRITICAL'):
                primary_guild = iv.get('guild_name', '')
                break

        # Component flags
        registry = formulation.get('component_registry', [])
        for comp in registry:
            sub = comp.get('substance', '').lower()
            if 'ashwagandha' in sub:
                has_ashwagandha = True
            if 'magnesium' in sub:
                has_magnesium = True
            if 'omega' in sub:
                has_omega = True
            if 'vitamin d' in sub:
                has_vit_d = True
            if 'lp815' in sub or 'lp 815' in sub:
                has_lp815 = True
            if comp.get('category') == 'prebiotic':
                has_prebiotic = True

        # FODMAP load from prebiotic design
        prebiotic_fodmap_g = formulation.get('decisions', {}).get('prebiotic_design', {}).get('total_fodmap_grams', 0)

    # --- Build phases ---
    phases = [
        {
            'weeks': '1–4',
            'label': 'Settling in',
            'color': '#3A6EA8',
            'dot_class': 'wk4',
            'title': '🌱 Settling in',
            'body': (
                "The probiotic strains in your capsule are adapting to your gut environment — "
                "encountering existing bacteria, beginning to colonise, and establishing early positions. "
                "Your daily prebiotic sachet is reaching the lower gut, priming the environment before "
                "measurable compositional shifts occur. "
                + ("Vitamin C, Zinc, and B-vitamins from your sachet begin supporting immune and energy pathways within days. " if has_prebiotic else "")
                + (f"The LP815 strain begins producing calming GABA from week 1, supporting stress-axis balance. " if has_lp815 else "")
                + "You may notice small changes in digestion rhythm — this is normal as your gut adjusts."
            ),
        },
        {
            'weeks': '5–8',
            'label': 'Early momentum',
            'color': '#2E8B6E',
            'dot_class': 'wk8',
            'title': '🔋 Early momentum',
            'body': (
                "Beneficial bacteria begin expanding as prebiotic fibres consistently supply the substrate they need. "
                + (f"Your {primary_guild.lower() or 'targeted bacterial groups'} start moving toward healthy abundance levels. " if primary_guild else "")
                + "Short-chain fatty acid production increases as the fermentation chain becomes more active. "
                + ("Omega-3 EPA and DHA from your softgels are building up in cell membranes, typically reaching effective anti-inflammatory concentrations by week 6. " if has_omega else "")
                + ("Vitamin D3 supports ongoing immune regulation. " if has_vit_d else "")
                + "Many people start noticing improvements in digestion consistency and gut comfort during this window."
            ),
        },
        {
            'weeks': '9–12',
            'label': 'Deepening balance',
            'color': '#C97C2A',
            'dot_class': 'wk12',
            'title': '🛡️ Deepening balance',
            'body': (
                "Guild populations move toward their target ranges. Butyrate output increases as the fermentation chain "
                "becomes more complete — your gut lining receives more of the protective compounds it needs for repair. "
                + ("Ashwagandha's adaptogenic effects on the stress axis compound with continued use, typically most noticeable from week 8 onward. " if has_ashwagandha else "")
                + ("Magnesium Bisglycinate supports deeper sleep and stress resilience as tissue saturation builds. " if has_magnesium else "")
                + "Inflammatory metabolite pressure typically decreases during this phase as the SCFA-to-protein fermentation balance shifts."
            ),
        },
        {
            'weeks': '13–16',
            'label': 'Consolidation',
            'color': '#6B5EA8',
            'dot_class': 'wk16',
            'title': '⚖️ Consolidation',
            'body': (
                "The ecological shifts from earlier phases become self-sustaining. A more diverse, well-balanced "
                "community is better equipped to hold its gains under normal life pressures — stress, travel, or "
                "temporary dietary changes. "
                + ("Botanical adaptogens and the full vitamin-mineral stack have reached their most effective tissue concentrations. " if has_ashwagandha or has_magnesium else "")
                + "Weeks 13–16 are the ideal window for a reassessment sample to measure the full compositional shift."
            ),
        },
    ]

    return phases


# ════════════════════════════════════════════════════════════════════════════════
#  LLM — SUPPLEMENT WHY TEXTS (per delivery unit, from decision trace)
# ════════════════════════════════════════════════════════════════════════════════

def _generate_supplement_why_texts(
    formulation: dict,
    profile: dict,
    active_deviations: list,
    model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
    region: str = 'eu-west-1',
) -> dict:
    """
    Generate personalised "Why you're taking it" text for each delivery unit.

    Reads the decision trace from component_registry (substance + what_it_targets +
    based_on per component, grouped by delivery unit) and asks Claude to write
    one 2-sentence plain-English summary per unit.

    Returns dict: {delivery_format_key: "text"} — empty dict on any failure.
    Stored in interpretations JSON and read by build_supplement_cards().
    """
    if not formulation:
        return {}

    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not available — skipping supplement why-text generation")
        return {}

    # ── Step 1: Group component_registry by delivery unit ─────────────────
    from collections import defaultdict
    registry = formulation.get('component_registry', [])
    f = formulation.get('formulation', {})
    unit_keys = sorted(
        [k for k in f.keys() if k.startswith('delivery_format_') and isinstance(f[k], dict)],
        key=lambda k: k
    )

    # Map delivery label → format key (component_registry uses short labels like "probiotic capsule")
    DELIVERY_LABEL_TO_KEY = {
        'probiotic capsule': 'delivery_format_1_probiotic_capsule',
        'softgel': 'delivery_format_2_omega_softgels',
        'sachet': 'delivery_format_3_daily_sachet',
        'powder': 'delivery_format_3_powder_jar',
        'morning wellness capsule': 'delivery_format_4_morning_wellness_capsules',
        'morning capsule': 'delivery_format_4_morning_wellness_capsules',
        'evening wellness capsule': 'delivery_format_5_evening_wellness_capsules',
        'evening capsule': 'delivery_format_4_evening_capsule',
        'polyphenol capsule': 'delivery_format_6_polyphenol_capsule',
        'polyphenol': 'delivery_format_6_polyphenol_capsule',
    }

    # Build reverse map: actual unit key → display name
    DISPLAY_NAMES = {
        'delivery_format_1_probiotic_capsule': 'Probiotic Capsule',
        'delivery_format_2_omega_softgels': 'Omega & Antioxidant Softgel',
        'delivery_format_3_daily_sachet': 'Daily Prebiotic Sachet',
        'delivery_format_3_powder_jar': 'Daily Prebiotic Powder',
        'delivery_format_4_morning_wellness_capsules': 'Morning Wellness Capsule',
        'delivery_format_4_evening_capsule': 'Evening Wellness Capsule',
        'delivery_format_5_evening_wellness_capsules': 'Evening Wellness Capsule',
        'delivery_format_5_polyphenol_capsule': 'Polyphenol Capsule',
        'delivery_format_6_polyphenol_capsule': 'Polyphenol Capsule',
    }

    # Group components by unit key
    by_unit: dict = defaultdict(list)
    for comp in registry:
        delivery_raw = (comp.get('delivery', '') or '').lower().strip()
        # Try exact and partial match to delivery label map
        matched_key = None
        for label, key in DELIVERY_LABEL_TO_KEY.items():
            if label in delivery_raw or delivery_raw in label:
                matched_key = key
                break
        if matched_key and matched_key in unit_keys:
            by_unit[matched_key].append(comp)

    # Ensure all unit keys are represented (even if no registry match)
    for uk in unit_keys:
        if uk not in by_unit:
            by_unit[uk] = []

    # ── Step 2: Build ingredient table per unit ───────────────────────────
    first_name = profile.get('first_name', 'the client')
    goals = ', '.join(profile.get('goals', [])) or 'general wellbeing'
    dev_summary = '; '.join(
        f"{d.get('client_label','')} ({d.get('value_str','')})"
        for d in active_deviations[:4]
        if isinstance(d, dict) and d.get('client_label')
    ) or 'no specific deviations'

    unit_blocks = []
    for uk in unit_keys:
        comps = by_unit.get(uk, [])
        unit_name = DISPLAY_NAMES.get(uk, uk.replace('_', ' ').title())

        # Also pull from unit components directly if registry match was sparse
        unit_data = f.get(uk, {})
        direct_comps = unit_data.get('components', unit_data.get('components_per_softgel', []))

        lines = []
        for c in comps[:10]:
            substance = c.get('substance', '')
            targets = c.get('what_it_targets', '') or ', '.join(c.get('health_claims', []))
            based_on = c.get('based_on', '')
            if substance:
                lines.append(f"  - {substance}: targets={targets} | reason={based_on[:80]}")

        # Fallback: use direct component rationales if registry was empty
        if not lines and direct_comps:
            for c in direct_comps[:8]:
                substance = c.get('substance', '')
                rationale = c.get('rationale', '')
                if substance and rationale:
                    lines.append(f"  - {substance}: {rationale[:100]}")

        if lines:
            block = f"{unit_name} (key: {uk}):\n" + '\n'.join(lines)
        else:
            block = f"{unit_name} (key: {uk}): [no component detail available]"
        unit_blocks.append(block)

    if not unit_blocks:
        return {}

    # ── Step 3: Single LLM call ───────────────────────────────────────────
    prompt = f"""You are writing the "Why you're taking it" text for each supplement unit in a personalised health report.

Client: {first_name}
Health goals: {goals}
Key microbiome findings: {dev_summary}

For EACH supplement unit below, write exactly 2 warm, plain-English sentences explaining:
1. What this unit collectively does (not ingredient by ingredient)
2. Why it's specifically relevant for this client given their goals and microbiome findings

Rules:
- No Latin names, no ingredient names unless they are very familiar (e.g. "Omega-3", "Vitamin C")
- No jargon — say "gut bacteria" not "microbiota", "protective compounds" not "SCFAs"
- Start with the collective purpose of the unit, not "This unit contains..."
- Reference the client's specific goal or finding where relevant
- Keep each answer to 2 sentences, max 50 words total per unit

SUPPLEMENT UNITS:
{chr(10).join(unit_blocks)}

Return ONLY valid JSON, no other text:
{{
  "delivery_format_1_probiotic_capsule": "<2 sentences>",
  "delivery_format_2_omega_softgels": "<2 sentences>",
  "<other_unit_key>": "<2 sentences>",
  ...
}}

Include exactly the keys listed in SUPPLEMENT UNITS above (use the exact key string in parentheses).
If a unit has no component detail, write a brief generic sentence about what that type of unit typically does."""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 800,
                'temperature': 0.4,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        raw_text = result_body['content'][0]['text'].strip()
        if raw_text.startswith('```'):
            raw_text = raw_text.split('\n', 1)[1] if '\n' in raw_text else raw_text[3:]
            if raw_text.endswith('```'):
                raw_text = raw_text[:-3]
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            # Filter to only valid unit keys
            return {k: v for k, v in parsed.items() if k in unit_keys and isinstance(v, str) and v.strip()}
        return {}
    except Exception as e:
        logger.warning(f"Supplement why-text LLM call failed: {e}")
        return {}


# ════════════════════════════════════════════════════════════════════════════════
#  SUPPLEMENT CARDS
# ════════════════════════════════════════════════════════════════════════════════

def build_supplement_cards(formulation: dict, analysis: dict) -> list:
    """Build one card per delivery unit from the formulation.

    Key-agnostic: discovers all delivery_format_* keys from the formulation JSON
    rather than relying on a hardcoded list. Works with both old naming
    (daily_sachet, evening_capsule) and new naming (powder_jar,
    morning_wellness_capsules, evening_wellness_capsules).
    """
    if not formulation:
        return []

    cards = []
    f = formulation.get('formulation', {})
    eco_rationale = formulation.get('ecological_rationale', {})

    UNIT_COLORS = ['#1E1E2A', '#C97C2A', '#2E8B6E', '#6B5EA8', '#3A6EA8', '#8B5E3C']
    UNIT_EMOJI = ['💊', '🐟', '🌿', '🌙', '🌙', '🌙']

    # Key-agnostic: discover all delivery_format_* keys from the actual JSON
    unit_keys = sorted(
        [k for k in f.keys() if k.startswith('delivery_format_') and isinstance(f[k], dict)],
        key=lambda k: k  # alphabetical ensures numeric order (1, 2, 3...)
    )

    # Display names — covers both old and new naming schemes
    DISPLAY_NAMES = {
        # Old naming (batches 001-002, 005-006)
        'delivery_format_1_probiotic_capsule': 'Probiotic Capsule',
        'delivery_format_2_omega_softgels': 'Omega & Antioxidant Softgel',
        'delivery_format_3_daily_sachet': 'Daily Prebiotic Sachet',
        'delivery_format_4_evening_capsule': 'Evening Wellness Capsule',
        'delivery_format_4b_evening_capsule_2': 'Evening Wellness Capsule 2',
        'delivery_format_5_polyphenol_capsule': 'Polyphenol Capsule',
        # New naming (batches 003-004, 007-009)
        'delivery_format_3_powder_jar': 'Daily Prebiotic Powder',
        'delivery_format_4_morning_wellness_capsules': 'Morning Wellness Capsule',
        'delivery_format_5_evening_wellness_capsules': 'Evening Wellness Capsule',
        'delivery_format_6_polyphenol_capsule': 'Polyphenol Capsule',
    }

    WHAT_IT_IS = {
        # Old naming
        'delivery_format_1_probiotic_capsule': 'Live beneficial bacteria, selected specifically for your microbiome pattern.',
        'delivery_format_2_omega_softgels': 'Essential fatty acids and antioxidants in an easy-to-absorb softgel form.',
        'delivery_format_3_daily_sachet': 'A blend of prebiotic fibres and micronutrients — food for your beneficial bacteria plus targeted vitamin and mineral support.',
        'delivery_format_4_evening_capsule': 'Botanical and functional molecules timed to work with your body\'s evening recovery processes.',
        'delivery_format_4b_evening_capsule_2': 'Additional evening support with targeted functional compounds.',
        'delivery_format_5_polyphenol_capsule': 'Concentrated plant compounds that modulate gut bacteria and reduce inflammatory signals.',
        # New naming
        'delivery_format_3_powder_jar': 'A blend of prebiotic fibres mixed into water — food for your beneficial bacteria, designed for easy daily use.',
        'delivery_format_4_morning_wellness_capsules': 'Targeted vitamins, minerals, and botanical compounds selected for your specific health profile.',
        'delivery_format_5_evening_wellness_capsules': 'Botanical and functional molecules timed to work with your body\'s evening recovery processes.',
        'delivery_format_6_polyphenol_capsule': 'Concentrated plant compounds that modulate gut bacteria and reduce inflammatory signals.',
    }

    TIMING_DISPLAY = {
        'morning': '🌅 Every morning',
        'evening': '🌙 30–60 min before bed',
        'morning_with_food': '🌅 Every morning with food',
    }

    # Get ecological rationale for probiotic card
    eco_rec = eco_rationale.get('recommendation', '')

    # Get primary intervention guild
    primary_guild = ''
    for iv in formulation.get('priority_interventions', []):
        if iv.get('priority_level') in ('1A', '1B', 'CRITICAL'):
            primary_guild = iv.get('guild_name', '')
            break

    card_num = 0
    for uk in unit_keys:
        unit = f.get(uk)
        if not unit:
            continue

        card_num += 1
        fmt = unit.get('format', {})
        timing = fmt.get('timing', 'morning')
        daily_count = fmt.get('daily_count', 1)

        timing_str = TIMING_DISPLAY.get(timing, f"{'🌅' if timing == 'morning' else '🌙'} Daily")
        if daily_count > 1:
            timing_str = f"{timing_str} ({daily_count}×)"

        # Collect ingredients
        pills = []
        if 'components' in unit:
            for c in unit['components']:
                dose = c.get('dose', c.get('dose_mg', c.get('dose_g', '')))
                pills.append({'name': c.get('substance', ''), 'dose': str(dose)})
        elif 'components_per_softgel' in unit:
            for c in unit['components_per_softgel']:
                dose = c.get('dose_per_softgel', '')
                pills.append({'name': c.get('substance', ''), 'dose': str(dose)})
        else:
            # Sachet — combine prebiotics and vitamins
            for section in ['prebiotics', 'vitamins_minerals', 'supplements']:
                sec_data = unit.get(section, {})
                for c in sec_data.get('components', []):
                    dose = c.get('dose_g', c.get('dose', ''))
                    pills.append({'name': c.get('substance', ''), 'dose': str(dose)})

        # Why text: use ecological_rationale for probiotics, component rationales otherwise
        why_text = ''
        if uk == 'delivery_format_1_probiotic_capsule':
            if eco_rec:
                # Trim to first 2 sentences
                sentences = eco_rec.split('. ')
                why_text = '. '.join(sentences[:2]) + ('.' if len(sentences) > 1 else '')
            elif primary_guild:
                why_text = f'Selected to address {primary_guild.lower()} imbalance detected in your microbiome analysis. The strains in this capsule support your specific fermentation pattern.'
        elif uk == 'delivery_format_2_omega_softgels':
            why_text = 'Omega-3 EPA and DHA support gut barrier integrity, reduce inflammatory signalling, and support brain and mood function — all directly relevant to your health goals and microbiome pattern.'
        elif uk == 'delivery_format_3_daily_sachet':
            # Pull prebiotic strategy
            strat = formulation.get('decisions', {}).get('prebiotic_design', {}).get('strategy', '')
            if strat:
                sentences = strat.split('. ')
                why_text = '. '.join(sentences[:2]) + '.'
            else:
                why_text = 'The prebiotic blend selectively feeds the beneficial bacteria your microbiome analysis identified as needing support, while the vitamins and minerals address the nutrient needs flagged in your questionnaire.'
        elif uk in ('delivery_format_4_evening_capsule', 'delivery_format_4b_evening_capsule_2'):
            # Summarise component rationales — full text, no truncation
            reasons = []
            for c in unit.get('components', []):
                r = c.get('rationale', '')
                if r:
                    reasons.append(r)
            why_text = ' '.join(reasons[:2]) if reasons else 'Evening botanical support targets the specific areas flagged in your lifestyle questionnaire, timed to work with your body\'s natural rest-and-repair cycle.'
        elif uk == 'delivery_format_3_powder_jar':
            strat = formulation.get('decisions', {}).get('prebiotic_design', {}).get('strategy', '')
            if strat:
                sentences = strat.split('. ')
                why_text = '. '.join(sentences[:2]) + '.'
            else:
                why_text = 'The prebiotic powder selectively feeds the beneficial bacteria your microbiome analysis identified as needing support, while delivering the micronutrients flagged in your questionnaire.'
        elif uk == 'delivery_format_4_morning_wellness_capsules':
            reasons = []
            for c in unit.get('components', []):
                r = c.get('rationale', '')
                if r:
                    reasons.append(r)
            why_text = ' '.join(reasons[:2]) if reasons else 'Morning botanical and micronutrient support is targeted to the specific immune, energy, and metabolic needs identified in your profile and microbiome results.'
        elif uk == 'delivery_format_5_evening_wellness_capsules':
            reasons = []
            for c in unit.get('components', []):
                r = c.get('rationale', '')
                if r:
                    reasons.append(r)
            why_text = ' '.join(reasons[:2]) if reasons else 'Evening botanical support targets the specific areas flagged in your lifestyle questionnaire, timed to work with your body\'s natural rest-and-repair cycle.'
        elif uk == 'delivery_format_6_polyphenol_capsule':
            reasons = []
            for c in unit.get('components', []):
                r = c.get('rationale', '')
                if r:
                    reasons.append(r)
            why_text = ' '.join(reasons[:2]) if reasons else 'Concentrated plant compounds that selectively modulate the gut bacteria flagged in your analysis, reducing inflammatory byproducts and supporting ecosystem rebalancing.'

        # ── Universal fallback: any unrecognised unit key ──────────────────────
        if not why_text:
            reasons = []
            for c in unit.get('components', []):
                r = c.get('rationale', '')
                if r:
                    reasons.append(r)
            why_text = ' '.join(reasons[:2]) if reasons else f'{DISPLAY_NAMES.get(uk, "This supplement unit")} is included based on your specific microbiome results and health questionnaire.'

        # What it supports — derive from component health claims
        supports = set()
        for comp in formulation.get('component_registry', []):
            delivery = comp.get('delivery', '')
            # Match delivery type
            if uk == 'delivery_format_1_probiotic_capsule' and 'probiotic capsule' in delivery.lower():
                for hc in comp.get('health_claims', []):
                    supports.add(hc)
            elif uk == 'delivery_format_2_omega_softgels' and 'softgel' in delivery.lower():
                for hc in comp.get('health_claims', []):
                    supports.add(hc)
            elif uk == 'delivery_format_3_daily_sachet' and 'sachet' in delivery.lower():
                for hc in comp.get('health_claims', []):
                    supports.add(hc)
            elif 'evening capsule' in delivery.lower() and 'evening' in timing:
                for hc in comp.get('health_claims', []):
                    supports.add(hc)

        # Clean up support labels
        support_labels = [s for s in sorted(supports) if s and len(s) < 60][:5]

        cards.append({
            'num': card_num,
            'key': uk,
            'name': DISPLAY_NAMES.get(uk, 'Supplement Unit'),
            'timing': timing_str,
            'what_it_is': WHAT_IT_IS.get(uk, 'A targeted supplement unit.'),
            'why': why_text,
            'pills': pills,
            'supports': support_labels,
            'color': UNIT_COLORS[card_num - 1] if card_num <= len(UNIT_COLORS) else '#1E1E2A',
            'emoji': UNIT_EMOJI[card_num - 1] if card_num <= len(UNIT_EMOJI) else '💊',
        })

    return cards


# ════════════════════════════════════════════════════════════════════════════════
#  HEALTH GOALS ALIGNMENT
# ════════════════════════════════════════════════════════════════════════════════

def build_goal_cards(questionnaire: dict, formulation: dict, analysis: dict) -> list:
    """Build one card per stated health goal."""

    GOAL_DETAILS = {
        'strengthen_immune_resilience': {
            'emoji': '🛡️',
            'title': 'Stronger immune resilience',
            'mechanism': 'Around 70% of your immune system is shaped by your gut bacteria. Beneficial bacteria modulate immune signalling, train immune cells, and maintain the gut barrier that prevents unwanted particles from triggering immune responses.',
            'formula_link': 'Your formula targets this through Zinc and Vitamin C in the sachet (directly support immune cell activity), Omega-3 EPA/DHA (resolve inflammatory signals), Vitamin D3 (regulates immune training), and the probiotic mix (rebuilds beneficial bacteria that educate immune cells).',
        },
        'improve_mood_reduce_anxiety': {
            'emoji': '🧘',
            'title': 'Better mood and reduced anxiety',
            'mechanism': 'Your gut produces about 90% of your body\'s serotonin and communicates directly with your brain via the vagus nerve. Gut microbiome imbalances — especially disruptions to beneficial bacteria — have been consistently linked to mood and anxiety patterns.',
            'formula_link': 'Your formula targets this through Ashwagandha (clinical evidence for cortisol reduction and anxiety relief), LP815 probiotic strain (produces calming GABA), Omega-3 DHA (brain health and mood regulation), and Magnesium Bisglycinate (supports the nervous system and stress resilience).',
        },
        'improve_sleep_quality': {
            'emoji': '😴',
            'title': 'Better sleep quality',
            'mechanism': 'Sleep and the gut microbiome operate in a bidirectional relationship. Gut bacteria influence circadian rhythm signals, serotonin and melatonin precursor availability, and inflammatory mediators that affect sleep architecture.',
            'formula_link': 'Your formula targets this through L-Theanine (promotes relaxation without sedation), Magnesium Bisglycinate (supports deep sleep phases), Ashwagandha (reduces HPA-axis activation that disrupts sleep onset), and the LP815 strain (GABA production supports calm states before sleep).',
        },
        'improve_digestive_comfort': {
            'emoji': '🌿',
            'title': 'Better digestive comfort',
            'mechanism': 'Digestive discomfort — bloating, irregular motility, and abdominal sensitivity — often traces back to microbiome imbalances: too much protein fermentation, too little fiber processing, or an ecosystem that is reacting to its own composition.',
            'formula_link': 'Your formula targets this through the prebiotic sachet (gradually feeds beneficial bacteria to shift fermentation balance), the probiotic mix (introduces strains with evidence for gut comfort), and PHGG (a gentle fiber that improves bowel regularity without triggering discomfort).',
        },
        'improve_energy_levels': {
            'emoji': '⚡',
            'title': 'Improved energy levels',
            'mechanism': 'The gut microbiome influences energy through multiple pathways: butyrate fuels colonocytes and regulates metabolism, B-vitamins produced by gut bacteria support cellular energy pathways, and inflammatory load from a disrupted microbiome creates systemic fatigue.',
            'formula_link': 'Your formula supports this through B12 and Folate in the sachet (essential for energy metabolism), Omega-3 (mitochondrial membrane support), and the probiotic and prebiotic blend (restores butyrate production, reducing inflammatory drain on energy).',
        },
        'weight_management': {
            'emoji': '⚖️',
            'title': 'Metabolic and weight support',
            'mechanism': 'Gut bacteria regulate metabolism through short-chain fatty acids that control insulin sensitivity, hunger hormones (GLP-1, PYY), and fat storage signalling. A fiber-rich, butyrate-producing microbiome is consistently associated with healthier metabolic outcomes.',
            'formula_link': 'Your formula targets this through the prebiotic blend (feeds bacteria that produce propionate and butyrate — key metabolic regulators), the probiotic mix (includes strains with metabolic support evidence), and Omega-3 (supports insulin sensitivity and metabolic flexibility).',
        },
        'metabolic_support': {
            'emoji': '🔬',
            'title': 'Metabolic health optimisation',
            'mechanism': 'Your gut bacteria directly regulate metabolic pathways: short-chain fatty acids modulate insulin response, Bifidobacteria influence lipid metabolism, and the composition of your fermentation byproducts affects liver function and systemic inflammation.',
            'formula_link': 'Your formula targets this through the prebiotic sachet (increases SCFA production), Omega-3 EPA/DHA (lipid metabolism and insulin sensitivity), Beta-glucans (known cholesterol and glucose metabolism support), and the probiotic mix (fermentation pattern restoration).',
        },
        'reduce_bloating': {
            'emoji': '🫧',
            'title': 'Reduce bloating',
            'mechanism': 'Bloating typically reflects fermentation imbalance — too much gas production from protein fermentation, or fermentation happening in the wrong location because bacteria are poorly distributed. Microbiome rebalancing directly addresses the source.',
            'formula_link': 'Your formula targets this through PHGG as the primary prebiotic (well-tolerated even by sensitive guts), moderate FODMAP dosing matched to your sensitivity level, and the probiotic mix (redistributes fermentation activity toward more comfortable patterns over time).',
        },
        'improve_bowel_regularity': {
            'emoji': '🔄',
            'title': 'Improved bowel regularity',
            'mechanism': 'Bowel regularity is closely regulated by the gut microbiome through motility signalling, butyrate effects on colon muscle tone, and serotonin production by gut bacteria. A more balanced bacterial community usually produces more consistent bowel patterns.',
            'formula_link': 'Your formula targets this through resistant starch and PHGG in the sachet (proven effects on bowel regularity and stool consistency), the probiotic mix (strains selected for motility support), and the overall shift toward a more butyrate-rich fermentation environment.',
        },
        'boost_energy_reduce_fatigue': {
            'emoji': '⚡',
            'title': 'Boost energy and reduce fatigue',
            'mechanism': 'The gut microbiome influences energy through multiple pathways: butyrate fuels colonocytes and regulates metabolism, B-vitamins produced by gut bacteria support cellular energy production, and inflammatory load from a disrupted microbiome creates systemic fatigue. A well-balanced gut is the foundation of sustained energy.',
            'formula_link': 'Your formula targets this through B12 and Folate (essential for energy metabolism), Omega-3 (mitochondrial membrane support), the probiotic and prebiotic blend (restores butyrate production and reduces inflammatory drain on energy), and adaptogenic support to address the stress-fatigue axis.',
        },
        'general_wellness_healthy_aging': {
            'emoji': '🌿',
            'title': 'General wellness and healthy ageing',
            'mechanism': 'A balanced, resilient gut microbiome is one of the most consistent predictors of healthy ageing across populations. Gut bacteria influence inflammation, metabolic function, immune competence, and even cognitive health — all of which are central to long-term vitality.',
            'formula_link': 'Your formula is designed holistically — the probiotic mix, prebiotic blend, Omega-3, antioxidant support, and targeted nutrients all work together to create the conditions your gut needs to support systemic health over the long term.',
        },
        'longevity_healthy_aging': {
            'emoji': '🌿',
            'title': 'Longevity and healthy ageing',
            'mechanism': 'A balanced, resilient gut microbiome is one of the most consistent predictors of healthy ageing across populations. Gut bacteria influence inflammation, metabolic function, immune competence, and even cognitive health — all of which are central to long-term vitality.',
            'formula_link': 'Your formula is designed holistically — the probiotic mix, prebiotic blend, Omega-3, antioxidant support, and targeted nutrients all work together to create the conditions your gut needs to support systemic health over the long term.',
        },
        'other': {
            'emoji': '🎯',
            'title': 'Your personal health goal',
            'mechanism': 'A balanced, resilient gut microbiome provides the foundation for many aspects of health — from energy and metabolism to immunity and mental clarity. Supporting your microbiome creates positive ripple effects across multiple systems.',
            'formula_link': 'Your formula is designed holistically — the probiotic mix, prebiotic blend, and targeted nutrients all work together to create the conditions your gut needs to function at its best.',
        },
    }

    if not questionnaire:
        return []

    qdata = questionnaire.get('questionnaire_data', {})
    goals_raw = qdata.get('step_1', {}).get('goals', {}).get('main_goals_ranked', [])
    other_detail = qdata.get('step_1', {}).get('goals', {}).get('other_goal_details', '')

    stated_goal_keys = set(goals_raw[:4])
    cards = []
    for g in goals_raw[:4]:  # max 4 stated goals
        details = GOAL_DETAILS.get(g, GOAL_DETAILS['other']).copy()
        if g == 'other' and other_detail:
            details['title'] = other_detail

        cards.append({
            'emoji': details['emoji'],
            'title': details['title'],
            'mechanism': details['mechanism'],
            'formula_link': details['formula_link'],
            'inferred': False,
        })

    # ── Inferred goals from formulation health claims ────────────────────────
    # Scan all components in the registry for health_claims strings
    # and map them to goal keys not already stated by the client.
    CLAIM_TO_GOAL = {
        'Stress/Anxiety':    'improve_mood_reduce_anxiety',
        'Sleep Quality':     'improve_sleep_quality',
        'Immune Resilience': 'strengthen_immune_resilience',
        'Fatigue':           'boost_energy_reduce_fatigue',
        'Digestive Comfort': 'improve_digestive_comfort',
        'Mood':              'improve_mood_reduce_anxiety',
        'Energy':            'improve_energy_levels',
        'Bowel Regularity':  'improve_bowel_regularity',
        'Metabolic Health':  'metabolic_support',
    }

    if formulation and len(cards) < 4:
        inferred_claims: set = set()
        for comp in formulation.get('component_registry', []):
            for hc in comp.get('health_claims', []):
                inferred_claims.add(hc)

        for claim, gkey in CLAIM_TO_GOAL.items():
            if len(cards) >= 4:
                break
            if claim not in inferred_claims:
                continue
            if gkey in stated_goal_keys:
                continue
            # Avoid duplicating already-inferred keys
            if any(c.get('_goal_key') == gkey for c in cards):
                continue
            details = GOAL_DETAILS.get(gkey, GOAL_DETAILS['other']).copy()
            cards.append({
                'emoji': details['emoji'],
                'title': details['title'],
                'mechanism': details['mechanism'],
                'formula_link': details['formula_link'],
                'inferred': True,         # renders a subtle tag in HTML
                '_goal_key': gkey,        # internal dedup key (stripped before render)
            })

    # Strip internal dedup key before returning
    for c in cards:
        c.pop('_goal_key', None)

    return cards


# ════════════════════════════════════════════════════════════════════════════════
#  LLM CONSISTENCY CHECK
# ════════════════════════════════════════════════════════════════════════════════

def run_consistency_check(data: dict, model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
                           region: str = 'eu-west-1') -> dict:
    """Run LLM consistency check on the assembled report content. Prints to console."""
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not available — skipping LLM consistency check")
        return {'status': 'skipped', 'reason': 'boto3 not available'}

    sample_id = data.get('sample_id', 'unknown')
    analysis = data.get('analysis', {})
    formulation = data.get('formulation', {})
    circle_scores = data.get('circle_scores', {})
    sw = data.get('strengths_challenges', {})
    phases = data.get('timeline_phases', [])
    supp_cards = data.get('supplement_cards', [])
    goal_cards = data.get('goal_cards', [])

    score_total = analysis.get('overall_score', {}).get('total', 0)
    score_band = analysis.get('overall_score', {}).get('band', '')

    strengths_text = '\n'.join([f"- {s['title']}: {s['text'][:100]}" for s in sw.get('strengths', [])])
    challenges_text = '\n'.join([f"- {c['title']} ({c.get('severity','?')}): {c['text'][:100]}" for c in sw.get('challenges', [])])
    supplements_text = '\n'.join([f"- {c['name']}: {c['why'][:300]}" for c in supp_cards])
    goals_text = '\n'.join([f"- {g['title']}: formula: {g['formula_link'][:300]}" for g in goal_cards])

    prompt = f"""You are reviewing a client-facing microbiome health report for logical consistency.

Sample: {sample_id}
Overall score: {score_total}/100 ({score_band})

Circle scores:
- Gut Lining Protection: {circle_scores.get('gut_lining', '?')}%
- Inflammation Control: {circle_scores.get('inflammation', '?')}%
- Fiber Processing: {circle_scores.get('fiber_processing', '?')}%
- Bifidobacteria Presence: {circle_scores.get('bifidobacteria', '?')}%

Strengths identified:
{strengths_text if strengths_text else "(none)"}

Challenges identified:
{challenges_text if challenges_text else "(none)"}

Supplement units and their WHY explanations:
{supplements_text if supplements_text else "(none)"}

Health goals and formula links:
{goals_text if goals_text else "(none)"}

Please check for:
1. Any contradictions between strengths and challenges (e.g., claiming low inflammation but flagging proteolytic overgrowth)
2. Whether supplement WHY explanations logically address the identified challenges
3. Whether health goal explanations are consistent with the identified microbiome patterns
4. Whether the circle scores are directionally consistent with the strengths/challenges
5. Any other logical inconsistencies in the report

Respond in this exact format:
CONSISTENCY_CHECK_START
[Overall: PASS | WARN | FAIL]
[Checks:]
- [PASS|WARN|FAIL]: <specific check description>
- [PASS|WARN|FAIL]: <specific check description>
(continue for each check)
[Summary]: <1-2 sentence overall assessment>
CONSISTENCY_CHECK_END"""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 600,
                'temperature': 0.5,
                'messages': [{'role': 'user', 'content': prompt}]
            }),
            contentType='application/json',
            accept='application/json',
        )
        result_body = json.loads(response['body'].read())
        result_text = result_body['content'][0]['text']

        # Extract and display
        if 'CONSISTENCY_CHECK_START' in result_text:
            check_content = result_text.split('CONSISTENCY_CHECK_START')[1].split('CONSISTENCY_CHECK_END')[0].strip()
        else:
            check_content = result_text.strip()

        _print_consistency_block(sample_id, check_content)
        return {'status': 'complete', 'result': check_content}

    except Exception as e:
        msg = f"Consistency check failed: {e}"
        logger.warning(msg)
        _print_consistency_block(sample_id, f"[WARN]: Could not complete check — {e}\n[Summary]: Manual review recommended.", failed=True)
        return {'status': 'error', 'reason': str(e)}


def _print_consistency_block(sample_id: str, content: str, failed: bool = False):
    """Print formatted consistency check to console."""
    border = '═' * 60
    print(f"\n╔{border}")
    print(f"║  REPORT CONSISTENCY CHECK — Sample {sample_id}")
    print(f"╠{border}")
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('[PASS]'):
            print(f"║  ✅ {line[6:].strip()}")
        elif line.startswith('[WARN]'):
            print(f"║  ⚠️  {line[6:].strip()}")
        elif line.startswith('[FAIL]'):
            print(f"║  ❌ {line[6:].strip()}")
        elif line.startswith('[Overall:'):
            print(f"║  {line}")
        elif line.startswith('[Summary]:'):
            print(f"║  💬 {line[10:].strip()}")
        elif line.startswith('[Checks:]'):
            print(f"║  {line}")
        else:
            print(f"║  {line}")
    print(f"╚{border}\n")


# ════════════════════════════════════════════════════════════════════════════════
#  HTML GENERATION
# ════════════════════════════════════════════════════════════════════════════════

def _esc(s) -> str:
    """HTML-escape a string value."""
    if s is None:
        return ''
    s = str(s)
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _score_color(score: float) -> str:
    if score >= 75:
        return '#2E8B6E'
    elif score >= 50:
        return '#C97C2A'
    else:
        return '#C24B3A'


def _dial_dashoffset(score: float, circumference: float = 232.0) -> float:
    """Convert 0-100 score to SVG stroke-dashoffset (0 = full circle filled)."""
    return circumference * (1.0 - score / 100.0)


def _pillar_pct(score: float, max_score: float) -> float:
    return round(score / max_score * 100) if max_score else 0


def generate_html(data: dict) -> str:
    """Assemble the complete HTML report.

    Reads ONLY from the flat health_report_interpretations JSON (schema v3.0).
    No raw file access — every field comes from a pre-computed JSON key.
    """
    sample_id = data['sample_id']
    profile = data['profile']
    circle_scores = data['circle_scores']
    sw = data['strengths_challenges']
    timeline_phases = data['timeline_phases']
    supplement_cards = data['supplement_cards']
    goal_cards = data['goal_cards']
    good_news = data['good_news']

    # Cover page — from flat JSON keys (schema v3.0)
    score_data = data.get('overall_score', {})
    total_score = score_data.get('total', 0)
    score_band = score_data.get('band', '')
    pillars = score_data.get('pillars', {})
    report_date = data.get('report_date', datetime.now().strftime('%Y-%m-%d'))
    report_date_display = datetime.strptime(report_date, '%Y-%m-%d').strftime('%d %B %Y') if report_date else '09 March 2026'

    score_color = _score_color(total_score)
    score_offset = _dial_dashoffset(total_score / 100 * 100, 283)

    # Score summary — pre-computed in schema v3.0, fallback for older schemas
    score_summary = data.get('score_summary', '')
    if not score_summary:
        distinct_areas = sw.get('distinct_areas', [])
        def _format_area_list(areas: list) -> str:
            bold = [f'<strong>{a}</strong>' for a in areas]
            if len(bold) == 0: return ''
            if len(bold) == 1: return bold[0]
            if len(bold) == 2: return f'{bold[0]} and {bold[1]}'
            return ', '.join(bold[:-1]) + f', and {bold[-1]}'
        if not distinct_areas:
            score_summary = f'Your overall score is <strong>{total_score}</strong> out of 100. Your gut ecosystem is in good shape.'
        elif len(distinct_areas) == 1:
            score_summary = f'Your overall score is <strong>{total_score}</strong> out of 100. The main area to focus on is {_format_area_list(distinct_areas)}.'
        else:
            score_summary = f'Your overall score is <strong>{total_score}</strong> out of 100. The main areas to focus on are {_format_area_list(distinct_areas)}.'

    # Key note from score drivers
    key_note = score_data.get('score_drivers', {}).get('key_note', '')

    # Bacterial groups for guild bars — from flat JSON (schema v3.0)
    bg = data.get('bacterial_groups', {})
    guilds_display = []
    GUILD_RANGES = {
        'fiber': (30, 50),
        'butyrate': (10, 25),
        'cross': (6, 12),
        'bifido': (2, 10),
        'hmo': (2, 10),
        'mucin': (1, 4),
        'proteolytic': (1, 5),
    }
    GUILD_LABELS = {
        'Fiber Degraders': ('Fiber Degraders', 'beneficial'),
        'Butyrate Producers': ('Butyrate Producers', 'beneficial'),
        'Cross-Feeders': ('Cross-Feeders', 'beneficial'),
        'Bifidobacteria': ('Bifidobacteria', 'beneficial'),
        'Mucin Degraders': ('Mucin Degraders', 'contextual'),
        'Proteolytic Guild': ('Proteolytic Guild', 'contextual'),
    }

    BAR_MAX = 55.0  # Visual max for bar display

    for gname, gdata in bg.items():
        abund = gdata.get('abundance', 0)
        status = gdata.get('status', '')
        clr = gdata.get('clr')

        # Determine color class
        gtype = 'beneficial'
        r_min, r_max = 0, 10
        for key, (rmin, rmax) in GUILD_RANGES.items():
            if key in gname.lower():
                r_min, r_max = rmin, rmax
                if key in ('mucin', 'proteolytic'):
                    gtype = 'contextual'
                break

        if gtype == 'beneficial':
            if abund == 0 or 'Below range' in status:
                # All below-range beneficial guilds → red (critical), not blue
                bar_class = 'critical'
                badge_class = 'badge-critical'
                badge_text = 'Absent' if abund == 0 else f'⚠ Below range · {abund:.1f}%'
            elif 'Above range' in status:
                bar_class = 'above'
                badge_class = 'badge-above'
                badge_text = f'↑ High · {abund:.1f}%'
            else:
                bar_class = 'ok'
                badge_class = 'badge-ok'
                badge_text = f'✓ Healthy · {abund:.1f}%'
        else:  # contextual
            if 'Above range' in status:
                bar_class = 'critical'
                badge_class = 'badge-critical'
                badge_text = f'↑ Elevated · {abund:.1f}%'
            else:
                bar_class = 'ok'
                badge_class = 'badge-ok'
                badge_text = f'✓ Controlled · {abund:.1f}%'

        # Bar position calculations (%)
        range_left_pct = r_min / BAR_MAX * 100
        range_width_pct = (r_max - r_min) / BAR_MAX * 100
        fill_width_pct = min(abund / BAR_MAX * 100, 100)

        # Note text from client interpretation — full text, no truncation
        note = gdata.get('client_interpretation', gdata.get('evenness_status', ''))

        guilds_display.append({
            'name': gname,
            'abund': abund,
            'bar_class': bar_class,
            'badge_class': badge_class,
            'badge_text': badge_text,
            'range_left_pct': round(range_left_pct, 1),
            'range_width_pct': round(range_width_pct, 1),
            'fill_width_pct': round(fill_width_pct, 1),
            'note': note,
        })

    # ── BUILD HTML ────────────────────────────────────────────────────────────

    parts = []

    parts.append(f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inside Your Gut — {_esc(profile.get("first_name", "Your"))}'s Report</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=Nunito:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --sand: #F5F0E8;
  --warm: #FDFAF5;
  --dark: #1E1E2A;
  --mid: #4A4858;
  --soft: #9A95A8;
  --rule: #E4DDD0;
  --green: #2E8B6E;
  --green-lt: #E8F5F1;
  --red: #C24B3A;
  --red-lt: #FCECEA;
  --amber: #C97C2A;
  --amber-lt: #FBF1E4;
  --blue: #3A6EA8;
  --blue-lt: #EAF0F8;
  --purple: #6B5EA8;
  --purple-lt: #F0EEF8;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Nunito',sans-serif; background:var(--sand); color:var(--dark); font-size:15px; line-height:1.7; }}

/* ── COVER ── */
.cover {{ min-height:100vh; background:var(--dark); display:grid; grid-template-rows:auto 1fr auto; padding:56px; position:relative; overflow:hidden; }}
.cover-blob1 {{ position:absolute; width:600px; height:600px; top:-150px; right:-150px; border-radius:50%; background:radial-gradient(circle,rgba(46,139,110,.22) 0%,transparent 65%); pointer-events:none; }}
.cover-blob2 {{ position:absolute; width:400px; height:400px; bottom:-100px; left:-80px; border-radius:50%; background:radial-gradient(circle,rgba(107,94,168,.18) 0%,transparent 65%); pointer-events:none; }}
.cover-top {{ display:flex; justify-content:space-between; align-items:center; position:relative; z-index:1; }}
.cover-brand {{ font-family:'Playfair Display',serif; font-size:15px; color:rgba(255,255,255,.5); letter-spacing:.15em; }}
.cover-tag {{ font-size:11px; letter-spacing:.25em; text-transform:uppercase; color:var(--green); background:rgba(46,139,110,.15); padding:6px 14px; border-radius:20px; }}
.cover-mid {{ display:flex; flex-direction:column; justify-content:center; position:relative; z-index:1; }}
.cover-eyebrow {{ font-size:12px; letter-spacing:.3em; text-transform:uppercase; color:rgba(255,255,255,.4); margin-bottom:20px; }}
.cover-h1 {{ font-family:'Playfair Display',serif; font-size:clamp(54px,8vw,96px); font-weight:400; line-height:1; color:white; margin-bottom:16px; }}
.cover-h1 span {{ font-style:italic; color:rgba(255,255,255,.38); }}
.cover-sub {{ font-size:16px; color:rgba(255,255,255,.5); max-width:420px; line-height:1.8; margin-bottom:48px; }}
.score-row {{ display:flex; align-items:center; gap:32px; }}
.score-dial {{ position:relative; width:120px; height:120px; flex-shrink:0; }}
.score-dial svg {{ transform:rotate(-90deg); }}
.dial-bg {{ fill:none; stroke:rgba(255,255,255,.08); stroke-width:10; }}
.dial-fill {{ fill:none; stroke-width:10; stroke-linecap:round; }}
.score-label {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.score-num {{ font-family:'Playfair Display',serif; font-size:30px; color:white; line-height:1; }}
.score-den {{ font-size:11px; color:rgba(255,255,255,.35); letter-spacing:.1em; }}
.score-info-text {{ color:rgba(255,255,255,.55); font-size:14px; max-width:320px; line-height:1.8; }}
.score-info-text strong {{ color:white; }}
.cover-bottom {{ position:relative; z-index:1; display:flex; gap:36px; flex-wrap:wrap; margin-top:24px; }}
.cover-stat .cs-label {{ font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:rgba(255,255,255,.25); margin-bottom:4px; }}
.cover-stat .cs-val {{ font-size:14px; color:rgba(255,255,255,.7); }}

/* Pillar scores */
.pillar-row {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:32px; position:relative; z-index:1; }}
.pillar-chip {{ background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1); border-radius:10px; padding:12px 16px; min-width:120px; }}
.pillar-chip .pc-name {{ font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:rgba(255,255,255,.35); margin-bottom:6px; }}
.pillar-chip .pc-score {{ font-family:'Playfair Display',serif; font-size:20px; color:white; }}
.pillar-chip .pc-max {{ font-size:11px; color:rgba(255,255,255,.3); }}
.pillar-chip .pc-bar {{ height:3px; background:rgba(255,255,255,.1); border-radius:2px; margin-top:8px; }}
.pillar-chip .pc-bar-fill {{ height:100%; border-radius:2px; }}

/* ── UTILITY ── */
.section {{ padding:72px 56px; background:var(--warm); border-bottom:1px solid var(--rule); }}
.section.sand {{ background:var(--sand); }}
.sec-label {{ font-size:10px; letter-spacing:.3em; text-transform:uppercase; color:var(--soft); margin-bottom:10px; }}
.sec-title {{ font-family:'Playfair Display',serif; font-size:38px; font-weight:400; margin-bottom:8px; }}
.sec-intro {{ color:var(--mid); max-width:600px; line-height:1.85; margin-bottom:48px; }}
.fade-in {{ opacity:0; transform:translateY(16px); animation:fadeUp .6s ease forwards; }}
@keyframes fadeUp {{ to {{ opacity:1; transform:none; }} }}

/* ── SECTION 1: DIALS ── */
.health-dials {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:48px; }}
.h-dial-card {{ background:var(--warm); border:1px solid var(--rule); border-radius:12px; padding:24px 16px; text-align:center; animation:fadeUp .5s ease both; }}
.h-dial-card:nth-child(1){{animation-delay:.05s}} .h-dial-card:nth-child(2){{animation-delay:.1s}}
.h-dial-card:nth-child(3){{animation-delay:.15s}} .h-dial-card:nth-child(4){{animation-delay:.2s}}
.h-dial-wrap {{ position:relative; width:88px; height:88px; margin:0 auto 12px; }}
.h-dial-wrap svg {{ transform:rotate(-90deg); }}
.hd-bg {{ fill:none; stroke:#E4DDD0; stroke-width:8; }}
.hd-fill {{ fill:none; stroke-width:8; stroke-linecap:round; stroke-dasharray:232; }}
.hd-label {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; }}
.hd-pct {{ font-family:'Playfair Display',serif; font-size:20px; line-height:1; }}
.hd-name {{ font-size:11px; color:var(--mid); margin-top:6px; font-weight:600; letter-spacing:.02em; }}
.hd-desc {{ font-size:12px; color:var(--soft); line-height:1.5; margin-top:4px; }}

/* ── GUILD BARS ── */
.guild-intro {{ display:flex; flex-direction:column; gap:20px; }}
.guild-explainer p {{ color:var(--mid); line-height:1.85; margin-bottom:14px; }}
.callout {{ background:var(--green-lt); border-left:3px solid var(--green); padding:16px 20px; border-radius:0 8px 8px 0; margin-top:20px; font-size:14px; color:var(--mid); line-height:1.7; }}
.guild-bars {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
.gbar {{ background:var(--warm); border:1px solid var(--rule); border-radius:10px; padding:16px 20px; position:relative; overflow:hidden; animation:fadeUp .5s ease both; }}
.gbar.critical {{ background:var(--red-lt); border-color:rgba(194,75,58,.3); }}
.gbar.below {{ background:var(--blue-lt); border-color:rgba(58,110,168,.3); }}
.gbar.above {{ background:var(--amber-lt); border-color:rgba(201,124,42,.3); }}
.gbar.ok {{ background:var(--green-lt); border-color:rgba(46,139,110,.2); }}
.gbar:nth-child(1){{animation-delay:.05s}} .gbar:nth-child(2){{animation-delay:.1s}}
.gbar:nth-child(3){{animation-delay:.15s}} .gbar:nth-child(4){{animation-delay:.2s}}
.gbar:nth-child(5){{animation-delay:.25s}} .gbar:nth-child(6){{animation-delay:.3s}}
.gbar-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
.gbar-name {{ font-size:13px; font-weight:600; }}
.gbar-badge {{ font-size:10px; font-weight:600; letter-spacing:.12em; text-transform:uppercase; padding:3px 10px; border-radius:20px; }}
.badge-critical {{ background:var(--red); color:white; }}
.badge-below {{ background:var(--blue); color:white; }}
.badge-above {{ background:var(--amber); color:white; }}
.badge-ok {{ background:var(--green); color:white; }}
.gbar-track {{ height:6px; background:rgba(0,0,0,.07); border-radius:3px; position:relative; margin-bottom:8px; }}
.gbar-range {{ position:absolute; height:100%; border-radius:3px; background:rgba(0,0,0,.12); }}
.gbar-fill {{ position:absolute; height:100%; border-radius:3px; transition:width 1.2s ease; }}
.fill-critical {{ background:var(--red); }}
.fill-below {{ background:var(--blue); }}
.fill-above {{ background:var(--amber); }}
.fill-ok {{ background:var(--green); }}
.gbar-note {{ font-size:12px; color:var(--mid); line-height:1.5; }}

/* ── SECTION 2: SW ── */
.sw-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
.sw-card {{ border-radius:12px; padding:32px; animation:fadeUp .5s ease both; }}
.sw-card.strengths {{ background:var(--green-lt); border:1px solid rgba(46,139,110,.25); }}
.sw-card.weaknesses {{ background:var(--red-lt); border:1px solid rgba(194,75,58,.25); }}
.sw-card-head {{ display:flex; align-items:center; gap:12px; margin-bottom:24px; }}
.sw-icon {{ width:40px; height:40px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; flex-shrink:0; }}
.icon-green {{ background:var(--green); }}
.icon-red {{ background:var(--red); }}
.sw-head-label {{ font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:var(--soft); }}
.sw-head-title {{ font-family:'Playfair Display',serif; font-size:22px; }}
.sw-items {{ display:flex; flex-direction:column; gap:14px; }}
.sw-item {{ display:flex; gap:12px; }}
.sw-dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; margin-top:7px; }}
.dot-green {{ background:var(--green); }}
.dot-red {{ background:var(--red); }}
.dot-amber {{ background:var(--amber); }}
.sw-text {{ font-size:14px; color:var(--mid); line-height:1.7; }}
.sw-text strong {{ color:var(--dark); }}
.sw-bottom {{ margin-top:32px; background:var(--warm); border-radius:10px; padding:20px 24px; border:1px solid var(--rule); font-size:14px; color:var(--mid); line-height:1.7; font-style:italic; }}

/* ── SECTION 3: ROOT CAUSES — flat layout ── */
.rc-grid {{ display:flex; flex-direction:column; gap:0; }}
.rc-dev-block {{ padding:28px 0; border-bottom:1px solid var(--rule); animation:fadeUp .5s ease both; }}
.rc-dev-block:first-child {{ padding-top:0; }}
.rc-dev-block:last-child {{ border-bottom:none; padding-bottom:0; }}
.rc-dev-block:nth-child(1){{animation-delay:.05s}} .rc-dev-block:nth-child(2){{animation-delay:.1s}}
.rc-dev-block:nth-child(3){{animation-delay:.15s}} .rc-dev-block:nth-child(4){{animation-delay:.2s}}
/* Narrative text — plain paragraphs, no coloured box */
.rc-narrative {{ margin-bottom:20px; }}
.rc-narrative p {{ font-size:14px; color:var(--mid); line-height:1.85; margin-bottom:10px; }}
.rc-narrative p:last-child {{ margin-bottom:0; }}
/* Factor stack — awareness-chip style */
.rc-factors-label {{ font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--soft);
  margin-bottom:10px; font-weight:600; display:flex; align-items:center; gap:10px; }}
.rc-factors-label::after {{ content:''; flex:1; height:1px; background:var(--rule); }}
.rc-factor-stack {{ display:flex; flex-direction:column; gap:8px; margin-bottom:4px; }}
/* Factor chip — matches awareness chip design */
details.rc-factor-chip {{ background:var(--warm); border:1px solid var(--rule); border-radius:10px; overflow:hidden; }}
details.rc-factor-chip summary {{ list-style:none; cursor:pointer; display:flex; align-items:center; gap:12px;
  padding:12px 18px; user-select:none; }}
details.rc-factor-chip summary::-webkit-details-marker {{ display:none; }}
details.rc-factor-chip summary::after {{ content:'▸'; font-size:11px; color:var(--soft); margin-left:auto; flex-shrink:0; }}
details.rc-factor-chip[open] summary::after {{ content:'▾'; }}
.rfc-icon {{ font-size:20px; flex-shrink:0; }}
.rfc-title {{ font-size:13px; font-weight:600; color:var(--dark); flex:1; min-width:0; }}
.rfc-ev-label {{ font-size:9px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
  color:white; padding:2px 8px; border-radius:20px; white-space:nowrap; flex-shrink:0; }}
.ev-label-established {{ background:var(--dark); }}
.ev-label-supported {{ background:var(--blue); }}
.ev-label-emerging {{ background:var(--amber); }}
/* Expanded body: personalised explanation + nested "What does science say" */
.rfc-body {{ padding:10px 18px 14px; border-top:1px solid var(--rule); }}
.rfc-text {{ font-size:13px; color:var(--mid); line-height:1.75; margin-bottom:10px; }}
details.rfc-science {{ background:var(--sand); border-radius:8px; overflow:hidden; margin-top:8px; }}
details.rfc-science summary {{ list-style:none; cursor:pointer; display:flex; align-items:center; gap:8px;
  padding:8px 12px; font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase;
  color:var(--soft); user-select:none; }}
details.rfc-science summary::-webkit-details-marker {{ display:none; }}
details.rfc-science summary::after {{ content:'＋'; font-size:12px; color:var(--soft); margin-left:auto; }}
details.rfc-science[open] summary::after {{ content:'－'; }}
.rfc-science-body {{ padding:0 12px 10px; font-size:12px; color:var(--mid); line-height:1.7; }}
/* Summary line takeaway */
.rc-takeaway {{ margin-top:16px; background:var(--green-lt); border-left:3px solid var(--green);
  border-radius:0 8px 8px 0; padding:12px 16px; font-size:14px; color:var(--green);
  font-weight:600; line-height:1.7; }}
/* ── SECTION 3: Zone B (awareness) ── */
.rc-awareness-zone {{ margin-top:40px; }}
.rc-awareness-label {{ font-size:10px; letter-spacing:.25em; text-transform:uppercase; color:var(--soft); margin-bottom:16px; display:flex; align-items:center; gap:10px; }}
.rc-awareness-label::after {{ content:''; flex:1; height:1px; background:var(--rule); }}
.rc-awareness-intro {{ font-size:13px; color:var(--mid); line-height:1.7; margin-bottom:20px; max-width:640px; }}
.rc-awareness-chips {{ display:flex; flex-direction:column; gap:10px; }}
details.rc-awareness-chip {{ background:var(--warm); border:1px solid var(--rule); border-radius:10px; overflow:hidden; }}
details.rc-awareness-chip summary {{ list-style:none; cursor:pointer; display:flex; align-items:center; gap:12px;
  padding:12px 18px; user-select:none; }}
details.rc-awareness-chip summary::-webkit-details-marker {{ display:none; }}
details.rc-awareness-chip summary::after {{ content:'▸'; font-size:11px; color:var(--soft); margin-left:auto; flex-shrink:0; }}
details.rc-awareness-chip[open] summary::after {{ content:'▾'; }}
.rc-aw-icon {{ font-size:20px; flex-shrink:0; }}
.rc-aw-title {{ font-size:13px; font-weight:600; color:var(--dark); }}
.rc-aw-arrow {{ font-size:11px; color:var(--soft); margin-left:4px; }}
.rc-aw-body {{ padding:0 18px 14px; font-size:13px; color:var(--mid); line-height:1.7; border-top:1px solid var(--rule); padding-top:12px; }}
/* ── empty state ── */
.rc-empty {{ background:var(--green-lt); border:1px solid rgba(46,139,110,.2); border-radius:12px; padding:32px; text-align:center; color:var(--mid); }}
.rc-empty p {{ font-size:14px; line-height:1.7; }}
/* ── SECTION 3 PLACEHOLDER (fallback) ── */
.placeholder-section {{ background:var(--amber-lt); border:1px dashed var(--amber); border-radius:12px; padding:32px; text-align:center; color:var(--mid); }}
.placeholder-section h3 {{ font-family:'Playfair Display',serif; font-size:22px; color:var(--amber); margin-bottom:8px; }}
.placeholder-section p {{ font-size:14px; line-height:1.7; }}

/* ── SECTION 3: FACTOR CARDS (evidence-based per physiological state) ── */
.rc-health-meaning {{ background:var(--blue-lt); border-left:3px solid var(--blue); border-radius:0 8px 8px 0;
  padding:14px 18px; margin-bottom:22px; }}
.rc-hm-label {{ font-size:10px; letter-spacing:.15em; text-transform:uppercase; color:var(--blue);
  margin-bottom:6px; font-weight:600; }}
.rc-hm-text {{ font-size:14px; color:var(--mid); line-height:1.8; }}
.rc-factors-label {{ font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--soft);
  margin-bottom:12px; font-weight:600; display:flex; align-items:center; gap:10px; }}
.rc-factors-label::after {{ content:''; flex:1; height:1px; background:var(--rule); }}
.rc-factor-stack {{ display:flex; flex-direction:column; gap:10px; margin-bottom:4px; }}
/* Individual factor card — collapsible <details> */
details.rc-factor-item {{ border-radius:10px; overflow:hidden; border:1px solid var(--rule); background:var(--warm); }}
details.rc-factor-item[open] {{ border-color:transparent; box-shadow:0 2px 12px rgba(0,0,0,.07); }}
/* Directionality accent — left border on the summary row */
details.rc-factor-item.rfi-driver {{ border-left:3px solid var(--dark); }}
details.rc-factor-item.rfi-bidirectional {{ border-left:3px solid var(--purple); }}
details.rc-factor-item.rfi-consequence {{ border-left:3px solid var(--amber); }}
details.rc-factor-item.rfi-associative {{ border-left:3px solid var(--blue); }}
/* Summary row — always visible collapsed state */
details.rc-factor-item > summary {{ list-style:none; cursor:pointer; display:flex; align-items:center;
  gap:10px; padding:13px 16px; user-select:none; }}
details.rc-factor-item > summary::-webkit-details-marker {{ display:none; }}
.rfi-icon {{ font-size:20px; flex-shrink:0; }}
.rfi-title {{ font-size:13px; font-weight:600; color:var(--dark); flex:1; min-width:0; }}
.rfi-arrow {{ font-size:10px; letter-spacing:.08em; color:var(--soft); white-space:nowrap; }}
.rfi-ev-badge {{ display:flex; align-items:center; gap:5px; flex-shrink:0; }}
.rfi-dots {{ display:flex; gap:3px; }}
.rfi-dot {{ width:6px; height:6px; border-radius:50%; background:var(--rule); }}
.rfi-dot.filled-strong {{ background:var(--dark); }}
.rfi-dot.filled-moderate {{ background:var(--blue); }}
.rfi-dot.filled-weak-moderate {{ background:var(--amber); }}
.rfi-dot.filled-weak {{ background:var(--soft); }}
.rfi-ev-label {{ font-size:9px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
  color:white; padding:2px 8px; border-radius:20px; white-space:nowrap; }}
.ev-label-established {{ background:var(--dark); }}
.ev-label-supported {{ background:var(--blue); }}
.ev-label-emerging {{ background:var(--amber); }}
.rfi-chevron {{ font-size:11px; color:var(--soft); margin-left:4px; flex-shrink:0; transition:transform .2s; }}
details.rc-factor-item[open] .rfi-chevron {{ transform:rotate(90deg); }}
/* Expanded body */
.rfi-body {{ padding:4px 16px 16px 46px; border-top:1px solid var(--rule); }}
.rfi-direction-row {{ font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:var(--soft);
  margin-bottom:8px; margin-top:8px; }}
.rfi-text {{ font-size:13px; color:var(--mid); line-height:1.7; }}
/* "Putting it together" block — LLM synthesis inside card */
.rc-synthesis {{ margin-top:20px; background:var(--sand); border-radius:8px; padding:14px 18px;
  border-left:3px solid var(--green); }}
.rc-synthesis-label {{ font-size:10px; letter-spacing:.15em; text-transform:uppercase; color:var(--green);
  margin-bottom:6px; font-weight:600; }}
.rc-synthesis-text {{ font-size:13px; color:var(--mid); line-height:1.75; font-style:italic; }}
/* Summary line takeaway */
.rc-takeaway {{ margin-top:16px; background:var(--green-lt); border-left:3px solid var(--green);
  border-radius:0 8px 8px 0; padding:12px 16px; font-size:14px; color:var(--green);
  font-weight:600; line-height:1.7; }}

/* ── SECTION 4: TIMELINE ── */
.turnaround-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:56px; align-items:start; }}
.timeline {{ position:relative; padding-left:36px; margin-top:8px; }}
.timeline::before {{ content:''; position:absolute; left:11px; top:8px; bottom:8px; width:2px; background:linear-gradient(to bottom,var(--blue),var(--green),var(--amber),var(--purple)); border-radius:2px; }}
.tl-item {{ position:relative; margin-bottom:36px; }}
.tl-dot {{ position:absolute; left:-30px; top:4px; width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:700; color:white; }}
.tl-period {{ font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--soft); margin-bottom:4px; }}
.tl-title {{ font-size:15px; font-weight:600; color:var(--dark); margin-bottom:6px; }}
.tl-body {{ font-size:14px; color:var(--mid); line-height:1.7; }}
.lifestyle-panel {{ background:var(--dark); border-radius:12px; padding:32px; color:white; }}
.lifestyle-panel .lp-label {{ font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:rgba(255,255,255,.4); margin-bottom:20px; }}
.lp-items {{ display:flex; flex-direction:column; gap:20px; }}
.lp-item {{ display:flex; gap:16px; align-items:flex-start; }}
.lp-emoji {{ font-size:24px; flex-shrink:0; }}
.lp-title {{ font-size:14px; font-weight:600; color:white; margin-bottom:4px; }}
.lp-text {{ font-size:13px; color:rgba(255,255,255,.55); line-height:1.65; }}

/* ── SECTION 5: SUPPLEMENTS ── */
.supp-units {{ display:flex; flex-direction:column; gap:28px; }}
.supp-unit {{ background:var(--warm); border:1px solid var(--rule); border-radius:12px; overflow:hidden; animation:fadeUp .5s ease both; }}
.supp-unit:nth-child(1){{animation-delay:.05s}} .supp-unit:nth-child(2){{animation-delay:.1s}}
.supp-unit:nth-child(3){{animation-delay:.15s}} .supp-unit:nth-child(4){{animation-delay:.2s}}
.supp-unit:nth-child(5){{animation-delay:.25s}} .supp-unit:nth-child(6){{animation-delay:.3s}}
.supp-header {{ display:grid; grid-template-columns:64px 1fr auto; align-items:center; border-bottom:1px solid var(--rule); }}
.supp-num-block {{ width:64px; height:64px; display:flex; align-items:center; justify-content:center; font-family:'Playfair Display',serif; font-size:26px; color:white; font-weight:400; }}
.supp-head-text {{ padding:14px 20px; }}
.supp-unit-name {{ font-size:15px; font-weight:600; }}
.supp-unit-when {{ font-size:12px; color:var(--soft); }}
.supp-unit-tag {{ padding:12px 20px; font-size:12px; color:var(--soft); white-space:nowrap; }}
.supp-meta {{ display:grid; grid-template-columns:1fr 1fr; gap:0; border-bottom:1px solid var(--rule); }}
.supp-meta-item {{ padding:16px 24px; border-right:1px solid var(--rule); }}
.supp-meta-item:last-child {{ border-right:none; }}
.smi-label {{ font-size:10px; letter-spacing:.15em; text-transform:uppercase; color:var(--soft); margin-bottom:4px; }}
.smi-text {{ font-size:13px; color:var(--mid); line-height:1.6; }}
.supp-why-band {{ padding:16px 24px; font-size:14px; color:var(--mid); line-height:1.75; display:flex; gap:12px; align-items:flex-start; border-bottom:1px solid var(--rule); }}
.why-icon {{ font-size:18px; flex-shrink:0; margin-top:2px; }}
.supp-pills {{ display:flex; flex-wrap:wrap; gap:8px; padding:16px 24px 20px; }}
.pill {{ background:var(--sand); border:1px solid var(--rule); border-radius:20px; padding:5px 14px; font-size:12px; color:var(--mid); white-space:nowrap; }}
.pill strong {{ color:var(--dark); font-weight:600; }}
.supp-supports {{ padding:0 24px 20px; }}
.supp-supports-label {{ font-size:10px; letter-spacing:.15em; text-transform:uppercase; color:var(--soft); margin-bottom:8px; }}
.supp-supports-chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
.sc-chip {{ background:var(--purple-lt); border:1px solid rgba(107,94,168,.2); border-radius:20px; padding:4px 12px; font-size:11px; color:var(--purple); }}

/* ── SECTION 6: GOALS ── */
.goals-row {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:20px; }}
.goal-card {{ border-radius:12px; padding:28px 24px; border:1px solid var(--rule); background:var(--warm); animation:fadeUp .5s ease both; }}
.goal-card:nth-child(1){{animation-delay:.05s}} .goal-card:nth-child(2){{animation-delay:.1s}}
.goal-card:nth-child(3){{animation-delay:.15s}} .goal-card:nth-child(4){{animation-delay:.2s}}
.goal-emoji {{ font-size:32px; margin-bottom:14px; display:block; }}
.goal-title {{ font-family:'Playfair Display',serif; font-size:18px; margin-bottom:10px; }}
.goal-mechanism {{ font-size:13px; color:var(--mid); line-height:1.75; margin-bottom:12px; }}
.goal-formula {{ font-size:13px; color:var(--mid); line-height:1.75; background:var(--sand); padding:12px 16px; border-radius:8px; border-left:3px solid var(--purple); }}

/* ── FOOTER ── */
.footer {{ background:var(--dark); color:rgba(255,255,255,.35); padding:36px 56px; display:flex; justify-content:space-between; align-items:center; font-size:12px; }}
.footer-brand {{ font-family:'Playfair Display',serif; font-size:16px; color:rgba(255,255,255,.6); }}

@media(max-width:900px) {{
  .cover {{ padding:36px 28px; }}
  .section {{ padding:56px 28px; }}
  .health-dials {{ grid-template-columns:1fr 1fr; }}
  .guild-intro {{ grid-template-columns:1fr; }}
  .sw-grid {{ grid-template-columns:1fr; }}
  .turnaround-grid {{ grid-template-columns:1fr; }}
  .goals-row {{ grid-template-columns:1fr; }}
  .footer {{ flex-direction:column; gap:16px; text-align:center; }}
  .pillar-row {{ gap:8px; }}
  .pillar-chip {{ min-width:100px; }}
}}

/* Pathway & interactive elements */
.pnode {{ cursor:pointer; transition:filter .2s; }}
.pnode:hover > rect {{ filter:brightness(0.96); }}
input.tp-range {{ -webkit-appearance:none; width:100%; height:3px; border-radius:2px; background:var(--rule); outline:none; cursor:pointer; }}
input.tp-range::-webkit-slider-thumb {{ -webkit-appearance:none; width:16px; height:16px; border-radius:50%; background:var(--blue); cursor:pointer; box-shadow:0 1px 4px rgba(58,110,168,.4); }}
.tp-ticks {{ display:flex; justify-content:space-between; margin-top:5px; }}
.tp-tick {{ font-size:9px; color:var(--soft); text-align:center; flex:1; }}
.guild-popup {{ display:none; position:absolute; z-index:200; background:var(--dark); color:white; border-radius:12px; padding:14px 16px; max-width:252px; min-width:200px; box-shadow:0 8px 32px rgba(0,0,0,.3); pointer-events:none; }}
.guild-popup.visible {{ display:block; }}
.gp-title {{ font-weight:700; font-size:13px; margin-bottom:4px; }}
.gp-status {{ display:inline-block; font-size:8.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; padding:2px 8px; border-radius:20px; margin-bottom:8px; color:white; }}
.gp-gauge-wrap {{ margin-bottom:8px; }}
.gp-gauge-row {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:3px; }}
.gp-gauge-val {{ font-size:14px; font-weight:700; }}
.gp-gauge-target {{ font-size:9px; color:rgba(255,255,255,.5); }}
.gp-gauge-track {{ width:100%; height:7px; border-radius:4px; background:rgba(255,255,255,.12); position:relative; overflow:visible; }}
.gp-gauge-optimal {{ position:absolute; top:0; height:100%; border-radius:4px; background:rgba(46,139,110,.5); }}
.gp-gauge-fill {{ position:absolute; top:0; left:0; height:100%; border-radius:4px; transition:width .4s; }}
.gp-gauge-marker {{ position:absolute; top:-2px; width:2px; height:11px; border-radius:1px; background:rgba(255,255,255,.5); }}
.gp-gauge-range {{ display:flex; justify-content:space-between; font-size:8px; color:rgba(255,255,255,.35); margin-top:2px; }}
.gp-delta {{ font-size:10px; font-weight:700; padding:3px 9px; border-radius:6px; display:inline-block; margin-bottom:7px; }}
.gp-text {{ font-size:11px; color:rgba(255,255,255,.65); line-height:1.6; }}
.info-popup {{ display:none; position:absolute; z-index:300; background:var(--warm); color:var(--dark); border-radius:12px; padding:14px 16px; max-width:240px; min-width:190px; box-shadow:0 6px 24px rgba(0,0,0,.14); border:1px solid var(--rule); pointer-events:none; }}
.info-popup.visible {{ display:block; }}
.ip-title {{ font-weight:700; font-size:12px; margin-bottom:8px; color:var(--dark); }}
.ip-row {{ display:flex; gap:8px; margin-bottom:6px; align-items:flex-start; }}
.ip-row:last-child {{ margin-bottom:0; }}
.ip-tag {{ font-size:8.5px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; padding:2px 7px; border-radius:10px; white-space:nowrap; flex-shrink:0; margin-top:1px; }}
.ip-tag.high {{ background:var(--amber-lt); color:var(--amber); }}
.ip-tag.low {{ background:var(--blue-lt); color:var(--blue); }}
.ip-tag.ok {{ background:var(--green-lt); color:var(--green); }}
.ip-tag.inv {{ background:var(--red-lt); color:var(--red); }}
.ip-body {{ font-size:11px; color:var(--mid); line-height:1.55; }}
.qanswer-popup {{ display:none; position:absolute; z-index:400; background:var(--dark); color:white; border-radius:12px; padding:14px 16px; max-width:240px; min-width:180px; box-shadow:0 6px 24px rgba(0,0,0,.28); pointer-events:none; }}
.qanswer-popup.visible {{ display:block; }}
.qa-q {{ font-weight:700; font-size:12px; margin-bottom:6px; }}
.qa-a {{ font-size:11px; color:rgba(255,255,255,.72); line-height:1.6; }}
.qpill .qpill-expand, .qpill .qpill-label {{ transition:opacity .2s; }}
.qpill:hover .qpill-bg {{ opacity:0 !important; }}
.qpill:hover .qpill-expand {{ opacity:.92 !important; }}
.qpill:hover .qpill-icon {{ display:none; }}
.qpill:hover .qpill-label {{ opacity:1 !important; }}
.option-tabs {{ display:flex; gap:0; margin-bottom:0; border-radius:10px 10px 0 0; overflow:hidden; border:1px solid var(--rule); border-bottom:none; width:fit-content; }}
.opt-tab {{ padding:8px 20px; font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; cursor:pointer; background:var(--sand); color:var(--soft); border:none; transition:background .2s,color .2s; }}
.opt-tab.active {{ background:var(--warm); color:var(--dark); }}
.opt-panel {{ display:none; }}
.opt-panel.active {{ display:block; }}
.gbar.critical {{ background:var(--red-lt); border-color:rgba(194,75,58,.3); }}
.gbar.below {{ background:var(--blue-lt); border-color:rgba(58,110,168,.3); }}
.gbar.above {{ background:var(--amber-lt); border-color:rgba(201,124,42,.3); }}
.gbar.ok {{ background:var(--green-lt); border-color:rgba(46,139,110,.2); }}
.gbar-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
.gbar-name {{ font-size:13px; font-weight:600; }}
.gbar-badge {{ font-size:10px; font-weight:600; letter-spacing:.12em; text-transform:uppercase; padding:3px 10px; border-radius:20px; }}
.badge-critical {{ background:var(--red); color:white; }}
.badge-below {{ background:var(--blue); color:white; }}
.badge-above {{ background:var(--amber); color:white; }}
.badge-ok {{ background:var(--green); color:white; }}
.gbar-track {{ height:6px; background:rgba(0,0,0,.07); border-radius:3px; position:relative; margin-bottom:8px; }}
.gbar-range {{ position:absolute; height:100%; border-radius:3px; background:rgba(0,0,0,.12); }}
.gbar-fill {{ position:absolute; height:100%; border-radius:3px; transition:width 1.2s ease; }}
.fill-critical {{ background:var(--red); }}
.fill-below {{ background:var(--blue); }}
.fill-above {{ background:var(--amber); }}
.fill-ok {{ background:var(--green); }}
.gbar-note {{ font-size:12px; color:var(--mid); line-height:1.5; }}
.pathway-svg-wrap {{ position:relative; }}
.callout {{ padding:14px 18px; border-radius:10px; border-left:3px solid var(--blue); font-size:13px; line-height:1.7; color:var(--mid); }}

/* ── SECTION 3: FACTOR-FIRST layout ── */
/* Metrics strip */
.metrics-strip {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:32px; }}
.ms-card {{ flex:1; min-width:160px; background:var(--warm); border:1px solid var(--rule); border-radius:10px; padding:14px 16px; display:flex; gap:12px; align-items:center; }}
.ms-card.ms-low {{ background:var(--red-lt); border-color:rgba(194,75,58,.25); }}
.ms-card.ms-high {{ background:var(--amber-lt); border-color:rgba(201,124,42,.25); }}
.ms-card.ms-crit {{ background:var(--red-lt); border-color:rgba(194,75,58,.35); }}
.ms-icon {{ font-size:22px; flex-shrink:0; }}
.ms-meta {{ min-width:0; }}
.ms-label {{ font-size:11px; font-weight:600; color:var(--dark); }}
.ms-value {{ font-size:13px; font-weight:700; color:var(--red); margin:2px 0; }}
.ms-card.ms-high .ms-value {{ color:var(--amber); }}
.ms-range {{ font-size:10px; color:var(--soft); }}
/* Factor cards */
.fc-cards {{ display:flex; flex-direction:column; gap:14px; margin-bottom:36px; }}
.fc-card {{ background:var(--warm); border:1px solid var(--rule); border-radius:12px; overflow:hidden; animation:fadeUp .5s ease both; }}
.fc-card:nth-child(1){{animation-delay:.05s}} .fc-card:nth-child(2){{animation-delay:.1s}}
.fc-card:nth-child(3){{animation-delay:.15s}} .fc-card:nth-child(4){{animation-delay:.2s}}
.fc-header {{ display:flex; align-items:flex-start; gap:12px; padding:16px 18px 14px; }}
.fc-icon {{ font-size:24px; flex-shrink:0; margin-top:2px; }}
.fc-meta {{ flex:1; min-width:0; }}
.fc-label {{ font-size:14px; font-weight:700; color:var(--dark); line-height:1.3; }}
.fc-subtitle {{ font-size:11px; color:var(--soft); margin-top:2px; }}
.fc-ev-badge {{ font-size:9px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:white; padding:3px 9px; border-radius:20px; white-space:nowrap; flex-shrink:0; margin-top:3px; }}
.fc-guild-dots {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }}
.fc-dot {{ font-size:10px; font-weight:600; padding:2px 8px; border-radius:20px; white-space:nowrap; color:white; }}
.fc-dot-low {{ background:var(--red); }}
.fc-dot-high {{ background:var(--amber); }}
.fc-dot-crit {{ background:var(--red); }}
.fc-body {{ padding:4px 18px 16px; border-top:1px solid var(--rule); }}
.fc-text {{ font-size:13px; color:var(--mid); line-height:1.8; margin-top:12px; }}
.fc-scope {{ display:inline-block; font-size:10px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--soft); margin-top:10px; }}
/* Cascade diagram */
.cascade-section {{ margin-bottom:32px; }}
.cascade-label {{ font-size:10px; letter-spacing:.25em; text-transform:uppercase; color:var(--soft); margin-bottom:16px; display:flex; align-items:center; gap:10px; }}
.cascade-label::after {{ content:''; flex:1; height:1px; background:var(--rule); }}
.cascade-diag {{ display:flex; gap:0; align-items:stretch; }}
.casc-left {{ display:flex; flex-direction:column; gap:8px; min-width:180px; padding-right:16px; }}
.casc-right {{ display:flex; flex-direction:column; gap:8px; flex:1; padding-left:16px; border-left:1px solid var(--rule); }}
.casc-arrow {{ display:flex; align-items:center; padding:0 8px; color:var(--soft); font-size:20px; align-self:center; }}
.casc-factor-pill {{ background:var(--sand); border:1px solid var(--rule); border-radius:8px; padding:8px 12px; display:flex; align-items:center; gap:8px; font-size:12px; font-weight:600; color:var(--mid); }}
.casc-guild-pill {{ border-radius:8px; padding:8px 12px; display:flex; align-items:center; justify-content:space-between; gap:8px; }}
.casc-guild-pill.gp-low {{ background:var(--red-lt); border:1px solid rgba(194,75,58,.2); }}
.casc-guild-pill.gp-high {{ background:var(--amber-lt); border:1px solid rgba(201,124,42,.2); }}
.casc-guild-pill.gp-crit {{ background:var(--red-lt); border:1px solid rgba(194,75,58,.3); }}
.cgp-label {{ font-size:12px; font-weight:600; color:var(--dark); }}
.cgp-value {{ font-size:11px; color:var(--soft); }}
.cgp-emojis {{ font-size:14px; }}
/* Goal card — inferred tag */
.goal-inferred-tag {{ display:inline-block; font-size:9px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--purple); background:var(--purple-lt); border:1px solid rgba(107,94,168,.2); border-radius:20px; padding:2px 9px; margin-bottom:10px; }}
</style>
</head>
<body>
''')

    # ════════════════════ COVER ════════════════════
    pillar_label_map = {
        'health_association': 'Health',
        'diversity_resilience': 'Diversity',
        'metabolic_function': 'Metabolic',
        'guild_balance': 'Guild Balance',
        'safety_profile': 'Safety',
    }

    parts.append(f'''
<div class="cover">
  <div class="cover-blob1"></div>
  <div class="cover-blob2"></div>

  <div class="cover-top">
    <div class="cover-brand">NB1 Health · Microbiome Health</div>
    <div class="cover-tag">Personalised Report · {_esc(report_date_display)}</div>
  </div>

  <div class="cover-mid">
    <div class="cover-eyebrow">Your gut health, explained simply</div>
    <h1 class="cover-h1">Inside<br>Your <span>Gut</span></h1>
    <p class="cover-sub">What your microbiome is telling us — in plain language. What's working, what needs support, and exactly what we're doing about it.</p>

    <div class="score-row">
      <div class="score-dial">
        <svg width="120" height="120" viewBox="0 0 120 120">
          <circle class="dial-bg" cx="60" cy="60" r="45"/>
          <circle class="dial-fill" cx="60" cy="60" r="45"
            stroke="{score_color}"
            style="stroke-dasharray:283; stroke-dashoffset:{score_offset:.1f};"/>
        </svg>
        <div class="score-label">
          <span class="score-num">{total_score}</span>
          <span class="score-den">/ 100</span>
        </div>
      </div>
      <div class="score-info-text">
        {score_summary}{'<br><span style="font-size:12px;opacity:.7;margin-top:4px;display:block">' + _esc(key_note) + '</span>' if key_note else ''}
      </div>
    </div>

    <div class="pillar-row">''')

    for pk, pv in pillars.items():
        plabel = pillar_label_map.get(pk, pk.replace('_', ' ').title())
        pct = _pillar_pct(pv.get('score', 0), pv.get('max', 20))
        pcolor = _score_color(pct)
        parts.append(f'''
      <div class="pillar-chip">
        <div class="pc-name">{_esc(plabel)}</div>
        <div><span class="pc-score">{pv.get("score", 0)}</span><span class="pc-max"> / {pv.get("max", 20)}</span></div>
        <div class="pc-bar"><div class="pc-bar-fill" style="width:{pct}%;background:{pcolor}"></div></div>
      </div>''')

    parts.append('</div>\n  </div>\n')

    # Profile snapshot
    parts.append('  <div class="cover-bottom">\n')
    if profile.get('sex') and profile.get('age'):
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Profile</div><div class="cs-val">{_esc(profile["sex"])} · {profile["age"]}</div></div>\n')
    if profile.get('diet'):
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Diet</div><div class="cs-val">{_esc(profile["diet"])}</div></div>\n')
    if profile.get('stress') is not None:
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Stress</div><div class="cs-val">{profile["stress"]} / 10</div></div>\n')
    if profile.get('sleep') is not None:
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Sleep</div><div class="cs-val">{profile["sleep"]} / 10</div></div>\n')
    if profile.get('sensitivity'):
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Sensitivity</div><div class="cs-val">{_esc(profile["sensitivity"])}</div></div>\n')
    if profile.get('goals'):
        goals_str = ' · '.join(profile['goals'])
        parts.append(f'    <div class="cover-stat"><div class="cs-label">Goals</div><div class="cs-val" style="max-width:260px">{_esc(goals_str)}</div></div>\n')
    parts.append('  </div>\n')

    # ── Cover progress banner ──
    parts.append('''
  <!-- PROGRESS BANNER -->
  <div style="margin-top:32px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;position:relative;z-index:1;">
    <div>
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:4px;">Your progress so far</div>
      <div style="font-family:\'Playfair Display\',serif;font-size:22px;color:white;line-height:1.1;" id="banner-tp-label">—</div>
      <div style="font-size:12px;color:rgba(255,255,255,.45);margin-top:3px;" id="banner-delta-text"></div>
      <div style="margin-top:10px;width:180px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
          <span style="font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:rgba(255,255,255,.35);">Microbiome score</span>
          <span id="banner-score-num" style="font-family:\'Playfair Display\',serif;font-size:18px;color:white;"></span>
        </div>
        <div style="height:5px;background:rgba(255,255,255,.1);border-radius:3px;overflow:hidden;">
          <div id="banner-score-bar" style="height:100%;border-radius:3px;transition:width .7s cubic-bezier(.4,0,.2,1),background .4s;"></div>
        </div>
      </div>
    </div>
    <div style="flex:1;min-width:200px;max-width:360px;">
      <input class="tp-range" id="banner-tp-slider" type="range" min="0" max="0" value="0" step="1" style="width:100%;accent-color:rgba(255,255,255,.6);">
      <div class="tp-ticks" id="banner-tp-ticks" style="margin-top:4px;"></div>
    </div>
  </div>
''')

    parts.append('</div>\n')

    # ════════════════════ SECTION 1: WHAT IS HAPPENING ════════════════════
    parts.append('''
<div class="section sand">
  <div class="sec-label">Section 1 · The Big Picture</div>
  <h2 class="sec-title">What is happening in your gut?</h2>
  <p class="sec-intro">Your gut is home to trillions of bacteria organised into functional groups — each with a different role. These four indicators summarise the key areas of your gut health right now.</p>

  <div class="health-dials">
''')

    dial_config = [
        ('gut_lining', 'Gut Lining\nProtection', 'How well your gut wall is protected and maintained'),
        ('inflammation', 'Inflammation\nControl', 'How favorable your microbiome is for keeping inflammatory pressure low'),
        ('fiber_processing', 'Fiber\nProcessing', 'How efficiently your bacteria ferment and process fiber'),
        ('bifidobacteria', 'Bifidobacteria\nPresence', 'The abundance of your beneficial Bifidobacteria'),
    ]

    for key, label, desc in dial_config:
        score = circle_scores.get(key, 0)
        color = _score_color(score)
        offset = _dial_dashoffset(score, 232)
        label_lines = label.split('\n')
        parts.append(f'''    <div class="h-dial-card">
      <div class="h-dial-wrap">
        <svg width="88" height="88" viewBox="0 0 88 88">
          <circle class="hd-bg" cx="44" cy="44" r="37" transform="rotate(-90 44 44)"/>
          <circle class="hd-fill" cx="44" cy="44" r="37"
            stroke="{color}"
            style="stroke-dashoffset:{offset:.1f};"
            transform="rotate(-90 44 44)"/>
        </svg>
        <div class="hd-label"><span class="hd-pct" style="color:{color}">{score}%</span></div>
      </div>
      <div class="hd-name">{"<br>".join(_esc(l) for l in label_lines)}</div>
      <div class="hd-desc">{_esc(desc)}</div>
    </div>
''')

    parts.append('  </div>\n\n')

    # Color legend + guild bars

    # Map guild names to JS keys for dynamic bars
    _GUILD_JS_KEY = {
        'fiber': 'fd', 'butyrate': 'bp', 'cross': 'cf',
        'bifido': 'bb', 'hmo': 'bb', 'oligosaccharide': 'bb',
        'mucin': 'md', 'proteolytic': 'pg',
    }
    def _js_key(gname):
        for kw, jk in _GUILD_JS_KEY.items():
            if kw in gname.lower():
                return jk
        return ''

    # ── Evolution-over-time slider ──
    parts.append('''
  <div style="background:var(--dark);border-radius:14px;padding:14px 20px;margin-bottom:20px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <span style="font-size:9px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:rgba(255,255,255,.4);">Evolution over time</span>
      <span id="hr-tp-badge" style="font-size:11px;font-weight:700;background:rgba(255,255,255,.1);color:rgba(255,255,255,.7);padding:2px 12px;border-radius:20px;">—</span>
    </div>
    <input class="tp-range" type="range" id="hr-tp-slider" min="0" max="0" value="0" step="1" style="accent-color:rgba(255,255,255,.6);">
    <div class="tp-ticks" id="hr-tp-ticks" style="margin-top:5px;"></div>
  </div>

  <p style="font-size:13px;color:var(--mid);margin-bottom:24px">
    Each bacterial group has a healthy target range. Your level is shown against that range using a colour-coded bar.
    <strong style="color:var(--green)">Green</strong> = within healthy range &middot;
    <strong style="color:var(--blue)">Blue</strong> = too low &middot;
    <strong style="color:var(--amber)">Amber</strong> = too high &middot;
    <strong style="color:var(--red)">Red</strong> = critically out of range.
    For protective bacteria, low is the concern. For opportunistic bacteria, high is the concern.
    Hover over any node in the diagram to see your detailed gauge and progress.
  </p>

  <div class="guild-intro">
    <div style="margin-bottom:-1px;">
      <div class="option-tabs">
        <button class="opt-tab active" onclick="switchTab(1)">Option 1 — Pathway schema</button>
        <button class="opt-tab" onclick="switchTab(2)">Option 2 — Bacterial ranges</button>
      </div>
    </div>
    <div id="opt-panel-1" class="opt-panel active" style="border:1px solid var(--rule);border-radius:0 10px 10px 10px;padding:16px;">
    <div class="guild-explainer">
      <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin-bottom:10px;font-weight:600;">How your gut bacteria work together</div>
      <div class="pathway-svg-wrap" id="pathway-wrap">

        <div class="info-popup" id="info-popup">
          <div class="ip-title" id="ip-title"></div>
          <div id="ip-content"></div>
        </div>
        <div class="qanswer-popup" id="qanswer-popup">
          <div class="qa-q" id="qa-q"></div>
          <div class="qa-a" id="qa-a"></div>
        </div>
        <div class="guild-popup" id="guild-tooltip">
          <div class="gp-title" id="gp-title"></div>
          <div style="display:flex;align-items:center;gap:7px;margin-bottom:8px;flex-wrap:wrap;">
            <span class="gp-status" id="gp-status"></span>
            <span class="gp-delta" id="gp-delta" style="display:none"></span>
          </div>
          <div class="gp-gauge-wrap">
            <div class="gp-gauge-row">
              <span class="gp-gauge-val" id="gp-val"></span>
              <span class="gp-gauge-target" id="gp-target"></span>
            </div>
            <div class="gp-gauge-track">
              <div class="gp-gauge-optimal" id="gp-optimal"></div>
              <div class="gp-gauge-fill" id="gp-gfill"></div>
              <div class="gp-gauge-marker" id="gp-marker"></div>
            </div>
            <div class="gp-gauge-range"><span>0%</span><span id="gp-range-end"></span></div>
          </div>
          <div class="gp-text" id="gp-text"></div>
        </div>

        <svg viewBox="0 0 640 420" xmlns="http://www.w3.org/2000/svg" id="pathway-svg">
          <defs>
            <marker id="ah-green" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0,7 3.5,0 7" fill="#2E8B6E"/></marker>
            <marker id="ah-amber" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0,7 3.5,0 7" fill="#C97C2A"/></marker>
            <marker id="ah-blue"  markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0,7 3.5,0 7" fill="#3A6EA8"/></marker>
            <marker id="ah-red"   markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0,7 3.5,0 7" fill="#C24B3A"/></marker>
            <marker id="ah-gray"  markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><polygon points="0 0,7 3.5,0 7" fill="#C8CBCF"/></marker>
          </defs>

          <rect x="0" y="0" width="640" height="12" fill="#F0EDE6"/>
          <text x="10" y="9" font-size="7" fill="#9A95A8" font-family="Nunito,sans-serif" font-weight="700" letter-spacing="1.5">FIBER PATHWAY &middot; left to right</text>

          <g class="pnode" data-guild="diet-fiber">
            <rect x="8" y="65" width="88" height="56" rx="9" fill="#EAF4EF" stroke="#2E8B6E" stroke-width="1.5"/>
            <text x="52" y="85" text-anchor="middle" font-size="10" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F966; Dietary</text>
            <text x="52" y="98" text-anchor="middle" font-size="9" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Fiber</text>
            <text x="52" y="110" text-anchor="middle" font-size="7" fill="#2E8B6E" font-family="Nunito,sans-serif">Complex sugars</text>
          </g>
          <line x1="96" y1="92" x2="108" y2="92" stroke="#3A6EA8" stroke-width="2" marker-end="url(#ah-blue)"/>
          <text x="100" y="88" text-anchor="middle" font-size="6" fill="#9A95A8" font-family="Nunito,sans-serif" font-style="italic">complex carbs</text>

          <!-- Simple Sugars source node -->
          <g class="pnode" data-guild="diet-sugars">
            <rect x="8" y="15" width="78" height="40" rx="8" fill="#FFF8EE" stroke="#C97C2A" stroke-width="1" stroke-dasharray="3,2"/>
            <text x="47" y="31" text-anchor="middle" font-size="9" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F36C; Simple</text>
            <text x="47" y="43" text-anchor="middle" font-size="8" fill="#1E1E2A" font-family="Nunito,sans-serif">Sugars</text>
          </g>
          <!-- Simple sugars → Bifidobacteria -->
          <path d="M86,35 C140,35 180,35 224,40" stroke="#C97C2A" stroke-width="1.2" fill="none" stroke-dasharray="4,2" marker-end="url(#ah-amber)"/>
          <text x="148" y="30" text-anchor="middle" font-size="5.5" fill="#C97C2A" font-family="Nunito,sans-serif" font-style="italic">simple sugars</text>
          <!-- Simple sugars → Cross-Feeders -->
          <path d="M86,45 C140,60 180,100 224,125" stroke="#C97C2A" stroke-width="1.2" fill="none" stroke-dasharray="4,2" marker-end="url(#ah-amber)"/>
          <text x="140" y="85" text-anchor="middle" font-size="5.5" fill="#C97C2A" font-family="Nunito,sans-serif" font-style="italic">simple sugars</text>
          <g class="pnode" data-guild="fd">
            <rect id="nr-fd" x="110" y="65" width="88" height="56" rx="9" fill="#EAF0F8" stroke="#3A6EA8" stroke-width="1.5"/>
            <text x="154" y="85" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F33E; Fiber</text>
            <text x="154" y="98" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Degraders</text>
            <text id="ns-fd" x="154" y="110" text-anchor="middle" font-size="7.5" fill="#3A6EA8" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="fd" style="cursor:pointer;"><circle cx="192" cy="66" r="7" fill="#3A6EA8" opacity=".85"/><text x="192" y="70" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>
          <line id="arr-fd-bb" x1="200" y1="93" x2="222" y2="46" stroke="#3A6EA8" stroke-width="2" marker-end="url(#ah-blue)"/>
          <line id="arr-fd-cf" x1="200" y1="93" x2="222" y2="136" stroke="#3A6EA8" stroke-width="2" marker-end="url(#ah-blue)"/>
          <rect id="bb-bottleneck-pill" x="340" y="14" width="116" height="14" rx="5" fill="#9A95A8" display="none"/>
          <text id="bb-bottleneck-text" x="398" y="24" text-anchor="middle" font-size="7.5" fill="white" font-family="Nunito,sans-serif" font-weight="700" display="none">&#x23F8; Substrate limited</text>

          <g class="pnode" data-guild="bb">
            <rect id="nr-bb" x="224" y="18" width="88" height="56" rx="9" fill="#FBF1E4" stroke="#C97C2A" stroke-width="1.5"/>
            <text x="268" y="37" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x2728; Bifido-</text>
            <text x="268" y="50" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">bacteria</text>
            <text id="ns-bb" x="268" y="65" text-anchor="middle" font-size="7.5" fill="#C97C2A" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="bb" style="cursor:pointer;"><circle cx="306" cy="19" r="7" fill="#C97C2A" opacity=".85"/><text x="306" y="23" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>
          <g class="pnode" data-guild="cf">
            <rect id="nr-cf" x="224" y="108" width="88" height="56" rx="9" fill="#FBF1E4" stroke="#C97C2A" stroke-width="1.5"/>
            <text x="268" y="127" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F517; Cross-</text>
            <text x="268" y="140" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Feeders</text>
            <text id="ns-cf" x="268" y="155" text-anchor="middle" font-size="7.5" fill="#C97C2A" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="cf" style="cursor:pointer;"><circle cx="306" cy="109" r="7" fill="#C97C2A" opacity=".85"/><text x="306" y="113" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>
          <line id="arr-bb" x1="312" y1="46" x2="362" y2="74" stroke="#C97C2A" stroke-width="2" marker-end="url(#ah-amber)"/>
          <line id="arr-cf" x1="312" y1="136" x2="362" y2="86" stroke="#C97C2A" stroke-width="2" marker-end="url(#ah-amber)"/>
          <g class="pnode" data-guild="bp">
            <rect id="nr-bp" x="364" y="52" width="88" height="56" rx="9" fill="#E8F5F1" stroke="#2E8B6E" stroke-width="1.5"/>
            <text x="408" y="72" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x26A1; Butyrate</text>
            <text x="408" y="85" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Producers</text>
            <text id="ns-bp" x="408" y="99" text-anchor="middle" font-size="7.5" fill="#2E8B6E" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="bp" style="cursor:pointer;"><circle cx="448" cy="53" r="7" fill="#2E8B6E" opacity=".85"/><text x="448" y="57" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>
          <line id="arr-bp" x1="452" y1="80" x2="542" y2="80" stroke="#2E8B6E" stroke-width="2" marker-end="url(#ah-green)"/>
          <rect x="460" y="60" width="74" height="13" rx="5" fill="#2E8B6E"/>
          <text x="497" y="68" text-anchor="middle" font-size="7.5" fill="white" font-family="Nunito,sans-serif" font-weight="700">Butyrate &rarr;</text>
          <g class="pnode" data-guild="out-bp">
            <rect id="nr-out-bp" x="544" y="52" width="88" height="56" rx="9" fill="#E8F5F1" stroke="#2E8B6E" stroke-width="1.5"/>
            <text x="588" y="70" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F3E0; SCFA</text>
            <text x="588" y="83" text-anchor="middle" font-size="8.5" fill="#4A4858" font-family="Nunito,sans-serif">Colon Fuel</text>
            <text id="ns-out-bp" x="588" y="99" text-anchor="middle" font-size="7.5" fill="#2E8B6E" font-family="Nunito,sans-serif">loading...</text>
          </g>

          <rect x="0" y="190" width="640" height="12" fill="#F0EDE6"/>
          <text x="10" y="199" font-size="7" fill="#9A95A8" font-family="Nunito,sans-serif" font-weight="700" letter-spacing="1.5">PROTEIN PATHWAY &middot; independent</text>
          <g class="pnode" data-guild="diet-protein">
            <rect x="8" y="210" width="88" height="56" rx="9" fill="#F0EEF8" stroke="#6B5EA8" stroke-width="1.5"/>
            <text x="52" y="230" text-anchor="middle" font-size="10" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F969; Dietary</text>
            <text x="52" y="243" text-anchor="middle" font-size="9" fill="#4A4858" font-family="Nunito,sans-serif">Protein</text>
            <text x="52" y="257" text-anchor="middle" font-size="7" fill="#6B5EA8" font-family="Nunito,sans-serif">undigested</text>
          </g>
          <path id="arr-prot" d="M96,238 C170,238 170,238 222,238" stroke="#6B5EA8" stroke-width="2" fill="none" marker-end="url(#ah-blue)"/>
          <text x="158" y="232" text-anchor="middle" font-size="5.5" fill="#6B5EA8" font-family="Nunito,sans-serif" font-style="italic">amino acids &amp; peptides</text>

          <!-- Fat source node -->
          <g class="pnode" data-guild="diet-fat">
            <rect x="120" y="268" width="78" height="40" rx="8" fill="#FFF8EE" stroke="#6B5EA8" stroke-width="1" stroke-dasharray="3,2"/>
            <text x="159" y="284" text-anchor="middle" font-size="9" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F9C8; Fat</text>
            <text x="159" y="298" text-anchor="middle" font-size="7" fill="#6B5EA8" font-family="Nunito,sans-serif">dietary lipids</text>
          </g>
          <!-- Fat → Protein Fermenters -->
          <path d="M198,280 C210,265 215,250 224,243" stroke="#6B5EA8" stroke-width="1.2" fill="none" stroke-dasharray="4,2" marker-end="url(#ah-blue)"/>
          <text x="220" y="272" text-anchor="middle" font-size="5.5" fill="#6B5EA8" font-family="Nunito,sans-serif" font-style="italic">bile acids</text>
          <g class="pnode" data-guild="pg">
            <rect id="nr-pg" x="224" y="210" width="88" height="56" rx="9" fill="#E8F5F1" stroke="#2E8B6E" stroke-width="1.5"/>
            <text x="268" y="230" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F9EB; Protein</text>
            <text x="268" y="243" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Fermenters</text>
            <text id="ns-pg" x="268" y="257" text-anchor="middle" font-size="7.5" fill="#2E8B6E" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="pg" style="cursor:pointer;"><circle cx="306" cy="211" r="7" fill="#2E8B6E" opacity=".85"/><text x="306" y="215" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>
          <line id="arr-pg" x1="312" y1="238" x2="542" y2="238" stroke="#2E8B6E" stroke-width="2" stroke-dasharray="5,3" marker-end="url(#ah-green)"/>
          <g class="pnode" data-guild="out-pg">
            <rect id="nr-out-pg" x="544" y="210" width="88" height="56" rx="9" fill="#E8F5F1" stroke="#2E8B6E" stroke-width="1.5"/>
            <text x="588" y="230" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x2601;&#xFE0F; Protein</text>
            <text x="588" y="243" text-anchor="middle" font-size="9" fill="#4A4858" font-family="Nunito,sans-serif">Byproducts</text>
            <text id="ns-out-pg" x="588" y="257" text-anchor="middle" font-size="7.5" fill="#2E8B6E" font-family="Nunito,sans-serif">loading...</text>
          </g>

          <rect x="0" y="306" width="640" height="12" fill="#F0EDE6"/>
          <text x="10" y="315" font-size="7" fill="#9A95A8" font-family="Nunito,sans-serif" font-weight="700" letter-spacing="1.5">MUCUS PATHWAY &middot; homeostatic role</text>

          <!-- Mucus-Layer Bacteria guild (under Protein Fermenters) -->
          <g class="pnode" data-guild="md">
            <rect id="nr-md" x="224" y="326" width="88" height="56" rx="9" fill="#EEF0F8" stroke="#6B7EA8" stroke-width="1.5"/>
            <text x="268" y="345" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F504; Mucus</text>
            <text x="268" y="358" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">Layer Bacteria</text>
            <text id="ns-md" x="268" y="372" text-anchor="middle" font-size="7.5" fill="#6B7EA8" font-family="Nunito,sans-serif">loading...</text>
            <g class="info-btn" data-info="md" style="cursor:pointer;"><circle cx="306" cy="327" r="7" fill="#6B7EA8" opacity=".85"/><text x="306" y="331" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">i</text></g>
          </g>

          <!-- Mucus Layer source (under Protein Byproducts) -->
          <g class="pnode" data-guild="out-md">
            <rect id="nr-out-md" x="544" y="326" width="88" height="56" rx="9" fill="#EEF0F8" stroke="#6B7EA8" stroke-width="1" stroke-dasharray="3,2"/>
            <text x="588" y="345" text-anchor="middle" font-size="9.5" fill="#1E1E2A" font-family="Nunito,sans-serif" font-weight="700">&#x1F6E1;&#xFE0F; Mucus</text>
            <text x="588" y="358" text-anchor="middle" font-size="9" fill="#4A4858" font-family="Nunito,sans-serif">Layer</text>
            <text id="ns-out-md" x="588" y="372" text-anchor="middle" font-size="7.5" fill="#6B7EA8" font-family="Nunito,sans-serif">loading...</text>
          </g>

          <!-- Mucus Layer → Bacteria: "mucus glycans" (substrate arrow) -->
          <line id="arr-md" x1="544" y1="349" x2="314" y2="349" stroke="#6B7EA8" stroke-width="2" stroke-dasharray="5,3" marker-end="url(#ah-blue)"/>
          <text x="429" y="342" text-anchor="middle" font-size="6" fill="#6B7EA8" font-family="Nunito,sans-serif" font-style="italic">mucus glycans</text>

          <!-- Bacteria → Mucus Layer: degradation feedback (return arrow) -->
          <line x1="312" y1="361" x2="542" y2="361" stroke="#6B7EA8" stroke-width="1.2" stroke-dasharray="3,2" marker-end="url(#ah-blue)"/>
          <text x="429" y="375" text-anchor="middle" font-size="5.5" fill="#9A95A8" font-family="Nunito,sans-serif" font-style="italic">degradation</text>

          <!-- Question pills -->
          <g id="qpill-fd" class="qpill" data-q="fd" style="cursor:pointer;display:none;">
            <rect class="qpill-bg" x="93" y="83" width="18" height="18" rx="9" fill="#3A6EA8" opacity=".9"/>
            <rect class="qpill-expand" x="93" y="83" width="148" height="18" rx="9" fill="#3A6EA8" opacity="0"/>
            <text class="qpill-icon" x="102" y="95" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="172" y="95" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">Why is fiber important?</text>
          </g>
          <g id="qpill-substrate" class="qpill" data-q="substrate" style="cursor:pointer;display:none;">
            <rect class="qpill-bg" x="460" y="14" width="18" height="14" rx="7" fill="#3A6EA8" opacity=".9"/>
            <rect class="qpill-expand" x="350" y="14" width="131" height="14" rx="7" fill="#3A6EA8" opacity="0"/>
            <text class="qpill-icon" x="469" y="24" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="417" y="24" text-anchor="middle" font-size="7.5" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">What is substrate limitation?</text>
          </g>
          <g id="qpill-bp" class="qpill" data-q="bp" style="cursor:pointer;display:none;">
            <rect class="qpill-bg" x="493" y="46" width="18" height="18" rx="9" fill="#2E8B6E" opacity=".9"/>
            <rect class="qpill-expand" x="390" y="46" width="150" height="18" rx="9" fill="#2E8B6E" opacity="0"/>
            <text class="qpill-icon" x="502" y="58" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="466" y="58" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">What does butyrate do?</text>
          </g>
          <g id="qpill-scfa" class="qpill" data-q="scfa" style="cursor:pointer;">
            <rect class="qpill-bg" x="624" y="52" width="16" height="16" rx="8" fill="#2E8B6E" opacity=".85"/>
            <rect class="qpill-expand" x="490" y="52" width="148" height="16" rx="8" fill="#2E8B6E" opacity="0"/>
            <text class="qpill-icon" x="632" y="63" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="565" y="63" text-anchor="middle" font-size="7.5" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">What are SCFAs?</text>
          </g>
          <g id="qpill-pg" class="qpill" data-q="pg" style="cursor:pointer;display:none;">
            <rect class="qpill-bg" x="420" y="226" width="18" height="18" rx="9" fill="#C24B3A" opacity=".9"/>
            <rect class="qpill-expand" x="280" y="226" width="190" height="18" rx="9" fill="#C24B3A" opacity="0"/>
            <text class="qpill-icon" x="429" y="238" text-anchor="middle" font-size="9" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="377" y="238" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">Why is too much protein bad?</text>
          </g>
          <g id="qpill-md" class="qpill" data-q="md" style="cursor:pointer;">
            <rect class="qpill-bg" x="624" y="326" width="16" height="16" rx="8" fill="#6B7EA8" opacity=".85"/>
            <rect class="qpill-expand" x="490" y="326" width="150" height="16" rx="8" fill="#6B7EA8" opacity="0"/>
            <text class="qpill-icon" x="632" y="337" text-anchor="middle" font-size="8" fill="white" font-family="Nunito,sans-serif" font-weight="700">?</text>
            <text class="qpill-label" x="565" y="337" text-anchor="middle" font-size="7.5" fill="white" font-family="Nunito,sans-serif" font-weight="700" opacity="0">What is the mucus layer?</text>
          </g>
        </svg>
      </div>
      <div id="dynamic-callout" class="callout" style="margin-top:12px"></div>
    </div>
    </div><!-- /opt-panel-1 -->

    <div id="opt-panel-2" class="opt-panel" style="border:1px solid var(--rule);border-radius:0 10px 10px 10px;padding:16px;">
    <div class="guild-bars">
''')

    for g in guilds_display:
        jk = _js_key(g['name'])
        fill_class = {'critical': 'fill-critical', 'below': 'fill-below', 'above': 'fill-above', 'ok': 'fill-ok'}.get(g['bar_class'], 'fill-ok')
        id_attr = f' id="gbar-{jk}"' if jk else ''
        badge_id = f' id="gbadge-{jk}"' if jk else ''
        fill_id = f' id="gfill-{jk}"' if jk else ''
        parts.append(f'''      <div class="gbar {g["bar_class"]}"{id_attr}>
        <div class="gbar-top">
          <span class="gbar-name">{_esc(g["name"])}</span>
          <span class="gbar-badge {g["badge_class"]}"{badge_id}>{_esc(g["badge_text"])}</span>
        </div>
        <div class="gbar-track">
          <div class="gbar-range" style="left:{g["range_left_pct"]}%;width:{g["range_width_pct"]}%"></div>
          <div class="gbar-fill {fill_class}"{fill_id} style="width:{g["fill_width_pct"]}%"></div>
        </div>
        <div class="gbar-note">{_esc(g["note"])}</div>
      </div>
''')

    parts.append('''    </div><!-- /guild-bars -->
    </div><!-- /opt-panel-2 -->
  </div><!-- /guild-intro -->
</div>
''')

    # ════════════════════ SECTION 2: STRENGTHS & CHALLENGES ════════════════════
    strengths = sw.get('strengths', [])
    challenges = sw.get('challenges', [])

    # Build reassuring bottom line based on pattern
    if not challenges:
        bottom_line = "Your gut is in excellent shape across all key areas. Your protocol is about maintaining and optimising these gains."
    elif len(challenges) <= 2:
        bottom_line = "The main issue is not total collapse — it is imbalance in a few high-impact areas. Your gut still has strong foundations to build on."
    else:
        bottom_line = "Despite these imbalances, the pattern is addressable. Your protocol targets each of these areas specifically and methodically."

    parts.append(f'''
<div class="section">
  <div class="sec-label">Section 2 · Your Profile</div>
  <h2 class="sec-title">Your strengths and areas to improve</h2>
  <p class="sec-intro">Every microbiome tells a story. Here is what yours says about what is working well — and what needs targeted support.</p>

  <div class="sw-grid">

    <div class="sw-card strengths">
      <div class="sw-card-head">
        <div class="sw-icon icon-green">✦</div>
        <div>
          <div class="sw-head-label">Working in your favour</div>
          <div class="sw-head-title">Your Strengths</div>
        </div>
      </div>
      <div class="sw-items">
''')

    if strengths:
        for s in strengths:
            parts.append(f'''        <div class="sw-item">
          <div class="sw-dot dot-green"></div>
          <div class="sw-text">{_esc(s.get("icon", ""))} <strong>{_esc(s.get("title", ""))}</strong> — {_esc(s.get("text", ""))}</div>
        </div>
''')
    else:
        parts.append('<div class="sw-text" style="color:var(--soft);font-style:italic">No clear strengths identified — your protocol will build these from the foundation up.</div>\n')

    parts.append(f'''      </div>
    </div>

    <div class="sw-card weaknesses">
      <div class="sw-card-head">
        <div class="sw-icon icon-red">!</div>
        <div>
          <div class="sw-head-label">Needs attention</div>
          <div class="sw-head-title">Key Challenges</div>
        </div>
      </div>
      <div class="sw-items">
''')

    SEVERITY_DOT = {'critical': 'dot-red', 'high': 'dot-red', 'moderate': 'dot-amber'}
    if challenges:
        for c in challenges:
            dot = SEVERITY_DOT.get(c.get('severity', 'moderate'), 'dot-amber')
            parts.append(f'''        <div class="sw-item">
          <div class="sw-dot {dot}"></div>
          <div class="sw-text">{_esc(c.get("icon", ""))} <strong>{_esc(c.get("title", ""))}</strong> — {_esc(c.get("text", ""))}</div>
        </div>
''')
    else:
        parts.append('<div class="sw-text" style="color:var(--soft);font-style:italic">No significant challenges identified — your microbiome is in good balance.</div>\n')

    parts.append(f'''      </div>
    </div>

  </div>

  <div class="sw-bottom">
    <em>"{_esc(bottom_line)}"</em>
  </div>
</div>
''')

    # ════════════════════ SECTION 3: ROOT CAUSES ════════════════════
    root_cause_data = data.get('root_cause_data', {'deviation_cards': [], 'awareness_chips': []})
    deviation_cards = root_cause_data.get('deviation_cards', [])
    awareness_chips = root_cause_data.get('awareness_chips', [])

    # Evidence label → badge colour
    EVID_BADGE_COLOR = {
        'Well established': '#1E1E2A',
        'Research supported': '#3A6EA8',
        'Emerging research': '#C97C2A',
    }

    parts.append('''
<div class="section sand">
  <div class="sec-label">Section 3 · The Story Behind Your Results</div>
  <h2 class="sec-title">What is behind this imbalance?</h2>
  <p class="sec-intro">We looked at everything you shared with us — your microbiome, your health history, your lifestyle — and asked: why might this person have this particular pattern? Here is what we found.</p>
''')

    has_any = bool(deviation_cards or awareness_chips)

    # ── Factor-first layout (new Section 3 design) ────────────────────────────
    factor_cards = root_cause_data.get('factor_cards', [])
    metrics_strip = root_cause_data.get('metrics_strip', [])
    cascade_guilds = root_cause_data.get('cascade_guilds', [])
    section_summary_html = root_cause_data.get('section_summary', '')

    def _ev_label_css(evidence_label: str) -> str:
        if evidence_label == 'Well established': return 'ev-label-established'
        if evidence_label == 'Research supported': return 'ev-label-supported'
        return 'ev-label-emerging'

    if factor_cards:
        # ── Section summary ────────────────────────────────────────────────
        if section_summary_html:
            parts.append(f'''  <div style="background:var(--green-lt);border-left:3px solid var(--green);border-radius:0 10px 10px 0;padding:16px 22px;margin-bottom:32px;font-size:14px;color:var(--mid);line-height:1.8;">
    {_esc(section_summary_html)}
  </div>
''')
        # ── Metrics strip ───────────────────────────────────────────────────
        if metrics_strip:
            parts.append('  <div class="metrics-strip">\n')
            for ms in metrics_strip:
                ms_cls = {'low': 'ms-low', 'high': 'ms-high', 'crit': 'ms-crit'}.get(ms.get('impact', 'low'), 'ms-low')
                parts.append(f'''    <div class="ms-card {ms_cls}">
      <span class="ms-icon">{ms.get("icon", "🔬")}</span>
      <div class="ms-meta">
        <div class="ms-label">{_esc(ms.get("client_label", ""))}</div>
        <div class="ms-value">{_esc(ms.get("value_str", ""))}</div>
        <div class="ms-range">Target: {_esc(ms.get("range_str", ""))}</div>
      </div>
    </div>
''')
            parts.append('  </div>\n')

        # ── Factor cards ────────────────────────────────────────────────────
        parts.append('  <div class="fc-cards">\n')
        for fc in factor_cards:
            ev_css = _ev_label_css(fc.get('evidence_label', 'Emerging research'))
            guild_impacts = fc.get('guild_impacts', [])
            explanation = fc.get('explanation', '')
            n_guilds = fc.get('guilds_affected_count', len(guild_impacts))
            parts.append(f'''    <div class="fc-card">
      <div class="fc-header">
        <span class="fc-icon">{fc.get("icon", "🔍")}</span>
        <div class="fc-meta">
          <div class="fc-label">{_esc(fc.get("label", ""))}</div>
          <div class="fc-subtitle">{_esc(fc.get("subtitle", ""))}</div>
          <div class="fc-guild-dots">
''')
            for gi in guild_impacts:
                dot_cls = {'low': 'fc-dot-low', 'high': 'fc-dot-high', 'crit': 'fc-dot-crit'}.get(gi.get('impact', 'low'), 'fc-dot-low')
                parts.append(f'            <span class="fc-dot {dot_cls}">{_esc(gi.get("client_label", ""))}</span>\n')
            parts.append(f'''          </div>
        </div>
        <span class="fc-ev-badge {ev_css}">{_esc(fc.get("evidence_label", "Emerging research"))}</span>
      </div>
''')
            if explanation:
                parts.append(f'''      <div class="fc-body">
        <div class="fc-text">{_esc(explanation)}</div>
        <span class="fc-scope">{n_guilds} / 4 guilds affected</span>
      </div>
''')
            parts.append('    </div>\n')
        parts.append('  </div>\n')

        # ── Cascade diagram ─────────────────────────────────────────────────
        if cascade_guilds and factor_cards:
            parts.append('  <div class="cascade-section">\n')
            parts.append('    <div class="cascade-label">How these factors connect to your results</div>\n')
            parts.append('    <p style="font-size:11px;color:var(--soft);margin-bottom:14px;">Each factor pill shows the direction of scientific evidence: <strong style="color:var(--dark);">→</strong> factor influences your gut &nbsp;|&nbsp; <strong style="color:var(--dark);">↔</strong> bidirectional — your gut also influences this factor back</p>\n')
            parts.append('    <div class="cascade-diag">\n')
            parts.append('      <div class="casc-left">\n')
            for fc in factor_cards:
                fc_dir = fc.get('directionality', 'driver')
                arrow = '↔' if fc_dir == 'bidirectional' else '→'
                parts.append(f'        <div class="casc-factor-pill"><span style="font-size:18px">{fc.get("icon","🔍")}</span> {_esc(fc.get("label",""))}<span style="font-size:11px;color:var(--soft);margin-left:6px;">{arrow}</span></div>\n')
            parts.append('      </div>\n')
            parts.append('      <div class="casc-arrow" style="font-size:14px;color:var(--soft);">factors</div>\n')
            parts.append('      <div class="casc-right">\n')
            for cg in cascade_guilds:
                gp_cls = {'low': 'gp-low', 'high': 'gp-high', 'crit': 'gp-crit'}.get(cg.get('impact', 'low'), 'gp-low')
                emojis = ' '.join(cg.get('driving_factor_emojis', []))
                parts.append(f'''        <div class="casc-guild-pill {gp_cls}">
          <span class="cgp-label">{_esc(cg.get("client_label",""))}</span>
          <span class="cgp-value">{_esc(cg.get("value_str",""))}</span>
          <span class="cgp-emojis">{emojis}</span>
        </div>
''')
            parts.append('      </div>\n    </div>\n  </div>\n')

        # ── Deviation cards as hidden fallback (backward compat) ───────────
        parts.append('  <div style="display:none">\n')
        parts.append('    <div class="rc-grid">\n')

    elif deviation_cards:
        # ── Old layout: no factor_cards available, show deviation cards directly ──
        if section_summary_html:
            parts.append(f'''  <div style="background:var(--green-lt);border-left:3px solid var(--green);border-radius:0 10px 10px 0;padding:16px 22px;margin-bottom:32px;font-size:14px;color:var(--mid);line-height:1.8;">
    {_esc(section_summary_html)}
  </div>
''')
        parts.append('  <div class="rc-grid">\n')

    if deviation_cards:
        for rc_item in deviation_cards:
            deviation = rc_item.get('deviation', {})
            narrative = rc_item.get('narrative', '')
            summary_line = rc_item.get('summary_line', '')
            # Deterministic fallback fields
            health_meaning = rc_item.get('health_meaning', deviation.get('description', ''))
            drivers = rc_item.get('drivers', [])
            personal_synthesis = rc_item.get('personal_synthesis', '')

            dev_icon = deviation.get('icon', '🔬')
            dev_label = _esc(deviation.get('client_label', ''))
            dev_value = _esc(deviation.get('value_str', ''))
            dev_range = _esc(deviation.get('range_str', ''))

            # ── Evidence strength → dot CSS class mapping ─────────────────────
            def _ev_dot_class(strength: str, pos: int) -> str:
                """Returns CSS class for dot at position (0-3) given evidence strength."""
                levels = {'strong': 4, 'moderate': 3, 'weak_to_moderate': 2, 'weak': 1}
                filled = levels.get(strength, 1)
                if pos < filled:
                    return f'rfi-dot filled-{strength.replace("_", "-")}'
                return 'rfi-dot'

            def _ev_label_class(evidence_label: str) -> str:
                if evidence_label == 'Well established':
                    return 'ev-label-established'
                if evidence_label == 'Research supported':
                    return 'ev-label-supported'
                return 'ev-label-emerging'

            def _dir_class(directionality: str) -> str:
                return f'rfi-{directionality}' if directionality in ('driver', 'bidirectional', 'consequence', 'associative') else 'rfi-associative'

            kb_drivers = rc_item.get('kb_drivers', [])

            # ── Flat deviation block (no card box, no header row) ─────────────
            parts.append('    <div class="rc-dev-block">\n')

            # ── Zone 1: Narrative — plain paragraphs, no coloured box ─────────
            if narrative:
                narrative_paragraphs = [p.strip() for p in narrative.split('\n\n') if p.strip()]
                if not narrative_paragraphs:
                    narrative_paragraphs = [narrative.strip()]
                parts.append('      <div class="rc-narrative">\n')
                for para in narrative_paragraphs:
                    parts.append(f'        <p>{_esc(para)}</p>\n')
                parts.append('      </div>\n')
            elif health_meaning:
                parts.append(f'      <div class="rc-narrative"><p>{_esc(health_meaning)}</p></div>\n')

            # ── Zone 2: Factor chips — awareness-chip style ───────────────────
            if kb_drivers:
                n_factors = len(kb_drivers)
                factor_label = f'{n_factors} contributing factor{"s" if n_factors != 1 else ""}'
                parts.append(f'      <div class="rc-factors-label">Why this happened — {factor_label}</div>\n')
                parts.append('      <div class="rc-factor-stack">\n')

                for driver in kb_drivers:
                    d_icon = driver.get('icon', '🔍')
                    d_label = _esc(driver.get('label', ''))
                    d_text = _esc(driver.get('text', ''))       # LLM personalised explanation
                    d_kb   = _esc(driver.get('kb_text', ''))    # KB static science text
                    d_evid = driver.get('evidence_label', 'Emerging research')
                    ev_label_css = _ev_label_class(d_evid)

                    parts.append(f'''        <details class="rc-factor-chip">
          <summary>
            <span class="rfc-icon">{d_icon}</span>
            <span class="rfc-title">{d_label}</span>
            <span class="rfc-ev-label {ev_label_css}">{_esc(d_evid)}</span>
          </summary>
          <div class="rfc-body">
            <div class="rfc-text">{d_text}</div>
''')
                    # "What does science say" — nested expandable, only if KB text exists
                    if d_kb:
                        parts.append(f'''            <details class="rfc-science">
              <summary>What does science say</summary>
              <div class="rfc-science-body">{d_kb}</div>
            </details>
''')
                    parts.append('          </div>\n        </details>\n')

                parts.append('      </div>\n')

            # ── Zone 3: Takeaway line ─────────────────────────────────────────
            if summary_line:
                parts.append(f'      <div class="rc-takeaway">✓ {_esc(summary_line)}</div>\n')
            elif personal_synthesis:
                parts.append(f'      <div class="rc-takeaway">{_esc(personal_synthesis)}</div>\n')

            parts.append('    </div>\n')

        parts.append('  </div>\n')
        # Close the display:none hidden wrapper (only opened when factor_cards layout is active)
        if factor_cards:
            parts.append('  </div>\n')

    # ── Awareness chips — triggered lifestyle factors with no active deviation ──
    if awareness_chips:
        parts.append('''  <div class="rc-awareness-zone">
    <div class="rc-awareness-label">From your questionnaire</div>
    <p class="rc-awareness-intro">You mentioned these factors when filling in your questionnaire. We're sharing them as educational background — <strong>these are not microbiome findings</strong>. Your test results don't currently show any measurable connection to them, but the research linking them to gut health is worth understanding.</p>
    <div class="rc-awareness-chips">
''')
        for aw in awareness_chips:
            aw_icon = aw.get('icon', '🔍')
            aw_label = _esc(aw.get('domain_label', ''))
            aw_arrow = _esc(aw.get('directionality_arrow', ''))
            aw_summary = _esc(aw.get('summary_text', ''))

            parts.append(f'''      <details class="rc-awareness-chip">
        <summary>
          <span class="rc-aw-icon">{aw_icon}</span>
          <span class="rc-aw-title">{aw_label}</span>
          <span style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-left:8px;background:var(--sand);padding:2px 8px;border-radius:20px;border:1px solid var(--rule);">You reported this</span>
        </summary>
        <div class="rc-aw-body">{aw_summary}</div>
      </details>
''')
        parts.append('    </div>\n  </div>\n')

    if not has_any:
        parts.append('''  <div class="rc-empty">
    <p>✅ <strong>Your microbiome looks healthy and your questionnaire doesn't flag any specific risk factors.</strong><br>
    There are no deviations to explain at this point. Your microbiome findings in Sections 1 and 2 tell the full story.</p>
  </div>
''')

    parts.append('</div>\n')

    # ════════════════════ SECTION 4: TIMELINE ════════════════════
    phase_colors = ['#3A6EA8', '#2E8B6E', '#C97C2A', '#6B5EA8']

    parts.append(f'''
<div class="section">
  <div class="sec-label">Section 4 · The Road Ahead</div>
  <h2 class="sec-title">How do we turn this around?</h2>
  <p class="sec-intro">Your personalized formula is designed to work in stages. Here is what to expect on the journey toward a more balanced microbiome — and how the different components of your formula support each phase.</p>

  <div class="turnaround-grid">
    <div class="timeline">
''')

    for i, phase in enumerate(timeline_phases):
        color = phase_colors[i] if i < len(phase_colors) else '#6B5EA8'
        parts.append(f'''      <div class="tl-item">
        <div class="tl-dot" style="background:{color}">{i + 1}</div>
        <div class="tl-period">Weeks {_esc(phase["weeks"])}</div>
        <div class="tl-title">{_esc(phase["title"])}</div>
        <div class="tl-body">{_esc(phase["body"])}</div>
      </div>
''')

    parts.append('''    </div>

    <div class="lifestyle-panel">
      <div class="lp-label">While your formula works its science</div>
      <div class="lp-items">
''')

    # Lifestyle recommendations (LLM-generated or fallback)
    lifestyle_recs = data.get('lifestyle_recommendations', [])
    if not lifestyle_recs:
        lifestyle_recs = _lifestyle_fallback()

    for rec in lifestyle_recs:
        parts.append(f'''        <div class="lp-item">
          <span class="lp-emoji">{_esc(rec["emoji"])}</span>
          <div>
            <div class="lp-title">{_esc(rec["title"])}</div>
            <div class="lp-text">{_esc(rec["text"])}</div>
          </div>
        </div>
''')

    parts.append('''      </div>
    </div>
  </div>
</div>
''')

    # ════════════════════ SECTION 5: SUPPLEMENTS ════════════════════
    if supplement_cards:
        parts.append(f'''
<div class="section sand">
  <div class="sec-label">Section 5 · Your Formula</div>
  <h2 class="sec-title">What you are taking and why</h2>
  <p class="sec-intro">Your formula contains several targeted units. Each one supports a different step in rebalancing your microbiome — and everything in it is there for a specific reason connected to your results.</p>

  <div class="supp-units">
''')

        for card in supplement_cards:
            parts.append(f'''    <div class="supp-unit">
      <div class="supp-header">
        <div class="supp-num-block" style="background:{_esc(card["color"])}">{card["num"]}</div>
        <div class="supp-head-text">
          <div class="supp-unit-name">{_esc(card["name"])}</div>
          <div class="supp-unit-when">{_esc(card["timing"])}</div>
        </div>
        <div class="supp-unit-tag">{_esc(card["emoji"])} {_esc(card["what_it_is"])}</div>
      </div>
      <div class="supp-meta">
        <div class="supp-meta-item">
          <div class="smi-label">What it is</div>
          <div class="smi-text">{_esc(card["what_it_is"])}</div>
        </div>
      </div>
''')
            if card.get('why'):
                parts.append(f'''      <div class="supp-why-band">
        <span class="why-icon">🎯</span>
        <span><strong>Why you're taking it:</strong> {_esc(card["why"])}</span>
      </div>
''')
            # Multi-capsule cards (Morning/Evening Wellness) — render per capsule with sub-headers
            if card.get('capsules'):
                for cap in card['capsules']:
                    cap_label = _esc(cap.get('label', ''))
                    cap_weight = _esc(cap.get('weight', ''))
                    _cap_weight_html = ('&nbsp;·&nbsp;<span style="font-size:11px;color:var(--soft);">' + cap_weight + '</span>') if cap_weight else ''
                    parts.append(f'      <div style="padding:4px 24px 0;"><span style="font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--soft);">{cap_label}</span>{_cap_weight_html}</div>\n')
                    parts.append('      <div class="supp-pills" style="padding-top:8px;padding-bottom:16px;">\n')
                    for comp in cap.get('components', []):
                        parts.append(f'        <div class="pill"><strong>{_esc(comp["name"])}</strong> · {_esc(str(comp["dose"]))}</div>\n')
                    parts.append('      </div>\n')
            # Single-level pill cards (Probiotic, Omega, Prebiotic)
            elif card.get('pills'):
                parts.append('      <div class="supp-pills">\n')
                for pill in card['pills']:
                    parts.append(f'        <div class="pill"><strong>{_esc(pill["name"])}</strong> · {_esc(str(pill["dose"]))}</div>\n')
                parts.append('      </div>\n')

            if card.get('supports'):
                parts.append('      <div class="supp-supports">\n')
                parts.append('        <div class="supp-supports-label">Supports</div>\n')
                parts.append('        <div class="supp-supports-chips">\n')
                for s in card['supports']:
                    parts.append(f'          <div class="sc-chip">{_esc(s)}</div>\n')
                parts.append('        </div>\n      </div>\n')

            parts.append('    </div>\n')

        parts.append('  </div>\n</div>\n')

    else:
        parts.append('''
<div class="section sand">
  <div class="sec-label">Section 5 · Your Formula</div>
  <h2 class="sec-title">What you are taking and why</h2>
  <p class="sec-intro">Formulation data not available for this sample. Please generate the formulation first using <code>generate_formulation.py</code>.</p>
</div>
''')

    # ════════════════════ SECTION 6: HEALTH GOALS ════════════════════
    if goal_cards:
        parts.append(f'''
<div class="section">
  <div class="sec-label">Section 6 · Your Goals</div>
  <h2 class="sec-title">How it aligns with your health goals</h2>
  <p class="sec-intro">Everything in this protocol connects directly to what you told us matters most. Here is the link between your goals, the science behind them, and how your formula addresses each one.</p>

  <div class="goals-row">
''')

        for gc in goal_cards:
            inferred_tag = '<div class="goal-inferred-tag">Also addressed by your formula</div>' if gc.get('inferred') else ''
            parts.append(f'''    <div class="goal-card">
      <span class="goal-emoji">{_esc(gc["emoji"])}</span>
      {inferred_tag}
      <div class="goal-title">{_esc(gc["title"])}</div>
      <div class="goal-mechanism">{_esc(gc["mechanism"])}</div>
      <div class="goal-formula"><strong>Your formula:</strong> {_esc(gc["formula_link"])}</div>
    </div>
''')

        parts.append('  </div>\n</div>\n')

    else:
        parts.append('''
<div class="section">
  <div class="sec-label">Section 6 · Your Goals</div>
  <h2 class="sec-title">How it aligns with your health goals</h2>
  <p class="sec-intro">Questionnaire data not available — health goals section requires a completed questionnaire.</p>
</div>
''')

    # ════════════════════ REFERENCES ════════════════════
    cited_papers = data.get('cited_papers', [])
    if cited_papers:
        parts.append('''
<div class="section sand">
  <div class="sec-label">References</div>
  <h2 class="sec-title" style="font-size:28px;margin-bottom:24px;">Scientific References</h2>
  <p style="font-size:13px;color:var(--soft);margin-bottom:24px;max-width:600px;line-height:1.7;">The following peer-reviewed papers were used to ground the explanations in Section 3. Citations are formatted in APA style.</p>
  <div style="display:flex;flex-direction:column;gap:14px;">
''')
        for paper in cited_papers:
            apa = _format_apa_citation(paper)
            # Render: plain text APA but with journal name in italic HTML
            # _format_apa_citation uses *journal* — replace *text* with <em>text</em>
            import re as _re
            apa_html = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', apa)
            # Make DOI a clickable link
            doi_val = paper.get('doi', '')
            if doi_val:
                doi_url = doi_val if doi_val.startswith('http') else f'https://doi.org/{doi_val}'
                apa_html = apa_html.replace(
                    f'https://doi.org/{doi_val.lstrip("https://doi.org/")}',
                    f'<a href="{doi_url}" style="color:var(--blue);text-decoration:none;" target="_blank">{doi_url}</a>'
                )
            parts.append(f'''    <div style="font-size:13px;color:var(--mid);line-height:1.75;padding:14px 18px;background:var(--warm);border-radius:8px;border-left:3px solid var(--rule);">
      {apa_html}
    </div>
''')
        parts.append('  </div>\n</div>\n')

    # ════════════════════ FOOTER ════════════════════
    parts.append(f'''
<div class="footer">
  <div class="footer-brand">NB1 Health</div>
  <div style="max-width:440px;text-align:center">This report is for informational purposes only and does not constitute medical advice. Please consult a healthcare professional before beginning any supplement protocol.</div>
  <div>{_esc(report_date_display)}</div>
</div>
''')

    # ════════════════════ JAVASCRIPT ════════════════════
    # Build HR_TPS (timepoint data) from flat JSON (schema v3.0: guild_timepoints)
    # Falls back to building from bacterial_groups if guild_timepoints not present
    _guild_timepoints = data.get('guild_timepoints', [])
    if _guild_timepoints:
        _hr_tps = _guild_timepoints
    else:
        # Fallback: build from bacterial_groups
        def _guild_val_from_bg(keyword: str) -> float:
            for _gname, _gdata in bg.items():
                if keyword.lower() in _gname.lower():
                    return round(_gdata.get('abundance', 0.0) / 100.0, 4)
            return 0.0
        _tp_label = 'Baseline — ' + datetime.strptime(report_date, '%Y-%m-%d').strftime('%b %Y') if report_date else 'Baseline'
        _hr_tps = [{
            'label': _tp_label,
            'guilds': {
                'fd': _guild_val_from_bg('fiber'),
                'bb': _guild_val_from_bg('bifidobacter') or _guild_val_from_bg('hmo'),
                'cf': _guild_val_from_bg('cross'),
                'bp': _guild_val_from_bg('butyrate'),
                'pg': _guild_val_from_bg('proteolytic'),
                'md': _guild_val_from_bg('mucin'),
            },
        }]
    import json as _json
    _hr_tps_json = _json.dumps(_hr_tps)

    # Inject score_summary as JS-safe string for updateCoverScore()
    import json as _json2
    _score_summary_js = _json2.dumps(score_summary)  # JSON-encoded → safe JS string literal

    parts.append(f'''
<script>
// ── Score summary (pre-computed, injected from JSON) ─────────────────────────────────────────────
const SCORE_SUMMARY = {_score_summary_js};

// ── Scroll animation ─────────────────────────────────────────────────────────────────────────────────
const observer = new IntersectionObserver((entries) => {{
  entries.forEach(e => {{ if (e.isIntersecting) e.target.style.animationPlayState = 'running'; }});
}}, {{ threshold: 0.1 }});
document.querySelectorAll('.h-dial-card,.gbar,.sw-card,.supp-unit,.goal-card,.tl-item').forEach(el => {{
  el.style.animationPlayState = 'paused'; observer.observe(el);
}});

// ── Timepoint data ─────────────────────────────────────────────────────────────────────────────────
const HR_TPS = {_hr_tps_json};

// ── Guild config ──────────────────────────────────────────────────────────────────────────────────
const GCFG = {{
  fd: {{name:'Fiber Degraders',    min:0.20, max:0.45, trackMax:0.55, invert:false}},
  bb: {{name:'Bifidobacteria',     min:0.04, max:0.14, trackMax:0.22, invert:false}},
  cf: {{name:'Cross-Feeders',      min:0.10, max:0.22, trackMax:0.30, invert:false}},
  bp: {{name:'Butyrate Producers', min:0.10, max:0.25, trackMax:0.32, invert:false}},
  pg: {{name:'Protein Fermenters', min:0.00, max:0.09, trackMax:0.22, invert:true }},
  md: {{name:'Mucus Layer Bacteria',min:0.00,max:0.06, trackMax:0.14, invert:true }},
}};

const TARGET_LABEL = {{
  fd:'Target ≥20%', bb:'Target 4–14%', cf:'Target 10–22%',
  bp:'Target 10–25%', pg:'Target ≤9%', md:'Target ≤6%',
}};

const GDESC = {{
  fd:'First in the fiber chain — they break complex plant fibers into simpler sugars that feed Bifidobacteria and Cross-Feeders.',
  bb:'They produce lactate and acetate, lowering gut pH to inhibit pathogens and amplify the signal for Cross-Feeders.',
  cf:'They consume lactate and acetate from Bifidobacteria and Fiber Degraders, converting them into precursors for Butyrate Producers.',
  bp:'End of the chain — they produce butyrate, the primary fuel for the cells lining your colon.',
  pg:'A separate pathway: they ferment undigested protein. Lower is better. If dominant, they signal a carb→protein ecosystem shift.',
  md:'Under healthy conditions, controlled mucin degradation stimulates goblet cell turnover and maintains mucus layer homeostasis. These bacteria are a normal part of a balanced ecosystem — only when they expand significantly (usually when dietary fiber is scarce) do they begin to compromise the protective lining.',
}};

// ── Status helpers ───────────────────────────────────────────────────────────────────────────────
function getStatus(key, val) {{
  const c = GCFG[key];
  if (!c) return 'ok';
  if (c.invert) {{
    if (val <= c.max) return 'ok';
    if (val <= c.max * 1.7) return 'above';
    return 'critical';
  }} else {{
    if (val >= c.min && val <= c.max * 1.5) return 'ok';
    if (val < c.min) return (val >= c.min * 0.55) ? 'below' : 'critical';
    return 'above';
  }}
}}

const SC = {{
  ok:       {{fill:'#E8F5F1', stroke:'#2E8B6E', tf:'#2E8B6E', marker:'ah-green', badge:'badge-ok',   badgeLabel:'✓ Healthy', fillClass:'fill-ok'}},
  below:    {{fill:'#EAF0F8', stroke:'#3A6EA8', tf:'#3A6EA8', marker:'ah-blue',  badge:'badge-below', badgeLabel:'↓ Low',    fillClass:'fill-below'}},
  above:    {{fill:'#FBF1E4', stroke:'#C97C2A', tf:'#C97C2A', marker:'ah-amber', badge:'badge-above', badgeLabel:'↑ High',   fillClass:'fill-above'}},
  critical: {{fill:'#FCECEA', stroke:'#C24B3A', tf:'#C24B3A', marker:'ah-red',   badge:'badge-critical',badgeLabel:'⚠ Critical',fillClass:'fill-critical'}},
}};

const INV_SC = {{
  ok:       {{fill:'#E8F5F1', stroke:'#2E8B6E', tf:'#2E8B6E', marker:'ah-green', badge:'badge-ok',   badgeLabel:'✓ Controlled'}},
  above:    {{fill:'#FBF1E4', stroke:'#C97C2A', tf:'#C97C2A', marker:'ah-amber', badge:'badge-above', badgeLabel:'↑ Elevated'}},
  critical: {{fill:'#FCECEA', stroke:'#C24B3A', tf:'#C24B3A', marker:'ah-red',   badge:'badge-critical',badgeLabel:'⚠ High'}},
}};

function sc(key, status) {{
  return (GCFG[key] && GCFG[key].invert) ? (INV_SC[status]||INV_SC.ok) : (SC[status]||SC.ok);
}}

function statusLabel(key, status, val) {{
  const s = sc(key, status);
  return `${{s.badgeLabel}} · ${{(val*100).toFixed(1)}}%`;
}}

// ── SVG update ───────────────────────────────────────────────────────────────────────────────────
const ARROW_IDS = {{ bb:'arr-bb', cf:'arr-cf', bp:'arr-bp', pg:'arr-pg', md:'arr-md' }};

function svgEl(id) {{ return document.getElementById(id); }}

function updateSVG(guilds, prevGuilds) {{
  ['fd','bb','cf','bp','pg','md'].forEach(key => {{
    const val = guilds[key];
    const status = getStatus(key, val);
    // For non-inverted (beneficial) guilds, remap 'below' → 'critical' (red) to match bars
    const displayStatus = (!GCFG[key]?.invert && status === 'below') ? 'critical' : status;
    const colors = sc(key, displayStatus);

    const nr = svgEl('nr-'+key);
    if (nr) {{ nr.setAttribute('fill', colors.fill); nr.setAttribute('stroke', colors.stroke); }}

    const ns = svgEl('ns-'+key);
    if (ns) {{ ns.textContent = statusLabel(key, displayStatus, val); ns.setAttribute('fill', colors.tf); }}
  }});

  ['cf','bp','pg','md'].forEach(key => {{
    const val    = guilds[key];
    const status = getStatus(key, val);
    const colors = sc(key, status);
    const arrId  = ARROW_IDS[key];
    if (arrId) {{
      const arr = svgEl(arrId);
      if (arr) {{
        if (key === 'md') {{
          arr.setAttribute('stroke', '#6B7EA8');
          arr.setAttribute('marker-end', 'url(#ah-blue)');
        }} else {{
          arr.setAttribute('stroke', colors.stroke);
          arr.setAttribute('marker-end', `url(#${{colors.marker}})`);
        }}
      }}
    }}
  }});

  const bnFD = ['below','critical'].includes(getStatus('fd', guilds.fd));
  const bnBB = ['below','critical'].includes(getStatus('bb', guilds.bb));
  const bnCF = ['below','critical'].includes(getStatus('cf', guilds.cf));

  function applyBottleneck(arrowId, active, normalStroke, normalMarker) {{
    const el = svgEl(arrowId);
    if (!el) return;
    el.setAttribute('stroke',       active ? '#C8CBCF' : normalStroke);
    el.setAttribute('stroke-width', active ? '1'       : '2');
    el.setAttribute('marker-end',   active ? 'url(#ah-gray)' : `url(#${{normalMarker}})`);
  }}

  applyBottleneck('arr-fd-bb', bnFD, '#3A6EA8', 'ah-blue');
  applyBottleneck('arr-fd-cf', bnFD, '#3A6EA8', 'ah-blue');

  const nrBb = svgEl('nr-bb');
  if (nrBb) nrBb.setAttribute('opacity', bnFD ? '0.58' : '1');

  applyBottleneck('arr-bb', bnBB, '#C97C2A', 'ah-amber');
  applyBottleneck('arr-cf', bnCF, '#C97C2A', 'ah-amber');

  const nrBp = svgEl('nr-bp');
  if (nrBp) nrBp.setAttribute('opacity', (bnBB && bnCF) ? '0.58' : '1');

  const bnPill = svgEl('bb-bottleneck-pill');
  const bnText = svgEl('bb-bottleneck-text');
  if (bnPill) bnPill.setAttribute('display', bnFD ? 'block' : 'none');
  if (bnText) bnText.setAttribute('display', bnFD ? 'block' : 'none');

  const bpSt = getStatus('bp', guilds.bp);
  const pgSt = getStatus('pg', guilds.pg);
  const bpC  = sc('bp', bpSt), pgC = sc('pg', pgSt);

  function setOutput(rectId, textId, c, txt) {{
    const r = svgEl(rectId); if (r) {{ r.setAttribute('fill', c.fill); r.setAttribute('stroke', c.stroke); }}
    const t = svgEl(textId); if (t) {{ t.textContent = txt; t.setAttribute('fill', c.tf); }}
  }}
  const bpPct = Math.min(100, Math.round(guilds.bp/0.18*100));

  setOutput('nr-out-bp','ns-out-bp', bpC, bpPct>=100?'✓ optimal':bpPct>=60?'✓ High':'↓ low fuel');
  setOutput('nr-out-pg','ns-out-pg', pgC, guilds.pg<=0.09?'✓ Low':guilds.pg<=0.14?'Moderate':'High');
  const mucusTxt = guilds.md<=0.06?'✓ Homeostatic':guilds.md<=0.10?'ℹ moderate':'↑ elevated';
  const nsOutMd = svgEl('ns-out-md');
  if (nsOutMd) {{ nsOutMd.textContent = mucusTxt; nsOutMd.setAttribute('fill','#6B7EA8'); }}
}}

// ── Guild bars update ───────────────────────────────────────────────────────────────────────────────
const GBAR_TRACK_MAX = {{ fd:0.55, bb:0.22, cf:0.30, bp:0.32, pg:0.22, md:0.14 }};

function updateBars(guilds) {{
  ['fd','bb','cf','bp','pg','md'].forEach(key => {{
    const val = guilds[key];
    const status = getStatus(key, val);
    const colors = sc(key, status);
    const trackMax = GBAR_TRACK_MAX[key] || 0.30;
    const fillPct = Math.min(100, Math.round(val/trackMax*100));

    const bar = document.getElementById('gbar-'+key);
    if (bar) {{
      // For non-inverted (beneficial) guilds, treat 'below' same as 'critical' (red) to match Python render
      const barStatus = (!GCFG[key]?.invert && status === 'below') ? 'critical' : status;
      const barColors = sc(key, barStatus);
      bar.className = 'gbar ' + (barStatus === 'ok' ? 'ok' : barStatus === 'below' ? 'below' : barStatus === 'critical' ? 'critical' : 'above');
    }}
    const badge = document.getElementById('gbadge-'+key);
    if (badge) {{
      badge.className = 'gbar-badge ' + barColors.badge;
      badge.textContent = statusLabel(key, barStatus, val);
    }}
    const fill = document.getElementById('gfill-'+key);
    if (fill) {{
      fill.style.width = fillPct + '%';
      fill.className = 'gbar-fill ' + colors.fillClass;
    }}
  }});
}}

// ── Tooltip ─────────────────────────────────────────────────────────────────────────────────────
let currentTpIdx = 0;
const tooltip = document.getElementById('guild-tooltip');
const pathWrap = document.getElementById('pathway-wrap');

function showTooltip(e, key) {{
  if (!GCFG[key]) {{ tooltip.classList.remove('visible'); return; }}
  const tp   = HR_TPS[currentTpIdx];
  const prev = currentTpIdx > 0 ? HR_TPS[currentTpIdx-1] : null;
  const val  = tp.guilds[key];
  const c    = GCFG[key];
  const status = getStatus(key, val);
  const colors = sc(key, status);

  document.getElementById('gp-title').textContent = GCFG[key].name;

  const statusEl = document.getElementById('gp-status');
  statusEl.textContent = colors.badgeLabel || 'ok';
  statusEl.style.background = colors.stroke;
  statusEl.style.color = 'white';

  const deltaEl = document.getElementById('gp-delta');
  if (prev) {{
    const prevVal = prev.guilds[key];
    const diff = val - prevVal;
    const absDiff = (Math.abs(diff)*100).toFixed(1);
    const isGood = (c.invert && diff < 0) || (!c.invert && diff > 0);
    const isNeutral = Math.abs(diff) < 0.003;
    deltaEl.style.display = 'inline-block';
    if (isNeutral) {{
      deltaEl.textContent = '→ no change';
      deltaEl.style.background = 'rgba(255,255,255,.1)';
      deltaEl.style.color = 'rgba(255,255,255,.5)';
    }} else if (isGood) {{
      deltaEl.textContent = `↑ +${{absDiff}}% vs prev`;
      deltaEl.style.background = 'rgba(46,139,110,.3)';
      deltaEl.style.color = '#7ee0b5';
    }} else {{
      deltaEl.textContent = `↓ -${{absDiff}}% vs prev`;
      deltaEl.style.background = 'rgba(194,75,58,.3)';
      deltaEl.style.color = '#f5a9a0';
    }}
  }} else {{
    deltaEl.style.display = 'none';
  }}

  document.getElementById('gp-val').textContent = (val*100).toFixed(1)+'%';
  document.getElementById('gp-val').style.color = colors.stroke;
  document.getElementById('gp-target').textContent = TARGET_LABEL[key]||'';

  const trackPct = v => Math.min(100, Math.round(v/c.trackMax*100));
  document.getElementById('gp-gfill').style.width  = trackPct(val)+'%';
  document.getElementById('gp-gfill').style.background = colors.stroke;

  const optEl = document.getElementById('gp-optimal');
  if (c.invert) {{ optEl.style.left='0%'; optEl.style.width=trackPct(c.max)+'%'; }}
  else          {{ optEl.style.left=trackPct(c.min)+'%'; optEl.style.width=trackPct(Math.min(c.max*1.5,c.trackMax)-c.min)+'%'; }}

  const markerEl = document.getElementById('gp-marker');
  markerEl.style.left = (c.invert ? trackPct(c.max) : trackPct(c.min))+'%';

  document.getElementById('gp-range-end').textContent = (c.trackMax*100).toFixed(0)+'%';
  let descText = GDESC[key]||'';
  if (key === 'bb' && ['below','critical'].includes(getStatus('fd', HR_TPS[currentTpIdx].guilds.fd))) {{
    descText = 'The competitive depletion reflects upstream substrate limitation. With fiber-degraders below optimal, the oligosaccharide flux narrows, creating a bottleneck. Bifidobacteria are poised for growth but constrained by the narrowed substrate pipeline.';
  }}
  document.getElementById('gp-text').textContent = descText;

  tooltip.classList.add('visible');

  const wRect = pathWrap.getBoundingClientRect();
  const nRect = e.currentTarget.getBoundingClientRect();
  let left = nRect.left - wRect.left;
  let top  = nRect.bottom - wRect.top + 6;
  if (left + 260 > wRect.width) left = wRect.width - 265;
  if (left < 0) left = 0;
  if (top + 200 > wRect.height) top = nRect.top - wRect.top - 210;
  tooltip.style.left = left+'px';
  tooltip.style.top  = top+'px';
}}

// ── Cover score ─────────────────────────────────────────────────────────────────────────────────
function updateCoverScore() {{
  const latestIdx = HR_TPS.length - 1;
  const score = computeBannerScore(HR_TPS[latestIdx].guilds);
  const dialFill = document.getElementById('cover-dial-fill');
  const scoreNum = document.getElementById('cover-score-num');
  const scoreText = document.getElementById('cover-score-text');
  if (dialFill) {{
    const offset = (283 * (1 - score / 100)).toFixed(1);
    dialFill.style.strokeDashoffset = offset;
    dialFill.setAttribute('stroke', score >= 65 ? '#2E8B6E' : score >= 40 ? '#C97C2A' : '#C24B3A');
  }}
  if (scoreNum) scoreNum.textContent = score;
  if (scoreText) scoreText.innerHTML = SCORE_SUMMARY + `<br><span style="font-size:12px;opacity:.7;margin-top:4px;display:block">Your most recent result — ${{HR_TPS[latestIdx].label}}</span>`;
}}

// ── Question pill data ────────────────────────────────────────────────────────────────────────────
const QDATA = {{
  fd: {{
    q: 'Why is fiber important?',
    a: 'Dietary fiber is the main fuel for your beneficial bacteria. Without it, the entire fermentation chain slows down — less butyrate is produced, your gut wall cells get less energy, and protective bacterial communities shrink.'
  }},
  substrate: {{
    q: 'What is substrate limitation?',
    a: "When Fiber Degraders are below their optimal range, there is less raw material flowing into the chain. Bifidobacteria and Cross-Feeders are ready and able — but they are waiting for more substrate. It's like having a factory with the right workers but not enough raw materials arriving."
  }},
  bp: {{
    q: 'What does butyrate do?',
    a: 'Butyrate is the primary energy source for the cells lining your colon. It helps maintain the gut barrier, reduces inflammation, and supports immune signalling. Low butyrate is associated with a more permeable gut wall.'
  }},
  pg: {{
    q: 'Why is too much protein fermentation bad?',
    a: 'Protein fermentation produces compounds like ammonia and phenols that can be pro-inflammatory at high levels. When protein fermenters dominate, it signals that the ecosystem has shifted away from the healthier fiber-driven state.'
  }},
  md: {{
    q: 'What is the mucus layer?',
    a: 'Your intestinal lining is coated by a protective mucus layer that acts as a barrier. Mucus Layer Bacteria use it as a backup fuel source — normal at low levels, but when they expand, they start consuming the lining faster than it regenerates.'
  }},
  scfa: {{
    q: 'What are SCFAs?',
    a: 'Short-Chain Fatty Acids (SCFAs) are the main output of your fiber fermentation chain. Butyrate, propionate and acetate are the key ones. They fuel your gut wall cells, regulate inflammation, support your immune system, and even communicate with your brain via the gut-brain axis. Low SCFAs are associated with a weaker gut barrier and higher inflammatory pressure.'
  }},
}};

function updateQPills(guilds) {{
  const bnFD  = ['below','critical'].includes(getStatus('fd', guilds.fd));
  const bpLow = ['below','critical'].includes(getStatus('bp', guilds.bp));
  const pgHigh = ['above','critical'].includes(getStatus('pg', guilds.pg));

  const show = {{
    'qpill-fd':        bnFD || bpLow,
    'qpill-substrate': bnFD,
    'qpill-bp':        bpLow || bnFD,
    'qpill-pg':        pgHigh,
  }};

  Object.entries(show).forEach(([id, visible]) => {{
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? 'block' : 'none';
  }});
  ['qpill-scfa','qpill-md','qpill-fd','qpill-bp'].forEach(id => {{
    const el = document.getElementById(id);
    if (el) el.style.display = 'block';
  }});
}}

const qaPopup = document.getElementById('qanswer-popup');

document.querySelectorAll('.qpill[data-q]').forEach(pill => {{
  pill.addEventListener('mouseenter', e => {{
    const key = pill.dataset.q;
    const d = QDATA[key];
    if (!d) return;
    document.getElementById('qa-q').textContent = d.q;
    document.getElementById('qa-a').textContent = d.a;
    qaPopup.classList.add('visible');
    const wRect = pathWrap.getBoundingClientRect();
    const nRect = e.currentTarget.getBoundingClientRect();
    let left = nRect.left - wRect.left;
    let top  = nRect.bottom - wRect.top + 6;
    if (left + 250 > wRect.width) left = wRect.width - 255;
    if (left < 0) left = 0;
    if (top + 170 > wRect.height) top = nRect.top - wRect.top - 175;
    qaPopup.style.left = left + 'px';
    qaPopup.style.top  = top  + 'px';
  }});
  pill.addEventListener('mouseleave', () => qaPopup.classList.remove('visible'));
}});

// ── Info button data ─────────────────────────────────────────────────────────────────────────────
const INFO_DATA = {{
  fd: {{
    title: '🌾 Fiber Degraders — why they matter',
    rows: [
      {{ tag:'ok',   label:'Healthy',  text:'Breaking down plant fibers efficiently, feeding the downstream chain.' }},
      {{ tag:'low',  label:'Too low',  text:'The fermentation chain loses its main fuel source. Less butyrate is produced, which means less support for your gut lining.' }},
      {{ tag:'high', label:'High',     text:'Generally a good sign — more fiber is being processed. Rarely problematic.' }},
    ]
  }},
  bb: {{
    title: '✨ Bifidobacteria — why they matter',
    rows: [
      {{ tag:'ok',   label:'Healthy',  text:'Producing lactate and acetate, lowering gut pH and keeping harmful bacteria in check.' }},
      {{ tag:'low',  label:'Too low',  text:'Less immune support and reduced pH drop — the protective acidic environment weakens.' }},
      {{ tag:'high', label:'High',     text:'Strong immune support. They amplify the downstream fermentation signal.' }},
    ]
  }},
  cf: {{
    title: '🔗 Cross-Feeders — why they matter',
    rows: [
      {{ tag:'ok',   label:'Healthy',  text:'Acting as the bridge of the chain — efficiently converting intermediates for the final step.' }},
      {{ tag:'low',  label:'Too low',  text:'Fewer precursors reach Butyrate Producers, reducing the final output even if upstream guilds are active.' }},
      {{ tag:'high', label:'High',     text:'In this case a positive sign — strong cross-feeding means excellent metabolic communication.' }},
    ]
  }},
  bp: {{
    title: '⚡ Butyrate Producers — why they matter',
    rows: [
      {{ tag:'ok',   label:'Healthy',  text:'Producing butyrate, the main fuel for your gut wall cells. Supports the gut barrier and helps regulate inflammation.' }},
      {{ tag:'low',  label:'Too low',  text:'Your gut wall cells receive less fuel. This can weaken the gut barrier over time.' }},
      {{ tag:'high', label:'High',     text:'Strong butyrate output — your gut lining is well-nourished.' }},
    ]
  }},
  pg: {{
    title: '🧫 Protein Fermenters — why they matter',
    rows: [
      {{ tag:'ok',   label:'Controlled', text:'Present at low levels — their byproducts are minimal and the fiber pathway remains dominant.' }},
      {{ tag:'inv',  label:'If elevated', text:'They produce compounds that can be pro-inflammatory and begin to compete with beneficial bacteria for ecological space. A sign the ecosystem is shifting from fiber-driven to protein-driven.' }},
    ]
  }},
  md: {{
    title: '🔄 Mucus Layer Bacteria — why they matter',
    rows: [
      {{ tag:'ok',   label:'Controlled', text:'Under healthy conditions, controlled mucin degradation stimulates goblet cell turnover and maintains mucus layer homeostasis. Normal and adaptive at low levels.' }},
      {{ tag:'inv',  label:'If elevated', text:"When they expand significantly — usually when dietary fiber is scarce — they start relying more heavily on the gut's protective mucus lining as a fuel source." }},
    ]
  }},
}};

const infoPopup  = document.getElementById('info-popup');

function showInfoPopup(e, key) {{
  const d = INFO_DATA[key];
  if (!d) return;
  document.getElementById('ip-title').textContent = d.title;
  const content = document.getElementById('ip-content');
  content.innerHTML = d.rows.map(r =>
    `<div class="ip-row"><span class="ip-tag ${{r.tag}}">${{r.label}}</span><span class="ip-body">${{r.text}}</span></div>`
  ).join('');
  infoPopup.classList.add('visible');

  const wRect = pathWrap.getBoundingClientRect();
  const nRect = e.currentTarget.getBoundingClientRect();
  let left = nRect.left - wRect.left - 10;
  let top  = nRect.bottom - wRect.top + 6;
  if (left + 250 > wRect.width) left = wRect.width - 255;
  if (left < 0) left = 0;
  if (top + 220 > wRect.height) top = nRect.top - wRect.top - 220;
  infoPopup.style.left = left + 'px';
  infoPopup.style.top  = top  + 'px';
  e.stopPropagation();
}}

document.querySelectorAll('.info-btn[data-info]').forEach(btn => {{
  btn.addEventListener('mouseenter', e => showInfoPopup(e, btn.dataset.info));
  btn.addEventListener('mouseleave', () => infoPopup.classList.remove('visible'));
}});

// ── Tab switch ──────────────────────────────────────────────────────────────────────────────────
function switchTab(n) {{
  document.querySelectorAll('.opt-tab').forEach((t,i) => t.classList.toggle('active', i+1===n));
  document.querySelectorAll('.opt-panel').forEach((p,i) => p.classList.toggle('active', i+1===n));
}}

// ── Banner slider ───────────────────────────────────────────────────────────────────────────────
function updateBanner(idx) {{
  const tp   = HR_TPS[idx];
  const prev = idx > 0 ? HR_TPS[idx-1] : null;
  document.getElementById('banner-tp-label').textContent = tp.label;

  // Use real score from JSON if present (schema v3.0); fall back to guild approximation
  const scoreNow = (tp.score != null) ? Math.round(tp.score) : computeBannerScore(tp.guilds);
  const scoreBar = document.getElementById('banner-score-bar');
  const scoreNum = document.getElementById('banner-score-num');
  if (scoreBar) {{
    scoreBar.style.width = scoreNow + '%';
    scoreBar.style.background = scoreNow >= 65 ? '#3dbe85' : scoreNow >= 40 ? '#f5a623' : '#e05c5c';
  }}
  if (scoreNum) {{
    scoreNum.textContent = scoreNow;
    scoreNum.style.color = scoreNow >= 65 ? '#7ee0b5' : scoreNow >= 40 ? '#fac67a' : '#f5a9a0';
  }}

  const deltaEl = document.getElementById('banner-delta-text');
  if (prev) {{
    const scorePrev = computeBannerScore(prev.guilds);
    const diff = scoreNow - scorePrev;
    if (diff > 0)      deltaEl.textContent = `↑ +${{diff}} pts since last test — keep going!`;
    else if (diff < 0) deltaEl.textContent = `↓ ${{diff}} pts since last test`;
    else               deltaEl.textContent = 'No change since last test';
  }} else {{
    deltaEl.textContent = 'Your baseline measurement';
  }}
}}

function computeBannerScore(g) {{
  const sc = (v,min) => Math.min(1, v/min);
  const pg_sc = g.pg <= GCFG.pg.max ? 1 : Math.max(0, 1-(g.pg-GCFG.pg.max)/GCFG.pg.max);
  return Math.min(100, Math.round(
    (sc(g.fd,GCFG.fd.min)*.22 + sc(g.bb,GCFG.bb.min)*.15 + sc(g.cf,GCFG.cf.min)*.18 +
     sc(g.md,GCFG.md.max)*.12 + sc(g.bp,GCFG.bp.min)*.25 + pg_sc*.08) * 100
  ));
}}

const bannerTicks = document.getElementById('banner-tp-ticks');
if (bannerTicks) {{
  HR_TPS.forEach(tp => {{
    const t = document.createElement('span');
    t.className = 'tp-tick'; t.style.color='rgba(255,255,255,.4)';
    t.textContent = tp.label.split('—')[0].trim();
    bannerTicks.appendChild(t);
  }});
}}
const bannerSlider = document.getElementById('banner-tp-slider');
if (bannerSlider) {{
  bannerSlider.addEventListener('input', e => {{
    const idx = parseInt(e.target.value);
    const hrSlider = document.getElementById('hr-tp-slider');
    if (hrSlider) hrSlider.value = idx;
    hrUpdate(idx);
  }});
}}

document.querySelectorAll('.pnode[data-guild]').forEach(node => {{
  node.addEventListener('mouseenter', e => showTooltip(e, node.dataset.guild));
  node.addEventListener('mouseleave', () => tooltip && tooltip.classList.remove('visible'));
}});

// ── Ecosystem dominance indicator ────────────────────────────────────────────────────────────
function updateDominance(guilds) {{
  const fiberScore = (
    Math.min(1, guilds.fd / GCFG.fd.min) +
    Math.min(1, guilds.bb / GCFG.bb.min) +
    Math.min(1, guilds.cf / GCFG.cf.min) +
    Math.min(1, guilds.bp / GCFG.bp.min)
  ) / 4;

  const pgPressure = Math.min(1, guilds.pg / (GCFG.pg.max * 2));
  const dominanceIdx = Math.round((1 - fiberScore) * 55 + pgPressure * 45);
  const clamped = Math.max(4, Math.min(96, dominanceIdx));

  const fill   = document.getElementById('dominance-fill');
  const badge  = document.getElementById('dominance-badge');
  const note   = document.getElementById('dominance-note');
  if (!fill || !badge || !note) return;

  fill.style.width = (100 - clamped) + '%';

  let label, bg, color, noteText;
  if (clamped < 35) {{
    label = '✓ Carb-driven';
    bg = '#E8F5F1'; color = '#2E8B6E';
    fill.style.background = '#2E8B6E';
    noteText = 'Your gut is running primarily on dietary fiber — the beneficial fermentation chain is active and dominant.';
  }} else if (clamped < 60) {{
    label = '↔ Transitional';
    bg = '#FBF1E4'; color = '#C97C2A';
    fill.style.background = '#C97C2A';
    noteText = 'Your ecosystem is in a transitional state — fiber fermentation is present but not fully dominant. Strengthening the fiber chain would shift the balance.';
  }} else {{
    label = '⚠ Protein-driven';
    bg = '#FCECEA'; color = '#C24B3A';
    fill.style.background = '#C24B3A';
    noteText = 'Protein fermentation is exerting significant pressure on your ecosystem. The fiber pathway needs support to reclaim dominance.';
  }}

  badge.textContent  = label;
  badge.style.background = bg;
  badge.style.color  = color;
  note.textContent   = noteText;
}}

// ── Dynamic callout ───────────────────────────────────────────────────────────────────────────────
function updateCallout(guilds, prev) {{
  const el = document.getElementById('dynamic-callout');
  if (!el) return;

  const fdLow  = ['below','critical'].includes(getStatus('fd', guilds.fd));
  const pgHigh = ['above','critical'].includes(getStatus('pg', guilds.pg));
  const bpOk   = getStatus('bp', guilds.bp) === 'ok';
  const improving = prev && (guilds.fd > prev.fd || guilds.bp > prev.bp);

  let icon, title, text, bgClass;

  if (fdLow && pgHigh) {{
    icon = '⚠️'; title = 'Ecosystem under pressure';
    text = 'Fiber-processing capacity is below optimal while protein fermenters are elevated. This combination can push the ecosystem toward a less favorable metabolic state — it is the primary target for your protocol.';
    bgClass = 'callout-warn';
  }} else if (fdLow && !pgHigh) {{
    icon = '🌱'; title = 'Fiber chain has room to grow';
    text = 'Your fiber-processing bacteria are below their optimal range. The rest of the chain — Bifidobacteria, Cross-Feeders, Butyrate Producers — is ready to amplify as soon as upstream capacity improves. This is the most impactful area to support.';
    bgClass = 'callout-info';
  }} else if (!fdLow && pgHigh) {{
    icon = '📊'; title = 'Protein fermentation elevated';
    text = 'Your fiber chain is performing well, but protein fermenters are above their target range. Keeping the fiber pathway strong is the best way to outcompete them for ecological space.';
    bgClass = 'callout-info';
  }} else {{
    icon = '💡'; title = 'Good news';
    text = 'Your gut bacteria community is well-balanced and stable, with strong connector bacteria that help different teams work together smoothly during daily changes.';
    bgClass = 'callout-good';
  }}

  const progressNote = (improving && prev)
    ? ` Your ${{guilds.fd > prev.fd ? 'Fiber Degraders' : 'Butyrate Producers'}} have increased since your last test — the trend is moving in the right direction.`
    : '';

  el.className = 'callout';
  el.innerHTML = `${{icon}} <strong>${{title}}:</strong>${{progressNote ? ` ${{progressNote}}` : ''}} ${{text}}`;

  if (bgClass === 'callout-warn') {{
    el.style.background = 'var(--amber-lt)';
    el.style.borderLeftColor = 'var(--amber)';
    el.style.color = 'var(--mid)';
  }} else if (bgClass === 'callout-good') {{
    el.style.background = 'var(--green-lt)';
    el.style.borderLeftColor = 'var(--green)';
    el.style.color = 'var(--mid)';
  }} else {{
    el.style.background = 'var(--blue-lt)';
    el.style.borderLeftColor = 'var(--blue)';
    el.style.color = 'var(--mid)';
  }}
}}

// ── Full update ──────────────────────────────────────────────────────────────────────────────────
function hrUpdate(idx) {{
  currentTpIdx = idx;
  const tp   = HR_TPS[idx];
  const prev = idx > 0 ? HR_TPS[idx-1] : null;
  const badge = document.getElementById('hr-tp-badge');
  if (badge) badge.textContent = tp.label;
  updateSVG(tp.guilds, prev ? prev.guilds : null);
  updateBars(tp.guilds);
  updateCallout(tp.guilds, prev ? prev.guilds : null);
  updateBanner(idx);
  updateQPills(tp.guilds);
  updateDominance(tp.guilds);
}}

// ── Init slider ──────────────────────────────────────────────────────────────────────────────────
const ticks = document.getElementById('hr-tp-ticks');
if (ticks) {{
  HR_TPS.forEach(tp => {{
    const t = document.createElement('span');
    t.className = 'tp-tick';
    t.textContent = tp.label.split('—')[0].trim();
    ticks.appendChild(t);
  }});
}}
const hrSlider = document.getElementById('hr-tp-slider');
if (hrSlider) {{
  hrSlider.max = HR_TPS.length - 1;
  hrSlider.addEventListener('input', e => hrUpdate(parseInt(e.target.value)));
}}
hrUpdate(0);
updateCoverScore();
</script>
</body>
</html>''')

    return ''.join(parts)


# ════════════════════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATION
# ════════════════════════════════════════════════════════════════════════════════

def process_sample(sample_dir: str, no_llm: bool = False,
                   model_id: str = 'eu.anthropic.claude-sonnet-4-20250514-v1:0',
                   region: str = 'eu-west-1',
                   elicit_key: str = '',
                   use_cached: bool = False) -> str:
    """
    Generate the health report HTML for a single sample.

    Two-phase architecture
    ─────────────────────
    Phase 1 (skipped when use_cached=True):
      • Load raw data, compute all derived values, run LLM calls.
      • Persist everything to health_report_interpretations_{id}.json.

    Phase 2 (always runs):
      • Load the data bundle (from memory after Phase 1, or from the
        cached JSON when use_cached=True).
      • Render final HTML from generate_html().

    use_cached=True lets you re-run HTML formatting/layout changes with
    zero LLM API cost as long as the interpretations JSON already exists.
    """
    sample_id = os.path.basename(sample_dir.rstrip('/'))

    # ── Phase 1: load from cache (--use-cached) ──────────────────────────────
    if use_cached:
        cache_path = _interpretations_json_path(sample_dir, sample_id)
        logger.info(f"Generating health report (cached mode): {sample_id}")
        logger.info(f"  Loading interpretations from: {cache_path}")
        data = load_interpretations_json(sample_dir, sample_id)
        if data is None:
            raise FileNotFoundError(
                f"No interpretations cache found at {cache_path}. "
                "Run without --use-cached first to generate and persist the interpretations."
            )
        logger.info("  ✓ Interpretations loaded from cache — skipping all LLM calls")

    # ── Phase 1: compute everything from scratch ──────────────────────────────
    else:
        logger.info(f"Generating health report: {sample_id}")

        # Resolve Elicit key: CLI arg → env var → .env file
        resolved_elicit_key = elicit_key or os.environ.get('ELICIT_API_KEY', '')
        if not resolved_elicit_key:
            # Try loading from science-engine/.env (one level up from this script)
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
        if resolved_elicit_key:
            logger.info("  Elicit API key found — scientific literature enrichment enabled")
        else:
            logger.info("  No Elicit API key — Section 3 will use knowledge base only")

        # Load raw data sources
        logger.info("  Loading data...")
        raw = load_data(sample_dir)

        # Compute derived values (all deterministic)
        logger.info("  Computing circle scores...")
        circle_scores = compute_circle_scores(raw['analysis'])
        logger.info(f"  Circle scores: {circle_scores}")

        logger.info("  Computing strengths and challenges...")
        sw = compute_strengths_challenges(raw['analysis'], circle_scores)
        logger.info(f"  Strengths: {len(sw['strengths'])}  Challenges: {len(sw['challenges'])}")

        logger.info("  Extracting profile...")
        profile = extract_profile(raw['questionnaire'], raw['formulation'])

        logger.info("  Extracting good news...")
        good_news = extract_good_news(raw['analysis'])

        logger.info("  Building timeline...")
        timeline_phases = build_timeline_phases(raw['analysis'], raw['formulation'])

        logger.info("  Building supplement cards...")
        supplement_cards = build_supplement_cards(raw['formulation'], raw['analysis'])
        logger.info(f"  Supplement units: {len(supplement_cards)}")

        logger.info("  Building goal cards...")
        goal_cards = build_goal_cards(raw['questionnaire'], raw['formulation'], raw['analysis'])
        logger.info(f"  Goal cards: {len(goal_cards)}")

        # LLM calls — Section 3 (and optional Elicit enrichment)
        logger.info("  Building root cause section (Section 3)...")
        root_cause_data = build_root_cause_section(
            raw['questionnaire'], raw['analysis'],
            no_llm=no_llm, model_id=model_id, region=region,
            elicit_key=resolved_elicit_key,
        )
        n_dev = len(root_cause_data.get('deviation_cards', []))
        n_aw = len(root_cause_data.get('awareness_chips', []))
        logger.info(f"  Section 3: {n_dev} deviation card(s), {n_aw} awareness chip(s)")

        # Generate LLM-personalised "Why you're taking it" texts for each supplement unit
        supplement_why_texts = {}
        if not no_llm and raw['formulation']:
            logger.info("  Generating supplement why-texts (LLM)...")
            # Collect active deviations for context
            _active_devs = _detect_microbiome_deviations(raw['analysis'])
            supplement_why_texts = _generate_supplement_why_texts(
                raw['formulation'], profile, _active_devs,
                model_id=model_id, region=region,
            )
            logger.info(f"  Supplement why-texts: {len(supplement_why_texts)} units covered")
            # Apply LLM texts to supplement cards (override deterministic fallback)
            for card in supplement_cards:
                uk = card.get('key', '')
                if uk in supplement_why_texts and supplement_why_texts[uk].strip():
                    card['why'] = supplement_why_texts[uk].strip()

        # Generate lifestyle recommendations (Elicit-powered + LLM)
        lifestyle_recs = []
        lifestyle_cited_papers = []
        if not no_llm:
            logger.info("  Generating lifestyle recommendations (Elicit + LLM)...")
            questionnaire_context = _extract_questionnaire_context(raw['questionnaire'])
            lifestyle_recs, lifestyle_cited_papers = _generate_lifestyle_recommendations(
                deviations=root_cause_data.get('deviation_cards', []),
                questionnaire_context=questionnaire_context,
                analysis=raw['analysis'],
                elicit_key=resolved_elicit_key,
                model_id=model_id,
                region=region,
            )
            logger.info(f"  Lifestyle: {len(lifestyle_recs)} recommendations, {len(lifestyle_cited_papers)} cited papers")

        # Merge cited papers: Section 3 + lifestyle (deduplicated by title)
        all_cited_papers = list(root_cause_data.get('cited_papers', []))
        seen_titles = {p.get('title', '') for p in all_cited_papers}
        for p in lifestyle_cited_papers:
            if p.get('title') and p['title'] not in seen_titles:
                all_cited_papers.append(p)
                seen_titles.add(p['title'])

        # ── Extract v3.0 flat microbiome fields from analysis ─────────────────
        from assemble_interpretations import (
            extract_overall_score, extract_bacterial_groups,
            extract_metabolic_dials, extract_ecological_metrics,
            extract_safety_profile, build_guild_timepoints,
            extract_report_date, compute_score_summary,
            extract_protocol_summary,
        )
        report_date = extract_report_date(raw['analysis'])
        overall_score = extract_overall_score(raw['analysis'])
        bacterial_groups = extract_bacterial_groups(raw['analysis'])
        metabolic_dials = extract_metabolic_dials(raw['analysis'])
        ecological_metrics = extract_ecological_metrics(raw['analysis'])
        safety_profile = extract_safety_profile(raw['analysis'])
        guild_timepoints = build_guild_timepoints(raw['analysis'], report_date)
        score_summary = compute_score_summary(
            overall_score.get('total', 0), sw.get('distinct_areas', [])
        )
        protocol_summary = extract_protocol_summary(raw['formulation']) if raw['formulation'] else {}

        # Assemble full data bundle
        data = {
            **raw,
            # v3.0 flat microbiome fields
            'schema_version': '3.0',
            'report_date': report_date,
            'overall_score': overall_score,
            'score_summary': score_summary,
            'bacterial_groups': bacterial_groups,
            'metabolic_dials': metabolic_dials,
            'ecological_metrics': ecological_metrics,
            'safety_profile': safety_profile,
            'guild_timepoints': guild_timepoints,
            'protocol_summary': protocol_summary,
            # LLM / derived layer
            'profile': profile,
            'circle_scores': circle_scores,
            'strengths_challenges': sw,
            'good_news': good_news,
            'timeline_phases': timeline_phases,
            'supplement_cards': supplement_cards,
            'goal_cards': goal_cards,
            'root_cause_data': root_cause_data,
            'cited_papers': all_cited_papers,
            'lifestyle_recommendations': lifestyle_recs,
        }

        # LLM consistency check (Phase 1 — while we still have full context)
        if not no_llm:
            logger.info("  Running LLM consistency check...")
            run_consistency_check(data, model_id=model_id, region=region)
        else:
            logger.info("  Skipping LLM consistency check (--no-llm)")
            print(f"\n╔{'═'*60}")
            print(f"║  REPORT CONSISTENCY CHECK — Sample {sample_id}")
            print(f"╠{'═'*60}")
            print(f"║  ⚠️  Skipped (--no-llm flag active) — manual review recommended")
            print(f"╚{'═'*60}\n")

        # ── Persist interpretations JSON (end of Phase 1) ────────────────────
        interp_path = save_interpretations_json(data, sample_dir)
        logger.info(f"  ✓ Interpretations saved: {interp_path}")

    # ── Phase 2: render HTML from data bundle ─────────────────────────────────
    logger.info("  Generating HTML...")
    html = generate_html(data)

    output_dir = os.path.join(sample_dir, 'reports', 'reports_html')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'health_report_{sample_id}.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"  ✓ HTML saved: {output_path}")
    return output_path


def process_batch(batch_dir: str, **kwargs):
    """Process all samples in a batch directory."""
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, '*')))
    sample_dirs = [d for d in sample_dirs if os.path.isdir(d) and not d.endswith('.DS_Store')]

    logger.info(f"Batch processing: {len(sample_dirs)} samples in {batch_dir}")
    results = []

    for sample_dir in sample_dirs:
        sample_id = os.path.basename(sample_dir)
        reports_dir = os.path.join(sample_dir, 'reports', 'reports_json')
        if not os.path.exists(reports_dir):
            logger.warning(f"  Skipping {sample_id} — no reports directory")
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
        description='Generate premium client-facing microbiome health report HTML',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--no-llm', action='store_true',
                        help='Skip LLM calls (Section 3 uses KB-only fallback, no consistency check)')
    parser.add_argument('--use-cached', action='store_true',
                        help=(
                            'Skip all LLM calls and reuse the existing '
                            'health_report_interpretations_{id}.json cache. '
                            'Renders fresh HTML from the cached interpretations — '
                            'zero API cost. Requires a prior non-cached run to exist.'
                        ))
    parser.add_argument('--model-id', default='eu.anthropic.claude-sonnet-4-20250514-v1:0',
                        help='Bedrock model ID')
    parser.add_argument('--region', default='eu-west-1', help='AWS region')
    parser.add_argument('--elicit-key', default='',
                        help='Elicit API key for scientific literature enrichment in Section 3. '
                             'Can also be set via ELICIT_API_KEY env var.')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.use_cached and args.no_llm:
        parser.error('--use-cached and --no-llm are mutually exclusive')

    kwargs = {
        'no_llm': args.no_llm,
        'use_cached': args.use_cached,
        'model_id': args.model_id,
        'region': args.region,
        'elicit_key': args.elicit_key,
    }

    if args.sample_dir:
        output_path = process_sample(args.sample_dir, **kwargs)
        print(f"\n✅ Health report saved: {output_path}")
    elif args.batch_dir:
        process_batch(args.batch_dir, **kwargs)


if __name__ == '__main__':
    main()
