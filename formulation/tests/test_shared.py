"""
Tests for shared modules — guild_priority.py and formatting.py.
"""

import pytest
from guild_priority import (
    compute_guild_priority, build_priority_list, score_to_label,
    classify_scenario, get_importance, is_beneficial,
    compute_evenness_modifier, format_priority_text,
    GUILD_CLIENT_NAMES, GUILD_DISPLAY_NAMES,
)
from formatting import format_dose, sleep_label


# ── guild_priority: helpers ──────────────────────────────────────────────────

class TestGuildPriorityHelpers:
    def test_is_beneficial(self):
        assert is_beneficial("fiber_degraders") is True
        assert is_beneficial("Fiber Degraders") is True
        assert is_beneficial("bifidobacteria") is True
        assert is_beneficial("proteolytic") is False
        assert is_beneficial("mucin_degraders") is False

    def test_get_importance(self):
        assert get_importance("butyrate_producers") == 1.2
        assert get_importance("bifidobacteria") == 0.9
        assert get_importance("fiber_degraders") == 1.0
        assert get_importance("unknown_guild") == 1.0  # fallback

    def test_score_to_label(self):
        assert score_to_label(10.0) == "CRITICAL"
        assert score_to_label(8.0) == "CRITICAL"
        assert score_to_label(6.0) == "1A"
        assert score_to_label(5.0) == "1A"
        assert score_to_label(3.0) == "1B"
        assert score_to_label(2.0) == "1B"
        assert score_to_label(1.0) == "Monitor"
        assert score_to_label(0.0) == "Monitor"


# ── guild_priority: classify_scenario ─────────────────────────────────────────

class TestClassifyScenario:
    def test_beneficial_below_suppressed_depleted(self):
        assert classify_scenario("Below range", 5.0, -0.5, beneficial=True) == "DEPLETED"

    def test_beneficial_below_balanced_understaffed(self):
        assert classify_scenario("Below range", 5.0, 0.0, beneficial=True) == "UNDERSTAFFED"

    def test_beneficial_below_enriched_substrate_limited(self):
        assert classify_scenario("Below range", 5.0, 0.5, beneficial=True) == "SUBSTRATE LIMITED"

    def test_beneficial_within_balanced_healthy(self):
        assert classify_scenario("Within range", 15.0, 0.0, beneficial=True) == "HEALTHY"

    def test_beneficial_within_enriched_thriving(self):
        assert classify_scenario("Within range", 15.0, 0.5, beneficial=True) == "THRIVING"

    def test_beneficial_within_suppressed_under_pressure(self):
        assert classify_scenario("Within range", 15.0, -0.5, beneficial=True) == "UNDER PRESSURE"

    def test_beneficial_above_enriched_overgrowth(self):
        assert classify_scenario("Above range", 30.0, 0.5, beneficial=True) == "OVERGROWTH"

    def test_contextual_below_favorable(self):
        """Contextual guilds below/within range = FAVORABLE."""
        assert classify_scenario("Below range", 3.0, -0.5, beneficial=False) == "FAVORABLE"
        assert classify_scenario("Within range", 5.0, 0.0, beneficial=False) == "FAVORABLE"

    def test_contextual_above_overgrowth(self):
        assert classify_scenario("Above range", 25.0, 0.5, beneficial=False) == "OVERGROWTH"

    def test_absent_status(self):
        assert classify_scenario("Absent — CRITICAL", 0, -1.0, beneficial=True) == "DEPLETED"


# ── guild_priority: compute_evenness_modifier ─────────────────────────────────

class TestEvennessModifier:
    def test_zero_state_no_modifier(self):
        """When state_value is 0, evenness doesn't matter."""
        assert compute_evenness_modifier(0.2, False, 0) == 1.0

    def test_low_evenness_contextual(self):
        assert compute_evenness_modifier(0.3, True, 5.0) == 1.3

    def test_low_evenness_beneficial(self):
        assert compute_evenness_modifier(0.3, False, 5.0) == 1.2

    def test_medium_evenness(self):
        assert compute_evenness_modifier(0.5, False, 5.0) == 1.1

    def test_high_evenness(self):
        assert compute_evenness_modifier(0.8, False, 5.0) == 1.0


