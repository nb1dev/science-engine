#!/usr/bin/env python3
"""
Stage 9: Output — Assemble master JSON and all derivative files.

Input:  PipelineContext (fully populated)
Output: Master formulation dict + all files saved to disk
"""

import json
from pathlib import Path
from typing import Dict

from ..models import PipelineContext
from formulation.platform_mapping import build_platform_json, build_decision_trace, build_manufacturing_recipe, build_component_rationale
from shared.guild_priority import build_priority_list
from shared.formatting import sleep_label as _sleep_label

KB_DIR = Path(__file__).parent.parent / "knowledge_base"

VITAMIN_PRODUCTION_DISCLAIMER = (
    "Your microbiome composition suggests you have {status} populations of bacteria "
    "associated with {vitamin} production. However, this tells us about potential — "
    "not actual vitamin output. To know whether you're getting enough {vitamin}, "
    "a blood test is the only reliable measure."
)


def run(ctx: PipelineContext) -> Dict:
    """Assemble master JSON and save all output files."""
    print("\n─── G. OUTPUT ──────────────────────────────────────────────")

    sample_dir = Path(ctx.sample_dir)
    formulation = ctx.formulation

    # Remove pipeline_version from metadata (internal only)
    clean_metadata = {k: v for k, v in formulation["metadata"].items() if k != "pipeline_version"}

    # Build canonical priority interventions
    priority_interventions = build_priority_list(ctx.guilds)

    # Questionnaire coverage assessment
    q_coverage = _assess_questionnaire_coverage(ctx.unified_input)

    # Build master JSON
    master = {
        "metadata": clean_metadata,
        "questionnaire_coverage": q_coverage,
        "priority_interventions": priority_interventions,
        "input_summary": _build_input_summary(ctx),
        "decisions": {
            "mix_selection": ctx.mix,
            "supplement_selection": ctx.supplements,
            "prebiotic_design": ctx.prebiotics,
            "rule_outputs": {
                "sensitivity": ctx.rule_outputs["sensitivity"],
                "health_claims": ctx.rule_outputs["health_claims"],
                "therapeutic_triggers": ctx.rule_outputs["therapeutic_triggers"],
                "prebiotic_range": ctx.rule_outputs["prebiotic_range"],
                "magnesium": ctx.rule_outputs["magnesium"],
                "softgel": ctx.rule_outputs.get("softgel", {}),
                "sleep_supplements": ctx.rule_outputs.get("sleep_supplements", {}),
                "goal_triggered_supplements": ctx.rule_outputs.get("goal_triggered_supplements", {}),
                "timing": ctx.rule_outputs["timing"],
            },
        },
        "formulation": formulation,
        "ecological_rationale": ctx.ecological_rationale,
        "input_narratives": ctx.input_narratives,
        "component_registry": ctx.component_registry,
        "clinical_summary": ctx.clinical_summary,
        "medication_rules": {
            "timing_override": ctx.medication.timing_override,
            "substances_removed": list(ctx.medication.substances_to_remove),
            "magnesium_removed": ctx.medication.magnesium_removed,
            "clinical_flags": ctx.medication.clinical_flags,
            "evidence_flags": ctx.medication.elicit_evidence_result.get("evidence_flags", []),
        },
        "vitamin_production_disclaimer": VITAMIN_PRODUCTION_DISCLAIMER,
        "version": 1,
        "revision_history": [],
    }

    # ── Medication timing override ───────────────────────────────────────
    if ctx.medication.timing_override:
        from formulation.apply_medication_timing_override import apply_timing_override
        master = apply_timing_override(master, ctx.medication.timing_override)
        formulation = master["formulation"]

    # ── Build platform JSON ──────────────────────────────────────────────
    platform = build_platform_json(master)

    # ── Save all files ───────────────────────────────────────────────────
    output_dir = sample_dir / "reports" / "reports_json"
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_json(output_dir / f"formulation_master_{ctx.sample_id}.json", master)
    _save_json(output_dir / f"formulation_platform_{ctx.sample_id}.json", platform)

    trace = build_decision_trace(master, trace_events=ctx.trace_events)
    _save_json(output_dir / f"decision_trace_{ctx.sample_id}.json", trace)

    recipe = build_manufacturing_recipe(master)
    _save_json(output_dir / f"manufacturing_recipe_{ctx.sample_id}.json", recipe)

    rationale = build_component_rationale(master)
    _save_json(output_dir / f"component_rationale_{ctx.sample_id}.json", rationale)

    print(f"\n  📄 Master:    formulation_master_{ctx.sample_id}.json")
    print(f"  📄 Platform:  formulation_platform_{ctx.sample_id}.json")
    print(f"  📄 Trace:     decision_trace_{ctx.sample_id}.json")
    print(f"  📄 Recipe:    manufacturing_recipe_{ctx.sample_id}.json")
    print(f"  📄 Rationale: component_rationale_{ctx.sample_id}.json")

    # ── Generate dashboards ──────────────────────────────────────────────
    try:
        from formulation.generate_dashboards import generate_dashboards
        generate_dashboards(ctx.sample_id, str(output_dir), str(sample_dir))
    except Exception as e:
        print(f"  ⚠️ Dashboard generation failed: {e}")

    # ── Deterministic validator (authoritative quality gate) ─────────────
    try:
        from formulation.formulation_validator import validate_formulation
        validation_report = validate_formulation(str(sample_dir), save_report=True)
    except Exception as e:
        print(f"  ⚠️ Deterministic validator failed: {e}")

    return master


