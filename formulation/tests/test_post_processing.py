"""
Tests for Stage 6 post-processing filters (s06_post_processing.py).
"""

import pytest
from copy import deepcopy
from formulation.models import PipelineContext, MedicationExclusions
from formulation.stages.s06_post_processing import (
    _apply_medication_exclusions, _apply_excluded_substance_filter,
    _apply_vitamin_gate, _apply_delivery_routing, _apply_piperine_addition,
    _apply_fodmap_correction, _apply_zinc_dose_guard,
)


# ── Helper to create a context with supplements ──────────────────────────────

def _make_ctx(vitamins=None, supplements=None, prebiotics=None,
              excluded_substances=None, unified_input=None):
    """Create PipelineContext with specified supplements."""
    ctx = PipelineContext(
        sample_id="test",
        sample_dir="/tmp/test",
        use_llm=False,
        compact=True,
        unified_input=unified_input or {
            "microbiome": {"guilds": {}, "clr_ratios": {}},
            "questionnaire": {
                "demographics": {"biological_sex": "female"},
                "medical": {"diagnoses": []},
                "lifestyle": {"stress_level": 5, "sleep_quality": 6},
            },
        },
    )
    ctx.supplements = {
        "vitamins_minerals": vitamins or [],
        "supplements": supplements or [],
    }
    ctx.prebiotics = {
        "prebiotics": prebiotics or [],
        "total_grams": sum(p.get("dose_g", 0) for p in (prebiotics or [])),
    }
    ctx.rule_outputs = {
        "timing": {"timing_assignments": {}},
        "therapeutic_triggers": {"reported_deficiencies": []},
        "health_claims": {"supplement_claims": [], "microbiome_vitamin_needs": []},
    }
    ctx.effective_goals = {"ranked": [], "top_goal": ""}
    if excluded_substances:
        ctx.medication = MedicationExclusions(excluded_substances=excluded_substances)
    return ctx


# ── Medication exclusion filter ──────────────────────────────────────────────

class TestMedicationExclusions:
    def test_removes_excluded_supplements(self):
        """Test the filter function directly."""
        ctx = _make_ctx(
            supplements=[
                {"substance": "St. John's Wort", "dose_mg": 300},
                {"substance": "Ashwagandha", "dose_mg": 600},
            ],
            excluded_substances={"st. john's wort"}
        )
        _apply_medication_exclusions(ctx)
        names = [s["substance"] for s in ctx.supplements["supplements"]]
        assert "St. John's Wort" not in names
        assert "Ashwagandha" in names
        assert ctx.removal_log.was_removed("st. john's wort")

    def test_no_exclusions_no_change(self):
        ctx = _make_ctx(
            supplements=[{"substance": "Ashwagandha", "dose_mg": 600}],
        )
        _apply_medication_exclusions(ctx)
        assert len(ctx.supplements["supplements"]) == 1


# ── Excluded substance filter ────────────────────────────────────────────────

class TestExcludedSubstanceFilter:
    def test_removes_magnesium_from_vitamins(self):
        ctx = _make_ctx(
            vitamins=[
                {"substance": "Magnesium", "dose_value": 200},
                {"substance": "Zinc", "dose_value": 8},
            ]
        )
        _apply_excluded_substance_filter(ctx)
        names = [v["substance"] for v in ctx.supplements["vitamins_minerals"]]
        assert "Magnesium" not in names
        assert "Zinc" in names

    def test_removes_melatonin_from_supplements(self):
        ctx = _make_ctx(
            supplements=[
                {"substance": "Melatonin", "dose_mg": 3},
                {"substance": "Curcumin", "dose_mg": 500},
            ]
        )
        _apply_excluded_substance_filter(ctx)
        names = [s["substance"] for s in ctx.supplements["supplements"]]
        assert "Melatonin" not in names
        assert "Curcumin" in names

    def test_removes_fiber_supplements(self):
        ctx = _make_ctx(
            supplements=[
                {"substance": "PHGG", "dose_mg": 3000},
                {"substance": "Ashwagandha", "dose_mg": 600},
            ]
        )
        _apply_excluded_substance_filter(ctx)
        names = [s["substance"] for s in ctx.supplements["supplements"]]
        assert "PHGG" not in names
        assert "Ashwagandha" in names


# ── Vitamin gate ──────────────────────────────────────────────────────────────

