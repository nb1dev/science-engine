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

    # ── Excluded supplement fallback (C.5b) ──────────────────────────────
    # After medication exclusions remove a substance, check if its health claim
    # category is now uncovered. If so, inject the next available KB-ranked
    # alternative (fully dynamic — reads from supplements_nonvitamins.json).
    _apply_excluded_supplement_fallback(ctx)

    # ── BMI-mandatory supplements (C.5c) ─────────────────────────────────
    # Deterministic injection based on clinical BMI thresholds.
    # Currently: BMI ≥ 27.5 → Glucomannan 3g (satiety fiber, powder jar).
    # Runs after medication exclusions (exclusion set is populated) and after
    # fallback injector, before the fiber/substance blocklist filter.
    _apply_bmi_mandatory_supplements(ctx)

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

    # ── Supplement deduplication (D.2f) ─────────────────────────────────
    # Must run last — after fallback injections, LLM selection, and all
    # other filters. Removes any substance that appears more than once.
    _apply_supplement_deduplication(ctx)

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


def _apply_excluded_supplement_fallback(ctx: PipelineContext):
    """Deterministic KB fallback: when a substance is removed by medication rules,
    check if its health claim category is now uncovered and inject the next
    available KB-ranked alternative.

    Design principles:
    - Fully KB-driven — reads supplement ranks from supplements_nonvitamins.json.
      No substance names are hardcoded here. Updating the KB automatically updates
      the fallback options.
    - Only fires when a claim is genuinely uncovered (no other selected supplement
      covers it) — does not add unnecessary supplements.
    - Respects the excluded_substances set — never reinjects an excluded substance.
    - Skips supplements with interaction_risk "medium" or "high".
    - Uses the minimum KB dose for the replacement.
    - Logs every injection clearly for the pipeline trace.
    """
    excluded = ctx.medication.excluded_substances or set()
    if not excluded:
        return

    # Load supplement KB
    kb_path = KB_DIR / "supplements_nonvitamins.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        kb = json.load(f)

    # Build a flat lookup: {substance_id: entry} and health_claim → sorted candidates
    supplements_flat = kb.get("supplements_flat", [])

    def _normalize(name: str) -> str:
        return name.lower().strip()

    # Which health claims are currently covered by selected supplements?
    active_supplement_claims: set = set()
    for sp in ctx.supplements.get("supplements", []):
        name_lower = _normalize(sp.get("substance", ""))
        for entry in supplements_flat:
            entry_names = {
                _normalize(entry.get("substance", "")),
                _normalize(entry.get("id", "")),
            }
            if any(n and (n in name_lower or name_lower in n) for n in entry_names):
                for claim in entry.get("parsed", {}).get("health_claims", []):
                    active_supplement_claims.add(claim)
                break

    # Which claims did the excluded substances cover?
    # Walk removal_reasons to find substances removed by medication rules and their claims
    claims_needing_fallback: dict = {}  # {claim: [excluded_substance_names]}
    for reason in getattr(ctx.medication, 'removal_reasons', []):
        substance_name = reason.get("substance", "").lower()
        if not substance_name:
            continue
        # Find what health claims this substance covered in the KB
        for entry in supplements_flat:
            entry_id = _normalize(entry.get("id", ""))
            entry_sub = _normalize(entry.get("substance", ""))
            if entry_id in substance_name or substance_name in entry_id or \
               entry_sub in substance_name or substance_name in entry_sub:
                for claim in entry.get("parsed", {}).get("health_claims", []):
                    if claim not in active_supplement_claims:
                        if claim not in claims_needing_fallback:
                            claims_needing_fallback[claim] = []
                        claims_needing_fallback[claim].append(substance_name)
                break

    if not claims_needing_fallback:
        return

    # Build candidates per claim: sorted by rank_priority ascending (1 = best)
    def _get_candidates(claim: str):
        candidates = []
        for entry in supplements_flat:
            if claim in entry.get("parsed", {}).get("health_claims", []):
                candidates.append(entry)
        candidates.sort(key=lambda e: e.get("parsed", {}).get("rank_priority", 99))
        return candidates

    injected = []

    for claim, excluded_names in claims_needing_fallback.items():
        # Check if this active health claim list warrants a supplement
        active_claims = ctx.rule_outputs.get("health_claims", {}).get("supplement_claims", [])
        if claim not in active_claims:
            continue  # This claim wasn't active for this client — skip

        candidates = _get_candidates(claim)
        for entry in candidates:
            entry_id = _normalize(entry.get("id", ""))
            entry_sub = _normalize(entry.get("substance", ""))

            # Skip if excluded by medication rules
            is_excluded = any(
                ex in entry_id or entry_id in ex or
                ex in entry_sub or entry_sub in ex
                for ex in excluded
            )
            if is_excluded:
                continue

            # Skip high/medium interaction risk
            interaction = entry.get("parsed", {}).get("interaction_level", "low")
            if interaction in ("medium", "high"):
                continue

            # Skip if already in selected supplements
            already_selected = any(
                _normalize(sp.get("substance", "")) in entry_sub or
                entry_sub in _normalize(sp.get("substance", ""))
                for sp in ctx.supplements.get("supplements", [])
            )
            if already_selected:
                active_supplement_claims.add(claim)
                break  # Claim is already covered, no need to inject

            # Determine dose
            parsed_dose = entry.get("parsed", {}).get("dose", {})
            dose_unit = parsed_dose.get("unit", "mg")
            dose_value = parsed_dose.get("min") or parsed_dose.get("value")
            if dose_value is None:
                continue  # No usable dose — skip this candidate

            dose_mg = dose_value * 1000 if dose_unit == "g" else dose_value

            # Determine delivery
            delivery_constraint = entry.get("delivery_constraint", "any")
            timing_restriction = entry.get("timing_restriction", "any")
            if delivery_constraint == "capsule_only":
                delivery = "morning_wellness_capsule"
            else:
                delivery = "morning_wellness_capsule"

            replacement = {
                "substance": entry.get("substance", ""),
                "dose_mg": dose_mg,
                "health_claim": claim,
                "rank": entry.get("rank", ""),
                "delivery": delivery,
                "informed_by": "medication_fallback",
                "rationale": (
                    f"Fallback replacement for excluded substance(s) [{', '.join(excluded_names)}] "
                    f"— {claim} health claim would be uncovered. "
                    f"Selected as next available KB-ranked option ({entry.get('rank', '?')})."
                ),
                "_fallback_injected": True,
            }
            ctx.supplements["supplements"].append(replacement)
            active_supplement_claims.add(claim)
            injected.append(f"{entry.get('substance')} ({claim}, {entry.get('rank')})")
            print(
                f"  💊 FALLBACK INJECTION: {entry.get('substance')} {dose_mg}mg "
                f"→ replaces excluded [{', '.join(excluded_names)}] for '{claim}' claim"
            )
            ctx.add_trace(
                "fallback_injection",
                entry.get("substance", ""),
                f"Fallback: replaces excluded [{', '.join(excluded_names)}] for '{claim}' claim",
                excluded_substances=list(excluded_names),
                health_claim=claim,
                rank=entry.get("rank", ""),
                dose_mg=dose_mg,
            )
            break  # One fallback per claim

    if not injected:
        # All uncovered claims already had coverage or no valid candidate found
        pass
    else:
        print(f"  ✅ Fallback: {len(injected)} supplement(s) injected to cover uncovered claims")


