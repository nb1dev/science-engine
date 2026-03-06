#!/usr/bin/env python3
"""
Rules Engine — All deterministic formulation rules.

Takes unified_input (from parse_inputs.py) and applies threshold-based rules
from knowledge_base JSONs. No LLM calls — pure Python logic.

Produces: rule_outputs dict with:
  - sensitivity_classification
  - health_claims (from goals + microbiome signals)
  - therapeutic_dose_triggers
  - prebiotic_dose_range
  - barrier_support_needed
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── KNOWLEDGE BASE LOADING ──────────────────────────────────────────────────

KB_DIR = Path(__file__).parent / "knowledge_base"

def _load_kb(filename: str) -> Dict:
    """Load a knowledge base JSON file."""
    path = KB_DIR / filename
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── SENSITIVITY CLASSIFICATION ───────────────────────────────────────────────

def classify_sensitivity(digestive: Dict) -> Dict:
    """
    Classify client sensitivity based on digestive questionnaire data.
    Returns: {"classification": "high"|"moderate"|"low", "reasoning": [...]}
    """
    kb = _load_kb("sensitivity_thresholds.json")
    rules = kb["classification_rules"]

    bloating_severity = digestive.get("bloating_severity")
    bloating_frequency = digestive.get("bloating_frequency", "")
    stool_type = digestive.get("stool_type")
    digestive_satisfaction = digestive.get("digestive_satisfaction")

    reasoning = []

    # Check HIGH sensitivity (OR conditions)
    high_triggered = False
    if bloating_severity is not None and bloating_severity >= 7:
        high_triggered = True
        reasoning.append(f"Bloating severity {bloating_severity}/10 (≥7 threshold)")
    if bloating_frequency and "daily" in str(bloating_frequency).lower():
        high_triggered = True
        reasoning.append(f"Daily bloating frequency")
    if stool_type is not None and stool_type in [6, 7]:
        high_triggered = True
        reasoning.append(f"Bristol stool type {stool_type} (loose/watery)")
    if digestive_satisfaction is not None and digestive_satisfaction <= 3:
        high_triggered = True
        reasoning.append(f"Digestive satisfaction {digestive_satisfaction}/10 (≤3 threshold)")

    if high_triggered:
        return {
            "classification": "high",
            "max_prebiotic_g": rules["high_sensitivity"]["max_prebiotic_g"],
            "prebiotic_clamp": rules["high_sensitivity"]["prebiotic_clamp"],
            "reasoning": reasoning
        }

    # Check LOW sensitivity (AND conditions)
    low_triggered = True
    if bloating_severity is not None and bloating_severity <= 3:
        reasoning.append(f"Bloating severity {bloating_severity}/10 (≤3 — low)")
    else:
        low_triggered = False

    if digestive_satisfaction is not None and digestive_satisfaction >= 7:
        reasoning.append(f"Digestive satisfaction {digestive_satisfaction}/10 (≥7 — good)")
    else:
        low_triggered = False

    if low_triggered:
        return {
            "classification": "low",
            "max_prebiotic_g": rules["low_sensitivity"]["max_prebiotic_g"],
            "prebiotic_clamp": rules["low_sensitivity"]["prebiotic_clamp"],
            "reasoning": reasoning
        }

    # Default: moderate
    if not reasoning:
        reasoning.append("Insufficient digestive data — defaulting to moderate")
    else:
        reasoning.append("Between high and low thresholds — moderate")

    return {
        "classification": "moderate",
        "max_prebiotic_g": rules["moderate_sensitivity"]["max_prebiotic_g"],
        "prebiotic_clamp": rules["moderate_sensitivity"]["prebiotic_clamp"],
        "reasoning": reasoning
    }


# ─── HEALTH CLAIM EXTRACTION ─────────────────────────────────────────────────

def extract_health_claims(goals: Dict, vitamin_signals: Dict) -> Dict:
    """
    Map questionnaire goals + microbiome vitamin signals to health claim categories.
    Returns: {"supplement_claims": [...], "vitamin_claims": [...], "microbiome_vitamin_needs": [...]}
    """
    kb = _load_kb("goal_to_health_claim.json")
    goal_mappings = kb["goal_mappings"]
    mb_signals = kb["microbiome_signal_to_vitamin_claims"]

    supplement_claims = set()
    vitamin_claims = set()
    triggers_timing = False
    claim_sources = []

    # Map questionnaire goals
    ranked_goals = goals.get("ranked", [])
    for goal in ranked_goals:
        goal_key = goal.lower().strip()
        if goal_key in goal_mappings:
            mapping = goal_mappings[goal_key]
            for claim in mapping.get("health_claims", []):
                supplement_claims.add(claim)
                claim_sources.append({"claim": claim, "source": "questionnaire_goal", "goal": goal_key})
            for vclaim in mapping.get("vitamin_claims", []):
                vitamin_claims.add(vclaim)
            if mapping.get("triggers_timing_rules"):
                triggers_timing = True

    # Map microbiome vitamin signals
    microbiome_vitamin_needs = []

    # Biotin
    biotin_signal = vitamin_signals.get("biotin", {})
    if biotin_signal.get("risk_level", 0) >= 1:
        vitamin_claims.add("Fatigue")
        vitamin_claims.add("Skin Quality")
        microbiome_vitamin_needs.append({
            "vitamin": "Biotin (B7)",
            "trigger": f"biotin risk_level={biotin_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # Folate
    folate_signal = vitamin_signals.get("folate", {})
    if folate_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        microbiome_vitamin_needs.append({
            "vitamin": "Folate (B9)",
            "trigger": f"folate risk_level={folate_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # B12
    b12_signal = vitamin_signals.get("B12", {})
    if b12_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        microbiome_vitamin_needs.append({
            "vitamin": "Vitamin B12",
            "trigger": f"B12 risk_level={b12_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    # B-complex
    bcomplex_signal = vitamin_signals.get("B_complex", {})
    if bcomplex_signal.get("risk_level", 0) >= 2:
        vitamin_claims.add("Immune System")
        vitamin_claims.add("Fatigue")
        vitamin_claims.add("Metabolism")
        microbiome_vitamin_needs.append({
            "vitamin": "B-Complex",
            "trigger": f"B-complex risk_level={bcomplex_signal.get('risk_level')}",
            "source": "microbiome_signal"
        })

    return {
        "supplement_claims": sorted(supplement_claims),
        "vitamin_claims": sorted(vitamin_claims),
        "microbiome_vitamin_needs": microbiome_vitamin_needs,
        "triggers_timing_rules": triggers_timing,
        "claim_sources": claim_sources,
    }


# ─── THERAPEUTIC DOSE TRIGGERS ────────────────────────────────────────────────

def check_therapeutic_triggers(medical: Dict, lifestyle: Dict) -> Dict:
    """
    Check if client has reported deficiencies that require therapeutic doses.
    Returns: {"therapeutic_vitamins": [...], "enhanced_vitamins": [...]}
    """
    kb = _load_kb("therapeutic_doses.json")
    dose_table = kb["therapeutic_dose_table"]

    reported_deficiencies = medical.get("vitamin_deficiencies", []) + medical.get("reported_deficiencies", [])
    # Normalize deficiency names
    reported_lower = [d.lower().strip() for d in reported_deficiencies if d]

    therapeutic_vitamins = []
    enhanced_vitamins = []

    # Symptom indicators
    has_brain_fog = "brain_fog" in str(lifestyle.get("stress_symptoms", [])).lower()
    has_fatigue = (lifestyle.get("energy_level") or "").lower() in ["very_low", "low"] if lifestyle.get("energy_level") else False
    age = None  # Will be passed separately if needed

    for entry in dose_table:
        vitamin_name = entry["vitamin"].lower()

        # Check if this vitamin is in reported deficiencies
        matched = False
        for deficiency in reported_lower:
            if vitamin_name.replace("vitamin ", "") in deficiency or deficiency in vitamin_name:
                matched = True
                break

        if not matched:
            continue

        # Determine dose tier
        has_symptoms = False
        if "b12" in vitamin_name and has_brain_fog:
            has_symptoms = True
        elif "d" in vitamin_name and has_fatigue:
            has_symptoms = True
        elif "iron" in vitamin_name and has_fatigue:
            has_symptoms = True

        if has_symptoms:
            therapeutic_vitamins.append({
                "vitamin": entry["vitamin"],
                "dose": entry["therapeutic_dose"],
                "standard_dose": entry["standard_dose"],
                "monitoring": entry["monitoring_required"],
                "masking_risk": entry.get("masking_risk"),
                "tier": "therapeutic",
                "reason": f"Reported {entry['vitamin']} deficiency with active symptoms"
            })
        else:
            enhanced_vitamins.append({
                "vitamin": entry["vitamin"],
                "dose": entry["enhanced_dose"],
                "standard_dose": entry["standard_dose"],
                "monitoring": entry["monitoring_required"],
                "tier": "enhanced",
                "reason": f"Reported {entry['vitamin']} deficiency without active symptoms"
            })

    return {
        "therapeutic_vitamins": therapeutic_vitamins,
        "enhanced_vitamins": enhanced_vitamins,
        "reported_deficiencies": reported_deficiencies,
    }


# ─── PREBIOTIC DOSE RANGE ────────────────────────────────────────────────────

def calculate_prebiotic_range(
    sensitivity: Dict,
    cfu_billions: int = 50,
    mix_id: int = None
) -> Dict:
    """
    Calculate allowed prebiotic gram range based on CFU tier + sensitivity.
    """
    kb = _load_kb("prebiotic_rules.json")
    dosing = kb["dosing_by_cfu_tier"]

    classification = sensitivity["classification"]

    # Determine CFU tier key
    if mix_id == 8 and cfu_billions <= 50:
        tier_key = "50B_mix8"
    elif cfu_billions <= 50:
        tier_key = "50B"
    elif cfu_billions <= 75:
        tier_key = "75B"
    else:
        tier_key = "100B"

    tier = dosing.get(tier_key, dosing["50B"])

    # Apply sensitivity clamp
    if classification == "high":
        g_range = tier.get("high_sensitivity", tier["total_g_range"])
    elif classification == "low":
        g_range = tier.get("low_high_tolerance", tier["total_g_range"])
    else:
        g_range = tier.get("moderate", tier["total_g_range"])

    # Also clamp by sensitivity max
    max_g = sensitivity.get("max_prebiotic_g", 10)
    g_range = [g_range[0], min(g_range[1], max_g)]

    return {
        "min_g": g_range[0],
        "max_g": g_range[1],
        "cfu_tier": tier_key,
        "sensitivity_clamp": classification,
        "note": tier.get("note", ""),
    }


# ─── BARRIER SUPPORT CHECK ───────────────────────────────────────────────────

def assess_magnesium_needs(lifestyle: Dict, goals: Dict) -> Dict:
    """
    Assess magnesium needs based on 3 criteria: sleep, sport, stress.
    Dosing: 2 capsules if ≥2 needs, 1 capsule if 1 need, 0 if no needs.
    Each capsule: 750mg Mg bisglycinate = 105mg elemental Mg.
    """
    needs = []
    reasoning = []
    ranked_goals = goals.get("ranked", [])

    # Need 1: Sleep
    sleep_quality = lifestyle.get("sleep_quality")
    sleep_goal = any("sleep" in g.lower() for g in ranked_goals)
    if (sleep_quality is not None and sleep_quality <= 7) or sleep_goal:
        needs.append("sleep")
        reason = []
        if sleep_quality is not None and sleep_quality <= 7:
            reason.append(f"sleep quality {sleep_quality}/10 ≤ 7")
        if sleep_goal:
            reason.append("sleep improvement is a stated goal")
        reasoning.append(f"Sleep: {' + '.join(reason)}")

    # Need 2: Sport/exercise
    exercise = lifestyle.get("exercise_frequency", "")
    exercise_str = str(exercise).lower() if exercise else ""
    sport_indicators = ["moderate", "vigorous", "strength", "cardio", "sport", "high", "good_all_day"]
    is_active = any(ind in exercise_str for ind in sport_indicators)
    if not is_active and lifestyle.get("energy_level"):
        energy = str(lifestyle.get("energy_level", "")).lower()
        if "good_all_day" in energy:
            is_active = True
    if is_active:
        needs.append("sport")
        reasoning.append(f"Sport: Active lifestyle ({exercise or 'inferred'})")

    # Need 3: Stress
    stress = lifestyle.get("stress_level")
    stress_goals = {"reduce_stress_anxiety", "improve_mood_reduce_anxiety"}
    stress_goal = any(g in stress_goals for g in ranked_goals)
    if (stress is not None and stress >= 6) or stress_goal:
        needs.append("stress")
        reason = []
        if stress is not None and stress >= 6:
            reason.append(f"stress {stress}/10 ≥ 6")
        if stress_goal:
            reason.append("stress/mood goal")
        reasoning.append(f"Stress: {' + '.join(reason)}")

    need_count = len(needs)
    capsules = 2 if need_count >= 2 else (1 if need_count == 1 else 0)

    return {
        "needs_identified": needs,
        "need_count": need_count,
        "capsules": capsules,
        "mg_bisglycinate_total_mg": capsules * 750,
        "elemental_mg_total_mg": capsules * 105,
        "reasoning": reasoning,
        "timing": None,  # Timing is determined by apply_timing_rules(), not hardcoded here
    }


def assess_softgel_needs(health_claims: Dict, medical: Dict, lifestyle: Dict, goals: Dict) -> Dict:
    """
    Assess whether client needs the fixed softgel (Omega + D3 + E + Astaxanthin).
    Client gets softgel if they need ANY ONE of the 4 components.
    Check contraindications from questionnaire.
    """
    needs = []
    reasoning = []
    contraindications = []
    ranked_goals = goals.get("ranked", [])

    # Omega-3 needs (broadly indicated)
    omega_triggers = {"improve_mood_reduce_anxiety", "reduce_stress_anxiety", "improve_skin_health",
                      "longevity_healthy_aging", "support_heart_health", "boost_energy_reduce_fatigue",
                      "improve_focus_concentration"}
    omega_goal = any(g in omega_triggers for g in ranked_goals)
    omega_claim = any(c in health_claims.get("supplement_claims", []) for c in
                      ["Stress/Anxiety", "Skin Quality", "Anti-inflammatory", "Memory & Cognition",
                       "Fatigue", "Triglycerides", "Blood Cholesterol"])
    if omega_goal or omega_claim:
        needs.append("omega3")
        reasoning.append(f"Omega-3: {'goal match' if omega_goal else 'health claim match'}")

    # Vitamin D needs
    vit_d_claims = any(c in health_claims.get("vitamin_claims", []) for c in ["Immune System"])
    vit_d_deficiency = any("d" in d.lower() for d in medical.get("vitamin_deficiencies", []) + medical.get("reported_deficiencies", []))
    if vit_d_claims or vit_d_deficiency:
        needs.append("vitamin_d")
        reason = []
        if vit_d_claims:
            reason.append("immune health claim")
        if vit_d_deficiency:
            reason.append("reported Vitamin D deficiency")
        reasoning.append(f"Vitamin D: {' + '.join(reason)}")

    # Vitamin E needs
    skin_goal = any("skin" in g.lower() for g in ranked_goals)
    if skin_goal:
        needs.append("vitamin_e")
        reasoning.append("Vitamin E: skin quality goal")

    # Astaxanthin needs
    sport_active = str(lifestyle.get("exercise_frequency", "")).lower()
    is_active = any(ind in sport_active for ind in ["moderate", "vigorous", "strength", "sport"])
    if not is_active and lifestyle.get("energy_level"):
        is_active = "good_all_day" in str(lifestyle.get("energy_level", "")).lower()
    if skin_goal or is_active:
        needs.append("astaxanthin")
        reason = []
        if skin_goal:
            reason.append("skin UV protection")
        if is_active:
            reason.append("muscle recovery (active lifestyle)")
        reasoning.append(f"Astaxanthin: {' + '.join(reason)}")

    # Contraindications check
    medications = medical.get("medications", [])
    meds_str = " ".join(str(m).lower() for m in medications) if medications else ""
    if "warfarin" in meds_str or "blood thinner" in meds_str or "anticoagulant" in meds_str:
        contraindications.append("Blood thinners — omega-3 may increase bleeding risk")
    if "chemotherapy" in meds_str:
        contraindications.append("Chemotherapy — Vitamin E may alter effectiveness")

    include = len(needs) > 0 and len(contraindications) == 0
    return {
        "include_softgel": include,
        "needs_identified": needs,
        "need_count": len(needs),
        "reasoning": reasoning,
        "contraindications": contraindications,
        "daily_count": 2 if include else 0,
    }


def select_sleep_supplements(lifestyle: Dict, goals: Dict) -> Dict:
    """Evidence-based sleep supplement selection.
    
    Decision tree:
    - Melatonin 1mg: ONLY for sleep onset problems (difficulty_falling_asleep)
    - L-Theanine 200-400mg: Default for arousal/relaxation (dose escalated for high stress + poor sleep)
    - Valerian 400mg: Only for maintenance issues + sleep_quality ≤4
    - Mg bisglycinate: Handled separately by assess_magnesium_needs()
    """
    sleep_quality = lifestyle.get("sleep_quality")
    stress_level = lifestyle.get("stress_level")
    stress_symptoms = lifestyle.get("stress_symptoms", [])
    ranked_goals = goals.get("ranked", [])
    
    # Parse sleep issues from questionnaire
    sleep_issues_raw = lifestyle.get("stress_symptoms", [])  # sleep issues often in stress section
    # Also check for sleep-specific fields if available
    sleep_issues = set()
    for item in sleep_issues_raw:
        item_lower = str(item).lower()
        if "falling_asleep" in item_lower or "difficulty_falling" in item_lower:
            sleep_issues.add("difficulty_falling_asleep")
        if "waking" in item_lower and "unrefreshed" in item_lower:
            sleep_issues.add("waking_unrefreshed")
        if "waking" in item_lower and ("during" in item_lower or "night" in item_lower):
            sleep_issues.add("waking_during_night")

    supplements = []
    reasoning = []

    # STEP 1: No supplement gate
    has_sleep_goal = any("sleep" in g.lower() for g in ranked_goals)
    if sleep_quality is not None and sleep_quality > 7 and not has_sleep_goal:
        return {"supplements": [], "reasoning": ["Sleep quality >7 and no sleep goal → no sleep supplement needed"]}

    # STEP 2: High arousal modifier
    high_stress = (
        stress_level is not None and stress_level >= 7 and
        any(s in str(stress_symptoms).lower() for s in ["racing_thoughts", "anxiety", "anxious", "on_edge"])
    )

    # L-theanine dose rule
    severe_sleep = sleep_quality is not None and sleep_quality <= 5
    l_theanine_dose = 400 if (high_stress and severe_sleep) else 200

    # STEP 3: Pattern cases
    has_onset = "difficulty_falling_asleep" in sleep_issues
    has_maintenance = "waking_during_night" in sleep_issues or "waking_unrefreshed" in sleep_issues

    if has_onset:
        # CASE 1: Sleep onset problem
        supplements.append({"substance": "Melatonin", "dose_mg": 1, "timing": "evening", "rationale": "Sleep onset problem — clock-resetter for sleep latency"})
        reasoning.append("Melatonin: difficulty_falling_asleep reported → onset problem")

        if stress_level is not None and (stress_level >= 5 or high_stress):
            supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                              "rationale": f"Arousal reduction for sleep onset (stress {stress_level}/10)"})
            reasoning.append(f"L-Theanine {l_theanine_dose}mg: stress {stress_level}/10 {'+ high arousal' if high_stress else ''}")

    elif has_maintenance:
        # CASE 2: Maintenance / non-restorative sleep
        supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                          "rationale": "Sleep maintenance — relaxation without sedation"})
        reasoning.append(f"L-Theanine {l_theanine_dose}mg: maintenance/non-restorative sleep pattern")

        if sleep_quality is not None and sleep_quality <= 4:
            supplements.append({"substance": "Valerian Root", "dose_mg": 400, "timing": "evening",
                              "rationale": f"Severe sleep maintenance (quality {sleep_quality}/10) — mild herbal hypnotic"})
            reasoning.append(f"Valerian 400mg: sleep quality ≤4 ({sleep_quality}/10) escalation")

    else:
        # CASE 3: No specific issues but sleep ≤7 or sleep goal
        supplements.append({"substance": "L-Theanine", "dose_mg": l_theanine_dose, "timing": "evening",
                          "rationale": "General sleep support — safe relaxation aid"})
        reasoning.append(f"L-Theanine {l_theanine_dose}mg: general poor sleep (no specific issues reported)")

        if sleep_quality is not None and sleep_quality <= 4:
            supplements.append({"substance": "Melatonin", "dose_mg": 1, "timing": "evening",
                              "rationale": f"Severe sleep quality ({sleep_quality}/10) — consider clock-resetter"})
            reasoning.append("Melatonin 1mg: severe fallback (sleep ≤4)")

    # STEP 4: Global stress safety check
    if high_stress:
        has_theanine = any(s["substance"] == "L-Theanine" for s in supplements)
        if not has_theanine:
            supplements.append({"substance": "L-Theanine", "dose_mg": 200, "timing": "evening",
                              "rationale": "High stress safety net — cognitive arousal management"})
            reasoning.append("L-Theanine 200mg: high stress safety check (wasn't already added)")

        has_melatonin = any(s["substance"] == "Melatonin" for s in supplements)
        if has_melatonin and not has_onset:
            supplements = [s for s in supplements if s["substance"] != "Melatonin"]
            reasoning.append("Melatonin removed: high stress but no onset problem confirmed")

    return {"supplements": supplements, "reasoning": reasoning}


# ─── TIMING OPTIMIZATION ─────────────────────────────────────────────────────

def apply_timing_rules(
    lifestyle: Dict,
    goals: Dict,
    selected_components: List[str] = None
) -> Dict:
    """
    Apply universal timing rules (Framework Step 7.5).
    Returns timing assignments for magnesium, ashwagandha, L-theanine.
    """
    kb = _load_kb("timing_rules.json")
    rules = kb["universal_rules"]

    sleep_quality = lifestyle.get("sleep_quality")
    stress_level = lifestyle.get("stress_level")
    energy_level = lifestyle.get("energy_level")
    ranked_goals = goals.get("ranked", [])
    top_goal = goals.get("top_goal", "")

    timing_assignments = {}
    evening_components = []

    # Rule 1: Magnesium ALWAYS evening timing
    # Mg bisglycinate is always taken in the evening (30-60 min before bed).
    # Dosing (1 vs 2 capsules) depends on need scoring — timing does not vary.
    magnesium_evening = True
    mg_reason_parts = []
    if sleep_quality is not None and sleep_quality <= 7:
        mg_reason_parts.append(f"sleep quality {sleep_quality}/10")
    if top_goal and "sleep" in top_goal.lower():
        mg_reason_parts.append("sleep goal")
    mg_reason = f"Always evening — Mg bisglycinate supports sleep, recovery, and relaxation"
    if mg_reason_parts:
        mg_reason += f" ({'; '.join(mg_reason_parts)})"
    timing_assignments["magnesium"] = {
        "timing": "evening",
        "delivery": "evening_hard_capsule",
        "reason": mg_reason
    }
    evening_components.append("Magnesium")

    # Rule 2: Ashwagandha timing — considers ALL goals (not just top goal)
    # Ashwagandha is primarily calming (cortisol reduction) → evening default
    # Only morning if pure energy/focus goal with NO calming needs
    CALMING_KEYWORDS = {"sleep", "anxiety", "mood", "stress", "relax"}
    ENERGY_KEYWORDS = {"energy", "focus", "concentration", "fatigue"}
    has_calming_goal = any(
        any(kw in g.lower() for kw in CALMING_KEYWORDS)
        for g in ranked_goals
    ) if ranked_goals else False
    has_energy_goal = any(
        any(kw in g.lower() for kw in ENERGY_KEYWORDS)
        for g in ranked_goals
    ) if ranked_goals else False

    if has_calming_goal and sleep_quality is not None and sleep_quality <= 7:
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": f"Calming goal + sleep {sleep_quality}/10 → evening synergy"
        }
        evening_components.append("Ashwagandha")
    elif has_calming_goal:
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": "Calming goal (anxiety/stress/mood) → evening"
        }
        evening_components.append("Ashwagandha")
    elif has_energy_goal:
        timing_assignments["ashwagandha"] = {
            "timing": "morning",
            "delivery": "morning_hard_capsule",
            "reason": "Pure energy/focus goal, no calming needs → morning"
        }
    else:
        # Default: evening (Ashwagandha is a calming adaptogen)
        timing_assignments["ashwagandha"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "reason": "Default evening — Ashwagandha is calming adaptogen"
        }
        evening_components.append("Ashwagandha")

    # Rule 3: L-Theanine sleep synergy (also triggered by mood/anxiety goals — calming synergy)
    EVENING_SYNERGY_KEYWORDS = {"sleep", "anxiety", "mood", "stress", "relax"}
    has_sleep_goal = any("sleep" in g.lower() for g in ranked_goals) if ranked_goals else False
    has_calming_goal = any(
        any(kw in g.lower() for kw in EVENING_SYNERGY_KEYWORDS)
        for g in (ranked_goals or [])
    )
    if magnesium_evening and has_calming_goal:
        timing_assignments["l_theanine"] = {
            "timing": "evening",
            "delivery": "evening_hard_capsule",
            "join_with": "Magnesium",
            "reason": "Sleep synergy — joins Mg in evening capsule"
        }
        if "L-Theanine" not in evening_components:
            evening_components.append("L-Theanine")
    else:
        timing_assignments["l_theanine"] = {
            "timing": "morning",
            "delivery": "morning_sachet",
            "reason": "No sleep synergy trigger — morning timing"
        }

    return {
        "timing_assignments": timing_assignments,
        "evening_components": evening_components,
        "evening_capsule_needed": len(evening_components) > 0,
    }


# ─── POLYPHENOL EXCLUSION CHECKS ─────────────────────────────────────────────

def check_polyphenol_exclusions(medical: Dict, demographics: Dict = None) -> Dict:
    """
    Check if any polyphenols should be excluded based on medical conditions.
    
    Exclusion rules (from nutritionist-confirmed Quercetin cautions):
    - Pregnancy/breastfeeding → auto-exclude Quercetin
    - Kidney disease → auto-exclude Quercetin
    - Anticoagulants/antiplatelets → flag Quercetin interaction (auto-remove in pipeline)
    
    Returns: {
        "excluded_substances": [list of substance names to exclude],
        "flagged_interactions": [list of interaction warnings],
        "reasoning": [list of reasoning strings]
    }
    """
    excluded = []
    flagged = []
    reasoning = []

    # Normalize medical data
    medical_history = [str(c).lower() for c in medical.get("medical_conditions", []) + medical.get("conditions", []) + medical.get("diagnoses", [])] if medical else []
    medications = [str(m).lower() for m in medical.get("medications", [])] if medical else []
    meds_str = " ".join(medications)

    # Check pregnancy/breastfeeding
    is_pregnant = any(kw in c for c in medical_history for kw in ["pregnant", "pregnancy", "expecting"])
    is_breastfeeding = any(kw in c for c in medical_history for kw in ["breastfeed", "lactating", "nursing"])
    # Also check demographics if available
    if demographics:
        preg_field = demographics.get("pregnant") or demographics.get("pregnancy") or demographics.get("is_pregnant")
        if preg_field and str(preg_field).lower() in ("true", "yes", "1"):
            is_pregnant = True
        bf_field = demographics.get("breastfeeding") or demographics.get("is_breastfeeding") or demographics.get("lactating")
        if bf_field and str(bf_field).lower() in ("true", "yes", "1"):
            is_breastfeeding = True

    if is_pregnant or is_breastfeeding:
        excluded.append("quercetin")
        status = "pregnant" if is_pregnant else "breastfeeding"
        reasoning.append(f"Quercetin auto-excluded: client is {status} (nutritionist rule: don't add unless specifically cleared)")

    # Check kidney disease
    kidney_keywords = ["kidney disease", "renal disease", "kidney failure", "renal failure", "ckd",
                       "chronic kidney", "kidney impairment", "renal impairment", "dialysis",
                       "nephropathy", "kidney stones", "renal stones"]
    has_kidney = any(kw in c for c in medical_history for kw in kidney_keywords)
    if has_kidney:
        excluded.append("quercetin")
        reasoning.append("Quercetin auto-excluded: kidney disease reported")

    # Check anticoagulants/antiplatelets
    anticoag_keywords = ["warfarin", "coumadin", "blood thinner", "anticoagulant", "antiplatelet",
                         "aspirin", "clopidogrel", "plavix", "heparin", "enoxaparin", "rivaroxaban",
                         "apixaban", "dabigatran", "edoxaban"]
    has_anticoag = any(kw in meds_str for kw in anticoag_keywords)
    if has_anticoag:
        if "quercetin" not in excluded:
            excluded.append("quercetin")
        flagged.append({
            "substance": "Quercetin",
            "interaction_type": "anticoagulant",
            "severity": "high",
            "warning": "Quercetin may potentiate anticoagulant/antiplatelet effects — auto-excluded",
            "medications_matched": [m for m in medications if any(kw in m for kw in anticoag_keywords)]
        })
        reasoning.append(f"Quercetin flagged+excluded: anticoagulant/antiplatelet medication detected")

    # Check multiple medications (flag only, don't auto-exclude)
    if len(medications) >= 3 and "quercetin" not in excluded:
        flagged.append({
            "substance": "Quercetin",
            "interaction_type": "polypharmacy",
            "severity": "medium",
            "warning": f"Client takes {len(medications)} medications — Quercetin flagged for review",
        })
        reasoning.append(f"Quercetin flagged for review: {len(medications)} concurrent medications")

    return {
        "excluded_substances": list(set(excluded)),
        "flagged_interactions": flagged,
        "reasoning": reasoning,
    }


# ─── GOAL-TRIGGERED MANDATORY SUPPLEMENTS ─────────────────────────────────────

def assess_goal_triggered_supplements(goals: Dict, lifestyle: Dict) -> Dict:
    """Deterministic supplement rules based on client goals.
    
    These supplements MUST be in the formula when specific goals are present.
    The LLM is told to include them with proper KB doses; Stage D validates presence.
    
    Rules:
    1. Energy/fatigue goal → B9 (Folate) + B12 + Vitamin C (standardized)
    2. Sleep goal → L-Theanine (already handled by select_sleep_supplements)
    
    Note: Glutathione is now LLM-decided based on KB health claims
    (Anti-inflammatory, Infection Susceptibility, Skin Quality, Sport/Recovery).
    
    Returns: {
        "mandatory_vitamins": [{"substance": ..., "reason": ...}],
        "mandatory_supplements": [{"substance": ..., "reason": ...}],
        "reasoning": [...]
    }
    """
    ranked_goals = goals.get("ranked", [])
    ranked_lower = [g.lower() for g in ranked_goals]
    reasoning = []
    
    mandatory_vitamins = []
    mandatory_supplements = []
    
    # Rule 1: Energy/fatigue → standardize B9 + B12 + Vitamin C
    ENERGY_KEYWORDS = {"energy", "fatigue", "reduce_fatigue", "boost_energy"}
    has_energy = any(any(kw in g for kw in ENERGY_KEYWORDS) for g in ranked_lower)
    if has_energy:
        mandatory_vitamins.append({"substance": "Folate (B9)", "reason": "Energy goal → standardized B9 (Marijn rule)"})
        mandatory_vitamins.append({"substance": "Vitamin B12", "reason": "Energy goal → standardized B12 (Marijn rule)"})
        mandatory_vitamins.append({"substance": "Vitamin C", "reason": "Energy goal → standardized Vitamin C (Marijn rule)"})
        reasoning.append("Energy/fatigue goal → B9 + B12 + Vitamin C standardized")
    
    # Note: Glutathione selection is now handled by the LLM supplement selection step
    # based on KB health claims (Anti-inflammatory, Infection Susceptibility, Skin Quality, Sport/Recovery).
    # It is no longer forced deterministically.
    
    return {
        "mandatory_vitamins": mandatory_vitamins,
        "mandatory_supplements": mandatory_supplements,
        "reasoning": reasoning,
    }


# ─── MAIN RULES ENGINE ───────────────────────────────────────────────────────

def apply_rules(unified_input: Dict) -> Dict:
    """
    Main entry point — apply all deterministic rules to unified input.

    Args:
        unified_input: Output from parse_inputs.parse_inputs()

    Returns:
        rule_outputs dict with all deterministic decisions
    """
    microbiome = unified_input["microbiome"]
    questionnaire = unified_input["questionnaire"]

    # 1. Sensitivity classification (with FODMAP digestion-goal override)
    sensitivity = classify_sensitivity(questionnaire["digestive"])
    
    # FODMAP override: if digestion comfort is a goal but no bloating reported,
    # treat as at least moderate sensitivity (conservative FODMAP clamping)
    ranked_goals = questionnaire["goals"].get("ranked", [])
    has_digestion_goal = any("digestion" in g.lower() or "comfort" in g.lower() for g in ranked_goals)
    bloating = questionnaire["digestive"].get("bloating_severity")
    if has_digestion_goal and (bloating is None or bloating <= 3) and sensitivity["classification"] == "low":
        sensitivity["classification"] = "moderate"
        sensitivity["reasoning"].append(f"FODMAP override: digestion goal present but bloating {bloating or 'N/R'}/10 → bumped to moderate (conservative)")

    # 2. Health claim extraction
    health_claims = extract_health_claims(
        questionnaire["goals"],
        microbiome["vitamin_signals"]
    )

    # 3. Therapeutic dose triggers
    therapeutic = check_therapeutic_triggers(
        questionnaire["medical"],
        questionnaire["lifestyle"]
    )

    # 4. Prebiotic dose range (using default 50B CFU — will be refined after mix selection)
    prebiotic_range = calculate_prebiotic_range(sensitivity, cfu_billions=50)

    # 5. Magnesium need assessment (replaces old barrier support)
    magnesium = assess_magnesium_needs(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 6. Softgel needs assessment
    softgel = assess_softgel_needs(
        health_claims,
        questionnaire["medical"],
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 7. Sleep supplement selection (evidence-based)
    sleep_supplements = select_sleep_supplements(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 8. Timing optimization
    timing = apply_timing_rules(
        questionnaire["lifestyle"],
        questionnaire["goals"]
    )

    # 9. Goal-triggered mandatory supplements (deterministic)
    goal_triggered = assess_goal_triggered_supplements(
        questionnaire["goals"],
        questionnaire["lifestyle"]
    )

    return {
        "sensitivity": sensitivity,
        "health_claims": health_claims,
        "therapeutic_triggers": therapeutic,
        "prebiotic_range": prebiotic_range,
        "magnesium": magnesium,
        "softgel": softgel,
        "sleep_supplements": sleep_supplements,
        "timing": timing,
        "goal_triggered_supplements": goal_triggered,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from parse_inputs import parse_inputs

    parser = argparse.ArgumentParser(description="Apply formulation rules to sample")
    parser.add_argument("--sample-dir", required=True, help="Path to sample directory")
    parser.add_argument("--output", help="Optional: save rule outputs JSON to file")
    args = parser.parse_args()

    unified = parse_inputs(args.sample_dir)
    results = apply_rules(unified)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Rule outputs saved to: {args.output}")
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False))
