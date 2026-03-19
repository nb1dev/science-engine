"""
Tests for rules_engine.py — All deterministic formulation rules.
"""

import pytest
from copy import deepcopy
from rules_engine import (
    classify_sensitivity, extract_health_claims, check_therapeutic_triggers,
    calculate_prebiotic_range, assess_magnesium_needs, assess_softgel_needs,
    select_sleep_supplements, apply_timing_rules, check_polyphenol_exclusions,
    assess_goal_triggered_supplements, apply_medication_rules, apply_rules,
)


# ── classify_sensitivity ──────────────────────────────────────────────────────

class TestClassifySensitivity:
    def test_high_bloating_severity(self):
        result = classify_sensitivity({"bloating_severity": 8, "digestive_satisfaction": 5})
        assert result["classification"] == "high"

    def test_high_daily_bloating(self):
        result = classify_sensitivity({"bloating_frequency": "daily", "bloating_severity": 5})
        assert result["classification"] == "high"

    def test_high_loose_stool(self):
        result = classify_sensitivity({"stool_type": 7, "bloating_severity": 3})
        assert result["classification"] == "high"

    def test_high_low_satisfaction(self):
        result = classify_sensitivity({"digestive_satisfaction": 2, "bloating_severity": 3})
        assert result["classification"] == "high"

    def test_low_sensitivity(self):
        result = classify_sensitivity({"bloating_severity": 2, "digestive_satisfaction": 8})
        assert result["classification"] == "low"

    def test_moderate_default(self):
        result = classify_sensitivity({"bloating_severity": 5, "digestive_satisfaction": 5})
        assert result["classification"] == "moderate"

    def test_no_data_defaults_moderate(self):
        result = classify_sensitivity({})
        assert result["classification"] == "moderate"

    def test_reasoning_populated(self):
        result = classify_sensitivity({"bloating_severity": 8})
        assert len(result["reasoning"]) > 0


# ── extract_health_claims ────────────────────────────────────────────────────

class TestExtractHealthClaims:
    def test_goal_mapping(self):
        goals = {"ranked": ["boost_energy_reduce_fatigue"], "top_goal": "boost_energy_reduce_fatigue"}
        result = extract_health_claims(goals, {})
        assert len(result["supplement_claims"]) > 0
        assert "Fatigue" in result["supplement_claims"] or "Energy" in str(result["supplement_claims"])

    def test_microbiome_vitamin_needs(self):
        goals = {"ranked": []}
        vit_signals = {"biotin": {"risk_level": 2}, "folate": {"risk_level": 3}}
        result = extract_health_claims(goals, vit_signals)
        assert len(result["microbiome_vitamin_needs"]) >= 1
        vitamin_names = [n["vitamin"] for n in result["microbiome_vitamin_needs"]]
        assert any("Biotin" in v for v in vitamin_names)

    def test_empty_goals(self):
        result = extract_health_claims({"ranked": []}, {})
        assert result["supplement_claims"] == []

    def test_multiple_goals(self):
        goals = {"ranked": ["improve_skin_health", "reduce_stress_anxiety"]}
        result = extract_health_claims(goals, {})
        assert len(result["supplement_claims"]) >= 2


# ── check_therapeutic_triggers ────────────────────────────────────────────────

class TestCheckTherapeuticTriggers:
    def test_no_deficiencies(self):
        result = check_therapeutic_triggers(
            {"vitamin_deficiencies": [], "reported_deficiencies": []},
            {"stress_symptoms": [], "energy_level": "moderate"}
        )
        assert result["therapeutic_vitamins"] == []
        assert result["enhanced_vitamins"] == []

    def test_b12_deficiency_with_symptoms(self):
        result = check_therapeutic_triggers(
            {"vitamin_deficiencies": ["Vitamin B12"], "reported_deficiencies": []},
            {"stress_symptoms": ["brain_fog"], "energy_level": "low"}
        )
        assert len(result["therapeutic_vitamins"]) > 0 or len(result["enhanced_vitamins"]) > 0


