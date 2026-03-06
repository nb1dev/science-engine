#!/usr/bin/env python3
"""
Cross-File Consistency Audit for Formulation Pipeline Outputs.

Checks all 5 JSON output files + HTML dashboard per sample for:
  1.  Master vs Platform: total_units, total_weight_g, validation_status
  2.  Master vs Decision Trace: total_units, total_weight_g, validation
  3.  Master vs Manufacturing Recipe: grand_total values
  4.  Component registry count == component_rationale count
  5.  Evening capsule label: not "Sleep Support Capsule" if no sleep aids
  6.  Polyphenol capsule label: should be "Morning Wellness Capsule"
  7.  No "you/your" in board dashboard HTML
  8.  All delivery formats in master appear in platform, trace, recipe
  9.  Polyphenol capsule (df5) in all outputs when present
  10. Ecological rationale not empty
  11. informed_by populated for every component in registry

Usage:
    python consistency_check.py [batch_dir]
    Default batch_dir: analysis/nb1_2026_001
"""

import json
import glob
import os
import re
import sys
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def approx_eq(a, b, tol=0.02):
    """Float approximate equality."""
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) <= tol


def normalise_label(label):
    """Lowercase, strip whitespace, collapse spaces."""
    if not label:
        return ""
    return re.sub(r"\s+", " ", label.strip().lower())


# Map from master delivery_format keys to canonical short names used in other files
DELIVERY_FORMAT_CANONICAL = {
    "delivery_format_1_probiotic_capsule": ["probiotic"],
    "delivery_format_2_omega_softgels": ["omega", "softgel"],
    "delivery_format_3_daily_sachet": ["sachet"],
    "delivery_format_4_evening_capsule": ["evening", "capsule", "wellness"],
    "delivery_format_5_polyphenol_capsule": ["morning wellness", "polyphenol", "wellness capsule"],
}

# Known sleep-aid substances (if these are absent → label shouldn't say "Sleep Support")
SLEEP_AID_SUBSTANCES = {"melatonin", "l-theanine", "valerian", "5-htp", "gaba", "magnesium"}

# Substances that are NOT sleep aids but can appear in evening capsule
NON_SLEEP_EVENING = {"ashwagandha", "propolis", "quercetin"}


# ---------------------------------------------------------------------------
# Check functions — each returns (passed: bool, detail: str)
# ---------------------------------------------------------------------------

def check_1_master_vs_platform(master, platform):
    """Total units, total weight, validation status match between master and platform."""
    issues = []
    m_units = master["formulation"]["protocol_summary"]["total_daily_units"]
    p_units = platform["overview"]["total_daily_units"]
    if m_units != p_units:
        issues.append(f"total_daily_units: master={m_units} vs platform={p_units}")

    m_weight = master["formulation"]["protocol_summary"]["total_daily_weight_g"]
    p_weight = platform["overview"]["total_daily_weight_g"]
    if not approx_eq(m_weight, p_weight):
        issues.append(f"total_daily_weight_g: master={m_weight} vs platform={p_weight}")

    m_val = master["metadata"]["validation_status"]
    p_val = platform["metadata"]["validation_status"]
    if m_val != p_val:
        issues.append(f"validation_status: master={m_val} vs platform={p_val}")

    if issues:
        return False, "; ".join(issues)
    return True, f"units={m_units}, weight={m_weight}g, validation={m_val}"


def check_2_master_vs_trace(master, trace):
    """Total units, total weight, validation match between master and decision trace."""
    issues = []
    m_units = master["formulation"]["protocol_summary"]["total_daily_units"]
    m_weight = master["formulation"]["protocol_summary"]["total_daily_weight_g"]
    m_val = master["metadata"]["validation_status"]

    t_ff = trace.get("final_formulation", {})
    t_units = t_ff.get("total_units")
    t_weight = t_ff.get("total_weight_g")
    t_val = t_ff.get("validation", trace.get("validation"))

    if m_units != t_units:
        issues.append(f"total_units: master={m_units} vs trace={t_units}")
    if not approx_eq(m_weight, t_weight):
        issues.append(f"total_weight_g: master={m_weight} vs trace={t_weight}")
    if m_val != t_val:
        issues.append(f"validation: master={m_val} vs trace={t_val}")

    if issues:
        return False, "; ".join(issues)
    return True, "OK"


