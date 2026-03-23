#!/usr/bin/env python3
"""
Platform Mapping — Transform master formulation JSON into platform-ready API payload.

Maps the detailed master formulation to a simplified, standardized JSON
structure for the client-facing platform where experts can review formulations.

The platform JSON is designed to:
  - Display formulation overview to experts (nutritionists, clinicians)
  - Show what's informed by microbiome vs questionnaire vs both
  - Enable expert revision workflow
  - Connect to the microbiome report action_plan "how" field
"""

import json
from typing import Dict, List, Optional

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'shared'))
from formatting import format_dose as _format_dose
from guild_priority import GUILD_CLIENT_NAMES as _GUILD_CLIENT_NAMES


def _get_unit_timing(formulation: Dict, delivery_key: str, default: str = "morning") -> str:
    """Read actual timing from formulation delivery format dict.
    
    Respects medication timing override (apply_timing_override rewrites
    format.timing to "evening (with dinner meal)" for all units).
    Falls back to default if the key doesn't exist or is None.
    """
    unit = formulation.get(delivery_key)
    if unit is None:
        return default
    fmt = unit.get("format") or {}
    return fmt.get("timing", default)


def _get_unit_label(formulation: Dict, delivery_key: str, default: str) -> str:
    """Read actual label from formulation delivery format dict.
    
    Respects medication timing override (apply_timing_override rewrites
    format.label from "Morning Wellness Capsule" to "Evening Wellness Capsule").
    Falls back to default if the key doesn't exist, is None, or label is missing.
    """
    unit = formulation.get(delivery_key)
    if unit is None:
        return default
    fmt = unit.get("format") or {}
    return fmt.get("label", default)


def _evening_capsule_label(components: list) -> str:
    """Universal label for the evening capsule — contents visible in contents_summary.
    
    Previously used 5 different content-aware names (Stress & Relaxation, Sleep Support,
    Sleep & Relaxation, Immune Support, Evening Wellness) which caused packaging/labeling
    inconsistency. Unified to a single label as of April 2026.
    """
    return "Evening Wellness Capsule"


def _enrich_priority_interventions(priority_interventions: list) -> list:
    """Add client-facing guild_display_name to each priority intervention.

    The formulation master stores internal guild names (e.g., 'Butyrate Producers')
    while the microbiome platform uses client-facing names (e.g., 'Gut-Lining Energy Producers').
    This enrichment adds guild_display_name so the platform frontend can show consistent
    names across both the health report and formulation views.

    Uses GUILD_CLIENT_NAMES from shared/guild_priority.py — single source of truth.
    """
    enriched = []
    for pi in priority_interventions:
        entry = dict(pi)  # shallow copy
        guild_name = pi.get('guild_name', '')
        guild_key = pi.get('guild_key', '')
        # Try guild_name first, then guild_key for client name lookup
        display = _GUILD_CLIENT_NAMES.get(guild_name) or _GUILD_CLIENT_NAMES.get(guild_key) or guild_name
        entry['guild_display_name'] = display
        enriched.append(entry)
    return enriched


def build_platform_json(master: Dict) -> Dict:
    """
    Transform master formulation JSON → platform-ready JSON.

    Args:
        master: Complete master formulation dict (from generate_formulation.py)

    Returns:
        Simplified platform JSON ready for API consumption
    """
    metadata = master.get("metadata", {})
    input_summary = master.get("input_summary", {})
    decisions = master.get("decisions", {})
    formulation = master.get("formulation", {})
    protocol = formulation.get("protocol_summary", {})

    mix = decisions.get("mix_selection", {})
    supplements = decisions.get("supplement_selection", {})
    prebiotics = decisions.get("prebiotic_design", {})
    rule_outputs = decisions.get("rule_outputs", {})

    return {
        "metadata": {
            "sample_id": metadata.get("sample_id"),
            "generated_at": metadata.get("generated_at"),
            "schema_version": "1.0",
            "pipeline_version": metadata.get("pipeline_version", "1.0.0"),
            "validation_status": metadata.get("validation_status"),
            "warnings": metadata.get("warnings", []),
            "formulation_version": master.get("version", 1),
        },

        "overview": {
            "protocol_duration_weeks": 16,
            "total_daily_units": protocol.get("total_daily_units"),
            "total_daily_weight_g": protocol.get("total_daily_weight_g"),
            "morning_solid_units": protocol.get("morning_solid_units"),
            "morning_drinks": protocol.get("morning_drinks"),
            "evening_solid_units": protocol.get("evening_solid_units"),
            "sensitivity_classification": rule_outputs.get("sensitivity", {}).get("classification"),
            "barrier_support_active": protocol.get("barrier_support_active", False),
        },

        "synbiotic_mix": {
            "mix_id": mix.get("mix_id"),
            "mix_name": mix.get("mix_name"),
            "primary_trigger": mix.get("primary_trigger"),
            "clr_context": mix.get("clr_context"),
            "confidence": mix.get("confidence"),
            "total_cfu_billions": mix.get("total_cfu_billions"),
            "informed_by": "microbiome",
            "strains": [
                {
                    "name": s.get("name"),
                    "cfu_billions": s.get("cfu_billions"),
                    "role": s.get("role"),
                }
                for s in mix.get("strains", [])
            ],
        },

        "prebiotics": {
            "strategy": prebiotics.get("strategy"),
            "total_grams": prebiotics.get("total_grams"),
            "total_fodmap_grams": prebiotics.get("total_fodmap_grams"),
            "informed_by": "both",
            "contradictions": prebiotics.get("contradictions_found", []),
            "overrides": prebiotics.get("overrides_applied", []),
            "components": [
                {
                    "name": p.get("substance"),
                    "dose_g": p.get("dose_g"),
                    "fodmap": p.get("fodmap", False),
                    "rationale": p.get("rationale"),
                }
                for p in prebiotics.get("prebiotics", [])
            ],
        },

        "vitamins_minerals": [
            {
                "name": vm.get("substance"),
                "dose": vm.get("dose"),
                "therapeutic": vm.get("therapeutic", False),
                "standard_dose": vm.get("standard_dose"),
                "delivery": vm.get("delivery"),
                "informed_by": vm.get("informed_by"),
                "rationale": vm.get("rationale"),
                "interaction_note": vm.get("interaction_note"),
            }
            for vm in supplements.get("vitamins_minerals", [])
        ],

        "supplements": [
            {
                "name": s.get("substance"),
                "dose_mg": s.get("dose_mg"),
                "health_claim": s.get("health_claim"),
                "rank": s.get("rank"),
                "delivery": s.get("delivery"),
                "informed_by": s.get("informed_by", "questionnaire"),
                "rationale": s.get("rationale"),
            }
            for s in supplements.get("supplements", [])
        ],

        "omega3": supplements.get("omega3", {}),

        "delivery_units": _build_delivery_summary(formulation),

        "existing_supplements": {
            "advice": supplements.get("existing_supplements_advice", []),
        },

        "timing": {
            "evening_capsule_needed": rule_outputs.get("timing", {}).get("evening_capsule_needed", False),
            "evening_components": rule_outputs.get("timing", {}).get("evening_components", []),
            "assignments": {
                k: {"timing": v.get("timing"), "reason": v.get("reason")}
                for k, v in rule_outputs.get("timing", {}).get("timing_assignments", {}).items()
            },
        },

        "evidence_tags": _build_evidence_tags(decisions, input_summary),

        "priority_interventions": _enrich_priority_interventions(master.get("priority_interventions", [])),
        "vitamin_production_disclaimer": master.get("vitamin_production_disclaimer", ""),

        "input_summary": input_summary,
    }


