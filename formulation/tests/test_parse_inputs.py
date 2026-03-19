"""
Tests for parse_inputs.py — Input parsing and unification.
"""

import pytest
import json
from parse_inputs import (
    extract_guild_data, extract_clr_ratios, extract_vitamin_signals,
    extract_overall_score, extract_root_causes, extract_questionnaire_data,
    _parse_stool_type, _extract_food_triggers, parse_inputs,
)


# ── extract_guild_data ────────────────────────────────────────────────────────

class TestExtractGuildData:
    def test_master_json_format(self):
        """Format 1: dict keyed by guild name with abundance/status."""
        analysis = {
            "bacterial_groups": {
                "Fiber Degraders": {
                    "abundance": 25.3,
                    "status": "Within range",
                    "clr": 0.15,
                    "priority_level": "Monitor",
                    "evenness": 0.82,
                },
                "HMO/Oligosaccharide-Utilising Bifidobacteria": {
                    "abundance": 5.2,
                    "status": "Below range",
                    "clr": -0.45,
                    "priority_level": "1A",
                },
            }
        }
        guilds = extract_guild_data(analysis)
        assert "fiber_degraders" in guilds
        assert "bifidobacteria" in guilds
        assert guilds["fiber_degraders"]["abundance_pct"] == 25.3
        assert guilds["fiber_degraders"]["status"] == "Within range"
        assert guilds["bifidobacteria"]["clr"] == -0.45

    def test_list_format(self):
        """Format 3: List with 'name' key."""
        analysis = {
            "bacterial_groups": [
                {"name": "Fiber Degraders", "abundance": 20.0, "status": "Within range"},
                {"name": "Butyrate Producers", "abundance": 15.0, "status": "Within range"},
            ]
        }
        guilds = extract_guild_data(analysis)
        assert "fiber_degraders" in guilds
        assert "butyrate_producers" in guilds

    def test_platform_format(self):
        """Format 2: bacterial_groups_tab.guilds."""
        analysis = {
            "bacterial_groups": {},
            "bacterial_groups_tab": {
                "guilds": [
                    {"name": "Cross-Feeders", "capacity": {"actual_pct": 12.0}, "status": "Within range"},
                ]
            }
        }
        guilds = extract_guild_data(analysis)
        assert "cross_feeders" in guilds
        assert guilds["cross_feeders"]["abundance_pct"] == 12.0

    def test_empty_analysis(self):
        guilds = extract_guild_data({})
        assert guilds == {}

    def test_guild_name_normalization(self):
        """All guild names should normalize to consistent keys."""
        analysis = {
            "bacterial_groups": {
                "Proteolytic Guild": {"abundance": 8.0, "status": "Above range"},
                "Mucin Degraders": {"abundance": 4.0, "status": "Within range"},
            }
        }
        guilds = extract_guild_data(analysis)
        assert "proteolytic" in guilds
        assert "mucin_degraders" in guilds


# ── extract_clr_ratios ───────────────────────────────────────────────────────

class TestExtractCLRRatios:
    def test_debug_raw_metrics_source(self):
        analysis = {
            "_debug": {
                "raw_metrics": {
                    "CUR": 0.35, "FCR": -0.12, "MDR": 0.45, "PPR": -0.28,
                    "CUR_label": "Fiber-dominant", "FCR_label": "Neutral",
                }
            }
        }
        ratios = extract_clr_ratios(analysis)
        assert ratios["CUR"] == 0.35
        assert ratios["FCR"] == -0.12
        assert ratios["MDR"] == 0.45
        assert ratios["PPR"] == -0.28
        assert ratios["CUR_label"] == "Fiber-dominant"

    def test_metabolic_function_dials_source(self):
        analysis = {
            "metabolic_function": {
                "dials": {
                    "main_fuel": {"value": 0.5},
                    "fermentation_efficiency": {"value": -0.2},
                    "mucus_dependency": {"value": 0.3},
                    "putrefaction_pressure": {"value": -0.1},
                }
            }
        }
        ratios = extract_clr_ratios(analysis)
        assert ratios["CUR"] == 0.5
        assert ratios["FCR"] == -0.2
        assert ratios["MDR"] == 0.3
        assert ratios["PPR"] == -0.1

    def test_empty_analysis(self):
        ratios = extract_clr_ratios({})
        assert all(v is None for k, v in ratios.items() if not k.endswith("_label"))