def check_3_master_vs_recipe(master, recipe):
    """Grand total values match between master and manufacturing recipe."""
    issues = []
    m_units = master["formulation"]["protocol_summary"]["total_daily_units"]
    m_weight = master["formulation"]["protocol_summary"]["total_daily_weight_g"]

    gt = recipe.get("grand_total", {})
    r_units = gt.get("total_units")
    r_weight = gt.get("total_daily_weight_g")

    if m_units != r_units:
        issues.append(f"total_units: master={m_units} vs recipe={r_units}")
    if not approx_eq(m_weight, r_weight):
        issues.append(f"total_weight_g: master={m_weight} vs recipe={r_weight}")

    r_val = recipe.get("validation")
    m_val = master["metadata"]["validation_status"]
    if r_val and m_val != r_val:
        issues.append(f"validation: master={m_val} vs recipe={r_val}")

    if issues:
        return False, "; ".join(issues)
    return True, "OK"


def check_4_component_counts(master, rationale):
    """component_registry count == how_this_addresses_your_health count."""
    m_count = len(master.get("component_registry", []))
    r_count = len(rationale.get("how_this_addresses_your_health", []))
    if m_count != r_count:
        return False, f"component_registry={m_count} vs how_this_addresses_your_health={r_count}"
    return True, f"both={m_count}"


def check_5_evening_capsule_label(master, platform):
    """Evening capsule label should NOT be 'Sleep Support Capsule' if it contains
    Ashwagandha/Propolis/Quercetin without actual sleep aids."""
    ev_format = master["formulation"].get("delivery_format_4_evening_capsule")
    if not ev_format:
        return True, "No evening capsule present"

    components = ev_format.get("components", [])
    substance_names = [c.get("substance", "").lower() for c in components]

    has_sleep_aid = any(
        any(sa in name for sa in SLEEP_AID_SUBSTANCES)
        for name in substance_names
    )
    has_non_sleep = any(
        any(ns in name for ns in NON_SLEEP_EVENING)
        for name in substance_names
    )

    # Check label in platform
    evening_units = platform.get("delivery_units", {}).get("evening", [])
    for unit in evening_units:
        label = normalise_label(unit.get("label", ""))
        if "sleep support capsule" in label and has_non_sleep and not has_sleep_aid:
            return False, (
                f"Label '{unit['label']}' says 'Sleep Support' but contains "
                f"{substance_names} without sleep aids"
            )

    return True, f"Evening label OK (components: {', '.join(substance_names) or 'none'})"


def check_6_polyphenol_label(master, platform, recipe):
    """Polyphenol capsule label should be 'Morning Wellness Capsule' 
    (not 'Polyphenol Hard Capsule')."""
    df5 = master["formulation"].get("delivery_format_5_polyphenol_capsule")
    if not df5:
        return True, "No polyphenol capsule (df5 is null)"

    issues = []

    # Check platform
    for timing, units in platform.get("delivery_units", {}).items():
        for unit in units:
            label = normalise_label(unit.get("label", ""))
            if "polyphenol" in label and "morning wellness" not in label:
                issues.append(f"Platform label: '{unit['label']}' (should be 'Morning Wellness Capsule')")

    # Check recipe
    for unit in recipe.get("units", []):
        label = normalise_label(unit.get("label", ""))
        if "polyphenol" in label and "morning wellness" not in label:
            issues.append(f"Recipe label: '{unit['label']}' (should be 'Morning Wellness Capsule')")

    if issues:
        return False, "; ".join(issues)

    # Verify it actually says "Morning Wellness Capsule" somewhere
    found_mwc = False
    for timing, units in platform.get("delivery_units", {}).items():
        for unit in units:
            if "morning wellness" in normalise_label(unit.get("label", "")):
                found_mwc = True
    for unit in recipe.get("units", []):
        if "morning wellness" in normalise_label(unit.get("label", "")):
            found_mwc = True

    if not found_mwc:
        return False, "df5 present in master but no 'Morning Wellness Capsule' label found in platform/recipe"

    return True, "Correctly labelled 'Morning Wellness Capsule'"


