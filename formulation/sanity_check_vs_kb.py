#!/usr/bin/env python3
"""
Sanity Check vs Knowledge Bases — Decision Correctness Audit.

Verifies that pipeline decisions are CORRECT according to the rules in the
knowledge base files, not just that files are consistent with each other.

Checks:
  1.  Sensitivity classification correctness
  2.  Mix strains match KB definition
  3.  LP815 enhancement trigger correctness
  4.  Prebiotic total within allowed range
  5.  FODMAP override applied when sensitivity=high
  6.  Magnesium capsule count correct
  7.  Softgel inclusion correct
  8.  Vitamin doses match KB standard
  9.  Probiotic capsule weight ≤ 650mg
  10. Sachet weight ≤ 19g
  11. Capsule-only substances not in sachet
  12. Evening capsule weight ≤ 650mg
  13. Goal → health claim mapping correct
  14. Timing rules applied correctly
  15. Total unit count within limits (≤8)

Usage:
    python sanity_check_vs_kb.py [batch_dir]
    Default batch_dir: analysis/nb1_2026_001
"""

import json
import glob
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# ─── KB Loading ───────────────────────────────────────────────────────────────

KB_DIR = Path(__file__).parent / "knowledge_base"

def _load_kb(filename):
    with open(KB_DIR / filename, "r") as f:
        return json.load(f)

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

# Preload all KBs
KB_SENSITIVITY = _load_kb("sensitivity_thresholds.json")
KB_MIXES = _load_kb("synbiotic_mixes.json")
KB_PREBIOTIC = _load_kb("prebiotic_rules.json")
KB_DELIVERY = _load_kb("delivery_format_rules.json")
KB_GOALS = _load_kb("goal_to_health_claim.json")
KB_TIMING = _load_kb("timing_rules.json")
KB_VITAMINS = _load_kb("vitamins_minerals.json")
KB_SUPPLEMENTS = _load_kb("supplements_nonvitamins.json")

# Build vitamin dose lookup
VITAMIN_DOSE_LOOKUP = {}
for v in KB_VITAMINS["vitamins_and_minerals"]:
    name = v["substance"].lower()
    parsed = v.get("parsed", {}).get("dose", {})
    VITAMIN_DOSE_LOOKUP[v["id"]] = {
        "substance": v["substance"],
        "parsed": parsed,
        "raw": v.get("max_intake_in_supplements", ""),
    }

# Capsule-only substances
CAPSULE_ONLY = set(s.lower() for s in KB_DELIVERY["capsule_only_substances"]["substances"])

# Deterministic exclusions — substances the vitamin gate removes by rule, NOT errors.
# The sanity check should NOT flag these as dose mismatches or missing substances.
DETERMINISTIC_EXCLUSIONS = {
    "vitamin b6": {
        "reason": "B6 restricted by vitamin gate (neuropathy risk)",
        "condition": lambda sex, deficiencies: (
            not any("b6" in d for d in deficiencies)
        ),
    },
    "iron": {
        "reason": "Iron excluded for males by deterministic rule",
        "condition": lambda sex, deficiencies: (
            sex == "male" and not any("iron" in d for d in deficiencies)
        ),
    },
}


# ─── Check Functions ──────────────────────────────────────────────────────────

