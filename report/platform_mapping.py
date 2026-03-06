"""
platform_mapping.py — Transform microbiome analysis JSON into platform-ready API payload

Takes the comprehensive _microbiome_analysis.json and restructures it into
a {sample_id}_platform.json that maps 1:1 to the platform's 5 tabs:
  - Overview
  - Bacterial Groups
  - Root Causes
  - Vitamins
  - Action Plan

All text uses client-level interpretations only (never scientific/annotated).
"""

import json
import os
import sys
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Import guild constants from shared module — single source of truth
# (consistent with formulation pipeline and all report modules)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
from guild_priority import (
    GUILD_ORDER,
    GUILD_CLIENT_NAMES as GUILD_DISPLAY_NAMES,
    GUILD_DISPLAY_NAMES as GUILD_SCIENTIFIC_NAMES,
)

# Platform dial keys mapping (overview_tab keys → platform keys)
DIAL_KEYS = {
    'main_fuel': 'main_fuel',
    'fermentation_efficiency': 'fermentation_efficiency',
    'gut_lining_dependence': 'gut_lining_dependence',
    'mucus_dependency': 'gut_lining_dependence',
    'harsh_byproducts': 'harsh_byproducts',
    'putrefaction_pressure': 'harsh_byproducts',
}


def _load_static_content() -> dict:
    """Load static content from knowledge base."""
    kb_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'knowledge_base', 'static_content.json'
    )
    try:
        with open(kb_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load static content: {e}")
        return {}


def _load_guild_knowledge_base() -> dict:
    """Load guild interpretation knowledge base."""
    kb_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'knowledge_base', 'guild_interpretation.json'
    )
    try:
        with open(kb_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load guild KB: {e}")
        return {}


# ══════════════════════════════════════════════════════════════
#    OVERVIEW TAB MAPPING
# ══════════════════════════════════════════════════════════════

def _extract_non_expert(value):
    """Extract non_expert version from a dual {scientific, non_expert} field.
    If it's already a plain string, return as-is."""
    if isinstance(value, dict) and 'non_expert' in value:
        return value['non_expert']
    return value


def _map_overview_tab(analysis: dict, static: dict) -> dict:
    """Map analysis data to overview tab platform structure.
    All text fields use non_expert versions only."""
    overview = analysis.get('overview', {})
    static_ov = static.get('overview_tab', {})

    # Gut health at a glance
    glance = overview.get('gut_health_glance', {})
    score_data = glance.get('overall_score', {})

    gut_health_glance = {
        'summary_sentence': _extract_non_expert(glance.get('summary_sentence', '')),
        'overall_score': {
            'total': score_data.get('total', 0),
            'band': score_data.get('band', ''),
            'band_description': score_data.get('band_description', ''),
            'score_drivers': score_data.get('score_drivers', {}),
        },
        'pillars': score_data.get('pillars', {}),
    }

    # Pillars — extract non_expert for platform
    pillars_raw = score_data.get('pillars', {})
    pillars_platform = {}
    for pk, pv in pillars_raw.items():
        pillars_platform[pk] = {
            'score': pv.get('score', 0),
            'max': pv.get('max', 0),
            'description': pv.get('non_expert', ''),
        }
    gut_health_glance['pillars'] = pillars_platform

    # What's happening — extract non_expert from balance/diversity
    whats = overview.get('whats_happening', {})
    ob = whats.get('overall_balance', {})
    dr = whats.get('diversity_resilience', {})
    whats_happening = {
        'overall_balance': {
            'label': ob.get('label', ''),
            'description': _extract_non_expert(ob) if 'non_expert' in ob else '',
        },
        'diversity_resilience': {
            'label': dr.get('label', ''),
            'description': _extract_non_expert(dr) if 'non_expert' in dr else '',
        },
        'key_strengths': [_extract_non_expert(s) if isinstance(s, dict) else s for s in whats.get('key_strengths', [])],
        'key_opportunities': [_extract_non_expert(o) if isinstance(o, dict) else o for o in whats.get('key_opportunities', [])],
        'summary_sentence': _extract_non_expert(whats.get('summary_sentence', '')),
    }

    # Metabolic dials
    dials_raw = overview.get('metabolic_dials', {})
    dials = {}
    for raw_key, platform_key in DIAL_KEYS.items():
        if raw_key in dials_raw:
            d = dials_raw[raw_key]
            dials[platform_key] = {
                'label': d.get('label', ''),
                'state': d.get('state', ''),
                'value': d.get('value'),
                'description': d.get('description', ''),
                'context': d.get('context', ''),
            }

    metabolic_dials = {
        'intro_text': static_ov.get('processing_food_intro', {}).get('text', ''),
        'dials': dials,
    }

    # What this means — extract non_expert from dual fields
    wtm = overview.get('what_this_means', {})
    good_news_raw = wtm.get('good_news', {})
    good_news_platform = {}
    for k, v in good_news_raw.items():
        good_news_platform[k] = _extract_non_expert(v) if isinstance(v, dict) else v

    possible_impacts_raw = wtm.get('possible_impacts', [])
    if isinstance(possible_impacts_raw, dict) and 'non_expert' in possible_impacts_raw:
        possible_impacts_platform = possible_impacts_raw['non_expert']
    elif isinstance(possible_impacts_raw, list):
        possible_impacts_platform = possible_impacts_raw
    else:
        possible_impacts_platform = []

    what_this_means = {
        'good_news': good_news_platform,
        'possible_impacts': possible_impacts_platform,
        'is_something_wrong': _extract_non_expert(wtm.get('is_something_wrong', '')),
        'can_this_be_fixed': _extract_non_expert(wtm.get('can_this_be_fixed', '')),
    }

    # Why this matters (static)
    why_matters = static_ov.get('why_this_matters', {})

    return {
        'gut_health_glance': gut_health_glance,
        'whats_happening': whats_happening,
        'why_we_look_at_this': static_ov.get('why_we_look_at_this', {}),
        'metabolic_dials': metabolic_dials,
        'what_this_means': what_this_means,
        'why_this_matters': why_matters,
    }


# ══════════════════════════════════════════════════════════════
#    BACTERIAL GROUPS TAB MAPPING
# ══════════════════════════════════════════════════════════════

def _map_bacterial_groups_tab(analysis: dict, data: dict,
                               static: dict, guild_kb: dict,
                               capacity_100: dict) -> dict:
    """Map analysis data to bacterial groups tab platform structure."""
    static_bg = static.get('bacterial_groups_tab', {})
    guild_defs = guild_kb.get('guild_definitions', {})

    # Get bacterial groups from analysis JSON (the report version)
    bg_data = {}

    # Try from the report-format bacterial_groups first
    if 'bacterial_groups' in analysis:
        bg_data = analysis['bacterial_groups']

    # Also check the overview structure for guild data
    guilds_from_data = data.get('guilds', {}) if data else {}

    guilds_list = []
    for step_idx, guild_key in enumerate(GUILD_ORDER, 1):
        display = GUILD_DISPLAY_NAMES.get(guild_key, guild_key)

        # Find guild data from report JSON
        guild_info = None
        for bg_name, bg_info in bg_data.items():
            normalized_bg = GUILD_DISPLAY_NAMES.get(bg_name, bg_name)
            normalized_gk = GUILD_DISPLAY_NAMES.get(guild_key, guild_key)
            if normalized_bg == normalized_gk or bg_name in guild_key or guild_key in bg_name:
                guild_info = bg_info
                break

        # Get functional summary from knowledge base
        func_summary = ''
        for kb_name, kb_def in guild_defs.items():
            if kb_name in guild_key or guild_key in kb_name:
                func_summary = kb_def.get('client_description', '')
                break

        # Get capacity data
        cap = capacity_100.get(guild_key, {})

        # Build guild entry (guild_key enables cross-referencing with formulation data)
        entry = {
            'step': step_idx,
            'guild_key': guild_key,
            'name': display,
            'functional_summary': func_summary,
            'capacity': {
                'actual_players': cap.get('actual_players', 0),
                'optimal_players': cap.get('optimal_players', 0),
                'min_players': cap.get('min_players', 0),
                'max_players': cap.get('max_players', 0),
                'actual_pct': cap.get('actual_pct', 0),
                'optimal_pct': cap.get('optimal_pct', 0),
            },
            'status': guild_info.get('status', '') if guild_info else '',
            'healthy_range': guild_info.get('healthy_range', '') if guild_info else '',
            'impact_explanation': '',
            'additional_note': '',
        }

        # Use CLIENT interpretation only (never scientific)
        if guild_info:
            client_interp = guild_info.get('client_interpretation', '')
            if not client_interp or client_interp == '[LLM skipped]':
                # Fall back to a generated summary
                client_interp = _generate_fallback_interpretation(
                    display, guild_info, cap
                )
            entry['impact_explanation'] = client_interp

            # Additional note from CLR + evenness
            clr_status = guild_info.get('clr_status', '')
            evenness_status = guild_info.get('evenness_status', '')
            notes = []
            if clr_status and clr_status != 'Undefined (abundance <1%)':
                notes.append(f"Competitive position: {clr_status}")
            if evenness_status:
                notes.append(f"Team diversity: {evenness_status}")
            entry['additional_note'] = '. '.join(notes) if notes else ''

        guilds_list.append(entry)

    # Flat structure — title + intro text + guilds list (no nested chain/capacity sections)
    intro_data = static_bg.get('intro', {})
    return {
        'title': intro_data.get('title', 'Your Gut Bacteria Groups'),
        'intro_text': intro_data.get('text', ''),
        'guilds': guilds_list,
    }


def _generate_fallback_interpretation(display: str, guild_info: dict,
                                       cap: dict) -> str:
    """Generate a simple fallback interpretation when LLM text is unavailable."""
    status = guild_info.get('status', '')
    actual = cap.get('actual_players', 0)
    optimal = cap.get('optimal_players', 0)

    if status == 'Within range':
        return f'Your {display.lower()} team is within the healthy range and functioning well.'
    elif status == 'Below range':
        return f'Your {display.lower()} team has {actual} workers where the ideal is {optimal}. This team could use more support to reach full capacity.'
    elif status == 'Above range':
        return f'Your {display.lower()} team is larger than typical, which may need monitoring.'
    elif 'CRITICAL' in status:
        return f'Your {display.lower()} team is critically depleted and needs priority attention.'
    else:
        return f'Your {display.lower()} team is at {actual} out of an ideal {optimal} workers.'


# ══════════════════════════════════════════════════════════════
#    ROOT CAUSES TAB MAPPING
# ══════════════════════════════════════════════════════════════

def _map_root_causes_tab(root_causes: dict, narratives: dict,
                          static: dict) -> dict:
    """Map root causes data to platform structure. Uses non_expert versions."""
    static_rc = static.get('root_causes_tab', {})

    # Extract non_expert insights
    raw_insights = narratives.get('root_causes_insights', [])
    platform_insights = []
    for insight in raw_insights:
        if isinstance(insight, dict):
            platform_insights.append({
                'title': insight.get('title', ''),
                'explanation': insight.get('non_expert', insight.get('explanation', '')),
            })
        else:
            platform_insights.append(insight)

    # Extract non_expert from enriched fields
    primary_pattern = root_causes.get('primary_pattern', {})
    pp_ne = primary_pattern.get('non_expert', '') if isinstance(primary_pattern, dict) else ''

    metabolic_evidence = []
    for ev in root_causes.get('metabolic_evidence', []):
        link = ev.get('root_cause_link', {})
        metabolic_evidence.append({
            'dial': ev.get('dial', ''),
            'label': ev.get('label', ''),
            'state': ev.get('state', ''),
            'explanation': link.get('non_expert', '') if isinstance(link, dict) else '',
        })

    lifestyle = root_causes.get('lifestyle_inference', {})
    lifestyle_platform = {
        'pattern': lifestyle.get('pattern', {}).get('non_expert', '') if isinstance(lifestyle.get('pattern'), dict) else '',
        'evidence': [e.get('non_expert', '') for e in lifestyle.get('evidence', []) if isinstance(e, dict)],
        'disclaimer': lifestyle.get('disclaimer', {}).get('non_expert', '') if isinstance(lifestyle.get('disclaimer'), dict) else '',
    }

    feedback_loops = []
    for loop in root_causes.get('feedback_loops', []):
        name = loop.get('name', {})
        hi = loop.get('health_impact', {})
        feedback_loops.append({
            'name': name.get('non_expert', '') if isinstance(name, dict) else str(name),
            'chain': loop.get('chain', []),
            'status': loop.get('status', ''),
            'health_impact': hi.get('non_expert', '') if isinstance(hi, dict) else str(hi) if hi else '',
        })

    return {
        'title': static_rc.get('title', 'Underlying Causes of Imbalance'),
        'primary_pattern': pp_ne,
        'primary_diagnosis': _extract_non_expert(narratives.get('root_causes_diagnosis', '')),
        'how_we_can_tell': {
            'diagnostic_flags': root_causes.get('diagnostic_flags', []),
            'disclaimer': static_rc.get('disclaimer', ''),
        },
        'metabolic_evidence': metabolic_evidence,
        'lifestyle_inference': lifestyle_platform,
        'feedback_loops': feedback_loops,
        'key_insights': platform_insights,
        'conclusion': {
            'what_this_means': _extract_non_expert(narratives.get('root_causes_conclusion', '')),
            'reversibility': root_causes.get('reversibility', {}),
        },
        'trophic_impact': root_causes.get('trophic_impact', {}),
        'causal_narrative': root_causes.get('causal_narrative', {}).get('non_expert', ''),
        'educational_intro': 'You\'ve probably heard that eating fiber is good and too much protein can be tough on your gut — but why exactly? Your gut bacteria are the reason. They form specialized teams that process what you eat, and their balance determines how well your body converts food into energy, protects your gut lining, and manages inflammation.',
    }


# ══════════════════════════════════════════════════════════════
#    VITAMINS TAB MAPPING
# ══════════════════════════════════════════════════════════════

def _map_vitamins_tab(vitamin_data: dict, static: dict) -> dict:
    """Map vitamin risk data to platform structure."""
    static_vit = static.get('vitamins_tab', {})
    vitamin_details = static_vit.get('vitamin_details', {})
    status_labels = static_vit.get('status_labels', {})

    # Determine which vitamins have good production
    robust_production = []
    vitamins_list = []

    # Map each vitamin
    for vkey in ['folate', 'B_complex', 'biotin', 'B12']:
        vdata = vitamin_data.get(vkey, {})
        if not isinstance(vdata, dict):
            continue

        risk_level = vdata.get('risk_level', 0)
        risk_label = vdata.get('risk_label', 'Unknown')
        assessment = vdata.get('assessment', '')

        # Get static details
        details = vitamin_details.get(vkey, {})

        # Map risk level to status
        status = status_labels.get(str(risk_level), risk_label)

        vitamin_entry = {
            'key': vkey,
            'display_name': details.get('display_name', vkey),
            'status': status,
            'status_level': risk_label.lower().replace('-', '_').replace(' ', '_'),
            'risk_level': risk_level,
            'role': details.get('role', ''),
            'food_sources': details.get('food_sources', []),
            'assessment': assessment,
        }

        # Add biotin-specific info
        if vkey == 'biotin':
            vitamin_entry['producers_detected'] = vdata.get('producers_detected', 0)
            vitamin_entry['producers_total'] = vdata.get('producers_total', 4)

        # Add B12-specific info
        if vkey == 'B12':
            vitamin_entry['akkermansia_pct'] = vdata.get('akkermansia_pct', 0)

        vitamins_list.append(vitamin_entry)

        if risk_level == 0:
            robust_production.append(details.get('display_name', vkey))

    return {
        'title': static_vit.get('title', 'Vitamins'),
        'intro': static_vit.get('intro', ''),
        'good_news': {
            'robust_production': robust_production,
            'functional_roles': static_vit.get('functional_roles', []),
        },
        'vitamins': vitamins_list,
    }


# ══════════════════════════════════════════════════════════════
#    ACTION PLAN TAB MAPPING
# ══════════════════════════════════════════════════════════════

def _map_action_plan_tab(action_plan: dict, static: dict,
                          bacterial_groups: dict = None) -> dict:
    """Map action plan data to platform structure. Includes monitor guilds."""
    static_ap = static.get('action_plan_tab', {})

    # Build monitor guilds list from bacterial_groups data
    monitor_guilds = []
    if bacterial_groups:
        for gname, gdata in bacterial_groups.items():
            if gdata.get('priority_level') == 'Monitor':
                display = GUILD_DISPLAY_NAMES.get(gname, gname)
                monitor_guilds.append({
                    'name': display,
                    'status': gdata.get('status', ''),
                    'abundance': gdata.get('abundance', 0),
                    'priority_level': 'Monitor',
                })

    return {
        'title': static_ap.get('title', 'Your Personalized Action Plan'),
        'intro': static_ap.get('intro', ''),
        'reversibility': action_plan.get('reversibility', {}),
        'steps': action_plan.get('intervention_steps', []),
        'monitor_guilds': monitor_guilds,
        'vitamin_check': action_plan.get('vitamin_check'),
        'forecast': action_plan.get('forecast', []),
        'next_steps': action_plan.get('next_steps', []),
        'reversibility_note': static_ap.get('reversibility_note', ''),
    }


# ══════════════════════════════════════════════════════════════
#    MAIN ASSEMBLY FUNCTION
# ══════════════════════════════════════════════════════════════

def build_platform_json(analysis_json: dict, data: dict = None,
                         root_causes: dict = None,
                         action_plan: dict = None,
                         narratives: dict = None) -> dict:
    """
    Build the complete platform-ready JSON payload.

    Args:
        analysis_json: The comprehensive _microbiome_analysis.json content
        data: Raw parsed metrics data (for guild details)
        root_causes: Output from root_causes_fields.compute_root_causes_fields()
        action_plan: Output from action_plan_fields.compute_action_plan_fields()
        narratives: LLM-generated narrative dict (from narratives.py)

    Returns:
        Platform-ready JSON dict with all 5 tabs
    """
    static = _load_static_content()
    guild_kb = _load_guild_knowledge_base()

    if narratives is None:
        narratives = {}
    if root_causes is None:
        root_causes = {}
    if action_plan is None:
        action_plan = {}

    # Get capacity data
    from action_plan_fields import compute_capacity_100
    guilds = data.get('guilds', {}) if data else {}
    capacity_100 = compute_capacity_100(guilds)

    # Extract vitamin data from analysis JSON
    vitamin_data = analysis_json.get('vitamin_synthesis', {})

    # Build metadata
    report_meta = analysis_json.get('report_metadata', {})
    metadata = {
        'sample_id': report_meta.get('sample_id', ''),
        'report_date': report_meta.get('report_date', ''),
        'generated_at': datetime.now().isoformat(),
        'platform_schema_version': '1.0',
        'source_algorithm_version': report_meta.get('algorithm_version',
                                                     report_meta.get('score_algorithm_version', '')),
        'llm_model': report_meta.get('llm_model'),
    }

    # Assemble all 5 tabs
    # Get guild scenarios from overview fields (computed during report generation)
    guild_scenarios = analysis_json.get('guild_scenarios', [])

    platform = {
        'metadata': metadata,
        'overview_tab': _map_overview_tab(analysis_json, static),
        'bacterial_groups_tab': _map_bacterial_groups_tab(
            analysis_json, data, static, guild_kb, capacity_100
        ),
        'root_causes_tab': _map_root_causes_tab(root_causes, narratives, static),
        'vitamins_tab': _map_vitamins_tab(vitamin_data, static),
        'action_plan_tab': _map_action_plan_tab(
            action_plan, static,
            bacterial_groups=analysis_json.get('bacterial_groups', {})
        ),
        'guild_scenarios': guild_scenarios,
    }

    return platform


def build_platform_from_files(analysis_path: str, report_path: str = None,
                               data: dict = None) -> dict:
    """
    Build platform JSON from saved file paths.

    Convenience function for building from existing JSON files.
    """
    with open(analysis_path) as f:
        analysis_json = json.load(f)

    report_json = None
    if report_path:
        with open(report_path) as f:
            report_json = json.load(f)

    # Merge report data into analysis if available
    if report_json:
        # Use report's bacterial_groups (has LLM interpretations)
        if 'bacterial_groups' in report_json:
            analysis_json['bacterial_groups'] = report_json['bacterial_groups']
        # Use report's key_messages
        if 'key_messages' in report_json:
            analysis_json['key_messages'] = report_json['key_messages']

    return build_platform_json(analysis_json, data=data)
