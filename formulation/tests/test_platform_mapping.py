"""
Tests for platform_mapping.py — Output assembly functions.
"""

import pytest
from platform_mapping import (
    build_platform_json, build_decision_trace,
    build_manufacturing_recipe, build_component_rationale,
    _evening_capsule_label,
)


# ── Helper: build a minimal master JSON ──────────────────────────────────────

def _make_master(mix_id=2, mix_name="Bifidogenic Restore", validation="PASS"):
    """Build a minimal master formulation JSON for testing."""
    return {
        "metadata": {
            "sample_id": "test_001",
            "generated_at": "2026-03-19T08:00:00Z",
            "pipeline_version": "3.0.0",
            "validation_status": validation,
            "warnings": [],
        },
        "questionnaire_coverage": {
            "completion_pct": 100,
            "coverage_level": "GOOD",
            "summary": "100% complete",
            "missing_data_areas": [],
        },
        "priority_interventions": [
            {"guild_key": "bifidobacteria", "guild_name": "Bifidobacteria",
             "priority_level": "CRITICAL", "priority_score": 10.0, "action": "Restore"},
        ],
        "input_summary": {
            "microbiome_driven": {
                "guild_status": {"bifidobacteria": "Below range"},
                "guild_details": {"bifidobacteria": {"name": "Bifidobacteria", "abundance_pct": 0.5, "status": "Below range", "priority_level": "CRITICAL", "clr": -0.8}},
                "clr_ratios": {"CUR": 0.1, "FCR": 0.2, "MDR": -0.05, "PPR": -0.2},
                "vitamin_signals": {},
                "overall_score": {"total": 55, "band": "Below Average"},
                "root_causes": {},
            },
            "questionnaire_driven": {
                "biological_sex": "female", "age": 35,
                "diet": "mixed", "goals_ranked": ["boost_energy_reduce_fatigue"],
                "stress_level": 6, "sleep_quality": 6,
                "bloating_severity": 4, "sensitivity_classification": "moderate",
                "reported_deficiencies": [],
            },
        },
        "decisions": {
            "mix_selection": {
                "mix_id": mix_id, "mix_name": mix_name,
                "primary_trigger": "Bifido depleted", "clr_context": "CLR -0.8",
                "confidence": "high", "total_cfu_billions": 50,
                "strains": [{"name": "Strain1", "cfu_billions": 50, "role": "test"}],
                "lpc37_added": False,
            },
            "supplement_selection": {
                "vitamins_minerals": [
                    {"substance": "Vitamin C", "dose": "250mg", "therapeutic": False,
                     "delivery": "morning_wellness_capsule", "informed_by": "questionnaire",
                     "rationale": "Energy support"},
                ],
                "supplements": [
                    {"substance": "Ashwagandha", "dose_mg": 600, "health_claim": "Stress",
                     "rank": "1st", "delivery": "evening_capsule", "rationale": "Stress relief"},
                ],
                "omega3": {"dose_daily_mg": 1425},
                "existing_supplements_advice": [],
            },
            "prebiotic_design": {
                "strategy": "PHGG-moderate", "total_grams": 6.0, "total_fodmap_grams": 1.5,
                "contradictions_found": [], "overrides_applied": [],
                "prebiotics": [
                    {"substance": "PHGG", "dose_g": 3.0, "fodmap": False, "rationale": "Safe base"},
                    {"substance": "GOS", "dose_g": 1.5, "fodmap": True, "rationale": "Bifido fuel"},
                    {"substance": "Beta-Glucans", "dose_g": 1.5, "fodmap": False, "rationale": "Butyrate"},
                ],
            },
            "rule_outputs": {
                "sensitivity": {"classification": "moderate", "reasoning": ["Between thresholds"]},
                "health_claims": {"supplement_claims": ["Fatigue", "Skin Quality"], "vitamin_claims": ["Fatigue"]},
                "therapeutic_triggers": {"reported_deficiencies": []},
                "prebiotic_range": {"min_g": 4, "max_g": 8},
                "magnesium": {"capsules": 2, "needs_identified": ["sleep", "stress"],
                             "mg_bisglycinate_total_mg": 1500, "elemental_mg_total_mg": 210, "reasoning": []},
                "softgel": {"include_softgel": True, "needs_identified": ["omega3"], "reasoning": []},
                "sleep_supplements": {"supplements": [], "reasoning": []},
                "goal_triggered_supplements": {},
                "timing": {
                    "timing_assignments": {
                        "magnesium": {"timing": "evening", "reason": "Always evening"},
                        "ashwagandha": {"timing": "evening", "reason": "Calming goal"},
                    },
                    "evening_capsule_needed": True,
                    "evening_components": ["Magnesium", "Ashwagandha"],
                },
            },
        },
        "formulation": {
            "metadata": {"sample_id": "test_001", "validation_status": validation, "warnings": []},
            "delivery_format_1_probiotic_capsule": {
                "format": {"type": "hard_capsule", "daily_count": 1, "timing": "morning"},
                "components": [{"substance": "Strain1", "cfu_billions": 50, "weight_mg": 500}],
                "totals": {"total_weight_mg": 500, "total_cfu_billions": 50, "validation": "PASS"},
            },
            "delivery_format_2_omega_softgels": {
                "format": {"type": "softgel", "daily_count": 2, "timing": "morning"},
                "components_per_softgel": [
                    {"substance": "Omega-3", "weight_mg_per_softgel": 712.5},
                ],
                "totals": {"weight_per_softgel_mg": 750, "daily_count": 2, "daily_total_mg": 1500, "validation": "PASS"},
            },
            "delivery_format_3_powder_jar": {
                "format": {"type": "jar", "daily_count": 1, "timing": "morning"},
                "prebiotics": {
                    "strategy": "PHGG-moderate",
                    "components": [
                        {"substance": "PHGG", "dose_g": 3.0, "weight_g": 3.0, "fodmap": False},
                        {"substance": "GOS", "dose_g": 1.5, "weight_g": 1.5, "fodmap": True},
                    ],
                },
                "botanicals": {"components": []},
                "totals": {
                    "prebiotic_total_g": 4.5, "botanical_total_g": 0,
                    "total_weight_g": 4.5, "total_fodmap_g": 1.5,
                    "within_daily_target": True, "validation": "PASS",
                    "phased_dosing": {"weeks_1_2_g": 2.25, "weeks_3_plus_g": 4.5},
                },
            },
            "protocol_summary": {
                "mix_id": mix_id, "mix_name": mix_name,
                "morning_solid_units": 4, "morning_jar_units": 1,
                "evening_solid_units": 3,
                "total_daily_units": 8, "total_daily_weight_g": 12.5,
                "morning_drinks": 1,
            },
        },
        "ecological_rationale": {},
        "input_narratives": {"microbiome_narrative": "", "questionnaire_narrative": ""},
        "component_registry": [],
        "clinical_summary": {"profile_narrative": [], "inferred_health_signals": [], "clinical_review_flags": []},
        "medication_rules": {"timing_override": None, "substances_removed": [], "magnesium_removed": False, "clinical_flags": [], "evidence_flags": []},
        "vitamin_production_disclaimer": "Test disclaimer",
        "version": 1,
    }


