"""
action_plan_fields.py — Deterministic action plan computation for platform

Generates prioritized intervention steps based on guild deficits.
Supplement recommendations are TBD — will be populated from the
supplement prediction pipeline once automated.

Outputs:
  - Prioritized intervention steps (ordered by impact)
  - Current vs target capacity (100-player scale)
  - Timeline estimates
  - Reversibility assessment
  - Forecast projections
"""

import json
import os
from scoring import GUILD_CONFIG


# ══════════════════════════════════════════════════════════════
#    CONSTANTS & CONFIGURATION
# ══════════════════════════════════════════════════════════════

# Scale factor: sum of optimal values → 100
_OPTIMAL_SUM = sum(cfg[2] for cfg in GUILD_CONFIG.values())  # 17.5+40+9+6+2.5+3 = 78
SCALE_FACTOR = 100.0 / _OPTIMAL_SUM

# Fixed guild display order for platform
GUILD_ORDER = [
    'Fiber Degraders',
    'HMO/Oligosaccharide-Utilising Bifidobacteria',
    'Cross-Feeders',
    'Butyrate Producers',
    'Proteolytic Dysbiosis Guild',
    'Mucin Degraders',
]

GUILD_DISPLAY_NAMES = {
    'Butyrate Producers': 'Gut-Lining Energy Producers',
    'Fiber Degraders': 'Fiber-Processing Bacteria',
    'Cross-Feeders': 'Intermediate Processors',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'Mucus-Layer Bacteria',
    'Proteolytic Dysbiosis Guild': 'Protein-Fermenting Bacteria',
}

# Timeline estimates per guild
TIMELINE_MAP = {
    'Fiber Degraders': 'Ongoing — gradual ecological shift',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': '4-6 weeks to detect initial changes',
    'Cross-Feeders': '6-12 weeks for measurable improvement',
    'Butyrate Producers': '4-6 weeks with adequate substrate',
    'Proteolytic Dysbiosis Guild': '4-6 weeks as competing guilds strengthen',
    'Mucin Degraders': '8-12 weeks as fiber availability increases',
}

# Action plan step titles per guild
STEP_TITLES = {
    'Fiber Degraders': 'Expand Capacity',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Restart the Amplifier',
    'Cross-Feeders': 'Fix the Bottleneck',
    'Butyrate Producers': 'Boost Energy Production',
    'Proteolytic Dysbiosis Guild': 'Calm Protein Processing',
    'Mucin Degraders': 'Rebalance Mucus Turnover',
}

# Why descriptions per guild
WHY_DESCRIPTIONS = {
    'Fiber Degraders': 'Fiber-processing bacteria are the entry point of the fermentation chain. Expanding this team increases raw material supply for all downstream teams.',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria produce lactate — a critical intermediate fuel that amplifies SCFA production by 50-70%. Restoring them unlocks major efficiency gains.',
    'Cross-Feeders': 'Intermediate processors bridge the gap between raw materials and finished products. Without them, fermentation intermediates accumulate instead of being converted to beneficial compounds.',
    'Butyrate Producers': 'Butyrate is the primary energy source for gut lining cells and a key anti-inflammatory compound. Boosting these bacteria strengthens your gut barrier.',
    'Proteolytic Dysbiosis Guild': 'Reducing protein-fermenting bacteria lowers production of ammonia, hydrogen sulfide, and other harsh byproducts that stress the gut lining.',
    'Mucin Degraders': 'Rebalancing mucus-layer bacteria protects your gut barrier and ensures the mucus layer regenerates faster than it is consumed.',
}


# ══════════════════════════════════════════════════════════════
#    100-PLAYER CAPACITY CALCULATION
# ══════════════════════════════════════════════════════════════

