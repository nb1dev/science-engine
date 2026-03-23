#!/usr/bin/env python3
"""
LLM Prebiotic Design — Design prebiotic blend for powder jar.

Input:  Selected mix, sensitivity, digestive symptoms, prebiotic rules KB.
Output: Structured JSON with strategy, prebiotics, condition_specific_additions.

v2: Replaced flat must_retain_at_minimum guard with substrate_necessity model.
    Guard now reads priority tiers and can_be_omitted_if conditions from KB,
    selects the minimum viable substrate set, and rebalances non-priority
    components to stay within prebiotic_range["max_g"].

v2.1: Added SIBO self-report detection from questionnaire fields.
      Fixed P3 skip to use explicit LLM boolean (sensitivity_override_active)
      rather than inferring from free-text overrides_applied list.
      Added 10% ceiling tolerance (prebiotic_tolerance_pct from KB).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .bedrock_client import call_bedrock, extract_json_from_response

KB_DIR = Path(__file__).parent.parent / "knowledge_base"

# ---------------------------------------------------------------------------
# Canonical substance name resolution
# ---------------------------------------------------------------------------

_SUBSTANCE_ALIASES: Dict[str, List[str]] = {
    "GOS":              ["gos", "galacto-oligosaccharide", "galactooligosaccharide"],
    "Inulin":           ["inulin", "pure inulin", "inulin (pure)"],
    "FOS":              ["fos", "fructo-oligosaccharide", "fructooligosaccharide",
                         "short-chain fos", "short chain fos"],
    "Psyllium":         ["psyllium", "psyllium husk", "psyllium/arabinoxylan",
                         "psyllium husk/arabinoxylan"],
    "Beta-Glucans":     ["beta-glucans", "beta glucans", "oat beta-glucan"],
    "PHGG":             ["phgg", "partially hydrolyzed guar gum"],
    "Resistant Starch": ["resistant starch", "rs", "hi-maize"],
    "Lactulose":        ["lactulose"],
}

_FODMAP_SUBSTANCES = {"GOS", "Inulin", "FOS", "Lactulose"}


def _load_kb(filename: str) -> Dict:
    with open(KB_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def _canonical(s: str) -> str:
    """Normalise a substance name to its canonical KB form."""
    low = s.lower().strip()
    for canon, aliases in _SUBSTANCE_ALIASES.items():
        if low in aliases:
            return canon
    return s


def _dose_of(substance: str, prebiotics: List[Dict]) -> float:
    """Current dose_g of substance in blend (0.0 if absent)."""
    canon = _canonical(substance)
    for p in prebiotics:
        if _canonical(p.get("substance", "")) == canon:
            return float(p.get("dose_g", 0.0))
    return 0.0


def _set_dose(substance: str, new_dose: float, prebiotics: List[Dict], reason: str) -> str:
    """Raise existing entry to new_dose, or inject new entry. Returns 'raised'/'injected'."""
    canon = _canonical(substance)
    for p in prebiotics:
        if _canonical(p.get("substance", "")) == canon:
            p["dose_g"] = new_dose
            p["rationale"] = p.get("rationale", "") + f" [substrate_guard: raised to {new_dose}g — {reason}]"
            return "raised"
    prebiotics.append({
        "substance": substance,
        "dose_g": new_dose,
        "fodmap": substance in _FODMAP_SUBSTANCES,
        "rationale": f"Injected by substrate_guard at {new_dose}g — {reason}",
    })
    return "injected"


# ---------------------------------------------------------------------------
# SIBO self-report detection from questionnaire
# ---------------------------------------------------------------------------

def _sibo_self_reported(unified_input: Dict) -> bool:
    """
    Return True if client mentions SIBO in any questionnaire free-text or
    structured diagnosis field. Checks:
      - questionnaire.medical.diagnoses (array of keys)
      - questionnaire.medical.diagnoses_other (free text)
      - questionnaire.additional_context_notes (step_8 free text)
    Case-insensitive substring match for "sibo".
    """
    questionnaire = unified_input.get("questionnaire", {})
    medical = questionnaire.get("medical", {})

    # Structured diagnoses array
    diagnoses = medical.get("diagnoses", [])
    if any("sibo" in str(d).lower() for d in diagnoses):
        return True

    # Free-text fields
    free_text_fields = [
        medical.get("diagnoses_other", ""),
        questionnaire.get("additional_context_notes", ""),
        questionnaire.get("additional_context", ""),
    ]
    return any("sibo" in (t or "").lower() for t in free_text_fields)


# ---------------------------------------------------------------------------
# can_be_omitted_if evaluator
# ---------------------------------------------------------------------------
# Condition strings use simple boolean expressions:
#   "Inulin >= 0.5 AND GOS >= 0.5"
#   "GOS >= 1.0 AND (PHGG >= 1.5 OR Beta-Glucans >= 1.5)"
#   "SIBO_override"
#   None  →  never omittable

def _eval_omit_condition(
    condition: Optional[str],
    prebiotics: List[Dict],
    context_flags: Dict,
) -> bool:
    """Return True if the omission condition is satisfied."""
    if condition is None:
        return False

    expr = condition

    # Named boolean flags
    expr = expr.replace("SIBO_override",
                        "True" if context_flags.get("sibo_override_active") else "False")
    expr = expr.replace("bloating_override",
                        "True" if context_flags.get("bloating_override_active") else "False")

    # Replace substance names with their current dose values (longest first)
    for canon in sorted(_SUBSTANCE_ALIASES.keys(), key=len, reverse=True):
        dose = _dose_of(canon, prebiotics)
        expr = re.sub(rf"\b{re.escape(canon)}\b", str(dose), expr)
        for alias in _SUBSTANCE_ALIASES[canon]:
            expr = re.sub(rf"\b{re.escape(alias)}\b", str(dose), expr, flags=re.IGNORECASE)

    expr = re.sub(r"\bAND\b", "and", expr)
    expr = re.sub(r"\bOR\b", "or", expr)

    try:
        return bool(eval(expr, {"__builtins__": {}}))  # noqa: S307 — controlled KB expression
    except Exception:
        return False  # conservative: don't omit if expression is unparseable


# ---------------------------------------------------------------------------
# Headroom rebalancer
# ---------------------------------------------------------------------------

def _make_headroom(
    prebiotics: List[Dict],
    deficit_g: float,
    max_g: float,
    effective_ceiling: float,
    protected: set,
) -> Tuple[float, List[Dict]]:
    """
    Proportionally reduce non-protected, non-FODMAP components to free up
    space for required substrate injection. Returns (freed_g, rebalance_log).

    Uses effective_ceiling (max_g * tolerance) rather than hard max_g so that
    the 10% tolerance absorbs minor floor injections without triggering warnings.

    Protected substances are those already at or above their floor.
    We never reduce FODMAP components to make room — required FODMAP floors
    come out of the non-FODMAP (PHGG / Beta-Glucans / Psyllium) budget.
    """
    log: List[Dict] = []
    current_total = sum(p.get("dose_g", 0.0) for p in prebiotics)
    headroom_available = max(0.0, effective_ceiling - current_total)

    if headroom_available >= deficit_g:
        return deficit_g, log

    still_needed = deficit_g - headroom_available
    reducible = [
        p for p in prebiotics
        if not p.get("fodmap", False)
        and _canonical(p.get("substance", "")) not in protected
        and p.get("dose_g", 0.0) > 0
    ]
    reducible_total = sum(p["dose_g"] for p in reducible)

    if reducible_total <= 0:
        return headroom_available, log

    reduce_fraction = min(1.0, still_needed / reducible_total)
    for p in reducible:
        original = p["dose_g"]
        reduction = round(original * reduce_fraction, 3)
        p["dose_g"] = max(0.0, round(original - reduction, 3))
        if p["dose_g"] < 0.05:
            p["dose_g"] = 0.0
        if reduction > 0.01:
            log.append({
                "substance": p["substance"],
                "reduced_by_g": round(reduction, 3),
                "from_g": original,
                "to_g": p["dose_g"],
                "reason": "proportional reduction for required substrate headroom",
            })

    freed = headroom_available + sum(e["reduced_by_g"] for e in log)
    return freed, log


# ---------------------------------------------------------------------------
# Mix 4 SIBO override detection
# ---------------------------------------------------------------------------

def _detect_sibo_override(mix_id, design: Dict, mix_rules: Dict, context_flags: Dict) -> bool:
    """
    Return True when Mix 4's SIBO override is active. Fires when EITHER:
      (a) LLM went zero-FODMAP: mix_id == 4 AND total_fodmap_grams < 0.15
          AND KB defines zero_fodmap_permitted for this mix
      (b) Client self-reported SIBO in questionnaire: context_flags["sibo_self_reported"] == True
          AND mix_id == 4

    Both paths require mix_id == 4 — SIBO override is only valid for this mix.
    """
    if str(mix_id) != "4":
        return False

    # Path (b): questionnaire self-report — deterministic, takes priority
    if context_flags.get("sibo_self_reported"):
        return True

    # Path (a): LLM chose zero-FODMAP and KB permits it
    if float(design.get("total_fodmap_grams", 0.0)) >= 0.15:
        return False
    return bool(mix_rules.get("sibo_override", {}).get("zero_fodmap_permitted", False))


# ---------------------------------------------------------------------------
# Main substrate necessity guard
# ---------------------------------------------------------------------------

def enforce_substrate_necessity(
    prebiotic_design: Dict,
    mix_id,
    prebiotic_kb: Dict,
    prebiotic_range: Dict,
    context_flags: Optional[Dict] = None,
) -> Dict:
    """
    Deterministic post-LLM/offline guard using the substrate_necessity model.

    Priority tiers:
      1 — mechanistically essential; always enforced
      2 — enforced unless can_be_omitted_if evaluates True given current blend
      3 — diversity only; skipped when sensitivity_override_active is True

    sensitivity_override_active is determined by:
      - is_high_sensitivity (from questionnaire, deterministic)
      - is_gassy (from questionnaire, deterministic)
      - sensitivity_override_active (explicit boolean from LLM output)
    Note: overrides_applied (LLM free-text list) is NOT used — it is a log
    field only, not a guard signal.

    After enforcing floors, proportionally rebalances non-priority non-FODMAP
    components to respect effective_ceiling = max_g * (1 + tolerance_pct/100).
    If floors can't fit even after full rebalancing, a warning is logged and
    floors are honoured anyway (clinical correctness takes precedence).
    """
    if context_flags is None:
        context_flags = {}

    mix_key = f"mix_{mix_id}"
    mix_rules = prebiotic_kb.get("per_mix_prebiotics", {}).get(mix_key, {})
    substrate_necessity = mix_rules.get("substrate_necessity", {})
    max_g = float(prebiotic_range.get("max_g", 99))

    # Apply tolerance — effective ceiling allows up to 10% over max_g silently
    tolerance_pct = float(prebiotic_kb.get("prebiotic_tolerance_pct", 0))
    effective_ceiling = round(max_g * (1 + tolerance_pct / 100.0), 2)

    prebiotics: List[Dict] = prebiotic_design.setdefault("prebiotics", [])

    # Mix 4 SIBO override — zero FODMAP is intentional, skip guard entirely
    sibo_active = _detect_sibo_override(mix_id, prebiotic_design, mix_rules, context_flags)
    context_flags["sibo_override_active"] = sibo_active
    if sibo_active:
        prebiotic_design["substrate_guard"] = {
            "applied": False,
            "reason": "Mix 4 SIBO override active — zero FODMAP intentional",
            "sibo_source": "questionnaire" if context_flags.get("sibo_self_reported") else "llm_output",
            "ceiling_respected": True,
        }
        return prebiotic_design

    # P3 sensitivity_override_active: driven by deterministic questionnaire flags
    # AND the explicit LLM boolean. overrides_applied (free-text list) is NOT used.
    sensitivity_override_active = bool(
        context_flags.get("is_high_sensitivity")
        or context_flags.get("is_gassy")
        or prebiotic_design.get("sensitivity_override_active", False)
    )
    context_flags["sensitivity_override_active"] = sensitivity_override_active

    if not substrate_necessity:
        prebiotic_design["substrate_guard"] = {
            "applied": False,
            "reason": f"No substrate_necessity defined for {mix_key}",
            "ceiling_respected": True,
        }
        return prebiotic_design

    corrections: List[Dict] = []
    rebalance_log: List[Dict] = []
    warnings: List[str] = []
    protected: set = set()

    # Process priority-1 first, then 2, then 3
    ordered = sorted(substrate_necessity.items(), key=lambda kv: kv[1].get("priority", 3))

    for substance, spec in ordered:
        priority = spec.get("priority", 3)
        min_dose = float(spec.get("minimum_g", 0.0))
        condition = spec.get("can_be_omitted_if", None)

        if min_dose <= 0:
            continue

        # Priority-3: freely omit under any sensitivity override
        if priority == 3 and sensitivity_override_active:
            continue

        # Evaluate omission condition against current blend state
        if _eval_omit_condition(condition, prebiotics, context_flags):
            continue  # Another substrate already satisfies this slot

        current = _dose_of(substance, prebiotics)
        if current >= min_dose:
            protected.add(_canonical(substance))
            continue  # Already satisfied

        deficit = round(min_dose - current, 3)

        # Make headroom before injecting (uses effective_ceiling with tolerance)
        freed, rb_log = _make_headroom(prebiotics, deficit, max_g, effective_ceiling, protected)
        rebalance_log.extend(rb_log)

        if freed < deficit - 0.01:
            warnings.append(
                f"{substance}: needed {deficit}g headroom but only {freed:.2f}g available "
                f"within effective_ceiling={effective_ceiling}g (max_g={max_g}g + {tolerance_pct}% tolerance) "
                f"— clinical floors take precedence."
            )

        action = _set_dose(substance, min_dose, prebiotics, spec.get("mechanism", "")[:80])
        protected.add(_canonical(substance))
        corrections.append({
            "action": action,
            "substance": substance,
            "priority": priority,
            "from_g": current,
            "to_g": min_dose,
        })

    # Recalculate totals
    new_total = round(sum(p.get("dose_g", 0.0) for p in prebiotics), 2)
    new_fodmap = round(sum(p.get("dose_g", 0.0) for p in prebiotics if p.get("fodmap", False)), 2)
    prebiotic_design["total_grams"] = new_total
    prebiotic_design["total_fodmap_grams"] = new_fodmap

    prebiotic_design["substrate_guard"] = {
        "applied": bool(corrections or rebalance_log),
        "substrate_necessity_guard_applied": bool(corrections),
        "mix_key": mix_key,
        "corrections": corrections,
        "rebalance_log": rebalance_log,
        "warnings": warnings,
        "final_total_g": new_total,
        "max_g_ceiling": max_g,
        "effective_ceiling_g": effective_ceiling,
        "tolerance_pct": tolerance_pct,
        "ceiling_respected": new_total <= effective_ceiling,
    }
    return prebiotic_design


# ---------------------------------------------------------------------------
# Phased dosing helper
# ---------------------------------------------------------------------------

def _build_phased_dosing(total_g: float) -> Dict:
    half = round(total_g * 0.5, 1)
    template = "Weeks 1–2: {half_dose_g}g daily. Week 3+: {full_dose_g}g daily."
    rationale = ""
    try:
        dfr = _load_kb("delivery_format_rules.json")
        policy = dfr.get("phased_dosing_policy", {})
        template = policy.get("instruction_template", template)
        rationale = policy.get("rationale", "")
    except Exception:
        pass
    return {
        "weeks_1_2_g": half,
        "weeks_3_plus_g": total_g,
        "instruction": template.replace("{half_dose_g}", str(half)).replace("{full_dose_g}", str(total_g)),
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

PREBIOTIC_DESIGN_SYSTEM = """You are a prebiotic formulation specialist.