# ── build_platform_json ──────────────────────────────────────────────────────

class TestBuildPlatformJson:
    def test_required_keys(self):
        master = _make_master()
        platform = build_platform_json(master)

        assert "metadata" in platform
        assert "overview" in platform
        assert "synbiotic_mix" in platform
        assert "prebiotics" in platform
        assert "vitamins_minerals" in platform
        assert "supplements" in platform
        assert "delivery_units" in platform
        assert "timing" in platform

    def test_mix_data(self):
        master = _make_master(mix_id=2, mix_name="Bifidogenic Restore")
        platform = build_platform_json(master)
        assert platform["synbiotic_mix"]["mix_id"] == 2
        assert platform["synbiotic_mix"]["mix_name"] == "Bifidogenic Restore"

    def test_validation_status(self):
        master = _make_master(validation="PASS")
        platform = build_platform_json(master)
        assert platform["metadata"]["validation_status"] == "PASS"


# ── build_decision_trace ──────────────────────────────────────────────────────

class TestBuildDecisionTrace:
    def test_has_decision_chain(self):
        master = _make_master()
        trace = build_decision_trace(master)
        assert "decision_chain" in trace
        assert len(trace["decision_chain"]) >= 3

    def test_first_step_is_sensitivity(self):
        master = _make_master()
        trace = build_decision_trace(master)
        assert trace["decision_chain"][0]["decision"] == "Sensitivity Classification"

    def test_mix_selection_step(self):
        master = _make_master()
        trace = build_decision_trace(master)
        mix_step = next(s for s in trace["decision_chain"] if s["decision"] == "Mix Selection")
        assert "Mix 2" in mix_step["result"]

    def test_trace_events_integrated(self):
        master = _make_master()
        events = [{"type": "test", "substance": "X", "description": "test event"}]
        trace = build_decision_trace(master, trace_events=events)
        assert trace is not None  # Should not crash with events


# ── build_manufacturing_recipe ────────────────────────────────────────────────

class TestBuildManufacturingRecipe:
    def test_has_units(self):
        master = _make_master()
        recipe = build_manufacturing_recipe(master)
        assert len(recipe["units"]) >= 2
        assert recipe["validation"] == "PASS"

    def test_probiotic_unit(self):
        master = _make_master()
        recipe = build_manufacturing_recipe(master)
        probiotic = next(u for u in recipe["units"] if "Probiotic" in u["label"])
        assert probiotic["quantity"] == 1
        assert probiotic["total_cfu_billions"] == 50

    def test_jar_unit(self):
        master = _make_master()
        recipe = build_manufacturing_recipe(master)
        jar = next((u for u in recipe["units"] if "Jar" in u.get("label", "") or "Powder" in u.get("label", "")), None)
        assert jar is not None
        assert jar["total_weight_g"] == pytest.approx(4.5, abs=0.1)

    def test_magnesium_unit(self):
        master = _make_master()
        recipe = build_manufacturing_recipe(master)
        mg = next((u for u in recipe["units"] if "Magnesium" in u.get("label", "")), None)
        assert mg is not None
        assert mg["quantity"] == 2


# ── build_component_rationale ─────────────────────────────────────────────────

class TestBuildComponentRationale:
    def test_has_health_table(self):
        master = _make_master()
        rationale = build_component_rationale(master)
        assert "how_this_addresses_your_health" in rationale
        assert "source_attribution" in rationale
        assert "health_axis_predictions" in rationale


# ── _evening_capsule_label ────────────────────────────────────────────────────

class TestEveningCapsuleLabel:
    def test_unified_label(self):
        """Evening capsule should always return 'Evening Wellness Capsule'."""
        assert _evening_capsule_label([]) == "Evening Wellness Capsule"
        assert _evening_capsule_label([{"substance": "Ashwagandha"}]) == "Evening Wellness Capsule"
        assert _evening_capsule_label([{"substance": "Melatonin"}, {"substance": "L-Theanine"}]) == "Evening Wellness Capsule"
