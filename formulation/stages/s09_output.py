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
from formulation.rules_engine import assess_capsule_underfill
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
            "exclusion_reasons": ctx.medication.exclusion_reasons,
        },
        "vitamin_production_disclaimer": VITAMIN_PRODUCTION_DISCLAIMER,
        "version": 1,
        "revision_history": [],
    }

    # ── SIBO clinical warning ────────────────────────────────────────────
    # If the SIBO zero-FODMAP override is active, surface a concise warning
    # listing the clinical factors that triggered it. Appears in platform JSON,
    # dashboard, and any downstream review tooling that reads metadata.warnings.
    _pb_design = master.get("decisions", {}).get("prebiotic_design", {})
    _sibo_assessment = _pb_design.get("sibo_assessment", {})
    if _sibo_assessment.get("sibo_suspected", False):
        _criteria = _sibo_assessment.get("criteria_met", [])
        _factors = ", ".join(_criteria) if _criteria else "clinical profile"
        _sibo_warning = f"SIBO suspected: {_factors}"
        _meta_warnings = master.get("formulation", {}).get("metadata", {}).setdefault("warnings", [])
        if _sibo_warning not in _meta_warnings:
            _meta_warnings.append(_sibo_warning)

    # ── Medication timing override ───────────────────────────────────────
    if ctx.medication.timing_override:
        from formulation.apply_medication_timing_override import apply_timing_override, print_timing_override_summary
        master = apply_timing_override(master, ctx.medication.timing_override)
        formulation = master["formulation"]
        print_timing_override_summary(ctx.medication.timing_override, formulation)

    # ── Build platform JSON ──────────────────────────────────────────────
    platform = build_platform_json(master)

    # ── Save all files ───────────────────────────────────────────────────
    output_dir = sample_dir / "reports" / "reports_json"
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_json(output_dir / f"formulation_master_{ctx.sample_id}.json", master)
    _save_json(output_dir / f"formulation_platform_{ctx.sample_id}.json", platform)

    trace = build_decision_trace(master, trace_events=ctx.trace_events, sample_dir=str(sample_dir))
    _save_json(output_dir / f"decision_trace_{ctx.sample_id}.json", trace)

    recipe = build_manufacturing_recipe(master)

    # ── Capsule underfill companion check ────────────────────────────────
    # Runs after recipe is built. Proposes companions for any capsule with
    # fill < 10% capacity. Results stored in recipe for downstream review.
    try:
        active_claims = ctx.rule_outputs.get("health_claims", {}).get("supplement_claims", [])
        existing_comps = [
            comp["substance"]
            for comp in ctx.component_registry
        ] if ctx.component_registry else []
        removed_subs = ctx.medication.substances_to_remove if ctx.medication else set()

        underfill_proposals = assess_capsule_underfill(
            recipe_units=recipe.get("units", []),
            active_health_claims=active_claims,
            substances_to_remove=removed_subs,
            existing_components=existing_comps,
        )
        if underfill_proposals:
            recipe["underfill_companion_proposals"] = underfill_proposals
            print(f"  ℹ️  Underfill companions proposed for {len(underfill_proposals)} capsule(s)")
            for p in underfill_proposals:
                print(f"      Unit {p['unit_number']} ({p['unit_label']}): "
                      f"add {p['companion_substance']} {p['companion_dose_mg']}mg")
    except Exception as e:
        print(f"  ⚠️ Underfill check failed: {e}")

    _save_json(output_dir / f"manufacturing_recipe_{ctx.sample_id}.json", recipe)

    # ── Render manufacturing recipe to MD + PDF ──────────────────────────
    try:
        from formulation.recipe_renderer import render_and_save as _render_recipe
        q_coverage = master.get("questionnaire_coverage")
        _render_recipe(recipe, ctx.sample_id, str(sample_dir), q_coverage=q_coverage)
    except Exception as e:
        print(f"  ⚠️ Recipe MD/PDF render failed: {e}")

    rationale = build_component_rationale(master)
    _save_json(output_dir / f"component_rationale_{ctx.sample_id}.json", rationale)

    print(f"\n  📄 Master:    formulation_master_{ctx.sample_id}.json")
    print(f"  📄 Platform:  formulation_platform_{ctx.sample_id}.json")
    print(f"  📄 Trace:     decision_trace_{ctx.sample_id}.json")
    print(f"  📄 Recipe:    manufacturing_recipe_{ctx.sample_id}.json")
    print(f"  📄 Rationale: component_rationale_{ctx.sample_id}.json")

    # ── Generate dashboards ──────────────────────────────────────────────
    try:
        from formulation.dashboard_renderer import generate_dashboards
        generate_dashboards(ctx.sample_id, str(output_dir), str(sample_dir))
    except Exception as e:
        print(f"  ⚠️ Dashboard generation failed: {e}")

    # ── Evening label patching (only when timing override is active) ─────
    if ctx.medication.timing_override:
        try:
            from formulation.generate_formulation_evening import EVENING_LABEL_PATCHES, EVENING_HTML_PATCHES
            html_dir = sample_dir / "reports" / "reports_html"
            _html_patch_count = 0
            if html_dir.exists():
                for html_file in html_dir.glob("*.html"):
                    try:
                        html_content = html_file.read_text(encoding='utf-8')
                        patched = html_content
                        for old, new in EVENING_LABEL_PATCHES + EVENING_HTML_PATCHES:
                            patched = patched.replace(old, new)
                        if patched != html_content:
                            html_file.write_text(patched, encoding='utf-8')
                            _html_patch_count += 1
                    except Exception as _html_err:
                        print(f"  ⚠️ HTML evening patch failed for {html_file.name}: {_html_err}")
            if _html_patch_count > 0:
                print(f"  ✅ Patched {_html_patch_count} dashboard HTML file(s) with evening labels")
        except Exception as e:
            print(f"  ⚠️ Evening label patching failed: {e}")

    # ── Deterministic validator (authoritative quality gate) ─────────────
    validation_report = None
    try:
        from formulation.formulation_validator import validate_formulation
        validation_report = validate_formulation(str(sample_dir), save_report=True)
        # Surface validator results in pipeline log
        if validation_report:
            # Keys from formulation_validator.py: overall_status (not "status"),
            # checks (not "issues") — filtered to FAIL status for display
            v_status = validation_report.get("overall_status", "?")
            v_issues = [c for c in validation_report.get("checks", []) if c.get("status") == "FAIL"]
            v_icon = "✅" if v_status == "PASS" else "❌"
            print(f"  {v_icon} Validator: {v_status} ({len(v_issues)} issue(s))")
            for issue in v_issues[:5]:  # Show up to 5 issues
                print(f"      → [{issue.get('severity', '?')}] {issue.get('check', issue.get('message', '?'))}: {issue.get('actual', '')}")
            if len(v_issues) > 5:
                print(f"      ... and {len(v_issues) - 5} more")
            # Add to master JSON for downstream consumption
            master["validation_report"] = validation_report
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
            "medications": q["medical"].get("medications", []),
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
