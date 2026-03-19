"""
Tests for llm_decisions.py — Offline mix selection, prebiotic design, strain lookup.
"""

import pytest
from copy import deepcopy
from llm_decisions import (
    select_mix_offline, design_prebiotics_offline, lookup_strains_for_mix,
    run_llm_decisions, _should_add_lp815,
)


# ── _should_add_lp815 ────────────────────────────────────────────────────────

class TestLP815:
    def test_high_stress_always_adds(self):
        assert _should_add_lp815(7, []) is True

    def test_moderate_stress_with_mood_goal(self):
        assert _should_add_lp815(5, ["improve_mood_reduce_anxiety"]) is True

    def test_low_stress_no_goal(self):
        assert _should_add_lp815(3, []) is False

    def test_none_stress(self):
        assert _should_add_lp815(None, []) is False


# ── select_mix_offline — Branch A (broad collapse) ───────────────────────────

class TestSelectMixOfflineBranchA:
    def test_broad_collapse_with_proteolytic_protein_driven(self, dysbiotic_unified_input):
        """≥3 compromised + proteolytic 1A + PPR > 0 → Mix 4."""
        result = select_mix_offline(dysbiotic_unified_input, {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 4
        assert result["confidence"] == "high"

    def test_broad_collapse_proteolytic_not_protein_driven(self, dysbiotic_unified_input):
        """≥3 compromised + proteolytic 1A + PPR ≤ 0 → Mix 1."""
        data = deepcopy(dysbiotic_unified_input)
        data["microbiome"]["clr_ratios"]["PPR"] = -0.2
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 1

    def test_broad_collapse_pure(self, base_unified_input):
        """≥3 beneficial compromised, no proteolytic overgrowth → Mix 1."""
        data = deepcopy(base_unified_input)
        guilds = data["microbiome"]["guilds"]
        # Make 3 beneficial guilds compromised
        guilds["fiber_degraders"]["abundance_pct"] = 3.0
        guilds["fiber_degraders"]["status"] = "Below range"
        guilds["fiber_degraders"]["clr"] = -0.6
        guilds["bifidobacteria"]["abundance_pct"] = 0.5
        guilds["bifidobacteria"]["status"] = "Below range"
        guilds["bifidobacteria"]["clr"] = -0.8
        guilds["butyrate_producers"]["abundance_pct"] = 4.0
        guilds["butyrate_producers"]["status"] = "Below range"
        guilds["butyrate_producers"]["clr"] = -0.5
        # Proteolytic normal
        guilds["proteolytic"]["abundance_pct"] = 5.0
        guilds["proteolytic"]["status"] = "Within range"
        guilds["proteolytic"]["clr"] = -0.1
        data["microbiome"]["clr_ratios"]["PPR"] = -0.3
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 1


# ── select_mix_offline — Branch B (targeted) ─────────────────────────────────

class TestSelectMixOfflineBranchB:
    def test_bifido_depleted_only(self, bifido_depleted_unified_input):
        """Only Bifido compromised → Mix 2."""
        result = select_mix_offline(bifido_depleted_unified_input,
                                     {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 2
        assert "Bifidobacteria" in result["primary_trigger"] or "Bifido" in result["primary_trigger"]

    def test_fiber_substrate_limited(self, base_unified_input):
        """Fiber below range with CLR > -0.3 → Mix 3."""
        data = deepcopy(base_unified_input)
        guilds = data["microbiome"]["guilds"]
        guilds["fiber_degraders"]["abundance_pct"] = 8.0
        guilds["fiber_degraders"]["status"] = "Below range"
        guilds["fiber_degraders"]["clr"] = 0.1
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] in (2, 3)  # Could be 2 if bifido also scores high


# ── select_mix_offline — Branch C (healthy/contextual) ───────────────────────

class TestSelectMixOfflineBranchC:
    def test_all_healthy_maintenance(self, base_unified_input):
        """All guilds at Monitor → Mix 6."""
        result = select_mix_offline(base_unified_input,
                                     {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 6
        assert result["confidence"] == "high"

    def test_proteolytic_overgrowth_only(self, base_unified_input):
        """Only proteolytic overgrown → Mix 4."""
        data = deepcopy(base_unified_input)
        guilds = data["microbiome"]["guilds"]
        guilds["proteolytic"]["abundance_pct"] = 30.0
        guilds["proteolytic"]["status"] = "Above range"
        guilds["proteolytic"]["clr"] = 1.2
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["mix_id"] == 4


# ── LP815 integration ─────────────────────────────────────────────────────────

class TestLP815Integration:
    def test_lp815_added_high_stress(self, base_unified_input):
        data = deepcopy(base_unified_input)
        data["questionnaire"]["lifestyle"]["stress_level"] = 7
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["lp815_added"] is True
        assert result["total_cfu_billions"] == 55

    def test_lp815_not_added_low_stress(self, base_unified_input):
        data = deepcopy(base_unified_input)
        data["questionnaire"]["lifestyle"]["stress_level"] = 3
        data["questionnaire"]["goals"]["ranked"] = ["improve_skin_health"]
        result = select_mix_offline(data, {"sensitivity": {"classification": "moderate"}})
        assert result["lp815_added"] is False
        assert result["total_cfu_billions"] == 50


# ── lookup_strains_for_mix ────────────────────────────────────────────────────

class TestLookupStrains:
    def test_mix_1_has_strains(self):
        strains = lookup_strains_for_mix(1)
        assert len(strains) > 0

    def test_mix_2_has_strains(self):
        strains = lookup_strains_for_mix(2)
        assert len(strains) > 0

    def test_all_mixes_have_strains(self):
        for mix_id in [1, 2, 3, 4, 5, 6, 8]:
            strains = lookup_strains_for_mix(mix_id)
            assert len(strains) > 0, f"Mix {mix_id} has no strains in KB"

    def test_invalid_mix_returns_empty(self):
        strains = lookup_strains_for_mix(99)
        assert strains == []


# ── design_prebiotics_offline ─────────────────────────────────────────────────

class TestDesignPrebioticsOffline:
    def test_basic_design(self, base_unified_input):
        rule_outputs = {
            "sensitivity": {"classification": "moderate", "max_prebiotic_g": 10},
            "prebiotic_range": {"min_g": 4, "max_g": 8, "cfu_tier": "50B"},
        }
        mix = {"mix_id": 2, "mix_name": "Bifidogenic Restore"}
        result = design_prebiotics_offline(base_unified_input, rule_outputs, mix)

        assert result["total_grams"] > 0
        assert result["total_grams"] <= 8
        assert len(result["prebiotics"]) > 0

    def test_high_sensitivity_clamp(self, base_unified_input):
        rule_outputs = {
            "sensitivity": {"classification": "high", "max_prebiotic_g": 6},
            "prebiotic_range": {"min_g": 3, "max_g": 6, "cfu_tier": "50B"},
        }
        data = deepcopy(base_unified_input)
        data["questionnaire"]["digestive"]["bloating_severity"] = 8
        mix = {"mix_id": 2, "mix_name": "Bifidogenic Restore"}
        result = design_prebiotics_offline(data, rule_outputs, mix)
        assert result["total_grams"] <= 6

    def test_phased_dosing_computed(self, base_unified_input):
        rule_outputs = {
            "sensitivity": {"classification": "moderate", "max_prebiotic_g": 10},
            "prebiotic_range": {"min_g": 4, "max_g": 8, "cfu_tier": "50B"},
        }
        mix = {"mix_id": 2, "mix_name": "Bifidogenic Restore"}
        result = design_prebiotics_offline(base_unified_input, rule_outputs, mix)
        assert "phased_dosing" in result
        assert result["phased_dosing"]["weeks_1_2_g"] == pytest.approx(result["total_grams"] * 0.5, abs=0.1)


# ── run_llm_decisions (offline mode) ──────────────────────────────────────────

class TestRunLLMDecisionsOffline:
    def test_offline_returns_all_sections(self, base_unified_input):
        from rules_engine import apply_rules
        rule_outputs = apply_rules(base_unified_input)
        result = run_llm_decisions(base_unified_input, rule_outputs, use_bedrock=False)

        assert "mix_selection" in result
        assert "supplement_selection" in result
        assert "prebiotic_design" in result
        assert result["mix_selection"]["mix_id"] is not None
        assert isinstance(result["supplement_selection"], dict)
        assert result["prebiotic_design"]["total_grams"] > 0

    def test_offline_strains_populated(self, base_unified_input):
        from rules_engine import apply_rules
        rule_outputs = apply_rules(base_unified_input)
        result = run_llm_decisions(base_unified_input, rule_outputs, use_bedrock=False)
        assert len(result["mix_selection"]["strains"]) > 0