def compute_capacity_100(guilds: dict) -> dict:
    """
    Compute the 100-player capacity for all guilds.

    Normalizes optimal values to sum to exactly 100.
    Scales actual values by the same factor.

    Returns dict of guild_name → {actual_players, optimal_players, ...}
    """
    result = {}

    for guild_key in GUILD_ORDER:
        config = GUILD_CONFIG.get(guild_key)
        if config is None:
            continue

        mn, mx, optimal, max_pts, gtype = config
        display = GUILD_DISPLAY_NAMES.get(guild_key, guild_key)

        # Find actual abundance from data
        actual_pct = 0.0
        for gname, gdata in guilds.items():
            if guild_key in gname or gname in guild_key:
                actual_pct = gdata['abundance']
                break

        actual_players = round(actual_pct * SCALE_FACTOR)
        optimal_players = round(optimal * SCALE_FACTOR)
        min_players = round(mn * SCALE_FACTOR)
        max_players = round(mx * SCALE_FACTOR)

        result[guild_key] = {
            'display_name': display,
            'actual_players': actual_players,
            'optimal_players': optimal_players,
            'min_players': min_players,
            'max_players': max_players,
            'actual_pct': round(actual_pct, 2),
            'optimal_pct': optimal,
            'range_min_pct': mn,
            'range_max_pct': mx,
            'guild_type': gtype,
        }

    return result


# ══════════════════════════════════════════════════════════════
#    INTERVENTION STEP GENERATION
# ══════════════════════════════════════════════════════════════

# ─── UNIFIED PRIORITY v2: importance × state × evenness ───────────────────────
# Uses shared/guild_priority.py as single source of truth.
# See formulation_automation/documentation/PRIORITY_SYSTEM_CHANGELOG.md
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
from guild_priority import compute_guild_priority, score_to_label, GUILD_ORDER as _SHARED_GUILD_ORDER, GUILD_CLIENT_NAMES as _SHARED_GUILD_CLIENT_NAMES


def _compute_priority_score(guild_key: str, gdata: dict, config: tuple) -> float:
    """
    Compute intervention priority score using shared guild_priority module.

    Delegates to compute_guild_priority() — single source of truth.
    Returns 0 if guild is healthy and needs no intervention.
    """
    mn, mx, optimal, max_pts, gtype = config
    abund = gdata['abundance']
    clr = gdata.get('clr')
    J = gdata.get('redundancy', 0.5)

    # Derive status string from abundance vs range (same logic as scoring.py)
    if abund == 0:
        status = 'Absent — CRITICAL' if gtype == 'beneficial' else 'Absent'
    elif abund < mn:
        status = 'Below range'
    elif abund > mx:
        status = 'Above range'
    else:
        status = 'Within range'

    priority = compute_guild_priority(guild_key, abund, status, clr, J)
    return priority['priority_score']


def generate_intervention_steps(data: dict) -> list:
    """
    Generate prioritized intervention steps.

    Only creates steps for guilds that need intervention.
    Steps are ordered by priority score (highest first).
    """
    guilds = data.get('guilds', {})
    capacity = compute_capacity_100(guilds)
    steps = []

    for guild_key in GUILD_ORDER:
        config = GUILD_CONFIG.get(guild_key)
        if config is None:
            continue

        mn, mx, optimal, max_pts, gtype = config
        display = GUILD_DISPLAY_NAMES.get(guild_key, guild_key)

        # Find guild data
        gdata = None
        for gname, gd in guilds.items():
            if guild_key in gname or gname in guild_key:
                gdata = gd
                break
        if gdata is None:
            continue

        priority = _compute_priority_score(guild_key, gdata, config)
        if priority <= 0:
            continue

        cap = capacity.get(guild_key, {})

        # Determine action direction
        if gtype == 'beneficial':
            action = 'expand'
            target_description = f'Target: {cap.get("min_players", 0)}-{cap.get("max_players", 0)} workers (healthy range)'
        else:
            action = 'reduce'
            target_description = f'Target: {cap.get("min_players", 0)}-{cap.get("max_players", 0)} workers (controlled range)'

        # NOTE: priority_level is derived AFTER sorting by _derive_priority_label()
        # to ensure labels are always consistent with step ordering.
        # The initial value here is a placeholder that gets overwritten.
        priority_level = 'TBD'

        steps.append({
            'guild_key': guild_key,
            'guild_display': display,
            'title': STEP_TITLES.get(guild_key, f'Address {display}'),
            'action': action,
            'priority_level': priority_level,
            'why': WHY_DESCRIPTIONS.get(guild_key, ''),
            'how': 'TBD — will be populated from supplement prediction pipeline',
            'timeline': TIMELINE_MAP.get(guild_key, '8-12 weeks'),
            'current_players': cap.get('actual_players', 0),
            'target_players_min': cap.get('min_players', 0),
            'target_players_max': cap.get('max_players', 0),
            'optimal_players': cap.get('optimal_players', 0),
            'current_pct': cap.get('actual_pct', 0),
            'target_range': f"{mn}-{mx}%",
            'target_description': target_description,
            'priority_score': round(priority, 2),
        })

    # Sort by priority (highest first)
    steps.sort(key=lambda s: s['priority_score'], reverse=True)

    # Assign step numbers AND derive priority labels from rank + score
    # This ensures labels are consistent with ordering (no more 1B at step 1, 1A at step 2)
    for i, step in enumerate(steps):
        step['step_number'] = i + 1
        step['priority_level'] = _derive_priority_label(step['priority_score'], i)

    return steps