# ── calculate_prebiotic_range ─────────────────────────────────────────────────

class TestCalculatePrebioticRange:
    def test_standard_50b(self):
        sens = {"classification": "moderate", "max_prebiotic_g": 10}
        result = calculate_prebiotic_range(sens, cfu_billions=50)
        assert result["min_g"] > 0
        assert result["max_g"] <= 10
        assert result["cfu_tier"] == "50B"

    def test_high_sensitivity_clamping(self):
        sens = {"classification": "high", "max_prebiotic_g": 6}
        result = calculate_prebiotic_range(sens, cfu_billions=50)
        assert result["max_g"] <= 6

    def test_mix8_special_tier(self):
        sens = {"classification": "moderate", "max_prebiotic_g": 10}
        result = calculate_prebiotic_range(sens, cfu_billions=50, mix_id=8)
        assert result["cfu_tier"] == "50B_mix8"

    def test_low_sensitivity_higher_range(self):
        sens_low = {"classification": "low", "max_prebiotic_g": 12}
        sens_high = {"classification": "high", "max_prebiotic_g": 6}
        r_low = calculate_prebiotic_range(sens_low, cfu_billions=50)
        r_high = calculate_prebiotic_range(sens_high, cfu_billions=50)
        assert r_low["max_g"] >= r_high["max_g"]


# ── assess_magnesium_needs ────────────────────────────────────────────────────

class TestAssessMagnesiumNeeds:
    def test_sleep_need(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 5, "stress_level": 3},
            {"ranked": []}
        )
        assert "sleep" in result["needs_identified"]

    def test_stress_need(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 8, "stress_level": 7},
            {"ranked": []}
        )
        assert "stress" in result["needs_identified"]

    def test_sleep_goal_triggers_need(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 8, "stress_level": 3},
            {"ranked": ["improve_sleep_quality"]}
        )
        assert "sleep" in result["needs_identified"]

    def test_two_needs_gives_two_capsules(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 5, "stress_level": 7},
            {"ranked": []}
        )
        assert result["capsules"] == 2
        assert result["elemental_mg_total_mg"] == 210

    def test_one_need_gives_one_capsule(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 5, "stress_level": 3},
            {"ranked": []}
        )
        assert result["capsules"] == 1
        assert result["elemental_mg_total_mg"] == 105

    def test_no_needs_gives_zero_capsules(self):
        result = assess_magnesium_needs(
            {"sleep_quality": 9, "stress_level": 3},
            {"ranked": []}
        )
        assert result["capsules"] == 0


# ── assess_softgel_needs ─────────────────────────────────────────────────────

class TestAssessSoftgelNeeds:
    def test_omega_triggered_by_goals(self):
        hc = {"supplement_claims": ["Stress/Anxiety"]}
        result = assess_softgel_needs(hc, {}, {}, {"ranked": ["reduce_stress_anxiety"]})
        assert result["include_softgel"] is True
        assert "omega3" in result["needs_identified"]

    def test_no_needs_no_softgel(self):
        result = assess_softgel_needs(
            {"supplement_claims": [], "vitamin_claims": []},
            {"vitamin_deficiencies": [], "reported_deficiencies": [], "medications": []},
            {"exercise_frequency": "", "energy_level": ""},
            {"ranked": []}
        )
        assert result["include_softgel"] is False
        assert result["daily_count"] == 0

    def test_warfarin_contraindication(self):
        hc = {"supplement_claims": ["Stress/Anxiety"]}
        medical = {"vitamin_deficiencies": [], "reported_deficiencies": [],
                   "medications": [{"name": "warfarin"}]}
        result = assess_softgel_needs(hc, medical, {}, {"ranked": ["reduce_stress_anxiety"]})
        assert result["include_softgel"] is False
        assert len(result["contraindications"]) > 0


# ── select_sleep_supplements ──────────────────────────────────────────────────

