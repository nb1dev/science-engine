"""
guild_priority.py — Single source of truth for guild priority calculation.

All pipeline consumers (formulation, report, dashboard, narrative) import from here.
No other file should independently compute priority scores or labels.

Formula: priority = importance × state × evenness_modifier
See formulation_automation/documentation/PRIORITY_SYSTEM_CHANGELOG.md
"""

from typing import Dict, List, Tuple, Optional


# ─── CONSTANTS ────────────────────────────────────────────────────────────────

PRIORITY_RANK_MAP = {"CRITICAL": 0, "1A": 1, "1B": 2, "Monitor": 3}
PRIORITY_COLOR_MAP = {"CRITICAL": "red", "1A": "orange", "1B": "amber", "Monitor": "teal"}
PRIORITY_HEX_MAP = {"CRITICAL": "#e74c3c", "1A": "#e67e22", "1B": "#f39c12", "Monitor": "#2ecc71"}

# ─── GUILD NAME MAPPINGS (SINGLE SOURCE OF TRUTH) ────────────────────────────
# All pipeline consumers import these instead of maintaining separate copies.
# Keys = internal/analysis guild names, values = display variants.

# Fixed display order for platform (analysis pipeline guild names)
GUILD_ORDER = [
    'Fiber Degraders',
    'HMO/Oligosaccharide-Utilising Bifidobacteria',
    'Cross-Feeders',
    'Butyrate Producers',
    'Proteolytic Dysbiosis Guild',
    'Mucin Degraders',
]

# Scientific display names (used in report/platform)
GUILD_DISPLAY_NAMES = {
    'Butyrate Producers': 'Butyrate Producers',
    'Fiber Degraders': 'Fiber Degraders',
    'Cross-Feeders': 'Cross-Feeders',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'Mucin Degraders',
    'Proteolytic Dysbiosis Guild': 'Proteolytic Guild',
}

# Client-facing (non-expert) display names
# Canonical set: Fibre Digesters, Bifidobacteria, Nutrient Recyclers,
#                Gut Wall Protectors, Protein Recyclers, Gut Lining Processors
GUILD_CLIENT_NAMES = {
    'Butyrate Producers': 'Gut Wall Protectors',
    'Fiber Degraders': 'Fibre Digesters',
    'Cross-Feeders': 'Nutrient Recyclers',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'Gut Lining Processors',
    'Proteolytic Dysbiosis Guild': 'Protein Recyclers',
    'Proteolytic Guild': 'Protein Recyclers',
}

# Non-expert names for narrative text (lowercase style)
GUILD_NON_EXPERT_NAMES = {
    'Butyrate Producers': 'gut wall protectors',
    'Fiber Degraders': 'fibre digesters',
    'Cross-Feeders': 'nutrient recyclers',
    'HMO/Oligosaccharide-Utilising Bifidobacteria': 'Bifidobacteria',
    'Bifidobacteria': 'Bifidobacteria',
    'Mucin Degraders': 'gut lining processors',
    'Proteolytic Dysbiosis Guild': 'protein recyclers',
    'Proteolytic Guild': 'protein recyclers',
}

# Guild type classification (for formulation logic)
HARMFUL_GUILD_NAMES = {
    "proteolytic", "proteolytic guild", "proteolytic dysbiosis guild",
    "protein-fermenting bacteria",
}
MUCIN_GUILD_NAMES = {
    "mucin_degraders", "mucin degraders", "mucus-layer bacteria",
}

# Guild importance weights — ecological role
# All known name variants included for cross-pipeline compatibility
_IMPORTANCE_RAW = {
    "fiber_degraders": 1.0,
    "Fiber Degraders": 1.0,
    "Fiber-Processing Bacteria": 1.0,
    "Fibre Digesters": 1.0,
    "butyrate_producers": 1.2,
    "Butyrate Producers": 1.2,
    "Gut-Lining Energy Producers": 1.2,
    "Gut Wall Protectors": 1.2,
    "bifidobacteria": 0.9,
    "Bifidobacteria": 0.9,
    "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.9,
    "cross_feeders": 1.1,
    "Cross-Feeders": 1.1,
    "Intermediate Processors": 1.1,
    "Nutrient Recyclers": 1.1,
    "proteolytic": 1.1,
    "Proteolytic Guild": 1.1,
    "Proteolytic Dysbiosis Guild": 1.1,
    "Protein-Fermenting Bacteria": 1.1,
    "Protein Recyclers": 1.1,
    "mucin_degraders": 0.6,
    "Mucin Degraders": 0.6,
    "Mucus-Layer Bacteria": 0.6,
    "Gut Lining Processors": 0.6,
}

# Beneficial guild identifiers (all name variants)
_BENEFICIAL_NAMES = {
    "fiber_degraders", "Fiber Degraders", "Fiber-Processing Bacteria", "Fibre Digesters",
    "butyrate_producers", "Butyrate Producers", "Gut-Lining Energy Producers", "Gut Wall Protectors",
    "bifidobacteria", "Bifidobacteria", "HMO/Oligosaccharide-Utilising Bifidobacteria",
    "cross_feeders", "Cross-Feeders", "Intermediate Processors", "Nutrient Recyclers",
}

