"""
scoring.py — Overall Gut Health Score (0-100)

Five-pillar scoring algorithm v3.1:
  P1: Health Association       (0-20)  — genetic health profile
  P2: Diversity & Resilience   (0-20)  — ecosystem stability
  P3: Metabolic Function       (0-20)  — how well food is processed
  P4: Guild Ecosystem Balance  (0-30)  — bacterial group harmony
  P5: Safety Profile           (0-10)  — absence of harmful patterns

All thresholds sourced from:
  - Microbiome Algorithm Framework v1.7
  - Guild Analysis v3.0 Section 2.2 (CLR ratio interpretation tables)
  - Guild Analysis v3.0 Section 1.1 (healthy reference ranges)
"""


# ── Guild Configuration ──
# (min%, max%, optimal%, max_points, type)
GUILD_CONFIG = {
    'Butyrate Producers':                        (10, 25, 17.5, 6, 'beneficial'),
    'Fiber Degraders':                           (30, 50, 40,   6, 'beneficial'),
    'Cross-Feeders':                             (6,  12, 9,    5, 'beneficial'),
    'HMO/Oligosaccharide-Utilising Bifidobacteria': (2, 10, 6,  5, 'beneficial'),
    'Mucin Degraders':                           (1,  4,  2.5,  4, 'contextual'),
    'Proteolytic Dysbiosis Guild':               (1,  5,  3,    4, 'contextual'),
}

# Score bands for client-facing labels
SCORE_BANDS = [
    (80, 'Excellent', 'Strong ecosystem with optimization opportunities only'),
    (65, 'Good',      'Healthy with specific areas for improvement'),
    (50, 'Fair',      'Multiple issues requiring targeted attention'),
    (35, 'Needs Attention', 'Significant imbalances, intervention recommended'),
    (0,  'Concerning', 'Major dysbiosis, comprehensive intervention needed'),
]


def get_score_band(total: float) -> dict:
    """Return the score band label and description for a given total score."""
    for threshold, label, description in SCORE_BANDS:
        if total >= threshold:
            return {'band': label, 'description': description, 'threshold': threshold}
    return {'band': 'Concerning', 'description': SCORE_BANDS[-1][2], 'threshold': 0}


# ── Helper: Get guild data by keyword ──
def _get_guild(guilds: dict, keyword: str) -> dict:
    """Find a guild by keyword match in name."""
    for gname, gdata in guilds.items():
        if keyword in gname:
            return gdata
    return {'abundance': 0.0, 'redundancy': 0.5, 'clr': None}


# ── CLR Ratio Scoring ──
def _ratio_score(value: float, favorable: float, unfavorable: float,
                 higher_is_better: bool = True) -> float:
    """
    Score a CLR ratio from 0-5 based on three zones:
      Zone 1 (≥ favorable):           5.0 pts (maximum)
      Zone 2 (unfavorable → favorable): 2.0-5.0 pts (linear interpolation)
      Zone 3 (< unfavorable):          0.0-2.0 pts (graceful decline)

    Thresholds from Guild Analysis v3.0 Section 2.2.
    Zone 2 floor = 2.0 because "Balanced" state is acceptable, not pathological.
    Zone 3 reaches 0 at 2× the unfavorable threshold.
    """
    if not higher_is_better:
        return _ratio_score(-value, -favorable, -unfavorable, True)

    if value >= favorable:
        return 5.0
    elif value >= unfavorable:
        return 2.0 + 3.0 * (value - unfavorable) / (favorable - unfavorable)
    else:
        return max(0.0, 2.0 * (1.0 + value / abs(unfavorable * 2)))


