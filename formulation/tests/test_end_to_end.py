"""
End-to-end integration test — Full pipeline chain on real + synthetic data.

Chains all 9 stages via package imports (same code path as pipeline.py)
to test the complete formulation flow in offline mode.
"""

import pytest
import json
from pathlib import Path
from formulation.stages import (
    s01_parse_inputs, s02_clinical_analysis, s03_medication_screening,
    s04_deterministic_rules, s05_formulation_decisions, s06_post_processing,
    s07_weight_calculation, s08_narratives, s09_output,
)


def _run_full_pipeline_stages(sample_dir: str):
    """Chain all 9 stages in order — mirrors pipeline.generate_formulation()."""
    ctx = s01_parse_inputs.run(sample_dir, use_llm=False, compact=True)
    if ctx is None:
        return None
    ctx = s02_clinical_analysis.run(ctx)
    ctx = s03_medication_screening.run(ctx)
    ctx = s04_deterministic_rules.run(ctx)
    ctx = s05_formulation_decisions.run(ctx)
    ctx = s06_post_processing.run(ctx)
    ctx = s07_weight_calculation.run(ctx)
    ctx = s08_narratives.run(ctx)
    master = s09_output.run(ctx)
    return master


class TestEndToEndOffline:
    """Run full pipeline on a real sample in offline mode."""

    def test_full_pipeline_produces_master(self, real_sample_dir):
        """Run all 9 stages and verify master JSON structure."""
        result = _run_full_pipeline_stages(real_sample_dir)

        assert result is not None, "Pipeline should not return None for a complete sample"

        # ── Required top-level keys ──────────────────────────────────────
        required_keys = [
            "metadata", "questionnaire_coverage", "priority_interventions",
            "input_summary", "decisions", "formulation",
            "ecological_rationale", "input_narratives",
            "clinical_summary", "medication_rules",
            "vitamin_production_disclaimer", "version",
        ]
        for key in required_keys:
            assert key in result, f"Missing top-level key: {key}"

        # ── Metadata validation ──────────────────────────────────────────
        meta = result["metadata"]
        assert meta["sample_id"] != ""
        assert meta["validation_status"] in ("PASS", "FAIL")
        assert "generated_at" in meta

        # ── Decisions structure ───────────────────────────────────────────
        decisions = result["decisions"]
        assert "mix_selection" in decisions
        assert "supplement_selection" in decisions
        assert "prebiotic_design" in decisions
        assert "rule_outputs" in decisions

        mix = decisions["mix_selection"]
        assert mix["mix_id"] in [1, 2, 3, 4, 5, 6, 8]
        assert mix["mix_name"] != ""
        assert len(mix.get("strains", [])) > 0

        # ── Formulation structure ────────────────────────────────────────
        formulation = result["formulation"]
        assert "delivery_format_1_probiotic_capsule" in formulation
        assert "delivery_format_3_powder_jar" in formulation
        assert "protocol_summary" in formulation

        proto = formulation["protocol_summary"]
        assert proto["total_daily_units"] > 0
        assert proto["total_daily_weight_g"] > 0

        # ── Probiotic capsule validation ─────────────────────────────────
        probiotic = formulation["delivery_format_1_probiotic_capsule"]
        assert probiotic["totals"]["validation"] == "PASS"
        assert probiotic["totals"]["total_cfu_billions"] >= 50

        # ── Jar validation ───────────────────────────────────────────────
        jar = formulation["delivery_format_3_powder_jar"]
        assert jar["totals"]["validation"] == "PASS"
        assert jar["totals"]["prebiotic_total_g"] > 0
        phased = jar["totals"].get("phased_dosing")
        assert phased is not None, "Phased dosing should be computed"
        assert phased["weeks_1_2_g"] < phased["weeks_3_plus_g"]

    def test_pipeline_validation_passes(self, real_sample_dir):
        """Pipeline should produce PASS validation for well-formed sample."""
        result = _run_full_pipeline_stages(real_sample_dir)
        assert result is not None
        assert result["metadata"]["validation_status"] == "PASS"

    def test_output_files_created(self, real_sample_dir):
        """Pipeline should create output files in reports directory."""
        result = _run_full_pipeline_stages(real_sample_dir)
        assert result is not None

        sample_id = Path(real_sample_dir).name
        output_dir = Path(real_sample_dir) / "reports" / "reports_json"

        expected_files = [
            f"formulation_master_{sample_id}.json",
            f"formulation_platform_{sample_id}.json",
            f"decision_trace_{sample_id}.json",
            f"manufacturing_recipe_{sample_id}.json",
            f"component_rationale_{sample_id}.json",
        ]
        for filename in expected_files:
            filepath = output_dir / filename
            assert filepath.exists(), f"Missing output file: {filename}"
            with open(filepath) as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{filename} should be a JSON object"


class TestEndToEndDysbiotic:
    """Test pipeline with synthetic dysbiotic data to verify mix selection."""

    def test_dysbiotic_selects_correct_mix(self, dysbiotic_unified_input, make_pipeline_context):
        """Dysbiotic profile should select Mix 1 or 4 (broad collapse)."""
        ctx = make_pipeline_context(unified_input=dysbiotic_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        assert ctx.mix["mix_id"] in (1, 4), f"Expected Mix 1 or 4 for broad collapse, got Mix {ctx.mix['mix_id']}"

    def test_dysbiotic_high_sensitivity(self, dysbiotic_unified_input, make_pipeline_context):
        """Dysbiotic profile with high bloating should classify as high sensitivity."""
        ctx = make_pipeline_context(unified_input=dysbiotic_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        assert ctx.rule_outputs["sensitivity"]["classification"] == "high"


class TestEndToEndBifidoDepleted:
    """Test pipeline with targeted Bifido depletion."""

    def test_bifido_depleted_selects_mix_2(self, bifido_depleted_unified_input, make_pipeline_context):
        """Single Bifido depletion should select Mix 2."""
        ctx = make_pipeline_context(unified_input=bifido_depleted_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        assert ctx.mix["mix_id"] == 2, f"Expected Mix 2 for Bifido depletion, got Mix {ctx.mix['mix_id']}"
