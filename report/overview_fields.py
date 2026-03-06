"""
overview_fields.py — Compute deterministic fields for the automated microbiome report

All fields here are computed from metrics WITHOUT LLM. Includes:
  - Overall balance state (Healthy/Transitional/Stressed)
  - Diversity & resilience state (High/Moderate/Low)
  - Metabolic dials (4 dials — corrected thresholds per How_your_gut_is_processing_food.md)
  - Key strengths (rule-based)
  - Key opportunities (rule-based, ecological only — no dietary advice)
  - Vitamin synthesis risk assessment (unified 0-3 scale)
  - Bacterial group status table
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'shared'))
from guild_priority import (compute_guild_priority as _compute_guild_priority,
                            GUILD_DISPLAY_NAMES as _SHARED_DISPLAY_NAMES,
                            GUILD_NON_EXPERT_NAMES as _SHARED_NE_NAMES,
                            GUILD_CLIENT_NAMES as _SHARED_CLIENT_NAMES)

from scoring import GUILD_CONFIG


# ══════════════════════════════════════════════════════════════
#                    STATE CLASSIFICATIONS
# ══════════════════════════════════════════════════════════════

# Dual interpretations for overall balance states
BALANCE_INTERPRETATIONS = {
    'healthy': {
        'scientific': 'GMWI2-positive with resilient health fraction — beneficial taxa dominate both in genetic presence and abundance, indicating a well-functioning ecosystem.',
        'non_expert': 'Your gut bacteria are well-balanced, with beneficial microbes in strong positions. This is a healthy foundation that supports good digestion, immunity, and overall wellbeing.',
    },
    'transitional': {
        'scientific': 'GMWI2/HF values in transitional range — ecosystem between healthy and depleted states with mixed health-associated signals.',
        'non_expert': 'Your gut is in a transitional state — sitting between healthy and stressed. This means there\'s real potential for improvement with the right support, and the situation is not fixed.',
    },
    'stressed': {
        'scientific': 'GMWI2-negative or depleted health fraction — opportunistic taxa gaining competitive advantage, health-associated species suppressed.',
        'non_expert': 'Your gut bacteria are under pressure, with some less helpful microbes gaining ground. The good news is this pattern is typically reversible with targeted microbial support.',
    },
}

# Dual interpretations for diversity states
DIVERSITY_INTERPRETATIONS = {
    'high': {
        'scientific': 'Shannon diversity ≥3.0 with Pielou evenness ≥0.70 — rich species pool with balanced distribution provides structural resilience against perturbation.',
        'non_expert': 'Your gut has a rich variety of bacterial species, and they\'re well-balanced — like a diverse ecosystem that can adapt to changes and bounce back from stress. This is one of the strongest indicators of gut health.',
    },
    'moderate': {
        'scientific': 'Moderate Shannon/Pielou values — adequate species diversity with some concentration in dominant taxa. Recovery capacity present but limited.',
        'non_expert': 'Your gut has a reasonable variety of bacteria, but some species are more dominant than others. Think of it as a garden with decent variety but a few plants taking up more space — there\'s room to create better balance.',
    },
    'low': {
        'scientific': 'Low Shannon diversity or Pielou evenness — limited species pool or dominant monoculture pattern, indicating fragility and reduced functional redundancy.',
        'non_expert': 'Your gut bacteria lack variety, which makes the ecosystem more fragile — like a garden with only a few types of plants. Building up diversity is a key priority, as a more varied bacterial community is more resilient and functional.',
    },
}


def classify_overall_balance(data: dict) -> dict:
    """Classify overall microbiome balance with dual interpretations."""
    gmwi2 = data.get('GMWI2') or 0
    hf = data.get('HF') or 0.5

    if gmwi2 >= 0.5 and hf >= 0.62:
        state = 'healthy'
    elif gmwi2 <= -0.5 or hf < 0.54:
        state = 'stressed'
    else:
        state = 'transitional'

    interp = BALANCE_INTERPRETATIONS[state]
    return {
        'state': state,
        'label': state.capitalize() if state != 'healthy' else 'Healthy',
        'scientific': interp['scientific'],
        'non_expert': interp['non_expert'],
        'source_metrics': {'GMWI2': gmwi2, 'HF': hf},
    }


def classify_diversity_resilience(data: dict) -> dict:
    """Classify diversity & resilience with dual interpretations.
    Uses data-driven thresholds from population_thresholds.json."""
    from thresholds import shannon_high, shannon_low, pielou_high, pielou_low

    shannon = data.get('Shannon') or 2.0
    pielou = data.get('Pielou') or 0.5

    sh = shannon_high()  # Q75
    sl = shannon_low()   # Q25
    ph = pielou_high()   # Q75

    if pielou >= ph and shannon >= sh:
        state = 'high'
    elif shannon >= sl:
        state = 'moderate'
    else:
        state = 'low'

    interp = DIVERSITY_INTERPRETATIONS[state]
    return {
        'state': state,
        'label': state.capitalize(),
        'scientific': interp['scientific'],
        'non_expert': interp['non_expert'],
        'source_metrics': {'Shannon': shannon, 'Pielou': pielou},
    }


# ══════════════════════════════════════════════════════════════
#     METABOLIC DIALS — Corrected per How_your_gut_is_processing_food.md
# ══════════════════════════════════════════════════════════════

# Descriptions and labels per state — from reference document EXACTLY
DIAL_CONFIG = {
    'main_fuel': {
        'heading': 'Main fuel: carbs or protein?',
        'metric': 'CUR',
        'states': {
            'carb_driven': {
                'label': 'Mainly runs on plant fiber',
                'threshold': 'CUR > +0.3',
                'description': 'Your bacteria prefer dietary fiber and carbohydrates, with minimal protein fermentation.',
                'context': 'This is generally the healthiest pattern. Carbohydrate-driven fermentation produces beneficial short-chain fatty acids that nourish your gut lining, support your immune system, and reduce inflammation. Your gut is running on its preferred fuel.',
            },
            'balanced': {
                'label': 'Balanced',
                'threshold': 'CUR -0.3 to +0.3',
                'description': 'Your bacteria process both carbohydrates and proteins in roughly equal proportions.',
                'context': 'A slight lean toward carbohydrate processing is ideal for gut health, as it produces more beneficial compounds. Your balanced state means your bacteria are versatile, but strengthening the carbohydrate-processing side could boost production of protective short-chain fatty acids.',
            },
            'protein_driven': {
                'label': 'Leans on protein',
                'threshold': 'CUR < -0.3',
                'description': 'Your bacteria are processing more protein than carbohydrates, indicating elevated protein fermentation.',
                'context': 'When bacteria ferment protein instead of fiber, they produce harsher byproducts like ammonia and hydrogen sulfide that can irritate the gut lining. This pattern suggests your fiber-processing bacteria need support to shift the balance back toward gentler, carbohydrate-based fermentation.',
            },
        },
    },
    'fermentation_efficiency': {
        'heading': 'Fermentation efficiency',
        'metric': 'FCR',
        'states': {
            'efficient': {
                'label': 'Efficient assembly line',
                'threshold': 'FCR > +0.3',
                'description': 'Your bacteria efficiently convert intermediate compounds into beneficial short-chain fatty acids.',
                'context': 'This means your bacterial teams are working well together — raw materials from fiber breakdown are being smoothly converted into butyrate and other compounds that protect your gut lining and support your immune system. The assembly line is running at full speed.',
            },
            'ok': {
                'label': 'OK but can improve',
                'threshold': 'FCR -0.3 to +0.3',
                'description': 'Your fermentation pathway works adequately but could be more efficient.',
                'context': 'Your bacteria are producing beneficial compounds, but some intermediate products are not being fully converted. Think of it as an assembly line where some half-finished products pile up instead of being completed. Strengthening the connector bacteria teams could improve this flow.',
            },
            'sluggish': {
                'label': 'Sluggish—too many intermediates',
                'threshold': 'FCR < -0.3',
                'description': 'Without {bottleneck}, your fermentation assembly line has to use backup routes that are less efficient.',
                'context': 'Your gut\'s production line has a bottleneck — intermediate compounds are building up instead of being converted into the beneficial end products your body needs. This means less butyrate (your gut lining\'s primary energy source) is being produced, which may affect gut barrier strength over time.',
            },
        },
    },
    'mucus_dependency': {
        'heading': 'Dependence on your gut lining',
        'metric': 'MDR',
        'states': {
            'diet_fed': {
                'label': 'Mainly fed by your diet',
                'threshold': 'MDR < -0.2',
                'description': 'Your bacteria primarily use dietary fiber for fuel—this is ideal and sustainable.',
                'context': 'Your gut bacteria get their energy from what you eat rather than consuming your gut\'s protective mucus layer. This is the healthiest pattern — your mucus barrier stays intact, protecting you from inflammation and keeping harmful substances out of your bloodstream.',
            },
            'backup': {
                'label': 'Using some mucus as backup',
                'threshold': 'MDR -0.2 to +0.2',
                'description': 'When dietary fiber is limited, your bacteria turn to the mucus layer for fuel—this works short-term but puts stress on your gut barrier.',
                'context': 'Your mucus-layer bacteria are more active than ideal, nibbling at your gut\'s protective coating as a backup fuel source. While this is a normal adaptive response, sustained reliance on mucus can thin your gut barrier over time, potentially allowing irritants to pass through more easily.',
            },
            'heavy_mucus': {
                'label': 'Heavily leaning on mucus',
                'threshold': 'MDR > +0.2',
                'description': 'Your bacteria rely heavily on the gut mucus layer due to insufficient dietary fiber—this creates barrier stress risk.',
                'context': 'Your gut bacteria are significantly eroding your protective mucus layer because there isn\'t enough dietary fiber reaching them. This is like a city consuming its flood defenses — the barrier that keeps harmful substances out is being thinned. Restoring fiber-processing bacteria is a priority to protect your gut lining.',
            },
        },
    },
    'putrefaction_pressure': {
        'heading': 'Smelly / harsh byproducts',
        'metric': 'PPR',
        'states': {
            'scfa_dominant': {
                'label': 'Mostly gentle, SCFA-dominant',
                'threshold': 'PPR < -0.2',
                'description': 'Your beneficial bacteria dominate, producing primarily gentle short-chain fatty acids with minimal harsh byproducts.',
                'context': 'Your gut produces mostly beneficial compounds — butyrate, propionate, and acetate — which nourish your gut lining, regulate inflammation, and even support brain function. The protein-fermenting bacteria that produce harsher chemicals are well contained. This is the ideal metabolic state.',
            },
            'balanced': {
                'label': 'Balanced',
                'threshold': 'PPR -0.2 to +0.2',
                'description': 'Some protein fermentation is happening alongside beneficial compound production.',
                'context': 'Protein fermentation in the gut produces compounds like ammonia and hydrogen sulfide that can irritate the gut lining in excess. In your case, your butyrate-producing bacteria are keeping these harsher byproducts in check — this is a manageable balance, but strengthening the beneficial side would provide more protection.',
            },
            'protein_pressure': {
                'label': 'More pressure from protein breakdown',
                'threshold': 'PPR > +0.2',
                'description': 'Elevated protein fermentation is generating ammonia, hydrogen sulfide, and other harsh metabolites.',
                'context': 'Your gut has shifted toward protein fermentation, producing compounds that can damage the gut lining and promote inflammation. Ammonia raises gut pH (making it less hospitable for beneficial bacteria), while hydrogen sulfide directly irritates cells. Reducing protein-fermenting bacteria and boosting fiber processors is important for long-term gut health.',
            },
        },
    },
}


def _smart_nan_for_dial(ratio_name: str, value, guilds: dict):
    """Smart nan handling — infers metabolic meaning from ecological context."""
    if value is not None:
        return value

    def _get_abund(keyword):
        for gname, gdata in guilds.items():
            if keyword in gname:
                return gdata['abundance']
        return 0.0

    mucin = _get_abund('Mucin')
    proteo = _get_abund('Proteolytic')
    bifido = max(_get_abund('Bifidobacteria'), _get_abund('HMO'))

    if ratio_name == 'PPR' and proteo < 1.0:
        return -0.8
    if ratio_name == 'MDR' and mucin < 1.0:
        return -0.8
    if ratio_name == 'CUR' and proteo < 1.0 and bifido >= 1.0:
        return 0.3
    if ratio_name == 'CUR' and proteo < 1.0 and bifido < 1.0:
        return -0.1
    return 0.0


def _identify_fcr_bottleneck(data: dict) -> str:
    """Deterministic FCR bottleneck identification."""
    guilds = data.get('guilds', {})
    bifido = 0
    cross = 0
    butyrate = 0
    for gname, gdata in guilds.items():
        if 'Bifidobacteria' in gname or 'HMO' in gname:
            bifido = gdata['abundance']
        elif 'Cross' in gname:
            cross = gdata['abundance']
        elif 'Butyrate' in gname:
            butyrate = gdata['abundance']

    if bifido < 1.0:
        return 'Bifidobacteria absent — primary lactate pathway missing'
    elif cross < 6.0:
        return 'insufficient connector bacteria to process fermentation intermediates'
    elif butyrate < 10.0:
        return 'terminal processors depleted — intermediates not converted to butyrate'
    else:
        return 'fermentation intermediates accumulating rather than being converted'


def compute_metabolic_dials(data: dict) -> dict:
    """
    Compute the four metabolic dials.

    CORRECTED thresholds per How_your_gut_is_processing_food.md:
      CUR: >+0.3 carb-driven (favorable), -0.3 to +0.3 balanced, <-0.3 protein-driven (unfavorable)
      FCR: >+0.3 efficient, -0.3 to +0.3 ok, <-0.3 sluggish
      MDR: <-0.2 diet-fed, -0.2 to +0.2 backup, >+0.2 heavy mucus
      PPR: <-0.2 SCFA-dominant, -0.2 to +0.2 balanced, >+0.2 protein pressure
    """
    guilds = data.get('guilds', {})

    cur = _smart_nan_for_dial('CUR', data.get('CUR'), guilds)
    fcr = _smart_nan_for_dial('FCR', data.get('FCR'), guilds)
    mdr = _smart_nan_for_dial('MDR', data.get('MDR'), guilds)
    ppr = _smart_nan_for_dial('PPR', data.get('PPR'), guilds)

    # CUR thresholds: ±0.3
    # POSITIVE CUR = carb guilds winning = carb-driven (favorable)
    # NEGATIVE CUR = proteolytic winning = protein-driven (unfavorable)
    if cur > 0.3:
        fuel_state = 'carb_driven'
    elif cur < -0.3:
        fuel_state = 'protein_driven'
    else:
        fuel_state = 'balanced'

    # FCR thresholds: ±0.3
    if fcr > 0.3:
        ferm_state = 'efficient'
    elif fcr < -0.3:
        ferm_state = 'sluggish'
    else:
        ferm_state = 'ok'

    # MDR thresholds: ±0.2
    if mdr < -0.2:
        lining_state = 'diet_fed'
    elif mdr > 0.2:
        lining_state = 'heavy_mucus'
    else:
        lining_state = 'backup'

    # PPR thresholds: ±0.2
    if ppr < -0.2:
        byproducts_state = 'scfa_dominant'
    elif ppr > 0.2:
        byproducts_state = 'protein_pressure'
    else:
        byproducts_state = 'balanced'

    # Build output
    result = {}
    for dial_key, config in DIAL_CONFIG.items():
        if dial_key == 'main_fuel':
            state = fuel_state
            val = cur
        elif dial_key == 'fermentation_efficiency':
            state = ferm_state
            val = fcr
        elif dial_key == 'mucus_dependency':
            state = lining_state
            val = mdr
        elif dial_key == 'putrefaction_pressure':
            state = byproducts_state
            val = ppr

        state_info = config['states'][state]
        description = state_info['description']

        # Fill bottleneck for sluggish FCR
        if dial_key == 'fermentation_efficiency' and state == 'sluggish':
            bottleneck = _identify_fcr_bottleneck(data)
            description = description.format(bottleneck=bottleneck)

        result[dial_key] = {
            'heading': config['heading'],
            'metric': config['metric'],
            'value': round(val, 3),
            'raw_value': data.get(config['metric']),
            'state': state,
            'label': state_info['label'],
            'description': description,
            'context': state_info.get('context', ''),
        }

        # Add bottleneck info for FCR
        if dial_key == 'fermentation_efficiency' and state == 'sluggish':
            result[dial_key]['bottleneck'] = _identify_fcr_bottleneck(data)

    return result


# ══════════════════════════════════════════════════════════════
#     VITAMIN SYNTHESIS RISK — Unified 0-3 scale
# ══════════════════════════════════════════════════════════════

def compute_vitamin_risks(data: dict) -> dict:
    """Compute vitamin synthesis risk assessment (unified 0-3 scale)."""
    vitamins = data.get('vitamins', {})
    guilds = data.get('guilds', {})

    # Get Bifidobacteria abundance
    bifido_abund = 0
    for gname, gdata in guilds.items():
        if 'Bifidobacteria' in gname or 'HMO' in gname:
            bifido_abund = gdata['abundance']

    result = {}

    # B12 — INVERSE signal + EXPLORATORY MR signal (Hou et al., 2025)
    akkermansia_pct = vitamins.get('akkermansia', 0) if vitamins else 0
    if akkermansia_pct is None:
        akkermansia_pct = 0

    b12_risk = 0
    b12_flags = []

    # Exploratory MR signal: Akkermansia >8% (2× healthy range max)
    if akkermansia_pct > 8:
        b12_risk = 1
        b12_flags.append(f'Akkermansia elevated at {akkermansia_pct:.1f}% (>8% exploratory MR threshold)')

    # Check nominal B12 genera (from MetaPhlAn, Hou et al. 2025)
    b12_genera = data.get('b12_genera', {})
    nominal_elevated = []
    for genus in ['Coprococcus', 'Enterorhabdus', 'Lactococcus']:
        abund = b12_genera.get(genus, 0)
        if abund > 1.0:  # elevated if >1% relative abundance
            nominal_elevated.append(f'{genus} {abund:.1f}%')

    if nominal_elevated:
        b12_flags.append(f'Additional nominal genera elevated: {", ".join(nominal_elevated)}')

    # Build assessment
    if b12_risk == 0 and not nominal_elevated:
        b12_assessment = 'No compositional indicators of B12 deficiency risk from microbiome data.'
    elif b12_risk >= 1:
        b12_assessment = f"Exploratory signal: {'; '.join(b12_flags)}. MR evidence (Hou et al., 2025, FDR<0.05) suggests elevated Akkermansia may be associated with B12 deficiency risk. Serum confirmation recommended."
    else:
        b12_assessment = f"Weak exploratory signal: {'; '.join(b12_flags)}. These are nominal associations (P<0.05, not FDR-corrected). Clinical significance uncertain."

    result['B12'] = {
        'risk_level': b12_risk,
        'risk_label': ['Low', 'Low-moderate', 'Moderate', 'High'][min(b12_risk, 3)],
        'assessment': b12_assessment,
        'akkermansia_pct': akkermansia_pct,
        'exploratory_threshold': 8.0,
        'b12_genera': b12_genera,
        'nominal_genera_elevated': nominal_elevated,
        'note': 'B12 uses INVERSE signal + exploratory MR evidence. Higher Akkermansia (FDR<0.05) paradoxically associated with B12 deficiency risk. Nominal genera (Coprococcus, Enterorhabdus, Lactococcus) provide weaker supporting evidence. Population risk factors (vegan, age>=50) assessed separately.',
    }

    # Folate — diversity-dependent (0-3 risk score)
    folate_risk = vitamins.get('folate_risk') if vitamins else None
    if folate_risk is None:
        folate_risk = 0
    result['folate'] = {
        'risk_level': folate_risk,
        'risk_label': ['Low', 'Low-moderate', 'Moderate', 'High'][min(folate_risk, 3)],
        'assessment': f"Folate risk score {folate_risk}/3. {'Bifidobacteria absent reduces folate production.' if bifido_abund < 2 else 'Bifidobacteria present supports folate production.'}",
        'risk_factors': {
            'shannon_below_2': (data.get('Shannon') or 3) < 2.0,
            'bacteroides_below_5pct': (vitamins.get('bacteroides_genus') or 20) < 5,
            'bifidobacterium_below_2pct': bifido_abund < 2.0,
        },
    }

    # Biotin — limited producer signal (0-4 producers → risk 0-3)
    biotin_producers = vitamins.get('biotin_producers') if vitamins else None
    if biotin_producers is None:
        biotin_producers = 2  # default assumption
    biotin_risk = max(0, 3 - biotin_producers)
    bp = biotin_producers  # shorthand for f-string
    result['biotin'] = {
        'risk_level': min(biotin_risk, 3),
        'risk_label': ['Low', 'Low-moderate', 'Moderate', 'High'][min(biotin_risk, 3)],
        'assessment': f"{bp}/4 biotin-producing species detected. {'Severely limited capacity.' if bp <= 1 else 'Moderate capacity.' if bp <= 2 else 'Adequate capacity.'}",
        'producers_detected': biotin_producers,
        'producers_total': 4,
    }

    # B-complex — composition-dependent (0-3 risk score)
    bcomplex_risk = vitamins.get('bcomplex_risk', 0) if vitamins else 0
    result['B_complex'] = {
        'risk_level': bcomplex_risk if bcomplex_risk else 0,
        'risk_label': ['Low', 'Low-moderate', 'Moderate', 'High'][min(bcomplex_risk or 0, 3)],
        'assessment': f"B-complex risk score {bcomplex_risk}/3. {'Bacteroides protective.' if (vitamins.get('bacteroides_genus') or 0) > 10 else 'Bacteroides below protective threshold.'}",
        'risk_factors': {
            'bacteroides_below_10pct': (vitamins.get('bacteroides_genus') or 20) < 10,
            'fb_ratio_above_1.85': (data.get('FB_ratio') or 1) > 1.85,
            'lachno_rumino_below_2pct': (vitamins.get('lachno_rumino') or 30) < 2,
        },
    }

    return result


# ══════════════════════════════════════════════════════════════
#     BACTERIAL GROUP STATUS TABLE
# ══════════════════════════════════════════════════════════════

# Simplified guild name mapping
GUILD_DISPLAY_NAMES = {
    'Butyrate Producers': 'Butyrate Producers',
    'Fiber Degraders': 'Fiber Degraders',
    'Cross-Feeders': 'Cross-Feeders',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'Mucin Degraders',
    'Proteolytic Dysbiosis Guild': 'Proteolytic Guild',
}

# Non-expert display names for client-facing text
GUILD_NON_EXPERT_NAMES = {
    'Butyrate Producers': 'gut-lining energy producers',
    'Fiber Degraders': 'fiber-processing bacteria',
    'Cross-Feeders': 'intermediate processors',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'mucus-layer bacteria',
    'Proteolytic Dysbiosis Guild': 'protein-fermenting bacteria',
    'Proteolytic Guild': 'protein-fermenting bacteria',
}


def compute_bacterial_groups(data: dict) -> dict:
    """Compute bacterial group status table with CLR and evenness context."""
    guilds = data.get('guilds', {})
    result = {}

    for gname, gdata in guilds.items():
        # Find config
        display_name = GUILD_DISPLAY_NAMES.get(gname, gname)
        config = None
        for cfg_name, cfg in GUILD_CONFIG.items():
            if cfg_name in gname or gname in cfg_name:
                config = cfg
                break

        if config is None:
            continue

        mn, mx, optimal, max_pts, gtype = config
        abund = gdata['abundance']
        clr = gdata.get('clr')
        J = gdata['redundancy']

        # Status
        if abund == 0:
            status = 'Absent — CRITICAL' if gtype == 'beneficial' else 'Absent'
        elif abund < mn:
            status = 'Below range'
        elif abund > mx:
            status = 'Above range'
        else:
            status = 'Within range'

        # CLR status
        if clr is None:
            clr_status = 'Undefined (abundance <1%)'
        elif clr > 1.0:
            clr_status = f'Enriched ({abs(clr):.1f}× geometric mean)'
        elif clr > 0.5:
            clr_status = f'Slightly enriched ({abs(clr):.1f}× GM)'
        elif clr < -1.0:
            clr_status = f'Depleted ({abs(clr):.1f}× below GM)'
        elif clr < -0.5:
            clr_status = f'Slightly depleted'
        else:
            clr_status = 'Balanced'

        # Evenness status
        if J >= 0.70:
            evenness_status = 'High redundancy — resilient'
        elif J >= 0.40:
            evenness_status = 'Moderate redundancy'
        elif J > 0:
            evenness_status = 'Low redundancy — monoculture risk'
        else:
            evenness_status = 'N/A'

        priority = _compute_guild_priority(gname, abund, status, clr, J)
        priority_level = priority['priority_level']

        result[display_name] = {
            'abundance': abund,
            'healthy_range': f'{mn}-{mx}%',
            'status': status,
            'priority_level': priority_level,
            'clr': clr,
            'clr_status': clr_status,
            'evenness': J,
            'evenness_status': evenness_status,
        }

    return result


# ══════════════════════════════════════════════════════════════
#               KEY STRENGTHS & OPPORTUNITIES
# ══════════════════════════════════════════════════════════════

def _clr_description(clr, gtype):
    """Generate CLR description for scientific and non-expert versions."""
    if clr is None:
        return 'CLR undefined (<1%)', 'too few to assess competitive position'
    if gtype == 'beneficial':
        if clr > 1.0:
            return f'CLR {clr:+.2f} enriched — winning competition', 'strongly positioned in the ecosystem'
        elif clr > 0.5:
            return f'CLR {clr:+.2f} slightly enriched', 'holding a good competitive position'
        elif clr > -0.5:
            return f'CLR {clr:+.2f} balanced', 'holding steady in the ecosystem'
        elif clr > -1.0:
            return f'CLR {clr:+.2f} slightly depleted', 'starting to lose ground to other bacteria'
        else:
            return f'CLR {clr:+.2f} depleted — losing competition', 'being outcompeted by other bacteria despite being present'
    else:  # contextual
        if clr > 1.0:
            return f'CLR {clr:+.2f} enriched — overgrowth pressure', 'growing too aggressively in the ecosystem'
        elif clr > 0.5:
            return f'CLR {clr:+.2f} slightly enriched', 'gaining competitive advantage'
        elif clr > -0.5:
            return f'CLR {clr:+.2f} balanced', 'well-controlled'
        else:
            return f'CLR {clr:+.2f} depleted', 'naturally kept in check'


def identify_key_strengths(data: dict) -> list:
    """Identify key strengths with dual {scientific, non_expert} format and CLR context."""
    strengths = []
    guilds = data.get('guilds', {})
    dysbiosis = data.get('dysbiosis', {})

    for gname, gdata in guilds.items():
        for cfg_name, (mn, mx, opt, pts, gtype) in GUILD_CONFIG.items():
            if cfg_name in gname or gname in cfg_name:
                display = GUILD_DISPLAY_NAMES.get(gname, gname.split()[0])
                ne_name = GUILD_NON_EXPERT_NAMES.get(gname, GUILD_NON_EXPERT_NAMES.get(display, display.lower()))
                clr = gdata.get('clr')
                J = gdata.get('redundancy', 0)
                clr_sci, clr_ne = _clr_description(clr, gtype)

                if gtype == 'beneficial' and gdata['abundance'] >= mn:
                    if gdata['abundance'] >= opt:
                        strengths.append({
                            'scientific': f"Strong {display} at {gdata['abundance']:.1f}% (optimal range, {clr_sci}, J={J:.2f})",
                            'non_expert': f"Your {ne_name} are performing well — well-staffed and {clr_ne}",
                        })
                    else:
                        strengths.append({
                            'scientific': f"{display} within healthy range at {gdata['abundance']:.1f}% ({clr_sci}, J={J:.2f})",
                            'non_expert': f"Your {ne_name} are within the healthy range and {clr_ne}",
                        })
                break

    fcr = data.get('FCR')
    mdr = data.get('MDR')
    ppr = data.get('PPR')

    if fcr is not None and fcr > 0.3:
        strengths.append({
            'scientific': f"Efficient fermentation (FCR {fcr:+.2f}) — intermediate conversion operating well",
            'non_expert': "Your gut's fermentation assembly line is running efficiently — converting raw materials into beneficial compounds smoothly",
        })
    if mdr is not None and mdr < -0.2:
        strengths.append({
            'scientific': f"Diet-fed ecosystem (MDR {mdr:+.2f}) — minimal host-substrate dependency",
            'non_expert': "Your bacteria get their fuel from what you eat, not from your gut's protective lining — this is the ideal pattern",
        })
    elif mdr is None:
        for gname, gdata in guilds.items():
            if 'Mucin' in gname and gdata['abundance'] < 1.0:
                strengths.append({
                    'scientific': "Diet-fed ecosystem — mucin degraders near-absent (<1%)",
                    'non_expert': "Your bacteria get their fuel from what you eat, keeping your gut's protective lining intact",
                })
                break
    if ppr is not None and ppr < -0.2:
        strengths.append({
            'scientific': f"SCFA-dominant metabolism (PPR {ppr:+.2f}) — minimal putrefaction pressure",
            'non_expert': "Your gut produces mostly gentle, beneficial compounds with minimal harsh byproducts",
        })
    elif ppr is None:
        for gname, gdata in guilds.items():
            if 'Proteolytic' in gname and gdata['abundance'] < 1.0:
                strengths.append({
                    'scientific': "Minimal protein fermentation pressure — proteolytic guild near-absent (<1%)",
                    'non_expert': "Your gut has very little protein fermentation happening, which means fewer harsh byproducts",
                })
                break

    if all(v == 0 for v in dysbiosis.values()):
        strengths.append({
            'scientific': "No dysbiosis-associated taxa detected (F. nucleatum, S. gallolyticus, P. anaerobius, E-S all absent)",
            'non_expert': "No harmful bacteria were detected — your gut has a clean safety profile",
        })

    shannon = data.get('Shannon') or 0
    if shannon >= 3.0:
        strengths.append({
            'scientific': f"High species diversity (Shannon {shannon:.2f}) — broad functional redundancy",
            'non_expert': "Your gut has a rich variety of bacterial species — this diversity helps your ecosystem adapt and stay resilient",
        })

    high_J = []
    for g, d in guilds.items():
        if d['redundancy'] >= 0.80 and d['abundance'] >= 2.0:
            high_J.append(GUILD_DISPLAY_NAMES.get(g, g.split()[0]))
    if high_J:
        strengths.append({
            'scientific': f"Exceptional evenness (J≥0.80) in {', '.join(high_J)} — strong species backup within these guilds",
            'non_expert': f"Your {', '.join([GUILD_NON_EXPERT_NAMES.get(g, g.lower()) for g in high_J])} teams have excellent backup diversity — no single species dominates",
        })

    return strengths[:6]


def identify_key_opportunities(data: dict) -> list:
    """Identify key opportunities with dual {scientific, non_expert} format and CLR context."""
    opportunities = []
    guilds = data.get('guilds', {})

    for gname, gdata in guilds.items():
        for cfg_name, (mn, mx, opt, pts, gtype) in GUILD_CONFIG.items():
            if cfg_name in gname or gname in cfg_name:
                display = GUILD_DISPLAY_NAMES.get(gname, gname.split()[0])
                ne_name = GUILD_NON_EXPERT_NAMES.get(gname, GUILD_NON_EXPERT_NAMES.get(display, display.lower()))
                clr = gdata.get('clr')
                clr_sci, clr_ne = _clr_description(clr, gtype)

                if gtype == 'beneficial' and gdata['abundance'] < mn:
                    opportunities.append({
                        'scientific': f"Expand {display} ({gdata['abundance']:.1f}% → {mn}-{mx}% target, {clr_sci})",
                        'non_expert': f"Your {ne_name} team needs more members to reach full capacity",
                    })
                elif gtype == 'contextual' and gdata['abundance'] > mx:
                    opportunities.append({
                        'scientific': f"Reduce {display} overgrowth ({gdata['abundance']:.1f}% vs {mx}% max, {clr_sci})",
                        'non_expert': f"Your {ne_name} have grown too large and need to be brought back into balance",
                    })
                break

    # CLR-specific: within range but losing competition
    for gname, gdata in guilds.items():
        if gdata.get('clr') is not None and gdata['clr'] < -1.0:
            for cfg_name, (mn, mx, opt, pts, gtype) in GUILD_CONFIG.items():
                if (cfg_name in gname or gname in cfg_name) and gtype == 'beneficial':
                    display = GUILD_DISPLAY_NAMES.get(gname, gname.split()[0])
                    ne_name = GUILD_NON_EXPERT_NAMES.get(gname, GUILD_NON_EXPERT_NAMES.get(display, display.lower()))
                    opportunities.append({
                        'scientific': f"Restore {display} competitive position (CLR {gdata['clr']:+.2f} — depleted despite {'adequate' if gdata['abundance'] >= mn else 'low'} abundance)",
                        'non_expert': f"Your {ne_name} are present but being outcompeted by other bacteria — they need support to regain their position",
                    })
                    break

    fcr = data.get('FCR')
    if fcr is not None and fcr < -0.3:
        opportunities.append({
            'scientific': f"Restore fermentation efficiency (FCR {fcr:+.2f} — intermediate processing bottleneck)",
            'non_expert': "Your gut's production line has a bottleneck — intermediate compounds aren't being fully converted into beneficial products",
        })

    smithii = data.get('smithii_abundance', 0)
    if smithii > 10:
        opportunities.append({
            'scientific': f"Address M. smithii overgrowth ({smithii:.1f}% — methane-producing archaea)",
            'non_expert': f"An unusual methane-producing organism has grown larger than expected and may need attention",
        })

    return opportunities[:5]


# ══════════════════════════════════════════════════════════════
#    GUILD SCENARIO CLASSIFICATION (9-Scenario Matrix)
# ══════════════════════════════════════════════════════════════

# Display names for platform
GUILD_DISPLAY_NAMES_SCENARIO = {
    'Butyrate Producers': 'Gut-Lining Energy Producers',
    'Fiber Degraders': 'Fiber-Processing Bacteria',
    'Cross-Feeders': 'Intermediate Processors',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'Mucus-Layer Bacteria',
    'Proteolytic Dysbiosis Guild': 'Protein-Fermenting Bacteria',
}

# 9-scenario matrix: (range_tier, clr_tier) → (scenario, action, severity)
SCENARIO_MATRIX = {
    ('above', 'enriched'):    ('OVERGROWTH', 'Reduce', 'critical'),
    ('above', 'balanced'):    ('ABUNDANT', 'Monitor', 'moderate'),
    ('above', 'suppressed'):  ('CROWDED', 'Unusual', 'moderate'),
    ('within', 'enriched'):   ('THRIVING', 'Optimal', 'healthy'),
    ('within', 'balanced'):   ('HEALTHY', 'Good', 'healthy'),
    ('within', 'suppressed'): ('UNDER PRESSURE', 'Support', 'attention'),
    ('below', 'enriched'):    ('SUBSTRATE LIMITED', 'Dietary', 'attention'),
    ('below', 'balanced'):    ('UNDERSTAFFED', 'Expand', 'attention'),
    ('below', 'suppressed'):  ('DEPLETED', 'Critical', 'critical'),
}

# Emoji mapping for scenarios
SCENARIO_EMOJI = {
    'critical': '⛔',
    'attention': '⚠️',
    'moderate': '🔶',
    'healthy': '✅',
}


def _classify_single_guild_scenario(abundance: float, range_min: float,
                                     range_max: float, clr_value, guild_type: str) -> dict:
    """
    Classify a single guild into one of 9 scenarios based on two axes:
    
    Axis 1 (Range Position): Where is this guild relative to its healthy reference range?
      - range_position = (abundance - range_min) / (range_max - range_min)
      - < 0: below range, 0-1: within range, > 1: above range
    
    Axis 2 (Competition Signal): How does this guild compete for ecological space?
      - CLR > +0.3: enriched (competitive advantage)
      - CLR -0.3 to +0.3: balanced (neutral)
      - CLR < -0.3: suppressed (competitive disadvantage)
    
    Returns dict with scenario classification + both axis values.
    """
    # Axis 1: Range position (normalized 0-1 within range)
    range_span = range_max - range_min
    range_position = (abundance - range_min) / range_span if range_span > 0 else 0.5
    
    if range_position > 1:
        range_tier = 'above'
        range_label = f"Above range ({abundance:.1f}% vs {range_max:.0f}% max)"
    elif range_position >= 0:
        if range_position > 0.7:
            range_label = f"Well within range ({abundance:.1f}%)"
        elif range_position > 0.2:
            range_label = f"Within range ({abundance:.1f}%)"
        else:
            range_label = f"Just within range ({abundance:.1f}%)"
        range_tier = 'within'
    else:
        range_tier = 'below'
        range_label = f"Below range ({abundance:.1f}% vs {range_min:.0f}% min)"
    
    # Axis 2: CLR competition signal
    if clr_value is None:
        clr_tier = 'balanced'
        clr_label = "CLR unavailable"
    elif clr_value > 0.3:
        clr_tier = 'enriched'
        clr_label = f"Competitive ({clr_value:+.2f})"
    elif clr_value < -0.3:
        clr_tier = 'suppressed'
        if clr_value < -2.0:
            clr_label = f"Marginal ({clr_value:+.2f})"
        else:
            clr_label = f"Suppressed ({clr_value:+.2f})"
    else:
        clr_tier = 'balanced'
        clr_label = f"Balanced ({clr_value:+.2f})"
    
    # Contextual guild override: below range is favorable for proteolytic
    if guild_type == 'contextual' and range_tier == 'below':
        scenario = 'FAVORABLE'
        action = 'Good'
        severity = 'healthy'
        combined = f"Below range (favorable for this guild type)"
    elif guild_type == 'contextual' and range_tier == 'above':
        # Use matrix but escalate severity
        scenario, action, severity = SCENARIO_MATRIX.get((range_tier, clr_tier),
                                                          ('UNKNOWN', 'Review', 'moderate'))
        combined = f"{scenario} — contextual guild overgrowth"
    else:
        scenario, action, severity = SCENARIO_MATRIX.get((range_tier, clr_tier),
                                                          ('UNKNOWN', 'Review', 'moderate'))
        combined = f"{scenario}"
    
    emoji = SCENARIO_EMOJI.get(severity, '❓')
    
    return {
        'scenario': scenario,
        'action': action,
        'severity': severity,
        'emoji': emoji,
        'combined_assessment': f"{emoji} {combined}",
        'range_position': round(range_position, 2),
        'range_tier': range_tier,
        'range_label': range_label,
        'clr_tier': clr_tier,
        'clr_label': clr_label,
        'clr_value': round(clr_value, 2) if clr_value is not None else None,
        'abundance_pct': round(abundance, 2),
        'range_min_pct': range_min,
        'range_max_pct': range_max,
    }


def compute_guild_scenarios(data: dict) -> list:
    """
    Compute 9-scenario matrix classification for all guilds.
    
    Combines two axes:
    - Axis 1: Range Position (abundance vs healthy reference range)
    - Axis 2: Competition Signal (CLR competitive position)
    
    Returns list of guild scenario dicts, ordered by ecological priority.
    """
    guilds = data.get('guilds', {})
    scenarios = []
    
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
        abundance = gdata['abundance']
        clr = gdata.get('clr')
        display_name = GUILD_DISPLAY_NAMES_SCENARIO.get(config_key, config_key)
        
        # Convert range from proportion to percentage for display
        abundance_pct = abundance
        mn_pct = mn
        mx_pct = mx
        
        scenario = _classify_single_guild_scenario(
            abundance_pct, mn_pct, mx_pct, clr, gtype
        )
        
        scenario['guild_key'] = config_key
        scenario['guild_display'] = display_name
        scenario['guild_type'] = gtype
        scenario['range_str'] = f"{mn_pct:.0f}-{mx_pct:.0f}%"
        scenario['evenness'] = gdata.get('redundancy')
        
        scenarios.append(scenario)
    
    return scenarios


# ══════════════════════════════════════════════════════════════
#                    ASSEMBLE ALL FIELDS
# ══════════════════════════════════════════════════════════════

def compute_overview_fields(data: dict) -> dict:
    """Compute all deterministic fields."""
    return {
        'overall_balance': classify_overall_balance(data),
        'diversity_resilience': classify_diversity_resilience(data),
        'metabolic_dials': compute_metabolic_dials(data),
        'vitamin_risks': compute_vitamin_risks(data),
        'bacterial_groups': compute_bacterial_groups(data),
        'key_strengths': identify_key_strengths(data),
        'key_opportunities': identify_key_opportunities(data),
        'guild_scenarios': compute_guild_scenarios(data),
    }