# State values from 9-scenario matrix
BENEFICIAL_STATE_VALUES = {
    "DEPLETED": 10, "UNDERSTAFFED": 7, "SUBSTRATE LIMITED": 5,
    "UNDER PRESSURE": 3, "CROWDED": 1,
    "ABUNDANT": 0, "THRIVING": 0, "HEALTHY": 0, "FAVORABLE": 0,
}

CONTEXTUAL_STATE_VALUES = {
    "OVERGROWTH": 10, "ABUNDANT": 6, "CROWDED": 4,
    "FAVORABLE": 0, "HEALTHY": 0, "THRIVING": 0,
    "UNDERSTAFFED": 0, "DEPLETED": 0, "SUBSTRATE LIMITED": 0, "UNDER PRESSURE": 0,
}

# ─── CLIENT-FACING SCENARIO LABELS (SINGLE SOURCE OF TRUTH) ────────────────
# Maps internal scenario codes to non-expert display names.
# All section builders import these — no renderer invents its own wording.

# Beneficial guild labels: full 3×3 matrix, each cell is distinct
BENEFICIAL_CLIENT_LABELS = {
    "OVERGROWTH":       "Flourishing",        # above + enriched
    "ABUNDANT":         "Abundant",            # above + balanced
    "CROWDED":          "Abundant bloom",      # above + suppressed
    "THRIVING":         "Thriving",            # within + enriched
    "HEALTHY":          "Healthy",             # within + balanced
    "UNDER PRESSURE":   "Struggling",          # within + suppressed
    "SUBSTRATE LIMITED":"Hungry",              # below + enriched
    "UNDERSTAFFED":     "Running low",         # below + balanced
    "DEPLETED":         "Exhausted",           # below + suppressed
}

# Contextual (opportunistic) guild labels
# FAVORABLE collapses within+below (both good), so range_tier disambiguates
CONTEXTUAL_CLIENT_LABELS = {
    "OVERGROWTH":       "Taking over",         # above + enriched — worst
    "ABUNDANT":         "Elevated",            # above + balanced
    "CROWDED":          "High but contained",  # above + suppressed
}
CONTEXTUAL_FAVORABLE_LABELS = {
    "within":           "In check",            # within range — favorable
    "below":            "Minimal",             # below range — favorable
}

# Special case: absent beneficial guild (abundance == 0)
ABSENT_CLIENT_LABEL = "Missing"


def client_label(scenario: str, beneficial: bool, range_tier: str,
                 abundance: float = None) -> str:
    """Return client-facing label for a scenario.

    Args:
        scenario: Internal scenario code from classify_scenario()
        beneficial: True for beneficial guilds, False for contextual
        range_tier: "above", "within", or "below"
        abundance: Guild abundance (used to detect absent guilds)

    Returns:
        Non-expert display label string
    """
    # Absent beneficial guild
    if beneficial and abundance is not None and abundance == 0:
        return ABSENT_CLIENT_LABEL

    if beneficial:
        return BENEFICIAL_CLIENT_LABELS.get(scenario, scenario)

    # Contextual guild
    if scenario == "FAVORABLE":
        return CONTEXTUAL_FAVORABLE_LABELS.get(range_tier, "In check")

    return CONTEXTUAL_CLIENT_LABELS.get(scenario, scenario)


# ─── CORE FUNCTIONS ──────────────────────────────────────────────────────────

def get_importance(guild_name: str) -> float:
    """Get importance weight for a guild by any name variant."""
    if guild_name in _IMPORTANCE_RAW:
        return _IMPORTANCE_RAW[guild_name]
    # Fuzzy fallback
    nl = guild_name.lower()
    for key, val in _IMPORTANCE_RAW.items():
        if key.lower() == nl:
            return val
    return 1.0


def is_beneficial(guild_name: str) -> bool:
    """Check if guild is beneficial (vs contextual) by any name variant."""
    if guild_name in _BENEFICIAL_NAMES:
        return True
    nl = guild_name.lower()
    return any(b.lower() == nl for b in _BENEFICIAL_NAMES)


def get_range_tier(status: str, abundance: float) -> str:
    """Derive range tier from status string and abundance.

    Exposed as a helper so callers (e.g. client_label) can reuse
    the same logic without duplicating it.
    """
    if "Below" in status or "Absent" in status or abundance == 0:
        return "below"
    elif "Above" in status:
        return "above"
    return "within"