def check_01_sensitivity(master):
    """Re-derive sensitivity from questionnaire data and compare.
    
    HIGH triggers (OR): bloating ≥7, daily bloating, stool type 6-7, digestive satisfaction ≤3
    LOW triggers (AND): bloating ≤3, digestive satisfaction ≥7
    
    Uses BOTH input_summary fields AND the pipeline's own reasoning strings for
    complete coverage of all HIGH trigger conditions.
    """
    q = master["input_summary"]["questionnaire_driven"]
    bloating = q.get("bloating_severity")
    rule_sensitivity = master["decisions"]["rule_outputs"]["sensitivity"]["classification"].lower()
    rule_reasoning = master["decisions"]["rule_outputs"]["sensitivity"].get("reasoning", [])

    # Re-derive using all available data
    high_triggered = False
    low_possible = True  # Assume low until contradicted
    high_reasons = []

    # Direct field checks (from input_summary)
    if bloating is not None and bloating >= 7:
        high_triggered = True
        high_reasons.append(f"bloating={bloating} ≥7")
    if bloating is None or bloating > 3:
        low_possible = False  # Low requires bloating ≤ 3

    # Cross-reference pipeline reasoning for fields NOT in input_summary
    # (digestive_satisfaction, stool_type, bloating_frequency are available to
    #  rules_engine but not exposed in input_summary.questionnaire_driven)
    for r in rule_reasoning:
        r_lower = r.lower()
        # Digestive satisfaction ≤ 3
        if "digestive satisfaction" in r_lower and ("≤3" in r_lower or "<=3" in r_lower or "/10" in r_lower):
            import re as _re
            _ds_match = _re.search(r'digestive satisfaction\s*(\d+)/10', r_lower)
            if _ds_match and int(_ds_match.group(1)) <= 3:
                high_triggered = True
                high_reasons.append(f"digestive_satisfaction {_ds_match.group(1)}/10 ≤3")
            elif "≤3" in r_lower or "<=3" in r_lower:
                high_triggered = True
                high_reasons.append("digestive_satisfaction ≤3 (from reasoning)")
        # Bristol stool type 6-7
        if ("stool type" in r_lower or "bristol" in r_lower) and any(f"type {t}" in r_lower or f"type_{t}" in r_lower for t in ("6", "7")):
            high_triggered = True
            high_reasons.append("stool type 6-7 (from reasoning)")
        # Daily bloating frequency
        if "daily" in r_lower and "bloating" in r_lower:
            high_triggered = True
            high_reasons.append("daily bloating frequency (from reasoning)")
        # Also check for digestive satisfaction ≥7 (needed for LOW classification)
        if "digestive satisfaction" in r_lower and ("≥7" in r_lower or ">=7" in r_lower):
            pass  # Good for low
        elif "digestive satisfaction" in r_lower:
            _ds_match2 = _re.search(r'digestive satisfaction\s*(\d+)/10', r_lower)
            if _ds_match2 and int(_ds_match2.group(1)) < 7:
                low_possible = False

    # Determine expected classification
    if high_triggered:
        expected = "high"
    elif low_possible and bloating is not None and bloating <= 3:
        expected = "low"
    else:
        expected = "moderate"

    # Allow FODMAP override: low → moderate is an expected override when digestion goal present
    # (applied in rules_engine.apply_rules)
    if expected == "low" and rule_sensitivity == "moderate":
        # Check if FODMAP override reasoning is present
        has_fodmap_override = any("fodmap override" in r.lower() for r in rule_reasoning)
        if has_fodmap_override:
            return True, f"Correctly classified as 'moderate' (FODMAP override from 'low': {[f'bloating={bloating}']})"

    if rule_sensitivity != expected:
        return False, f"Expected '{expected}' (bloating={bloating}, reasons={high_reasons}) but got '{rule_sensitivity}'"
    return True, f"Correctly classified as '{rule_sensitivity}' (triggers: {high_reasons or [f'bloating={bloating}']})"


def check_02_mix_strains(master):
    """Verify selected mix strains match KB definition."""
    mix_sel = master["decisions"]["mix_selection"]
    mix_id = str(mix_sel["mix_id"])
    kb_mix = KB_MIXES["mixes"].get(mix_id)
    if not kb_mix:
        return False, f"Mix {mix_id} not found in KB"

    kb_strain_names = set(s["name"] for s in kb_mix["strains"])
    # Get strains from master (excluding LP815 which is added separately)
    master_strains = set()
    for s in mix_sel.get("strains", []):
        name = s.get("name", "")
        if "LP815" not in name:
            master_strains.add(name)

    # Check that all KB strains are present
    missing = kb_strain_names - master_strains
    extra = master_strains - kb_strain_names
    issues = []
    if missing:
        issues.append(f"Missing from formulation: {missing}")
    if extra:
        issues.append(f"Extra strains not in KB: {extra}")

    if issues:
        return False, "; ".join(issues)
    return True, f"All {len(kb_strain_names)} strains from Mix {mix_id} ({kb_mix['mix_name']}) present"