def _build_delivery_summary(formulation: Dict) -> Dict:
    """Build simplified delivery unit summary for platform display.
    
    Supports both v2 (sachet) and v3 (powder jar + pooled capsules) architectures.
    """
    result = {"morning": [], "evening": []}

    # Probiotic capsule
    capsule = formulation.get("delivery_format_1_probiotic_capsule", {})
    if capsule:
        totals = capsule.get("totals", {})
        result["morning"].append({
            "type": "hard_capsule",
            "label": "Probiotic Capsule",
            "count": capsule.get("format", {}).get("daily_count", 1),
            "contents_summary": f"{totals.get('total_cfu_billions', 0)}B CFU probiotics",
            "weight_mg": totals.get("total_weight_mg"),
            "validation": totals.get("validation"),
        })

    # Softgels
    softgels = formulation.get("delivery_format_2_omega_softgels", {})
    if softgels:
        totals = softgels.get("totals", {})
        components = softgels.get("components_per_softgel", [])
        contents = ", ".join(c.get("substance", "") for c in components)
        result["morning"].append({
            "type": "softgel",
            "label": "Omega Softgels",
            "count": softgels.get("format", {}).get("daily_count", 2),
            "contents_summary": contents,
            "weight_per_unit_mg": totals.get("weight_per_softgel_mg"),
            "validation": totals.get("validation"),
        })

    # v3: Powder Jar (replaces sachet)
    jar = formulation.get("delivery_format_3_powder_jar")
    if jar:
        totals = jar.get("totals", {})
        phased = totals.get("phased_dosing", {})
        pb_items = ", ".join(p.get("substance", "") for p in jar.get("prebiotics", {}).get("components", []))
        bot_items = ", ".join(b.get("substance", "") for b in jar.get("botanicals", {}).get("components", []))
        contents = pb_items + (f" + {bot_items}" if bot_items else "")
        summary = {
            "type": "jar",
            "label": "Prebiotic & Botanical Powder Jar",
            "count": 1,
            "contents_summary": contents or f"{totals.get('prebiotic_total_g', 0)}g prebiotics",
            "weight_g": totals.get("total_weight_g"),
            "within_daily_target": totals.get("within_daily_target", True),
            "validation": totals.get("validation", "PASS"),
        }
        if phased:
            summary["phased_dosing"] = {
                "weeks_1_2_g": phased.get("weeks_1_2_g"),
                "weeks_3_plus_g": phased.get("weeks_3_plus_g"),
                "instruction": phased.get("instruction", ""),
            }
        result["morning"].append(summary)
    else:
        # v2 fallback: Daily Sachet
        sachet = formulation.get("delivery_format_3_daily_sachet", {})
        if sachet:
            totals = sachet.get("totals", {})
            result["morning"].append({
                "type": "sachet",
                "label": "Daily Sachet",
                "count": 1,
                "contents_summary": f"{totals.get('prebiotic_total_g', 0)}g prebiotics + vitamins + supplements",
                "weight_g": totals.get("total_weight_g"),
                "validation": totals.get("validation"),
            })

    # v3: Morning Wellness Capsules (pooled vitamins + minerals + light botanicals)
    mwc = formulation.get("delivery_format_4_morning_wellness_capsules")
    if mwc:
        totals = mwc.get("totals", {})
        count = totals.get("capsule_count", 1)
        opt = totals.get("optimizer_record", {})
        components = mwc.get("components", [])
        # Use pre-formatted 'dose' string (e.g. "40mcg", "250mg") — dose_mg is 0.0 for mcg vitamins
        contents = ", ".join(
            f"{c.get('substance', '')} {c.get('dose', str(round(c.get('dose_mg', 0), 1)) + 'mg')}"
            for c in components[:5]
        )
        if len(components) > 5:
            contents += f" + {len(components)-5} more"
        summary = {
            "type": "hard_capsule",
            "label": "Morning Wellness Capsule",
            "count": count,
            "contents_summary": contents,
            "total_weight_mg": totals.get("total_weight_mg"),
            "validation": totals.get("validation", "PASS"),
        }
        if opt.get("adjustments_made"):
            summary["optimizer_adjustments"] = opt["adjustments_made"]
        result["morning"].append(summary)

    # Polyphenol capsule (Tier 2 — Curcumin+Piperine, Bergamot) — both v2 and v3
    polyphenol = formulation.get("delivery_format_5_polyphenol_capsule") or formulation.get("delivery_format_6_polyphenol_capsule")
    if polyphenol:
        totals = polyphenol.get("totals", {})
        components = polyphenol.get("components", [])
        contents = ", ".join(f"{c.get('substance', '')} {c.get('dose_mg', '')}mg" for c in components)
        result["morning"].append({
            "type": "hard_capsule",
            "label": "Morning Wellness Capsule",
            "count": polyphenol.get("format", {}).get("daily_count", 1),
            "contents_summary": contents,
            "weight_mg": totals.get("total_weight_mg") if totals else 0,
            "validation": totals.get("validation") if totals else "N/A",
        })

    # v3: Evening Wellness Capsules (pooled sleep aids + calming adaptogens)
    ewc = formulation.get("delivery_format_5_evening_wellness_capsules")
    if ewc:
        totals = ewc.get("totals", {})
        count = totals.get("capsule_count", 1)
        opt = totals.get("optimizer_record", {})
        components = ewc.get("components", [])
        contents = ", ".join(f"{c.get('substance', '')} {c.get('dose_mg', '')}mg" for c in components)
        summary = {
            "type": "hard_capsule",
            "label": "Evening Wellness Capsule",
            "count": count,
            "contents_summary": contents,
            "total_weight_mg": totals.get("total_weight_mg"),
            "validation": totals.get("validation", "PASS"),
        }
        if opt.get("adjustments_made"):
            summary["optimizer_adjustments"] = opt["adjustments_made"]
        result["evening"].append(summary)
    else:
        # v2 fallback: single Evening Capsule
        evening = formulation.get("delivery_format_4_evening_capsule")
        if evening:
            totals = evening.get("totals", {})
            components = evening.get("components", [])
            contents = ", ".join(c.get("substance", "") for c in components)
            result["evening"].append({
                "type": "hard_capsule",
                "label": _evening_capsule_label(components),
                "count": 1,
                "contents_summary": contents,
                "weight_mg": totals.get("total_weight_mg") if totals else 0,
                "validation": totals.get("validation") if totals else "N/A",
            })

    return result


def _extract_executive_summary(sample_dir: str, sample_id: str) -> Dict:
    """Extract 4 executive summary sections from the narrative report markdown.

    Reads Section 1 of the narrative report and extracts:
    - overall_pattern  (### Overall Pattern Classification)
    - dysbiosis_markers (### Dysbiosis-Associated Markers)
    - critical_finding  (### Critical Finding)
    - health_implications (### Health Implications)

    Returns a dict with those 4 keys. Falls back to empty strings if not found.
    """
    import re as _re
    from pathlib import Path as _Path

    result = {
        "overall_pattern": "",
        "dysbiosis_markers": "",
        "critical_finding": "",
        "health_implications": "",
    }

    md_path = _Path(sample_dir) / "reports" / "reports_md" / f"narrative_report_{sample_id}.md"
    if not md_path.exists():
        return result

    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception:
        return result

    def _extract_section(heading: str) -> str:
        """Extract text for a named section, handling two report formats:

        Format A (newer reports): ### heading on its own line
          ### Overall Pattern Classification
          text...

        Format B (older reports): **Bold heading** inline or as paragraph title
          **Overall Pattern Classification: subtitle**
          text...
          OR: **Dysbiosis-Associated Markers.** text on same line...
        """
        # Format A: ### heading (with optional subtitle after colon or dash)
        # Match both ## (H2) and ### (H3) headings — report format varies by batch.
        # Note: {{2,3}} doubles the braces so the f-string produces {2,3} for the regex.
        pattern_a = rf"#{{2,3}}\s+{_re.escape(heading)}[^\n]*\n(.*?)(?=\n#{{2,3}}\s|\n---|\Z)"
        m = _re.search(pattern_a, content, _re.DOTALL)
        if m:
            return m.group(1).strip()

        # Format B1: **heading** or **heading: subtitle** as a standalone bold line,
        # followed by text paragraph(s)
        pattern_b1 = rf"\*\*{_re.escape(heading)}[^*\n]*\*\*\s*\n(.*?)(?=\n\*\*[A-Z]|\n##|\n---|\Z)"
        m = _re.search(pattern_b1, content, _re.DOTALL)
        if m:
            return m.group(1).strip()

        # Format B2: **heading.** text continues on same line (e.g. **Dysbiosis-Associated Markers.** ...)
        # Captures the bold label line + any following lines until next bold section or heading
        pattern_b2 = rf"\*\*{_re.escape(heading)}[^*\n]*\*\*\s*(.*?)(?=\n\*\*[A-Z]|\n##|\n---|\Z)"
        m = _re.search(pattern_b2, content, _re.DOTALL)
        if m:
            return m.group(1).strip()

        return ""

    result["overall_pattern"] = _extract_section("Overall Pattern Classification")
    result["dysbiosis_markers"] = _extract_section("Dysbiosis-Associated Markers")
    result["critical_finding"] = _extract_section("Critical Finding")
    result["health_implications"] = _extract_section("Health Implications")

    return result


