"""
Shared fixtures for formulation pipeline tests.

Provides:
  - Real sample paths for integration tests
  - Synthetic unified_input fixtures for unit tests
  - PipelineContext builders for stage tests
  - Mock LLM helpers
"""

import sys
import json
import pytest
from pathlib import Path
from copy import deepcopy

# ── Path setup ────────────────────────────────────────────────────────────────
FORMULATION_DIR = Path(__file__).parent.parent
SCIENCE_ENGINE_DIR = FORMULATION_DIR.parent
SHARED_DIR = SCIENCE_ENGINE_DIR / "shared"
WORKSPACE_DIR = SCIENCE_ENGINE_DIR.parent
KB_DIR = FORMULATION_DIR / "knowledge_base"

# Add science-engine to path FIRST so formulation is importable as a package
# (required for stages/ relative imports like `from ..models import PipelineContext`)
sys.path.insert(0, str(SCIENCE_ENGINE_DIR))
# Also add formulation dir for standalone module imports (models, rules_engine, etc.)
sys.path.insert(0, str(FORMULATION_DIR))
sys.path.insert(0, str(SHARED_DIR))

# ── Real sample paths ────────────────────────────────────────────────────────
REAL_SAMPLE_DIR = WORKSPACE_DIR / "analysis" / "nb1_2026_003" / "1421106528699"
REAL_BATCH_DIR = WORKSPACE_DIR / "analysis" / "nb1_2026_003"


@pytest.fixture
def real_sample_dir():
    """Path to a real sample with complete data (batch 003, sample 1421106528699)."""
    if not REAL_SAMPLE_DIR.exists():
        pytest.skip("Real sample directory not found")
    return str(REAL_SAMPLE_DIR)


@pytest.fixture
def real_batch_dir():
    """Path to a real batch directory."""
    if not REAL_BATCH_DIR.exists():
        pytest.skip("Real batch directory not found")
    return str(REAL_BATCH_DIR)


# ── Synthetic unified_input ───────────────────────────────────────────────────

def _make_guild(name, abundance_pct, status, clr=None, priority_level="Monitor",
                evenness=None, optimal_pct=0, actual_players=0, optimal_players=0):
    return {
        "name": name,
        "abundance_pct": abundance_pct,
        "status": status,
        "healthy_range": "",
        "clr": clr,
        "clr_status": "",
        "priority_level": priority_level,
        "evenness": evenness,
        "evenness_status": "",
        "optimal_pct": optimal_pct,
        "actual_players": actual_players,
        "optimal_players": optimal_players,
    }


@pytest.fixture
def healthy_guilds():
    """Guild data where everything is within range."""
    return {
        "fiber_degraders": _make_guild("Fiber Degraders", 25.0, "Within range", clr=0.1),
        "bifidobacteria": _make_guild("Bifidobacteria", 8.0, "Within range", clr=0.05),
        "cross_feeders": _make_guild("Cross-Feeders", 15.0, "Within range", clr=-0.1),
        "butyrate_producers": _make_guild("Butyrate Producers", 20.0, "Within range", clr=0.2),
        "proteolytic": _make_guild("Proteolytic Guild", 5.0, "Within range", clr=-0.2),
        "mucin_degraders": _make_guild("Mucin Degraders", 3.0, "Within range", clr=-0.1),
    }


@pytest.fixture
def dysbiotic_guilds():
    """Guild data with broad collapse (≥3 beneficial guilds compromised)."""
    return {
        "fiber_degraders": _make_guild("Fiber Degraders", 5.0, "Below range", clr=-0.5, priority_level="1A"),
        "bifidobacteria": _make_guild("Bifidobacteria", 0.5, "Below range", clr=-0.8, priority_level="CRITICAL"),
        "cross_feeders": _make_guild("Cross-Feeders", 3.0, "Below range", clr=-0.4, priority_level="1B"),
        "butyrate_producers": _make_guild("Butyrate Producers", 4.0, "Below range", clr=-0.3, priority_level="1A"),
        "proteolytic": _make_guild("Proteolytic Guild", 25.0, "Above range", clr=0.8, priority_level="1A"),
        "mucin_degraders": _make_guild("Mucin Degraders", 3.0, "Within range", clr=0.0),
    }