You will receive:
1. Selected synbiotic mix with a substrate_necessity model — minimum doses, priority tiers,
   can_be_omitted_if conditions, and mechanism explanations for each required substrate
2. Client sensitivity classification and allowed gram range
3. Client digestive symptoms
4. Contradiction override rules for sensitive clients

Your task: Design the optimal prebiotic blend for the POWDER JAR.

SUBSTRATE NECESSITY MODEL — read and apply carefully:
- priority 1: mechanistically essential — include at minimum_g regardless of sensitivity
- priority 2: include unless can_be_omitted_if condition is met by what you have already included
- priority 3: diversity only — omit freely when any sensitivity override is active
- can_be_omitted_if: evaluate against your own blend (e.g. if condition says "Inulin >= 0.5"
  and you have included Inulin at 0.5g or more, that substrate's floor is waived)

OPTIMISATION LOGIC — do not blindly include every substrate:
1. Lock in all priority-1 substrates at their minimum doses first
2. For each priority-2 substrate, check whether its can_be_omitted_if is already satisfied
   by the substrates you have locked in — if yes, skip it
3. For priority-3, skip if any sensitivity override is active
4. Fill remaining gram budget with non-FODMAP base fibers (PHGG, Beta-Glucans) to reach min_g

SENSITIVITY OVERRIDE FIELD — required in your response:
Set "sensitivity_override_active": true if you are applying any sensitivity-driven
contradiction override (FODMAP reduction, PHGG substitution, bloating protocol) that
warrants skipping priority-3 diversity substrates. Set false if no sensitivity override
is being applied. This is an explicit clinical judgement, not derived from overrides_applied.

ADDITIONAL RULES:
- Total prebiotic grams MUST be within the provided dose range
- PHGG is the safest non-FODMAP base fiber for sensitive clients
- Rationale for each entry must reference the specific mechanism, not just "required"
- Total FODMAP should stay ≤ sensitivity threshold

PHASED DOSING: total_grams is the FULL week-3+ dose. Phasing is auto-computed downstream.
DELIVERY: Prebiotic blend goes into a POWDER JAR (soft daily target ≤19g).

RESPOND WITH ONLY A JSON OBJECT:
{
  "strategy": "<description including which substrates were omitted and why>",
  "total_grams": <number>,
  "total_fodmap_grams": <number>,
  "contradictions_found": [<list>],
  "overrides_applied": [<list>],
  "sensitivity_override_active": <true|false>,
  "prebiotics": [
    {"substance": "<n>", "dose_g": <number>, "fodmap": <bool>, "rationale": "<mechanism-referenced why>"}
  ],
  "condition_specific_additions": [
    {"substance": "<n>", "dose_g_or_mg": "<dose>", "condition": "<which>", "rationale": "<why>"}
  ]
}"""


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

def design_prebiotics(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """LLM prebiotic blend design for powder jar.

    Sends the full substrate_necessity model in the prompt so the LLM performs
    intelligent substrate optimisation. The deterministic guard runs after to
    catch any omissions or floor violations.

    SIBO self-report is detected from the questionnaire and passed as a
    context flag — this allows the guard to honour zero-FODMAP for Mix 4
    regardless of what the LLM returned.
    """
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input["questionnaire"]["digestive"]
    goals = unified_input["questionnaire"]["goals"]
    bloating = int(digestive.get("bloating_severity") or 0)

    prebiotic_kb = _load_kb("prebiotic_rules.json")
    mix_key = f"mix_{mix_selection['mix_id']}"
    mix_prebiotics = prebiotic_kb["per_mix_prebiotics"].get(mix_key, {})

    user_prompt = f"""## Selected Synbiotic Mix
Mix ID: {mix_selection["mix_id"]}
Mix Name: {mix_selection["mix_name"]}

## Substrate Necessity Model (PRIMARY GUIDE)
{json.dumps(mix_prebiotics.get("substrate_necessity", {}), indent=2)}

## Contradiction Overrides
{json.dumps(mix_prebiotics.get("contradiction_overrides", {}), indent=2)}

## SIBO Override (Mix 4 only)
{json.dumps(mix_prebiotics.get("sibo_override", {}), indent=2)}

## Sensitivity Classification
{json.dumps(sensitivity, indent=2)}

## Allowed Prebiotic Dose Range
Min: {prebiotic_range["min_g"]}g, Max: {prebiotic_range["max_g"]}g
CFU tier: {prebiotic_range["cfu_tier"]}

## Client Digestive Profile
Bloating severity: {bloating}/10
Bloating frequency: {digestive.get("bloating_frequency")}
Stool type (Bristol): {digestive.get("stool_type")}
Digestive satisfaction: {digestive.get("digestive_satisfaction")}/10

## Client Goals
{json.dumps(goals.get("ranked", []))}

## Condition-Specific Addition Options
{json.dumps(prebiotic_kb["condition_specific_additions"], indent=2)}

## Polyphenol Antimicrobial Thresholds
{json.dumps(prebiotic_kb["polyphenol_antimicrobial_thresholds"], indent=2)}

Design the prebiotic blend. Total must be within {prebiotic_range["min_g"]}–{prebiotic_range["max_g"]}g.
Return ONLY the JSON response."""

    response_text = call_bedrock(PREBIOTIC_DESIGN_SYSTEM, user_prompt)
    design = extract_json_from_response(response_text)

    # Detect SIBO from questionnaire (deterministic — takes priority over LLM output detection)
    sibo_self_reported = _sibo_self_reported(unified_input)

    context_flags = {
        "is_gassy": bloating >= 7,
        "is_high_sensitivity": sensitivity.get("classification") == "high",
        "sibo_self_reported": sibo_self_reported,
    }
    design = enforce_substrate_necessity(
        design, mix_selection["mix_id"], prebiotic_kb, prebiotic_range, context_flags
    )
    design["phased_dosing"] = _build_phased_dosing(design["total_grams"])
    return design


# ---------------------------------------------------------------------------
# Offline path
# ---------------------------------------------------------------------------

def design_prebiotics_offline(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """Mix-aware offline prebiotic design using synbiotic_mixes.json default formulas.

    Offline fallback when LLM is unavailable.

    Logic:
    1. Select formula: gassy_client > fodmap_sensitive > default
    2. Scale proportionally into allowed gram range
    3. Run enforce_substrate_necessity to inject/raise any missing floors
    4. Rebalancer trims non-priority components to stay within effective_ceiling
    5. Build phased dosing on final totals

    sensitivity_override_active is set deterministically (is_high_sensitivity or is_gassy)
    since there is no LLM reasoning in the offline path.
    """
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input.get("questionnaire", {}).get("digestive", {})
    bloating = int(digestive.get("bloating_severity") or 0)

    mix_id = mix_selection.get("mix_id")
    if mix_id is None:
        mix_id = 6  # Default to maintenance

    try:
        mix_data = _load_kb("synbiotic_mixes.json")["mixes"].get(str(mix_id), {})
    except Exception:
        mix_data = {}

    is_high_sensitivity = sensitivity.get("classification") == "high"
    is_gassy = bloating >= 7

    # Select formula
    if is_gassy and "gassy_client_formula" in mix_data:
        formula = mix_data["gassy_client_formula"]
        strategy = f"Mix {mix_id} gassy client formula (bloating {bloating}/10)"
        overrides = [f"gassy_client_formula selected — bloating {bloating}/10"]
    elif is_high_sensitivity and "fodmap_sensitive_formula" in mix_data:
        formula = mix_data["fodmap_sensitive_formula"]
        strategy = f"Mix {mix_id} FODMAP-sensitive formula (high sensitivity)"
        overrides = ["fodmap_sensitive_formula selected — high sensitivity"]
    else:
        formula = mix_data.get("default_prebiotic_formula", {})
        strategy = f"Mix {mix_id} ({mix_data.get('mix_name', '?')}) default formula"
        overrides = []

    components = formula.get("components", [])
    formula_total = sum(c.get("dose_g", 0) for c in components)
    max_g = float(prebiotic_range.get("max_g", 8))
    min_g = float(prebiotic_range.get("min_g", 4))

    prebiotics: List[Dict] = []
    if formula_total > 0 and components:
        if formula_total > max_g:
            scale = max_g / formula_total
            overrides.append(f"Scaled down {formula_total}g → {max_g}g (sensitivity clamp)")
        elif formula_total < min_g:
            scale = min_g / formula_total
            overrides.append(f"Scaled up {formula_total}g → {min_g}g")
        else:
            scale = 1.0

        for c in components:
            dose = round(c.get("dose_g", 0) * scale, 2)
            if dose > 0:
                prebiotics.append({
                    "substance": c.get("substance", "Unknown"),
                    "dose_g": dose,
                    "fodmap": c.get("fodmap", False),
                    "rationale": c.get("rationale", formula.get("rationale", "")),
                })
    else:
        target = (min_g + max_g) / 2
        prebiotics = [
            {"substance": "PHGG",         "dose_g": round(target * 0.5, 2), "fodmap": False, "rationale": "Safe base fiber (no mix formula found)"},
            {"substance": "Beta-Glucans", "dose_g": round(target * 0.3, 2), "fodmap": False, "rationale": "Butyrate substrate"},
            {"substance": "GOS",          "dose_g": round(target * 0.2, 2), "fodmap": True,  "rationale": "Bifidogenic"},
        ]
        strategy = "Generic PHGG-moderate fallback (no mix formula available)"

    contradictions: List[str] = []
    if is_gassy:
        contradictions.append(f"bloating {bloating}/10")
    if is_high_sensitivity:
        contradictions.append("high sensitivity classification")

    # sensitivity_override_active: deterministic in offline path (no LLM reasoning)
    offline_sensitivity_override = is_high_sensitivity or is_gassy

    design = {
        "strategy": strategy,
        "total_grams": round(sum(p["dose_g"] for p in prebiotics), 2),
        "total_fodmap_grams": round(sum(p["dose_g"] for p in prebiotics if p["fodmap"]), 2),
        "contradictions_found": contradictions,
        "overrides_applied": overrides,
        "sensitivity_override_active": offline_sensitivity_override,
        "prebiotics": prebiotics,
        "condition_specific_additions": [],
    }

    prebiotic_kb = _load_kb("prebiotic_rules.json")

    # Detect SIBO from questionnaire
    sibo_self_reported = _sibo_self_reported(unified_input)

    context_flags = {
        "is_gassy": is_gassy,
        "is_high_sensitivity": is_high_sensitivity,
        "bloating_override_active": is_gassy or is_high_sensitivity,
        "sibo_self_reported": sibo_self_reported,
    }
    design = enforce_substrate_necessity(
        design, mix_id, prebiotic_kb, prebiotic_range, context_flags
    )
    design["phased_dosing"] = _build_phased_dosing(design["total_grams"])
    return design