def build_decision_trace(master: Dict, trace_events: list = None, sample_dir: str = None) -> Dict:
    """Build board-readable decision trace JSON — linear chain from inputs → decisions → formula.

    Args:
        master: Complete master formulation dict
        trace_events: Optional list of pipeline decision events (chronological).
                     When provided, enables HTML decision trace to be generated from
                     the same source as the terminal pipeline log output.
        sample_dir: Optional path to sample directory — used to extract executive
                    summary sections from the narrative report markdown.
    """
    metadata = master.get("metadata", {})
    input_summary = master.get("input_summary", {})
    decisions = master.get("decisions", {})
    formulation = master.get("formulation", {})
    rule_outputs = decisions.get("rule_outputs", {})
    mix = decisions.get("mix_selection", {})
    supplements = decisions.get("supplement_selection", {})
    prebiotics = decisions.get("prebiotic_design", {})

    mb = input_summary.get("microbiome_driven", {})
    q = input_summary.get("questionnaire_driven", {})

    # Build guild summary — use guild_details (full data) if available, fall back to guild_status
    guild_details = mb.get("guild_details", {})
    guild_status_map = mb.get("guild_status", {})
    
    guild_entries = []
    for gk in guild_details or guild_status_map:
        if guild_details and gk in guild_details:
            gd = guild_details[gk]
            guild_entries.append({
                "name": gd.get("name", gk),
                "status": gd.get("status", ""),
                "abundance_pct": gd.get("abundance_pct", 0),
                "priority_level": gd.get("priority_level", ""),
                "clr": gd.get("clr"),
            })
        else:
            guild_entries.append({
                "name": gk,
                "status": guild_status_map.get(gk, ""),
            })

    clr = mb.get("clr_ratios", {})
    clr_display = {}
    for r in ["CUR", "FCR", "MDR", "PPR"]:
        val = clr.get(r)
        label = clr.get(f"{r}_label")
        if val is not None:
            clr_display[r] = f"{val:+.3f} ({label})" if label else f"{val:+.3f}"

    steps = []

    # Step 1: Sensitivity
    sens = rule_outputs.get("sensitivity", {})
    steps.append({
        "step": 1, "decision": "Sensitivity Classification", "method": "deterministic",
        "input": "; ".join(sens.get("reasoning", [])),
        "result": sens.get("classification", "?").upper(),
        "reasoning": f"Prebiotic max: {sens.get('max_prebiotic_g', '?')}g",
    })

    # Step 2: Mix Selection
    steps.append({
        "step": 2, "decision": "Mix Selection", "method": "deterministic",
        "input": "Guild status: " + ", ".join(f"{g['name']}={g['status']}" for g in guild_entries),
        "result": f"Mix {mix.get('mix_id')} — {mix.get('mix_name')}",
        "reasoning": mix.get("primary_trigger", ""),
        "rule_applied": mix.get("clr_context", ""),
        "alternative": mix.get("alternative_considered", ""),
        "confidence": mix.get("confidence", ""),
    })

    # Step 3: LP815
    if mix.get("lp815_added"):
        steps.append({
            "step": 3, "decision": "LP815 Enhancement", "method": "deterministic",
            "input": f"Stress {q.get('stress_level', '?')}/10, goals: {q.get('goals_ranked', [])}",
            "result": "YES — 5B CFU added",
            "reasoning": "Stress/mood triggers met → GABA producer for stress/sleep",
        })

    # Step 4: Softgel Decision
    softgel = rule_outputs.get("softgel", rule_outputs.get("magnesium", {}).get("softgel", {}))
    if not softgel:
        # Try from formulation
        softgel = {"include_softgel": formulation.get("delivery_format_2_omega_softgels") is not None}
    sg_needs = softgel.get("needs_identified", []) if isinstance(softgel, dict) else []
    sg_include = softgel.get('include_softgel', True) if isinstance(softgel, dict) else True
    # Always list all 4 fixed components, annotating trigger vs bundled
    sg_components = []
    if sg_include:
        for comp_name in ["omega3", "vitamin_d", "vitamin_e", "astaxanthin"]:
            label = {"omega3": "Omega-3 DHA & EPA (712.5mg×2)", "vitamin_d": "Vitamin D3 (10mcg×2)",
                     "vitamin_e": "Vitamin E (7.5mg×2)", "astaxanthin": "Astaxanthin (3mg×2)"}.get(comp_name, comp_name)
            is_trigger = comp_name in sg_needs
            sg_components.append(f"{label} {'[TRIGGER]' if is_trigger else '[bundled]'}")
    steps.append({
        "step": len(steps) + 1, "decision": "Softgel Decision", "method": "deterministic",
        "input": f"Goals: {q.get('goals_ranked', [])}, Health claims: {rule_outputs.get('health_claims', {}).get('supplement_claims', [])}",
        "result": f"{'YES' if sg_include else 'NO'} — triggers: {sg_needs}",
        "reasoning": "; ".join(softgel.get("reasoning", [])) if isinstance(softgel, dict) else "Default",
        "components": sg_components if sg_include else [],
    })

    # Step 5: Magnesium
    mg = rule_outputs.get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        mg_trace_timing = rule_outputs.get("timing", {}).get("timing_assignments", {}).get("magnesium", {}).get("timing", "evening")
        steps.append({
            "step": len(steps) + 1, "decision": "Magnesium Capsules", "method": "deterministic",
            "input": f"Sleep {q.get('sleep_quality', '?')}/10, Stress {q.get('stress_level', '?')}/10, exercise data",
            "result": f"{mg['capsules']} capsule(s) {mg_trace_timing} ({mg.get('elemental_mg_total_mg', 0)}mg elemental)",
            "reasoning": "; ".join(mg.get("reasoning", [])),
        })

    # Step 6: Prebiotic Design
    # Include substrate_guard data (v2.1) — corrections, rebalancing, SIBO status, ceiling
    substrate_guard = prebiotics.get("substrate_guard", {})
    prebiotic_step = {
        "step": len(steps) + 1, "decision": "Prebiotic Design", "method": "deterministic (mix-aware)" if "offline" in str(prebiotics.get("strategy", "")).lower() or "Mix" in str(prebiotics.get("strategy", "")) else "LLM",
        "input": f"Mix {mix.get('mix_id')} default formula, sensitivity={sens.get('classification', '?')}, range={rule_outputs.get('prebiotic_range', {}).get('min_g', '?')}-{rule_outputs.get('prebiotic_range', {}).get('max_g', '?')}g",
        "result": f"{prebiotics.get('total_grams', 0)}g ({len(prebiotics.get('prebiotics', []))} components)",
        "reasoning": prebiotics.get("strategy", ""),
        "overrides": prebiotics.get("overrides_applied", []),
        "sensitivity_override_active": prebiotics.get("sensitivity_override_active", False),
        "components": [
            {"substance": p.get("substance", ""), "dose_g": p.get("dose_g", 0), "fodmap": p.get("fodmap", False)}
            for p in prebiotics.get("prebiotics", [])
        ],
    }
    if substrate_guard:
        prebiotic_step["substrate_guard"] = {
            "applied": substrate_guard.get("applied", False),
            "reason": substrate_guard.get("reason", ""),
            "sibo_source": substrate_guard.get("sibo_source"),
            "corrections": substrate_guard.get("corrections", []),
            "rebalance_log": substrate_guard.get("rebalance_log", []),
            "warnings": substrate_guard.get("warnings", []),
            "final_total_g": substrate_guard.get("final_total_g"),
            "effective_ceiling_g": substrate_guard.get("effective_ceiling_g"),
            "tolerance_pct": substrate_guard.get("tolerance_pct"),
            "ceiling_respected": substrate_guard.get("ceiling_respected", True),
        }
    steps.append(prebiotic_step)

    # Step 7: Supplement Selection — uses component_registry as single source of truth
    # This ensures Step 7 lists EXACTLY the same components as Final Formulation
    # (including sleep supplements, evening capsule overflow, polyphenol capsules, etc.)
    registry = master.get("component_registry", [])
    vms = supplements.get("vitamins_minerals", [])
    supps = supplements.get("supplements", [])
    if registry:
        # Registry-based: complete bill of materials matching Final Formulation
        step7_components = [f"{entry['substance']}: {entry.get('dose', '')}" for entry in registry]
        # Count vitamins/minerals vs supplements from registry categories
        n_vm = sum(1 for e in registry if e.get("category") in ("vitamin_mineral",))
        n_supp = sum(1 for e in registry if e.get("category") in ("supplement", "sleep_supplement", "polyphenol"))
        n_other = len(registry) - n_vm - n_supp
        step7_result = f"{len(registry)} total components ({n_vm} vitamins/minerals + {n_supp} supplements + {n_other} other)"
    else:
        # Fallback for pre-registry JSONs
        step7_components = [f"{v['substance']}: {v.get('dose', '')}" for v in vms] + [f"{s['substance']}: {s.get('dose_mg', '')}mg" for s in supps]
        step7_result = f"{len(vms)} vitamins/minerals + {len(supps)} supplements"
    steps.append({
        "step": len(steps) + 1, "decision": "Supplement Selection", "method": "LLM" if vms or supps else "offline (skeleton)",
        "input": f"Health claims: {rule_outputs.get('health_claims', {}).get('supplement_claims', [])}, Vitamin claims: {rule_outputs.get('health_claims', {}).get('vitamin_claims', [])}",
        "result": step7_result,
        "components": step7_components,
    })

    # Build delivery summary (supports v2 and v3)
    proto = formulation.get("protocol_summary", {})
    delivery = []
    if formulation.get("delivery_format_1_probiotic_capsule"):
        t = formulation["delivery_format_1_probiotic_capsule"].get("totals", {})
        delivery.append({"type": "Probiotic Capsule", "timing": "morning", "count": 1, "contents": f"{t.get('total_cfu_billions', 0)}B CFU"})
    if formulation.get("delivery_format_2_omega_softgels"):
        delivery.append({"type": "Omega Softgel", "timing": "morning", "count": 2, "contents": "Omega 712.5mg + D3 + E + Astaxanthin"})
    # v3: Powder Jar
    if formulation.get("delivery_format_3_powder_jar"):
        jt = formulation["delivery_format_3_powder_jar"].get("totals", {})
        phased = jt.get("phased_dosing", {})
        jar_contents = f"{jt.get('prebiotic_total_g', 0)}g prebiotics"
        if jt.get("botanical_total_g", 0) > 0:
            jar_contents += f" + {jt['botanical_total_g']}g botanicals"
        jar_entry = {"type": "Powder Jar", "timing": "morning", "count": 1, "contents": jar_contents, "weight_g": jt.get("total_weight_g", 0)}
        if phased:
            jar_entry["phased_dosing"] = {"weeks_1_2_g": phased.get("weeks_1_2_g"), "weeks_3_plus_g": phased.get("weeks_3_plus_g"), "instruction": phased.get("instruction", "")}
        delivery.append(jar_entry)
    elif formulation.get("delivery_format_3_daily_sachet"):
        t = formulation["delivery_format_3_daily_sachet"].get("totals", {})
        delivery.append({"type": "Daily Sachet", "timing": "morning", "count": 1, "contents": f"{t.get('prebiotic_total_g', 0)}g prebiotics + vitamins"})
    # v3: Morning Wellness Capsules
    if formulation.get("delivery_format_4_morning_wellness_capsules"):
        mwct = formulation["delivery_format_4_morning_wellness_capsules"].get("totals", {})
        mwc_count = mwct.get("capsule_count", 1)
        delivery.append({"type": "Morning Wellness Capsule", "timing": "morning", "count": mwc_count, "contents": f"vitamins + minerals + light botanicals", "weight_mg": mwct.get("total_weight_mg", 0)})
    # Polyphenol capsule
    pp_key = "delivery_format_5_polyphenol_capsule" if formulation.get("delivery_format_5_polyphenol_capsule") else "delivery_format_6_polyphenol_capsule"
    if formulation.get(pp_key):
        pp = formulation[pp_key]
        pp_t = pp.get("totals", {})
        pp_contents = ", ".join(f"{c.get('substance', '')} {c.get('dose_mg', '')}mg" for c in pp.get("components", []))
        delivery.append({"type": "Morning Wellness Capsule", "timing": "morning", "count": 1, "contents": pp_contents, "weight_mg": pp_t.get("total_weight_mg", 0)})
    # v3: Evening Wellness Capsules
    if formulation.get("delivery_format_5_evening_wellness_capsules"):
        ewct = formulation["delivery_format_5_evening_wellness_capsules"].get("totals", {})
        ewc_count = ewct.get("capsule_count", 1)
        delivery.append({"type": "Evening Wellness Capsule", "timing": "evening", "count": ewc_count, "contents": "sleep aids + calming adaptogens", "weight_mg": ewct.get("total_weight_mg", 0)})
    # v2 fallback: Evening capsules
    elif formulation.get("delivery_format_4_evening_capsule"):
        ec = formulation["delivery_format_4_evening_capsule"]
        ec_comps = ec.get("components", [])
        contents = ", ".join(f"{c.get('substance', '')} {c.get('dose_mg', '')}mg" for c in ec_comps)
        delivery.append({"type": "Evening Wellness Capsule", "timing": "evening", "count": 1, "contents": contents, "weight_mg": ec.get("totals", {}).get("total_weight_mg", 0)})
    # NOTE: delivery_format_4b_evening_capsule_2 was a v2 artifact.
    # v3 evening overflow is handled inside delivery_format_5_evening_wellness_capsules
    # (capsule_count ≥ 2 + per-capsule layout from CapsuleStackingOptimizer).

    if mg.get("capsules", 0) > 0:
        mg_del_timing = rule_outputs.get("timing", {}).get("timing_assignments", {}).get("magnesium", {}).get("timing", "evening")
        delivery.append({"type": "Mg Bisglycinate Capsule", "timing": mg_del_timing, "count": mg["capsules"], "contents": f"{mg.get('mg_bisglycinate_total_mg', 0)}mg bisglycinate ({mg.get('elemental_mg_total_mg', 0)}mg elemental)"})

    # Get Opus narratives if available
    input_narratives = master.get("input_narratives", {})
    ecological_rationale = master.get("ecological_rationale", {})
    q_coverage = master.get("questionnaire_coverage", {})

    mb_narrative = input_narratives.get("microbiome_narrative", "")
    q_narrative = input_narratives.get("questionnaire_narrative", "")
    mb_summary_default = "%d guilds analyzed. Score: %s (%s)" % (len(guild_entries), mb.get('overall_score', {}).get('total', '?'), mb.get('overall_score', {}).get('band', '?'))
    q_summary_default = "Goals: %s. Stress %s/10, Sleep %s/10" % (', '.join(q.get('goals_ranked', [])[:3]), q.get('stress_level', '?'), q.get('sleep_quality', '?'))

    # Build formulation logic narrative from root_causes (same source as health report)
    root_causes = mb.get("root_causes", {})
    guild_scenarios = mb.get("guild_scenarios", [])
    formulation_narrative = _build_formulation_narrative(guild_entries, clr_display, mix, root_causes)

    # Extract executive summary from narrative report MD (4 structured sections)
    sample_id = metadata.get("sample_id", "")
    exec_summary = _extract_executive_summary(sample_dir, sample_id) if sample_dir and sample_id else {}
    # Fallback: if extraction failed, put the LLM narrative into overall_pattern
    if not any(exec_summary.values()):
        exec_summary = {"overall_pattern": mb_narrative or mb_summary_default}

    return {
        "sample_id": sample_id,
        "generated_at": metadata.get("generated_at"),
        "validation": metadata.get("validation_status"),
        "inputs": {
            "microbiome": {
                "executive_summary": exec_summary,
                "summary": exec_summary.get("overall_pattern") or mb_narrative or mb_summary_default,
                "guilds": guild_entries,
                "clr_ratios": clr_display,
                "formulation_narrative": formulation_narrative,
                "guild_scenarios": guild_scenarios,
            },
            "questionnaire": {
                "summary": q_narrative or q_summary_default,
                "goals_ranked": [g.replace("_", " ").title() for g in q.get("goals_ranked", [])],
                "sensitivity": sens.get("classification", "?"),
                "stress": q.get("stress_level"),
                "sleep": q.get("sleep_quality"),
                "bloating": q.get("bloating_severity"),
                "coverage": q_coverage.get("coverage_level", ""),
                "coverage_pct": q_coverage.get("completion_pct", 0),
                "medications": q.get("medications", []),
                "medication_exclusions": master.get("medication_rules", {}).get("exclusion_reasons", []),
            },
        },
        "ecological_rationale": ecological_rationale,
        "decision_chain": steps,
        "final_formulation": {
            "delivery_units": delivery,
            "total_units": proto.get("total_daily_units", 0),
            "total_weight_g": proto.get("total_daily_weight_g", 0),
            "validation": metadata.get("validation_status"),
        },
        "evidence_sources": _build_evidence_tags(decisions, input_summary),
    }