@pytest.fixture
def bifido_depleted_guilds():
    """Guild data with only Bifidobacteria depleted — targeted intervention."""
    return {
        "fiber_degraders": _make_guild("Fiber Degraders", 22.0, "Within range", clr=0.1),
        "bifidobacteria": _make_guild("Bifidobacteria", 0.3, "Below range — Absent", clr=-1.0, priority_level="CRITICAL"),
        "cross_feeders": _make_guild("Cross-Feeders", 14.0, "Within range", clr=-0.1),
        "butyrate_producers": _make_guild("Butyrate Producers", 18.0, "Within range", clr=0.1),
        "proteolytic": _make_guild("Proteolytic Guild", 6.0, "Within range", clr=-0.1),
        "mucin_degraders": _make_guild("Mucin Degraders", 4.0, "Within range", clr=-0.05),
    }


@pytest.fixture
def base_questionnaire():
    """Minimal complete questionnaire data."""
    return {
        "completion": {
            "completed_steps": [1, 2, 3, 4, 5, 6, 7, 8, 9],
            "is_completed": True,
            "completion_pct": 100,
        },
        "demographics": {
            "age": 35,
            "biological_sex": "female",
            "height_cm": 165,
            "weight_kg": 62,
            "bmi": 22.8,
            "country": "Netherlands",
            "occupation_environment": "office",
        },
        "goals": {
            "ranked": ["boost_energy_reduce_fatigue", "improve_skin_health", "reduce_stress_anxiety"],
            "top_goal": "boost_energy_reduce_fatigue",
            "other_raw_text": None,
            "other_resolved_key": None,
        },
        "digestive": {
            "stool_type": 4,
            "bloating_frequency": "occasionally",
            "bloating_severity": 4,
            "bloating_when": [],
            "digestive_satisfaction": 6,
            "abdominal_pain_severity": 2,
            "abdominal_pain_frequency": "rarely",
            "abdominal_pain_character": [],
            "digestive_symptoms_with_stress": "sometimes",
        },
        "medical": {
            "medications": [],
            "diagnoses": [],
            "family_history": {},
            "vitamin_deficiencies": [],
            "reported_deficiencies": [],
            "drug_allergies": "",
            "drug_allergies_has": "no",
            "nsaid_use": "",
            "nsaid_which": "",
            "skin_concerns": ["acne"],
            "skin_persistence": "persistent",
            "skin_change_patterns": "diet_stress",
            "skin_issues_frequency": "frequent",
            "uti_per_year": "1-2",
            "colds_per_year": "2-3",
            "infection_recovery": "2-4 weeks",
            "gut_brain_symptoms": [],
            "colon_symptoms": [],
            "motility_details": "",
            "motility_symptoms": [],
            "previous_supplements": "",
            "previous_supplement_effect": "",
            "previous_supplement_notes": "",
        },
        "lifestyle": {
            "stress_level": 6,
            "sleep_quality": 6,
            "sleep_duration": 7,
            "sleep_issues": [],
            "energy_level": "moderate",
            "mental_clarity": "",
            "mood_stability": "",
            "stress_recovery": "",
            "stress_symptoms": ["racing_thoughts"],
            "digestive_symptoms_with_stress": "sometimes",
            "exercise_detail": {
                "types": ["yoga", "walking"],
                "moderate_days_per_week": 3,
                "moderate_minutes_per_session": 30,
                "vigorous_days_per_week": 0,
                "vigorous_minutes_per_session": 0,
                "avg_daily_steps": "5000-7500",
                "hours_sitting_per_day": 8,
                "resistance_training": "",
            },
            "weight_kg": 62,
        },
        "current_supplements": [],
        "diet": {"diet_pattern": "mixed", "fiber_intake": "moderate"},
        "health_axes": {},
        "food_triggers": {"triggers": ["dairy"], "count": 1, "colon_triggers_text": ""},
    }


@pytest.fixture
def base_unified_input(healthy_guilds, base_questionnaire):
    """Complete unified_input for a healthy sample."""
    return {
        "sample_id": "1421000000000",
        "batch_id": "nb1_2026_test",
        "microbiome": {
            "guilds": healthy_guilds,
            "clr_ratios": {"CUR": 0.1, "FCR": 0.2, "MDR": -0.1, "PPR": -0.3},
            "vitamin_signals": {
                "biotin": {"display_name": "Biotin", "status": "adequate", "risk_level": 0},
                "folate": {"display_name": "Folate", "status": "adequate", "risk_level": 0},
                "B12": {"display_name": "B12", "status": "adequate", "risk_level": 0},
            },
            "overall_score": {"total": 72, "band": "Moderate"},
            "root_causes": {},
            "guild_scenarios": [],
        },
        "questionnaire": base_questionnaire,
        "_sources": {"microbiome_analysis": "/test", "questionnaire": "/test/q"},
    }