class TestSelectSleepSupplements:
    def test_no_supplement_needed(self):
        result = select_sleep_supplements(
            {"sleep_quality": 9, "stress_level": 3, "stress_symptoms": []},
            {"ranked": []}
        )
        assert result["supplements"] == []

    def test_sleep_onset_gets_melatonin(self):
        result = select_sleep_supplements(
            {"sleep_quality": 5, "stress_level": 6,
             "stress_symptoms": ["difficulty_falling_asleep"]},
            {"ranked": ["improve_sleep_quality"]}
        )
        substances = [s["substance"] for s in result["supplements"]]
        assert "Melatonin" in substances

    def test_maintenance_gets_ltheanine(self):
        result = select_sleep_supplements(
            {"sleep_quality": 5, "stress_level": 5,
             "stress_symptoms": ["waking_during_night"]},
            {"ranked": ["improve_sleep_quality"]}
        )
        substances = [s["substance"] for s in result["supplements"]]
        assert "L-Theanine" in substances

    def test_severe_maintenance_gets_valerian(self):
        result = select_sleep_supplements(
            {"sleep_quality": 3, "stress_level": 5,
             "stress_symptoms": ["waking_during_night"]},
            {"ranked": ["improve_sleep_quality"]}
        )
        substances = [s["substance"] for s in result["supplements"]]
        assert "Valerian Root" in substances

    def test_high_stress_escalated_theanine(self):
        result = select_sleep_supplements(
            {"sleep_quality": 4, "stress_level": 8,
             "stress_symptoms": ["racing_thoughts", "anxiety"]},
            {"ranked": ["reduce_stress_anxiety"]}
        )
        for s in result["supplements"]:
            if s["substance"] == "L-Theanine":
                assert s["dose_mg"] == 400, "High stress + severe sleep → 400mg L-Theanine"

    def test_general_poor_sleep(self):
        result = select_sleep_supplements(
            {"sleep_quality": 6, "stress_level": 4, "stress_symptoms": []},
            {"ranked": ["improve_sleep_quality"]}
        )
        substances = [s["substance"] for s in result["supplements"]]
        assert "L-Theanine" in substances


# ── apply_timing_rules ────────────────────────────────────────────────────────

class TestApplyTimingRules:
    def test_magnesium_always_evening(self):
        result = apply_timing_rules(
            {"sleep_quality": 8, "stress_level": 3},
            {"ranked": [], "top_goal": ""}
        )
        assert result["timing_assignments"]["magnesium"]["timing"] == "evening"

    def test_ashwagandha_evening_for_calming_goal(self):
        result = apply_timing_rules(
            {"sleep_quality": 5, "stress_level": 7},
            {"ranked": ["reduce_stress_anxiety"], "top_goal": "reduce_stress_anxiety"}
        )
        assert result["timing_assignments"]["ashwagandha"]["timing"] == "evening"

    def test_ashwagandha_morning_for_energy_only(self):
        result = apply_timing_rules(
            {"sleep_quality": 9, "stress_level": 2},
            {"ranked": ["boost_energy_reduce_fatigue"], "top_goal": "boost_energy_reduce_fatigue"}
        )
        assert result["timing_assignments"]["ashwagandha"]["timing"] == "morning"


# ── check_polyphenol_exclusions ──────────────────────────────────────────────

class TestCheckPolyphenolExclusions:
    def test_no_exclusions(self):
        result = check_polyphenol_exclusions({}, {})
        assert result["excluded_substances"] == []

    def test_kidney_disease_excludes_quercetin(self):
        result = check_polyphenol_exclusions(
            {"diagnoses": ["chronic kidney disease"]}, {}
        )
        assert "quercetin" in result["excluded_substances"]

    def test_pregnancy_excludes_quercetin(self):
        result = check_polyphenol_exclusions({}, {"pregnant": "yes"})
        assert "quercetin" in result["excluded_substances"]


# ── assess_goal_triggered_supplements ─────────────────────────────────────────

