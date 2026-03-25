#!/usr/bin/env python3
"""
LLM Supplement Selection — Vitamins, minerals, and non-vitamin supplements.

Input:  Health claims, therapeutic triggers, questionnaire demographics, KB databases.
Output: Structured JSON with vitamins_minerals, supplements, omega3, existing_supplements_advice.
"""

import json
from pathlib import Path
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


def _load_kb(filename: str) -> Dict:
    with open(KB_DIR / filename, 'r', encoding='utf-8') as f:
        return json.load(f)


SUPPLEMENT_SELECTION_SYSTEM = """You are a clinical nutritionist selecting vitamins, minerals, and supplements.

You will receive:
1. Health claim categories (from client goals + microbiome signals)
2. Reported vitamin deficiencies and therapeutic dose triggers
3. Vitamin/mineral reference database (with authoritative doses)
4. Non-vitamin supplement database (with ranked choices)
5. Client demographics and current supplements

Your task: Select appropriate vitamins, minerals, and non-vitamin supplements.

CRITICAL RULES:
- Use EXACT doses from the reference databases (max_intake_in_supplements for vitamins)
- Use therapeutic doses when deficiency is reported (from therapeutic_triggers)
- Check interaction_risk for every selected item
- Respect rank order (prefer 1st choice supplements)
- Do NOT select supplements the client is already taking

⚠️ DO NOT SELECT THE FOLLOWING — they are handled by other pipeline steps:
- Magnesium (handled by separate Mg bisglycinate evening capsule)
- Vitamin D (already in fixed softgel: 10mcg × 2 = 20mcg daily)
- Vitamin E (already in fixed softgel: 7.5mg × 2 = 15mg daily)
- Omega-3 / DHA / EPA (already in fixed softgel: 712.5mg × 2 = 1425mg daily)
- Astaxanthin (already in fixed softgel)
- Melatonin (handled by deterministic sleep supplement selection)
- L-Theanine (handled by deterministic sleep supplement selection)
- Valerian / Valerian Root (handled by deterministic sleep supplement selection)
- PHGG, Psyllium, Inulin, FOS, GOS, Beta-glucans, Resistant Starch, Glucomannan, or ANY prebiotic fiber
  (handled by the separate prebiotic design step)
If any of these appear in your selection, they will be REMOVED by the pipeline.

DELIVERY FIELD VALUES (use exactly one per ingredient):
  "morning_wellness_capsule" — vitamins, minerals, light botanicals (dose ≤ 650mg)
  "jar" — heavy non-bitter botanicals (dose > 650mg)
  "evening_capsule" — sleep aids, calming adaptogens, capsule-only substances
  "polyphenol_capsule" — Curcumin+Piperine, Bergamot only
  "softgel" — fat-soluble vitamins only (already handled)

RESPOND WITH ONLY A JSON OBJECT:
{
  "vitamins_minerals": [
    {"substance": "<name>", "dose": "<exact dose>", "dose_value": <number>, "dose_unit": "<mg|mcg>",
     "therapeutic": <true|false>, "standard_dose": "<if therapeutic>", "delivery": "<delivery>",
     "informed_by": "<microbiome|questionnaire|both>", "rationale": "<why>", "interaction_note": "<any>"}
  ],
  "supplements": [
    {"substance": "<name>", "dose_mg": <number>, "health_claim": "<category>", "rank": "<1st|2nd|3rd>",
     "delivery": "<delivery>", "informed_by": "<questionnaire>", "rationale": "<why>"}
  ],
  "omega3": {"dose_daily_mg": 1425, "dose_per_softgel_mg": 712.5, "rationale": "<why>"},
  "existing_supplements_advice": [{"name": "<supp>", "action": "continue|stop|adjust", "note": "<guidance>"}]
}"""