def build_manufacturing_recipe(master: Dict) -> Dict:
    """Build manufacturing recipe JSON — exact weights, units, ingredients for production.
    
    Timing and labels are read from the formulation format dicts (not hardcoded),
    so medication timing overrides (e.g., all units → dinner) are automatically
    reflected in the recipe without any post-processing.
    """
    metadata = master.get("metadata", {})
    formulation = master.get("formulation", {})
    decisions = master.get("decisions", {})
    rule_outputs = decisions.get("rule_outputs", {})
    mix = decisions.get("mix_selection", {})
    proto = formulation.get("protocol_summary", {})

    # Read actual timing from each delivery format (respects medication override)
    _t1 = _get_unit_timing(formulation, "delivery_format_1_probiotic_capsule", "morning")
    _t2 = _get_unit_timing(formulation, "delivery_format_2_omega_softgels", "morning")
    _t3 = _get_unit_timing(formulation, "delivery_format_3_powder_jar", "morning")
    _t4 = _get_unit_timing(formulation, "delivery_format_4_morning_wellness_capsules", "morning")
    _l4 = _get_unit_label(formulation, "delivery_format_4_morning_wellness_capsules", "Morning Wellness Capsule")
    _t6 = _get_unit_timing(formulation, "delivery_format_6_polyphenol_capsule",
           _get_unit_timing(formulation, "delivery_format_5_polyphenol_capsule", "morning, with food"))
    _l6 = _get_unit_label(formulation, "delivery_format_6_polyphenol_capsule",
           _get_unit_label(formulation, "delivery_format_5_polyphenol_capsule", "Morning Wellness Capsule"))

    units = []
    unit_num = 0

    # Unit 1: Probiotic capsule
    capsule = formulation.get("delivery_format_1_probiotic_capsule", {})
    if capsule and capsule.get("components"):
        unit_num += 1
        ingredients = []
        for c in capsule["components"]:
            ing = {"component": c["substance"], "amount_mg": c["weight_mg"]}
            if "cfu_billions" in c:
                ing["cfu_billions"] = c["cfu_billions"]
            ingredients.append(ing)
        totals = capsule.get("totals", {})
        units.append({
            "unit_number": unit_num,
            "label": "Probiotic Hard Capsule",
            "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian", "color": "tan/beige opaque"},
            "timing": _t1,
            "quantity": 1,
            "fill_weight_mg": totals.get("total_weight_mg", 0),
            "storage": "With desiccant packet, cool and dry",
            "ingredients": ingredients,
            "total_weight_mg": totals.get("total_weight_mg", 0),
            "total_cfu_billions": totals.get("total_cfu_billions", 0),
            "mix_id": mix.get("mix_id"),
            "mix_name": mix.get("mix_name"),
        })

    # Unit 2: Softgels (fixed composition)
    softgels = formulation.get("delivery_format_2_omega_softgels", {})
    if softgels and softgels.get("components_per_softgel"):
        unit_num += 1
        ingredients_per = []
        for c in softgels["components_per_softgel"]:
            ing = {"component": c["substance"]}
            if "weight_mg_per_softgel" in c:
                if c.get("weight_note") == "NEGLIGIBLE":
                    ing["amount"] = c.get("dose_per_softgel", "")
                    ing["weight_note"] = "negligible"
                else:
                    ing["amount_mg"] = c["weight_mg_per_softgel"]
            ing["dose_per_softgel"] = c.get("dose_per_softgel", "")
            ingredients_per.append(ing)

        sg_totals = softgels.get("totals", {})
        sg_count = softgels.get("format", {}).get("daily_count", 2)
        units.append({
            "unit_number": unit_num,
            "label": "Omega + Antioxidant Softgel",
            "format": {"type": "softgel", "size": "0", "material": "vegetarian", "color": "transparent gel"},
            "timing": _t2,
            "quantity": sg_count,
            "fill_weight_per_unit_mg": sg_totals.get("weight_per_softgel_mg", 750),
            "total_weight_mg": sg_totals.get("daily_total_mg", 750 * sg_count),
            "note": "Fixed composition — identical for all clients",
            "ingredients_per_unit": ingredients_per,
            "daily_totals": {
                "omega3_mg": 712.5 * sg_count,
                "vitamin_d_mcg": 10 * sg_count,
                "vitamin_e_mg": 7.5 * sg_count,
                "astaxanthin_mg": 3 * sg_count,
            },
        })

    # Unit 3: Powder Jar (v3) or Daily Sachet (v2 fallback)
    jar = formulation.get("delivery_format_3_powder_jar")
    sachet = formulation.get("delivery_format_3_daily_sachet", {})
    if jar:
        unit_num += 1
        ingredients = []

        # Prebiotics
        for p in jar.get("prebiotics", {}).get("components", []):
            ingredients.append({
                "component": p["substance"],
                "amount_g": p["dose_g"],
                "category": "prebiotic",
                "fodmap": p.get("fodmap", False),
            })

        # Heavy botanicals
        for b in jar.get("botanicals", {}).get("components", []):
            ingredients.append({
                "component": b["substance"],
                "amount_g": b.get("dose_g", 0),
                "category": "botanical_heavy",
            })

        jar_totals = jar.get("totals", {})
        phased = jar_totals.get("phased_dosing", {})
        jar_unit = {
            "unit_number": unit_num,
            "label": "Prebiotic & Botanical Powder Jar",
            "format": {"type": "jar", "mixing": "Mix in 200-300ml water (a large glass). Stir well and drink immediately."},
            "timing": _t3,
            "quantity": 1,
            "ingredients": ingredients,
            "total_weight_g": jar_totals.get("total_weight_g", 0),
            "prebiotic_weight_g": jar_totals.get("prebiotic_total_g", 0),
            "botanical_weight_g": jar_totals.get("botanical_total_g", 0),
            "total_fodmap_g": jar_totals.get("total_fodmap_g", 0),
        }
        if phased:
            jar_unit["phased_dosing"] = {
                "weeks_1_2_g": phased.get("weeks_1_2_g"),
                "weeks_3_plus_g": phased.get("weeks_3_plus_g"),
                "instruction": phased.get("instruction", ""),
                "rationale": phased.get("rationale", ""),
            }
        units.append(jar_unit)
    elif sachet:
        unit_num += 1
        ingredients = []
        for p in sachet.get("prebiotics", {}).get("components", []):
            ingredients.append({"component": p["substance"], "amount_g": p["dose_g"], "category": "prebiotic", "fodmap": p.get("fodmap", False)})
        for v in sachet.get("vitamins_minerals", {}).get("components", []):
            ingredients.append({"component": v["substance"], "amount": v["dose"], "amount_mg": v["weight_mg"], "category": "vitamin_mineral"})
        for s in sachet.get("supplements", {}).get("components", []):
            ingredients.append({"component": s["substance"], "amount_mg": s.get("dose_mg", s.get("weight_mg", 0)), "category": "supplement"})
        s_totals = sachet.get("totals", {})
        units.append({
            "unit_number": unit_num,
            "label": "Daily Sachet (Powder Blend)",
            "format": {"type": "sachet", "mixing": "Mix in 6-8oz water"},
            "timing": "morning",
            "quantity": 1,
            "ingredients": ingredients,
            "total_weight_g": s_totals.get("total_weight_g", 0),
            "prebiotic_weight_g": s_totals.get("prebiotic_total_g", 0),
            "total_fodmap_g": s_totals.get("total_fodmap_g", 0),
        })

    # Unit 3b: Morning Wellness Capsules (v3 — pooled vitamins + minerals + light botanicals)
    mwc = formulation.get("delivery_format_4_morning_wellness_capsules")
    if mwc and mwc.get("components"):
        unit_num += 1
        ingredients = []
        for c in mwc["components"]:
            ing = {
                "component": c["substance"],
                "category": c.get("type", "vitamin_mineral"),
            }
            dose_mg = c.get("dose_mg", c.get("weight_mg", 0))
            dose_unit = c.get("dose_unit", "mg")
            if c.get("weight_note") == "NEGLIGIBLE":
                ing["amount"] = c.get("dose", "")
                ing["weight_note"] = "negligible"
            elif dose_unit.lower() in ("mcg", "ug", "μg"):
                ing["amount"] = c.get("dose", "")
            else:
                ing["amount_mg"] = dose_mg
            ingredients.append(ing)

        mwc_totals = mwc.get("totals", {})
        opt = mwc_totals.get("optimizer_record", {})
        mwc_cap_count = mwc_totals.get("capsule_count", 1)
        mwc_capsule_layout = mwc_totals.get("capsules", [])
        # Per-capsule fill: use max fill across capsules (for validation against 650mg spec)
        _mwc_fills = [c.get("fill_mg", 0) for c in mwc_capsule_layout]
        mwc_max_fill = round(max(_mwc_fills), 1) if _mwc_fills else mwc_totals.get("total_weight_mg", 0)
        mwc_unit = {
            "unit_number": unit_num,
            "label": _l4,
            "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
            "timing": _t4,
            "quantity": mwc_cap_count,
            "fill_weight_per_capsule_mg": mwc_max_fill,  # per-capsule max (for capacity validation)
            "total_fill_weight_mg": mwc_totals.get("total_weight_mg", 0),  # sum across all capsules
            "ingredients": ingredients,
            "ingredients_note": f"Total across {mwc_cap_count} capsules — see capsule_layout for per-capsule breakdown" if mwc_cap_count > 1 else None,
            "total_weight_mg": mwc_totals.get("total_weight_mg", 0),
            "capsule_layout": mwc_capsule_layout,
        }
        if opt.get("adjustments_made"):
            mwc_unit["optimizer_adjustments"] = opt["adjustments_made"]
            mwc_unit["optimizer_outcome"] = opt.get("optimization_outcome", "")
        units.append(mwc_unit)

    # Unit 4: Polyphenol capsule (Tier 2 — Curcumin+Piperine, Bergamot)
    polyphenol_cap = formulation.get("delivery_format_5_polyphenol_capsule") or formulation.get("delivery_format_6_polyphenol_capsule")
    if polyphenol_cap and polyphenol_cap.get("components"):
        unit_num += 1
        ingredients = []
        for c in polyphenol_cap["components"]:
            ingredients.append({
                "component": c["substance"],
                "amount_mg": c.get("dose_mg", c.get("weight_mg", 0)),
                "category": "polyphenol",
            })
        pp_totals = polyphenol_cap.get("totals", {})
        units.append({
            "unit_number": unit_num,
            "label": _l6,
            "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
            "timing": _t6,
            # Polyphenols (Curcumin+Piperine, Bergamot) require dietary fat for absorption.
            # Must be taken WITH a meal — not on an empty stomach.
            "timing_note": "Take with a meal containing fat — required for polyphenol absorption",
            "quantity": 1,
            "fill_weight_mg": pp_totals.get("total_weight_mg", 0),
            "ingredients": ingredients,
            "total_weight_mg": pp_totals.get("total_weight_mg", 0),
        })

    # Unit 5: Evening Wellness Capsules (v3) or Evening Capsule (v2 fallback)
    ewc = formulation.get("delivery_format_5_evening_wellness_capsules")
    if ewc and ewc.get("components"):
        unit_num += 1
        ingredients = []
        for c in ewc["components"]:
            ingredients.append({
                "component": c["substance"],
                "amount_mg": c.get("dose_mg", c.get("weight_mg", 0)),
                "category": "evening_supplement",
            })
        ewc_totals = ewc.get("totals", {})
        opt = ewc_totals.get("optimizer_record", {})
        ewc_cap_count = ewc_totals.get("capsule_count", 1)
        ewc_capsule_layout = ewc_totals.get("capsules", [])
        _ewc_fills = [c.get("fill_mg", 0) for c in ewc_capsule_layout]
        ewc_max_fill = round(max(_ewc_fills), 1) if _ewc_fills else ewc_totals.get("total_weight_mg", 0)
        ewc_unit = {
            "unit_number": unit_num,
            "label": "Evening Wellness Capsule",
            "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
            "timing": "evening (30-60 min before bed)",
            "quantity": ewc_cap_count,
            "fill_weight_per_capsule_mg": ewc_max_fill,  # per-capsule max (for capacity validation)
            "total_fill_weight_mg": ewc_totals.get("total_weight_mg", 0),
            "ingredients": ingredients,
            "ingredients_note": f"Total across {ewc_cap_count} capsules — see capsule_layout for per-capsule breakdown" if ewc_cap_count > 1 else None,
            "total_weight_mg": ewc_totals.get("total_weight_mg", 0),
            "capsule_layout": ewc_capsule_layout,
        }
        if opt.get("adjustments_made"):
            ewc_unit["optimizer_adjustments"] = opt["adjustments_made"]
            ewc_unit["optimizer_outcome"] = opt.get("optimization_outcome", "")
        units.append(ewc_unit)
    else:
        # v2 fallback: single Evening Capsule
        evening_cap = formulation.get("delivery_format_4_evening_capsule")
        if evening_cap and evening_cap.get("components"):
            unit_num += 1
            ingredients = []
            for c in evening_cap["components"]:
                ingredients.append({"component": c["substance"], "amount_mg": c.get("dose_mg", c.get("weight_mg", 0)), "category": "evening_supplement"})
            ec_totals = evening_cap.get("totals", {})
            ec_label = _evening_capsule_label(evening_cap["components"])
            units.append({
                "unit_number": unit_num,
                "label": ec_label,
                "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
                "timing": "evening (30-60 min before bed)",
                "quantity": 1,
                "fill_weight_mg": ec_totals.get("total_weight_mg", 0),
                "ingredients": ingredients,
                "total_weight_mg": ec_totals.get("total_weight_mg", 0),
            })

        # v2: Evening capsule 2 (overflow)
        evening_cap2 = formulation.get("delivery_format_4b_evening_capsule_2")
        if evening_cap2 and evening_cap2.get("components"):
            unit_num += 1
            ingredients = []
            for c in evening_cap2["components"]:
                ingredients.append({"component": c["substance"], "amount_mg": c.get("dose_mg", c.get("weight_mg", 0)), "category": "evening_supplement"})
            ec2_totals = evening_cap2.get("totals", {})
            ec2_label = _evening_capsule_label(evening_cap2["components"])
            units.append({
                "unit_number": unit_num,
                "label": f"{ec2_label} (2)",
                "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
                "timing": "evening (30-60 min before bed)",
                "quantity": 1,
                "fill_weight_mg": ec2_totals.get("total_weight_mg", 0),
                "ingredients": ingredients,
                "total_weight_mg": ec2_totals.get("total_weight_mg", 0),
            })

    # Unit: Magnesium capsules (timing determined by timing engine)
    mg = rule_outputs.get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        unit_num += 1
        # Get authoritative timing from timing assignments (not hardcoded)
        mg_timing_info = rule_outputs.get("timing", {}).get("timing_assignments", {}).get("magnesium", {})
        mg_timing = mg_timing_info.get("timing", "evening")
        mg_timing_label = "evening (30-60 min before bed)"  # Mg is always evening
        units.append({
            "unit_number": unit_num,
            "label": "Magnesium Bisglycinate Capsule",
            "format": {"type": "hard_capsule", "size": "00", "material": "vegetarian"},
            "timing": mg_timing_label,
            "quantity": mg["capsules"],
            "fill_weight_per_unit_mg": 750,
            "ingredients_per_unit": [
                {"component": "Magnesium bisglycinate", "amount_mg": 750, "elemental_mg": 105, "nrv_pct": 14}
            ],
            "daily_totals": {
                "mg_bisglycinate_mg": mg["mg_bisglycinate_total_mg"],
                "elemental_mg_mg": mg["elemental_mg_total_mg"],
            },
            "needs": mg.get("needs_identified", []),
        })

    return {
        "sample_id": metadata.get("sample_id"),
        "generated_at": metadata.get("generated_at"),
        "protocol_summary": f"{proto.get('morning_solid_units', 0)} morning solid units + {proto.get('morning_jar_units', 0)} drink + {proto.get('evening_solid_units', 0)} evening units",
        "protocol_duration_weeks": 16,
        "units": units,
        "grand_total": {
            "total_units": proto.get("total_daily_units", 0),
            "total_daily_weight_g": proto.get("total_daily_weight_g", 0),
            "morning_units": proto.get("morning_solid_units", 0) + proto.get("morning_jar_units", 0),
            "evening_units": proto.get("evening_solid_units", 0),
        },
        "validation": metadata.get("validation_status"),
    }