# ── extract_vitamin_signals ───────────────────────────────────────────────────

class TestExtractVitaminSignals:
    def test_vitamins_tab_format(self):
        analysis = {
            "vitamins_tab": {
                "vitamins": [
                    {"key": "biotin", "display_name": "Biotin", "status": "adequate", "risk_level": 0},
                    {"key": "folate", "display_name": "Folate", "status": "at risk", "risk_level": 2},
                ]
            }
        }
        signals = extract_vitamin_signals(analysis)
        assert "biotin" in signals
        assert "folate" in signals
        assert signals["folate"]["risk_level"] == 2

    def test_empty_analysis(self):
        signals = extract_vitamin_signals({})
        assert signals == {}


# ── extract_overall_score ─────────────────────────────────────────────────────

class TestExtractOverallScore:
    def test_platform_format(self):
        analysis = {
            "overview_tab": {
                "gut_health_glance": {
                    "overall_score": {"total": 68, "band": "Moderate"}
                }
            }
        }
        score = extract_overall_score(analysis)
        assert score["total"] == 68
        assert score["band"] == "Moderate"

    def test_empty(self):
        score = extract_overall_score({})
        assert score["total"] == 0


# ── extract_root_causes ──────────────────────────────────────────────────────

class TestExtractRootCauses:
    def test_with_data(self):
        analysis = {
            "root_causes": {
                "diagnostic_flags": [
                    {"flag": "Bifido absent", "guild": "bifidobacteria", "severity": "CRITICAL", "direction": "below"}
                ],
                "primary_pattern": {"pattern": "Bifido depletion", "scientific": "Lactate amplifier failure"},
                "trophic_impact": {"primary_bottleneck": "bifido", "cascade_impacts": []},
                "reversibility": {"level": "high", "label": "Highly reversible", "estimated_timeline": "8-12 weeks"},
            }
        }
        rc = extract_root_causes(analysis)
        assert len(rc["diagnostic_flags"]) == 1
        assert rc["diagnostic_flags"][0]["flag"] == "Bifido absent"
        assert rc["primary_pattern"]["scientific"] == "Lactate amplifier failure"
        assert rc["reversibility"]["level"] == "high"

    def test_empty(self):
        assert extract_root_causes({}) == {}


# ── _parse_stool_type ─────────────────────────────────────────────────────────

class TestParseStoolType:
    def test_type_4(self):
        assert _parse_stool_type("type_4") == 4

    def test_type_6(self):
        assert _parse_stool_type("type_6") == 6

    def test_empty(self):
        assert _parse_stool_type("") is None

    def test_none(self):
        assert _parse_stool_type(None) is None

    def test_numeric_string(self):
        assert _parse_stool_type("3") == 3


# ── _extract_food_triggers ───────────────────────────────────────────────────

class TestExtractFoodTriggers:
    def test_deduplication(self):
        step2 = {"food_triggers": ["dairy", "gluten"]}
        step4 = {"trigger_foods": ["dairy", "spicy food"]}
        step7 = {"food_sensitivities": ["gluten"]}
        result = _extract_food_triggers(step2, step4, step7)
        assert result["triggers"] == ["dairy", "gluten", "spicy food"]
        assert result["count"] == 3

    def test_empty_inputs(self):
        result = _extract_food_triggers({}, {}, {})
        assert result["triggers"] == []
        assert result["count"] == 0


# ── extract_questionnaire_data ────────────────────────────────────────────────

