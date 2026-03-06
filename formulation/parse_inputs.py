#!/usr/bin/env python3
"""
Parse Inputs Module — Load and unify microbiome + questionnaire data.

Reads:
  - {sample}_microbiome_analysis.json (from report_automation pipeline)
  - questionnaire_{sample}.json (from sample directory)

Produces: unified_input dict with all data needed for formulation decisions.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional, Any


def load_microbiome_analysis(sample_dir: str) -> Dict:
    """Load microbiome analysis JSON from report_automation output or sample dir."""
    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name

    # Search paths in priority order
    search_paths = [
        # 1. Sample's reports directory (new naming — standard pipeline output)
        sample_dir / "reports" / "reports_json" / f"microbiome_analysis_master_{sample_id}.json",
        # 2. Sample's reports directory (old naming)
        sample_dir / "reports" / "reports_json" / f"{sample_id}_microbiome_analysis.json",
        # 3. Sample's own report_json directory (legacy)
        sample_dir / "report_json" / f"{sample_id}_microbiome_analysis.json",
    ]

    analysis_path = None
    for path in search_paths:
        if path.exists():
            analysis_path = path
            break

    if analysis_path is None:
        # Return empty analysis dict with a flag — allows pipeline to detect missing data
        print(f"  ⚠️ Microbiome analysis JSON not found for {sample_id}")
        print(f"     Searched: {[str(p) for p in search_paths]}")
        print(f"     ⚠️ WARNING: Formulation will run without microbiome data — mix selection will default to Maintenance.")
        return {"_microbiome_data_missing": True}

    with open(analysis_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_questionnaire(sample_dir: str) -> Dict:
    """Load questionnaire — prefer .json, fall back to .md parsing."""
    import glob as _glob
    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name
    q_dir = sample_dir / "questionnaire"

    # Priority 1: JSON file (structured, preferred)
    json_path = q_dir / f"questionnaire_{sample_id}.json"
    if json_path.exists():
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # Priority 2: Markdown file (parsed fallback)
    md_files = sorted(_glob.glob(str(q_dir / f"questionnaire_{sample_id}*.md")))
    if md_files:
        print(f"  ⚠️ No JSON questionnaire — parsing .md fallback: {Path(md_files[0]).name}")
        return _parse_md_questionnaire(md_files[0], sample_id)

    # Priority 3: No questionnaire at all — return empty structure
    print(f"  ⚠️ No questionnaire found for {sample_id} — proceeding with microbiome data only")
    return {"questionnaire_data": {}, "completed_steps": [], "is_completed": False}


def _parse_md_questionnaire(md_path: str, sample_id: str) -> Dict:
    """Parse a markdown questionnaire into the same structure as JSON questionnaires."""
    import re as _re

    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    def _extract(pattern, default=None):
        """Extract first match of regex pattern from content."""
        m = _re.search(pattern, content, _re.IGNORECASE)
        return m.group(1).strip() if m else default

    def _extract_float(pattern, default=None):
        val = _extract(pattern)
        if val:
            try:
                return float(val)
            except ValueError:
                return default
        return default

    def _extract_int(pattern, default=None):
        val = _extract(pattern)
        if val:
            try:
                return int(float(val))
            except ValueError:
                return default
        return default

    def _extract_list(pattern):
        """Extract items after a pattern that are listed as '- item'."""
        m = _re.search(pattern + r'\n((?:- .+\n)*)', content, _re.IGNORECASE)
        if m:
            items = _re.findall(r'- (.+)', m.group(1))
            return [i.strip() for i in items if i.strip() and i.strip() != 'None']
        return []

    # Basic info
    age = _extract_float(r'\*\*Age:\*\*\s*(\d+\.?\d*)')
    sex = _extract(r'\*\*Sex/Gender:\*\*\s*(\w+)')
    height = _extract_float(r'\*\*Height \(cm\):\*\*\s*(\d+\.?\d*)')
    weight = _extract_float(r'\*\*Weight \(kg\):\*\*\s*(\d+\.?\d*)')
    country = _extract(r'\*\*Country of residence:\*\*\s*(.+)')

    # Goals — handle both `Primary goals (ranked):\n` and `Primary goals (ranked):**\n`
    goals_section = _re.search(r'Primary goals \(ranked\):\*{0,2}\s*\n((?:- .+\n)*)', content)
    goals = []
    if goals_section:
        goals = [g.strip() for g in _re.findall(r'- (.+)', goals_section.group(1)) if g.strip()]

    # Digestive
    stool_type = _extract(r'\*\*Type:\*\*\s*(type_\d)')
    bloating_severity = _extract_int(r'Bloating.*?Severity \(1-10\):\*\*\s*(\d+)')
    bloating_frequency = _extract(r'Bloating.*?Frequency:\*\*\s*(.+)')
    digestive_satisfaction = _extract_int(r'Overall Digestive Satisfaction.*?Score \(1-10\):\*\*\s*(\d+)')

    # Stress & Sleep
    stress = _extract_int(r'Stress level \(1-10\):\*\*\s*(\d+)')
    sleep_quality = _extract_int(r'Quality rating \(1-10\):\*\*\s*(\d+)')
    sleep_duration = _extract_float(r'Duration \(weeknights\):\*\*\s*(\d+\.?\d*)')
    energy = _extract(r'Typical energy level:\*\*\s*(.+)')

    # Stress symptoms
    stress_symptoms = []
    stress_section = _re.search(r'Stress symptoms:\*\*\s*\n?((?:- .+\n)*)', content)
    if stress_section:
        stress_symptoms = [s.strip() for s in _re.findall(r'- (.+)', stress_section.group(1)) if s.strip()]

    # Medications & medical
    diagnoses_text = _extract(r'Current Diagnoses\s*\n- (.+)', '')
    medications = []
    vitamin_deficiencies = []

    # Vitamin deficiencies
    vit_def_section = _re.search(r'Vitamin Deficiencies\s*\n\*\*Deficiencies:\*\*\s*\n?((?:- .+\n)*)', content)
    if vit_def_section:
        vit_defs = [v.strip() for v in _re.findall(r'- (.+)', vit_def_section.group(1)) if v.strip() and v.strip() != 'None']
        vitamin_deficiencies = vit_defs

    # Food triggers
    food_triggers = []
    trigger_section = _re.search(r'Trigger foods:\*\*\s*\n?((?:- .+\n)*)', content)
    if trigger_section:
        food_triggers = [t.strip() for t in _re.findall(r'- (.+)', trigger_section.group(1)) if t.strip()]

    # Current supplements
    current_supplements = []
    supp_matches = _re.findall(r'Supplement \d+:.*?Name/Brand:\s*(.+?)$', content, _re.MULTILINE)
    current_supplements = [s.strip() for s in supp_matches if s.strip()]

    # Completed steps
    steps_match = _extract(r'Completed Steps:\*\*\s*\{(.+?)\}')
    completed_steps = []
    if steps_match:
        completed_steps = [int(s.strip()) for s in steps_match.split(',') if s.strip().isdigit()]

    # Build same structure as JSON questionnaire
    return {
        "questionnaire_data": {
            "step_1": {
                "basic": {
                    "age": age,
                    "biological_sex": sex,
                    "height_cm": height,
                    "weight_kg": weight,
                    "country_of_residence": country,
                },
                "goals": {
                    "main_goals_ranked": goals,
                },
            },
            "step_2": {
                "stool_pattern": stool_type,
                "bloating_frequency": bloating_frequency,
                "bloating_severity": bloating_severity,
                "digestive_satisfaction": digestive_satisfaction,
                "food_triggers": food_triggers,
            },
            "step_3": {
                "diagnoses": [diagnoses_text] if diagnoses_text and diagnoses_text != 'None' else [],
                "medications": medications,
                "family_history": [],
                "vitamin_deficiencies": vitamin_deficiencies,
                "reported_deficiencies": vitamin_deficiencies,
            },
            "step_4": {},
            "step_5": {
                "overall_stress_level_1_10": stress,
                "sleep_quality_rating_1_10": sleep_quality,
                "sleep_duration_weeknights_hours": sleep_duration,
                "energy_level": energy,
                "stress_symptoms": stress_symptoms,
            },
            "step_6": {
                "current_supplements": current_supplements,
            },
            "step_7": {
                "food_sensitivities": food_triggers,
            },
        },
        "completed_steps": completed_steps,
        "is_completed": len(completed_steps) >= 9,
        "_source": "md_parsed",
    }


def extract_guild_data(analysis: Dict) -> Dict:
    """Extract guild abundances, CLR values, and status from microbiome analysis."""
    guilds = {}
    bacterial_groups = analysis.get("bacterial_groups", {})

    # Handle three formats:
    # 1. Master JSON: dict keyed by guild name → {"Fiber Degraders": {"abundance": 15.23, ...}}
    # 2. Platform JSON: bacterial_groups_tab.guilds → list of dicts with "name" key
    # 3. List format: list of dicts with "name" key

    items_to_process = []

    if isinstance(bacterial_groups, dict) and bacterial_groups:
        # Format 1: Master analysis JSON — dict keyed by guild name
        first_value = next(iter(bacterial_groups.values()), {})
        if "abundance" in first_value or "status" in first_value:
            # Master JSON format — keys are guild names, values are guild data
            items_to_process = [(name, data) for name, data in bacterial_groups.items()]
        else:
            # Maybe nested differently
            items_to_process = [(name, data) for name, data in bacterial_groups.items()]
    elif isinstance(bacterial_groups, list):
        # Format 3: List with "name" key
        items_to_process = [(g.get("name", ""), g) for g in bacterial_groups if g.get("name")]

    # Also check bacterial_groups_tab for platform JSON (Format 2)
    if not items_to_process:
        bg_tab = analysis.get("bacterial_groups_tab", {})
        guilds_list = bg_tab.get("guilds", [])
        items_to_process = [(g.get("name", ""), g) for g in guilds_list if g.get("name")]

    for name, guild in items_to_process:
        if not name:
            continue

        # Normalize guild name to key
        key = name.lower().replace(" ", "_").replace("-", "_")
        if "fiber" in key:
            key = "fiber_degraders"
        elif "bifido" in key:
            key = "bifidobacteria"
        elif "cross" in key:
            key = "cross_feeders"
        elif "butyrate" in key:
            key = "butyrate_producers"
        elif "proteo" in key:
            key = "proteolytic"
        elif "mucin" in key or "mucus" in key:
            key = "mucin_degraders"

        # Handle both master JSON format (abundance, clr) and platform format (capacity.actual_pct)
        capacity = guild.get("capacity", {})
        abundance = guild.get("abundance", capacity.get("actual_pct", guild.get("abundance_pct", 0)))

        guilds[key] = {
            "name": name,
            "abundance_pct": abundance,
            "status": guild.get("status", ""),
            "healthy_range": guild.get("healthy_range", ""),
            "clr": guild.get("clr"),
            "clr_status": guild.get("clr_status", ""),
            "priority_level": guild.get("priority_level", ""),
            "evenness": guild.get("evenness"),
            "evenness_status": guild.get("evenness_status", ""),
            "optimal_pct": capacity.get("optimal_pct", 0),
            "actual_players": capacity.get("actual_players", 0),
            "optimal_players": capacity.get("optimal_players", 0),
        }

    return guilds


def extract_clr_ratios(analysis: Dict) -> Dict:
    """Extract CLR diagnostic ratios from analysis."""
    ratios = {"CUR": None, "FCR": None, "MDR": None, "PPR": None}

    # Source 1: _debug.raw_metrics — values are TOP-LEVEL keys (not nested under clr_ratios)
    debug = analysis.get("_debug", {})
    raw_metrics = debug.get("raw_metrics", {})
    if raw_metrics:
        ratios["CUR"] = raw_metrics.get("CUR")
        ratios["FCR"] = raw_metrics.get("FCR")
        ratios["MDR"] = raw_metrics.get("MDR")
        ratios["PPR"] = raw_metrics.get("PPR")
        # Pre-computed labels (computed at source, prevents sign misreading)
        ratios["CUR_label"] = raw_metrics.get("CUR_label")
        ratios["FCR_label"] = raw_metrics.get("FCR_label")
        ratios["MDR_label"] = raw_metrics.get("MDR_label")
        ratios["PPR_label"] = raw_metrics.get("PPR_label")

    # Source 2: metabolic_function.dials (master analysis format)
    if not any(v is not None for v in ratios.values()):
        dials = analysis.get("metabolic_function", {}).get("dials", {})
        if dials:
            ratios["CUR"] = dials.get("main_fuel", {}).get("value")
            ratios["FCR"] = dials.get("fermentation_efficiency", {}).get("value")
            ratios["MDR"] = dials.get("mucus_dependency", dials.get("gut_lining_dependence", {})).get("value") if isinstance(dials.get("mucus_dependency", dials.get("gut_lining_dependence")), dict) else None
            ratios["PPR"] = dials.get("putrefaction_pressure", dials.get("harsh_byproducts", {})).get("value") if isinstance(dials.get("putrefaction_pressure", dials.get("harsh_byproducts")), dict) else None

    # Source 3: overview_tab.metabolic_dials.dials (platform format)
    if not any(v is not None for v in ratios.values()):
        dials = analysis.get("overview_tab", {}).get("metabolic_dials", {}).get("dials", {})
        if dials:
            ratios["CUR"] = dials.get("main_fuel", {}).get("value")
            ratios["FCR"] = dials.get("fermentation_efficiency", {}).get("value")
            ratios["MDR"] = dials.get("gut_lining_dependence", {}).get("value")
            ratios["PPR"] = dials.get("harsh_byproducts", {}).get("value")

    return ratios


def extract_vitamin_signals(analysis: Dict) -> Dict:
    """Extract vitamin synthesis signals from analysis."""
    signals = {}

    # From vitamins_tab (platform format)
    vitamins_tab = analysis.get("vitamins_tab", {})
    vitamins_list = vitamins_tab.get("vitamins", [])

    if not vitamins_list:
        vitamins_list = analysis.get("vitamin_synthesis", {}).get("vitamins", [])

    for vit in vitamins_list:
        key = vit.get("key", "")
        signals[key] = {
            "display_name": vit.get("display_name", ""),
            "status": vit.get("status", ""),
            "risk_level": vit.get("risk_level", 0),
            "assessment": vit.get("assessment", ""),
            "producers_detected": vit.get("producers_detected"),
            "producers_total": vit.get("producers_total"),
        }

    return signals


def extract_overall_score(analysis: Dict) -> Dict:
    """Extract overall gut health score."""
    # Platform format
    glance = analysis.get("overview_tab", {}).get("gut_health_glance", {})
    score_data = glance.get("overall_score", {})

    if not score_data:
        score_data = analysis.get("overall_score", {})

    return {
        "total": score_data.get("total", 0),
        "band": score_data.get("band", ""),
    }


def extract_root_causes(analysis: Dict) -> Dict:
    """Extract root causes from microbiome analysis — single source of truth for both
    the health report and the formulation decision trace."""
    rc = analysis.get("root_causes", {})
    if not rc:
        return {}

    # Extract diagnostic flags (ordered by severity)
    diagnostic_flags = []
    for flag in rc.get("diagnostic_flags", []):
        diagnostic_flags.append({
            "flag": flag.get("flag", ""),
            "guild": flag.get("guild", ""),
            "severity": flag.get("severity", ""),
            "direction": flag.get("direction", ""),
            "metric_detail": flag.get("metric_detail", {}),
        })

    # Extract primary pattern
    primary_pattern = rc.get("primary_pattern", {})

    # Extract trophic impact
    trophic = rc.get("trophic_impact", {})
    cascade_impacts = []
    for ci in trophic.get("cascade_impacts", []):
        cascade_impacts.append({
            "type": ci.get("type", ""),
            "title": ci.get("title", ""),
            "description": ci.get("description", ""),
        })

    return {
        "diagnostic_flags": diagnostic_flags,
        "primary_pattern": {
            "pattern": primary_pattern.get("pattern", ""),
            "scientific": primary_pattern.get("scientific", ""),
            "non_expert": primary_pattern.get("non_expert", ""),
        },
        "trophic_impact": {
            "primary_bottleneck": trophic.get("primary_bottleneck", ""),
            "cascade_impacts": cascade_impacts,
        },
        "reversibility": {
            "level": rc.get("reversibility", {}).get("level", ""),
            "label": rc.get("reversibility", {}).get("label", ""),
            "estimated_timeline": rc.get("reversibility", {}).get("estimated_timeline", ""),
        },
    }


def extract_questionnaire_data(questionnaire: Dict) -> Dict:
    """Extract key fields from questionnaire JSON."""
    q_data = questionnaire.get("questionnaire_data", {})
    completed_steps = questionnaire.get("completed_steps", [])

    # Basic info (step 1)
    step1 = q_data.get("step_1", {})
    basic = step1.get("basic", {})
    goals = step1.get("goals", {})

    # Digestive (step 2)
    step2 = q_data.get("step_2", {})

    # Medical (step 3)
    step3 = q_data.get("step_3", {})

    # Diet (step 4)
    step4 = q_data.get("step_4", {})

    # Lifestyle (step 5)
    step5 = q_data.get("step_5", {})

    # Supplements (step 6)
    step6 = q_data.get("step_6", {})

    # Health axes (step 7)
    step7 = q_data.get("step_7", {})

    return {
        "completion": {
            "completed_steps": completed_steps,
            "is_completed": questionnaire.get("is_completed", False),
            "completion_pct": len(completed_steps) / 9 * 100 if completed_steps else 0,
        },
        "demographics": {
            "age": basic.get("age"),
            "biological_sex": basic.get("biological_sex"),
            "height_cm": basic.get("height_cm"),
            "weight_kg": basic.get("weight_kg"),
            "country": basic.get("country_of_residence"),
        },
        "goals": {
            "ranked": goals.get("main_goals_ranked", []),
            "top_goal": goals.get("main_goals_ranked", [None])[0] if goals.get("main_goals_ranked") else None,
        },
        "digestive": {
            "stool_type": _parse_stool_type(step2.get("stool_pattern", "")),
            "bloating_frequency": step2.get("bloating_frequency", ""),
            "bloating_severity": step2.get("bloating_severity"),
            "digestive_satisfaction": step2.get("digestive_satisfaction"),
        },
        "medical": {
            "medications": step3.get("medications", []),
            "diagnoses": step3.get("diagnoses", []),
            "family_history": step3.get("family_history", []),
            "vitamin_deficiencies": step3.get("vitamin_deficiencies", []),
            "reported_deficiencies": step3.get("reported_deficiencies", []),
        },
        "lifestyle": {
            "stress_level": step5.get("overall_stress_level_1_10"),
            "sleep_quality": step5.get("sleep_quality_rating_1_10"),
            "sleep_duration": step5.get("sleep_duration_weeknights_hours"),
            "energy_level": step5.get("energy_level"),
            "exercise_frequency": step5.get("exercise_frequency"),
            "stress_symptoms": step5.get("stress_symptoms", []),
        },
        "current_supplements": step6.get("current_supplements", []),
        "diet": step4,
        "health_axes": step7,
        "food_triggers": _extract_food_triggers(step2, step7),
    }


def _parse_stool_type(stool_str: str) -> Optional[int]:
    """Extract Bristol stool type number from string like 'type_4'."""
    if not stool_str:
        return None
    import re
    match = re.search(r'(\d)', str(stool_str))
    if match:
        return int(match.group(1))
    return None


def _extract_food_triggers(step2: Dict, step7: Dict) -> Dict:
    """Extract food sensitivity/trigger information."""
    triggers = []

    # From step 2 (digestive)
    food_triggers = step2.get("food_triggers", [])
    if isinstance(food_triggers, list):
        triggers.extend(food_triggers)

    # From step 7 (health axes — food sensitivities)
    food_sens = step7.get("food_sensitivities", [])
    if isinstance(food_sens, list):
        triggers.extend(food_sens)

    return {
        "triggers": triggers,
        "count": len(set(triggers)),
    }


def parse_inputs(sample_dir: str) -> Dict:
    """
    Main entry point — load all inputs and produce unified dict.

    Args:
        sample_dir: Path to sample directory (e.g., analysis/nb1_2026_002/1421022165619/)

    Returns:
        Unified input dict with:
        - sample_id
        - microbiome (guilds, CLR ratios, vitamin signals, overall score)
        - questionnaire (demographics, goals, digestive, medical, lifestyle, etc.)
    """
    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name

    # Load raw data
    analysis = load_microbiome_analysis(str(sample_dir))
    questionnaire = load_questionnaire(str(sample_dir))

    # Extract structured data
    unified = {
        "sample_id": sample_id,
        "batch_id": sample_dir.parent.name,

        "microbiome": {
            "guilds": extract_guild_data(analysis),
            "clr_ratios": extract_clr_ratios(analysis),
            "vitamin_signals": extract_vitamin_signals(analysis),
            "overall_score": extract_overall_score(analysis),
            "root_causes": extract_root_causes(analysis),
            "guild_scenarios": analysis.get("guild_scenarios", []),
        },

        "questionnaire": extract_questionnaire_data(questionnaire),

        "_sources": {
            "microbiome_analysis": str(sample_dir),
            "questionnaire": str(sample_dir / "questionnaire"),
        }
    }

    return unified


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Parse sample inputs for formulation")
    parser.add_argument("--sample-dir", required=True, help="Path to sample directory")
    parser.add_argument("--output", help="Optional: save unified input JSON to file")
    args = parser.parse_args()

    result = parse_inputs(args.sample_dir)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Unified input saved to: {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