def _smart_nan_ratio(ratio_name: str, value, guilds: dict) -> float:
    """
    Handle nan/None CLR ratios by computing them from guild abundances.

    When the upstream bioinformatics pipeline reports nan (because it uses a
    <1% threshold for CLR), we can still calculate the diagnostic ratios
    directly from guild abundances. The GM cancels in all ratio formulas,
    so ratios are just log-ratios between guild abundances.

    CLR ratio formulas (GM cancels in subtraction):
      CUR = [(Fiber_CLR + Bifido_CLR) / 2] - Proteo_CLR = ln(sqrt(Fiber*Bifido) / Proteo)
      FCR = [(Butyrate_CLR + Cross_CLR) / 2] - Bifido_CLR = ln(sqrt(Butyrate*Cross) / Bifido)
      MDR = Mucin_CLR - Fiber_CLR = ln(Mucin / Fiber)
      PPR = Proteo_CLR - Butyrate_CLR = ln(Proteo / Butyrate)

    For truly absent guilds (0.000%), uses pseudocount of 0.001%.
    """
    import math

    if value is not None:
        return value

    # Extract guild abundances
    PSEUDOCOUNT = 0.001  # % — for truly absent guilds

    def _abund(keyword):
        g = _get_guild(guilds, keyword)
        a = g['abundance']
        return a if a > 0 else PSEUDOCOUNT

    fiber = _abund('Fiber')
    bifido_val = _abund('Bifidobacteria')
    if bifido_val == PSEUDOCOUNT:
        bifido_val = _abund('HMO')
    butyrate = _abund('Butyrate')
    cross = _abund('Cross')
    mucin = _abund('Mucin')
    proteo = _abund('Proteolytic')

    # Calculate the ratio directly from abundances (GM cancels)
    if ratio_name == 'CUR':
        return math.log(math.sqrt(fiber * bifido_val) / proteo)
    elif ratio_name == 'FCR':
        return math.log(math.sqrt(butyrate * cross) / bifido_val)
    elif ratio_name == 'MDR':
        return math.log(mucin / fiber)
    elif ratio_name == 'PPR':
        return math.log(proteo / butyrate)

    return 0.0  # Unknown ratio name → neutral


# ── Guild Abundance Scoring ──
def _guild_abundance_score(abundance: float, mn: float, mx: float,
                           optimal: float, max_pts: float, gtype: str) -> float:
    """
    Score a guild by its position relative to the healthy reference range.

    Within range: full or near-full score (max 30% edge penalty).
    Below range (beneficial): linear drop to 0 as abundance → 0.
    Below range (contextual): 50-70% credit (below range often OK for mucin/proteo).
    Above range (beneficial): up to 60% penalty.
    Above range (contextual): harsh penalty, drops to 0 quickly.
    """
    if mn <= abundance <= mx:
        distance = abs(abundance - optimal) / max(optimal - mn, mx - optimal)
        return max_pts * (1.0 - 0.3 * min(distance, 1.0))
    elif abundance < mn:
        if gtype == 'contextual':
            return max_pts * 0.7 if abundance >= mn * 0.5 else max_pts * 0.5
        return max_pts * max(0.0, abundance / mn)
    else:  # above range
        excess = (abundance - mx) / mx
        if gtype == 'beneficial':
            return max_pts * max(0.4, 1.0 - excess * 0.6)
        else:  # contextual — harsh
            return max_pts * max(0.0, 0.7 - excess)


# ── Guild CLR Modifier ──
def _guild_clr_modifier(clr_value, guild_type: str) -> float:
    """
    Additional score modifier based on guild CLR competitive position.

    Beneficial guilds: negative CLR = suppressed (penalty), strong positive = bonus.
    Contextual guilds: positive CLR = overgrowth (penalty).

    v3.1: Increased CLR impact — CLR depletion within abundance range is the
    "DIVERGENT — Underutilized" scenario from the 9-scenario matrix, which
    the framework identifies as requiring intervention despite adequate abundance.
    """
    if clr_value is None:
        return 0.0

    if guild_type == 'beneficial':
        if clr_value < -2.0:
            return -3.0   # Severe depletion — critical competitive disadvantage
        elif clr_value < -1.5:
            return -2.5
        elif clr_value < -1.0:
            return -2.0   # Clear depletion — likely functional deficit
        elif clr_value < -0.5:
            return -1.0   # Mild depletion — early warning
        elif clr_value > 1.5:
            return 1.0    # Strong enrichment bonus
        elif clr_value > 1.0:
            return 0.5
        return 0.0
    else:  # contextual
        if clr_value > 2.0:
            return -3.0   # Severe overgrowth
        elif clr_value > 1.5:
            return -2.5
        elif clr_value > 1.0:
            return -1.5
        elif clr_value > 0.5:
            return -0.5
        return 0.0