def _derive_priority_label(priority_score: float, sorted_rank: int) -> str:
    """Derive priority level label from score alone (unified v2 system).
    
    Delegates to shared guild_priority.score_to_label() — single source of truth.
    See formulation_automation/documentation/PRIORITY_SYSTEM_CHANGELOG.md
    """
    return score_to_label(priority_score)


# ══════════════════════════════════════════════════════════════
#    FORECAST PROJECTIONS
# ══════════════════════════════════════════════════════════════

def generate_forecast(data: dict, steps: list) -> list:
    """
    Generate expected improvement projections for each intervention step.
    """
    guilds = data.get('guilds', {})
    capacity = compute_capacity_100(guilds)
    forecasts = []

    for step in steps:
        guild_key = step['guild_key']
        cap = capacity.get(guild_key, {})

        forecasts.append({
            'guild_display': step['guild_display'],
            'current_players': cap.get('actual_players', 0),
            'target_players_min': cap.get('min_players', 0),
            'target_players_max': cap.get('max_players', 0),
            'direction': '↑' if step['action'] == 'expand' else '↓',
        })

    return forecasts


# ══════════════════════════════════════════════════════════════
#    VITAMIN CHECK STEP
# ══════════════════════════════════════════════════════════════

def generate_vitamin_check_step(vitamin_risks: dict) -> dict:
    """
    Generate a vitamin-focused action step if any vitamins have elevated risk.
    """
    needs_attention = []
    for vname, vdata in vitamin_risks.items():
        if vname == 'interpretation':
            continue
        risk = vdata.get('risk_level', 0) if isinstance(vdata, dict) else 0
        if risk >= 1:
            label = vdata.get('risk_label', 'Unknown')
            needs_attention.append(f"{vname} ({label})")

    if not needs_attention:
        return None

    return {
        'title': 'Check Vitamin Support',
        'action': 'supplement_check',
        'why': f'Your microbiome shows reduced production capacity for: {", ".join(needs_attention)}. Dietary reinforcement or supplementation may help.',
        'how': 'TBD — will be populated from supplement prediction pipeline',
        'vitamins_flagged': needs_attention,
    }


# ══════════════════════════════════════════════════════════════
#                    ASSEMBLE ACTION PLAN
# ══════════════════════════════════════════════════════════════

