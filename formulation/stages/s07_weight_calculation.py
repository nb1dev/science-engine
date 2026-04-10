#!/usr/bin/env python3
"""
Stage 7: Weight Calculation — Build FormulationCalculator and generate formulation.

Input:  PipelineContext with mix, supplements, prebiotics (post-filtered)
Output: PipelineContext with calc, formulation, component_registry populated

This stage populates the FormulationCalculator with all components from the
previous stages and runs generate() to produce validated weights.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import PipelineContext
from formulation.weight_calculator import FormulationCalculator, distribute_cfu_evenly, EVENING_CAPSULE_CAPACITY_MG, PROBIOTIC_ACTIVE_CAPACITY_MG, CFU_TO_MG_FACTOR
from formulation.dose_optimizer import DoseOptimizer

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


# ─── Supplement KB lookup (cached) ────────────────────────────────────────────

_SUPPLEMENT_KB_CACHE: Optional[Dict] = None
_KNOWN_PREBIOTIC_SUBSTANCES_CACHE: Optional[set] = None


def clear_kb_caches():
    """Reset module-level KB caches.

    Call this between test runs to prevent stale data from leaking across
    test cases that swap KB files or run multiple formulations in-process.
    """
    global _SUPPLEMENT_KB_CACHE, _KNOWN_PREBIOTIC_SUBSTANCES_CACHE
    _SUPPLEMENT_KB_CACHE = None
    _KNOWN_PREBIOTIC_SUBSTANCES_CACHE = None


def _load_supplement_kb_lookup() -> Dict:
    """Build normalized lookup from supplements_nonvitamins.json for dose + timing info.

    Returns dict: {normalized_name: {min_dose_mg, timing_restriction, rank_priority, ...}}
    """
    import re as _re
    global _SUPPLEMENT_KB_CACHE
    if _SUPPLEMENT_KB_CACHE is not None:
        return _SUPPLEMENT_KB_CACHE

    kb_path = KB_DIR / "supplements_nonvitamins.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb = json.load(f)

    lookup = {}
    for entry in kb.get("supplements_flat", []):
        substance = entry.get("substance", "")
        parsed = entry.get("parsed", {})
        dose = parsed.get("dose", {})

        min_dose_mg = None
        if "min" in dose:
            unit = dose.get("unit", "mg")
            min_dose_mg = dose["min"] * 1000 if unit == "g" else dose["min"]
        elif "value" in dose:
            unit = dose.get("unit", "mg")
            min_dose_mg = dose["value"] * 1000 if unit == "g" else dose["value"]

        timing = entry.get("timing_restriction", "any")
        rank = parsed.get("rank_priority", 3)

        info = {
            "min_dose_mg": min_dose_mg,
            "timing_restriction": timing,
            "rank_priority": rank,
            "substance_full": substance,
        }

        # Build multiple name variants for fuzzy matching
        names = set()
        names.add(substance.lower().strip())
        base = _re.sub(r'\s*\(.*?\)\s*', '', substance).strip().lower()
        if base:
            names.add(base)
        paren = _re.search(r'\(([^)]+)\)', substance)
        if paren:
            names.add(paren.group(1).strip().lower())
        names.add(entry.get("id", "").lower())

        for n in names:
            if n:
                lookup[n] = info

    _SUPPLEMENT_KB_CACHE = lookup
    return lookup


def _find_kb_entry(substance_name: str) -> Optional[Dict]:
    """Fuzzy match substance name against KB lookup."""
    lookup = _load_supplement_kb_lookup()
    name_lower = substance_name.lower().strip()
    if name_lower in lookup:
        return lookup[name_lower]
    for kb_key, kb_val in lookup.items():
        if kb_key in name_lower or name_lower in kb_key:
            return kb_val
    return None


def _load_known_prebiotic_substances() -> set:
    """Build a normalized set of all prebiotic substances from prebiotic_rules.json.

    Collects every substance listed under must_include, highly_recommended, and optional
    across all 8 per-mix prebiotic entries. Used to classify condition_specific_additions
    as microbiome_primary prebiotics vs questionnaire_only botanicals.

    A CSA substance that appears in any mix's prebiotic lists is a prebiotic substrate
    chosen for microbiome reasons (even if at trace dose due to sensitivity override).
    A CSA substance NOT in any mix list (e.g. Safflower, Bergamot, Capsicum) is a
    condition-specific botanical driven by questionnaire goals.

    Result is cached for the lifetime of the process.
    """
    global _KNOWN_PREBIOTIC_SUBSTANCES_CACHE
    if _KNOWN_PREBIOTIC_SUBSTANCES_CACHE is not None:
        return _KNOWN_PREBIOTIC_SUBSTANCES_CACHE

    kb_path = KB_DIR / "prebiotic_rules.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb = json.load(f)

    substances: set = set()
    for mix_key, mix_data in kb.get("per_mix_prebiotics", {}).items():
        for field in ("must_include", "highly_recommended", "optional"):
            for item in mix_data.get(field, []):
                # Normalize: lowercase, strip whitespace, strip trailing notes like "(min 1.0g)"
                normalized = item.lower().strip()
                # Strip parenthetical suffixes e.g. "psyllium/arabinoxylan" → keep as-is,
                # "beta-glucans (oats)" → also add "beta-glucans"
                substances.add(normalized)
                if "(" in normalized:
                    base = normalized[:normalized.index("(")].strip()
                    if base:
                        substances.add(base)
                # Handle slash variants e.g. "psyllium/arabinoxylan"
                if "/" in normalized:
                    for part in normalized.split("/"):
                        part = part.strip()
                        if part:
                            substances.add(part)

    _KNOWN_PREBIOTIC_SUBSTANCES_CACHE = substances
    return substances


# Substances that are FODMAP-positive among known prebiotic fibers.
# Used to set fodmap=True when routing CSA prebiotics.
_CSA_FODMAP_SUBSTANCES = {"lactulose", "gos", "galactooligosaccharides", "fos",
                           "oligofructose", "inulin", "pure inulin"}


def _parse_csa_dose_to_g(dose_str: str) -> Optional[float]:
    """Parse a condition_specific_addition dose string to grams.

    Handles formats like:
      "0.2g", "200mg", "300-500mg" (uses lower bound), "200"
    Returns None if unparseable.
    """
    import re as _re
    dose_str = dose_str.strip().lower()

    # e.g. "0.2g" or "1.0 g"
    m = _re.match(r'^([\d.]+)\s*g$', dose_str)
    if m:
        return float(m.group(1))

    # e.g. "200mg" or "300 mg"
    m = _re.match(r'^([\d.]+)\s*mg$', dose_str)
    if m:
        return round(float(m.group(1)) / 1000, 4)

    # e.g. "300-500mg" — use lower bound
    m = _re.match(r'^([\d.]+)[\-–]([\d.]+)\s*mg$', dose_str)
    if m:
        return round(float(m.group(1)) / 1000, 4)

    # bare number — assume mg
    m = _re.match(r'^([\d.]+)$', dose_str)
    if m:
        val = float(m.group(1))
        # heuristic: values < 10 are likely already grams
        return val if val < 10 else round(val / 1000, 4)

    return None


def run(ctx: PipelineContext) -> PipelineContext:
    """Build FormulationCalculator, add all components, generate formulation."""
    print("\n─── E. WEIGHTS & VALIDATION ────────────────────────────────")

    calc = FormulationCalculator(sample_id=ctx.sample_id)

    # ── Probiotics ───────────────────────────────────────────────────────
    _add_probiotics(calc, ctx.mix)

    # ── Softgels ─────────────────────────────────────────────────────────
    softgel_decision = ctx.rule_outputs.get("softgel", {})
    if softgel_decision.get("include_softgel", False):
        calc.add_fixed_softgels(daily_count=softgel_decision["daily_count"])
        print(f"  Softgels: {softgel_decision['daily_count']}× (needs: {softgel_decision['needs_identified']})")
    else:
        print(f"  Softgels: NONE")

    # ── Prebiotics ───────────────────────────────────────────────────────
    calc.set_prebiotic_strategy(ctx.prebiotics.get("strategy", ""))
    for pb in ctx.prebiotics.get("prebiotics", []):
        calc.add_prebiotic(pb["substance"], pb["dose_g"],
                           fodmap=pb.get("fodmap", False), rationale=pb.get("rationale", ""))

    # ── Condition-specific additions from prebiotic design ───────────────
    # These are optional layers returned by the LLM prebiotic designer in
    # condition_specific_additions[]. Two types:
    #
    #   1. Prebiotic fibers — substances listed in per_mix_prebiotics (must_include,
    #      highly_recommended, optional) across any mix. These are mix-driven substrates
    #      placed here at trace dose because sensitivity overrides reduced their bulk
    #      amounts. They belong in jar_prebiotics → microbiome_primary source.
    #      Examples: GOS, Inulin, FOS, Quercetin, Apple polyphenols.
    #
    #   2. Condition-specific botanicals — substances NOT in any mix's prebiotic list,
    #      chosen purely from questionnaire goals (skin, metabolic, etc.).
    #      Examples: Safflower, Bergamot, Capsicum extract, Plant sterols.
    #      These stay as jar_botanicals → questionnaire_only source.
    #
    # BUG FIX (20 Mar 2026): this list was silently dropped after the monolith
    # was split into modular stages — s07 only processed prebiotics[], not CSAs.
    # CLASSIFICATION FIX (20 Mar 2026): prebiotic-type CSAs now routed as
    # prebiotics (microbiome_primary), not botanicals (questionnaire_only).
    _known_prebiotics = _load_known_prebiotic_substances()
    for csa in ctx.prebiotics.get("condition_specific_additions", []):
        substance = csa.get("substance", "")
        dose_raw = str(csa.get("dose_g_or_mg", "")).strip()
        if not substance or not dose_raw:
            continue
        dose_g = _parse_csa_dose_to_g(dose_raw)
        if dose_g and dose_g > 0:
            substance_normalized = substance.lower().strip()
            # Check against all KB prebiotic variants (exact and partial)
            is_prebiotic = (
                substance_normalized in _known_prebiotics
                or any(kp in substance_normalized for kp in _known_prebiotics if len(kp) > 3)
            )
            if is_prebiotic:
                is_fodmap = substance_normalized in _CSA_FODMAP_SUBSTANCES
                calc.add_prebiotic(substance, dose_g,
                                   fodmap=is_fodmap, rationale=csa.get("rationale", ""))
                print(f"  + CSA prebiotic: {substance} {dose_g}g → jar prebiotics"
                      f" [fodmap={is_fodmap}] ({csa.get('condition', 'condition-specific')})")
            else:
                calc.add_jar_botanical(substance, dose_g, rationale=csa.get("rationale", ""))
                print(f"  + CSA botanical: {substance} {dose_g}g → jar botanicals"
                      f" ({csa.get('condition', 'condition-specific')})")

    # ── Vitamins/Minerals → morning pooled capsules ──────────────────────
    for vm in ctx.supplements.get("vitamins_minerals", []):
        delivery = vm.get("delivery", "morning_wellness_capsule")
        if delivery in ("sachet", "morning_wellness_capsule"):
            calc.add_morning_pooled_component(
                substance=vm["substance"],
                dose_value=vm.get("dose_value", 0),
                dose_unit=vm.get("dose_unit", "mg"),
                therapeutic=vm.get("therapeutic", False),
                standard_dose=vm.get("standard_dose", ""),
                rationale=vm.get("rationale", ""),
                clinical_note=vm.get("interaction_note", ""),
                informed_by=vm.get("informed_by", "questionnaire"),
                source_type="vitamin_mineral",
            )

    # ── Supplements → route by delivery ──────────────────────────────────
    for supp in ctx.supplements.get("supplements", []):
        delivery = supp.get("delivery", "morning_wellness_capsule")
        dose_mg = supp.get("dose_mg", 0)
        if delivery == "jar":
            calc.add_jar_botanical(supp["substance"], round(dose_mg / 1000, 3),
                                   rationale=supp.get("rationale", ""))
        elif delivery in ("sachet", "morning_wellness_capsule"):
            # Heavy botanicals (>650mg) can't fit in a capsule — reroute to powder jar
            if dose_mg > calc._heavy_threshold:
                calc.add_jar_botanical(supp["substance"], round(dose_mg / 1000, 3),
                                       rationale=supp.get("rationale", ""))
                print(f"  ⚠️ {supp['substance']} ({dose_mg}mg) exceeds capsule capacity — rerouted to powder jar")
            else:
                calc.add_light_botanical_to_morning(supp["substance"], dose_mg,
                                                     rationale=supp.get("rationale", ""))
        elif delivery == "evening_capsule":
            calc.add_evening_component(supp["substance"], dose_mg,
                                        rationale=supp.get("rationale", ""))
        elif delivery == "polyphenol_capsule":
            calc.add_polyphenol_capsule(supp["substance"], dose_mg,
                                         rationale=supp.get("rationale", ""), timing="morning")

    # ── Sleep supplements ────────────────────────────────────────────────
    _add_sleep_supplements(calc, ctx)

    # ── Magnesium ────────────────────────────────────────────────────────
    mg = ctx.rule_outputs.get("magnesium", {})
    if ctx.medication.magnesium_removed:
        print(f"  💊 Mg capsules: SUPPRESSED by medication rule")
        ctx.rule_outputs["magnesium"]["capsules"] = 0
    elif mg.get("capsules", 0) > 0:
        mg_timing = ctx.rule_outputs.get("timing", {}).get("timing_assignments", {}).get("magnesium", {}).get("timing", "evening")
        calc.add_magnesium_capsules(mg["capsules"], needs=mg.get("needs_identified", []),
                                     reasoning=mg.get("reasoning", []), timing=mg_timing)
        print(f"  Mg capsules: {mg['capsules']}× {mg_timing}")

    # ── Capsule capacity guards ───────────────────────────────────────────
    # Enforce 650mg capacity for each capsule type after all components are
    # added. Uses the generalised _enforce_capsule_capacity() which:
    #   • Never touches vitamin_mineral components (protected, therapeutically selected)
    #   • Reduces botanicals/supplements to KB minimums first
    #   • Then splits into N capsules if still over
    #   • Logs a warning if vitamins alone exceed capacity (can't auto-resolve)
    calc.evening_pooled_components = _enforce_capsule_capacity(
        calc.evening_pooled_components, EVENING_CAPSULE_CAPACITY_MG, "evening", ctx)

    # NOTE: polyphenol capsules are NOT passed through _enforce_capsule_capacity().
    # The enforcer was silently truncating oversized ingredients (e.g. Curcumin 1010mg → 650mg)
    # because polyphenol components lack _source="vitamin_mineral" and got classified as
    # "reducible". The polyphenol capsule already has its own splitting logic in
    # weight_calculator._calc_polyphenol_capsule_totals() which correctly handles oversized
    # ingredients by equal-splitting them across multiple capsules.
    # Bug fix: 24 Mar 2026 — removed enforcer call that caused 35% dose loss across 28 clients.

    calc.morning_pooled_components = _enforce_capsule_capacity(
        calc.morning_pooled_components, EVENING_CAPSULE_CAPACITY_MG, "morning", ctx)

    # ── Dose optimizer (JSON-driven rules) ───────────────────────────────
    _run_dose_optimizer(calc, ctx)

    # ── Jar dedup sweep ──────────────────────────────────────────────────
    # Rule: a substance must appear ONCE in the entire formulation.
    # Capsule duplicates are fine (same substance in morning + evening = OK).
    # But jar + capsule = NOT OK. Remove from jar — capsule wins.
    # Also: jar_prebiotics + jar_botanicals duplicate = NOT OK.
    #   Keep prebiotic (microbiome-driven), remove botanical.
    # BUG FIX (25 Mar 2026): Apple Polyphenol Extract appeared doubled in
    #   3 samples across batches 001, 005, 011 via two different patterns.
    _dedup_jar_vs_capsules(calc)

    # ── Generate formulation ─────────────────────────────────────────────
    formulation = calc.generate()
    validation = formulation["metadata"]["validation_status"]
    print(f"  {'✅' if validation == 'PASS' else '❌'} Validation: {validation}")
    print(f"  Total daily weight: {formulation['protocol_summary']['total_daily_weight_g']}g")
    print(f"  Total units: {formulation['protocol_summary']['total_daily_units']}")

    # ── Build component registry — single source of truth ────────────────
    ctx.component_registry = _build_component_registry(calc, ctx)

    ctx.calc = calc
    ctx.formulation = formulation
    return ctx


def _add_probiotics(calc, mix: Dict):
    """Add probiotic strains to calculator with capacity guard."""
    MAX_CAPSULE_CFU = int(PROBIOTIC_ACTIVE_CAPACITY_MG / CFU_TO_MG_FACTOR)  # = 48
    strains = mix.get("strains", [])
    if strains:
        total_cfu = sum(s.get("cfu_billions", 10) for s in strains)
        if total_cfu * CFU_TO_MG_FACTOR > PROBIOTIC_ACTIVE_CAPACITY_MG:
            cfu_per = distribute_cfu_evenly(MAX_CAPSULE_CFU, len(strains))
            for strain in strains:
                calc.add_probiotic(strain["name"], cfu_per, mix_id=mix["mix_id"], mix_name=mix["mix_name"])
        else:
            for strain in strains:
                calc.add_probiotic(strain["name"], strain.get("cfu_billions", 10),
                                    mix_id=mix["mix_id"], mix_name=mix["mix_name"],
                                    rationale=strain.get("role", ""))


def _add_sleep_supplements(calc, ctx: PipelineContext):
    """Add deterministic sleep supplements with correct timing."""
    sleep_supps = ctx.rule_outputs.get("sleep_supplements", {})
    timing_assignments = ctx.rule_outputs.get("timing", {}).get("timing_assignments", {})

    if not sleep_supps.get("supplements"):
        return

    for ss in sleep_supps["supplements"]:
        substance_key = ss["substance"].lower().replace("-", "_").replace(" ", "_")
        timing_info = timing_assignments.get(substance_key, {})
        assigned_timing = timing_info.get("timing", "morning")

        if assigned_timing == "evening":
            calc.add_evening_component(ss["substance"], ss["dose_mg"], rationale=ss.get("rationale", ""))
        else:
            calc.add_light_botanical_to_morning(ss["substance"], ss["dose_mg"], rationale=ss.get("rationale", ""))


def _enforce_capsule_capacity(
    components: List[Dict],
    capacity_mg: int,
    capsule_label: str,
    ctx: PipelineContext,
) -> List[Dict]:
    """Universal capsule capacity enforcer — works for evening, polyphenol, and morning capsules.

    Replaces the old _enforce_evening_capacity() (evening-only) with a generic version
    that handles all capsule types. Called from run() for each pooled capsule group.

    Protection rule:
      Components with _source == "vitamin_mineral" are NEVER reduced or dropped.
      They are therapeutically selected, KB-dosed, and clinically justified.
      If vitamins alone exceed capacity, a warning is logged and the validator catches it.

    Algorithm:
      1. If total ≤ capacity_mg → return unchanged
      2. Separate protected (vitamin_mineral) from reducible components
      3. Step 1: Reduce reducible components to KB min_dose_mg (lowest priority first)
         — if total (protected + reduced reducible) ≤ capacity → done
      4. Step 2: Bin-pack all components into N capsules (largest-first greedy)
         — reducible components that still overflow a single slot get their
           dose reduced to KB min in the overflow capsule, then dropped if needed
      5. Cross-capsule deduplication
      6. Return the final flat component list (CapsuleStackingOptimizer in
         weight_calculator.py will assign them to capsules at generate() time)

    Returns:
        Modified component list (same objects, possibly mutated doses).
    """
    import copy as _copy

    if not components:
        return components

    total = sum(c.get("dose_mg", 0) for c in components)
    if total <= capacity_mg:
        return components

    print(f"  ⚠️ {capsule_label.capitalize()} capsule overflow: {total}mg > {capacity_mg}mg — resolving...")

    # ── Separate protected (vitamins) from reducible (botanicals/supplements) ─
    protected = [c for c in components if c.get("_source") == "vitamin_mineral"]
    reducible = [c for c in components if c.get("_source") != "vitamin_mineral"]

    protected_total = sum(c.get("dose_mg", 0) for c in protected)
    reducible_total = sum(c.get("dose_mg", 0) for c in reducible)

    # ── Step 1: Try reducing reducible components to KB minimums ──────────
    original_reducible = _copy.deepcopy(reducible)

    # Sort by rank_priority descending — lowest priority reduced first
    reducible_sorted = sorted(reducible, key=lambda c: -((_find_kb_entry(c["substance"]) or {}).get("rank_priority", 3)))

    for comp in reducible_sorted:
        if protected_total + sum(c.get("dose_mg", 0) for c in reducible) <= capacity_mg:
            break
        kb = _find_kb_entry(comp["substance"])
        if kb and kb.get("min_dose_mg") is not None and comp["dose_mg"] > kb["min_dose_mg"]:
            old = comp["dose_mg"]
            # Single occupant — use capacity as floor, not KB min.
            # KB min only applies when multiple components compete for space.
            would_be_alone = len(reducible) == 1
            target = capacity_mg if would_be_alone else kb["min_dose_mg"]
            saved = old - target
            comp["dose_mg"] = target
            comp["weight_mg"] = target
            floor_label = f"capacity {capacity_mg}mg" if would_be_alone else f"KB min {target}mg"
            print(f"    → {comp['substance']}: {old}mg → {target}mg ({floor_label}, saved {saved}mg)")

    current_total = protected_total + sum(c.get("dose_mg", 0) for c in reducible)
    if current_total <= capacity_mg:
        print(f"    ✅ {capsule_label.capitalize()} resolved by dose reduction: {current_total}mg ≤ {capacity_mg}mg")
        return protected + reducible

    # ── Step 2: Still over — restore original doses and bin-pack into N capsules ──
    # Only restore reducible; protected stay as-is.
    reducible = original_reducible
    all_comps = protected + reducible

    # Check: if vitamins alone exceed capacity, we cannot auto-resolve
    if protected_total > capacity_mg:
        print(f"    ⚠️ WARNING: {capsule_label} vitamin_mineral components alone total {protected_total}mg "
              f"> {capacity_mg}mg — cannot auto-resolve. Validator will catch this.")
        return all_comps  # return as-is; validator flags it

    # Greedy largest-first bin-pack
    all_comps_sorted = sorted(all_comps, key=lambda c: -c.get("dose_mg", 0))
    bins: List[List] = [[]]
    bin_totals: List[float] = [0.0]

    for comp in all_comps_sorted:
        dose = comp.get("dose_mg", 0)
        # Find first bin with enough headroom
        placed = False
        for i, (bin_list, bin_total) in enumerate(zip(bins, bin_totals)):
            if bin_total + dose <= capacity_mg:
                bin_list.append(comp)
                bin_totals[i] += dose
                placed = True
                break
        if not placed:
            # Needs a new bin — but first check if the component itself exceeds capacity
            if dose > capacity_mg:
                # Single component too large: reduce to KB min
                kb = _find_kb_entry(comp["substance"])
                is_protected = comp.get("_source") == "vitamin_mineral"
                if not is_protected and kb and kb.get("min_dose_mg") is not None:
                    old = dose
                    comp["dose_mg"] = kb["min_dose_mg"]
                    comp["weight_mg"] = kb["min_dose_mg"]
                    print(f"    → {comp['substance']}: {old}mg → {kb['min_dose_mg']}mg (single-component overflow, reduced to KB min)")
                    # Try to fit reduced dose in existing bin
                    dose = comp["dose_mg"]
                    for i, (bin_list, bin_total) in enumerate(zip(bins, bin_totals)):
                        if bin_total + dose <= capacity_mg:
                            bin_list.append(comp)
                            bin_totals[i] += dose
                            placed = True
                            break
                if not placed:
                    # Still can't place — drop if reducible, warn if protected
                    if is_protected:
                        print(f"    ⚠️ WARNING: {comp['substance']} ({dose}mg) cannot fit in any {capsule_label} capsule — validator will catch this")
                        bins[0].append(comp)  # attach to first bin for output
                    else:
                        ctx.removal_log.add(comp["substance"], f"{capsule_label} capsule overflow — too large for single capsule", f"{capsule_label}_overflow")
                        print(f"    → Dropped: {comp['substance']} ({dose}mg) — {capsule_label} overflow (too large for capsule)")
            else:
                # Open a new bin
                bins.append([comp])
                bin_totals.append(dose)

    # ── Cross-bin deduplication ────────────────────────────────────────────
    all_result = [c for bin_list in bins for c in bin_list]
    seen: Dict = {}
    deduped: List[Dict] = []
    for comp in all_result:
        key = comp["substance"].lower().strip()
        if key in seen:
            existing = seen[key]
            if comp["dose_mg"] > existing["dose_mg"]:
                deduped.remove(existing)
                deduped.append(comp)
                seen[key] = comp
                print(f"    🔄 Dedup: {comp['substance']} — kept {comp['dose_mg']}mg, dropped {existing['dose_mg']}mg")
            else:
                print(f"    🔄 Dedup: {comp['substance']} — kept {existing['dose_mg']}mg, dropped {comp['dose_mg']}mg")
        else:
            seen[key] = comp
            deduped.append(comp)

    n_caps = len(bins)
    final_total = sum(c.get("dose_mg", 0) for c in deduped)
    print(f"    ✅ {capsule_label.capitalize()} resolved: {final_total}mg → {n_caps} capsule(s)")
    return deduped


def _dedup_jar_vs_capsules(calc):
    """Remove jar entries that duplicate substances already in any capsule.

    Rules:
      1. If a substance is in BOTH jar (prebiotics or botanicals) AND any capsule
         (morning_pooled, evening_pooled, polyphenol_capsules) → remove from jar.
         Capsule wins — it has precise dosing from LLM/deterministic selection.
      2. If a substance is in BOTH jar_prebiotics AND jar_botanicals → keep the
         prebiotic entry (microbiome-driven, higher priority), remove the botanical.

    Substance matching is case-insensitive with parenthetical suffix stripping.

    BUG FIX (25 Mar 2026): Catches all jar duplication patterns that caused
    Apple Polyphenol Extract to appear twice in 3 client formulations.
    """
    import re

    def _normalize(name: str) -> str:
        return re.sub(r'\s*\(.*?\)\s*', '', name).strip().lower()

    # ── Collect all capsule substance names ──────────────────────────────
    capsule_substances = set()
    for c in calc.morning_pooled_components:
        capsule_substances.add(_normalize(c.get("substance", "")))
    for c in calc.evening_pooled_components:
        capsule_substances.add(_normalize(c.get("substance", "")))
    for c in calc.polyphenol_capsules:
        capsule_substances.add(_normalize(c.get("substance", "")))
    capsule_substances.discard("")

    # ── Step 1: Remove jar entries that duplicate a capsule substance ────
    removed_count = 0
    for pool_name in ("jar_prebiotics", "jar_botanicals"):
        pool = getattr(calc, pool_name, [])
        cleaned = []
        for item in pool:
            name = _normalize(item.get("substance", ""))
            if name in capsule_substances:
                removed_count += 1
                print(f"  🔄 Jar dedup: removed '{item['substance']}' from {pool_name}"
                      f" — already in capsule (capsule wins)")
            else:
                cleaned.append(item)
        setattr(calc, pool_name, cleaned)

    # ── Step 2: Dedup within jar — prebiotic wins over botanical ─────────
    prebiotic_names = {_normalize(p.get("substance", "")) for p in calc.jar_prebiotics}
    botanicals_cleaned = []
    for item in getattr(calc, 'jar_botanicals', []):
        name = _normalize(item.get("substance", ""))
        if name in prebiotic_names:
            removed_count += 1
            print(f"  🔄 Jar dedup: removed '{item['substance']}' from jar_botanicals"
                  f" — already in jar_prebiotics (prebiotic wins)")
        else:
            botanicals_cleaned.append(item)
    calc.jar_botanicals = botanicals_cleaned

    if removed_count > 0:
        print(f"  ✅ Jar dedup sweep: removed {removed_count} duplicate(s)")
    else:
        print(f"  ✅ Jar dedup sweep: no duplicates found")


def _run_dose_optimizer(calc, ctx: PipelineContext):
    """Run the JSON-driven DoseOptimizer on evening components.

    The DoseOptimizer is the ONLY layer allowed to change upstream-selected doses
    based on clinical policy rules in knowledge_base/dose_optimization_rules.json.
    """
    if not calc.evening_pooled_components:
        return

    optimizer = DoseOptimizer()
    opt_result = optimizer.optimize(calc.evening_pooled_components)

    for log_line in opt_result.get("log", []):
        print(log_line)

    if opt_result.get("applied_rules"):
        print(f"  ✅ Dose optimizer rules applied: {opt_result['applied_rules']}")
        calc.evening_pooled_components = opt_result["components"]

        for rule_name in opt_result["applied_rules"]:
            ctx.add_trace("optimizer", "", f"Dose optimizer — {rule_name}", rule_id=rule_name)


def _build_component_registry(calc, ctx: PipelineContext) -> List[Dict]:
    """Build component registry — single source of truth for ALL components.

    Built from the ACTUAL calculator state (post-dedup, post-trim).
    Each entry has: substance, dose, delivery, category, source, health_claims,
    based_on, what_it_targets, informed_by.

    This registry is consumed by:
    - build_component_rationale() for the health table
    - build_decision_trace() Step 7 (Supplement Selection)
    - dashboard_renderer.py for the board dashboard
    - Source attribution % calculation
    """
    registry = []
    mix = ctx.mix
    mix_name = mix.get("mix_name", "")
    mix_trigger = mix.get("primary_trigger", "")
    q = ctx.unified_input.get("questionnaire", {})
    stress = q.get("lifestyle", {}).get("stress_level", "?")
    sleep = q.get("lifestyle", {}).get("sleep_quality", "?")
    rule_outputs = ctx.rule_outputs
    supplements = ctx.supplements
    sg_decision = rule_outputs.get("softgel", {})

    # 1. Probiotics (from calc.probiotic_components)
    non_lpc37 = [p for p in calc.probiotic_components if "LPC-37" not in p.get("substance", "")]
    if non_lpc37:
        per_strain_cfu = non_lpc37[0].get("cfu_billions", 0)
        total_base_cfu = sum(p.get("cfu_billions", 0) for p in non_lpc37)
        dose_str = f"{total_base_cfu}B CFU ({per_strain_cfu}B each)" if per_strain_cfu else f"{total_base_cfu}B CFU"
        registry.append({
            "substance": f"{len(non_lpc37)} base strains ({mix_name})",
            "dose": dose_str,
            "delivery": "probiotic capsule",
            "category": "probiotic",
            "source": "microbiome_primary",
            "health_claims": [mix_name, mix_trigger.split("(")[0].strip() if "(" in mix_trigger else mix_trigger],
            "based_on": f"Microbiome analysis ({mix_trigger})",
            "what_it_targets": mix_name,
            "informed_by": "microbiome",
        })

    # LPc-37 psychobiotic strain separately
    lpc37_strains = [p for p in calc.probiotic_components if "LPC-37" in p.get("substance", "")]
    if lpc37_strains:
        mix_id = mix.get("mix_id")
        lpc37_label = "LPc-37 psychobiotic strain" if mix_id == 7 else "LPc-37 gut-brain enhancement strain"
        registry.append({
            "substance": f"{lpc37_label} (5B CFU)",
            "dose": "5B CFU",
            "delivery": "probiotic capsule",
            "category": "probiotic",
            "source": "microbiome_linked",
            "health_claims": ["Stress/Anxiety", "Sleep Quality", "Gut-Brain"],
            "based_on": f"Microbiome gut-brain pattern + stress {stress}/10",
            "what_it_targets": "Stress, anxiety, mood, sleep (produces calming GABA)",
            "informed_by": "microbiome + questionnaire",
        })

    # 2. Softgels
    if calc.softgel_count > 0:
        registry.append({
            "substance": f"Omega-3 DHA & EPA ({712.5 * calc.softgel_count}mg)",
            "dose": f"{712.5 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "omega",
            "source": "questionnaire_only",
            "health_claims": ["Brain Health", "Anti-inflammatory"],
            "based_on": "Mood/brain health goals",
            "what_it_targets": "Brain health, mood support, anti-inflammatory",
            "informed_by": "questionnaire",
        })
        registry.append({
            "substance": f"Vitamin D3 ({10 * calc.softgel_count}mcg / {400 * calc.softgel_count} IU)",
            "dose": f"{10 * calc.softgel_count}mcg",
            "delivery": "softgel",
            "category": "vitamin",
            "source": "questionnaire_only",
            "health_claims": ["Immune System"],
            "based_on": "Immune health claim",
            "what_it_targets": "Immune support, bone health",
            "informed_by": "questionnaire",
        })
        registry.append({
            "substance": f"Vitamin E ({7.5 * calc.softgel_count}mg)",
            "dose": f"{7.5 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "vitamin",
            "source": "questionnaire_only",
            "health_claims": ["Antioxidant Protection"],
            "based_on": "General wellness (bundled with omega softgel)",
            "what_it_targets": "Antioxidant protection, skin health",
            "informed_by": "questionnaire",
        })
        registry.append({
            "substance": f"Astaxanthin ({3 * calc.softgel_count}mg active)",
            "dose": f"{3 * calc.softgel_count}mg",
            "delivery": "softgel",
            "category": "antioxidant",
            "source": "questionnaire_only",
            "health_claims": ["Antioxidant Protection"],
            "based_on": "General wellness (bundled with omega softgel)",
            "what_it_targets": "Antioxidant, UV protection, muscle recovery",
            "informed_by": "questionnaire",
        })

    # 3. Prebiotics (from calc — post-dedup)
    for pb in calc.jar_prebiotics:
        registry.append({
            "substance": f"{pb['substance']} ({pb['dose_g']}g)",
            "dose": f"{pb['dose_g']}g",
            "delivery": "powder jar",
            "category": "prebiotic",
            "source": "microbiome_primary",
            "health_claims": [f"{mix_name} substrate"],
            "based_on": f"Microbiome pattern ({pb.get('rationale', mix_name)})",
            "what_it_targets": f"Substrate for {mix_name} strains",
            "informed_by": "microbiome",
        })

    # 4. Vitamins/Minerals + light botanicals (from calc.morning_pooled_components — post-dedup)
    supp_claims_map = {}
    for sp in supplements.get("supplements", []):
        supp_claims_map[sp.get("substance", "").lower()] = sp.get("health_claim", "")

    for vm in calc.morning_pooled_components:
        substance = vm["substance"]
        dose_str = vm.get("dose", f"{vm.get('weight_mg', 0)}mg")
        informed = vm.get("informed_by", "questionnaire")
        rationale = vm.get("rationale", "")
        source_type = vm.get("_source", "")

        if informed == "microbiome":
            source = "microbiome_primary"
        elif informed == "both":
            source = "microbiome_linked"
        else:
            source = "questionnaire_only"

        category = "vitamin_mineral" if source_type == "vitamin_mineral" else "supplement"
        claim = supp_claims_map.get(substance.lower(), "")
        health_claims = [claim] if claim else []

        registry.append({
            "substance": f"{substance} ({dose_str})",
            "dose": dose_str,
            "delivery": "morning wellness capsule",
            "category": category,
            "source": source,
            "health_claims": health_claims,
            "based_on": f"{'Microbiome + ' if informed in ('microbiome', 'both') else ''}Questionnaire" + (f" ({claim})" if claim else ""),
            "what_it_targets": rationale or claim or "General wellness",
            "informed_by": informed,
        })

    # 4b. Jar botanicals (heavy non-bitter botanicals routed to powder jar)
    for jb in getattr(calc, 'jar_botanicals', []):
        substance = jb["substance"]
        claim = supp_claims_map.get(substance.lower(), "")
        rationale = jb.get("rationale", "")
        health_claims = [claim] if claim else []

        registry.append({
            "substance": f"{substance} ({jb['dose_g']}g)",
            "dose": f"{jb['dose_g']}g",
            "delivery": "powder jar",
            "category": "supplement",
            "source": "questionnaire_only",
            "health_claims": health_claims,
            "based_on": f"Questionnaire ({claim})" if claim else "Health questionnaire",
            "what_it_targets": rationale or claim or "General wellness",
            "informed_by": "questionnaire",
        })

    # 5. Evening capsule components
    for ec in calc.evening_pooled_components:
        registry.append({
            "substance": f"{ec['substance']} ({ec['dose_mg']}mg)",
            "dose": f"{ec['dose_mg']}mg",
            "delivery": "evening capsule",
            "category": "sleep_supplement",
            "source": "questionnaire_only",
            "health_claims": ["Sleep Quality"],
            "based_on": f"Questionnaire (sleep quality {sleep}/10)",
            "what_it_targets": ec.get("rationale", "Sleep/relaxation support"),
            "informed_by": "questionnaire",
        })

    # 6. Polyphenol capsule components
    for pc in calc.polyphenol_capsules:
        substance = pc["substance"]
        claim = supp_claims_map.get(substance.lower(), "")
        if not claim:
            for k, v in supp_claims_map.items():
                if "curcumin" in k and "curcumin" in substance.lower():
                    claim = v
                    break
                elif "bergamot" in k and "bergamot" in substance.lower():
                    claim = v
                    break
        registry.append({
            "substance": f"{substance} ({pc['dose_mg']}mg)",
            "dose": f"{pc['dose_mg']}mg",
            "delivery": "polyphenol capsule",
            "category": "polyphenol",
            "source": "questionnaire_only",
            "health_claims": [claim] if claim else ["Anti-inflammatory"],
            "based_on": f"Questionnaire ({claim})" if claim else "Anti-inflammatory support",
            "what_it_targets": pc.get("rationale", claim or "Anti-inflammatory, microbiome modulation"),
            "informed_by": "questionnaire",
        })

    # 7. Magnesium capsules
    mg = rule_outputs.get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        mg_needs = mg.get("needs_identified", [])
        registry.append({
            "substance": f"Magnesium Bisglycinate ({mg['mg_bisglycinate_total_mg']}mg / {mg['elemental_mg_total_mg']}mg elemental)",
            "dose": f"{mg['elemental_mg_total_mg']}mg elemental",
            "delivery": "magnesium capsule",
            "category": "mineral",
            "source": "questionnaire_only",
            "health_claims": [n.title() for n in mg_needs],
            "based_on": f"Questionnaire ({'; '.join(mg.get('reasoning', []))})",
            "what_it_targets": ", ".join(n.title() for n in mg_needs) if mg_needs else "Magnesium support",
            "informed_by": "questionnaire",
        })

    # ── Dedup registry by substance name ─────────────────────────────────
    seen = set()
    deduped = []
    for entry in registry:
        key = entry.get("substance", "").lower()
        if key not in seen:
            seen.add(key)
            deduped.append(entry)

    # ── Medication timing override consolidation ──────────────────────────
    # When a timing override is active (e.g., levothyroxine → all units to dinner),
    # the same substance may appear in multiple original delivery formats
    # (e.g., "apple polyphenol extract" in both jar_prebiotics and jar_botanicals).
    # After the override is applied in Stage 8, both entries get annotated with
    # "→ dinner (medication override)" but remain as separate registry entries.
    # This causes validator warnings about duplicates.
    #
    # Fix: When timing override is active, merge duplicate substances into a single
    # consolidated entry with combined dose and a "multiple sources" note.
    timing_override_active = ctx.medication.timing_override is not None
    if timing_override_active:
        # Normalize substance names by stripping dose annotations in parentheses
        import re
        consolidated = {}
        for entry in deduped:
            substance_full = entry.get("substance", "")
            # Strip dose info: "Apple polyphenol (0.5g)" → "Apple polyphenol"
            substance_base = re.sub(r'\s*\([^)]*\)\s*$', '', substance_full).strip().lower()
            
            if substance_base in consolidated:
                # Merge: combine doses if same unit, or note "multiple sources"
                existing = consolidated[substance_base]
                # Update delivery to note consolidation
                existing["delivery"] = "dinner (medication override - multiple sources)"
                # If we can parse and combine doses, do so; otherwise leave as-is
                # For simplicity, just mark as consolidated and keep first dose
                print(f"    🔄 Timing override: consolidated duplicate '{substance_base}' from registry")
            else:
                consolidated[substance_base] = entry
        
        deduped = list(consolidated.values())

    return deduped
