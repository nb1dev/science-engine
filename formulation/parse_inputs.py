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


# ─── OTHER GOAL NORMALIZATION ─────────────────────────────────────────────────

# All valid goal keys from goal_to_health_claim.json
_KNOWN_GOAL_KEYS = [
    "boost_energy_reduce_fatigue",
    "increase_energy_reduce_fatigue",
    "improve_skin_health",
    "improve_skin",
    "improve_sleep",
    "improve_sleep_quality",
    "reduce_stress_anxiety",
    "improve_mood_reduce_anxiety",
    "improve_digestion",
    "improve_digestion_gut_comfort",
    "reduce_bloating_irregular_stool",
    "resolve_bloating_irregular_stool",
    "improve_focus_concentration",
    "strengthen_immune_system",
    "strengthen_immune_resilience",
    "longevity_healthy_aging",
    "general_wellness_healthy_aging",
    "weight_management",
    "improve_mood",
    "support_heart_health",
    "balance_blood_sugar",
    "microbiome_restoration",
]


def _normalize_other_goal(text: str, use_bedrock: bool = True) -> Optional[str]:
    """Map a client's free-text 'other' goal to the closest known goal key using LLM.

    The LLM is given the complete list of valid goal keys and instructed to always
    pick the closest match. Only returns None if Bedrock is unavailable or the text
    is entirely non-health-related.

    Args:
        text: The raw free-text from other_goal_details
        use_bedrock: Whether to attempt Bedrock LLM call

    Returns:
        A goal key string from _KNOWN_GOAL_KEYS, or None if resolution failed.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    if not use_bedrock:
        print(f"  ⚠️ Other goal '{text}' — Bedrock unavailable, cannot resolve to goal key")
        return None

    try:
        import boto3
        from botocore.config import Config

        config = Config(read_timeout=60, connect_timeout=10, retries={"max_attempts": 2})
        client = boto3.client("bedrock-runtime", region_name="eu-west-1", config=config)

        goal_keys_str = "\n".join(f"- {k}" for k in _KNOWN_GOAL_KEYS)

        system_prompt = (
            "You are mapping a client's free-text health goal to a standardized goal key. "
            "You MUST pick from the provided list. Always return the single closest match — "
            "never refuse. Only return 'unresolvable' if the text has absolutely no health meaning.\n\n"
            "CRITICAL DISAMBIGUATION RULES (apply these before choosing a key):\n"
            "1. weight_management → ONLY for EXPLICIT weight/body composition goals: 'lose weight', "
            "'slim down', 'reduce appetite', 'feel fuller', 'body fat'. "
            "NEVER use for 'metabolism' or 'metabolic health' alone — those belong to longevity_healthy_aging.\n"
            "2. longevity_healthy_aging or general_wellness_healthy_aging → metabolic optimisation, "
            "metabolic health, anti-aging, general wellness, vitality: "
            "'optimise metabolism', 'metabolic health', 'feel younger', 'healthy aging', 'longevity', "
            "'feel better overall', 'general health'.\n"
            "3. boost_energy_reduce_fatigue → ONLY when fatigue/tiredness is EXPLICIT: 'more energy', "
            "'less tired', 'reduce fatigue', 'exhausted'. "
            "Do NOT use for 'metabolism' alone.\n"
            "4. improve_focus_concentration → cognitive goals ONLY: 'focus', 'brain', 'sharp', "
            "'concentration', 'memory', 'mental clarity'. "
            "Do NOT confuse with energy goals.\n"
            "5. support_heart_health → ONLY explicit cardiovascular: 'heart', 'cholesterol', "
            "'blood pressure', 'cardiovascular'. "
            "Do NOT use for general wellness without heart mention.\n"
            "6. strengthen_immune_resilience → ONLY when immunity is explicitly mentioned: "
            "'immune', 'get sick less', 'infection', 'immunity'. "
            "Do NOT use for general wellness.\n"
            "7. If free text mentions multiple goals, pick the PRIMARY one (most prominent).\n\n"
            "Respond with ONLY a JSON object: {\"resolved_key\": \"<key>\", \"confidence\": \"high|medium|low\"}"
        )

        user_prompt = (
            f"VALID GOAL KEYS:\n{goal_keys_str}\n\n"
            f"CLIENT FREE-TEXT GOAL: \"{text}\"\n\n"
            f"Apply the disambiguation rules above, then map to the single closest goal key. "
            f"Return ONLY: {{\"resolved_key\": \"<key>\", \"confidence\": \"high|medium|low\"}}"
        )

        response = client.invoke_model(
            modelId="eu.anthropic.claude-sonnet-4-20250514-v1:0",
            contentType="application/json",
            accept="application/json",
            body=__import__("json").dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": 0.0,
            }),
        )

        result = __import__("json").loads(response["body"].read())
        response_text = result["content"][0]["text"].strip()

        # Parse JSON response
        import re as _re
        json_match = _re.search(r'\{.*\}', response_text, _re.DOTALL)
        if json_match:
            parsed = __import__("json").loads(json_match.group(0))
            resolved = parsed.get("resolved_key", "").strip().lower()
            if resolved and resolved != "unresolvable" and resolved in _KNOWN_GOAL_KEYS:
                confidence = parsed.get("confidence", "medium")
                print(f"  ✅ Other goal '{text}' → '{resolved}' (confidence: {confidence})")
                return resolved
            elif resolved == "unresolvable":
                print(f"  ⚠️ Other goal '{text}' → LLM returned unresolvable — will pass as raw context")
                return None

        print(f"  ⚠️ Other goal '{text}' → could not parse LLM response: {response_text[:100]}")
        return None

    except Exception as e:
        print(f"  ⚠️ Other goal normalization failed for '{text}': {e} — will pass as raw context")
        return None


def _resolve_goals(goals: Dict, use_bedrock: bool = True) -> Dict:
    """Resolve goals dict, replacing 'other' with the LLM-mapped goal key.

    Takes the raw goals block from step_1 of the questionnaire:
      {
        "main_goals_ranked": ["strengthen_immune_resilience", "other"],
        "other_goal_details": "Optimise metabolism"
      }

    Returns a resolved goals dict suitable for downstream pipeline use:
      {
        "ranked": ["strengthen_immune_resilience", "longevity_healthy_aging"],
        "top_goal": "strengthen_immune_resilience",
        "other_raw_text": "Optimise metabolism",
        "other_resolved_key": "longevity_healthy_aging"
      }

    If 'other' cannot be resolved (Bedrock unavailable, truly non-health text),
    'other' is dropped from ranked but the raw text is preserved under
    'other_raw_text' so it can be forwarded to LLM steps as freeform context.
    """
    raw_ranked = goals.get("main_goals_ranked", [])
    other_text = (goals.get("other_goal_details") or "").strip()

    resolved_ranked = []
    other_resolved_key = None

    for goal in raw_ranked:
        if goal.lower().strip() == "other":
            if other_text:
                mapped = _normalize_other_goal(other_text, use_bedrock=use_bedrock)
                if mapped:
                    resolved_ranked.append(mapped)
                    other_resolved_key = mapped
                else:
                    # Could not resolve — omit from ranked, preserve raw text for context
                    print(f"  ⚠️ 'other' goal could not be resolved to a goal key — raw text '{other_text}' preserved for context only")
            else:
                # "other" selected but no free-text provided — silently drop
                print(f"  ⚠️ 'other' goal selected but other_goal_details is empty — skipping")
        else:
            resolved_ranked.append(goal)

    return {
        "ranked": resolved_ranked,
        "top_goal": resolved_ranked[0] if resolved_ranked else None,
        "other_raw_text": other_text if other_text else None,
        "other_resolved_key": other_resolved_key,
    }


def extract_questionnaire_data(questionnaire: Dict, use_bedrock: bool = True) -> Dict:
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

    # ── Demographics + computed BMI ─────────────────────────────────────────
    height_cm = basic.get("height_cm")
    weight_kg = basic.get("weight_kg")
    bmi = None
    if height_cm and weight_kg and height_cm > 0:
        bmi = round(weight_kg / ((height_cm / 100) ** 2), 1)

    # ── BMI context — athletic override ──────────────────────────────────────
    # Standard BMI thresholds misclassify lean, highly muscular athletes as
    # "overweight". This derived field passes contextual information to the
    # clinical analyzer so it does not flag fit clients incorrectly.
    # Rule: BMI 25–29.9 + regularly active (not just elite athletes)
    # → override label to avoid misclassifying well-muscled clients as overweight.
    # Threshold lowered to: vigorous ≥3×/week ≥30min OR moderate ≥4×/week ≥45min
    exercise_step5 = step5  # step5 is available in this scope
    _vigorous_days = exercise_step5.get("vigorous_days_per_week") or 0
    _vigorous_mins = exercise_step5.get("vigorous_minutes_per_session") or 0
    _moderate_days = exercise_step5.get("moderate_days_per_week") or 0
    _moderate_mins = exercise_step5.get("moderate_minutes_per_session") or 0
    _is_highly_athletic = (
        (_vigorous_days >= 3 and _vigorous_mins >= 30) or
        (_moderate_days >= 4 and _moderate_mins >= 45)
    )
    bmi_context = None
    if bmi is not None:
        if 25.0 <= bmi < 30.0 and _is_highly_athletic:
            bmi_context = (
                f"BMI {bmi} — likely elevated due to lean muscle mass "
                f"(highly athletic: {_vigorous_days}× vigorous/week, "
                f"{_vigorous_mins}min/session). "
                f"Do NOT classify as overweight — muscle mass inflation expected."
            )
        elif bmi >= 30.0:
            bmi_context = f"BMI {bmi} — obese range"
        elif 25.0 <= bmi < 30.0:
            bmi_context = f"BMI {bmi} — above standard range (individual context applies — verify body composition)"
        else:
            bmi_context = f"BMI {bmi} — normal range"

    # ── Medications — handle BOTH formats ────────────────────────────────
    # Newer questionnaires (batch 008+): all in other_medications array
    # Older questionnaires (batch 001-007): structured fields (statin_has, ppi_has, etc.)
    other_medications = step3.get("other_medications", []) or []
    
    # Normalise array-based medications
    medications_normalised = []
    for m in other_medications:
        if isinstance(m, dict):
            med_name = m.get("name", "").strip()
            if med_name:  # Only add if name is not empty
                medications_normalised.append({
                    "name": med_name,
                    "dosage": m.get("dosage", ""),
                    "how_long": m.get("how_long", ""),
                })
        elif isinstance(m, str) and m.strip():
            medications_normalised.append({"name": m.strip(), "dosage": "", "how_long": ""})
    
    # ── Extract from structured fields (backward compatibility for older questionnaires) ──
    # Map of structured field → (has_field, which_field, dosage_field, duration_field)
    structured_meds = [
        ("statin_has", "statin_which", "statin_dosage", "statin_how_long"),
        ("ppi_has", "ppi_which", "ppi_dosage", "ppi_how_long"),
        ("ssri_snri_has", "ssri_snri_drug", "ssri_snri_dosage", "ssri_snri_how_long"),
        ("metformin_has", "metformin_name", "metformin_dosage", "metformin_how_long"),
        ("nsaid_use", "nsaid_which", "nsaid_typical_dose", "nsaid_how_long"),
        ("corticosteroids_has", "corticosteroids_which", "corticosteroids_dosage", "corticosteroids_how_long"),
        ("immunosuppressants_has", "immunosuppressants_drugs", None, "immunosuppressants_how_long"),
        ("hrt_has", "hrt_type", "hrt_dosage", "hrt_how_long"),
        ("hormonal_contraception_has", "hormonal_contraception_brand", None, "hormonal_contraception_how_long"),
    ]
    
    for has_field, which_field, dosage_field, duration_field in structured_meds:
        has_val = step3.get(has_field, "no")
        # Check if medication is present (has_field == "yes" or nsaid_use == "yes")
        if has_val and str(has_val).lower() == "yes":
            med_name = step3.get(which_field, "").strip()
            if med_name and med_name.lower() not in ("none", "n/a", ""):
                # Check if already in array to avoid duplicates
                already_present = any(
                    m.get("name", "").lower() == med_name.lower() 
                    for m in medications_normalised
                )
                if not already_present:
                    medications_normalised.append({
                        "name": med_name,
                        "dosage": step3.get(dosage_field, "") if dosage_field else "",
                        "how_long": step3.get(duration_field, "") if duration_field else "",
                    })

    # ── Skin concerns (step 7) ────────────────────────────────────────────────
    skin_concerns_raw = step7.get("skin_concerns", []) or []
    skin_persistence = step7.get("skin_persistence", "")
    skin_change_patterns = step7.get("skin_change_patterns", "")
    skin_frequency = step7.get("skin_issues_frequency", "")

    # ── Exercise detail (step 5) ─────────────────────────────────────────────
    exercise_detail = {
        "types": step5.get("exercise_types", []),
        "moderate_days_per_week": step5.get("moderate_days_per_week"),
        "moderate_minutes_per_session": step5.get("moderate_minutes_per_session"),
        "vigorous_days_per_week": step5.get("vigorous_days_per_week"),
        "vigorous_minutes_per_session": step5.get("vigorous_minutes_per_session"),
        "avg_daily_steps": step5.get("average_daily_step_count", ""),
        "hours_sitting_per_day": step5.get("hours_sitting_per_day"),
        "resistance_training": step5.get("resistance_strength_training", ""),
    }

    return {
        "completion": {
            "completed_steps": completed_steps,
            "is_completed": questionnaire.get("is_completed", False),
            "completion_pct": len(completed_steps) / 9 * 100 if completed_steps else 0,
        },
        "demographics": {
            "age": basic.get("age"),
            "biological_sex": basic.get("biological_sex"),
            "height_cm": height_cm,
            "weight_kg": weight_kg,
            "bmi": bmi,
            "bmi_context": bmi_context,
            "country": basic.get("country_of_residence"),
            "occupation_environment": basic.get("occupation_work_environment", ""),
        },
        "goals": _resolve_goals(goals, use_bedrock),
        "digestive": {
            "stool_type": _parse_stool_type(step2.get("stool_pattern", "")),
            "bloating_frequency": step2.get("bloating_frequency", ""),
            "bloating_severity": step2.get("bloating_severity"),
            "bloating_when": step2.get("bloating_when", []),
            "digestive_satisfaction": step2.get("digestive_satisfaction"),
            "abdominal_pain_severity": step2.get("abdominal_pain_severity"),
            "abdominal_pain_frequency": step2.get("abdominal_pain_frequency", ""),
            "abdominal_pain_character": step2.get("abdominal_pain_character", []),
            "digestive_symptoms_with_stress": step5.get("digestive_symptoms_with_stress", ""),
        },
        "medical": {
            # FIXED: use other_medications (not the always-empty medications field)
            "medications": medications_normalised,
            "diagnoses": step3.get("diagnoses", []),
            "family_history": step3.get("family_history", {}),
            "vitamin_deficiencies": step3.get("vitamin_deficiencies", []),
            "reported_deficiencies": step3.get("reported_deficiencies", []),
            "drug_allergies": step3.get("drug_allergies_details", ""),
            "drug_allergies_has": step3.get("drug_allergies_has", "no"),
            "nsaid_use": step3.get("nsaid_use", ""),
            "nsaid_which": step3.get("nsaid_which", ""),
            "skin_concerns": skin_concerns_raw,
            "skin_persistence": skin_persistence,
            "skin_change_patterns": skin_change_patterns,
            "skin_issues_frequency": skin_frequency,
            "uti_per_year": step7.get("uti_per_year", ""),
            "colds_per_year": step7.get("colds_per_year", ""),
            "infection_recovery": step3.get("infection_recovery", ""),
            "gut_brain_symptoms": step7.get("gut_brain_symptoms", []),
            "colon_symptoms": step7.get("colon_symptoms", []),
            "motility_details": step7.get("motility_details", ""),
            "motility_symptoms": step7.get("motility_symptoms", []),
            "previous_supplements": step6.get("previous_products_tried", ""),
            "previous_supplement_effect": step6.get("previous_effect", ""),
            "previous_supplement_notes": step6.get("previous_effect_notes", ""),
        },
        "lifestyle": {
            "stress_level": step5.get("overall_stress_level_1_10"),
            "sleep_quality": step5.get("sleep_quality_rating_1_10"),
            "sleep_duration": step5.get("sleep_duration_weeknights_hours"),
            "sleep_issues": step5.get("sleep_issues", []),
            "energy_level": step5.get("typical_energy_level", step5.get("energy_level")),
            "mental_clarity": step5.get("mental_clarity", ""),
            "mood_stability": step5.get("mood_stability", ""),
            "stress_recovery": step5.get("stress_recovery", ""),
            "stress_symptoms": step5.get("stress_symptoms", []),
            "digestive_symptoms_with_stress": step5.get("digestive_symptoms_with_stress", ""),
            "exercise_detail": exercise_detail,
            "weight_kg": weight_kg,
        },
        "current_supplements": step6.get("current_supplements", step6.get("supplements", [])),
        "diet": step4,
        "health_axes": step7,
        "food_triggers": _extract_food_triggers(step2, step4, step7),
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


def _extract_food_triggers(step2: Dict, step4: Dict, step7: Dict) -> Dict:
    """Extract food sensitivity/trigger information from all relevant steps."""
    triggers = []

    # From step 2 (digestive — trigger_foods field if present)
    food_triggers_2 = step2.get("food_triggers", [])
    if isinstance(food_triggers_2, list):
        triggers.extend(food_triggers_2)

    # From step 4 (diet — the canonical trigger_foods list)
    food_triggers_4 = step4.get("trigger_foods", [])
    if isinstance(food_triggers_4, list):
        triggers.extend(food_triggers_4)

    # From step 7 (health axes — food sensitivities / colon triggers)
    food_sens = step7.get("food_sensitivities", [])
    if isinstance(food_sens, list):
        triggers.extend(food_sens)

    # Sensitivity avoids (step 4 — e.g. lactose)
    sens_avoids = step4.get("sensitivity_avoids", [])
    if isinstance(sens_avoids, list):
        triggers.extend(sens_avoids)

    # Deduplicate while preserving order
    seen = set()
    unique_triggers = []
    for t in triggers:
        if t and t not in seen:
            seen.add(t)
            unique_triggers.append(t)

    # Also capture raw colon trigger text for LLM context
    colon_triggers_text = step7.get("colon_triggers_foods", "")

    return {
        "triggers": unique_triggers,
        "count": len(unique_triggers),
        "colon_triggers_text": colon_triggers_text,
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
