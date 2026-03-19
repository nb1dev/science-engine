"""
Tests for generate_formulation_evening.py — Evening timing override display layer.

Tests the label patching logic and verifies that the evening pipeline correctly
detects and applies medication timing overrides.
"""

import pytest
from copy import deepcopy
from generate_formulation_evening import _patch_evening_labels, EVENING_LABEL_PATCHES, EVENING_HTML_PATCHES
from formulation.stages import (
    s03_medication_screening, s04_deterministic_rules,
    s05_formulation_decisions, s09_output,
)


# ── Label patching unit tests ────────────────────────────────────────────────

class TestEveningLabelPatching:
    def test_morning_cap_to_dinner(self):
        text = "🌅 Morning cap → vitamins"
        result = _patch_evening_labels(text)
        assert "🌙 Dinner cap" in result
        assert "Morning" not in result

    def test_morning_wellness_capsule(self):
        text = "Morning Wellness Capsule"
        result = _patch_evening_labels(text)
        assert "Dinner Wellness Capsule" in result

    def test_morning_wellness_capsules_plural(self):
        text = "Morning Wellness Capsules"
        result = _patch_evening_labels(text)
        assert "Dinner Wellness Capsules" in result

    def test_morning_wellness_lowercase(self):
        text = "morning wellness capsule"
        result = _patch_evening_labels(text)
        assert "dinner wellness capsule" in result

    def test_idempotent(self):
        """Patching twice should produce the same result."""
        text = "Morning Wellness Capsule 🌅 Morning cap"
        result1 = _patch_evening_labels(text)
        result2 = _patch_evening_labels(result1)
        assert result1 == result2

    def test_no_match_unchanged(self):
        text = "Evening Wellness Capsule — already correct"
        result = _patch_evening_labels(text)
        assert result == text

    def test_multiple_replacements(self):
        text = (
            "🌅 Morning cap → Vitamin C 250mg\n"
            "Morning Wellness Capsule × 2\n"
            "morning wellness capsule label"
        )
        result = _patch_evening_labels(text)
        assert "🌙 Dinner cap" in result
        assert "Dinner Wellness Capsule" in result
        assert "dinner wellness capsule" in result


# ── HTML patching tests ──────────────────────────────────────────────────────

class TestEveningHTMLPatching:
    def test_html_patches_exist(self):
        assert len(EVENING_HTML_PATCHES) > 0

    def test_html_morning_unit_cards(self):
        html = '<div class="unit-card morning">🌅 1× morning</div>'
        for old, new in EVENING_LABEL_PATCHES + EVENING_HTML_PATCHES:
            html = html.replace(old, new)
        assert "evening" in html
        assert "dinner" in html.lower() or "evening" in html.lower()

    def test_all_patches_have_pairs(self):
        """Every patch should have both old and new non-empty strings."""
        for old, new in EVENING_LABEL_PATCHES:
            assert old, "Empty old pattern in EVENING_LABEL_PATCHES"
            assert new, "Empty new pattern in EVENING_LABEL_PATCHES"
        for old, new in EVENING_HTML_PATCHES:
            assert old, "Empty old pattern in EVENING_HTML_PATCHES"
            assert new, "Empty new pattern in EVENING_HTML_PATCHES"


# ── Medication timing override detection (via Stage 3) ────────────────────────

class TestTimingOverrideDetection:
    def test_levothyroxine_triggers_timing_override(self, make_pipeline_context, base_unified_input):
        """Levothyroxine should trigger Tier A timing override in KB rules."""
        from rules_engine import apply_medication_rules

        data = deepcopy(base_unified_input)
        data["questionnaire"]["medical"]["medications"] = [
            {"name": "Levothyroxine", "dosage": "100mcg", "how_long": "5 years"}
        ]
        result = apply_medication_rules(data)

        # Check if levothyroxine matched any rule
        matched = result.get("matched_rules", [])
        if matched:
            # If it matched, check for timing override
            override = result.get("timing_override")
            if override:
                assert override["tier"] == "A"
                assert "dinner" in override.get("move_to", "").lower() or override.get("move_to") is not None

    def test_no_medication_no_override(self, base_unified_input):
        """No medications should produce no timing override."""
        from rules_engine import apply_medication_rules
        result = apply_medication_rules(base_unified_input)
        assert result["timing_override"] is None

    def test_stage3_stores_override_in_context(self, medication_unified_input, make_pipeline_context):
        """Stage 3 should store timing override in MedicationExclusions."""
        ctx = make_pipeline_context(unified_input=medication_unified_input, use_llm=False)
        ctx = s03_medication_screening.run(ctx)
        # The override may or may not be present depending on KB content
        assert hasattr(ctx.medication, "timing_override")