class TestVitaminGate:
    def test_iron_excluded_for_males(self):
        ui = {
            "microbiome": {"guilds": {}, "clr_ratios": {}},
            "questionnaire": {
                "demographics": {"biological_sex": "male"},
                "medical": {},
            },
        }
        ctx = _make_ctx(
            vitamins=[
                {"substance": "Iron", "dose_value": 18},
                {"substance": "Zinc", "dose_value": 11},
            ],
            unified_input=ui,
        )
        ctx.rule_outputs["therapeutic_triggers"] = {"reported_deficiencies": []}
        ctx.rule_outputs["health_claims"] = {"microbiome_vitamin_needs": []}
        _apply_vitamin_gate(ctx)
        names = [v["substance"] for v in ctx.supplements["vitamins_minerals"]]
        assert "Iron" not in names
        assert "Zinc" in names

    def test_iron_kept_for_females(self):
        ctx = _make_ctx(
            vitamins=[{"substance": "Iron", "dose_value": 18}]
        )
        ctx.rule_outputs["therapeutic_triggers"] = {"reported_deficiencies": []}
        ctx.rule_outputs["health_claims"] = {"microbiome_vitamin_needs": []}
        _apply_vitamin_gate(ctx)
        names = [v["substance"] for v in ctx.supplements["vitamins_minerals"]]
        assert "Iron" in names


# ── Delivery routing ──────────────────────────────────────────────────────────

class TestDeliveryRouting:
    def test_fat_soluble_to_softgel(self):
        ctx = _make_ctx(
            vitamins=[
                {"substance": "Vitamin A", "delivery": "morning_wellness_capsule"},
                {"substance": "Vitamin C", "delivery": "morning_wellness_capsule"},
            ]
        )
        _apply_delivery_routing(ctx)
        vit_a = next(v for v in ctx.supplements["vitamins_minerals"] if v["substance"] == "Vitamin A")
        vit_c = next(v for v in ctx.supplements["vitamins_minerals"] if v["substance"] == "Vitamin C")
        assert vit_a["delivery"] == "softgel"
        assert vit_c["delivery"] == "morning_wellness_capsule"


# ── Piperine addition ────────────────────────────────────────────────────────

class TestPiperineAddition:
    def test_piperine_auto_added(self):
        ctx = _make_ctx(
            supplements=[{"substance": "Curcumin", "dose_mg": 500}]
        )
        _apply_piperine_addition(ctx)
        curcumin = ctx.supplements["supplements"][0]
        assert "Piperine" in curcumin["substance"]
        assert curcumin["dose_mg"] == 505  # 500 + 5
        assert ctx.piperine_applied is True

    def test_no_curcumin_no_piperine(self):
        ctx = _make_ctx(
            supplements=[{"substance": "Ashwagandha", "dose_mg": 600}]
        )
        _apply_piperine_addition(ctx)
        assert ctx.piperine_applied is False


# ── FODMAP correction ─────────────────────────────────────────────────────────

class TestFODMAPCorrection:
    def test_lactulose_flagged(self):
        ctx = _make_ctx(
            prebiotics=[
                {"substance": "Lactulose", "dose_g": 2.0, "fodmap": False},
                {"substance": "PHGG", "dose_g": 3.0, "fodmap": False},
            ]
        )
        _apply_fodmap_correction(ctx)
        lactulose = next(p for p in ctx.prebiotics["prebiotics"] if p["substance"] == "Lactulose")
        assert lactulose["fodmap"] is True
        phgg = next(p for p in ctx.prebiotics["prebiotics"] if p["substance"] == "PHGG")
        assert phgg["fodmap"] is False


# ── Zinc dose guard ──────────────────────────────────────────────────────────

class TestZincDoseGuard:
    def test_unknown_sex_clamps_zinc(self):
        ui = {
            "microbiome": {"guilds": {}, "clr_ratios": {}},
            "questionnaire": {"demographics": {"biological_sex": ""}},
        }
        ctx = _make_ctx(
            vitamins=[{"substance": "Zinc", "dose_value": 11, "dose": "11 mg/d"}],
            unified_input=ui,
        )
        _apply_zinc_dose_guard(ctx)
        zinc = ctx.supplements["vitamins_minerals"][0]
        assert zinc["dose_value"] == 8

    def test_known_sex_keeps_zinc(self):
        ctx = _make_ctx(
            vitamins=[{"substance": "Zinc", "dose_value": 11, "dose": "11 mg/d"}]
        )
        _apply_zinc_dose_guard(ctx)
        zinc = ctx.supplements["vitamins_minerals"][0]
        assert zinc["dose_value"] == 11