def check_7_html_you_your(html_path):
    """No 'you/your/you're/yours' in board dashboard HTML — should be 'this sample's'."""
    if not os.path.exists(html_path):
        return False, f"HTML file not found: {html_path}"

    with open(html_path, "r") as f:
        html_content = f.read()

    # Pattern: word-boundary 'you', 'your', 'you're', 'yours', 'yourself'
    # Exclude occurrences inside JSON-like strings that are clearly data attributes
    pattern = re.compile(r'\b(you|your|you\'re|yours|yourself)\b', re.IGNORECASE)
    matches = []

    for i, line in enumerate(html_content.split("\n"), 1):
        # Skip <style> and <script> blocks, meta tags, and title
        stripped = line.strip()
        if stripped.startswith(("<style", "</style", "<script", "</script", "<meta", "<title")):
            continue
        found = pattern.findall(line)
        if found:
            # Get context (truncate long lines)
            context = line.strip()[:120]
            matches.append(f"Line {i}: '{', '.join(found)}' in: ...{context}...")

    if matches:
        return False, f"{len(matches)} occurrence(s) found:\n" + "\n".join(f"    {m}" for m in matches[:10])
    return True, "No 'you/your' found"


def _get_master_delivery_formats(master):
    """Return list of (key, label_hint) for non-null delivery formats in master."""
    formulation = master["formulation"]
    formats = []
    for key, search_terms in DELIVERY_FORMAT_CANONICAL.items():
        val = formulation.get(key)
        if val is not None:
            formats.append((key, search_terms))
    return formats


def _format_present_in_units(units_list, search_terms):
    """Check if any unit in a list matches any of the search terms."""
    for unit in units_list:
        # unit can have 'type', 'label', or 'format.type'
        label = normalise_label(unit.get("label", "") or unit.get("type", ""))
        contents = normalise_label(unit.get("contents_summary", "") or unit.get("contents", ""))
        combined = label + " " + contents
        if any(term in combined for term in search_terms):
            return True
    return False


def check_8_delivery_format_presence(master, platform, trace, recipe):
    """All delivery formats present in master must appear in platform, trace, and recipe."""
    active_formats = _get_master_delivery_formats(master)
    issues = []

    for key, terms in active_formats:
        # Platform check
        all_platform_units = []
        for timing, units in platform.get("delivery_units", {}).items():
            all_platform_units.extend(units)
        if not _format_present_in_units(all_platform_units, terms):
            issues.append(f"{key} missing from platform delivery_units")

        # Trace check
        trace_units = trace.get("final_formulation", {}).get("delivery_units", [])
        if not _format_present_in_units(trace_units, terms):
            issues.append(f"{key} missing from trace final_formulation.delivery_units")

        # Recipe check
        recipe_units = recipe.get("units", [])
        if not _format_present_in_units(recipe_units, terms):
            issues.append(f"{key} missing from recipe units")

    if issues:
        return False, "; ".join(issues)
    return True, f"All {len(active_formats)} delivery formats present across all files"


