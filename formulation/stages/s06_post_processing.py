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
from typing import Dict, Optional

from ..models import PipelineContext
from formulation.rules_engine import apply_timing_rules, check_polyphenol_exclusions

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


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

    # ── Polyphenol daily cap 1500mg (D.1c) ──────────────────────────────
    _apply_polyphenol_cap(ctx)

    # ── Piperine auto-addition for curcumin (D.1d) ──────────────────────
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
        if ex in name_lower:
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
    """Remove deterministic-handled substances from LLM selections.

    Logs each removed substance with the rule that triggered exclusion,
    so the pipeline output and decision trace show exactly what was dropped and why.
    """

    def _is_excluded(name):
        name_lower = name.lower()
        for ex in EXCLUDED_SUBSTANCES:
            if re.search(r'\b' + re.escape(ex) + r'\b', name_lower):
                return True
        return False

    # Collect removed items with their rule context before filtering
    removed_vms = []
    for vm in ctx.supplements.get("vitamins_minerals", []):
        if _is_excluded(vm.get("substance", "")):
            removed_vms.append(vm["substance"])

    removed_supps = []
    removed_fibers = []
    for sp in ctx.supplements.get("supplements", []):
        name = sp.get("substance", "")
        if _is_excluded(name):
            removed_supps.append(name)
        elif name.lower().strip() in EXCLUDED_FIBERS:
            removed_fibers.append(name)

    # Apply filtering
    filtered_vms = [vm for vm in ctx.supplements.get("vitamins_minerals", []) if not _is_excluded(vm.get("substance", ""))]
    filtered_supps = [sp for sp in ctx.supplements.get("supplements", [])
                      if not _is_excluded(sp.get("substance", "")) and sp.get("substance", "").lower().strip() not in EXCLUDED_FIBERS]

    ctx.supplements["vitamins_minerals"] = filtered_vms
    ctx.supplements["supplements"] = filtered_supps

    # Log each removed item with its rule
    total_removed = len(removed_vms) + len(removed_supps) + len(removed_fibers)
    if total_removed:
        for name in removed_vms:
            print(f"    → {name} (deterministic: handled by softgel/magnesium/sleep rules)")
            ctx.removal_log.add(name, "Deterministic: handled by softgel/magnesium/sleep rules", "excluded_substance_filter")
        for name in removed_supps:
            print(f"    → {name} (deterministic: handled by pipeline supplement rules)")
            ctx.removal_log.add(name, "Deterministic: handled by pipeline supplement rules", "excluded_substance_filter")
        for name in removed_fibers:
            print(f"    → {name} (deterministic: fiber/prebiotic handled by prebiotic designer)")
            ctx.removal_log.add(name, "Deterministic: fiber/prebiotic handled by prebiotic designer", "excluded_substance_filter")
        print(f"  ⚠️ Excluded {total_removed} deterministic-handled item(s) total")


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


# ─── Polyphenol cap KB lookup (cached) ────────────────────────────────────────

_POLYPHENOL_CAP_KB_CACHE: Optional[Dict] = None


def _load_polyphenol_cap_lookup() -> Dict:
    """Load supplement KB and build {normalized_name: {supplement_type, min_dose_mg, rank_priority}}.

    Used by _apply_polyphenol_cap() to identify polyphenol substances by KB lookup
    rather than hardcoded lists.

    NOTE: The polyphenol daily cap value is read exclusively from
    delivery_format_rules.json (polyphenol_delivery_classification.total_polyphenol_mass_cap_mg).
    The same value in supplements_nonvitamins.json is a mirror for readability only.

    Cached for process lifetime to avoid repeated disk I/O.
    """
    import re as _re
    global _POLYPHENOL_CAP_KB_CACHE
    if _POLYPHENOL_CAP_KB_CACHE is not None:
        return _POLYPHENOL_CAP_KB_CACHE

    kb_path = KB_DIR / "supplements_nonvitamins.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb = json.load(f)

    lookup: Dict = {}
    for entry in kb.get("supplements_flat", []):
        substance = entry.get("substance", "")
        parsed = entry.get("parsed", {})
        dose = parsed.get("dose", {})
        rank_priority = parsed.get("rank_priority", 3)
        supplement_type = entry.get("supplement_type", "")

        min_dose_mg = None
        if dose:
            unit = dose.get("unit", "mg")
            if "min" in dose:
                min_dose_mg = dose["min"] * 1000 if unit == "g" else dose["min"]
            elif "value" in dose:
                min_dose_mg = dose["value"] * 1000 if unit == "g" else dose["value"]

        info = {
            "supplement_type": supplement_type,
            "min_dose_mg": min_dose_mg,
            "rank_priority": rank_priority,
        }

        # Index by multiple name variants
        names = set()
        names.add(substance.lower().strip())
        base = _re.sub(r'\s*\(.*?\)\s*', '', substance).strip().lower()
        if base:
            names.add(base)
        names.add(entry.get("id", "").lower())

        for n in names:
            if n:
                lookup[n] = info

    _POLYPHENOL_CAP_KB_CACHE = lookup
    return lookup