# ── Dysbiosis Marker Scoring (threshold-based) ──
def _dysbiosis_marker_score(abundance: float, threshold_mild: float = 0.1,
                            threshold_significant: float = 0.5) -> float:
    """
    Score a dysbiosis marker (0-3 pts). Full marks unless exceeding threshold.
    Presence alone is NOT penalized — only exceedance of clinically relevant levels.
    """
    if abundance <= threshold_mild:
        return 3.0
    elif abundance <= threshold_significant:
        return 3.0 * (1.0 - (abundance - threshold_mild) /
                       (threshold_significant - threshold_mild + 0.3))
    else:
        return max(0.0, 3.0 * 0.3 * (1.0 - (abundance - threshold_significant) / 1.0))


def _es_marker_score(abundance: float) -> float:
    """
    E-S (Escherichia-Shigella) scoring. Normal commensal up to 3%.
    Gradual reduction 3-5%, steep above 5%.
    """
    if abundance <= 3.0:
        return 3.0
    elif abundance <= 5.0:
        return 3.0 * (1.0 - (abundance - 3.0) / 4.0)
    else:
        return max(0.0, 3.0 * 0.3 * (1.0 - (abundance - 5.0) / 5.0))


# ══════════════════════════════════════════════════════════════
#                    MAIN SCORING FUNCTION
# ══════════════════════════════════════════════════════════════

