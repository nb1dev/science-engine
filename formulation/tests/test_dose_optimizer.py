"""
Tests for dose_optimizer.py — JSON-driven dose optimization rules.
"""

import pytest
from dose_optimizer import DoseOptimizer, add_excipient_if_needed


class TestDoseOptimizer:
    def test_load_rules(self):
        optimizer = DoseOptimizer()
        assert len(optimizer.rules) > 0

    def test_find_component(self):
        components = [
            {"substance": "Ashwagandha (Withania somnifera)", "dose_mg": 600},
            {"substance": "L-Theanine", "dose_mg": 200},
        ]
        found = DoseOptimizer._find_component(components, "Ashwagandha")
        assert found is not None
        assert found["dose_mg"] == 600

    def test_find_component_case_insensitive(self):
        components = [{"substance": "L-Theanine", "dose_mg": 200}]
        found = DoseOptimizer._find_component(components, "l-theanine")
        assert found is not None

    def test_find_component_not_found(self):
        components = [{"substance": "Zinc", "dose_mg": 8}]
        found = DoseOptimizer._find_component(components, "Quercetin")
        assert found is None

    def test_optimize_empty(self):
        optimizer = DoseOptimizer()
        result = optimizer.optimize([])
        assert result["components"] == []
        assert result["applied_rules"] == []

    def test_optimize_preserves_non_matching(self):
        optimizer = DoseOptimizer()
        components = [
            {"substance": "Quercetin", "dose_mg": 300, "weight_mg": 300, "rationale": "test"},
        ]
        result = optimizer.optimize(components)
        # Should not crash; Quercetin alone shouldn't trigger most rules
        assert len(result["components"]) == 1


class TestAddExcipient:
    def test_fills_gap(self):
        components = [
            {"substance": "A", "dose_mg": 400, "weight_mg": 400},
            {"substance": "B", "dose_mg": 100, "weight_mg": 100},
        ]
        result = add_excipient_if_needed(components, capacity_mg=650)
        excipient = next((c for c in result if c.get("type") == "excipient"), None)
        assert excipient is not None
        assert excipient["dose_mg"] == 150

    def test_no_gap(self):
        components = [
            {"substance": "A", "dose_mg": 650, "weight_mg": 650},
        ]
        result = add_excipient_if_needed(components, capacity_mg=650)
        excipient = next((c for c in result if c.get("type") == "excipient"), None)
        assert excipient is None

    def test_overflow_raises(self):
        components = [
            {"substance": "A", "dose_mg": 700, "weight_mg": 700},
        ]
        with pytest.raises(ValueError, match="exceed"):
            add_excipient_if_needed(components, capacity_mg=650)

    def test_idempotent(self):
        components = [
            {"substance": "A", "dose_mg": 400, "weight_mg": 400},
        ]
        result1 = add_excipient_if_needed(components, 650)
        result2 = add_excipient_if_needed(result1, 650)
        excipients = [c for c in result2 if c.get("type") == "excipient"]
        assert len(excipients) == 1