def check_9_polyphenol_in_all(master, platform, trace, recipe):
    """If df5 polyphenol capsule is present in master, it must appear in all outputs."""
    df5 = master["formulation"].get("delivery_format_5_polyphenol_capsule")
    if not df5:
        return True, "df5 is null — N/A"

    terms = DELIVERY_FORMAT_CANONICAL["delivery_format_5_polyphenol_capsule"]
    issues = []

    all_platform_units = []
    for timing, units in platform.get("delivery_units", {}).items():
        all_platform_units.extend(units)
    if not _format_present_in_units(all_platform_units, terms):
        issues.append("Missing from platform")

    trace_units = trace.get("final_formulation", {}).get("delivery_units", [])
    if not _format_present_in_units(trace_units, terms):
        issues.append("Missing from trace")

    recipe_units = recipe.get("units", [])
    if not _format_present_in_units(recipe_units, terms):
        issues.append("Missing from recipe")

    if issues:
        return False, f"df5 present in master but: {'; '.join(issues)}"
    return True, "df5 present in all outputs"


def check_10_ecological_rationale(master):
    """ecological_rationale.selected_rationale must not be empty."""
    eco = master.get("ecological_rationale", {})
    selected = eco.get("selected_rationale", "")
    if not selected or not selected.strip():
        return False, "ecological_rationale.selected_rationale is empty"
    # Also check alternative and combined
    issues = []
    if not eco.get("alternative_analysis", "").strip():
        issues.append("alternative_analysis empty")
    if not eco.get("combined_assessment", "").strip():
        issues.append("combined_assessment empty")
    if not eco.get("recommendation", "").strip():
        issues.append("recommendation empty")
    if issues:
        return False, f"selected_rationale OK but: {'; '.join(issues)}"
    return True, f"All 4 rationale sections populated ({len(selected)} chars)"


def check_11_informed_by(master):
    """component_registry informed_by field must be populated for every component."""
    registry = master.get("component_registry", [])
    missing = []
    for i, comp in enumerate(registry):
        informed = comp.get("informed_by")
        if not informed or (isinstance(informed, str) and not informed.strip()):
            substance = comp.get("substance", f"component #{i}")
            missing.append(substance)
    if missing:
        return False, f"{len(missing)} component(s) missing informed_by: {', '.join(missing[:5])}"
    return True, f"All {len(registry)} components have informed_by"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

CHECK_NAMES = {
    1: "Master vs Platform (units/weight/validation)",
    2: "Master vs Trace (units/weight/validation)",
    3: "Master vs Recipe (grand_total)",
    4: "Component registry count = rationale count",
    5: "Evening capsule label (no 'Sleep Support' without sleep aids)",
    6: "Polyphenol capsule label ('Morning Wellness Capsule')",
    7: "No 'you/your' in HTML dashboard",
    8: "All delivery formats present across files",
    9: "Polyphenol capsule (df5) in all outputs",
    10: "Ecological rationale populated",
    11: "informed_by populated for all components",
}


def run_sample(sample_dir, sample_id):
    """Run all checks for one sample. Returns list of (check_num, passed, detail)."""
    json_dir = os.path.join(sample_dir, "reports", "reports_json")
    html_dir = os.path.join(sample_dir, "reports", "reports_html")

    master_path = os.path.join(json_dir, f"formulation_master_{sample_id}.json")
    platform_path = os.path.join(json_dir, f"formulation_platform_{sample_id}.json")
    trace_path = os.path.join(json_dir, f"decision_trace_{sample_id}.json")
    recipe_path = os.path.join(json_dir, f"manufacturing_recipe_{sample_id}.json")
    rationale_path = os.path.join(json_dir, f"component_rationale_{sample_id}.json")
    html_path = os.path.join(html_dir, f"formulation_decision_trace_{sample_id}.html")

    # Load files
    try:
        master = load_json(master_path)
        platform = load_json(platform_path)
        trace = load_json(trace_path)
        recipe = load_json(recipe_path)
        rationale = load_json(rationale_path)
    except FileNotFoundError as e:
        return [(0, False, f"Missing file: {e}")]

    results = []
    results.append((1, *check_1_master_vs_platform(master, platform)))
    results.append((2, *check_2_master_vs_trace(master, trace)))
    results.append((3, *check_3_master_vs_recipe(master, recipe)))
    results.append((4, *check_4_component_counts(master, rationale)))
    results.append((5, *check_5_evening_capsule_label(master, platform)))
    results.append((6, *check_6_polyphenol_label(master, platform, recipe)))
    results.append((7, *check_7_html_you_your(html_path)))
    results.append((8, *check_8_delivery_format_presence(master, platform, trace, recipe)))
    results.append((9, *check_9_polyphenol_in_all(master, platform, trace, recipe)))
    results.append((10, *check_10_ecological_rationale(master)))
    results.append((11, *check_11_informed_by(master)))

    return results