def select_supplements(unified_input: Dict, rule_outputs: Dict,
                       medication_exclusions=None) -> Dict:
    """LLM supplement selection — vitamins, minerals, non-vitamin supplements.

    Args:
        unified_input: Parsed pipeline input (from parse_inputs).
        rule_outputs: Deterministic rule outputs (health_claims, therapeutic_triggers, etc.).
        medication_exclusions: MedicationExclusions object with excluded_substances,
            evidence_excluded_substances, and exclusion_reasons from Stage 3.
            Used to build a dynamic exclusion block in the LLM prompt so the LLM
            avoids selecting interacting substances in the first place.

    Returns:
        Dict with vitamins_minerals, supplements, omega3, existing_supplements_advice.
    """
    health_claims = rule_outputs["health_claims"]
    therapeutic = rule_outputs["therapeutic_triggers"]
    questionnaire = unified_input["questionnaire"]

    vitamins_kb = _load_kb("vitamins_minerals.json")
    supplements_kb = _load_kb("supplements_nonvitamins.json")

    # Calculate evening capsule headroom
    sleep_supps = rule_outputs.get("sleep_supplements", {}).get("supplements", [])
    evening_timing = rule_outputs.get("timing", {}).get("timing_assignments", {})
    reserved_evening_mg = 0
    reserved_evening_items = []
    for ss in sleep_supps:
        substance_key = ss["substance"].lower().replace("-", "_").replace(" ", "_")
        timing_info = evening_timing.get(substance_key, {})
        if timing_info.get("timing") == "evening":
            reserved_evening_mg += ss.get("dose_mg", 0)
            reserved_evening_items.append(f"{ss['substance']} {ss['dose_mg']}mg")
    available_evening_mg = 650 - reserved_evening_mg
    evening_headroom_note = f"Evening Wellness Capsule: {available_evening_mg}mg available per capsule"
    if reserved_evening_items:
        evening_headroom_note += f" (650mg minus {reserved_evening_mg}mg reserved for: {', '.join(reserved_evening_items)})."
    else:
        evening_headroom_note += " (650mg per capsule, no sleep supplements reserved)."

    # Build dynamic medication exclusion block for LLM prompt
    med_exclusion_block = ""
    if medication_exclusions:
        all_excluded = set()
        exclusion_details = []

        # Merge both LLM screener exclusions (exclusion_reasons) AND deterministic KB rule
        # removals (removal_reasons). These are separate fields on MedicationExclusions:
        #   - exclusion_reasons: populated by the LLM medication screener (Stage A.5b)
        #   - removal_reasons:   populated by deterministic KB rules (Stage A.6, e.g. MED_003)
        # Without merging both, KB-only removals (e.g. curcumin via MED_003) are silently
        # absent from the LLM exclusion prompt and the LLM may re-select the excluded substance.
        all_reasons = (
            getattr(medication_exclusions, 'exclusion_reasons', []) +
            getattr(medication_exclusions, 'removal_reasons', [])
        )
        for reason in all_reasons:
            substance = reason.get('substance', '')
            medication = reason.get('medication', '')
            mechanism = reason.get('mechanism', '')
            if substance:
                substance_lower = substance.lower()
                if substance_lower not in all_excluded:
                    all_excluded.add(substance_lower)
                    exclusion_details.append(f"- {substance} (interacts with {medication} — {mechanism})")

        # Evidence-derived exclusions (from evidence retrieval in S3)
        for substance in getattr(medication_exclusions, 'evidence_excluded_substances', set()):
            if substance.lower() not in all_excluded:
                all_excluded.add(substance.lower())
                exclusion_details.append(f"- {substance.title()} (evidence-based medication interaction)")

        if exclusion_details:
            med_exclusion_block = (
                "\n## ⚠️ MEDICATION EXCLUSIONS (client-specific — DO NOT SELECT)\n"
                "The following substances are excluded due to medication interactions.\n"
                "Select functionally similar safe alternatives where possible.\n"
                + "\n".join(exclusion_details) + "\n"
            )

    user_prompt = f"""## Health Claim Categories to Address
Supplement claims: {json.dumps(health_claims["supplement_claims"])}
Vitamin claims: {json.dumps(health_claims["vitamin_claims"])}
Microbiome vitamin needs: {json.dumps(health_claims["microbiome_vitamin_needs"])}

## Therapeutic Dose Triggers
{json.dumps(therapeutic, indent=2)}

## Client Demographics
Age: {questionnaire["demographics"].get("age")}
Sex: {questionnaire["demographics"].get("biological_sex")}
Goals (ranked): {json.dumps(questionnaire["goals"]["ranked"])}

## Current Supplements (DO NOT DUPLICATE)
{json.dumps(questionnaire.get("current_supplements", []))}

## Current Medications (check interactions)
{json.dumps(questionnaire["medical"].get("medications", []))}
{med_exclusion_block}
## EVENING CAPSULE HEADROOM
{evening_headroom_note}

## MANDATORY ITEMS (deterministic — MUST include with KB doses)
{json.dumps(rule_outputs.get("goal_triggered_supplements", {}).get("mandatory_vitamins", []), indent=2)}
{json.dumps(rule_outputs.get("goal_triggered_supplements", {}).get("mandatory_supplements", []), indent=2)}

## Vitamin & Mineral Reference Database
{json.dumps(vitamins_kb["vitamins_and_minerals"], indent=2)}

## Non-Vitamin Supplement Database (by health category)
{json.dumps(supplements_kb["health_categories"], indent=2)}

## Polyphenol Daily Cap (STRICT — 1,500mg/day)
Total daily dose across ALL substances where supplement_type == "Fermentable Polyphenol Substrate"
must not exceed 1,500mg/day (1% tolerance = 1,515mg). Substances in this class:
  Curcumin, Bergamot Polyphenolic Fraction (dose range 500–1000mg), Quercetin,
  Fermented Pomegranate Polyphenols, Apple Polyphenol Extract, Capsicum Extract.
If multiple are clinically indicated, prioritise by rank (1st Choice > 2nd Choice > 3rd Choice).
Reduce lower-ranked doses to their KB minimum before dropping them entirely.
Note: Piperine is auto-added by the pipeline at 1:100 with Curcumin — do NOT include piperine
in dose_mg for Curcumin. The pipeline adds it automatically.

Select vitamins, minerals, and supplements. Use exact doses from databases.
Return ONLY the JSON response."""

    response_text = call_bedrock(SUPPLEMENT_SELECTION_SYSTEM, user_prompt, max_tokens=6000,
                                 temperature=0.05)
    return extract_json_from_response(response_text)