def _save_json(path: Path, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _build_input_summary(ctx: PipelineContext) -> Dict:
    """Build input_summary block for master JSON."""
    unified = ctx.unified_input
    mb = unified["microbiome"]
    q = unified["questionnaire"]

    return {
        "microbiome_driven": {
            "guild_status": {k: v["status"] for k, v in mb["guilds"].items()},
            "guild_details": {
                k: {"name": v.get("name", k), "abundance_pct": v.get("abundance_pct", 0),
                     "status": v.get("status", ""), "priority_level": v.get("priority_level", ""),
                     "clr": v.get("clr")}
                for k, v in mb["guilds"].items()
            },
            "clr_ratios": mb["clr_ratios"],
            "vitamin_signals": {k: v["status"] for k, v in mb["vitamin_signals"].items()},
            "overall_score": mb["overall_score"],
            "root_causes": mb.get("root_causes", {}),
        },
        "questionnaire_driven": {
            "biological_sex": q["demographics"].get("biological_sex", "N/A"),
            "age": q["demographics"].get("age", "N/A"),
            "diet": q.get("diet", {}).get("diet_pattern", "None") if isinstance(q.get("diet"), dict) else (q.get("diet") or "None"),
            "goals_ranked": q["goals"]["ranked"],
            "other_raw_text": q["goals"].get("other_raw_text"),
            "other_resolved_key": q["goals"].get("other_resolved_key"),
            "stress_level": q["lifestyle"]["stress_level"],
            "sleep_quality": q["lifestyle"]["sleep_quality"],
            "sleep_quality_label": _sleep_label(q["lifestyle"]["sleep_quality"]),
            "bloating_severity": q["digestive"]["bloating_severity"],
            "sensitivity_classification": ctx.rule_outputs["sensitivity"]["classification"],
            "reported_deficiencies": ctx.rule_outputs["therapeutic_triggers"]["reported_deficiencies"],
        },
    }


def _assess_questionnaire_coverage(unified_input: Dict) -> Dict:
    """Assess questionnaire completeness."""
    q = unified_input.get("questionnaire", {})
    completion = q.get("completion", {})
    completion_pct = completion.get("completion_pct", 0)

    missing = []
    if q.get("lifestyle", {}).get("stress_level") is None:
        missing.append("Stress level not reported")
    if q.get("lifestyle", {}).get("sleep_quality") is None:
        missing.append("Sleep quality not reported")
    if q.get("digestive", {}).get("bloating_severity") is None:
        missing.append("Bloating severity not reported")
    if not q.get("goals", {}).get("ranked"):
        missing.append("Health goals not ranked")

    if completion_pct == 0:
        level = "MINIMAL"
    elif completion_pct < 50:
        level = "LOW"
    elif completion_pct < 80:
        level = "MODERATE"
    else:
        level = "GOOD"

    return {
        "completion_pct": completion_pct,
        "completed_steps": completion.get("completed_steps", []),
        "coverage_level": level,
        "summary": f"Questionnaire {completion_pct:.0f}% complete",
        "missing_data_areas": missing,
    }