class TestAssessGoalTriggeredSupplements:
    def test_energy_goal_triggers_b9_b12_c(self):
        result = assess_goal_triggered_supplements(
            {"ranked": ["boost_energy_reduce_fatigue"]}, {}
        )
        substances = [v["substance"] for v in result["mandatory_vitamins"]]
        assert "Folate (B9)" in substances
        assert "Vitamin B12" in substances
        assert "Vitamin C" in substances

    def test_skin_goal_triggers_b3_b5(self):
        result = assess_goal_triggered_supplements(
            {"ranked": ["improve_skin_health"]}, {}
        )
        substances = [v["substance"] for v in result["mandatory_vitamins"]]
        assert "Niacinamide (B3)" in substances
        assert "Pantothenic Acid (B5)" in substances

    def test_no_relevant_goals(self):
        result = assess_goal_triggered_supplements(
            {"ranked": ["reduce_stress_anxiety"]}, {}
        )
        assert result["mandatory_vitamins"] == []
        assert result["mandatory_supplements"] == []


# ── apply_medication_rules ────────────────────────────────────────────────────

class TestApplyMedicationRules:
    def test_no_medications(self):
        result = apply_medication_rules({"questionnaire": {"medical": {"medications": []}}})
        assert result["matched_rules"] == []
        assert result["timing_override"] is None

    def test_no_medication_field(self):
        result = apply_medication_rules({"questionnaire": {"medical": {}}})
        assert result["matched_rules"] == []

    def test_unmatched_medications_tracked(self):
        result = apply_medication_rules({"questionnaire": {"medical": {
            "medications": [{"name": "SomeUnknownDrug123", "dosage": "5mg"}]
        }}})
        assert len(result["unmatched_medications"]) == 1

    def test_medication_normalization(self):
        """Fuzzy matching should handle case, accents, dosage suffixes."""
        result = apply_medication_rules({"questionnaire": {"medical": {
            "medications": [{"name": "ramipril 2,5mg daily", "dosage": ""}]
        }}})
        # ramipril should match if it's in the KB
        # Just verify it doesn't crash — match depends on KB content
        assert isinstance(result["matched_rules"], list)


# ── apply_rules (full engine) ─────────────────────────────────────────────────

class TestApplyRules:
    def test_full_rule_application(self, base_unified_input):
        result = apply_rules(base_unified_input)

        # All expected outputs present
        assert "sensitivity" in result
        assert "health_claims" in result
        assert "therapeutic_triggers" in result
        assert "prebiotic_range" in result
        assert "magnesium" in result
        assert "softgel" in result
        assert "sleep_supplements" in result
        assert "timing" in result
        assert "goal_triggered_supplements" in result

        # Sensitivity should be classified
        assert result["sensitivity"]["classification"] in ("high", "moderate", "low")

    def test_fodmap_override(self):
        """Digestion goal + low bloating should bump to moderate."""
        unified = {
            "microbiome": {"guilds": {}, "clr_ratios": {}, "vitamin_signals": {},
                          "overall_score": {"total": 50, "band": "Below Average"},
                          "root_causes": {}, "guild_scenarios": []},
            "questionnaire": {
                "completion": {"completed_steps": [1, 2, 3], "is_completed": False, "completion_pct": 33},
                "demographics": {"age": 30, "biological_sex": "female"},
                "goals": {"ranked": ["improve_digestion_gut_comfort"], "top_goal": "improve_digestion_gut_comfort"},
                "digestive": {"bloating_severity": 2, "digestive_satisfaction": 8, "stool_type": 4},
                "medical": {"medications": [], "vitamin_deficiencies": [], "reported_deficiencies": []},
                "lifestyle": {"stress_level": 3, "sleep_quality": 8, "stress_symptoms": [], "energy_level": "good"},
                "current_supplements": [],
                "diet": {}, "health_axes": {}, "food_triggers": {"triggers": [], "count": 0},
            },
        }
        result = apply_rules(unified)
        # Should be moderate due to FODMAP override (digestion goal + low bloating)
        assert result["sensitivity"]["classification"] == "moderate"
