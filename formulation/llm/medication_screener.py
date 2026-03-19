#!/usr/bin/env python3
"""
LLM Medication Interaction Screening.

Input:  Client medications + full supplement/vitamin database.
Output: Definitive exclusion set of substances that must never enter the formulation.
"""

import json
from pathlib import Path
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response, HAS_BOTO3

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


def _load_kb(filename: str) -> Dict:
    with open(KB_DIR / filename, 'r', encoding='utf-8') as f:
        return json.load(f)


MEDICATION_SCREENING_SYSTEM = """You are a clinical pharmacologist screening medications against a supplement database.

Identify every supplement CONTRAINDICATED or carrying HIGH-SEVERITY interaction risk.

RULES:
- Recognize brand names in ANY language
- Identify pharmacological CLASS of each medication
- Only flag HIGH-severity interactions (clinically significant harm risk)
- Do NOT flag timing-based interactions resolvable by spacing

Return ONLY:
{
  "excluded_substances": ["substance_1", ...],
  "exclusion_reasons": [{"substance": "...", "medication": "...", "mechanism": "...", "severity": "high"}]
}"""


def screen_medication_interactions(unified_input: Dict, use_bedrock: bool = True) -> Dict:
    """LLM medication interaction screening.

    Returns:
        {excluded_substances: set, exclusion_reasons: list, skipped: bool}
    """
    if not use_bedrock or not HAS_BOTO3:
        return {"excluded_substances": set(), "exclusion_reasons": [], "skipped": True}

    medications = unified_input.get("questionnaire", {}).get("medical", {}).get("medications", [])
    if not medications:
        return {"excluded_substances": set(), "exclusion_reasons": [], "skipped": False}

    med_lines = []
    for m in medications:
        if isinstance(m, dict):
            med_lines.append(f"- {m.get('name', '')} {m.get('dosage', '')} ({m.get('how_long', '')})")
        else:
            med_lines.append(f"- {m}")
    meds_formatted = "\n".join(med_lines)

    # Build substance list from KBs
    substance_lines = []
    try:
        for entry in _load_kb("supplements_nonvitamins.json").get("supplements_flat", []):
            substance_lines.append(f"- {entry.get('substance', '')} (risk: {entry.get('interaction_risk', '')})")
    except Exception:
        pass
    try:
        for entry in _load_kb("vitamins_minerals.json").get("vitamins_and_minerals", []):
            substance_lines.append(f"- {entry.get('substance', '')} (note: {entry.get('interaction_note', '')})")
    except Exception:
        pass

    user_prompt = f"""## Patient Medications
{meds_formatted}

## Available Supplements
{chr(10).join(substance_lines)}

Identify ALL high-severity interactions. Return ONLY JSON."""

    try:
        response_text = call_bedrock(MEDICATION_SCREENING_SYSTEM, user_prompt, max_tokens=2000, temperature=0.1)
        result = extract_json_from_response(response_text)
        excluded_set = {name.lower().strip() for name in result.get("excluded_substances", [])}
        return {"excluded_substances": excluded_set, "exclusion_reasons": result.get("exclusion_reasons", []), "skipped": False}
    except Exception as e:
        print(f"  ⚠️ Medication screening failed: {e}")
        return {"excluded_substances": set(), "exclusion_reasons": [], "skipped": True}
