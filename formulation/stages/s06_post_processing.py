#!/usr/bin/env python3
"""
Stage 6: Post-Processing — Apply all filters in declared order.

Input:  PipelineContext with mix, supplements, prebiotics, rule_outputs
Output: PipelineContext with supplements/prebiotics cleaned and routed

This stage calls all filters from filters/ in the correct sequence.
Each filter is a pure function: PipelineContext → PipelineContext.

NOTE: During the incremental refactor, this stage delegates to the existing
generate_formulation.py post-processing logic via the monolith. The filters/
directory contains the target architecture — individual filter modules will
be wired in as they are extracted and tested.
"""

import re
import json
from pathlib import Path
from typing import Dict

from ..models import PipelineContext
from formulation.rules_engine import apply_timing_rules, check_polyphenol_exclusions


def run(ctx: PipelineContext) -> PipelineContext:
    """Apply all post-processing filters in order."""
    print("\n─── D. ROUTING ─────────────────────────────────────────────")

    # Re-apply timing with effective goals
    selected_components = [s.get("substance", "") for s in ctx.supplements.get("supplements", [])]
    timing = apply_timing_rules(
        ctx.unified_input["questionnaire"]["lifestyle"],
        ctx.effective_goals,
        selected_components
    )
    ctx.rule_outputs["timing"] = timing

    # ── Medication exclusion enforcement (C.5a) ─────────────────────────
    if ctx.medication.excluded_substances:
        _apply_medication_exclusions(ctx)

    # ── Excluded substance filter (D.0a) ─────────────────────────────────
    _apply_excluded_substance_filter(ctx)

    # ── Vitamin inclusion gate (D.0b) ────────────────────────────────────
    _apply_vitamin_gate(ctx)

    # ── Delivery routing overrides (D.0c) ────────────────────────────────
    _apply_delivery_routing(ctx)

    # ── Polyphenol exclusion guards (D.1b) ──────────────────────────────
    _apply_polyphenol_exclusions(ctx)

    # ── Piperine auto-addition for curcumin (D.1c) ──────────────────────
    _apply_piperine_addition(ctx)

    # ── FODMAP correction (D.2d) ────────────────────────────────────────
    _apply_fodmap_correction(ctx)

    # ── Zinc dose guard (D.2e) ──────────────────────────────────────────
    _apply_zinc_dose_guard(ctx)

    print(f"  ✅ Post-processing complete")
    return ctx


# ─── Individual filter implementations ────────────────────────────────────────

def _apply_medication_exclusions(ctx: PipelineContext):
    """Remove LLM-selected items that are in the medication exclusion set."""
    excluded = ctx.medication.excluded_substances
    removed = []

    filtered_vms = []
    for vm in ctx.supplements.get("vitamins_minerals", []):
        if _is_medication_excluded(vm.get("substance", ""), excluded):
            removed.append(vm["substance"])
        else:
            filtered_vms.append(vm)
    ctx.supplements["vitamins_minerals"] = filtered_vms

    filtered_supps = []
    for sp in ctx.supplements.get("supplements", []):
        if _is_medication_excluded(sp.get("substance", ""), excluded):
            removed.append(sp["substance"])
        else:
            filtered_supps.append(sp)
    ctx.supplements["supplements"] = filtered_supps

    if removed:
        print(f"  🚫 MEDICATION EXCLUSION: Removed {len(removed)} substance(s): {removed}")
        for name in removed:
            ctx.removal_log.add(name, "Medication interaction", "medication_exclusion", "high")


def _is_medication_excluded(substance_name: str, excluded_set: set) -> bool:
    if not excluded_set:
        return False
    name_lower = substance_name.lower().strip()
    for ex in excluded_set:
        if ex in name_lower or name_lower in ex:
            return True
    return False


# Substances handled deterministically — LLM should not select these
EXCLUDED_SUBSTANCES = {"magnesium", "vitamin d", "vitamin d3", "vitamin e", "omega-3", "omega",
                       "dha", "epa", "astaxanthin", "melatonin", "l-theanine", "valerian",
                       "valerian root", "valeriana"}
EXCLUDED_FIBERS = {"phgg", "psyllium", "psyllium husk", "inulin", "pure inulin", "fos",
                   "oligofructose", "gos", "galactooligosaccharides", "beta-glucans",
                   "beta-glucans (oats)", "resistant starch", "glucomannan"}


def _apply_excluded_substance_filter(ctx: PipelineContext):
    """Remove deterministic-handled substances from LLM selections."""
    removed = []

    def _is_excluded(name):
        name_lower = name.lower()
        for ex in EXCLUDED_SUBSTANCES:
            if re.search(r'\b' + re.escape(ex) + r'\b', name_lower):
                return True
        return False

    filtered_vms = [vm for vm in ctx.supplements.get("vitamins_minerals", []) if not _is_excluded(vm.get("substance", ""))]
    filtered_supps = [sp for sp in ctx.supplements.get("supplements", [])
                      if not _is_excluded(sp.get("substance", "")) and sp.get("substance", "").lower().strip() not in EXCLUDED_FIBERS]

    n_removed = len(ctx.supplements.get("vitamins_minerals", [])) - len(filtered_vms) + len(ctx.supplements.get("supplements", [])) - len(filtered_supps)
    ctx.supplements["vitamins_minerals"] = filtered_vms
    ctx.supplements["supplements"] = filtered_supps

    if n_removed:
        print(f"  ⚠️ Excluded {n_removed} deterministic-handled item(s)")


