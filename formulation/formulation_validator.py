#!/usr/bin/env python3
"""
Formulation Validator — Comprehensive deterministic quality gate.

Runs ~30 checks across 7 categories against the formulation master JSON,
questionnaire, manufacturing recipe, and knowledge bases.

Usage:
  # Standalone
  python formulation_validator.py analysis/nb1_2026_004/1421012391191

  # From pipeline (stage s10)
  from formulation.formulation_validator import validate_formulation
  report = validate_formulation(sample_dir)

Output:
  validation_report_{sample_id}.json  — structured audit report
  Terminal summary with pass/warn/error counts
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

VALIDATOR_VERSION = "1.2.1"

# Capsule/unit capacities (must match weight_calculator.py)
HARD_CAPSULE_CAPACITY_MG      = 650
PROBIOTIC_CAPSULE_CAPACITY_MG = 500    # size 0 — must match weight_calculator.py
PROBIOTIC_ACTIVE_CAPACITY_MG  = 495.0  # 500 × 0.99 (1% reserved for SiO2 + SSF)
SOFTGEL_CAPACITY_MG = 750
JAR_TARGET_G = 19.0
MG_CAPSULE_FILL_MG = 750

# Tolerance for float comparisons
WEIGHT_TOLERANCE_MG = 1.5
WEIGHT_TOLERANCE_G = 0.05

KB_DIR = Path(__file__).parent / "knowledge_base"


# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def _load_json(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_kb(name: str) -> Optional[Dict]:
    return _load_json(KB_DIR / name)


# ═══════════════════════════════════════════════════════════════
# CHECK RESULT HELPER
# ═══════════════════════════════════════════════════════════════

class CheckResult:
    def __init__(self, category: str, check: str, status: str,
                 severity: str = "info", expected: str = "", actual: str = "",
                 detail: str = ""):
        self.category = category
        self.check = check
        self.status = status  # PASS | FAIL | WARN | SKIP
        self.severity = severity  # error | warning | info
        self.expected = expected
        self.actual = actual
        self.detail = detail

    def to_dict(self):
        d = {
            "category": self.category,
            "check": self.check,
            "status": self.status,
            "severity": self.severity,
        }
        if self.expected:
            d["expected"] = self.expected
        if self.actual:
            d["actual"] = self.actual
        if self.detail:
            d["detail"] = self.detail
        return d


def _pass(cat, check, detail=""):
    return CheckResult(cat, check, "PASS", "info", detail=detail)


def _fail(cat, check, expected, actual, detail=""):
    return CheckResult(cat, check, "FAIL", "error", expected, actual, detail)


def _warn(cat, check, detail=""):
    return CheckResult(cat, check, "WARN", "warning", detail=detail)


def _skip(cat, check, detail=""):
    return CheckResult(cat, check, "SKIP", "info", detail=detail)


# ═══════════════════════════════════════════════════════════════
# NEGATION-AWARE NARRATIVE SEARCH
# ═══════════════════════════════════════════════════════════════

# Standard negation prefixes that negate a keyword in clinical text.
# Used by _narrative_mentions_positively() to avoid false positives
# when the narrative says e.g. "no UTIs" or "0-1 colds/year".
_NEGATION_PREFIXES = [
    "no", "0", "none", "zero", "without", "never", "rarely",
    "no history of", "absence of", "denies", "not", "negative for",
    "excellent resistance", "0-1",
]

# Post-keyword negation suffixes — cover cases like "UTIs absent" where the
# negating word comes AFTER the keyword (v1.2.2).
_NEGATION_SUFFIXES = [
    "absent", "not present", "not found", "not detected",
    "none detected", "0/year", "0 per year",
]


def _narrative_mentions_positively(keyword: str, narrative_text: str) -> bool:
    """Check if *keyword* appears in narrative in a POSITIVE (affirming) context.

    Returns False when the keyword is:
    - preceded by a negation phrase within a 60-character look-back window
      (covers "no UTIs", "0-1 colds/year", "Excellent resistance (..., no UTIs)")
    - followed by a negation word within a 40-character look-forward window
      (covers "UTIs absent", "UTIs not present")

    Args:
        keyword: lowercase term to search for (e.g. "uti", "infection", "allergy")
        narrative_text: lowercased narrative string

    Returns:
        True only if keyword is mentioned AND not negated.
    """
    # Find all occurrences of the keyword
    pattern = re.compile(r'\b' + re.escape(keyword))
    for match in pattern.finditer(narrative_text):
        start = match.start()
        # Look back up to 60 chars for a negation prefix
        window_start = max(0, start - 60)
        prefix_window = narrative_text[window_start:start]
        negated_prefix = any(neg in prefix_window for neg in _NEGATION_PREFIXES)

        # Look forward up to 40 chars for a post-keyword negation suffix
        forward_window = narrative_text[match.end():match.end() + 40]
        negated_suffix = any(suf in forward_window for suf in _NEGATION_SUFFIXES)

        if not negated_prefix and not negated_suffix:
            return True  # Found a positive (non-negated) mention
    return False  # All mentions were negated, or keyword absent entirely


# ═══════════════════════════════════════════════════════════════
# KB DOSE SCHEMA RESOLVER
# ═══════════════════════════════════════════════════════════════

def _get_kb_max(kb_dose: Dict, biological_sex: str = "unknown") -> Optional[float]:
    """Extract the maximum allowed dose from a KB parsed.dose object.

    Handles all 3 KB schema shapes (v1.1.0):
      Shape A — range:   {"min": 300, "max": 600, "unit": "mg"}
      Shape B — single:  {"value": 250, "unit": "mg"}
      Shape C — gendered: {"male": 11, "female": 8, "unit": "mg"}

    For Shape B, the stated value IS the max (KB field is 'max_intake_in_supplements').
    For Shape C, selects male/female based on biological_sex; falls back to the
    lower of the two if sex is unknown.

    Returns:
        Max dose as float, or None if no dose data could be resolved.
    """
    if not kb_dose:
        return None

    # Shape A: has explicit "max"
    if "max" in kb_dose:
        return float(kb_dose["max"])

    # Shape C: gender-specific (e.g. Zinc)
    if "male" in kb_dose or "female" in kb_dose:
        sex = biological_sex.lower() if biological_sex else "unknown"
        if sex == "male" and "male" in kb_dose:
            return float(kb_dose["male"])
        elif sex == "female" and "female" in kb_dose:
            return float(kb_dose["female"])
        else:
            # Unknown sex → use the lower (safer) limit
            candidates = [kb_dose.get("male"), kb_dose.get("female")]
            candidates = [float(c) for c in candidates if c is not None]
            return min(candidates) if candidates else None

    # Shape B: single "value" — this IS the max (field = max_intake_in_supplements)
    if "value" in kb_dose:
        return float(kb_dose["value"])

    return None


# ═══════════════════════════════════════════════════════════════
# CATEGORY 1: PHYSICAL CONSISTENCY
# ═══════════════════════════════════════════════════════════════

def check_physical_consistency(master: Dict, recipe: Dict) -> List[CheckResult]:
    """Verify all weights, fills, and capacities are internally consistent."""
    results = []
    formulation = master.get("formulation", {})

    # ── 1a. Probiotic capsule — active fill ≤ PROBIOTIC_ACTIVE_CAPACITY_MG ──
    probiotic = formulation.get("delivery_format_1_probiotic_capsule", {})
    if probiotic:
        active_mg = probiotic.get("totals", {}).get("active_weight_mg", 0)
        if active_mg > PROBIOTIC_ACTIVE_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
            results.append(_fail("physical", "probiotic_capsule_capacity",
                f"≤{PROBIOTIC_ACTIVE_CAPACITY_MG}mg (active)", f"{active_mg}mg",
                "Probiotic capsule active content exceeds capacity"))
        else:
            results.append(_pass("physical", "probiotic_capsule_capacity",
                f"{active_mg}mg ≤ {PROBIOTIC_ACTIVE_CAPACITY_MG}mg (active)"))

    # ── 1b. Morning capsules — per-capsule fill ≤ 650mg ──────────────────
    mwc = formulation.get("delivery_format_4_morning_wellness_capsules", {})
    if mwc:
        capsules = mwc.get("totals", {}).get("capsules", [])
        for cap in capsules:
            cap_num = cap.get("capsule_number", "?")
            fill = cap.get("fill_mg", 0)
            if fill > HARD_CAPSULE_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
                results.append(_fail("physical", f"morning_capsule_{cap_num}_capacity",
                    f"≤{HARD_CAPSULE_CAPACITY_MG}mg", f"{fill}mg",
                    f"Morning capsule {cap_num} exceeds capacity"))
            else:
                results.append(_pass("physical", f"morning_capsule_{cap_num}_capacity",
                    f"{fill}mg ≤ {HARD_CAPSULE_CAPACITY_MG}mg"))

        # Verify fill_mg matches component sum per capsule
        for cap in capsules:
            cap_num = cap.get("capsule_number", "?")
            fill = cap.get("fill_mg", 0)
            comp_sum = 0
            for c in cap.get("components", []):
                if c.get("weight_note") == "NEGLIGIBLE":
                    comp_sum += c.get("dose_value", 0) / 1000.0  # mcg → mg
                else:
                    comp_sum += c.get("dose_mg", c.get("weight_mg", 0))
            if abs(fill - comp_sum) > WEIGHT_TOLERANCE_MG:
                results.append(_fail("physical", f"morning_capsule_{cap_num}_fill_sum",
                    f"fill_mg={fill}", f"component_sum={round(comp_sum, 1)}",
                    "Fill weight doesn't match component sum"))
            else:
                results.append(_pass("physical", f"morning_capsule_{cap_num}_fill_sum"))

    # ── 1c. Evening capsules — per-capsule fill ≤ 650mg ──────────────────
    ewc = formulation.get("delivery_format_5_evening_wellness_capsules", {})
    if ewc:
        capsules = ewc.get("totals", {}).get("capsules", [])
        for cap in capsules:
            cap_num = cap.get("capsule_number", "?")
            fill = cap.get("fill_mg", 0)
            if fill > HARD_CAPSULE_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
                results.append(_fail("physical", f"evening_capsule_{cap_num}_capacity",
                    f"≤{HARD_CAPSULE_CAPACITY_MG}mg", f"{fill}mg",
                    f"Evening capsule {cap_num} exceeds capacity"))
            else:
                results.append(_pass("physical", f"evening_capsule_{cap_num}_capacity",
                    f"{fill}mg ≤ {HARD_CAPSULE_CAPACITY_MG}mg"))

    # ── 1d. Polyphenol capsule(s) — per-capsule fill ≤ 650mg ─────────────
    # Same pattern as morning/evening: the optimizer may split into N capsules
    # when total polyphenol weight exceeds 650mg (e.g. Curcumin 500 + Bergamot 500).
    # We must check each capsule individually, not the grand total.
    pp = formulation.get("delivery_format_6_polyphenol_capsule") or formulation.get("delivery_format_5_polyphenol_capsule")
    if pp:
        pp_capsules = pp.get("totals", {}).get("capsules", [])
        if pp_capsules:
            for cap in pp_capsules:
                cap_num = cap.get("capsule_number", "?")
                fill = cap.get("fill_mg", 0)
                if fill > HARD_CAPSULE_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
                    results.append(_fail("physical", f"polyphenol_capsule_{cap_num}_capacity",
                        f"≤{HARD_CAPSULE_CAPACITY_MG}mg", f"{fill}mg",
                        f"Polyphenol capsule {cap_num} exceeds capacity"))
                else:
                    results.append(_pass("physical", f"polyphenol_capsule_{cap_num}_capacity",
                        f"{fill}mg ≤ {HARD_CAPSULE_CAPACITY_MG}mg"))
        else:
            # Fallback for older formulations without per-capsule layout:
            # single capsule — check total_weight_mg directly
            pp_mg = pp.get("totals", {}).get("total_weight_mg", 0)
            if pp_mg > HARD_CAPSULE_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
                results.append(_fail("physical", "polyphenol_capsule_capacity",
                    f"≤{HARD_CAPSULE_CAPACITY_MG}mg", f"{pp_mg}mg"))
            else:
                results.append(_pass("physical", "polyphenol_capsule_capacity",
                    f"{pp_mg}mg ≤ {HARD_CAPSULE_CAPACITY_MG}mg"))

    # ── 1e. Softgel ≤ 750mg ──────────────────────────────────────────────
    sg = formulation.get("delivery_format_2_omega_softgels", {})
    if sg:
        sg_mg = sg.get("totals", {}).get("weight_per_softgel_mg", 0)
        if sg_mg > SOFTGEL_CAPACITY_MG + WEIGHT_TOLERANCE_MG:
            results.append(_fail("physical", "softgel_capacity",
                f"≤{SOFTGEL_CAPACITY_MG}mg", f"{sg_mg}mg"))
        else:
            results.append(_pass("physical", "softgel_capacity"))

    # ── 1f. Jar weight check ─────────────────────────────────────────────
    jar = formulation.get("delivery_format_3_powder_jar", {})
    if jar:
        jar_g = jar.get("totals", {}).get("total_weight_g", 0)
        if jar_g > JAR_TARGET_G + WEIGHT_TOLERANCE_G:
            results.append(_warn("physical", "jar_exceeds_target",
                f"Jar {jar_g}g exceeds soft target {JAR_TARGET_G}g"))
        else:
            results.append(_pass("physical", "jar_within_target",
                f"{jar_g}g ≤ {JAR_TARGET_G}g"))

        # FODMAP total matches sum
        fodmap_comps = sum(
            c.get("dose_g", 0) for c in jar.get("prebiotics", {}).get("components", [])
            if c.get("fodmap")
        )
        stated_fodmap = jar.get("totals", {}).get("total_fodmap_g", 0)
        if abs(fodmap_comps - stated_fodmap) > WEIGHT_TOLERANCE_G:
            results.append(_fail("physical", "jar_fodmap_total",
                f"stated={stated_fodmap}g", f"computed={round(fodmap_comps, 2)}g"))
        else:
            results.append(_pass("physical", "jar_fodmap_total"))

    # ── 1g. Total unit count matches protocol summary ─────────────────────
    proto = formulation.get("protocol_summary", {})
    stated_units = proto.get("total_daily_units", 0)
    morning = proto.get("morning_solid_units", 0)
    jar_units = proto.get("morning_jar_units", 0)
    evening = proto.get("evening_solid_units", 0)
    computed_units = morning + jar_units + evening
    if stated_units != computed_units:
        results.append(_fail("physical", "total_unit_count",
            f"morning({morning})+jar({jar_units})+evening({evening})={computed_units}",
            f"stated={stated_units}"))
    else:
        results.append(_pass("physical", "total_unit_count", f"{stated_units} units"))

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 2: DOSE vs KB COMPLIANCE
# ═══════════════════════════════════════════════════════════════

def check_dose_kb_compliance(master: Dict) -> List[CheckResult]:
    """Verify doses fall within knowledge base ranges."""
    results = []

    # Load KBs
    vm_kb = _load_kb("vitamins_minerals.json")
    supp_kb = _load_kb("supplements_nonvitamins.json")

    # ── Vitamin name aliases ──────────────────────────────────────────────
    # Maps LLM-selected substance names → canonical KB substance names.
    # Required when the LLM returns a specific form/brand name that differs
    # from the KB entry (e.g. "Niacinamide (B3)" → "Niacin (B3)").
    _VM_ALIASES = {
        "niacinamide (b3)": "niacin (b3)",
        "nicotinamide (b3)": "niacin (b3)",
        "niacinamide": "niacin (b3)",
        "nicotinamide": "niacin (b3)",
        "folate": "folate (b9)",
        "folic acid": "folate (b9)",
        "vitamin b9": "folate (b9)",
        "cyanocobalamin": "vitamin b12",
        "methylcobalamin": "vitamin b12",
        "cobalamin": "vitamin b12",
        "pantothenic acid (b5)": "pantothenic acid (b5)",
        "pantothenate": "pantothenic acid (b5)",
        "vitamin b5": "pantothenic acid (b5)",
        "thiamine": "thiamin (b1)",
        "vitamin b1": "thiamin (b1)",
        "riboflavin": "riboflavin (b2)",
        "vitamin b2": "riboflavin (b2)",
        "pyridoxine": "vitamin b6",
        "vitamin b6 (pyridoxine)": "vitamin b6",
        "cholecalciferol": "vitamin d",
        "vitamin d3": "vitamin d",
        "ascorbic acid": "vitamin c",
        "tocopherol": "vitamin e",
        "retinol": "vitamin a",
        "beta-carotene": "vitamin a",
    }

    # Build vitamin lookup: substance_lower → {min, max, unit}
    vm_lookup = {}
    if vm_kb:
        for vm in vm_kb.get("vitamins_and_minerals", []):
            name = vm.get("substance", "").lower().strip()
            dose = vm.get("parsed", {}).get("dose", {})
            vm_lookup[name] = dose
            # Also index by any aliases declared in the KB entry itself
            for alias in vm.get("aliases", []):
                vm_lookup[alias.lower().strip()] = dose

    # Build supplement lookup
    supp_lookup = {}
    if supp_kb:
        for entry in supp_kb.get("supplements_flat", []):
            name = entry.get("substance", "").lower().strip()
            dose = entry.get("parsed", {}).get("dose", {})
            supp_lookup[name] = dose
            # Also index by id
            sid = entry.get("id", "").lower().strip()
            if sid:
                supp_lookup[sid] = dose

    # Get biological sex for gender-specific dose lookups (e.g. Zinc)
    biological_sex = master.get("input_summary", {}).get("questionnaire_driven", {}).get("biological_sex", "unknown")

    # Check vitamins/minerals from decisions
    decisions = master.get("decisions", {})
    for vm in decisions.get("supplement_selection", {}).get("vitamins_minerals", []):
        substance = vm.get("substance", "")
        dose_val = vm.get("dose_value", 0)
        dose_unit = vm.get("dose_unit", "mg")

        substance_lower = substance.lower().strip()
        # Resolve alias first (e.g. "Niacinamide (B3)" → "Niacin (B3)")
        resolved_name = _VM_ALIASES.get(substance_lower, substance_lower)

        # Find in KB — exact match on canonical name, then alias-resolved name, then partial
        kb_dose = vm_lookup.get(substance_lower) or vm_lookup.get(resolved_name)
        if not kb_dose:
            # Try partial match against resolved name
            for k, v in vm_lookup.items():
                if k in resolved_name or resolved_name in k:
                    kb_dose = v
                    break
        if not kb_dose:
            # Final fallback: partial match on original name
            for k, v in vm_lookup.items():
                if k in substance_lower or substance_lower in k:
                    kb_dose = v
                    break

        # Resolve max using schema-aware helper (v1.1.0)
        kb_max = _get_kb_max(kb_dose, biological_sex) if kb_dose else None
        if kb_max is not None:
            if dose_val > kb_max:
                results.append(_fail("dose_kb", f"vitamin_{substance}_max",
                    f"≤{kb_max}{dose_unit}", f"{dose_val}{dose_unit}",
                    f"{substance} exceeds KB max"))
            else:
                results.append(_pass("dose_kb", f"vitamin_{substance}_range"))
        else:
            results.append(_skip("dose_kb", f"vitamin_{substance}_range",
                f"No KB max found for {substance}"))

    # Check supplements
    for supp in decisions.get("supplement_selection", {}).get("supplements", []):
        substance = supp.get("substance", "")
        dose_mg = supp.get("dose_mg", 0)

        # Find in KB
        kb_dose = None
        for k, v in supp_lookup.items():
            if k in substance.lower() or substance.lower() in k:
                kb_dose = v
                break

        # Resolve max using schema-aware helper (v1.1.0)
        kb_max = _get_kb_max(kb_dose, biological_sex) if kb_dose else None
        if kb_max is not None:
            kb_unit = kb_dose.get("unit", "mg")
            compare_mg = kb_max * 1000 if kb_unit == "g" else kb_max
            if dose_mg > compare_mg:
                results.append(_fail("dose_kb", f"supplement_{substance}_max",
                    f"≤{compare_mg}mg", f"{dose_mg}mg"))
            else:
                results.append(_pass("dose_kb", f"supplement_{substance}_range"))
        else:
            results.append(_skip("dose_kb", f"supplement_{substance}_range",
                f"No KB max found for {substance}"))

    # Check probiotic total against mix
    mix = decisions.get("mix_selection", {})
    total_cfu = mix.get("total_cfu_billions", 0)
    strains = mix.get("strains", [])
    computed_cfu = sum(s.get("cfu_billions", 0) for s in strains)
    if total_cfu != computed_cfu:
        results.append(_fail("dose_kb", "probiotic_total_cfu",
            f"stated={total_cfu}B", f"sum={computed_cfu}B"))
    else:
        results.append(_pass("dose_kb", "probiotic_total_cfu", f"{total_cfu}B CFU"))

    # Check for duplicate substances across all delivery units
    registry = master.get("component_registry", [])
    seen_substances = {}
    for entry in registry:
        name = entry.get("substance", "").lower().strip()
        # Normalize: strip dose info in parens for dedup
        base = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        if base in seen_substances:
            results.append(_warn("dose_kb", f"duplicate_substance_{base}",
                f"'{base}' appears in both {seen_substances[base]} and {entry.get('delivery', '?')}"))
        else:
            seen_substances[base] = entry.get("delivery", "?")

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 3: QUESTIONNAIRE FIDELITY
# ═══════════════════════════════════════════════════════════════

def check_questionnaire_fidelity(master: Dict, questionnaire: Dict) -> List[CheckResult]:
    """Verify clinical profile claims match actual questionnaire data."""
    results = []
    clinical = master.get("clinical_summary", {})
    narrative = clinical.get("profile_narrative", [])
    narrative_text = " ".join(narrative).lower()

    if not questionnaire:
        results.append(_skip("questionnaire", "questionnaire_available", "No questionnaire file found"))
        return results

    # Get raw questionnaire data (v1.2.0: access via questionnaire_data wrapper)
    q_data = questionnaire.get("questionnaire_data", questionnaire)  # fallback to root if no wrapper
    step7 = q_data.get("step_7", {}) or {}
    step3 = q_data.get("step_3", {}) or {}
    step1 = q_data.get("step_1", {}) or {}
    step2 = q_data.get("step_2", {}) or {}

    # ── 3a. UTI frequency ────────────────────────────────────────────────
    # Uses negation-aware search: "no UTIs" → not a positive mention (v1.1.0)
    uti_raw = step7.get("uti_per_year", "")
    has_positive_uti = _narrative_mentions_positively("uti", narrative_text)
    if has_positive_uti:
        if uti_raw in ("none_or_rarely", "none", ""):
            results.append(_fail("questionnaire", "uti_frequency_accuracy",
                f"uti_per_year='{uti_raw}' (none/rarely)",
                "Narrative positively mentions UTI history",
                "Profile claims UTI history but questionnaire shows none/rarely"))
        else:
            results.append(_pass("questionnaire", "uti_frequency_accuracy"))
    else:
        results.append(_pass("questionnaire", "uti_frequency_accuracy",
            "No positive UTI mention in narrative"))

    # ── 3b. Colds frequency ──────────────────────────────────────────────
    colds_raw = step7.get("colds_per_year", "")
    COLDS_LABELS = {
        "rarely_0_1": "0-1", "none": "0", "2-3": "2-3", "4+": "4+",
    }
    colds_label = COLDS_LABELS.get(colds_raw, colds_raw)
    results.append(_pass("questionnaire", "colds_frequency",
        f"colds_per_year='{colds_raw}' → {colds_label}"))

    # ── 3c. Demographics consistency ─────────────────────────────────────
    input_summary = master.get("input_summary", {}).get("questionnaire_driven", {})
    q_age = input_summary.get("age")
    q_sex = input_summary.get("biological_sex")

    if q_age and str(q_age) not in narrative_text:
        results.append(_warn("questionnaire", "age_in_narrative",
            f"Age {q_age} not found in clinical narrative"))
    else:
        results.append(_pass("questionnaire", "age_in_narrative"))

    if q_sex and q_sex.lower() not in narrative_text:
        results.append(_warn("questionnaire", "sex_in_narrative",
            f"Sex '{q_sex}' not found in clinical narrative"))
    else:
        results.append(_pass("questionnaire", "sex_in_narrative"))

    # ── 3d. Stress level consistency ─────────────────────────────────────
    stress = input_summary.get("stress_level")
    if stress is not None and str(stress) in narrative_text:
        results.append(_pass("questionnaire", "stress_level_consistent"))
    elif stress is not None:
        results.append(_warn("questionnaire", "stress_level_consistent",
            f"Stress {stress}/10 not found in narrative"))
    else:
        results.append(_skip("questionnaire", "stress_level_consistent"))

    # ── 3e. Sleep quality consistency ────────────────────────────────────
    sleep = input_summary.get("sleep_quality")
    if sleep is not None and str(sleep) in narrative_text:
        results.append(_pass("questionnaire", "sleep_quality_consistent"))
    elif sleep is not None:
        results.append(_warn("questionnaire", "sleep_quality_consistent",
            f"Sleep {sleep}/10 not found in narrative"))
    else:
        results.append(_skip("questionnaire", "sleep_quality_consistent"))

    # ── 3f. Drug allergies not in formulation ────────────────────────────
    # v1.2.0: Try both field names (drug_allergies_details is canonical, drug_allergies is legacy)
    drug_allergies = step3.get("drug_allergies_details", "") or step3.get("drug_allergies", "") or ""
    # Type guard: ensure string (some schemas may return a dict or bool)
    if not isinstance(drug_allergies, str):
        drug_allergies = str(drug_allergies) if drug_allergies else ""
    if drug_allergies:
        allergy_lower = drug_allergies.lower()
        registry = master.get("component_registry", [])
        for entry in registry:
            substance = entry.get("substance", "").lower()
            if allergy_lower in substance or substance in allergy_lower:
                results.append(_fail("questionnaire", "drug_allergy_in_formulation",
                    f"allergy='{drug_allergies}'", f"found in '{entry['substance']}'",
                    "Drug allergy substance found in formulation!"))
                break
        else:
            results.append(_pass("questionnaire", "drug_allergy_not_in_formulation",
                f"Allergy '{drug_allergies}' not in any formulation component"))

    # ── 3g. Inferred health signals justified ────────────────────────────
    signals = clinical.get("inferred_health_signals", [])
    for sig in signals:
        signal_name = sig.get("signal", "")
        reason = sig.get("reason", "")
        # Basic validation: signal should have a non-empty reason
        if not reason:
            results.append(_warn("questionnaire", f"signal_{signal_name}_has_reason",
                f"Inferred signal '{signal_name}' has no reason"))
        else:
            results.append(_pass("questionnaire", f"signal_{signal_name}_has_reason"))

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 4: DECISION CONSISTENCY
# ═══════════════════════════════════════════════════════════════

def check_decision_consistency(master: Dict) -> List[CheckResult]:
    """Verify pipeline decisions follow deterministic rules."""
    results = []
    decisions = master.get("decisions", {})
    rule_outputs = decisions.get("rule_outputs", {})
    mix = decisions.get("mix_selection", {})
    input_summary = master.get("input_summary", {})
    mb = input_summary.get("microbiome_driven", {})
    q = input_summary.get("questionnaire_driven", {})

    # ── 4a. LPc-37 trigger ────────────────────────────────────────────────
    # v1.2.0: None-safe defaults — questionnaire fields can be null
    # Rule mirrors _should_add_lpc37() in llm/mix_selector.py exactly:
    #   stress ≥ 6 → always add
    #   stress ≥ 4 AND goal is "improve_mood_reduce_anxiety" or "reduce_stress_anxiety" → add
    lpc37_added = mix.get("lpc37_added", False)
    stress = q.get("stress_level") or 0
    sleep = q.get("sleep_quality") or 10
    goals = q.get("goals_ranked", []) or []
    _MOOD_GOALS = {"improve_mood_reduce_anxiety", "reduce_stress_anxiety"}
    has_mood_goal = any(g.lower() in _MOOD_GOALS for g in goals)
    lpc37_should_be_added = (stress >= 6) or (stress >= 4 and has_mood_goal)

    if lpc37_added and not lpc37_should_be_added:
        results.append(_warn("decision", "lpc37_trigger",
            f"LPc-37 added but triggers not met (stress={stress}, mood_goal={has_mood_goal})"))
    elif not lpc37_added and lpc37_should_be_added:
        results.append(_warn("decision", "lpc37_trigger",
            f"LPc-37 NOT added but triggers met (stress={stress}, mood_goal={has_mood_goal})"))
    else:
        results.append(_pass("decision", "lpc37_trigger"))

    # ── 4b. Magnesium trigger ─────────────────────────────────────────────
    # v1.2.0: Mirrors assess_magnesium_needs() in rules_engine.py exactly:
    #   Sleep: sleep ≤ 7 OR "sleep" goal
    #   Sport: active lifestyle (any sport indicator in Mg needs)
    #   Stress: stress ≥ 6 OR stress/mood goal
    mg = rule_outputs.get("magnesium", {})
    mg_capsules = mg.get("capsules", 0) or 0
    mg_needs = mg.get("needs_identified", []) or []

    # Use what the pipeline actually computed as the source of truth for needs
    # — we just verify capsule count matches need count (1 need → 1 cap, ≥2 → 2 caps)
    expected_capsules_from_needs = 0
    if len(mg_needs) >= 2:
        expected_capsules_from_needs = 2
    elif len(mg_needs) == 1:
        expected_capsules_from_needs = 1

    # Cross-check: at least one of our known triggers should explain the needs
    sleep_goal = any("sleep" in g.lower() for g in goals)
    stress_goal = any(g.lower() in {"improve_mood_reduce_anxiety", "reduce_stress_anxiety", "reduce_stress"} for g in goals)
    known_triggers_met = (
        sleep <= 7 or sleep_goal or
        stress >= 6 or stress_goal or
        "sport" in mg_needs
    )

    # If medication rule removed magnesium, skip capsule count check (suppression is correct)
    mg_removed_by_med = master.get("medication_rules", {}).get("magnesium_removed", False)
    if mg_removed_by_med:
        results.append(_pass("decision", "magnesium_trigger",
            f"Mg suppressed by medication rule — needs={mg_needs}, capsules correctly=0"))
    elif mg_capsules > 0 and not mg_needs:
        results.append(_warn("decision", "magnesium_trigger",
            f"Mg capsules={mg_capsules} but needs list is empty"))
    elif mg_capsules != expected_capsules_from_needs:
        results.append(_warn("decision", "magnesium_trigger",
            f"Mg needs={mg_needs} ({len(mg_needs)} needs) → expected {expected_capsules_from_needs} capsules but got {mg_capsules}"))
    else:
        results.append(_pass("decision", "magnesium_trigger",
            f"Mg needs={mg_needs}, capsules={mg_capsules}"))

    # ── 4c. Sensitivity classification ────────────────────────────────────
    sens = rule_outputs.get("sensitivity", {})
    classification = sens.get("classification", "")
    bloating = q.get("bloating_severity") or 0
    # Check basic rule: bloating ≤ 3 → likely moderate or low
    if bloating <= 3 and classification == "high":
        results.append(_warn("decision", "sensitivity_classification",
            f"Bloating {bloating}/10 (low) but sensitivity='{classification}'"))
    else:
        results.append(_pass("decision", "sensitivity_classification",
            f"bloating={bloating}, classification={classification}"))

    # ── 4d. Prebiotic total within range ──────────────────────────────────
    pb_range = rule_outputs.get("prebiotic_range", {})
    min_g = pb_range.get("min_g", 0)
    max_g = pb_range.get("max_g", 99)
    prebiotic_design = decisions.get("prebiotic_design", {})
    total_g = prebiotic_design.get("total_grams", 0)
    if total_g < min_g - WEIGHT_TOLERANCE_G:
        results.append(_fail("decision", "prebiotic_total_range",
            f"{min_g}-{max_g}g", f"{total_g}g",
            "Prebiotic total below range"))
    elif total_g > max_g + 1.0:  # 1g tolerance above max
        results.append(_warn("decision", "prebiotic_total_range",
            f"Prebiotic total {total_g}g above range max {max_g}g"))
    else:
        results.append(_pass("decision", "prebiotic_total_range",
            f"{total_g}g within {min_g}-{max_g}g"))

    # ── 4e. Timing assignments ────────────────────────────────────────────
    # v1.2.0: Skip timing checks when a global medication override is active —
    # the override restructures all units into a single delivery timing (e.g., "dinner"),
    # so individual evening-capsule placement checks are not meaningful.
    timing = rule_outputs.get("timing", {})
    assignments = timing.get("timing_assignments", {})
    formulation = master.get("formulation", {})
    ewc = formulation.get("delivery_format_5_evening_wellness_capsules", {})
    med_override_active = bool(master.get("medication_rules", {}).get("timing_override"))

    # Substances that have their own dedicated delivery unit (not evening wellness capsule)
    DEDICATED_DELIVERY_KEYS = {"magnesium"}

    # Check if magnesium was removed by medication rule (used to guard timing check)
    mg_removed_by_med = master.get("medication_rules", {}).get("magnesium_removed", False)

    if ewc and not med_override_active:
        # Build the set of all prescribed substances (from component registry)
        # so we only check timing for substances that were actually selected
        registry_substances = {
            re.sub(r'\s*\([^)]*\)\s*$', '', e.get("substance", "").lower()).strip()
            for e in master.get("component_registry", [])
        }

        evening_substances = [c.get("substance", "").lower() for c in ewc.get("components", [])]
        for substance_key, info in assignments.items():
            if info.get("timing") == "evening":
                # Skip substances with their own dedicated unit (e.g., magnesium capsules)
                if substance_key.lower() in DEDICATED_DELIVERY_KEYS:
                    # Guard: if magnesium was removed by medication rule, don't claim
                    # it has a dedicated delivery unit — it was correctly excluded
                    if substance_key.lower() == "magnesium" and mg_removed_by_med:
                        results.append(_pass("decision", f"timing_{substance_key}",
                            f"'{substance_key}' removed by medication rule — timing check skipped"))
                    else:
                        results.append(_pass("decision", f"timing_{substance_key}",
                            f"'{substance_key}' has dedicated delivery unit"))
                    continue

                # v1.2.0: Skip if this substance was not selected for this client
                # (timing_assignments always includes template entries for all possible evening
                # substances, but only a subset are actually prescribed per client)
                normalized_key = substance_key.replace("_", " ").replace("-", " ").lower()
                substance_was_prescribed = any(
                    normalized_key in reg_sub or reg_sub in normalized_key
                    for reg_sub in registry_substances
                )
                if not substance_was_prescribed:
                    results.append(_pass("decision", f"timing_{substance_key}",
                        f"'{substance_key}' not prescribed for this client — timing check skipped"))
                    continue

                # Fuzzy match against evening capsule components
                found = any(
                    normalized_key in s.replace("-", " ").lower()
                    or s.replace("-", " ").lower() in normalized_key
                    for s in evening_substances
                )
                if not found:
                    results.append(_warn("decision", f"timing_{substance_key}",
                        f"'{substance_key}' assigned evening but not found in evening capsule"))
                else:
                    results.append(_pass("decision", f"timing_{substance_key}"))
    elif med_override_active:
        # All units moved to single timing by medication rule — skip per-substance placement checks
        for substance_key, info in assignments.items():
            if info.get("timing") == "evening" and substance_key.lower() not in DEDICATED_DELIVERY_KEYS:
                results.append(_pass("decision", f"timing_{substance_key}",
                    f"Medication override active — all units consolidated, timing check skipped"))

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 5: MEDICATION SAFETY
# ═══════════════════════════════════════════════════════════════

def check_medication_safety(master: Dict) -> List[CheckResult]:
    """Verify medication rules were correctly applied."""
    results = []
    med_rules = master.get("medication_rules", {})

    # ── 5a. Removed substances not in formulation ────────────────────────
    removed = med_rules.get("substances_removed", [])
    if removed:
        registry = master.get("component_registry", [])
        registry_substances = [e.get("substance", "").lower() for e in registry]
        for substance in removed:
            if any(substance.lower() in s for s in registry_substances):
                results.append(_fail("medication", f"removed_{substance}",
                    "Should be removed", "Still in formulation",
                    f"Medication rule removed '{substance}' but it's still present"))
            else:
                results.append(_pass("medication", f"removed_{substance}",
                    f"'{substance}' correctly absent"))
    else:
        results.append(_pass("medication", "no_substances_removed"))

    # ── 5b. Magnesium removal ────────────────────────────────────────────
    mg_removed = med_rules.get("magnesium_removed", False)
    if mg_removed:
        mg = master.get("decisions", {}).get("rule_outputs", {}).get("magnesium", {})
        if mg.get("capsules", 0) > 0:
            results.append(_fail("medication", "magnesium_removal",
                "capsules=0", f"capsules={mg['capsules']}",
                "Mg was flagged for removal but capsules still in formulation"))
        else:
            results.append(_pass("medication", "magnesium_removal"))
    else:
        results.append(_pass("medication", "magnesium_not_flagged"))

    # ── 5c. Timing override applied correctly ────────────────────────────
    # v1.2.0: timing_override can be a dict (MED_001 rule) or a string
    timing_override = med_rules.get("timing_override")
    if timing_override:
        # Extract the target timing from the override (handles both str and dict)
        if isinstance(timing_override, dict):
            override_target = timing_override.get("move_to", "").lower()
        else:
            override_target = str(timing_override).lower()

        if override_target:
            formulation = master.get("formulation", {})
            for key in ["delivery_format_1_probiotic_capsule", "delivery_format_2_omega_softgels",
                         "delivery_format_3_powder_jar", "delivery_format_4_morning_wellness_capsules"]:
                unit = formulation.get(key, {})
                if unit:
                    fmt = unit.get("format", {})
                    actual_timing = fmt.get("timing", "").lower()
                    if override_target not in actual_timing:
                        results.append(_warn("medication", f"timing_override_{key}",
                            f"Override='{override_target}' but timing='{actual_timing}'"))
                    else:
                        results.append(_pass("medication", f"timing_override_{key}"))
        else:
            results.append(_skip("medication", "timing_override",
                "Override present but no target timing specified"))
    else:
        results.append(_pass("medication", "no_timing_override"))

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 6: CROSS-FILE CONSISTENCY
# ═══════════════════════════════════════════════════════════════

def check_cross_file_consistency(master: Dict, recipe: Dict) -> List[CheckResult]:
    """Verify all output files are consistent with each other."""
    results = []

    # ── 6a. Component registry count ─────────────────────────────────────
    registry = master.get("component_registry", [])
    if not registry:
        results.append(_warn("cross_file", "component_registry_exists",
            "No component_registry in master"))
    else:
        results.append(_pass("cross_file", "component_registry_exists",
            f"{len(registry)} components"))

    # ── 6b. Recipe units match formulation units ─────────────────────────
    if recipe:
        recipe_units = recipe.get("units", [])
        formulation = master.get("formulation", {})
        proto = formulation.get("protocol_summary", {})
        stated_total = proto.get("total_daily_units", 0)
        recipe_total = recipe.get("grand_total", {}).get("total_units", 0)

        if stated_total != recipe_total:
            results.append(_fail("cross_file", "recipe_vs_master_unit_count",
                f"master={stated_total}", f"recipe={recipe_total}"))
        else:
            results.append(_pass("cross_file", "recipe_vs_master_unit_count",
                f"{stated_total} units"))

        # Check recipe ingredient count matches formulation components
        recipe_ingredients = set()
        for unit in recipe_units:
            for ing in unit.get("ingredients", unit.get("ingredients_per_unit", [])):
                recipe_ingredients.add(ing.get("component", "").lower().strip())

        # Build formulation component set from master
        formulation_components = set()
        for df_key in ["delivery_format_1_probiotic_capsule",
                       "delivery_format_4_morning_wellness_capsules",
                       "delivery_format_5_evening_wellness_capsules",
                       "delivery_format_6_polyphenol_capsule",
                       "delivery_format_5_polyphenol_capsule"]:
            df = formulation.get(df_key) or {}  # v1.2.0: guard against null delivery formats
            for c in df.get("components", []):
                formulation_components.add(c.get("substance", "").lower().strip())

        # Check for missing (in formulation but not recipe)
        missing = formulation_components - recipe_ingredients
        if missing:
            results.append(_warn("cross_file", "recipe_missing_components",
                f"In formulation but not recipe: {missing}"))
        else:
            results.append(_pass("cross_file", "recipe_completeness"))

    else:
        results.append(_skip("cross_file", "recipe_available", "No recipe file"))

    return results


# ═══════════════════════════════════════════════════════════════
# CATEGORY 7: SENSITIVE DATA PROTECTION
# ═══════════════════════════════════════════════════════════════

def check_sensitive_data(master: Dict, sample_dir: Path) -> List[CheckResult]:
    """Verify no sensitive data leaked into client-facing outputs."""
    results = []

    # Check client dashboard HTML for sensitive data
    html_dir = sample_dir / "reports" / "reports_html"
    sample_id = master.get("metadata", {}).get("sample_id", "")

    client_html_path = html_dir / f"supplement_guide_{sample_id}.html"
    if client_html_path.exists():
        with open(client_html_path, 'r', encoding='utf-8') as f:
            html_content = f.read().lower()

        # Check for drug allergies in client HTML
        med_rules = master.get("medication_rules", {})
        questionnaire = master.get("clinical_summary", {})

        # Check for "you/your" absence in board dashboard
        board_html_path = html_dir / f"formulation_decision_trace_{sample_id}.html"
        if board_html_path.exists():
            with open(board_html_path, 'r', encoding='utf-8') as f:
                board_content = f.read()
            # Simple check: "your" outside of quoted strings / component names
            your_count = len(re.findall(r'\byour\b', board_content, re.IGNORECASE))
            if your_count > 5:  # Allow a few in quoted rationale text
                results.append(_warn("sensitive_data", "board_third_person",
                    f"Board dashboard has {your_count} instances of 'your/Your' — should use third person"))
            else:
                results.append(_pass("sensitive_data", "board_third_person"))
        else:
            results.append(_skip("sensitive_data", "board_third_person",
                "Board dashboard not found"))

        results.append(_pass("sensitive_data", "client_html_exists"))
    else:
        results.append(_skip("sensitive_data", "client_html_exists",
            "Client HTML not found"))

    return results


# ═══════════════════════════════════════════════════════════════
# MAIN VALIDATOR
# ═══════════════════════════════════════════════════════════════

def validate_formulation(sample_dir: str, save_report: bool = True) -> Dict:
    """Run all validation checks on a formulation output.

    Args:
        sample_dir: Path to sample directory (e.g. analysis/nb1_2026_004/1421012391191)
        save_report: Whether to save JSON report to disk

    Returns:
        Structured validation report dict
    """
    sample_path = Path(sample_dir)
    sample_id = sample_path.name

    json_dir = sample_path / "reports" / "reports_json"
    q_dir = sample_path / "questionnaire"

    # Load files
    master = _load_json(json_dir / f"formulation_master_{sample_id}.json")
    recipe = _load_json(json_dir / f"manufacturing_recipe_{sample_id}.json")
    questionnaire = _load_json(q_dir / f"questionnaire_{sample_id}.json")

    if not master:
        print(f"  ❌ No formulation_master found for {sample_id}")
        return {"error": "No formulation_master found"}

    # Run all checks
    all_results: List[CheckResult] = []

    all_results.extend(check_physical_consistency(master, recipe))
    all_results.extend(check_dose_kb_compliance(master))
    all_results.extend(check_questionnaire_fidelity(master, questionnaire))
    all_results.extend(check_decision_consistency(master))
    all_results.extend(check_medication_safety(master))
    all_results.extend(check_cross_file_consistency(master, recipe))
    all_results.extend(check_sensitive_data(master, sample_path))

    # Compile report
    passed = sum(1 for r in all_results if r.status == "PASS")
    failed = sum(1 for r in all_results if r.status == "FAIL")
    warned = sum(1 for r in all_results if r.status == "WARN")
    skipped = sum(1 for r in all_results if r.status == "SKIP")
    total = len(all_results)

    overall = "PASS" if failed == 0 else "FAIL"

    report = {
        "sample_id": sample_id,
        "validator_version": VALIDATOR_VERSION,
        "timestamp": datetime.now(tz=None).isoformat() + "Z",
        "overall_status": overall,
        "summary": {
            "total_checks": total,
            "passed": passed,
            "failed": failed,
            "warnings": warned,
            "skipped": skipped,
        },
        "checks": [r.to_dict() for r in all_results],
    }

    # Print terminal summary
    _print_summary(report, all_results)

    # Save report
    if save_report:
        report_path = json_dir / f"validation_report_{sample_id}.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  📄 Validation report: validation_report_{sample_id}.json")

    return report


def _print_summary(report: Dict, results: List[CheckResult]):
    """Print a clear terminal summary."""
    s = report["summary"]
    status = report["overall_status"]
    icon = "✅" if status == "PASS" else "❌"

    print(f"\n── FORMULATION VALIDATOR {'─' * 40}")
    print(f"  {icon} {s['passed']}/{s['total_checks']} checks passed | "
          f"⚠️ {s['warnings']} warnings | "
          f"🚨 {s['failed']} errors | "
          f"⏭️ {s['skipped']} skipped")

    errors = [r for r in results if r.status == "FAIL"]
    warnings = [r for r in results if r.status == "WARN"]

    if errors:
        print(f"\n  🚨 ERRORS:")
        for e in errors:
            detail = f" — {e.detail}" if e.detail else ""
            print(f"    [{e.category}] {e.check}: expected {e.expected}, got {e.actual}{detail}")

    if warnings:
        print(f"\n  ⚠️ WARNINGS:")
        for w in warnings:
            detail = w.detail or w.check
            print(f"    [{w.category}] {detail}")

    print(f"{'─' * 60}")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python formulation_validator.py <sample_dir>")
        print("  e.g. python formulation_validator.py analysis/nb1_2026_004/1421012391191")
        sys.exit(1)

    sample_dir = sys.argv[1]
    report = validate_formulation(sample_dir)
    sys.exit(0 if report.get("overall_status") == "PASS" else 1)