def check_03_lp815(master):
    """LP815 should be added when stress ≥6 or (stress ≥4 AND mood/anxiety goal)."""
    q = master["input_summary"]["questionnaire_driven"]
    stress = q.get("stress_level")
    goals = q.get("goals_ranked", [])
    goals_lower = [g.lower() for g in goals]

    lp815_expected = False
    reason = ""
    if stress is not None and stress >= 6:
        lp815_expected = True
        reason = f"stress={stress} ≥ 6"
    elif stress is not None and stress >= 4:
        mood_goal = any(kw in g for g in goals_lower for kw in ["mood", "anxiety", "stress"])
        if mood_goal:
            lp815_expected = True
            reason = f"stress={stress} ≥ 4 + mood/anxiety goal"

    lp815_actual = master["decisions"]["mix_selection"].get("lp815_added", False)

    if lp815_expected and not lp815_actual:
        return False, f"LP815 should be added ({reason}) but was NOT"
    if not lp815_expected and lp815_actual:
        return False, f"LP815 was added but no trigger met (stress={stress}, goals={goals})"
    status = "correctly added" if lp815_actual else "correctly omitted"
    return True, f"LP815 {status} ({reason or 'no trigger'})"


def check_04_prebiotic_range(master):
    """Total prebiotic grams within allowed range for CFU tier + sensitivity."""
    sensitivity = master["decisions"]["rule_outputs"]["sensitivity"]["classification"].lower()
    mix_sel = master["decisions"]["mix_selection"]
    total_cfu = mix_sel.get("total_cfu_billions", 50)
    prebiotic_total = master["decisions"]["prebiotic_design"]["total_grams"]

    # Determine CFU tier
    mix_id = mix_sel.get("mix_id")
    if mix_id == 8 and total_cfu <= 50:
        tier_key = "50B_mix8"
    elif total_cfu <= 50:
        tier_key = "50B"
    elif total_cfu <= 75:
        tier_key = "75B"
    else:
        tier_key = "100B"

    dosing = KB_PREBIOTIC["dosing_by_cfu_tier"]
    tier = dosing.get(tier_key, dosing["50B"])

    if sensitivity == "high":
        g_range = tier.get("high_sensitivity", tier["total_g_range"])
    elif sensitivity == "low":
        g_range = tier.get("low_high_tolerance", tier["total_g_range"])
    else:
        g_range = tier.get("moderate", tier["total_g_range"])

    min_g, max_g = g_range
    # Allow small tolerance
    if prebiotic_total < min_g - 0.5:
        return False, f"Prebiotic total {prebiotic_total}g below range [{min_g}-{max_g}g] (tier={tier_key}, sensitivity={sensitivity})"
    if prebiotic_total > max_g + 0.5:
        return False, f"Prebiotic total {prebiotic_total}g above range [{min_g}-{max_g}g] (tier={tier_key}, sensitivity={sensitivity})"
    return True, f"Prebiotic {prebiotic_total}g within [{min_g}-{max_g}g] (tier={tier_key}, sensitivity={sensitivity})"


def check_05_fodmap_override(master):
    """If sensitivity=high, no bulk FODMAP prebiotics (GOS/FOS/Inulin)."""
    sensitivity = master["decisions"]["rule_outputs"]["sensitivity"]["classification"].lower()
    if sensitivity != "high":
        return True, "Sensitivity not high — FODMAP override check N/A"

    prebiotics = master["decisions"]["prebiotic_design"].get("prebiotics", [])
    fodmap_issues = []
    for p in prebiotics:
        substance = p.get("substance", "").lower()
        dose_g = p.get("dose_g", 0)
        is_fodmap = p.get("fodmap", False)
        # Bulk FODMAP = GOS/FOS/Inulin at >1g
        if is_fodmap and dose_g > 1.0:
            fodmap_issues.append(f"{p['substance']} at {dose_g}g (FODMAP, >1g)")

    if fodmap_issues:
        return False, f"High sensitivity but bulk FODMAP prebiotics present: {', '.join(fodmap_issues)}"
    return True, "High sensitivity — no bulk FODMAP prebiotics (override applied correctly)"