def classify_scenario(status: str, abundance: float, clr: Optional[float],
                      beneficial: bool) -> str:
    """Classify guild into 9-scenario matrix.

    Args:
        status: Guild status string (e.g. "Below range", "Absent — CRITICAL", "Above range")
        abundance: Guild abundance percentage
        clr: CLR value (None if undefined)
        beneficial: True for beneficial guilds, False for contextual
    """
    range_tier = get_range_tier(status, abundance)

    # CLR tier
    if clr is None:
        clr_tier = "balanced"
    elif clr > 0.3:
        clr_tier = "enriched"
    elif clr < -0.3:
        clr_tier = "suppressed"
    else:
        clr_tier = "balanced"

    # Contextual guilds: below/within = favorable
    if not beneficial and range_tier in ("below", "within"):
        return "FAVORABLE"

    SCENARIO_MAP = {
        ("above", "enriched"): "OVERGROWTH",
        ("above", "balanced"): "ABUNDANT",
        ("above", "suppressed"): "CROWDED",
        ("within", "enriched"): "THRIVING",
        ("within", "balanced"): "HEALTHY",
        ("within", "suppressed"): "UNDER PRESSURE",
        ("below", "enriched"): "SUBSTRATE LIMITED",
        ("below", "balanced"): "UNDERSTAFFED",
        ("below", "suppressed"): "DEPLETED",
    }
    return SCENARIO_MAP.get((range_tier, clr_tier), "HEALTHY")


def compute_evenness_modifier(evenness: float, is_contextual: bool,
                               state_value: float) -> float:
    """Asymmetric evenness modifier. Only applies when state > 0."""
    if state_value == 0:
        return 1.0
    if evenness is None:
        evenness = 0.0
    if evenness < 0.40:
        return 1.3 if is_contextual else 1.2
    elif evenness < 0.70:
        return 1.1
    return 1.0


def score_to_label(score: float) -> str:
    """Convert priority score to label. Single threshold source."""
    if score >= 8.0:
        return "CRITICAL"
    elif score >= 5.0:
        return "1A"
    elif score >= 2.0:
        return "1B"
    return "Monitor"


def compute_guild_priority(guild_name: str, abundance: float, status: str,
                            clr: Optional[float], evenness: Optional[float]
                            ) -> Dict:
    """Compute priority for a single guild. Returns full detail dict.

    This is THE canonical computation — all consumers call this.
    """
    beneficial = is_beneficial(guild_name)
    importance = get_importance(guild_name)
    J = evenness if evenness is not None else 0.5

    range_tier = get_range_tier(status, abundance)
    scenario = classify_scenario(status, abundance, clr, beneficial)

    if not beneficial:
        state_value = CONTEXTUAL_STATE_VALUES.get(scenario, 0)
    else:
        state_value = BENEFICIAL_STATE_VALUES.get(scenario, 0)

    evenness_mod = compute_evenness_modifier(J, not beneficial, state_value)
    score = round(importance * state_value * evenness_mod, 2)
    label = score_to_label(score)
    cl = client_label(scenario, beneficial, range_tier, abundance)

    return {
        "priority_level": label,
        "priority_score": score,
        "priority_rank": PRIORITY_RANK_MAP.get(label, 3),
        "scenario": scenario,
        "client_label": cl,
        "range_tier": range_tier,
        "color": PRIORITY_COLOR_MAP.get(label, "teal"),
        "color_hex": PRIORITY_HEX_MAP.get(label, "#2ecc71"),
        "importance_weight": importance,
        "state_value": state_value,
        "evenness_modifier": evenness_mod,
        "is_beneficial": beneficial,
    }


def build_priority_list(guilds: Dict) -> List[Dict]:
    """Build canonical sorted priority list from guild data.

    Accepts guild dicts with EITHER formulation-style keys
    (abundance_pct, status, clr, evenness/redundancy) OR
    report-style keys (abundance, status, clr, evenness/redundancy).

    Returns list sorted by priority_score descending.
    """
    results = []
    for gname, gdata in guilds.items():
        # Handle both key conventions
        abundance = gdata.get("abundance_pct", gdata.get("abundance", 0)) or 0
        status = gdata.get("status", "")
        clr = gdata.get("clr")
        evenness = gdata.get("evenness", gdata.get("redundancy"))

        priority = compute_guild_priority(gname, abundance, status, clr, evenness)
        display_name = gdata.get("name", gname)

        # Action text
        if priority["is_beneficial"] and ("Below" in status or "Absent" in status or abundance == 0):
            action = f"Restore/expand {display_name} (currently {abundance:.1f}%)"
        elif not priority["is_beneficial"] and "Above" in status:
            action = f"Reduce {display_name} overgrowth (currently {abundance:.1f}%)"
        elif priority["state_value"] > 0:
            action = f"Support {display_name} ({priority['scenario'].lower()}, {abundance:.1f}%)"
        else:
            action = f"Maintain {display_name} ({status})"

        results.append({
            "guild_key": gname,
            "guild_name": display_name,
            "abundance_pct": abundance,
            "status": status,
            "clr": clr,
            "evenness": evenness,
            "action": action,
            **priority,
        })

    results.sort(key=lambda x: -x["priority_score"])
    return results


def format_priority_text(guilds: Dict) -> str:
    """Format priority list as text for LLM context injection."""
    items = build_priority_list(guilds)
    lines = []
    for item in items:
        if item["priority_score"] > 0:
            lines.append(f"  {item['priority_level']}: {item['guild_name']} "
                         f"({item['abundance_pct']:.1f}%, {item['status']}) "
                         f"— score {item['priority_score']}")
        else:
            lines.append(f"  Monitor: {item['guild_name']} "
                         f"({item['abundance_pct']:.1f}%, {item['status']})")
    return "\n".join(lines) if lines else "  All guilds at Monitor priority"