def main():
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    batch_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(workspace, "analysis", "nb1_2026_001")

    if not os.path.isdir(batch_dir):
        print(f"ERROR: Batch directory not found: {batch_dir}")
        sys.exit(1)

    # Discover samples
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, "[0-9]*")))
    if not sample_dirs:
        print(f"ERROR: No sample directories found in {batch_dir}")
        sys.exit(1)

    print(f"=" * 80)
    print(f"FORMULATION PIPELINE — CROSS-FILE CONSISTENCY AUDIT")
    print(f"Batch: {batch_dir}")
    print(f"Samples: {len(sample_dirs)}")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"=" * 80)

    all_results = {}  # sample_id -> [(check_num, passed, detail)]
    summary_pass = {i: 0 for i in range(1, 12)}
    summary_fail = {i: 0 for i in range(1, 12)}

    for sdir in sample_dirs:
        sample_id = os.path.basename(sdir)
        # Skip non-sample dirs
        if not sample_id.isdigit():
            continue
        # Check if reports exist
        json_dir = os.path.join(sdir, "reports", "reports_json")
        if not os.path.isdir(json_dir):
            print(f"\n⚠️  {sample_id}: No reports/reports_json/ directory — skipping")
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

    # -----------------------------------------------------------------------
    # Print results
    # -----------------------------------------------------------------------
    n_samples = len(all_results)

    # Summary table
    print(f"\n{'=' * 80}")
    print(f"SUMMARY ({n_samples} samples)")
    print(f"{'=' * 80}")
    print(f"{'#':<4} {'Check':<55} {'Pass':>5} {'Fail':>5}")
    print(f"{'-' * 4} {'-' * 55} {'-' * 5} {'-' * 5}")
    total_checks = 0
    total_pass = 0
    total_fail = 0
    for i in range(1, 12):
        p = summary_pass[i]
        f = summary_fail[i]
        total_checks += p + f
        total_pass += p
        total_fail += f
        status = "✅" if f == 0 else "❌"
        print(f"{i:<4} {CHECK_NAMES[i]:<55} {p:>5} {f:>5} {status}")
    print(f"{'-' * 4} {'-' * 55} {'-' * 5} {'-' * 5}")
    print(f"{'':4} {'TOTAL':<55} {total_pass:>5} {total_fail:>5}")
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

    # -----------------------------------------------------------------------
    # Write Markdown report
    # -----------------------------------------------------------------------
    report_path = os.path.join(batch_dir, "CONSISTENCY_CHECK_REPORT.md")
    with open(report_path, "w") as f:
        f.write(f"# Formulation Pipeline — Cross-File Consistency Audit\n\n")
        f.write(f"**Batch:** `{batch_dir}`  \n")
        f.write(f"**Samples:** {n_samples}  \n")
        f.write(f"**Generated:** {datetime.now().isoformat()}  \n")
        f.write(f"**Overall pass rate:** {total_pass}/{total_checks} ({pct:.1f}%)  \n\n")

        f.write(f"## Summary\n\n")
        f.write(f"| # | Check | Pass | Fail | Status |\n")
        f.write(f"|---|-------|------|------|--------|\n")
        for i in range(1, 12):
            p = summary_pass[i]
            fa = summary_fail[i]
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

    # Exit code
    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    main()