# Substances handled deterministically — LLM should not select these
EXCLUDED_SUBSTANCES = {"magnesium", "vitamin d", "vitamin d3", "vitamin e", "omega-3", "omega",
                       "dha", "epa", "astaxanthin", "melatonin", "l-theanine", "valerian",
                       "valerian root", "valeriana"}
def _apply_bmi_mandatory_supplements(ctx: PipelineContext):
    """Inject mandatory supplements based on BMI thresholds.

    Rule: BMI ≥ 27.5 → Glucomannan 3g/day (powder jar)
    ─────────────────────────────────────────────────────
    Glucomannan at 3g/day is the standard satiety dose — it reduces appetite via
    viscous gel formation in the stomach and slows gastric emptying. It is
    complementary to Capsicum Extract (which acts via thermogenesis) and both
    are clinically indicated together for weight management.

    Safety guards (all must pass):
    1. BMI ≥ 27.5 (overweight threshold — WHO Asian BMI cut-off)
    2. "Fullness/Satiety" is in active supplement claims (confirms weight management
       is clinically indicated for this client)
    3. Glucomannan not in medication excluded_substances (contraindication safety)
    4. Glucomannan not already selected by LLM (prevent duplication)
    5. No bowel obstruction / swallowing disorder in diagnoses (clinical safety —
       glucomannan can swell and cause obstruction in these conditions)

    Delivery: "jar" (3g powder) — goes in powder jar alongside prebiotics.
    Glucomannan is NOT in EXCLUDED_FIBERS so it passes the fiber blocklist filter.
    """
    bmi = ctx.unified_input["questionnaire"]["demographics"].get("bmi")
    if bmi is None or bmi < 27.5:
        return

    # Guard 2: Fullness/Satiety must be an active claim
    active_claims = ctx.rule_outputs.get("health_claims", {}).get("supplement_claims", [])
    if "Fullness/Satiety" not in active_claims:
        return

    # Guard 3: Not excluded by medication rules
    excluded = ctx.medication.excluded_substances or set()
    if any("glucomannan" in ex.lower() for ex in excluded):
        return

    # Guard 4: Not already selected by LLM
    existing_names = {sp.get("substance", "").lower() for sp in ctx.supplements.get("supplements", [])}
    if any("glucomannan" in name for name in existing_names):
        return

    # Guard 5: No bowel obstruction or swallowing disorder
    diagnoses = [str(d).lower() for d in ctx.unified_input.get("questionnaire", {}).get("medical", {}).get("diagnoses", [])]
    contraindicated_conditions = [
        "bowel obstruction", "intestinal obstruction", "esophageal stricture",
        "dysphagia", "swallowing disorder", "achalasia", "esophageal",
    ]
    if any(kw in diag for diag in diagnoses for kw in contraindicated_conditions):
        print(f"  ⚠️  Glucomannan skipped: contraindicated condition in diagnoses")
        return

    # All guards passed — inject Glucomannan 3g
    ctx.supplements["supplements"].append({
        "substance": "Glucomannan",
        "dose_mg": 3000,
        "dose_g": 3.0,
        "health_claim": "Fullness/Satiety",
        "rank": "2nd Choice",
        "delivery": "jar",
        "informed_by": "bmi_rule",
        "rationale": (
            f"BMI {bmi} ≥ 27.5 — mandatory Glucomannan 3g/day (deterministic weight management rule). "
            f"Complements LLM-selected Fullness/Satiety supplement via independent mechanism "
            f"(viscous gel → satiety vs thermogenesis). KB dose range: 1–5g."
        ),
        "_bmi_mandatory": True,
    })
    print(f"  ⚖️  BMI {bmi} ≥ 27.5 → Glucomannan 3g injected (Fullness/Satiety, powder jar)")
    ctx.add_trace(
        "bmi_mandatory_injection",
        "Glucomannan",
        f"BMI {bmi} ≥ 27.5 → Glucomannan 3g/day mandatory (deterministic weight management rule)",
        bmi=bmi,
        dose_g=3.0,
        health_claim="Fullness/Satiety",
    )


