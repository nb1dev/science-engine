"""
Regression tests — Compare stage-chain output against existing formulations.

Verifies that the modular pipeline produces structurally equivalent output
to the previously generated formulation master JSONs.
"""

import pytest
import json
from pathlib import Path
from formulation.stages import (
    s01_parse_inputs, s02_clinical_analysis, s03_medication_screening,
    s04_deterministic_rules, s05_formulation_decisions, s06_post_processing,
    s07_weight_calculation, s08_narratives, s09_output,
)


REAL_SAMPLE_DIR = Path(__file__).parent.parent.parent.parent / "analysis" / "nb1_2026_003" / "1421106528699"


def _run_full_pipeline_stages(sample_dir: str):
    """Chain all 9 stages — mirrors pipeline.generate_formulation()."""
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
    return s09_output.run(ctx)


class TestRegressionAgainstExisting:
    """Compare modular pipeline output against previously saved formulation."""

    @pytest.fixture
    def existing_master(self):
        """Load previously generated master JSON."""
        master_path = REAL_SAMPLE_DIR / "reports" / "reports_json" / "formulation_master_1421106528699.json"
        if not master_path.exists():
            pytest.skip("No existing formulation master found for regression comparison")
        with open(master_path, 'r') as f:
            return json.load(f)

    def test_mix_selection_matches(self, existing_master, real_sample_dir):
        """Mix selection should be identical (deterministic)."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        old_mix = existing_master["decisions"]["mix_selection"]["mix_id"]
        new_mix = new_result["decisions"]["mix_selection"]["mix_id"]
        assert new_mix == old_mix, f"Mix mismatch: old={old_mix}, new={new_mix}"

    def test_sensitivity_matches(self, existing_master, real_sample_dir):
        """Sensitivity classification should be identical (deterministic)."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        old_sens = existing_master["decisions"]["rule_outputs"]["sensitivity"]["classification"]
        new_sens = new_result["decisions"]["rule_outputs"]["sensitivity"]["classification"]
        assert new_sens == old_sens, f"Sensitivity mismatch: old={old_sens}, new={new_sens}"

    def test_magnesium_capsules_match(self, existing_master, real_sample_dir):
        """Magnesium capsule count should be identical (deterministic)."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        old_mg = existing_master["decisions"]["rule_outputs"]["magnesium"]["capsules"]
        new_mg = new_result["decisions"]["rule_outputs"]["magnesium"]["capsules"]
        assert new_mg == old_mg, f"Mg capsules mismatch: old={old_mg}, new={new_mg}"

    def test_prebiotic_range_matches(self, existing_master, real_sample_dir):
        """Prebiotic dose range should be identical (deterministic)."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        old_range = existing_master["decisions"]["rule_outputs"]["prebiotic_range"]
        new_range = new_result["decisions"]["rule_outputs"]["prebiotic_range"]
        assert new_range["min_g"] == old_range["min_g"]
        assert new_range["max_g"] == old_range["max_g"]

    def test_structural_completeness(self, existing_master, real_sample_dir):
        """New output should have all the same top-level keys as old."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        for key in existing_master:
            assert key in new_result, f"Missing key in new output: {key}"

    def test_validation_status_matches(self, existing_master, real_sample_dir):
        """Validation status should be identical."""
        new_result = _run_full_pipeline_stages(real_sample_dir)
        assert new_result is not None
        old_status = existing_master["metadata"]["validation_status"]
        new_status = new_result["metadata"]["validation_status"]
        assert new_status == old_status, f"Validation mismatch: old={old_status}, new={new_status}"


class TestDeterministicConsistency:
    """Verify deterministic components produce identical results across runs."""

    def test_mix_selection_deterministic(self, base_unified_input):
        from llm_decisions import select_mix_offline
        rule_outputs = {"sensitivity": {"classification": "moderate"}}
        result1 = select_mix_offline(base_unified_input, rule_outputs)
        result2 = select_mix_offline(base_unified_input, rule_outputs)
        assert result1["mix_id"] == result2["mix_id"]
        assert result1["total_cfu_billions"] == result2["total_cfu_billions"]

    def test_rules_deterministic(self, base_unified_input):
        from rules_engine import apply_rules
        result1 = apply_rules(base_unified_input)
        result2 = apply_rules(base_unified_input)
        assert result1["sensitivity"]["classification"] == result2["sensitivity"]["classification"]
        assert result1["magnesium"]["capsules"] == result2["magnesium"]["capsules"]

    def test_prebiotic_design_deterministic(self, base_unified_input):
        from llm_decisions import design_prebiotics_offline
        rule_outputs = {
            "sensitivity": {"classification": "moderate", "max_prebiotic_g": 10},
            "prebiotic_range": {"min_g": 4, "max_g": 8, "cfu_tier": "50B"},
        }
        mix = {"mix_id": 6, "mix_name": "Maintenance Gold Standard"}
        r1 = design_prebiotics_offline(base_unified_input, rule_outputs, mix)
        r2 = design_prebiotics_offline(base_unified_input, rule_outputs, mix)
        assert r1["total_grams"] == r2["total_grams"]
