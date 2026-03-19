#!/usr/bin/env python3
"""
LLM Formulation Sanity Check — Post-pipeline QA review.

Reviews manufacturing recipe for structural correctness (not clinical appropriateness).
"""

import json
from pathlib import Path
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response, HAS_BOTO3, OPUS_MODEL_ID

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


def _load_kb(filename: str) -> Dict:
    with open(KB_DIR / filename, 'r', encoding='utf-8') as f:
        return json.load(f)


def _build_kb_references() -> str:
    """Load KB files and build compact reference summaries for the sanity check prompt."""
    references = []

    try:
        delivery_kb = _load_kb("delivery_format_rules.json")
        lines = ["## DELIVERY FORMAT REFERENCE"]
        for fmt_key, fmt_data in delivery_kb.get("delivery_formats", {}).items():
            label = fmt_data.get("label", fmt_key)
            capacity_mg = fmt_data.get("capacity_mg") or fmt_data.get("capacity_mg_per_capsule")
            capacity_g = fmt_data.get("capacity_g") or fmt_data.get("daily_target_g")
            timing = fmt_data.get("timing", "?")
            fixed = fmt_data.get("fixed_composition", False)
            cap_str = f"{capacity_mg}mg" if capacity_mg else (f"{capacity_g}g" if capacity_g else "N/A")
            lines.append(f"- {label}: capacity={cap_str}, timing={timing}{' [FIXED]' if fixed else ''}")
        references.append("\n".join(lines))
    except Exception as e:
        references.append(f"## DELIVERY FORMAT REFERENCE: failed ({e})")

    try:
        supplements_kb = _load_kb("supplements_nonvitamins.json")
        lines = ["## SUPPLEMENT TIMING REFERENCE"]
        seen = set()
        for cat in supplements_kb.get("health_categories", []):
            for supp in cat.get("supplements", []):
                substance = supp.get("substance", "")
                if substance.lower() not in seen:
                    seen.add(substance.lower())
                    lines.append(f"- {substance}: timing={supp.get('timing_restriction', 'any')}")
        references.append("\n".join(lines))
    except Exception as e:
        references.append(f"## SUPPLEMENT TIMING REFERENCE: failed ({e})")

    try:
        vitamins_kb = _load_kb("vitamins_minerals.json")
        lines = ["## VITAMIN/MINERAL DOSE REFERENCE"]
        for vm in vitamins_kb.get("vitamins_and_minerals", []):
            lines.append(f"- {vm.get('substance', '')}: dose={vm.get('max_intake_in_supplements', '')}")
        references.append("\n".join(lines))
    except Exception as e:
        references.append(f"## VITAMIN/MINERAL DOSE REFERENCE: failed ({e})")

    return "\n\n".join(references)


SANITY_CHECK_SYSTEM = """You are a formulation QA engineer checking for INTERNAL CONSISTENCY only.
Do NOT comment on clinical appropriateness.

CHECK: capsule fill efficiency, dose inconsistency, capacity violations, timing contradictions,
zero-dose ingredients, dose vs KB mismatch.

Return: {"warnings": [{"severity": "error|warning|info", "unit": "...", "issue": "...", "suggestion": "..."}],
"overall_assessment": "one sentence"}"""


def formulation_sanity_check(recipe: Dict, health_claims: list = None,
                              client_goals: list = None, use_bedrock: bool = True) -> Dict:
    """LLM-powered sanity check of the manufacturing recipe.

    Returns:
        {warnings: list, overall_assessment: str}
    """
    if not use_bedrock or not HAS_BOTO3:
        return {"warnings": [], "overall_assessment": "Skipped (offline)", "skipped": True}

    kb_references = _build_kb_references()

    FIXED_LABELS = {"probiotic hard capsule", "probiotic capsule",
                    "omega + antioxidant softgel", "magnesium bisglycinate capsule"}

    units_summary = []
    skipped_fixed = []
    for unit in recipe.get("units", []):
        label = unit.get("label", "?")
        if label.lower() in FIXED_LABELS:
            skipped_fixed.append(f"{unit.get('quantity', 1)}× {label}")
            continue
        timing = unit.get("timing", "?")
        fill_mg = unit.get("fill_weight_per_capsule_mg") or unit.get("fill_weight_mg") or unit.get("total_weight_mg")
        fill_g = unit.get("fill_weight_g") or unit.get("total_weight_g")
        ingredients = unit.get("ingredients", unit.get("ingredients_per_unit", []))
        ing_lines = []
        for ing in ingredients:
            amt_mg = ing.get("amount_mg")
            amt_g = ing.get("amount_g")
            amt = f"{amt_mg}mg" if amt_mg and amt_mg > 0 else (f"{amt_g}g" if amt_g and amt_g > 0 else str(ing.get("amount", "?")))
            ing_lines.append(f"  - {ing.get('component', '?')}: {amt}")
        fill_str = f"{fill_mg}mg" if fill_mg else (f"{fill_g}g" if fill_g else "?")
        qty = unit.get("quantity", 1)
        total_fill = unit.get("total_fill_weight_mg")
        note = unit.get("ingredients_note", "")
        qty_note = f" × {qty} capsules" if qty > 1 else ""
        total_note = f" (total across all: {total_fill}mg)" if total_fill and qty > 1 else ""
        note_line = f"\n  NOTE: {note}" if note else ""
        units_summary.append(f"UNIT: {label} ({timing}, fill_per_capsule={fill_str}{qty_note}{total_note})\n" + "\n".join(ing_lines) + note_line)

    if not units_summary:
        return {"warnings": [], "overall_assessment": "All fixed-format — no LLM QA needed."}

    skipped_str = "\n".join(f"  - {s} (fixed)" for s in skipped_fixed) if skipped_fixed else ""

    prompt = f"""Review these recipe units against KB:

{chr(10).join(units_summary)}
{f'{chr(10)}Fixed units excluded: {chr(10)}{skipped_str}' if skipped_str else ''}

{kb_references}

Flag ONLY issues where recipe CONTRADICTS KB."""

    try:
        response_text = call_bedrock(SANITY_CHECK_SYSTEM, prompt, max_tokens=1500,
                                      temperature=0.1, model_id=OPUS_MODEL_ID)
        result = extract_json_from_response(response_text)
        if isinstance(result, dict) and "warnings" in result:
            return result
        return {"warnings": [], "overall_assessment": "Unexpected format"}
    except Exception as e:
        print(f"  ⚠️ Sanity check failed: {e}")
        return {"warnings": [], "overall_assessment": f"Failed: {e}", "skipped": True}
