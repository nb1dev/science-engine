#!/usr/bin/env python3
"""
LLM Prebiotic Design — Design prebiotic blend for powder jar.

Input:  Selected mix, sensitivity, digestive symptoms, prebiotic rules KB.
Output: Structured JSON with strategy, prebiotics, condition_specific_additions.
"""

import json
from pathlib import Path
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


def _load_kb(filename: str) -> Dict:
    with open(KB_DIR / filename, 'r', encoding='utf-8') as f:
        return json.load(f)


PREBIOTIC_DESIGN_SYSTEM = """You are a prebiotic formulation specialist.

You will receive:
1. Selected synbiotic mix (with required prebiotics per mix)
2. Client sensitivity classification and prebiotic dose range
3. Client digestive symptoms (bloating, stool type, food triggers)
4. Prebiotic rules (per-mix requirements, contradiction overrides)

Your task: Design the prebiotic blend for the POWDER JAR.

CRITICAL RULES:
- Total prebiotic grams MUST be within the provided dose range
- If contradictions exist (bloating ≥7, IBS-D, FODMAP sensitivity), apply override rules
- PHGG is the safest base fiber for sensitive clients
- List each prebiotic with exact gram dose
- Total FODMAP should stay ≤ sensitivity threshold

PHASED DOSING: total_grams is the FULL week-3+ dose. The pipeline auto-computes
the week-1-2 half-dose. Acknowledge phased dosing in rationale.

DELIVERY: Prebiotic blend goes into a POWDER JAR (soft daily target ≤19g).
Leave headroom below 19g for potential botanical additions.

RESPOND WITH ONLY A JSON OBJECT:
{
  "strategy": "<description>",
  "total_grams": <number>,
  "total_fodmap_grams": <number>,
  "contradictions_found": [<list>],
  "overrides_applied": [<list>],
  "prebiotics": [
    {"substance": "<name>", "dose_g": <number>, "fodmap": <true|false>, "rationale": "<why>"}
  ],
  "condition_specific_additions": [
    {"substance": "<name>", "dose_g_or_mg": "<dose>", "condition": "<which>", "rationale": "<why>"}
  ]
}"""