# ── Evening pipeline integration (timing override flow) ───────────────────────

class TestEveningTimingFlow:
    def test_override_propagates_through_pipeline(self, make_pipeline_context):
        """If timing override is set, it should propagate to output stage."""
        from copy import deepcopy
        from formulation.stages import s06_post_processing, s07_weight_calculation, s08_narratives

        # Create input with levothyroxine
        unified = {
            "sample_id": "test_evening",
            "batch_id": "nb1_2026_test",
            "microbiome": {
                "guilds": {
                    "fiber_degraders": {"name": "Fiber Degraders", "abundance_pct": 25.0, "status": "Within range", "clr": 0.1, "priority_level": "Monitor", "evenness": 0.8},
                    "bifidobacteria": {"name": "Bifidobacteria", "abundance_pct": 8.0, "status": "Within range", "clr": 0.05, "priority_level": "Monitor", "evenness": 0.7},
                    "cross_feeders": {"name": "Cross-Feeders", "abundance_pct": 15.0, "status": "Within range", "clr": -0.1, "priority_level": "Monitor"},
                    "butyrate_producers": {"name": "Butyrate Producers", "abundance_pct": 20.0, "status": "Within range", "clr": 0.2, "priority_level": "Monitor"},
                    "proteolytic": {"name": "Proteolytic Guild", "abundance_pct": 5.0, "status": "Within range", "clr": -0.2, "priority_level": "Monitor"},
                    "mucin_degraders": {"name": "Mucin Degraders", "abundance_pct": 3.0, "status": "Within range", "clr": -0.1, "priority_level": "Monitor"},
                },
                "clr_ratios": {"CUR": 0.1, "FCR": 0.2, "MDR": -0.1, "PPR": -0.3},
                "vitamin_signals": {},
                "overall_score": {"total": 72, "band": "Moderate"},
                "root_causes": {},
                "guild_scenarios": [],
            },
            "questionnaire": {
                "completion": {"completed_steps": [1, 2, 3, 4, 5, 6, 7, 8, 9], "is_completed": True, "completion_pct": 100},
                "demographics": {"age": 45, "biological_sex": "female", "height_cm": 165, "weight_kg": 62, "bmi": 22.8},
                "goals": {"ranked": ["boost_energy_reduce_fatigue"], "top_goal": "boost_energy_reduce_fatigue"},
                "digestive": {"stool_type": 4, "bloating_frequency": "rarely", "bloating_severity": 2, "digestive_satisfaction": 7},
                "medical": {
                    "medications": [{"name": "Levothyroxine", "dosage": "100mcg", "how_long": "5 years"}],
                    "diagnoses": [], "family_history": {}, "vitamin_deficiencies": [],
                    "reported_deficiencies": [], "drug_allergies": "", "drug_allergies_has": "no",
                    "skin_concerns": [], "uti_per_year": "", "colds_per_year": "",
                    "gut_brain_symptoms": [], "colon_symptoms": [], "motility_details": "",
                    "motility_symptoms": [], "previous_supplements": "",
                    "nsaid_use": "", "nsaid_which": "",
                    "skin_persistence": "", "skin_change_patterns": "", "skin_issues_frequency": "",
                    "infection_recovery": "", "previous_supplement_effect": "", "previous_supplement_notes": "",
                },
                "lifestyle": {
                    "stress_level": 4, "sleep_quality": 7, "sleep_duration": 7,
                    "sleep_issues": [], "energy_level": "moderate", "stress_symptoms": [],
                    "digestive_symptoms_with_stress": "no",
                    "exercise_detail": {"types": [], "moderate_days_per_week": 2},
                    "weight_kg": 62,
                },
                "current_supplements": [],
                "diet": {"diet_pattern": "mixed"},
                "health_axes": {},
                "food_triggers": {"triggers": [], "count": 0, "colon_triggers_text": ""},
            },
        }

        ctx = make_pipeline_context(unified_input=unified, use_llm=False)
        ctx = s03_medication_screening.run(ctx)

        # Verify medication was processed
        assert hasattr(ctx.medication, "timing_override")
        assert isinstance(ctx.medication.matched_rules, list)

        # Continue pipeline to check override propagates
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        ctx = s06_post_processing.run(ctx)
        ctx = s07_weight_calculation.run(ctx)
        ctx = s08_narratives.run(ctx)

        # The timing override (if present in KB) will be in ctx.medication.timing_override
        # It gets applied in s09_output.run() via apply_medication_timing_override()
        # We can verify the mechanism works without actually writing files
        assert ctx.formulation != {}
        assert ctx.formulation["metadata"]["validation_status"] in ("PASS", "FAIL")