def _apply_vitamin_gate(ctx: PipelineContext):
    """Remove unjustified vitamins (B6 without deficiency, iron for males)."""
    sex = ctx.unified_input.get("questionnaire", {}).get("demographics", {}).get("biological_sex", "").lower()
    deficiencies = [d.lower() for d in ctx.rule_outputs.get("therapeutic_triggers", {}).get("reported_deficiencies", []) if d]
    mb_needs = [n.get("vitamin", "").lower() for n in ctx.rule_outputs.get("health_claims", {}).get("microbiome_vitamin_needs", [])]
    removed = []

    filtered = []
    for vm in ctx.supplements.get("vitamins_minerals", []):
        substance_lower = vm.get("substance", "").lower()

        # Iron gate: males excluded unless deficiency
        if "iron" in substance_lower and sex == "male" and not any("iron" in d for d in deficiencies):
            removed.append(f"{vm['substance']} (iron excluded for males)")
            ctx.removal_log.add(vm["substance"], "Iron excluded for males", "vitamin_gate")
            continue

        # B6 restricted unless deficiency/microbiome signal
        if "b6" in substance_lower:
            has_b6 = any("b6" in d for d in deficiencies) or any("b6" in n for n in mb_needs) or vm.get("therapeutic", False)
            if not has_b6:
                removed.append(f"{vm['substance']} (B6 restricted — neuropathy risk)")
                ctx.removal_log.add(vm["substance"], "B6 restricted", "vitamin_gate")
                continue

        filtered.append(vm)

    if removed:
        ctx.supplements["vitamins_minerals"] = filtered
        ctx.vitamin_gate_removed = removed
        print(f"  🚫 Vitamin gate: {len(removed)} removed")


def _apply_delivery_routing(ctx: PipelineContext):
    """Route fat-soluble vitamins to softgel, others to morning capsule."""
    FAT_SOLUBLE = {"vitamin a", "vitamin d", "vitamin d3", "vitamin e"}
    for vm in ctx.supplements.get("vitamins_minerals", []):
        substance_lower = vm.get("substance", "").lower()
        if any(fs in substance_lower for fs in FAT_SOLUBLE):
            vm["delivery"] = "softgel"
        else:
            vm["delivery"] = "morning_wellness_capsule"


def _apply_polyphenol_exclusions(ctx: PipelineContext):
    """Check polyphenol exclusions based on medical conditions."""
    medical = ctx.unified_input.get("questionnaire", {}).get("medical", {})
    demographics = ctx.unified_input.get("questionnaire", {}).get("demographics", {})
    result = check_polyphenol_exclusions(medical, demographics)

    ctx.excluded_polyphenols = set(result.get("excluded_substances", []))
    if ctx.excluded_polyphenols:
        for reason in result.get("reasoning", []):
            print(f"  🚨 {reason}")
        before = len(ctx.supplements.get("supplements", []))
        ctx.supplements["supplements"] = [
            sp for sp in ctx.supplements.get("supplements", [])
            if sp.get("substance", "").lower() not in ctx.excluded_polyphenols
        ]
        removed = before - len(ctx.supplements.get("supplements", []))
        if removed:
            print(f"  🗑️ Removed {removed} excluded polyphenol(s)")


def _apply_piperine_addition(ctx: PipelineContext):
    """Auto-add piperine at 1:100 ratio for curcumin."""
    for sp in ctx.supplements.get("supplements", []):
        if "curcumin" in sp.get("substance", "").lower():
            curcumin_dose = sp.get("dose_mg", 500)
            piperine_dose = round(curcumin_dose / 100, 1)
            sp["substance"] = f"Curcumin {curcumin_dose}mg (+ {piperine_dose}mg Piperine)"
            sp["dose_mg"] = curcumin_dose + piperine_dose
            sp["piperine_auto_added"] = True
            ctx.piperine_applied = True
            ctx.add_trace("piperine_auto", "Curcumin + Piperine",
                          f"Auto-added Piperine at 1:100 ({piperine_dose}mg)")
            print(f"  → Curcumin {curcumin_dose}mg + Piperine {piperine_dose}mg bundled")
            break


def _apply_fodmap_correction(ctx: PipelineContext):
    """Enforce FODMAP classification for known FODMAP substances."""
    KNOWN_FODMAP = {"lactulose", "gos", "fos", "inulin"}
    corrections = 0
    for pb in ctx.prebiotics.get("prebiotics", []):
        name_lower = pb.get("substance", "").lower().strip()
        if any(f in name_lower for f in KNOWN_FODMAP) and not pb.get("fodmap", False):
            pb["fodmap"] = True
            corrections += 1
    if corrections:
        new_total = sum(pb.get("dose_g", 0) for pb in ctx.prebiotics.get("prebiotics", []) if pb.get("fodmap"))
        ctx.prebiotics["total_fodmap_grams"] = new_total
        print(f"  🔧 FODMAP correction: {corrections} substance(s) → total FODMAP: {new_total}g")


def _apply_zinc_dose_guard(ctx: PipelineContext):
    """Default zinc to 8mg (female/conservative) when sex is unknown."""
    sex = ctx.unified_input.get("questionnaire", {}).get("demographics", {}).get("biological_sex", "")
    if not sex or sex.lower() not in ("male", "female"):
        for vm in ctx.supplements.get("vitamins_minerals", []):
            if "zinc" in vm.get("substance", "").lower() and vm.get("dose_value", 0) > 8:
                vm["dose_value"] = 8
                vm["dose"] = "8 mg/d"
                print(f"  🔧 Zinc dose: sex unknown → 8mg (conservative)")
                break
