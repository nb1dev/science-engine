#!/usr/bin/env python3
"""
LLM Clinical Questionnaire Analysis — Profile narrative, inferred signals, review flags.

Input:  Full questionnaire data from unified_input.
Output: {profile_narrative, inferred_health_signals, clinical_review_flags}
"""

import json
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response, HAS_BOTO3


def analyze_questionnaire_clinical(unified_input: Dict, use_bedrock: bool = True) -> Dict:
    """Analyze questionnaire for clinical profile, inferred signals, and review flags.

    Returns:
        {
          "profile_narrative": ["bullet 1", ...],
          "inferred_health_signals": [{"signal": "...", "reason": "..."}, ...],
          "clinical_review_flags": [{"severity": "high", "title": "...", "detail": "..."}, ...]
        }
    """
    if not use_bedrock or not HAS_BOTO3:
        return {"profile_narrative": [], "inferred_health_signals": [], "clinical_review_flags": [], "skipped": True}

    q = unified_input.get("questionnaire", {})
    demographics = q.get("demographics", {})
    lifestyle = q.get("lifestyle", {})
    medical = q.get("medical", {})
    digestive = q.get("digestive", {})
    goals = q.get("goals", {}).get("ranked", [])
    diet = q.get("diet", {})
    food_triggers = q.get("food_triggers", {})
    exercise = lifestyle.get("exercise_detail", {})

    meds = medical.get("medications", [])
    meds_formatted = "; ".join(
        f"{m.get('name','')} {m.get('dosage','')} ({m.get('how_long','')})" if isinstance(m, dict) else str(m)
        for m in meds
    ) if meds else "None reported"

    fh = medical.get("family_history", {})
    fh_parts = []
    if isinstance(fh, dict):
        for condition, data in fh.items():
            if isinstance(data, dict) and data.get("has"):
                fh_parts.append(f"{condition} ({data.get('relatives', 'unknown')})")
    fh_formatted = "; ".join(fh_parts) if fh_parts else "None reported"

    ex_types = exercise.get("types", [])
    ex_str = (
        f"{', '.join(ex_types) if ex_types else 'not specified'} | "
        f"moderate {exercise.get('moderate_days_per_week','?')}x/week, "
        f"vigorous {exercise.get('vigorous_days_per_week','?')}x/week | "
        f"steps/day: {exercise.get('avg_daily_steps','?')} | "
        f"sitting: {exercise.get('hours_sitting_per_day','?')}h/day"
    )

    # ── Map raw enum values to human-readable labels ──────────────────────────
    UTI_LABELS = {
        "none_or_rarely": "None (0/year)",
        "none": "None (0/year)",
        "rarely": "Rarely (0–1/year)",
        "1-2": "Occasional (1–2/year)",
        "3+": "Frequent (3+/year)",
        "": "Not reported",
    }
    COLDS_LABELS = {
        "rarely_0_1": "Rarely (0–1/year)",
        "none": "None (0/year)",
        "2-3": "Occasionally (2–3/year)",
        "4+": "Frequently (4+/year)",
        "": "Not reported",
    }
    uti_label = UTI_LABELS.get(medical.get('uti_per_year', ''), medical.get('uti_per_year', '?'))
    colds_label = COLDS_LABELS.get(medical.get('colds_per_year', ''), medical.get('colds_per_year', '?'))

    system_prompt = """You are a clinical nutritionist conducting a detailed clinical review of a patient questionnaire.

Your task:
1. Write bullet-point clinical profile
2. Identify symptoms implying additional health claims not explicitly stated as goals
3. Flag everything requiring human clinical review

Return ONLY valid JSON:
{
  "profile_narrative": ["• Demographic: ...", ...],
  "inferred_health_signals": [{"signal": "skin_quality", "reason": "Persistent acne + triggers"}, ...],
  "clinical_review_flags": [{"severity": "high", "title": "...", "detail": "..."}, ...]
}

Valid signals: ["infection_susceptibility", "skin_quality", "bowel_function", "fatigue", "immune_system",
"stress_anxiety", "sleep_quality", "hormone_balance", "anti_inflammatory", "heart_health",
"weight_management", "bone_health"]
Severity: "high" | "medium" | "low"."""

    user_prompt = f"""DEMOGRAPHICS: Sex: {demographics.get('biological_sex', '?')} | Age: {demographics.get('age', '?')}
Weight: {demographics.get('weight_kg', '?')}kg | BMI: {demographics.get('bmi_context') or demographics.get('bmi', '?')}

GOALS: {chr(10).join(f"  {i+1}. {g.replace('_',' ')}" for i, g in enumerate(goals))}

DIAGNOSES: {', '.join(medical.get('diagnoses', [])) or 'None'}
MEDICATIONS: {meds_formatted}
Drug allergies: {medical.get('drug_allergies', '') or 'None'}

SKIN: Concerns: {medical.get('skin_concerns', [])} | Persistence: {medical.get('skin_persistence', '')}

DIGESTIVE: Satisfaction: {digestive.get('digestive_satisfaction', '?')}/10 | Bloating: {digestive.get('bloating_frequency', '?')}
Stress worsens digestion: {digestive.get('digestive_symptoms_with_stress', '?')}

LIFESTYLE: Stress: {lifestyle.get('stress_level', '?')}/10 | Sleep: {lifestyle.get('sleep_quality', '?')}/10
Energy: {lifestyle.get('energy_level', '?')} | Exercise: {ex_str}

DIET: Pattern: {diet.get('diet_pattern', '?')} | Fiber: {diet.get('fiber_intake', '?')}

INFECTIONS: UTI: {uti_label} | Colds: {colds_label}

FAMILY HISTORY: {fh_formatted}"""

    try:
        raw = call_bedrock(system_prompt, user_prompt, max_tokens=2000, temperature=0.2)
        result = extract_json_from_response(raw)

        # Normalise inferred signals
        raw_signals = result.get("inferred_health_signals", [])
        normalised = []
        for s in raw_signals:
            if isinstance(s, dict) and "signal" in s:
                normalised.append(s)
            elif isinstance(s, str):
                normalised.append({"signal": s, "reason": ""})

        return {
            "profile_narrative": result.get("profile_narrative", []),
            "inferred_health_signals": normalised,
            "clinical_review_flags": result.get("clinical_review_flags", []),
        }
    except Exception as e:
        print(f"  ⚠️ Clinical questionnaire analysis failed: {e}")
        return {"profile_narrative": [], "inferred_health_signals": [], "clinical_review_flags": []}
