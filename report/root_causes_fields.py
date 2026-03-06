"""
root_causes_fields.py — Deterministic root causes computation for platform

Computes diagnostic flags and structural data for the Root Causes tab.
Narrative text (primary diagnosis, key insights, conclusion) is LLM-generated
via narratives.py.

Deterministic outputs:
  - Diagnostic flags (quantitative deviations from healthy ranges)
  - Dietary pattern inference
  - Trophic cascade impact analysis
  - Reversibility assessment
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'shared'))
from guild_priority import GUILD_CLIENT_NAMES as _SHARED_CLIENT_NAMES

from scoring import GUILD_CONFIG


# ══════════════════════════════════════════════════════════════
#    GUILD DISPLAY NAMES — imported from shared/guild_priority.py
#    (single source of truth, consistent with formulation pipeline)
# ══════════════════════════════════════════════════════════════

GUILD_DISPLAY_NAMES = _SHARED_CLIENT_NAMES


def _find_guild_config(gname: str):
    """Find GUILD_CONFIG entry for a guild name."""
    for cfg_name, cfg in GUILD_CONFIG.items():
        if cfg_name in gname or gname in cfg_name:
            return cfg_name, cfg
    return None, None


# ══════════════════════════════════════════════════════════════
#               DIAGNOSTIC FLAGS
# ══════════════════════════════════════════════════════════════

def compute_diagnostic_flags(data: dict) -> list:
    """
    Generate quantitative diagnostic flags — deviations expressed
    as multipliers or fractions of healthy range boundaries.

    Returns list of dicts with:
      - flag: human-readable diagnostic statement
      - guild: which guild this refers to
      - severity: 'critical', 'significant', 'moderate', 'mild'
      - metric_detail: raw numbers for backend use
    """
    guilds = data.get('guilds', {})
    flags = []

    for gname, gdata in guilds.items():
        cfg_name, config = _find_guild_config(gname)
        if config is None:
            continue

        mn, mx, optimal, max_pts, gtype = config
        abund = gdata['abundance']
        display = GUILD_DISPLAY_NAMES.get(gname, gname)

        if gtype == 'beneficial':
            if abund == 0:
                flags.append({
                    'flag': f'{display} are completely absent',
                    'guild': display,
                    'severity': 'critical',
                    'direction': 'absent',
                    'metric_detail': {
                        'actual': 0, 'range_min': mn, 'range_max': mx,
                    },
                })
            elif abund < mn:
                fraction = abund / mn
                if fraction < 0.25:
                    severity = 'critical'
                    desc = f'{display} are at less than a quarter of the minimum healthy level'
                elif fraction < 0.5:
                    severity = 'significant'
                    desc = f'{display} are at about half of the minimum healthy level'
                elif fraction < 0.75:
                    severity = 'moderate'
                    desc = f'{display} are below the healthy range'
                else:
                    severity = 'mild'
                    desc = f'{display} are slightly below the healthy range'

                flags.append({
                    'flag': desc,
                    'guild': display,
                    'severity': severity,
                    'direction': 'below',
                    'metric_detail': {
                        'actual': round(abund, 1),
                        'range_min': mn,
                        'range_max': mx,
                        'fraction_of_min': round(fraction, 2),
                    },
                })

        elif gtype == 'contextual':
            if abund > mx:
                multiplier = abund / mx
                if multiplier >= 3:
                    severity = 'critical'
                    desc = f'{display} are {multiplier:.0f}× above the healthy maximum'
                elif multiplier >= 2:
                    severity = 'significant'
                    desc = f'{display} are {multiplier:.1f}× above the healthy maximum'
                elif multiplier >= 1.5:
                    severity = 'moderate'
                    desc = f'{display} are moderately above the healthy range'
                else:
                    severity = 'mild'
                    desc = f'{display} are slightly above the healthy range'

                flags.append({
                    'flag': desc,
                    'guild': display,
                    'severity': severity,
                    'direction': 'above',
                    'metric_detail': {
                        'actual': round(abund, 1),
                        'range_max': mx,
                        'multiplier_of_max': round(multiplier, 2),
                    },
                })

    # CLR-based competitive flags — only add if guild not already flagged for abundance
    already_flagged_guilds = {f['guild'] for f in flags}
    for gname, gdata in guilds.items():
        cfg_name, config = _find_guild_config(gname)
        if config is None:
            continue
        mn, mx, optimal, max_pts, gtype = config
        display = GUILD_DISPLAY_NAMES.get(gname, gname)
        clr = gdata.get('clr')

        if clr is not None and gtype == 'beneficial' and clr < -1.0:
            if display in already_flagged_guilds:
                # Enhance existing flag instead of duplicating
                for f in flags:
                    if f['guild'] == display:
                        f['flag'] += f' and losing competitive ground to other bacteria'
                        f['metric_detail']['clr'] = round(clr, 2)
                        break
            else:
                flags.append({
                    'flag': f'{display} are losing competitive ground to other bacteria (suppressed)',
                    'guild': display,
                    'severity': 'significant' if clr < -1.5 else 'moderate',
                    'direction': 'suppressed',
                    'metric_detail': {
                        'clr': round(clr, 2),
                        'meaning': f'{abs(clr):.1f}× below community average',
                    },
                })

    # Sort by severity
    severity_order = {'critical': 0, 'significant': 1, 'moderate': 2, 'mild': 3}
    flags.sort(key=lambda f: severity_order.get(f['severity'], 4))

    return flags


# ══════════════════════════════════════════════════════════════
#            TROPHIC CASCADE IMPACT
# ══════════════════════════════════════════════════════════════

def compute_trophic_impact(data: dict) -> dict:
    """
    Assess trophic cascade dynamics — which bottlenecks exist
    and what downstream effects they cause.
    """
    guilds = data.get('guilds', {})

    # Get key guild abundances
    fiber = 0
    bifido = 0
    cross = 0
    butyrate = 0
    mucin = 0
    proteo = 0

    for gname, gdata in guilds.items():
        if 'Fiber' in gname:
            fiber = gdata['abundance']
        elif 'Bifidobacteria' in gname or 'HMO' in gname:
            bifido = gdata['abundance']
        elif 'Cross' in gname:
            cross = gdata['abundance']
        elif 'Butyrate' in gname:
            butyrate = gdata['abundance']
        elif 'Mucin' in gname:
            mucin = gdata['abundance']
        elif 'Proteolytic' in gname:
            proteo = gdata['abundance']

    impacts = []

    # Bifido absence → lactate pathway broken
    if bifido < 1.0:
        impacts.append({
            'type': 'missing_amplifier',
            'title': 'Bifidobacteria Are Missing',
            'description': 'Without Bifidobacteria, your gut loses a key step in food processing — the production of lactate, which fuels other beneficial bacterial teams. This can reduce beneficial compound output by 50-70%.',
            'affected_guilds': ['Bifidobacteria', 'Intermediate processors', 'Gut-lining energy producers'],
            'efficiency_loss': '50-70%',
        })

    # Cross-feeders low → bottleneck
    if cross < 6.0:
        impacts.append({
            'type': 'bottleneck',
            'title': 'Intermediate Processors Are Understaffed',
            'description': 'Your intermediate processors — the bacteria that convert raw fermentation products into finished beneficial compounds — don\'t have enough members to keep up with demand.',
            'affected_guilds': ['Intermediate processors', 'Gut-lining energy producers'],
            'efficiency_loss': 'variable',
        })

    # Fiber low → substrate limitation
    if fiber < 30.0:
        impacts.append({
            'type': 'substrate_limitation',
            'title': 'Not Enough Fiber-Processing Bacteria',
            'description': 'Your fiber-processing bacteria are below the level needed to supply the rest of the chain with raw materials. This means every team downstream gets less to work with.',
            'affected_guilds': ['Fiber-processing bacteria', 'Bifidobacteria', 'Intermediate processors', 'Gut-lining energy producers'],
            'efficiency_loss': 'proportional to deficit',
        })

    # Proteolytic overgrowth → feedback loop
    if proteo > 5.0:
        impacts.append({
            'type': 'feedback_loop',
            'title': 'Protein-Fermenting Bacteria Are Overactive',
            'description': 'Your protein-fermenting bacteria have grown beyond healthy levels and are producing ammonia and other harsh compounds that make it harder for beneficial bacteria to thrive.',
            'affected_guilds': ['Protein-fermenting bacteria', 'Gut-lining energy producers', 'Fiber-processing bacteria'],
            'efficiency_loss': 'escalating',
        })

    # Mucin overgrowth → barrier stress
    if mucin > 4.0:
        impacts.append({
            'type': 'barrier_stress',
            'title': 'Mucus-Layer Bacteria Are Overactive',
            'description': 'Your mucus-layer bacteria have expanded beyond healthy levels, which means your gut is consuming its protective lining for fuel — a sign that not enough dietary fiber is reaching your bacteria.',
            'affected_guilds': ['Mucus-layer bacteria', 'Fiber-processing bacteria'],
            'efficiency_loss': 'barrier integrity risk',
        })

    # Butyrate low → colonocyte energy deficit
    if butyrate < 10.0:
        impacts.append({
            'type': 'energy_deficit',
            'title': 'Gut-Lining Energy Producers Are Low',
            'description': 'Your gut-lining energy producers are below optimal, which means less butyrate — the primary fuel for your gut lining cells and a key anti-inflammatory compound — is being made.',
            'affected_guilds': ['Gut-lining energy producers'],
            'efficiency_loss': 'proportional to deficit',
        })

    return {
        'cascade_impacts': impacts,
        'primary_bottleneck': impacts[0]['type'] if impacts else 'none',
        'total_impacts': len(impacts),
    }


# ══════════════════════════════════════════════════════════════
#            REVERSIBILITY ASSESSMENT
# ══════════════════════════════════════════════════════════════

# Recovery guild weights — reflects trophic cascade position
# Higher weight = more impact on recovery speed
RECOVERY_GUILD_WEIGHTS = {
    'Fiber Degraders': 2.0,       # Upstream gateway — rate-limiting primary producers
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 1.5,  # Specialist amplifiers with rapid kinetics
    'Cross-Feeders': 1.5,         # Network connectivity — syntrophic routing
    'Butyrate Producers': 1.0,    # Terminal output — mostly passive recovery
    # Proteolytic Guild: excluded — not a recovery predictor
    # Mucin Degraders: checked separately for transition safety
}


def assess_reversibility(data: dict, score_total: float) -> dict:
    """
    Weighted reversibility assessment.

    Recovery capacity = Shannon diversity + weighted beneficial guild resilience.
    Transition safety = Mucin degrader diversity check.

    Guild weights reflect trophic cascade position:
      Fiber Degraders (2.0) — upstream gateway, diverse = faster recovery
      Bifidobacteria (1.5) — specialist amplifiers, rapid prebiotic response
      Cross-Feeders (1.5) — network connectivity, syntrophic routing
      Butyrate Producers (1.0) — terminal output, mostly passive recovery
    """
    from thresholds import shannon_low, shannon_high

    guilds = data.get('guilds', {})
    shannon = data.get('Shannon') or 2.0
    sh_low = shannon_low()   # Q25
    sh_high = shannon_high()  # Q75

    # Weighted resilience score for beneficial guilds
    resilience_score = 0.0
    max_resilience = sum(RECOVERY_GUILD_WEIGHTS.values())  # 6.0
    guild_details = {}

    for gname, gdata in guilds.items():
        for cfg_key, weight in RECOVERY_GUILD_WEIGHTS.items():
            if cfg_key in gname or gname in cfg_key:
                J = gdata.get('redundancy', 0)
                abund = gdata['abundance']
                is_resilient = J >= 0.60 and abund >= 1.0
                if is_resilient:
                    resilience_score += weight
                guild_details[GUILD_DISPLAY_NAMES.get(gname, gname)] = {
                    'evenness': round(J, 2),
                    'abundance': round(abund, 1),
                    'resilient': is_resilient,
                    'weight': weight,
                }
                break

    # Transition safety — Mucin Degrader check
    mucin_abund = 0
    mucin_J = 0
    for gname, gdata in guilds.items():
        if 'Mucin' in gname:
            mucin_abund = gdata['abundance']
            mucin_J = gdata.get('redundancy', 0)

    transition_caution = False
    transition_note = ''
    if mucin_abund > 8.0 and mucin_J < 0.40:
        transition_caution = True
        transition_note = 'Your mucus-layer bacteria are elevated with low diversity — the rebalancing process may need careful monitoring to ensure a smooth transition.'
    elif mucin_abund > 4.0 and mucin_J >= 0.60:
        transition_note = 'Your mucus-layer bacteria are elevated but diverse, which means they should reduce gradually and smoothly as fiber availability improves.'
    elif mucin_abund > 4.0:
        transition_note = 'Your mucus-layer bacteria are somewhat elevated — we expect them to normalize as other teams strengthen.'

    # Combined assessment
    if shannon >= sh_high and resilience_score >= 4.0:
        level = 'high'
        label = 'High reversibility'
        description = 'Your microbiome has strong diversity and multiple resilient beneficial teams, providing an excellent foundation for recovery.'
        timeline = '8-12 weeks'
    elif shannon >= sh_low and resilience_score >= 2.5:
        level = 'moderate'
        label = 'Good reversibility'
        description = 'Your microbiome has adequate diversity and some resilient teams. Recovery is expected with targeted support.'
        timeline = '12-16 weeks'
    elif shannon >= sh_low * 0.85 and resilience_score >= 1.5:
        level = 'moderate_low'
        label = 'Moderate reversibility'
        description = 'Your microbiome has reduced diversity but still has recoverable potential. A comprehensive approach is recommended.'
        timeline = '16-24 weeks'
    else:
        level = 'low'
        label = 'Challenging but possible'
        description = 'Your microbiome has significant depletion requiring sustained, multi-pronged intervention.'
        timeline = '24+ weeks'

    return {
        'level': level,
        'label': label,
        'description': description,
        'estimated_timeline': timeline,
        'transition_caution': transition_caution,
        'transition_note': transition_note,
        'supporting_factors': {
            'shannon_diversity': round(shannon, 3),
            'resilience_score': round(resilience_score, 1),
            'max_resilience': max_resilience,
            'guild_details': guild_details,
            'overall_score': score_total,
        },
        'method_note': 'Recovery potential is estimated from your bacterial diversity (how many species you have) and the backup diversity within your four beneficial teams. More variety means more pathways to recovery. This is a prediction based on your test results — individual responses may vary.',
    }


# ══════════════════════════════════════════════════════════════
#                    ASSEMBLE ROOT CAUSES
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
#            METABOLIC EVIDENCE — Connect dials to root causes
# ══════════════════════════════════════════════════════════════

DIAL_ROOT_CAUSE_LINKS = {
    'main_fuel': {
        'carb_driven': {
            'scientific': 'CUR positive — carbohydrate-fermenting guilds (Fiber Degraders, Bifidobacteria) outcompete proteolytic guild, indicating fiber-driven metabolism.',
            'non_expert': 'Your gut bacteria strongly prefer processing plant-based fiber — this is the healthiest fuel pattern and shows good fiber availability.',
        },
        'balanced': {
            'scientific': 'CUR near-neutral — mixed substrate utilization with neither carbohydrate nor protein guilds dominating competitive landscape.',
            'non_expert': 'Your bacteria process a mix of carbohydrates and proteins — strengthening the fiber-processing side could improve beneficial compound production.',
        },
        'protein_driven': {
            'scientific': 'CUR negative — proteolytic guild dominates substrate competition, indicating shift from carbohydrate to protein fermentation with associated inflammatory metabolite production.',
            'non_expert': 'Your gut has shifted toward breaking down protein instead of fiber, which produces harsher byproducts — this suggests your fiber-processing bacteria need more support.',
        },
    },
    'fermentation_efficiency': {
        'efficient': {
            'scientific': 'FCR positive — terminal processors (Butyrate Producers, Cross-Feeders) efficiently convert intermediates, indicating intact fermentation cascade.',
            'non_expert': 'Your bacterial teams work well together — raw materials flow smoothly through the chain to produce beneficial compounds that protect your gut.',
        },
        'ok': {
            'scientific': 'FCR near-neutral — adequate intermediate processing but suboptimal conversion efficiency, suggesting partial cross-feeding network limitation.',
            'non_expert': 'Your fermentation works but isn\'t fully optimized — some intermediate compounds aren\'t being completely processed into beneficial products.',
        },
        'sluggish': {
            'scientific': 'FCR negative — intermediate metabolite accumulation due to disrupted cross-feeding network. Lactate/acetate clearance impaired, reducing terminal SCFA synthesis.',
            'non_expert': 'A bottleneck in your gut\'s production line means intermediate compounds build up instead of being converted into the beneficial products your body needs.',
        },
    },
    'mucus_dependency': {
        'diet_fed': {
            'scientific': 'MDR negative — Fiber Degraders dominate over Mucin Degraders, indicating dietary substrate adequacy. Mucus layer turnover minimal.',
            'non_expert': 'Your bacteria get fuel from what you eat, keeping your gut\'s protective lining intact — this is the ideal pattern for gut barrier health.',
        },
        'backup': {
            'scientific': 'MDR near-neutral — moderate mucin utilization alongside dietary fermentation, suggesting intermittent fiber limitation or adaptive mucin turnover.',
            'non_expert': 'Your bacteria are starting to nibble at your gut\'s protective lining as a backup fuel source — this suggests they\'re not getting quite enough dietary fiber.',
        },
        'heavy_mucus': {
            'scientific': 'MDR positive — Mucin Degraders competitively advantage over Fiber Degraders, indicating chronic dietary substrate insufficiency forcing host-substrate dependency.',
            'non_expert': 'Your bacteria heavily rely on your gut\'s protective layer for fuel because not enough dietary fiber reaches them — this puts sustained pressure on your gut barrier.',
        },
    },
    'putrefaction_pressure': {
        'scfa_dominant': {
            'scientific': 'PPR negative — Butyrate Producers dominate over Proteolytic Guild. SCFA production exceeds putrefactive metabolite generation. Anti-inflammatory metabolic profile.',
            'non_expert': 'Your gut produces mostly gentle, beneficial compounds — the bacteria that produce harsh chemicals are well contained, keeping your gut environment healthy.',
        },
        'balanced': {
            'scientific': 'PPR near-neutral — Butyrate Producers and Proteolytic Guild in competitive equilibrium. Moderate protein fermentation with adequate SCFA buffering capacity.',
            'non_expert': 'Some protein fermentation is happening, but your gut-lining energy producers are keeping the harsher byproducts in check — a manageable balance.',
        },
        'protein_pressure': {
            'scientific': 'PPR positive — Proteolytic Guild competitive advantage over Butyrate Producers. Elevated ammonia, H2S, phenol production creating pro-inflammatory colonic environment.',
            'non_expert': 'Too much protein fermentation is producing harsh compounds (ammonia, hydrogen sulfide) that stress your gut lining — shifting back toward fiber processing is important.',
        },
    },
}


def compute_metabolic_evidence(data: dict, fields: dict) -> list:
    """Connect metabolic dials to root causes with dual interpretations."""
    dials = fields.get('metabolic_dials', {})
    evidence = []
    for dial_key, dial_data in dials.items():
        state = dial_data.get('state', '')
        links = DIAL_ROOT_CAUSE_LINKS.get(dial_key, {}).get(state, {})
        if links:
            evidence.append({
                'dial': dial_key,
                'heading': dial_data.get('heading', ''),
                'state': state,
                'label': dial_data.get('label', ''),
                'root_cause_link': links,
            })
    return evidence


# ══════════════════════════════════════════════════════════════
#            PRIMARY PATTERN — from guild priorities + dials
# ══════════════════════════════════════════════════════════════

def compute_primary_pattern(data: dict, fields: dict) -> dict:
    """Determine the primary dysbiosis pattern from guild and metabolic data."""
    guilds = data.get('guilds', {})
    dials = fields.get('metabolic_dials', {})

    # Get key states
    fiber = 0; bifido = 0; proteo = 0; mucin = 0
    for gname, gdata in guilds.items():
        if 'Fiber' in gname: fiber = gdata['abundance']
        elif 'Bifidobacteria' in gname or 'HMO' in gname: bifido = gdata['abundance']
        elif 'Proteolytic' in gname: proteo = gdata['abundance']
        elif 'Mucin' in gname: mucin = gdata['abundance']

    mdr_state = dials.get('mucus_dependency', {}).get('state', '')
    ppr_state = dials.get('putrefaction_pressure', {}).get('state', '')
    fcr_state = dials.get('fermentation_efficiency', {}).get('state', '')
    cur_state = dials.get('main_fuel', {}).get('state', '')

    # Pattern detection (priority order)
    if bifido == 0 and fiber < 30:
        return {
            'pattern': 'bifidobacteria_loss_fiber_deficit',
            'scientific': 'Complete Bifidobacteria depletion with fiber degrader deficit — lactate pathway eliminated, forcing ecosystem into alternative fermentation routes with reduced SCFA amplification.',
            'non_expert': 'Your gut lost a key group of beneficial bacteria (Bifidobacteria) and doesn\'t have enough fiber-processing bacteria — this disrupts the normal chain of food processing and reduces production of protective compounds.',
        }
    elif mucin > 4 and mdr_state == 'heavy_mucus':
        return {
            'pattern': 'mucin_dependent_dysbiosis',
            'scientific': 'Mucin-dependent dysbiosis — ecosystem shifted to host-substrate utilization due to dietary fiber insufficiency. Elevated mucin degradation indicates chronic barrier stress.',
            'non_expert': 'Your gut bacteria shifted toward eating your protective gut lining because they weren\'t getting enough dietary fiber — this puts sustained pressure on your gut barrier.',
        }
    elif proteo > 5 and ppr_state == 'protein_pressure':
        return {
            'pattern': 'protein_driven_dysbiosis',
            'scientific': 'Protein-driven dysbiosis — proteolytic guild dominance with elevated putrefactive metabolite production (ammonia, H2S, phenols). Carbohydrate-fermenting guilds suppressed.',
            'non_expert': 'Your gut has shifted toward protein fermentation, producing harsher byproducts that stress your gut lining — the fiber-processing bacteria need reinforcement.',
        }
    elif fcr_state == 'sluggish':
        return {
            'pattern': 'fermentation_bottleneck',
            'scientific': 'Fermentation efficiency bottleneck — disrupted cross-feeding network limiting terminal SCFA synthesis despite adequate upstream substrate availability.',
            'non_expert': 'Your gut\'s production line has a bottleneck — the bacteria that should be converting intermediate products into beneficial compounds aren\'t working at full capacity.',
        }
    elif fiber < 30 and cur_state != 'protein_driven':
        return {
            'pattern': 'fiber_processing_deficit',
            'scientific': 'Fiber processing deficit — primary degradation capacity below optimal despite balanced substrate competition. Upstream limitation constraining downstream guild function.',
            'non_expert': 'Your fiber-processing bacteria team is understaffed — this means less raw material enters the production chain, limiting how much beneficial compound your gut can produce.',
        }
    else:
        return {
            'pattern': 'healthy_maintenance',
            'scientific': 'Ecosystem operating within functional parameters — carbohydrate-driven metabolism with adequate fermentation efficiency and minimal host-substrate dependency.',
            'non_expert': 'Your gut ecosystem is functioning well overall — the bacteria are processing food efficiently and producing beneficial compounds. Focus is on maintaining this healthy state.',
        }


# ══════════════════════════════════════════════════════════════
#            LIFESTYLE INFERENCE
# ══════════════════════════════════════════════════════════════

def compute_lifestyle_inference(data: dict, fields: dict) -> dict:
    """Infer lifestyle patterns from metabolic and guild data."""
    guilds = data.get('guilds', {})
    dials = fields.get('metabolic_dials', {})

    fiber = 0; proteo = 0; mucin = 0; bifido = 0
    for gname, gdata in guilds.items():
        if 'Fiber' in gname: fiber = gdata['abundance']
        elif 'Proteolytic' in gname: proteo = gdata['abundance']
        elif 'Mucin' in gname: mucin = gdata['abundance']
        elif 'Bifidobacteria' in gname or 'HMO' in gname: bifido = gdata['abundance']

    evidence = []
    if fiber < 30:
        evidence.append({'scientific': f'Fiber Degraders at {fiber:.0f}% (below 30% minimum) suggests limited complex fiber reaching the colon', 'non_expert': 'Your fiber-processing bacteria are understaffed, suggesting less fiber variety in recent conditions'})
    if proteo > 5:
        evidence.append({'scientific': f'Proteolytic Guild at {proteo:.0f}% (above 5% max) indicates elevated colonic protein load', 'non_expert': 'More protein-fermenting bacteria than ideal, suggesting more protein relative to fiber in your system'})
    if mucin > 4:
        evidence.append({'scientific': f'Mucin Degraders at {mucin:.0f}% (above 4% max) indicates compensatory host-substrate utilization', 'non_expert': 'Your mucus-layer bacteria expanded to compensate for insufficient dietary fuel'})
    if bifido == 0:
        evidence.append({'scientific': 'Complete Bifidobacteria absence suggests sustained oligosaccharide deficiency or prior antibiotic disruption', 'non_expert': 'The complete absence of Bifidobacteria suggests your gut experienced a significant disruption — either from medication or sustained dietary changes'})

    # Determine pattern
    if proteo > 5 and fiber < 30:
        pattern = {'scientific': 'Higher protein, lower fiber variety in recent conditions', 'non_expert': 'Your bacteria suggest a pattern of higher protein and lower fiber variety in your recent conditions'}
    elif fiber < 30 and mucin > 4:
        pattern = {'scientific': 'Fiber-insufficient pattern with mucin compensation', 'non_expert': 'Your bacteria suggest insufficient dietary fiber, causing them to rely on your gut lining instead'}
    elif fiber >= 30 and proteo <= 5:
        pattern = {'scientific': 'Balanced substrate availability with adequate fiber provision', 'non_expert': 'Your bacterial patterns suggest a reasonably balanced diet with adequate fiber'}
    else:
        pattern = {'scientific': 'Mixed metabolic signals — moderate fiber and protein balance', 'non_expert': 'Your bacterial patterns suggest a mixed diet — there may be room to increase fiber variety'}

    return {
        'pattern': pattern,
        'evidence': evidence,
        'disclaimer': {
            'scientific': 'Dietary inference from microbial competitive dynamics — not confirmed dietary assessment. Multiple lifestyle factors influence composition.',
            'non_expert': 'This is what your bacteria suggest about recent conditions — it should be confirmed with actual dietary information for a complete picture.',
        },
    }


# ══════════════════════════════════════════════════════════════
#            FEEDBACK LOOPS
# ══════════════════════════════════════════════════════════════

def compute_feedback_loops(data: dict) -> list:
    """Identify active feedback loops from guild and metabolic data.
    Each loop includes health_impact explaining what it means for the person."""
    from thresholds import shannon_low

    guilds = data.get('guilds', {})
    shannon = data.get('Shannon') or 3.0
    loops = []

    fiber = 0; bifido = 0; proteo = 0; mucin = 0; cross = 0; butyrate = 0
    for gname, gdata in guilds.items():
        if 'Fiber' in gname: fiber = gdata['abundance']
        elif 'Bifidobacteria' in gname or 'HMO' in gname: bifido = gdata['abundance']
        elif 'Proteolytic' in gname: proteo = gdata['abundance']
        elif 'Mucin' in gname: mucin = gdata['abundance']
        elif 'Cross' in gname: cross = gdata['abundance']
        elif 'Butyrate' in gname: butyrate = gdata['abundance']

    # 1. Fiber Gap Cycle
    if fiber < 30 and mucin > 4:
        loops.append({
            'name': {'scientific': 'Fiber Starvation → Mucin Compensation Cycle', 'non_expert': 'The Fiber Gap Cycle'},
            'chain': ['Not enough fiber in the diet', 'Fiber-processing team shrinks', 'Other teams get less raw material', 'Mucus-layer team grows to fill the energy gap', 'Gut\'s protective lining gets worn down', 'System becomes reliant on its own lining for fuel'],
            'health_impact': {'scientific': 'Chronic mucin degradation thins the mucus barrier, increasing intestinal permeability and risk of low-grade systemic inflammation.', 'non_expert': 'This can show up as irregular digestion, increased food sensitivities, or bloating — your gut barrier becomes thinner and more permeable over time, which may let irritants through.'},
            'status': 'active' if mucin > 8 else 'developing',
        })

    # 2. Protein Pressure Cycle
    if proteo > 5:
        loops.append({
            'name': {'scientific': 'Proteolytic Expansion → pH Alkalinization Loop', 'non_expert': 'The Protein Pressure Cycle'},
            'chain': ['Too much protein reaching the gut', 'Protein-fermenting bacteria produce ammonia', 'Ammonia makes the gut environment more alkaline', 'Alkaline conditions favor even more protein fermenters', 'Beneficial bacteria get suppressed', 'Cycle keeps reinforcing itself'],
            'health_impact': {'scientific': 'Elevated ammonia, H2S, and phenol production creates pro-inflammatory colonic environment, potentially affecting barrier function and systemic inflammation markers.', 'non_expert': 'You might notice more gas, bloating, or stronger body odor — these are signs of excess harsh compounds being produced in your gut that can irritate the lining.'},
            'status': 'active' if proteo > 10 else 'developing',
        })

    # 3. Missing Bridge
    if bifido == 0:
        loops.append({
            'name': {'scientific': 'Bifidobacteria Loss → Lactate Pathway Disruption Cascade', 'non_expert': 'The Missing Bridge'},
            'chain': ['Bifidobacteria lost from the gut', 'No more lactate being produced', 'Intermediate processors lose their main fuel', 'Fermentation slows down overall', 'Less butyrate produced for gut lining', 'Gut barrier gets less energy support'],
            'health_impact': {'scientific': 'Loss of lactate-mediated cross-feeding reduces SCFA output by 50-70%, compromising colonocyte energy supply and anti-inflammatory signaling.', 'non_expert': 'This reduces your gut\'s ability to produce its main protective compound (butyrate), which can lead to a less resilient gut barrier and increased susceptibility to inflammation.'},
            'status': 'active',
        })

    # 4. Butyrate Depletion Spiral
    if butyrate < 10:
        loops.append({
            'name': {'scientific': 'Butyrate Producer Depletion → Colonocyte Energy Deficit Spiral', 'non_expert': 'The Energy Drain'},
            'chain': ['Gut-lining energy producers decline', 'Less butyrate available for gut lining cells', 'Gut lining gets weaker', 'Weakened barrier lets more irritants through', 'Increased inflammation further suppresses beneficial bacteria', 'Cycle deepens'],
            'health_impact': {'scientific': 'Reduced butyrate supply compromises colonocyte metabolism, tight junction integrity, and Treg cell induction, creating cascading barrier and immune dysfunction.', 'non_expert': 'Your gut lining may not be getting enough energy to maintain itself properly — this can contribute to digestive discomfort, increased sensitivity to foods, and lower overall gut resilience.'},
            'status': 'active' if butyrate < 5 else 'developing',
        })

    # 5. Diversity Loss Cascade
    if shannon < shannon_low():
        loops.append({
            'name': {'scientific': 'Diversity Erosion → Functional Redundancy Loss Cascade', 'non_expert': 'The Variety Gap'},
            'chain': ['Fewer types of bacteria present', 'Less backup when any species declines', 'Dominant species take over more space', 'Ecosystem becomes fragile', 'Harder to respond to dietary changes', 'Recovery takes longer'],
            'health_impact': {'scientific': 'Reduced alpha diversity below population Q25 indicates loss of functional redundancy — ecosystem becomes brittle with reduced capacity to buffer perturbations from diet, stress, or medication.', 'non_expert': 'With fewer types of bacteria, your gut has less flexibility to adapt to changes — like having only a few tools instead of a full toolkit. This makes your system more fragile and slower to recover from disruptions.'},
            'status': 'active',
        })

    # 6. Intermediate Processor Bottleneck
    if cross < 6 and fiber >= 15 and bifido >= 1:
        loops.append({
            'name': {'scientific': 'Cross-Feeder Depletion → Intermediate Accumulation Bottleneck', 'non_expert': 'The Processing Jam'},
            'chain': ['Intermediate processors too few', 'Raw products from upstream pile up', 'Fermentation intermediates accumulate', 'Gas and bloating may increase', 'Final beneficial products reduced', 'Gut lining gets less protection'],
            'health_impact': {'scientific': 'Depleted cross-feeding capacity creates metabolic dead ends — lactate and succinate accumulate rather than being efficiently converted to terminal SCFAs.', 'non_expert': 'This is like a traffic jam in your gut\'s production line — the raw materials are there, but not enough workers to process them. This can contribute to gas, bloating, and reduced production of the compounds that protect your gut.'},
            'status': 'active' if cross < 3 else 'developing',
        })

    # 7. Virtuous Cycle (healthy)
    if fiber >= 30 and bifido >= 2 and proteo <= 5 and mucin <= 4:
        loops.append({
            'name': {'scientific': 'Healthy Fermentation Maintenance Cycle', 'non_expert': 'The Virtuous Cycle'},
            'chain': ['Adequate dietary fiber available', 'Fiber-processing bacteria thrive', 'Bifidobacteria amplify production', 'Intermediate processors pass products efficiently', 'Gut-lining energy producers do their job', 'Healthy gut barrier maintained'],
            'health_impact': {'scientific': 'Optimal SCFA production with efficient cross-feeding networks — supports colonocyte energy, barrier integrity, immune homeostasis, and anti-inflammatory signaling.', 'non_expert': 'This supports steady digestion, consistent energy levels, and a strong immune response — your gut is running on its preferred fuel and producing the protective compounds your body needs.'},
            'status': 'stable',
        })

    return loops


# ══════════════════════════════════════════════════════════════
#                    ASSEMBLE ROOT CAUSES
# ══════════════════════════════════════════════════════════════

def compute_causal_narrative(data: dict, diagnostic_flags: list, metabolic_evidence: list,
                              trophic_impact: dict, primary_pattern: dict) -> dict:
    """Build a connected 3-layer causal narrative integrating ranges, CLR, and metabolic metrics.
    
    The 3 layers:
      1. Healthy ranges — what's below/above/within range
      2. CLR ratios — who's winning/losing competition (ecological dynamics)
      3. Food-processing metrics (CUR, FCR, MDR, PPR) — metabolic confirmation
    
    Connected via guild-to-guild ecological relationships into one causal story.
    Returns dual-language output: scientific + non_expert (health report style).
    """
    guilds = data.get('guilds', {})
    
    # Extract guild data for narrative construction
    fiber = 0; bifido = 0; cross = 0; butyrate = 0; mucin = 0; proteo = 0
    fiber_clr = None; bifido_clr = None; butyrate_clr = None
    for gname, gdata in guilds.items():
        if 'Fiber' in gname:
            fiber = gdata['abundance']; fiber_clr = gdata.get('clr')
        elif 'Bifidobacteria' in gname or 'HMO' in gname:
            bifido = gdata['abundance']; bifido_clr = gdata.get('clr')
        elif 'Cross' in gname:
            cross = gdata['abundance']
        elif 'Butyrate' in gname:
            butyrate = gdata['abundance']; butyrate_clr = gdata.get('clr')
        elif 'Mucin' in gname:
            mucin = gdata['abundance']
        elif 'Proteolytic' in gname:
            proteo = gdata['abundance']
    
    # Extract metabolic states
    met_states = {}
    for me in metabolic_evidence:
        met_states[me['dial']] = {'state': me['state'], 'label': me['label']}
    
    fcr_state = met_states.get('fermentation_efficiency', {}).get('state', '')
    mdr_state = met_states.get('mucus_dependency', {}).get('state', '')
    ppr_state = met_states.get('putrefaction_pressure', {}).get('state', '')
    cur_state = met_states.get('main_fuel', {}).get('state', '')
    
    fcr_label = met_states.get('fermentation_efficiency', {}).get('label', '')
    mdr_label = met_states.get('mucus_dependency', {}).get('label', '')
    ppr_label = met_states.get('putrefaction_pressure', {}).get('label', '')
    
    pattern = primary_pattern.get('pattern', 'healthy_maintenance')
    bottleneck = trophic_impact.get('primary_bottleneck', 'none')
    
    # ── Build scientific narrative ──
    sci_parts = []
    ne_parts = []  # non-expert
    
    # LAYER 1: What's happening (ranges)
    range_issues_sci = []
    range_issues_ne = []
    for flag in diagnostic_flags:
        detail = flag.get('metric_detail', {})
        guild = flag['guild']
        direction = flag.get('direction', '')
        
        if direction == 'absent':
            range_issues_sci.append(f"{guild} absent (0%)")
            range_issues_ne.append(f"your {guild.lower()} are completely missing")
        elif direction == 'below':
            actual = detail.get('actual', '?')
            rmin = detail.get('range_min', '?')
            rmax = detail.get('range_max', '?')
            range_issues_sci.append(f"{guild} at {actual}% (healthy range: {rmin}-{rmax}%)")
            range_issues_ne.append(f"your {guild.lower()} are below healthy levels ({actual}% vs {rmin}-{rmax}% needed)")
        elif direction == 'above':
            actual = detail.get('actual', '?')
            rmax = detail.get('range_max', '?')
            range_issues_sci.append(f"{guild} elevated at {actual}% (max: {rmax}%)")
            range_issues_ne.append(f"your {guild.lower()} have grown beyond healthy levels ({actual}% vs {rmax}% maximum)")
        elif direction == 'suppressed':
            clr = detail.get('clr', '?')
            range_issues_sci.append(f"{guild} competitively suppressed (CLR {clr})")
            range_issues_ne.append(f"your {guild.lower()} are losing ground to other bacteria despite adequate numbers")
    
    if range_issues_sci:
        sci_parts.append("Range assessment: " + "; ".join(range_issues_sci) + ".")
        ne_parts.append("Here's what we found: " + "; ".join(range_issues_ne) + ".")
    
    # LAYER 2: Why it's happening (CLR competitive dynamics)
    clr_insights_sci = []
    clr_insights_ne = []
    
    if bifido_clr is not None and bifido_clr < -1.0:
        clr_insights_sci.append(f"Bifidobacteria CLR {bifido_clr:+.2f} indicates severe competitive disadvantage — ecological pressure is actively eroding this population")
        clr_insights_ne.append(f"your Bifidobacteria have the right numbers but are under pressure from other groups — without help, they'll keep shrinking")
    elif bifido_clr is not None and bifido_clr < -0.3:
        clr_insights_sci.append(f"Bifidobacteria CLR {bifido_clr:+.2f} shows moderate competitive pressure")
        clr_insights_ne.append(f"your Bifidobacteria face some competition from other bacteria")
    
    if fiber_clr is not None and fiber_clr > 0.3 and fiber < 30:
        clr_insights_sci.append(f"Fiber Degraders CLR {fiber_clr:+.2f} positive despite low abundance — substrate-limited rather than competition-limited")
        clr_insights_ne.append(f"your fiber-processing bacteria can do more — they just aren't getting enough fiber to work with")
    elif fiber_clr is not None and fiber_clr < -0.3 and fiber < 30:
        clr_insights_sci.append(f"Fiber Degraders CLR {fiber_clr:+.2f} negative with low abundance — both substrate-limited and losing competitive ground")
        clr_insights_ne.append(f"your fiber-processing bacteria are both short-staffed and losing ground to other groups — a double challenge")
    
    if butyrate_clr is not None and butyrate_clr > 0.3:
        clr_insights_sci.append(f"Butyrate Producers CLR {butyrate_clr:+.2f} positive — competitively strong, efficient terminal processors")
        clr_insights_ne.append(f"your gut-lining energy producers are strong and doing their job well — they just need enough raw materials from the teams above them")
    
    if clr_insights_sci:
        sci_parts.append("Competitive dynamics: " + ". ".join(clr_insights_sci) + ".")
        ne_parts.append("Looking deeper at the competition between groups: " + ". ".join(clr_insights_ne) + ".")
    
    # LAYER 3: What this means metabolically (CUR, FCR, MDR, PPR)
    met_insights_sci = []
    met_insights_ne = []
    
    if fcr_state == 'efficient':
        met_insights_sci.append("FCR confirms intact fermentation cascade — terminal processing is efficient, the bottleneck is upstream substrate supply")
        met_insights_ne.append("the good news is your gut processes food efficiently — the issue isn't the workers, it's not enough raw material getting into the system")
    elif fcr_state == 'sluggish':
        met_insights_sci.append("FCR confirms disrupted cross-feeding — intermediate metabolite accumulation indicates processing bottleneck")
        met_insights_ne.append("there's a jam in the system — half-finished products pile up instead of being turned into the beneficial compounds your body needs")
    
    if mdr_state == 'diet_fed':
        met_insights_sci.append("MDR negative confirms dietary substrate adequacy — gut barrier is not being consumed for fuel")
        met_insights_ne.append("your gut's protective lining is safe — your bacteria are fueled by food, not by eating the lining itself")
    elif mdr_state == 'heavy_mucus':
        met_insights_sci.append("MDR positive confirms chronic barrier stress — gut lining being consumed as substrate due to fiber insufficiency")
        met_insights_ne.append("your bacteria have started eating your gut's protective lining for fuel because they're not getting enough fiber")
    
    if ppr_state == 'scfa_dominant':
        met_insights_sci.append("PPR negative confirms anti-inflammatory metabolic profile — SCFA production dominates over putrefactive metabolites")
        met_insights_ne.append("your gut mostly makes gentle, helpful compounds — the harsh stuff is well controlled")
    elif ppr_state == 'protein_pressure':
        met_insights_sci.append("PPR positive confirms elevated putrefactive pressure — ammonia and H2S production stressing the colonic environment")
        met_insights_ne.append("too much protein is being broken down in harsh ways, producing compounds that irritate your gut")
    
    if met_insights_sci:
        sci_parts.append("Metabolic confirmation: " + ". ".join(met_insights_sci) + ".")
        ne_parts.append("What the food-processing data tells us: " + ". ".join(met_insights_ne) + ".")
    
    # CONNECTING STORY: Guild-to-guild cascade
    cascade_sci = []
    cascade_ne = []
    
    if pattern == 'fiber_processing_deficit':
        cascade_sci.append("Causal chain: Fiber Degrader deficit → reduced substrate for downstream guilds → constrained Bifidobacteria amplification → limited SCFA terminal output despite functional fermentation cascade")
        cascade_ne.append("Putting it all together: not enough fiber-processing bacteria → less raw material for the next team → Bifidobacteria can't do their job fully → and in the end, your gut makes fewer of the protective compounds it needs — even though the teams themselves are capable")
    elif pattern == 'bifidobacteria_loss_fiber_deficit':
        cascade_sci.append("Causal chain: Bifidobacteria loss eliminates lactate amplification pathway → Cross-Feeders lose primary substrate → terminal SCFA production reduced 50-70% → compounded by Fiber Degrader deficit limiting upstream input")
        cascade_ne.append("Putting it all together: a key team (Bifidobacteria) went missing → the teams that depend on them can't work properly → on top of that, not enough fiber-processors at the start → so your gut makes far fewer protective compounds than it should")
    elif pattern == 'mucin_dependent_dysbiosis':
        cascade_sci.append("Causal chain: Chronic dietary fiber insufficiency → Fiber Degraders decline → Mucin Degraders expand as compensatory substrate source → sustained barrier turnover → progressive permeability risk")
        cascade_ne.append("Putting it all together: not enough fiber reaching your gut → bacteria turned to eating the protective lining instead → this creates a cycle where the lining gets thinner while the mucus-eating bacteria keep growing")
    elif pattern == 'protein_driven_dysbiosis':
        cascade_sci.append("Causal chain: Proteolytic guild expansion → ammonia/H2S production → colonic pH alkalinization → carbohydrate-fermenting guilds suppressed → further proteolytic advantage (feedback loop)")
        cascade_ne.append("Putting it all together: protein-fermenting bacteria grew too large → their harsh byproducts changed the gut environment → this made it even harder for the beneficial bacteria to survive → creating a cycle that keeps getting worse")
    elif pattern == 'fermentation_bottleneck':
        cascade_sci.append("Causal chain: Cross-Feeder depletion → intermediate metabolite accumulation → impaired lactate/acetate routing → terminal SCFA synthesis constrained despite adequate upstream substrate")
        cascade_ne.append("Putting it all together: the middle team in your gut's chain is understaffed → raw materials pile up without being processed → and the final protective compounds can't be made fast enough, even though there's plenty of raw material")
    elif pattern == 'healthy_maintenance':
        cascade_sci.append("No pathological cascade detected — guild interactions operating within functional parameters with adequate cross-feeding dynamics")
        cascade_ne.append("Your gut bacteria are working together well — each team passes its output to the next, and the end result is good production of the compounds that keep your gut healthy")
    
    if cascade_sci:
        sci_parts.append(cascade_sci[0])
        ne_parts.append(cascade_ne[0])
    
    return {
        'scientific': " ".join(sci_parts),
        'non_expert': " ".join(ne_parts),
    }


def compute_root_causes_fields(data: dict, score_total: float, fields: dict = None) -> dict:
    """Compute all deterministic root causes fields including enriched data."""
    result = {
        'diagnostic_flags': compute_diagnostic_flags(data),
        'trophic_impact': compute_trophic_impact(data),
        'reversibility': assess_reversibility(data, score_total),
    }

    # Enriched fields (require overview_fields output)
    if fields:
        result['primary_pattern'] = compute_primary_pattern(data, fields)
        result['metabolic_evidence'] = compute_metabolic_evidence(data, fields)
        result['lifestyle_inference'] = compute_lifestyle_inference(data, fields)
        result['feedback_loops'] = compute_feedback_loops(data)
        
        # 3-layer causal narrative — connects ranges + CLR + metabolic metrics
        # into one unified story (scientific + non-expert versions)
        result['causal_narrative'] = compute_causal_narrative(
            data,
            result['diagnostic_flags'],
            result['metabolic_evidence'],
            result['trophic_impact'],
            result['primary_pattern'],
        )

    return result