def build_component_rationale(master: Dict) -> Dict:
    """Build component-to-imbalance/symptom rationale mapping.
    
    NOW READS FROM component_registry (single source of truth) when available.
    Falls back to old assembly logic for backward compatibility with pre-registry JSONs.
    
    Generates:
    1. how_this_addresses_your_health: Client-facing table
    2. source_attribution: Per-component source labels
    3. health_axis_predictions: Microbiome pattern → predicted health axis
    """
    metadata = master.get("metadata", {})
    input_summary = master.get("input_summary", {})
    decisions = master.get("decisions", {})
    formulation = master.get("formulation", {})
    rule_outputs = decisions.get("rule_outputs", {})

    mb = input_summary.get("microbiome_driven", {})
    q = input_summary.get("questionnaire_driven", {})
    guilds = mb.get("guild_status", {})
    clr = mb.get("clr_ratios", {})
    goals = q.get("goals_ranked", [])

    # ── 1. Build health_table from component_registry (single source of truth) ──
    registry = master.get("component_registry", [])
    
    if registry:
        # USE REGISTRY — canonical, post-dedup, guaranteed consistent
        health_table = []
        for entry in registry:
            claims_str = ", ".join(entry.get("health_claims", []))
            health_table.append({
                "component": entry["substance"],
                "what_it_targets": entry.get("what_it_targets", ""),
                "based_on": entry.get("based_on", ""),
                "source": entry.get("source", "questionnaire_only"),
                "delivery": entry.get("delivery", ""),
                "health_claim": claims_str,
            })
    else:
        # FALLBACK — old assembly logic for backward compatibility
        health_table = _build_health_table_legacy(master)

    # ── 2. Source Attribution Summary ──
    source_counts = {"microbiome_primary": 0, "microbiome_linked": 0, "questionnaire_only": 0, "fixed_component": 0}
    for item in health_table:
        src = item.get("source", "questionnaire_only")
        if src in source_counts:
            source_counts[src] += 1

    total = sum(source_counts.values())
    microbiome_informed = source_counts["microbiome_primary"] + source_counts["microbiome_linked"]

    source_summary = {
        "total_components": total,
        "microbiome_primary": source_counts["microbiome_primary"],
        "microbiome_linked": source_counts["microbiome_linked"],
        "questionnaire_only": source_counts["questionnaire_only"],
        "fixed_component": source_counts["fixed_component"],
        "microbiome_informed_pct": round(microbiome_informed / total * 100, 1) if total > 0 else 0,
    }

    # ── 3. Health Axis Predictions ──
    health_axes = _derive_health_axes(guilds, clr, goals, q)

    return {
        "sample_id": metadata.get("sample_id"),
        "generated_at": metadata.get("generated_at"),
        "how_this_addresses_your_health": health_table,
        "source_attribution": source_summary,
        "health_axis_predictions": health_axes,
        "key_points": [
            "Components labeled 'Microbiome analysis' are chosen based on objective gut bacteria measurements",
            "Components labeled 'Microbiome pattern + [symptom]' connect bacterial imbalances to health concerns",
            "This approach treats root causes identified in the microbiome, not just symptoms",
        ],
    }


