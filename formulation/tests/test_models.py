"""
Tests for pipeline data models — PipelineContext, RemovalLog, MedicationExclusions.
"""

import pytest
from models import PipelineContext, RemovalLog, RemovalEntry, MedicationExclusions


# ── RemovalLog ────────────────────────────────────────────────────────────────

class TestRemovalLog:
    def test_add_and_was_removed(self):
        log = RemovalLog()
        log.add("Quercetin", "Polyphenol cap exceeded", "polyphenol_cap")
        assert log.was_removed("quercetin")
        assert log.was_removed("Quercetin")
        assert not log.was_removed("Curcumin")

    def test_was_removed_partial_match(self):
        log = RemovalLog()
        log.add("vitamin b6", "B6 restricted", "vitamin_gate")
        assert log.was_removed("b6")
        assert log.was_removed("vitamin b6")

    def test_reason_for(self):
        log = RemovalLog()
        log.add("Iron", "Iron excluded for males", "vitamin_gate")
        assert log.reason_for("iron") == "Iron excluded for males"
        assert log.reason_for("zinc") is None

    def test_removed_at_stage(self):
        log = RemovalLog()
        log.add("Quercetin", "Cap exceeded", "polyphenol_cap")
        log.add("Iron", "Male exclusion", "vitamin_gate")
        log.add("Curcumin", "Cap exceeded", "polyphenol_cap")

        poly_entries = log.removed_at_stage("polyphenol_cap")
        assert len(poly_entries) == 2
        assert poly_entries[0].substance == "quercetin"
        assert poly_entries[1].substance == "curcumin"

    def test_all_removed_names(self):
        log = RemovalLog()
        log.add("Quercetin", "reason1", "stage1")
        log.add("Iron", "reason2", "stage2")
        names = log.all_removed_names()
        assert names == {"quercetin", "iron"}

    def test_empty_log(self):
        log = RemovalLog()
        assert not log.was_removed("anything")
        assert log.reason_for("anything") is None
        assert log.removed_at_stage("any") == []
        assert log.all_removed_names() == set()

    def test_backward_compat_property_sets(self):
        log = RemovalLog()
        log.add("item1", "overflow", "sachet_overflow")
        log.add("item2", "overflow", "evening_overflow")
        log.add("item3", "conflict", "mineral_conflict")
        log.add("item4", "interaction", "herb_drug_interaction")
        log.add("item5", "cap", "polyphenol_cap")

        assert "item1" in log.capacity_trimmed_names
        assert "item2" in log.evening_overflow_dropped
        assert "item3" in log.conflict_removed_names
        assert "item4" in log.interaction_removed_names
        assert "item5" in log.polyphenol_cap_dropped


# ── MedicationExclusions ─────────────────────────────────────────────────────

class TestMedicationExclusions:
    def test_defaults(self):
        med = MedicationExclusions()
        assert med.excluded_substances == set()
        assert med.exclusion_reasons == []
        assert med.timing_override is None
        assert med.substances_to_remove == set()
        assert med.magnesium_removed is False

    def test_add_exclusions(self):
        med = MedicationExclusions()
        med.excluded_substances.add("quercetin")
        med.excluded_substances.add("st. john's wort")
        assert len(med.excluded_substances) == 2


# ── PipelineContext ──────────────────────────────────────────────────────────

class TestPipelineContext:
    def test_default_initialization(self):
        ctx = PipelineContext()
        assert ctx.sample_id == ""
        assert ctx.use_llm is True
        assert ctx.compact is False
        assert ctx.unified_input == {}
        assert ctx.clinical_summary["profile_narrative"] == []
        assert ctx.removal_log.all_removed_names() == set()
        assert ctx.trace_events == []
        assert ctx.warnings == []

    def test_custom_initialization(self):
        ctx = PipelineContext(
            sample_id="test_123",
            use_llm=False,
        )
        assert ctx.sample_id == "test_123"
        assert ctx.use_llm is False

    def test_guilds_accessor(self):
        ctx = PipelineContext(unified_input={
            "microbiome": {
                "guilds": {"fiber": {"name": "Fiber"}},
                "clr_ratios": {},
            },
            "questionnaire": {},
        })
        assert ctx.guilds == {"fiber": {"name": "Fiber"}}

    def test_guilds_accessor_empty(self):
        ctx = PipelineContext()
        assert ctx.guilds == {}

    def test_clr_accessor(self):
        ctx = PipelineContext(unified_input={
            "microbiome": {
                "guilds": {},
                "clr_ratios": {"CUR": 0.5, "FCR": -0.3},
            },
            "questionnaire": {},
        })
        assert ctx.clr["CUR"] == 0.5
        assert ctx.clr["FCR"] == -0.3

    def test_questionnaire_accessor(self):
        ctx = PipelineContext(unified_input={
            "microbiome": {"guilds": {}, "clr_ratios": {}},
            "questionnaire": {"demographics": {"age": 30}},
        })
        assert ctx.questionnaire["demographics"]["age"] == 30

    def test_goals_ranked_accessor(self):
        ctx = PipelineContext(
            effective_goals={"ranked": ["goal1", "goal2"], "top_goal": "goal1"}
        )
        assert ctx.goals_ranked == ["goal1", "goal2"]

    def test_goals_ranked_empty(self):
        ctx = PipelineContext()
        assert ctx.goals_ranked == []

    def test_add_trace(self):
        ctx = PipelineContext()
        ctx.add_trace("test_event", "Zinc", "Zinc added at 8mg", source="questionnaire")
        assert len(ctx.trace_events) == 1
        event = ctx.trace_events[0]
        assert event["type"] == "test_event"
        assert event["substance"] == "Zinc"
        assert event["description"] == "Zinc added at 8mg"
        assert event["source"] == "questionnaire"

    def test_add_multiple_traces(self):
        ctx = PipelineContext()
        ctx.add_trace("event1", "A", "desc1")
        ctx.add_trace("event2", "B", "desc2")
        assert len(ctx.trace_events) == 2

    def test_removal_log_independence(self):
        """Each PipelineContext should have its own RemovalLog."""
        ctx1 = PipelineContext()
        ctx2 = PipelineContext()
        ctx1.removal_log.add("X", "reason", "stage")
        assert not ctx2.removal_log.was_removed("X")