def _populate_how_from_formulation(steps: list, sample_dir: str, sample_id: str) -> list:
    """Populate intervention step 'how' fields from formulation master JSON.
    
    Reads the formulation_master JSON (if it exists) and maps guild interventions
    to actual supplement/prebiotic/probiotic recommendations.
    
    Guild → formulation mapping:
    - Probiotic mix (which mix, which strains) → all guild steps
    - Prebiotics (which fibers, doses) → fiber/butyrate/bifido steps
    - Specific supplements → relevant guild steps
    
    Falls back to 'TBD' if formulation hasn't been generated yet.
    """
    # Try to find formulation master JSON
    formulation_paths = [
        os.path.join(sample_dir, "reports", "reports_json", f"formulation_master_{sample_id}.json"),
        os.path.join(sample_dir, "reports", "reports_json", f"formulation_master.json"),
    ]
    
    master = None
    for fpath in formulation_paths:
        if os.path.exists(fpath):
            try:
                with open(fpath, 'r') as f:
                    master = json.load(f)
                break
            except (json.JSONDecodeError, IOError):
                continue
    
    if not master:
        # Formulation not yet generated — keep TBD
        return steps
    
    # Extract key formulation data
    mix = master.get("decisions", {}).get("mix_selection", {})
    mix_name = mix.get("mix_name", "")
    mix_id = mix.get("mix_id")
    strains = mix.get("strains", [])
    strain_names = [s.get("name", "") for s in strains if "LP815" not in s.get("name", "")]
    lp815 = mix.get("lp815_added", False)
    
    prebiotics = master.get("decisions", {}).get("prebiotic_design", {})
    prebiotic_list = prebiotics.get("prebiotics", [])
    prebiotic_summary = ", ".join(f"{p['substance']} {p['dose_g']}g" for p in prebiotic_list)
    
    total_cfu = mix.get("total_cfu_billions", 50)
    
    # Build guild-specific "how" descriptions
    guild_how = {}
    
    # All guilds benefit from the probiotic mix
    base_how = f"Mix {mix_id} ({mix_name}, {total_cfu}B CFU, {len(strain_names)} strains)"
    if prebiotic_summary:
        base_how += f" + prebiotics: {prebiotic_summary}"
    if lp815:
        base_how += " + LP815 psychobiotic (5B CFU)"
    
    # Guild-specific additions
    guild_how['Fiber Degraders'] = base_how
    guild_how['HMO/Oligosaccharide-Utilising Bifidobacteria'] = base_how
    guild_how['Cross-Feeders'] = base_how
    guild_how['Butyrate Producers'] = base_how
    guild_how['Proteolytic Dysbiosis Guild'] = f"Competitive displacement via {base_how}"
    guild_how['Mucin Degraders'] = f"Fiber expansion to reduce mucus dependency via {base_how}"
    
    # Populate steps
    for step in steps:
        guild_key = step.get('guild_key', '')
        how_text = guild_how.get(guild_key)
        if how_text:
            step['how'] = how_text
        elif step.get('how', '').startswith('TBD'):
            step['how'] = base_how  # Fallback: use general formulation summary
    
    return steps


def compute_action_plan_fields(data: dict, score_total: float,
                                vitamin_risks: dict = None,
                                sample_dir: str = None,
                                sample_id: str = None) -> dict:
    """Compute all action plan fields.
    
    Args:
        data: Parsed metrics data
        score_total: Overall gut health score
        vitamin_risks: Vitamin risk assessment dict
        sample_dir: Path to sample directory (for formulation lookup)
        sample_id: Sample ID (for formulation lookup)
    """
    from root_causes_fields import assess_reversibility

    steps = generate_intervention_steps(data)
    
    # Populate 'how' from formulation if available
    if sample_dir and sample_id:
        steps = _populate_how_from_formulation(steps, sample_dir, sample_id)
    
    forecast = generate_forecast(data, steps)
    reversibility = assess_reversibility(data, score_total)

    # Add vitamin step if needed
    vitamin_step = None
    if vitamin_risks:
        vitamin_step = generate_vitamin_check_step(vitamin_risks)

    # Load static content for next_steps
    static_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'knowledge_base', 'static_content.json'
    )
    try:
        with open(static_path) as f:
            static = json.load(f)
        next_steps = static.get('action_plan_tab', {}).get('next_steps', [])
    except (FileNotFoundError, json.JSONDecodeError):
        next_steps = []

    return {
        'reversibility': reversibility,
        'intervention_steps': steps,
        'vitamin_check': vitamin_step,
        'forecast': forecast,
        'next_steps': next_steps,
        'total_steps': len(steps) + (1 if vitamin_step else 0),
    }