def _build_health_table_legacy(master: Dict) -> list:
    """Legacy health table assembly — used only when component_registry is not available."""
    decisions = master.get("decisions", {})
    formulation = master.get("formulation", {})
    rule_outputs = decisions.get("rule_outputs", {})
    mix = decisions.get("mix_selection", {})
    supplements = decisions.get("supplement_selection", {})
    prebiotics = decisions.get("prebiotic_design", {})
    q = master.get("input_summary", {}).get("questionnaire_driven", {})
    goals = q.get("goals_ranked", [])

    health_table = []
    mix_name = mix.get("mix_name", "")
    mix_trigger = mix.get("primary_trigger", "")
    lp815 = mix.get("lp815_added", False)
    strain_count = len(mix.get("strains", []))
    base_strains = strain_count - (1 if lp815 else 0)

    health_table.append({"component": f"{base_strains} probiotic strains ({mix_name})", "what_it_targets": _derive_mix_target(mix), "based_on": f"Microbiome analysis ({mix_trigger})", "source": "microbiome_primary", "delivery": "probiotic capsule"})
    if lp815:
        health_table.append({"component": "LP815 psychobiotic strain (5B CFU)", "what_it_targets": "Stress, anxiety, mood, sleep", "based_on": f"Microbiome gut-brain pattern + stress {q.get('stress_level','?')}/10", "source": "microbiome_linked", "delivery": "probiotic capsule"})

    for pb in prebiotics.get("prebiotics", []):
        health_table.append({"component": f"{pb['substance']} ({pb['dose_g']}g)", "what_it_targets": _derive_prebiotic_target(pb['substance'], mix_name), "based_on": f"Microbiome pattern", "source": "microbiome_primary", "delivery": "sachet"})

    mg = rule_outputs.get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        health_table.append({"component": f"Magnesium Bisglycinate ({mg['mg_bisglycinate_total_mg']}mg)", "what_it_targets": _derive_mg_target(mg.get("needs_identified", [])), "based_on": "Questionnaire", "source": "questionnaire_only", "delivery": "evening capsule"})

    return health_table