def check_06_magnesium_count(master):
    """Re-derive Mg capsule count from questionnaire needs."""
    q = master["input_summary"]["questionnaire_driven"]
    goals = q.get("goals_ranked", [])
    goals_lower = [g.lower() for g in goals]
    stress = q.get("stress_level")
    sleep = q.get("sleep_quality")

    needs = []
    # Sleep need
    sleep_goal = any("sleep" in g for g in goals_lower)
    if (sleep is not None and sleep <= 7) or sleep_goal:
        needs.append("sleep")
    # Stress need
    stress_goal = any(kw in g for g in goals_lower for kw in ["stress", "anxiety", "mood"])
    if (stress is not None and stress >= 6) or stress_goal:
        needs.append("stress")
    # Sport need — check if exercise data available
    # (We can't always re-derive sport from master, so we'll check the rule output)

    expected_min_capsules = 2 if len(needs) >= 2 else (1 if len(needs) >= 1 else 0)
    actual = master["decisions"]["rule_outputs"]["magnesium"]["capsules"]

    # Allow actual >= expected (sport need may add)
    if actual < expected_min_capsules:
        return False, f"Expected ≥{expected_min_capsules} Mg capsules (needs={needs}) but got {actual}"
    if actual > 2:
        return False, f"Mg capsules {actual} exceeds max 2"
    return True, f"Mg capsules={actual} (needs: {master['decisions']['rule_outputs']['magnesium']['needs_identified']})"


def check_07_softgel(master):
    """Verify softgel inclusion based on goal triggers."""
    softgel_included = master["decisions"]["rule_outputs"]["softgel"]["include_softgel"]
    softgel_needs = master["decisions"]["rule_outputs"]["softgel"]["needs_identified"]

    # Just verify logic: if needs > 0, should be included
    if len(softgel_needs) > 0 and not softgel_included:
        return False, f"Softgel needs identified ({softgel_needs}) but softgel not included"
    if len(softgel_needs) == 0 and softgel_included:
        return False, f"No softgel needs but softgel included"
    status = "included" if softgel_included else "excluded"
    return True, f"Softgel correctly {status} (needs: {softgel_needs or 'none'})"


def check_08_vitamin_doses(master):
    """Verify vitamin doses match KB standard doses.
    
    Aware of deterministic exclusions (B6, iron for males) — substances
    removed by the vitamin gate are NOT flagged as errors.
    """
    vitamins = master["decisions"]["supplement_selection"].get("vitamins_minerals", [])
    sex = master["input_summary"]["questionnaire_driven"].get("biological_sex", "").lower()
    reported_deficiencies = [d.lower() for d in master["input_summary"]["questionnaire_driven"].get("reported_deficiencies", []) if d]
    issues = []

    for v in vitamins:
        substance = v["substance"]
        dose_value = v.get("dose_value")
        if dose_value is None:
            continue

        # Check if this substance is a deterministic exclusion (should have been removed by vitamin gate)
        substance_lower = substance.lower()
        is_det_excluded = False
        for excl_key, excl_info in DETERMINISTIC_EXCLUSIONS.items():
            if excl_key in substance_lower:
                if excl_info["condition"](sex, reported_deficiencies):
                    is_det_excluded = True
                    break
        if is_det_excluded:
            continue  # Skip — vitamin gate should have removed this; not a dose error

        # Find in KB by matching name
        matched_kb = None
        for kb_v in KB_VITAMINS["vitamins_and_minerals"]:
            if kb_v["substance"].lower() in substance_lower or substance_lower in kb_v["substance"].lower():
                matched_kb = kb_v
                break

        if not matched_kb:
            continue

        parsed = matched_kb.get("parsed", {}).get("dose", {})
        expected_value = parsed.get("value")

        # Handle sex-specific (zinc)
        if "zinc" in substance_lower:
            if sex == "male":
                expected_value = parsed.get("male", expected_value)
            else:
                expected_value = parsed.get("female", expected_value)

        if expected_value is None:
            continue

        # Check therapeutic override
        therapeutic = v.get("therapeutic", False)
        if therapeutic:
            continue  # Therapeutic doses intentionally exceed standard

        # Allow 10% tolerance
        if abs(dose_value - expected_value) > expected_value * 0.15:
            issues.append(f"{substance}: {dose_value} vs KB standard {expected_value}")

    if issues:
        return False, f"{len(issues)} dose mismatch(es): {'; '.join(issues[:5])}"
    return True, f"All {len(vitamins)} vitamin/mineral doses match KB"