def _find_polyphenol_kb(substance_name: str) -> Optional[Dict]:
    """Fuzzy match substance name against polyphenol cap KB lookup."""
    lookup = _load_polyphenol_cap_lookup()
    name_lower = substance_name.lower().strip()
    if name_lower in lookup:
        return lookup[name_lower]
    for kb_key, kb_val in lookup.items():
        if kb_key in name_lower or name_lower in kb_key:
            return kb_val
    return None


def _apply_polyphenol_cap(ctx: PipelineContext):
    """Enforce 1,500mg/day total polyphenol cap across all Fermentable Polyphenol Substrate supplements.

    Cap value is read from delivery_format_rules.json (authoritative single source of truth).
    Applies before piperine bundling — curcumin doses here are pre-piperine.

    Algorithm:
      1. Identify all supplements with supplement_type == "Fermentable Polyphenol Substrate" via KB lookup
      2. If total ≤ cap × 1.01 → return (within tolerance)
      3. Reduce lowest-priority (highest rank_priority number) to KB min_dose_mg floor, largest overage first
      4. If still over cap: drop lowest-priority substance entirely
      5. Log all reductions and drops to ctx.removal_log
    """
    # Load cap from delivery_format_rules.json (AUTHORITATIVE — do not read from supplements_nonvitamins.json)
    try:
        dfr_path = KB_DIR / "delivery_format_rules.json"
        with open(dfr_path, 'r', encoding='utf-8') as f:
            dfr = json.load(f)
        cap_mg = dfr.get("polyphenol_delivery_classification", {}).get("total_polyphenol_mass_cap_mg", 1500)
    except Exception:
        cap_mg = 1500  # safe fallback

    tolerance = cap_mg * 1.01  # 1% tolerance

    # Identify polyphenol supplements via KB lookup
    supplements = ctx.supplements.get("supplements", [])
    polyphenols = []
    for sp in supplements:
        kb = _find_polyphenol_kb(sp.get("substance", ""))
        if kb and kb.get("supplement_type") == "Fermentable Polyphenol Substrate":
            polyphenols.append(sp)

    if not polyphenols:
        return

    total = sum(sp.get("dose_mg", 0) for sp in polyphenols)
    if total <= tolerance:
        return

    print(f"  ⚠️ Polyphenol cap: {total}mg > {cap_mg}mg — reducing...")

    # Step 1: Reduce lowest-priority to KB min (highest rank_priority number first)
    def _rank(sp):
        kb = _find_polyphenol_kb(sp.get("substance", ""))
        return kb.get("rank_priority", 3) if kb else 3

    ranked = sorted(polyphenols, key=lambda sp: -_rank(sp))

    for sp in ranked:
        if total <= tolerance:
            break
        kb = _find_polyphenol_kb(sp.get("substance", ""))
        min_mg = kb.get("min_dose_mg") if kb else None
        current = sp.get("dose_mg", 0)
        if min_mg is not None and current > min_mg:
            saved = current - min_mg
            sp["dose_mg"] = min_mg
            total -= saved
            print(f"    → {sp['substance']}: {current}mg → {min_mg}mg (KB min, saved {saved}mg)")

    # Step 2: If still over, drop lowest-priority entirely
    for sp in reversed(ranked):
        if total <= tolerance:
            break
        total -= sp.get("dose_mg", 0)
        ctx.supplements["supplements"] = [
            s for s in ctx.supplements["supplements"]
            if s is not sp
        ]
        ctx.removal_log.add(
            sp["substance"],
            f"Polyphenol daily cap ({cap_mg}mg/day)",
            "polyphenol_cap",
        )
        print(f"    → {sp['substance']} dropped — polyphenol cap ({cap_mg}mg/day)")

    remaining_polyphenols = [
        sp for sp in ctx.supplements.get("supplements", [])
        if _find_polyphenol_kb(sp.get("substance", "")) and
        _find_polyphenol_kb(sp.get("substance", "")).get("supplement_type") == "Fermentable Polyphenol Substrate"
    ]
    final_total = sum(sp.get("dose_mg", 0) for sp in remaining_polyphenols)
    print(f"    ✅ Polyphenol total after cap: {final_total}mg ≤ {cap_mg}mg")


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
                vm["dose_unit"] = "mg"
                print(f"  🔧 Zinc dose: sex unknown → 8mg (conservative)")
                break