def compute_score(data: dict) -> dict:
    """
    Compute the Overall Gut Health Score from parsed metrics data.

    Args:
        data: dict from parse_metrics.parse_all()

    Returns:
        dict with pillar scores, total, band, and detailed breakdown
    """
    guilds = data.get('guilds', {})
    dysbiosis = data.get('dysbiosis', {})
    scores = {}
    details = {}

    # ════════════════════════════════════════════════
    # PILLAR 1: Health Association (0-20)
    # ════════════════════════════════════════════════
    hf = data.get('HF') or 0.5
    gmwi2 = data.get('GMWI2') or 0
    wgmwi2 = data.get('wGMWI2') or 0
    sb = data.get('SB') or 0
    br = data.get('BR') or 0.5
    z = data.get('z_score') or 0

    hf_score = min(hf / 0.80, 1.0) * 8
    concordance = 2.0 if (gmwi2 >= 0 and wgmwi2 >= 0) or (gmwi2 < 0 and wgmwi2 < 0) else 0.0
    sb_score = min(max(0, sb + 1) / 2.5, 1.0) * 5
    br_score = min(br / 0.75, 1.0) * 3
    z_pts = min(max(0, (z + 1) / 3), 1.0) * 2

    scores['P1_health'] = min(hf_score + concordance + sb_score + br_score + z_pts, 20.0)
    details['P1'] = {
        'hf_score': round(hf_score, 2),
        'concordance': concordance,
        'sb_score': round(sb_score, 2),
        'br_score': round(br_score, 2),
        'z_pts': round(z_pts, 2),
    }

    # ════════════════════════════════════════════════
    # PILLAR 2: Diversity & Resilience (0-20)
    # ════════════════════════════════════════════════
    shannon = data.get('Shannon') or 2.0
    shannon_score = min(shannon / 3.5, 1.0) * 13

    guild_Js = [gdata['redundancy'] for gdata in guilds.values() if gdata['abundance'] >= 1.0]
    avg_J = sum(guild_Js) / len(guild_Js) if guild_Js else 0.5
    guild_resilience = min(avg_J / 0.75, 1.0) * 7

    scores['P2_diversity'] = min(shannon_score + guild_resilience, 20.0)
    details['P2'] = {
        'shannon_score': round(shannon_score, 2),
        'avg_guild_J': round(avg_J, 3),
        'guild_resilience': round(guild_resilience, 2),
        'guilds_counted': len(guild_Js),
    }

    # ════════════════════════════════════════════════
    # PILLAR 3: Metabolic Function (0-20)
    # ════════════════════════════════════════════════
    cur_val = _smart_nan_ratio('CUR', data.get('CUR'), guilds)
    fcr_val = _smart_nan_ratio('FCR', data.get('FCR'), guilds)
    mdr_val = _smart_nan_ratio('MDR', data.get('MDR'), guilds)
    ppr_val = _smart_nan_ratio('PPR', data.get('PPR'), guilds)

    cur_pts = _ratio_score(cur_val, 0.5, -0.5, True)
    fcr_pts = _ratio_score(fcr_val, 0.3, -0.3, True)
    # MDR scoring — recalibrated 2026-03-06: favorable threshold moved from -0.5 to -1.0
    # (matching the dial classification change; diet_fed now requires MDR < -1.0)
    mdr_pts = _ratio_score(mdr_val, -1.0, 0.2, False)
    ppr_pts = _ratio_score(ppr_val, -0.5, 0.5, False)

    scores['P3_metabolic'] = min(cur_pts + fcr_pts + mdr_pts + ppr_pts, 20.0)
    details['P3'] = {
        'CUR': {'raw': data.get('CUR'), 'resolved': round(cur_val, 3), 'score': round(cur_pts, 2)},
        'FCR': {'raw': data.get('FCR'), 'resolved': round(fcr_val, 3), 'score': round(fcr_pts, 2)},
        'MDR': {'raw': data.get('MDR'), 'resolved': round(mdr_val, 3), 'score': round(mdr_pts, 2)},
        'PPR': {'raw': data.get('PPR'), 'resolved': round(ppr_val, 3), 'score': round(ppr_pts, 2)},
    }

    # ════════════════════════════════════════════════
    # PILLAR 4: Guild Ecosystem Balance (0-30)
    # ════════════════════════════════════════════════
    g_abundance_scores = {}
    g_clr_modifiers = {}

    for gname, gdata in guilds.items():
        # Find matching config
        config = None
        config_key = None
        for cfg_name, cfg in GUILD_CONFIG.items():
            if cfg_name in gname or gname in cfg_name:
                config = cfg
                config_key = cfg_name
                break
        if config is None:
            continue

        mn, mx, optimal, max_pts, gtype = config
        abund = gdata['abundance']

        # Abundance score
        short_name = config_key.split()[0].lower()  # 'butyrate', 'fiber', etc.
        if 'Bifidobacteria' in config_key or 'HMO' in config_key:
            short_name = 'bifido'
        elif 'Proteolytic' in config_key:
            short_name = 'proteo'
        elif 'Mucin' in config_key:
            short_name = 'mucin'
        elif 'Cross' in config_key:
            short_name = 'cross'

        g_abundance_scores[short_name] = _guild_abundance_score(abund, mn, mx, optimal, max_pts, gtype)

        # CLR modifier
        g_clr_modifiers[short_name] = _guild_clr_modifier(gdata.get('clr'), gtype)

    total_guild = sum(g_abundance_scores.values()) + sum(g_clr_modifiers.values())
    scores['P4_guild'] = max(0.0, min(total_guild, 30.0))
    details['P4'] = {
        'abundance_scores': {k: round(v, 2) for k, v in g_abundance_scores.items()},
        'clr_modifiers': {k: round(v, 2) for k, v in g_clr_modifiers.items()},
        'total_before_cap': round(total_guild, 2),
    }

    # ════════════════════════════════════════════════
    # PILLAR 5: Safety Profile (0-10)
    # ════════════════════════════════════════════════

    # Dysbiosis markers (threshold-based, 2 pts each, max 8)
    fn_score = _dysbiosis_marker_score(dysbiosis.get('F_nucleatum', 0)) * (2.0 / 3.0)
    sg_score = _dysbiosis_marker_score(dysbiosis.get('S_gallolyticus', 0)) * (2.0 / 3.0)
    pa_score = _dysbiosis_marker_score(dysbiosis.get('P_anaerobius', 0)) * (2.0 / 3.0)
    es_score = _es_marker_score(dysbiosis.get('E_Shigella', 0)) * (2.0 / 3.0)
    marker_total = fn_score + sg_score + pa_score + es_score  # max 8

    # Guild extreme overgrowth penalty (0-1)
    mucin_a = _get_guild(guilds, 'Mucin')['abundance']
    proteo_a = _get_guild(guilds, 'Proteolytic')['abundance']
    extreme_penalty = 0.0
    if mucin_a > 10:
        extreme_penalty += 0.5 * min(1.0, (mucin_a - 10) / 20)
    if proteo_a > 8:
        extreme_penalty += 0.5 * min(1.0, (proteo_a - 8) / 10)

    # BCFA pathway penalty (0-0.5)
    bcfa_count = data.get('bcfa_pathway_count', 0)
    bcfa_penalty = 0.5 if bcfa_count >= 2 else (0.25 if bcfa_count >= 1 else 0.0)

    # M. smithii overgrowth penalty (0-2)
    smithii = data.get('smithii_abundance', 0)
    if smithii > 10:
        smithii_penalty = min(2.0, (smithii - 10) / 5 * 2)
    elif smithii > 5:
        smithii_penalty = (smithii - 5) / 5 * 0.5
    else:
        smithii_penalty = 0.0

    safety_base = 2.0  # Base points for non-marker safety factors
    safety_penalties = extreme_penalty + bcfa_penalty + smithii_penalty

    scores['P5_safety'] = max(0.0, min(marker_total + (safety_base - safety_penalties), 10.0))
    details['P5'] = {
        'markers': {
            'F_nucleatum': round(fn_score, 2),
            'S_gallolyticus': round(sg_score, 2),
            'P_anaerobius': round(pa_score, 2),
            'E_Shigella': round(es_score, 2),
            'total': round(marker_total, 2),
        },
        'extreme_penalty': round(extreme_penalty, 2),
        'bcfa_penalty': round(bcfa_penalty, 2),
        'smithii_penalty': round(smithii_penalty, 2),
        'safety_base': safety_base,
    }

    # ════════════════════════════════════════════════
    # TOTAL
    # ════════════════════════════════════════════════
    total = (scores['P1_health'] + scores['P2_diversity'] + scores['P3_metabolic'] +
             scores['P4_guild'] + scores['P5_safety'])

    band = get_score_band(total)

    # ════════════════════════════════════════════════
    # SCORE DRIVERS — what contributes most
    # ════════════════════════════════════════════════
    pillar_info = {
        'health_association':   {
            'score': round(scores['P1_health'], 1), 'max': 20, 'label': 'Health Association',
            'scientific': 'Measures how health-associated your species composition is based on the GMWI2 model of 155 taxa.',
            'non_expert': 'This shows how many of your bacteria are the types typically found in healthy people — think of it as your gut\'s health fingerprint.',
        },
        'diversity_resilience': {
            'score': round(scores['P2_diversity'], 1), 'max': 20, 'label': 'Diversity & Resilience',
            'scientific': 'Combined Shannon diversity and guild-level evenness — measures species richness and structural redundancy.',
            'non_expert': 'How many different types of bacteria you have and how well-balanced they are — more variety means your gut can handle stress and recover faster.',
        },
        'metabolic_function':   {
            'score': round(scores['P3_metabolic'], 1), 'max': 20, 'label': 'Metabolic Function',
            'scientific': 'Composite of 4 CLR diagnostic ratios (CUR, FCR, MDR, PPR) measuring substrate utilization, fermentation efficiency, mucus dependency, and putrefaction pressure.',
            'non_expert': 'This shows how well your bacteria are processing food — are they running on the right fuel, converting it efficiently, and producing gentle rather than harsh byproducts.',
        },
        'guild_balance':        {
            'score': round(scores['P4_guild'], 1), 'max': 30, 'label': 'Bacterial Group Balance',
            'scientific': 'Evaluates 6 functional guilds by abundance position within reference ranges and CLR competitive status, weighted by ecological dependency.',
            'non_expert': 'This checks whether your six bacterial teams are properly staffed — each team has a job, and this score shows if they have enough members to do their work well.',
        },
        'safety_profile':       {
            'score': round(scores['P5_safety'], 1), 'max': 10, 'label': 'Safety Profile',
            'scientific': 'Absence of dysbiosis-associated taxa (F. nucleatum, S. gallolyticus, P. anaerobius, E-S), extreme guild overgrowth, and BCFA pathway activity.',
            'non_expert': 'This checks for any red flags — harmful bacteria, extreme overgrowth, or signs of unhealthy fermentation. A high score here means your gut has a clean safety record.',
        },
    }

    # Find strongest and weakest by % of max
    pillar_pcts = {k: v['score'] / v['max'] for k, v in pillar_info.items()}
    strongest_key = max(pillar_pcts, key=pillar_pcts.get)
    weakest_key = min(pillar_pcts, key=pillar_pcts.get)

    strongest_pct = round(pillar_pcts[strongest_key] * 100)
    weakest_pct = round(pillar_pcts[weakest_key] * 100)

    # Generate human-readable key note
    if weakest_pct < 50:
        key_note = f"Your score is mainly held back by {pillar_info[weakest_key]['label'].lower()} ({weakest_pct}% of its potential)"
    elif strongest_pct > 80:
        key_note = f"Your strongest area is {pillar_info[strongest_key]['label'].lower()} ({strongest_pct}% of its potential)"
    else:
        key_note = f"Your score reflects a balanced profile across all areas"

    score_drivers = {
        'strongest': {
            'pillar': strongest_key,
            'label': pillar_info[strongest_key]['label'],
            'pct_of_max': strongest_pct,
        },
        'weakest': {
            'pillar': weakest_key,
            'label': pillar_info[weakest_key]['label'],
            'pct_of_max': weakest_pct,
        },
        'key_note': key_note,
    }

    return {
        'total': round(total, 1),
        'band': band['band'],
        'band_description': band['description'],
        'pillars': {k: {'score': v['score'], 'max': v['max'], 'scientific': v['scientific'], 'non_expert': v['non_expert']} for k, v in pillar_info.items()},
        'score_drivers': score_drivers,
        'details': details,
    }


# ── CLI test ──
if __name__ == '__main__':
    import sys
    from parse_metrics import parse_all

    if len(sys.argv) < 2:
        print("Usage: python scoring.py <sample_dir>")
        sys.exit(1)

    data = parse_all(sys.argv[1])
    result = compute_score(data)

    print(f"\n{'='*60}")
    print(f"Sample: {data['sample_id']}")
    print(f"Overall Score: {result['total']}/100 [{result['band']}]")
    print(f"\nPillar Breakdown:")
    for name, p in result['pillars'].items():
        pct = p['score'] / p['max'] * 100
        bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
        print(f"  {name:<24} {p['score']:>5.1f}/{p['max']:>2}  {bar} {pct:.0f}%")
    print(f"\nDetails:")
    import json
    print(json.dumps(result['details'], indent=2))
    print(f"{'='*60}")