def _derive_mix_target(mix: Dict) -> str:
    """Derive what the probiotic mix targets based on mix name."""
    mix_id = mix.get("mix_id")
    targets = {
        1: "Broad gut ecosystem recovery, multiple guild restoration",
        2: "Bifidobacteria restoration, lactate pathway repair, gut balance",
        3: "Butyrate production enhancement, fiber metabolism optimization",
        4: "Proteolytic guild suppression, reducing harsh byproducts",
        5: "Mucus barrier restoration, gut lining protection",
        6: "Ecosystem maintenance, diversity preservation",
        8: "Fiber expansion, competitive displacement of mucin degraders",
    }
    return targets.get(mix_id, "Gut microbiome optimization")


def _derive_prebiotic_target(substance: str, mix_name: str) -> str:
    """Derive what a prebiotic targets."""
    s = substance.lower()
    if "phgg" in s:
        return "Gentle fiber support, comfortable digestion"
    elif "gos" in s:
        return "Bifidobacteria fuel, probiotic establishment"
    elif "inulin" in s:
        return "Bifidobacteria fuel, SCFA production"
    elif "fos" in s:
        return "Selective Bifidobacteria feeding"
    elif "resistant starch" in s:
        return "Butyrate production, LP815 GABA fuel"
    elif "beta-glucan" in s or "beta glucan" in s:
        return "Gentle SCFA substrate, fiber expansion"
    elif "pectin" in s:
        return "Butyrate production, barrier support"
    else:
        return f"Prebiotic support for {mix_name}"


def _derive_mg_target(needs: list) -> str:
    """Derive magnesium target from needs."""
    targets = []
    if "sleep" in needs:
        targets.append("sleep quality (melatonin production, muscle relaxation)")
    if "sport" in needs:
        targets.append("muscle recovery (exercise support)")
    if "stress" in needs:
        targets.append("stress management (GABA receptor modulation)")
    return ", ".join(targets).capitalize() if targets else "General wellness"


def _extract_claims_from_rationale(rationale: str, goals: list) -> str:
    """Extract specific health claim keywords from a rationale string and user goals.
    
    Scans the LLM-generated rationale text for recognizable health concern keywords
    and maps them to concise claim labels. Also cross-references the user's ranked
    goals to produce a precise, human-readable "Based on" descriptor.
    
    Examples:
        "Supports energy metabolism and reduces fatigue" → "Fatigue, Metabolism"
        "Supports immune system function" → "Immune System"
        "Supports skin quality and energy metabolism" → "Skin Quality, Fatigue"
    """
    if not rationale:
        return ""
    
    rationale_lower = rationale.lower()
    found = []
    
    # Map keywords in rationale text → concise claim labels
    # Order matters: check more specific phrases first
    KEYWORD_TO_CLAIM = [
        # Energy / Fatigue
        (["fatigue", "energy metabolism", "reduces fatigue", "energy production", "anti-fatigue"], "Fatigue"),
        # Immune
        (["immune system", "immune function", "immune support", "immune resilience"], "Immune System"),
        # Skin
        (["skin quality", "skin health", "skin moisture", "skin improvement"], "Skin Quality"),
        # Metabolism
        (["metabolism", "metabolic"], "Metabolism"),
        # Mood / Stress / Anxiety
        (["mood", "anxiety", "stress resilience", "stress management"], "Stress/Anxiety"),
        # Sleep
        (["sleep", "sleep quality", "sleep onset"], "Sleep Quality"),
        # Digestion
        (["bowel", "digestive", "stool", "bloating"], "Bowel Function"),
        # Cognition
        (["focus", "concentration", "cognition", "memory"], "Concentration"),
        # Anti-inflammatory / Aging
        (["anti-inflammatory", "healthy aging", "longevity"], "Anti-inflammatory"),
        # Weight
        (["weight", "satiety", "fullness", "appetite"], "Weight Management"),
        # Heart
        (["cholesterol", "triglycerides", "heart", "cardiovascular"], "Heart Health"),
        # Nervous system (maps to fatigue/stress context)
        (["nervous system"], "Nervous System"),
    ]
    
    for keywords, label in KEYWORD_TO_CLAIM:
        if any(kw in rationale_lower for kw in keywords):
            if label not in found:
                found.append(label)
    
    # If we found claims, return them (limit to 3 most relevant for readability)
    if found:
        return ", ".join(found[:3])
    
    # Fallback: try to match against user goals
    GOAL_TO_LABEL = {
        "boost_energy": "Energy goal",
        "increase_energy": "Energy goal",
        "reduce_fatigue": "Fatigue goal",
        "improve_skin": "Skin goal",
        "improve_sleep": "Sleep goal",
        "reduce_stress": "Stress goal",
        "improve_mood": "Mood goal",
        "improve_digestion": "Digestion goal",
        "strengthen_immune": "Immune goal",
        "longevity": "Healthy aging goal",
        "weight_management": "Weight goal",
        "improve_focus": "Focus goal",
    }
    goal_labels = []
    for goal in goals:
        goal_lower = goal.lower()
        for key, label in GOAL_TO_LABEL.items():
            if key in goal_lower and label not in goal_labels:
                goal_labels.append(label)
    
    if goal_labels:
        return ", ".join(goal_labels[:3])
    
    return ""