# Fibers blocked here are placed in the powder jar by the prebiotic designer step.
# If the LLM also selected them as supplements, they would appear twice — once in
# the jar and once in a capsule. This list contains ONLY fibers that the prebiotic
# designer actively uses (confirmed in prebiotic_rules.json).
#
# Glucomannan is intentionally EXCLUDED from this list — it is never placed in the jar
# by the prebiotic designer. It is a satiety supplement (1–5g/day, Fullness/Satiety
# health claim) and must be freely selectable by the LLM for BMI ≥ 27.5 clients.
EXCLUDED_FIBERS = {"phgg", "psyllium", "psyllium husk", "inulin", "pure inulin", "fos",
                   "oligofructose", "gos", "galactooligosaccharides", "beta-glucans",
                   "beta-glucans (oats)", "resistant starch"}


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


def _apply_supplement_deduplication(ctx: PipelineContext):
    """Remove duplicate supplements — keep first occurrence, drop subsequent ones.

    The LLM may occasionally select the same substance twice (e.g. once for
    Skin Quality and once for another overlapping claim). This filter runs last
    after all other post-processing to guarantee a clean deduplicated output.

    Normalization: strips bracketed text, lowercase, collapse whitespace.
    Examples of matches treated as duplicates:
      - "Apple polyphenol extract" and "Apple polyphenol extract" (exact)
      - "Ashwagandha (Withania somnifera)" and "Ashwagandha" (base name match)
    """
    import re as _re

    def _base_name(name: str) -> str:
        """Strip parenthetical suffixes and normalize."""
        return _re.sub(r'\s*\(.*?\)\s*', '', name).lower().strip()

    seen_names: set = set()
    seen_base: set = set()
    deduped = []
    dropped = []

    for sp in ctx.supplements.get("supplements", []):
        name = sp.get("substance", "")
        name_lower = name.lower().strip()
        base = _base_name(name)

        if name_lower in seen_names or base in seen_base:
            dropped.append(name)
            ctx.removal_log.add(name, "Duplicate supplement — already present in formula", "deduplication")
        else:
            seen_names.add(name_lower)
            seen_base.add(base)
            deduped.append(sp)

    if dropped:
        ctx.supplements["supplements"] = deduped
        print(f"  🔁 DEDUPLICATION: Removed {len(dropped)} duplicate(s): {dropped}")


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