# ── guild_priority: compute_guild_priority ────────────────────────────────────

class TestComputeGuildPriority:
    def test_healthy_guild(self):
        result = compute_guild_priority("fiber_degraders", 25.0, "Within range", 0.1, 0.8)
        assert result["priority_level"] == "Monitor"
        assert result["priority_score"] == 0
        assert result["scenario"] == "HEALTHY"
        assert result["is_beneficial"] is True

    def test_depleted_guild(self):
        result = compute_guild_priority("bifidobacteria", 0.5, "Below range", -0.8, 0.3)
        assert result["priority_level"] in ("CRITICAL", "1A")
        assert result["priority_score"] >= 5.0
        assert result["scenario"] == "DEPLETED"

    def test_proteolytic_overgrowth(self):
        result = compute_guild_priority("proteolytic", 25.0, "Above range", 0.8, 0.4)
        assert result["priority_level"] in ("CRITICAL", "1A")
        assert result["scenario"] == "OVERGROWTH"
        assert result["is_beneficial"] is False

    def test_mucin_favorable(self):
        result = compute_guild_priority("mucin_degraders", 3.0, "Within range", -0.1, 0.7)
        assert result["priority_level"] == "Monitor"
        assert result["scenario"] == "FAVORABLE"

    def test_absent_critical(self):
        result = compute_guild_priority("bifidobacteria", 0, "Absent — CRITICAL", -1.0, 0.0)
        assert result["priority_level"] in ("CRITICAL", "1A")
        assert result["priority_score"] >= 5.0


# ── guild_priority: build_priority_list ──────────────────────────────────────

class TestBuildPriorityList:
    def test_sorted_by_score(self, dysbiotic_guilds):
        result = build_priority_list(dysbiotic_guilds)
        scores = [r["priority_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_action_text_present(self, dysbiotic_guilds):
        result = build_priority_list(dysbiotic_guilds)
        for item in result:
            assert "action" in item
            assert len(item["action"]) > 0

    def test_healthy_guilds_all_monitor(self, healthy_guilds):
        result = build_priority_list(healthy_guilds)
        for item in result:
            assert item["priority_level"] == "Monitor"

    def test_format_priority_text(self, dysbiotic_guilds):
        text = format_priority_text(dysbiotic_guilds)
        assert len(text) > 0
        assert "CRITICAL" in text or "1A" in text


# ── guild_priority: name mappings ─────────────────────────────────────────────

class TestGuildNameMappings:
    def test_client_names_complete(self):
        """All guild display names should have client-facing equivalents."""
        for name in GUILD_DISPLAY_NAMES.values():
            assert name in GUILD_CLIENT_NAMES, f"Missing client name for: {name}"


# ── formatting: format_dose ──────────────────────────────────────────────────

class TestFormatDose:
    def test_whole_number(self):
        assert format_dose(1500.0) == "1500"

    def test_decimal(self):
        assert format_dose(712.5) == "712.5"

    def test_small_decimal(self):
        assert format_dose(0.9) == "0.9"

    def test_none(self):
        assert format_dose(None) == "—"

    def test_integer(self):
        assert format_dose(100) == "100"


# ── formatting: sleep_label ──────────────────────────────────────────────────

class TestSleepLabel:
    def test_poor(self):
        assert sleep_label(2) == "poor"

    def test_below_average(self):
        assert sleep_label(4) == "below average"

    def test_moderate(self):
        assert sleep_label(6) == "moderate"

    def test_good(self):
        assert sleep_label(8) == "good"

    def test_excellent(self):
        assert sleep_label(10) == "excellent"

    def test_none(self):
        assert sleep_label(None) == "not reported"

    def test_boundary_3(self):
        assert sleep_label(3) == "poor"

    def test_boundary_7(self):
        assert sleep_label(7) == "moderate"

    def test_boundary_9(self):
        assert sleep_label(9) == "good"