def _derive_health_axes(guilds: Dict, clr: Dict, goals: list, q: Dict) -> list:
    """Derive health axis predictions from microbiome patterns."""
    axes = []
    
    fcr = clr.get("FCR")
    mdr = clr.get("MDR")
    ppr = clr.get("PPR")
    stress = q.get("stress_level")
    sleep = q.get("sleep_quality")
    bloating = q.get("bloating_severity")

    # Check for Bifido absence/depletion
    bifido_status = guilds.get("bifidobacteria", "")
    bifido_absent = "Absent" in str(bifido_status) or "Below" in str(bifido_status)

    # Gut-Brain Axis
    has_brain_goals = any(g for g in goals if any(k in g.lower() for k in ["mood", "anxiety", "stress", "sleep", "brain", "focus"]))
    if bifido_absent or (fcr is not None and fcr < -0.3) or has_brain_goals:
        verification = "CONFIRMED" if (stress and stress >= 6) or (sleep and sleep <= 7) else "predicted"
        axes.append({
            "axis": "Gut-Brain",
            "microbiome_pattern": f"{'Bifidobacteria depleted' if bifido_absent else 'Normal Bifido'}, FCR={fcr if fcr else 'N/A'} → reduced SCFA for blood-brain barrier",
            "predicted_manifestations": "Sleep issues, stress sensitivity, mood/anxiety concerns via reduced BBB support",
            "questionnaire_verification": verification,
            "severity": f"Stress {stress}/10, Sleep {sleep}/10" if stress else "Not assessed",
        })

    # Bloating / FODMAP
    fiber_status = guilds.get("fiber_degraders", "")
    if bloating and bloating >= 5:
        axes.append({
            "axis": "Bloating/FODMAP Sensitivity",
            "microbiome_pattern": f"Fiber guild: {fiber_status}. Fermentation pattern may cause gas with diverse substrates",
            "predicted_manifestations": "Post-meal bloating with fermentable foods, gas production",
            "questionnaire_verification": f"CONFIRMED (bloating {bloating}/10)",
            "severity": f"Bloating {bloating}/10",
        })

    # Gut-Immune
    proteo_status = guilds.get("proteolytic", "")
    has_immune_goal = any("immune" in g.lower() for g in goals)
    if "Above" in str(proteo_status) or has_immune_goal:
        axes.append({
            "axis": "Gut-Immune",
            "microbiome_pattern": f"Proteolytic: {proteo_status}. {'Elevated protein fermentation → reduced Treg induction' if 'Above' in str(proteo_status) else 'Standard pattern'}",
            "predicted_manifestations": "Immune resilience concerns via altered immune tone",
            "questionnaire_verification": "CONFIRMED (immune goal)" if has_immune_goal else "Not reported",
            "severity": "Preventive" if has_immune_goal else "Subclinical",
        })

    # Gut-Barrier (Mucin)
    mucin_status = guilds.get("mucin_degraders", "")
    if "Above" in str(mucin_status):
        axes.append({
            "axis": "Gut-Barrier",
            "microbiome_pattern": f"Mucin Degraders: {mucin_status}. MDR={mdr if mdr else 'N/A'} → barrier stress risk",
            "predicted_manifestations": "Gut lining thinning, potential permeability issues",
            "questionnaire_verification": "Subclinical (no direct symptoms reported)" if not bloating or bloating < 7 else f"Possible contributor to bloating {bloating}/10",
            "severity": "Moderate" if "Above" in str(mucin_status) else "Low",
        })

    # Energy/Fatigue
    has_energy_goal = any("energy" in g.lower() or "fatigue" in g.lower() for g in goals)
    if has_energy_goal:
        axes.append({
            "axis": "Energy/Metabolism",
            "microbiome_pattern": "SCFA production efficiency affects cellular energy via mitochondrial pathways",
            "predicted_manifestations": "Fatigue, reduced energy, post-meal energy dips",
            "questionnaire_verification": "CONFIRMED (energy/fatigue is a stated goal)",
            "severity": "Goal-driven",
        })

    return axes


def _build_formulation_narrative(guild_entries: list, clr_display: Dict, mix: Dict, root_causes: Dict = None) -> str:
    """Build a concise narrative explaining what the guild data means for formulation selection.
    
    Uses root_causes from the microbiome analysis (same source as the health report)
    to ensure consistency. Falls back to guild-derived narrative if root_causes unavailable.
    """
    parts = []
    
    # Primary source: root_causes from microbiome analysis (single source of truth)
    if root_causes and root_causes.get("diagnostic_flags"):
        # Diagnostic flags — same as health report
        flags = root_causes["diagnostic_flags"]
        for i, flag in enumerate(flags):
            severity = flag.get("severity", "")
            label = "Primary" if i == 0 else "Secondary"
            detail = flag.get("metric_detail", {})
            detail_str = ""
            if detail.get("clr"):
                detail_str = f" (CLR {detail['clr']:+.2f})"
            elif detail.get("actual") and detail.get("range_min"):
                detail_str = f" ({detail['actual']:.0f}% vs {detail['range_min']}-{detail.get('range_max', '?')}% range)"
            parts.append(f"{label} [{severity}]: {flag['flag']}{detail_str}.")
        
        # Primary pattern — same as health report root cause
        pp = root_causes.get("primary_pattern", {})
        if pp.get("scientific"):
            parts.append(f"Pattern: {pp['scientific']}")
        
        # Trophic impact
        trophic = root_causes.get("trophic_impact", {})
        if trophic.get("primary_bottleneck"):
            for ci in trophic.get("cascade_impacts", []):
                # Sanitize: replace "you/your" with third-person (board dashboard is not client-facing)
                _desc = ci.get('description', '')
                _desc = _desc.replace("Your ", "This sample's ").replace("your ", "this sample's ").replace(" you ", " this individual ")
                _title = ci['title'].replace("Your ", "This Sample's ").replace("your ", "this sample's ")
                parts.append(f"Impact: {_title} — {_desc}")
    else:
        # Fallback: derive from guild entries if root_causes not available
        PRIORITY_RANK = {"CRITICAL": 0, "1A": 1, "1B": 2}
        issues = []
        for g in guild_entries:
            prio = g.get("priority_level", "")
            for key, rank in PRIORITY_RANK.items():
                if key.upper() in str(prio).upper():
                    pct = g.get("abundance_pct")
                    pct_str = f" at {pct:.1f}%" if isinstance(pct, (int, float)) else ""
                    issues.append((rank, f"{g.get('name', '?')}{pct_str} — {g.get('status', '?')}, priority {prio}"))
                    break
        issues.sort(key=lambda x: x[0])
        if issues:
            parts.append(f"Primary: {issues[0][1]}.")
            if len(issues) > 1:
                parts.append(f"Secondary: {'; '.join(iss[1] for iss in issues[1:])}.")
        else:
            parts.append("All guilds at Monitor priority — maintenance formulation appropriate.")
    
    # Mix selection reasoning (always added)
    mix_name = mix.get("mix_name", "")
    mix_trigger = mix.get("primary_trigger", "")
    if mix_name:
        parts.append(f"→ Selected Mix: {mix_name}. {mix_trigger}")
    
    # Diagnostic ratios context
    ratio_notes = [f"{rn}={val}" for rn, val in clr_display.items()]
    if ratio_notes:
        parts.append(f"Diagnostic ratios: {', '.join(ratio_notes)}.")
    
    # Final sanitization: ensure no "you/your" leaked through any text field
    # (board dashboard is not client-facing — must use third-person language)
    import re as _re
    result = " ".join(parts)
    result = _re.sub(r'\bYour\b', "This sample's", result)
    result = _re.sub(r'\byour\b', "this sample's", result)
    result = _re.sub(r'\bYou\b', "This individual", result)
    result = _re.sub(r'\byou\b', "this individual", result)
    return result


def _build_evidence_tags(decisions: Dict, input_summary: Dict) -> Dict:
    """Tag each formulation component by its evidence source."""
    microbiome_driven = ["synbiotic_mix", "prebiotic_strategy"]
    questionnaire_driven = []
    both_driven = []

    # Check vitamins/minerals
    for vm in decisions.get("supplement_selection", {}).get("vitamins_minerals", []):
        informed = vm.get("informed_by", "questionnaire")
        name = vm.get("substance", "")
        if informed == "microbiome":
            microbiome_driven.append(name)
        elif informed == "both":
            both_driven.append(name)
        else:
            questionnaire_driven.append(name)

    # Supplements are typically questionnaire-driven
    for s in decisions.get("supplement_selection", {}).get("supplements", []):
        questionnaire_driven.append(s.get("substance", ""))

    return {
        "microbiome_driven": microbiome_driven,
        "questionnaire_driven": questionnaire_driven,
        "both_driven": both_driven,
    }