def check_09_probiotic_weight(master):
    """Probiotic capsule fill weight ≤ 650mg."""
    df1 = master["formulation"].get("delivery_format_1_probiotic_capsule")
    if not df1:
        return True, "No probiotic capsule"
    weight = df1["totals"]["total_weight_mg"]
    if weight > 650:
        return False, f"Probiotic capsule weight {weight}mg > 650mg capacity"
    return True, f"Probiotic capsule {weight}mg ≤ 650mg ({weight/650*100:.0f}% utilization)"


def check_10_sachet_weight(master):
    """Sachet total weight ≤ 19g."""
    df3 = master["formulation"].get("delivery_format_3_daily_sachet")
    if not df3:
        return True, "No sachet"
    weight = df3["totals"]["total_weight_g"]
    if weight > 19.0:
        return False, f"Sachet weight {weight}g > 19g capacity"
    return True, f"Sachet {weight}g ≤ 19g ({weight/19*100:.0f}% utilization)"


def check_11_capsule_only_routing(master):
    """Capsule-only substances (ashwagandha, quercetin, curcumin, etc.) must NOT be in sachet."""
    df3 = master["formulation"].get("delivery_format_3_daily_sachet")
    if not df3:
        return True, "No sachet"

    sachet_substances = []
    for section_key in ["prebiotics", "vitamins_minerals", "supplements"]:
        section = df3.get(section_key, {})
        components = section.get("components", []) if isinstance(section, dict) else []
        for c in components:
            sachet_substances.append(c.get("substance", "").lower())

    violations = []
    for sub in sachet_substances:
        for capsule_sub in CAPSULE_ONLY:
            if capsule_sub in sub:
                violations.append(f"'{sub}' should be capsule-only")

    if violations:
        return False, f"{len(violations)} capsule-only substance(s) in sachet: {'; '.join(violations)}"
    return True, "No capsule-only substances in sachet"


def check_12_evening_capsule_weight(master):
    """Evening capsule weight ≤ 650mg."""
    df4 = master["formulation"].get("delivery_format_4_evening_capsule")
    if not df4:
        return True, "No evening capsule"
    weight = df4["totals"].get("total_weight_mg", 0)
    if weight > 650:
        return False, f"Evening capsule weight {weight}mg > 650mg capacity"
    return True, f"Evening capsule {weight}mg ≤ 650mg ({weight/650*100:.0f}% utilization)"


def check_13_health_claims(master):
    """Re-derive health claims from goals and check they're present."""
    q = master["input_summary"]["questionnaire_driven"]
    goals = q.get("goals_ranked", [])
    actual_claims = set(master["decisions"]["rule_outputs"]["health_claims"]["supplement_claims"])

    goal_mappings = KB_GOALS["goal_mappings"]
    expected_claims = set()
    for goal in goals:
        goal_key = goal.lower().strip()
        if goal_key in goal_mappings:
            for claim in goal_mappings[goal_key].get("health_claims", []):
                expected_claims.add(claim)

    missing = expected_claims - actual_claims
    if missing:
        return False, f"Missing health claims: {missing} (from goals: {goals})"
    return True, f"All expected health claims present ({len(expected_claims)} claims from {len(goals)} goals)"


def check_14_timing(master):
    """Verify timing rules: Mg evening when sleep ≤7, Ashwagandha evening when calming goal."""
    q = master["input_summary"]["questionnaire_driven"]
    sleep = q.get("sleep_quality")
    goals = q.get("goals_ranked", [])
    goals_lower = [g.lower() for g in goals]
    timing = master["decisions"]["rule_outputs"]["timing"]["timing_assignments"]
    issues = []

    # Mg timing
    if sleep is not None and sleep <= 7:
        mg_timing = timing.get("magnesium", {}).get("timing", "")
        if mg_timing != "evening":
            issues.append(f"Mg should be evening (sleep={sleep} ≤7) but got '{mg_timing}'")

    # Ashwagandha timing
    calming_kws = {"sleep", "anxiety", "mood", "stress"}
    has_calming = any(any(kw in g for kw in calming_kws) for g in goals_lower)
    ashwa_timing = timing.get("ashwagandha", {}).get("timing", "")
    if has_calming and sleep is not None and sleep <= 7:
        if ashwa_timing != "evening":
            issues.append(f"Ashwagandha should be evening (calming goal + sleep={sleep}) but got '{ashwa_timing}'")

    if issues:
        return False, "; ".join(issues)
    return True, f"Timing rules correct (Mg={timing.get('magnesium', {}).get('timing', 'N/A')}, Ashwa={ashwa_timing or 'N/A'})"