@pytest.fixture
def dysbiotic_unified_input(dysbiotic_guilds, base_questionnaire):
    """Unified input with broad dysbiosis + high stress."""
    q = deepcopy(base_questionnaire)
    q["lifestyle"]["stress_level"] = 8
    q["lifestyle"]["sleep_quality"] = 4
    q["lifestyle"]["stress_symptoms"] = ["racing_thoughts", "anxiety"]
    q["goals"]["ranked"] = ["reduce_stress_anxiety", "improve_sleep_quality", "improve_digestion_gut_comfort"]
    q["goals"]["top_goal"] = "reduce_stress_anxiety"
    q["digestive"]["bloating_severity"] = 7
    q["digestive"]["digestive_satisfaction"] = 3
    return {
        "sample_id": "1421000000001",
        "batch_id": "nb1_2026_test",
        "microbiome": {
            "guilds": dysbiotic_guilds,
            "clr_ratios": {"CUR": -0.5, "FCR": -0.3, "MDR": 0.1, "PPR": 0.6},
            "vitamin_signals": {
                "biotin": {"display_name": "Biotin", "status": "at risk", "risk_level": 2},
                "folate": {"display_name": "Folate", "status": "at risk", "risk_level": 2},
                "B12": {"display_name": "B12", "status": "adequate", "risk_level": 0},
            },
            "overall_score": {"total": 35, "band": "Poor"},
            "root_causes": {},
            "guild_scenarios": [],
        },
        "questionnaire": q,
    }


@pytest.fixture
def bifido_depleted_unified_input(bifido_depleted_guilds, base_questionnaire):
    """Unified input with targeted Bifido depletion."""
    return {
        "sample_id": "1421000000002",
        "batch_id": "nb1_2026_test",
        "microbiome": {
            "guilds": bifido_depleted_guilds,
            "clr_ratios": {"CUR": 0.2, "FCR": 0.1, "MDR": -0.05, "PPR": -0.2},
            "vitamin_signals": {},
            "overall_score": {"total": 55, "band": "Below Average"},
            "root_causes": {},
            "guild_scenarios": [],
        },
        "questionnaire": base_questionnaire,
    }


@pytest.fixture
def medication_unified_input(base_unified_input):
    """Unified input with medications that trigger KB rules."""
    data = deepcopy(base_unified_input)
    data["questionnaire"]["medical"]["medications"] = [
        {"name": "Levothyroxine", "dosage": "100mcg", "how_long": "5 years"},
        {"name": "Ramipril", "dosage": "5mg", "how_long": "2 years"},
    ]
    return data


# ── PipelineContext helpers ───────────────────────────────────────────────────

@pytest.fixture
def make_pipeline_context():
    """Factory for creating PipelineContext with specified data."""
    from formulation.models import PipelineContext

    def _factory(unified_input=None, use_llm=False, **kwargs):
        ctx = PipelineContext(
            sample_id=unified_input.get("sample_id", "test") if unified_input else "test",
            sample_dir="/tmp/test_sample",
            batch_id=unified_input.get("batch_id", "test_batch") if unified_input else "test_batch",
            use_llm=use_llm,
            compact=True,
            unified_input=unified_input or {},
        )
        for k, v in kwargs.items():
            setattr(ctx, k, v)
        return ctx

    return _factory


# ── Knowledge base loaders ────────────────────────────────────────────────────

@pytest.fixture
def synbiotic_mixes_kb():
    """Load synbiotic_mixes.json knowledge base."""
    with open(KB_DIR / "synbiotic_mixes.json", 'r') as f:
        return json.load(f)


@pytest.fixture
def supplements_kb():
    """Load supplements_nonvitamins.json knowledge base."""
    with open(KB_DIR / "supplements_nonvitamins.json", 'r') as f:
        return json.load(f)


@pytest.fixture
def vitamins_kb():
    """Load vitamins_minerals.json knowledge base."""
    with open(KB_DIR / "vitamins_minerals.json", 'r') as f:
        return json.load(f)
