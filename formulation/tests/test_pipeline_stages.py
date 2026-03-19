"""
Integration tests for individual pipeline stages (s01-s09).

Tests each stage in isolation using synthetic data, verifying
PipelineContext is correctly populated at each step.
"""

import pytest
from copy import deepcopy
from formulation.models import PipelineContext, MedicationExclusions
from rules_engine import apply_rules

# Stage imports use package-qualified paths (stages use relative imports)
from formulation.stages import s01_parse_inputs, s02_clinical_analysis
from formulation.stages import s03_medication_screening, s04_deterministic_rules
from formulation.stages import s05_formulation_decisions, s06_post_processing
from formulation.stages import s07_weight_calculation, s08_narratives


# ── Stage 1: Parse Inputs ────────────────────────────────────────────────────

class TestStage1ParseInputs:
    def test_creates_pipeline_context(self, real_sample_dir):
        ctx = s01_parse_inputs.run(real_sample_dir, use_llm=False)
        assert ctx is not None
        assert isinstance(ctx, PipelineContext)
        assert ctx.sample_id != ""
        assert ctx.unified_input != {}
        assert ctx.use_llm is False

    def test_questionnaire_guard(self, tmp_path):
        """Sample with no questionnaire should return None."""
        sample_dir = tmp_path / "1234567890123"
        sample_dir.mkdir()
        result = s01_parse_inputs.run(str(sample_dir), use_llm=False)
        assert result is None


# ── Stage 2: Clinical Analysis ────────────────────────────────────────────────

class TestStage2ClinicalAnalysis:
    def test_offline_mode(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s02_clinical_analysis.run(ctx)
        # Offline mode should leave clinical_summary with defaults
        assert isinstance(ctx.clinical_summary, dict)
        assert "profile_narrative" in ctx.clinical_summary


# ── Stage 3: Medication Screening ─────────────────────────────────────────────

class TestStage3MedicationScreening:
    def test_no_medications(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s03_medication_screening.run(ctx)
        assert isinstance(ctx.medication, MedicationExclusions)
        assert ctx.medication.timing_override is None

    def test_with_medications(self, medication_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=medication_unified_input, use_llm=False)
        ctx = s03_medication_screening.run(ctx)
        assert isinstance(ctx.medication, MedicationExclusions)
        # At minimum, KB rules should have been applied
        assert isinstance(ctx.medication.matched_rules, list)


# ── Stage 4: Deterministic Rules ──────────────────────────────────────────────

class TestStage4DeterministicRules:
    def test_rules_applied(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input)
        ctx = s04_deterministic_rules.run(ctx)

        assert "sensitivity" in ctx.rule_outputs
        assert "health_claims" in ctx.rule_outputs
        assert "timing" in ctx.rule_outputs
        assert ctx.rule_outputs["sensitivity"]["classification"] in ("high", "moderate", "low")
        assert ctx.effective_goals["ranked"]  # Should have goals

    def test_inferred_signals_merged(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input)
        ctx.clinical_summary = {
            "profile_narrative": [],
            "inferred_health_signals": [
                {"signal": "infection_susceptibility", "reason": "UTI 1-2x/year"}
            ],
            "clinical_review_flags": [],
        }
        ctx = s04_deterministic_rules.run(ctx)
        # Inferred signal should be merged into effective goals
        claims = ctx.rule_outputs["health_claims"]["supplement_claims"]
        assert isinstance(claims, list)


# ── Stage 5: Formulation Decisions ────────────────────────────────────────────

class TestStage5FormulationDecisions:
    def test_offline_decisions(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)

        assert ctx.mix.get("mix_id") is not None
        assert isinstance(ctx.supplements, dict)
        assert isinstance(ctx.prebiotics, dict)
        assert ctx.prebiotics.get("total_grams", 0) > 0

    def test_strains_populated(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)

        assert len(ctx.mix.get("strains", [])) > 0


# ── Stage 6: Post-Processing ─────────────────────────────────────────────────

class TestStage6PostProcessing:
    def test_post_processing_runs(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        ctx = s06_post_processing.run(ctx)

        # Timing should have been re-applied
        assert "timing" in ctx.rule_outputs


# ── Stage 7: Weight Calculation ───────────────────────────────────────────────

class TestStage7WeightCalculation:
    def test_weight_calculation(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        ctx = s06_post_processing.run(ctx)
        ctx = s07_weight_calculation.run(ctx)

        assert ctx.formulation != {}
        assert ctx.formulation["metadata"]["validation_status"] in ("PASS", "FAIL")
        assert ctx.formulation["protocol_summary"]["total_daily_units"] > 0
        assert ctx.formulation["protocol_summary"]["total_daily_weight_g"] > 0

    def test_validation_passes(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        ctx = s06_post_processing.run(ctx)
        ctx = s07_weight_calculation.run(ctx)

        assert ctx.formulation["metadata"]["validation_status"] == "PASS"


# ── Stage 8: Narratives ──────────────────────────────────────────────────────

class TestStage8Narratives:
    def test_offline_narratives(self, base_unified_input, make_pipeline_context):
        ctx = make_pipeline_context(unified_input=base_unified_input, use_llm=False)
        ctx = s04_deterministic_rules.run(ctx)
        ctx = s05_formulation_decisions.run(ctx)
        ctx = s06_post_processing.run(ctx)
        ctx = s07_weight_calculation.run(ctx)
        ctx = s08_narratives.run(ctx)

        # KB fallback should provide ecological rationale for mixes with alternatives
        assert isinstance(ctx.ecological_rationale, dict)
        assert isinstance(ctx.input_narratives, dict)