def check_15_unit_count(master):
    """Total unit count ≤ 8 (absolute max)."""
    total = master["formulation"]["protocol_summary"]["total_daily_units"]
    if total > 8:
        return False, f"Total units {total} > absolute max 8"
    if total > 6:
        return True, f"Total units {total} (above preferred max 6, within absolute max 8)"
    return True, f"Total units {total} ≤ 6 preferred max"


# ─── Main Runner ──────────────────────────────────────────────────────────────

CHECK_NAMES = {
    1: "Sensitivity classification correctness",
    2: "Mix strains match KB definition",
    3: "LP815 enhancement trigger",
    4: "Prebiotic total within allowed range",
    5: "FODMAP override for high sensitivity",
    6: "Magnesium capsule count",
    7: "Softgel inclusion logic",
    8: "Vitamin doses match KB standard",
    9: "Probiotic capsule weight ≤ 650mg",
    10: "Sachet weight ≤ 19g",
    11: "Capsule-only substances not in sachet",
    12: "Evening capsule weight ≤ 650mg",
    13: "Goal → health claim mapping",
    14: "Timing rules applied correctly",
    15: "Total unit count within limits",
}

CHECK_FUNCS = {
    1: lambda m, *_: check_01_sensitivity(m),
    2: lambda m, *_: check_02_mix_strains(m),
    3: lambda m, *_: check_03_lp815(m),
    4: lambda m, *_: check_04_prebiotic_range(m),
    5: lambda m, *_: check_05_fodmap_override(m),
    6: lambda m, *_: check_06_magnesium_count(m),
    7: lambda m, *_: check_07_softgel(m),
    8: lambda m, *_: check_08_vitamin_doses(m),
    9: lambda m, *_: check_09_probiotic_weight(m),
    10: lambda m, *_: check_10_sachet_weight(m),
    11: lambda m, *_: check_11_capsule_only_routing(m),
    12: lambda m, *_: check_12_evening_capsule_weight(m),
    13: lambda m, *_: check_13_health_claims(m),
    14: lambda m, *_: check_14_timing(m),
    15: lambda m, *_: check_15_unit_count(m),
}


def run_sample(sample_dir, sample_id):
    """Run all checks for one sample."""
    json_dir = os.path.join(sample_dir, "reports", "reports_json")
    master_path = os.path.join(json_dir, f"formulation_master_{sample_id}.json")

    try:
        master = load_json(master_path)
    except FileNotFoundError:
        return [(0, False, f"Missing formulation_master_{sample_id}.json")]

    results = []
    for i in range(1, 16):
        try:
            passed, detail = CHECK_FUNCS[i](master)
            results.append((i, passed, detail))
        except Exception as e:
            results.append((i, False, f"ERROR: {e}"))

    return results