def design_prebiotics(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """LLM prebiotic blend design for powder jar.

    Args:
        unified_input: Parsed pipeline input.
        rule_outputs: Deterministic rule outputs (sensitivity, prebiotic_range).
        mix_selection: Selected probiotic mix dict.

    Returns:
        Prebiotic design dict with strategy, prebiotics, condition_specific_additions.
    """
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input["questionnaire"]["digestive"]
    goals = unified_input["questionnaire"]["goals"]

    prebiotic_kb = _load_kb("prebiotic_rules.json")
    mix_key = f"mix_{mix_selection['mix_id']}"
    mix_prebiotics = prebiotic_kb["per_mix_prebiotics"].get(mix_key, {})

    user_prompt = f"""## Selected Synbiotic Mix
Mix ID: {mix_selection["mix_id"]}
Mix Name: {mix_selection["mix_name"]}

## Per-Mix Prebiotic Requirements
{json.dumps(mix_prebiotics, indent=2)}

## Sensitivity Classification
{json.dumps(sensitivity, indent=2)}

## Allowed Prebiotic Dose Range
Min: {prebiotic_range["min_g"]}g, Max: {prebiotic_range["max_g"]}g
CFU tier: {prebiotic_range["cfu_tier"]}

## Client Digestive Profile
Bloating severity: {digestive.get("bloating_severity")}/10
Bloating frequency: {digestive.get("bloating_frequency")}
Stool type (Bristol): {digestive.get("stool_type")}
Digestive satisfaction: {digestive.get("digestive_satisfaction")}/10

## Client Goals
{json.dumps(goals.get("ranked", []))}

## Condition-Specific Addition Options
{json.dumps(prebiotic_kb["condition_specific_additions"], indent=2)}

## Polyphenol Antimicrobial Thresholds
{json.dumps(prebiotic_kb["polyphenol_antimicrobial_thresholds"], indent=2)}

Design the prebiotic blend for the POWDER JAR.
Total must be within {prebiotic_range["min_g"]}-{prebiotic_range["max_g"]}g.
Return ONLY the JSON response."""

    response_text = call_bedrock(PREBIOTIC_DESIGN_SYSTEM, user_prompt)
    return extract_json_from_response(response_text)


def design_prebiotics_offline(unified_input: Dict, rule_outputs: Dict, mix_selection: Dict) -> Dict:
    """Mix-aware prebiotic design using synbiotic_mixes.json default formulas.

    Offline fallback when LLM is unavailable.

    Logic:
    1. Look up the selected mix's default prebiotic formula
    2. Check for high-sensitivity contradictions → apply overrides
    3. Scale to fit within allowed gram range
    4. Return structured prebiotic blend
    """
    sensitivity = rule_outputs["sensitivity"]
    prebiotic_range = rule_outputs["prebiotic_range"]
    digestive = unified_input.get("questionnaire", {}).get("digestive", {})
    bloating = digestive.get("bloating_severity", 0) or 0

    mix_id = mix_selection.get("mix_id")
    if mix_id is None:
        mix_id = 6  # Default to maintenance if MIXED flag

    # Load mix's default prebiotic formula from synbiotic_mixes.json
    try:
        mix_data = _load_kb("synbiotic_mixes.json")["mixes"].get(str(mix_id), {})
    except Exception:
        mix_data = {}

    # Determine which formula to use
    is_high_sensitivity = sensitivity.get("classification") == "high"
    is_gassy = bloating >= 7

    if is_gassy and "gassy_client_formula" in mix_data:
        formula = mix_data["gassy_client_formula"]
        strategy = f"Mix {mix_id} gassy client formula (bloating {bloating}/10)"
        overrides = [f"Using gassy_client_formula due to bloating {bloating}/10"]
    elif is_high_sensitivity and "fodmap_sensitive_formula" in mix_data:
        formula = mix_data["fodmap_sensitive_formula"]
        strategy = f"Mix {mix_id} FODMAP-sensitive formula (high sensitivity)"
        overrides = ["Using FODMAP-sensitive formula due to high sensitivity"]
    else:
        formula = mix_data.get("default_prebiotic_formula", {})
        strategy = f"Mix {mix_id} ({mix_data.get('mix_name', '?')}) default formula"
        overrides = []

    # Extract components
    components = formula.get("components", [])

    # Scale to fit within allowed range
    formula_total = sum(c.get("dose_g", 0) for c in components)
    max_g = prebiotic_range.get("max_g", 8)
    min_g = prebiotic_range.get("min_g", 4)

    prebiotics = []
    if formula_total > 0 and components:
        if formula_total > max_g:
            scale = max_g / formula_total
            overrides.append(f"Scaled down from {formula_total}g to {max_g}g (sensitivity clamp)")
        elif formula_total < min_g:
            scale = min_g / formula_total
            overrides.append(f"Scaled up from {formula_total}g to {min_g}g")
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
        # Fallback — generic PHGG-moderate if no formula found
        target = (min_g + max_g) / 2
        prebiotics = [
            {"substance": "PHGG", "dose_g": round(target * 0.5, 2), "fodmap": False, "rationale": "Safe base fiber (no mix formula found)"},
            {"substance": "Beta-Glucans", "dose_g": round(target * 0.3, 2), "fodmap": False, "rationale": "Butyrate substrate"},
            {"substance": "GOS", "dose_g": round(target * 0.2, 2), "fodmap": True, "rationale": "Bifidogenic"},
        ]
        strategy = "Generic PHGG-moderate fallback (no mix formula available)"

    total_g = round(sum(p["dose_g"] for p in prebiotics), 2)
    total_fodmap = round(sum(p["dose_g"] for p in prebiotics if p["fodmap"]), 2)

    contradictions = []
    if is_gassy:
        contradictions.append(f"bloating {bloating}/10")
    if is_high_sensitivity:
        contradictions.append("high sensitivity classification")

    # Phased dosing
    half_dose = round(total_g * 0.5, 1)
    try:
        dfr_path = KB_DIR / "delivery_format_rules.json"
        with open(dfr_path, 'r', encoding='utf-8') as f:
            dfr = json.load(f)
        policy = dfr.get("phased_dosing_policy", {})
        template = policy.get("instruction_template", "Weeks 1–2: {half_dose_g}g daily. Week 3+: {full_dose_g}g daily.")
        instruction = template.replace("{half_dose_g}", str(half_dose)).replace("{full_dose_g}", str(total_g))
        rationale = policy.get("rationale", "")
    except Exception:
        instruction = f"Weeks 1–2: {half_dose}g daily. Week 3+: {total_g}g daily."
        rationale = ""

    return {
        "strategy": strategy,
        "total_grams": total_g,
        "total_fodmap_grams": total_fodmap,
        "contradictions_found": contradictions,
        "overrides_applied": overrides,
        "prebiotics": prebiotics,
        "condition_specific_additions": [],
        "phased_dosing": {
            "weeks_1_2_g": half_dose,
            "weeks_3_plus_g": total_g,
            "instruction": instruction,
            "rationale": rationale,
        },
    }