class TestExtractQuestionnaireData:
    def test_basic_extraction(self):
        questionnaire = {
            "questionnaire_data": {
                "step_1": {
                    "basic": {"age": 30, "biological_sex": "female", "height_cm": 170, "weight_kg": 65},
                    "goals": {"main_goals_ranked": ["boost_energy_reduce_fatigue"]},
                },
                "step_2": {"bloating_severity": 5, "stool_pattern": "type_4"},
                "step_3": {"diagnoses": [], "other_medications": []},
                "step_4": {},
                "step_5": {"overall_stress_level_1_10": 7, "sleep_quality_rating_1_10": 5},
                "step_6": {},
                "step_7": {},
            },
            "completed_steps": [1, 2, 3, 4, 5],
            "is_completed": False,
        }
        result = extract_questionnaire_data(questionnaire, use_bedrock=False)
        assert result["demographics"]["age"] == 30
        assert result["demographics"]["biological_sex"] == "female"
        assert result["demographics"]["bmi"] == round(65 / (1.70 ** 2), 1)
        assert result["digestive"]["bloating_severity"] == 5
        assert result["digestive"]["stool_type"] == 4
        assert result["lifestyle"]["stress_level"] == 7
        assert result["lifestyle"]["sleep_quality"] == 5
        assert result["completion"]["completion_pct"] == pytest.approx(55.55, abs=0.1)

    def test_medications_from_other_medications(self):
        """Medications should come from other_medications, not medications field."""
        questionnaire = {
            "questionnaire_data": {
                "step_1": {"basic": {}, "goals": {"main_goals_ranked": []}},
                "step_2": {}, "step_3": {
                    "medications": [],  # always empty in practice
                    "other_medications": [
                        {"name": "Levothyroxine", "dosage": "100mcg", "how_long": "5 years"}
                    ],
                },
                "step_4": {}, "step_5": {}, "step_6": {}, "step_7": {},
            },
            "completed_steps": [1, 2, 3],
        }
        result = extract_questionnaire_data(questionnaire, use_bedrock=False)
        assert len(result["medical"]["medications"]) == 1
        assert result["medical"]["medications"][0]["name"] == "Levothyroxine"

    def test_goal_resolution_without_bedrock(self):
        """When use_bedrock=False, 'other' goals should be dropped gracefully."""
        questionnaire = {
            "questionnaire_data": {
                "step_1": {
                    "basic": {},
                    "goals": {
                        "main_goals_ranked": ["boost_energy_reduce_fatigue", "other"],
                        "other_goal_details": "Optimise metabolism",
                    },
                },
                "step_2": {}, "step_3": {}, "step_4": {}, "step_5": {},
                "step_6": {}, "step_7": {},
            },
            "completed_steps": [1],
        }
        result = extract_questionnaire_data(questionnaire, use_bedrock=False)
        # 'other' dropped because Bedrock unavailable
        assert "boost_energy_reduce_fatigue" in result["goals"]["ranked"]
        assert result["goals"]["other_raw_text"] == "Optimise metabolism"


# ── parse_inputs (integration with real data) ─────────────────────────────────

class TestParseInputsIntegration:
    def test_real_sample(self, real_sample_dir):
        """Test parse_inputs with a real sample directory."""
        result = parse_inputs(real_sample_dir)

        # Structure
        assert "sample_id" in result
        assert "microbiome" in result
        assert "questionnaire" in result

        # Microbiome
        mb = result["microbiome"]
        assert len(mb["guilds"]) >= 4, f"Expected ≥4 guilds, got {len(mb['guilds'])}"
        assert any(v is not None for v in mb["clr_ratios"].values()), "Expected at least one CLR ratio"

        # Questionnaire
        q = result["questionnaire"]
        assert q["completion"]["completion_pct"] > 0, "Questionnaire should have some completion"
        assert q["goals"]["ranked"], "Goals should be present"