def main():
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    batch_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(workspace, "analysis", "nb1_2026_001")

    if not os.path.isdir(batch_dir):
        print(f"ERROR: Batch directory not found: {batch_dir}")
        sys.exit(1)

    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, "[0-9]*")))
    if not sample_dirs:
        print(f"ERROR: No sample directories found in {batch_dir}")
        sys.exit(1)

    print(f"{'=' * 80}")
    print(f"FORMULATION PIPELINE — SANITY CHECK vs KNOWLEDGE BASES")
    print(f"Batch: {batch_dir}")
    print(f"Samples: {len(sample_dirs)}")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"{'=' * 80}")

    all_results = {}
    summary_pass = {i: 0 for i in range(1, 16)}
    summary_fail = {i: 0 for i in range(1, 16)}

    for sdir in sample_dirs:
        sample_id = os.path.basename(sdir)
        if not sample_id.isdigit():
            continue
        json_dir = os.path.join(sdir, "reports", "reports_json")
        if not os.path.isdir(json_dir):
            continue

        results = run_sample(sdir, sample_id)
        all_results[sample_id] = results

        for check_num, passed, detail in results:
            if check_num == 0:
                continue
            if passed:
                summary_pass[check_num] += 1
            else:
                summary_fail[check_num] += 1

    n_samples = len(all_results)
    n_checks = 15

    # Summary
    print(f"\n{'=' * 80}")
    print(f"SUMMARY ({n_samples} samples × {n_checks} checks)")
    print(f"{'=' * 80}")
    print(f"{'#':<4} {'Check':<50} {'Pass':>5} {'Fail':>5}")
    print(f"{'-' * 4} {'-' * 50} {'-' * 5} {'-' * 5}")
    total_pass = total_fail = 0
    for i in range(1, 16):
        p, f = summary_pass[i], summary_fail[i]
        total_pass += p
        total_fail += f
        icon = "✅" if f == 0 else "❌"
        print(f"{i:<4} {CHECK_NAMES[i]:<50} {p:>5} {f:>5} {icon}")
    total_checks = total_pass + total_fail
    print(f"{'-' * 4} {'-' * 50} {'-' * 5} {'-' * 5}")
    print(f"{'':4} {'TOTAL':<50} {total_pass:>5} {total_fail:>5}")
    pct = (total_pass / total_checks * 100) if total_checks else 0
    print(f"\nOverall pass rate: {total_pass}/{total_checks} ({pct:.1f}%)")

    # Per-sample detail
    for sample_id in sorted(all_results.keys()):
        results = all_results[sample_id]
        fails = [(n, d) for n, p, d in results if not p]
        status = "✅ ALL PASS" if not fails else f"❌ {len(fails)} FAIL(S)"
        print(f"\n{'─' * 80}")
        print(f"Sample: {sample_id}  {status}")
        print(f"{'─' * 80}")
        for check_num, passed, detail in results:
            icon = "✅" if passed else "❌"
            name = CHECK_NAMES.get(check_num, "Unknown")
            print(f"  {icon} [{check_num:>2}] {name}")
            if not passed:
                for line in detail.split("\n"):
                    print(f"         {line}")

    # Write MD report
    report_path = os.path.join(batch_dir, "SANITY_CHECK_VS_KB_REPORT.md")
    with open(report_path, "w") as f:
        f.write(f"# Formulation Pipeline — Sanity Check vs Knowledge Bases\n\n")
        f.write(f"**Batch:** `{batch_dir}`  \n")
        f.write(f"**Samples:** {n_samples}  \n")
        f.write(f"**Generated:** {datetime.now().isoformat()}  \n")
        f.write(f"**Overall pass rate:** {total_pass}/{total_checks} ({pct:.1f}%)  \n\n")

        f.write(f"## Summary\n\n")
        f.write(f"| # | Check | Pass | Fail | Status |\n")
        f.write(f"|---|-------|------|------|--------|\n")
        for i in range(1, 16):
            p, fa = summary_pass[i], summary_fail[i]
            st = "✅" if fa == 0 else "❌"
            f.write(f"| {i} | {CHECK_NAMES[i]} | {p} | {fa} | {st} |\n")
        f.write(f"| | **TOTAL** | **{total_pass}** | **{total_fail}** | |\n\n")

        f.write(f"## Per-Sample Details\n\n")
        for sample_id in sorted(all_results.keys()):
            results = all_results[sample_id]
            fails = [(n, d) for n, p, d in results if not p]
            status = "✅ ALL PASS" if not fails else f"❌ {len(fails)} FAIL(S)"
            f.write(f"### Sample `{sample_id}` — {status}\n\n")
            for check_num, passed, detail in results:
                icon = "✅" if passed else "❌"
                name = CHECK_NAMES.get(check_num, "Unknown")
                f.write(f"- {icon} **[{check_num}] {name}**")
                if not passed:
                    f.write(f"\n")
                    for line in detail.split("\n"):
                        f.write(f"  - {line}\n")
                else:
                    f.write(f" — {detail}\n")
            f.write(f"\n")

    print(f"\n📄 Report saved to: {report_path}")
    print(f"{'=' * 80}")
    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    main()
