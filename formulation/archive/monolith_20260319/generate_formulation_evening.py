#!/usr/bin/env python3
"""
Evening Formulation Pipeline — Medication Timing Override variant.

When a Tier A medication rule (e.g., MED_001 for levothyroxine) requires all
supplement units to be taken at dinner, the standard pipeline's display output
uses hardcoded morning labels because the override is applied post-assembly
(Stage 8.5). This module provides an evening-aware pipeline that produces
identical JSON output but with corrected display labels throughout.

Architecture:
  generate_formulation.py (Stage A.6) detects timing override
  → redirects to this module
  → runs standard pipeline silently (correct JSON, wrong display)
  → patches pipeline log with evening labels
  → prints corrected output to terminal

The master JSON, platform JSON, recipe, dashboards, and trace are all
correct (built from the post-override master). Only the pipeline log
display needs label correction.

Usage:
    # Called automatically by generate_formulation.py when timing override detected.
    # Can also be invoked directly for testing:
    python generate_formulation_evening.py --sample-dir /path/to/sample/

    # Offline mode
    python generate_formulation_evening.py --sample-dir /path/to/sample/ --no-llm
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


SCRIPT_DIR = Path(__file__).parent


# ─── LABEL PATCHES ────────────────────────────────────────────────────────────
# Each tuple: (original_label, evening_label)
# Applied in order to the pipeline log text. Order matters for overlapping
# patterns (longer/more specific patterns first).

EVENING_LABEL_PATCHES = [
    # Display box icons + labels
    ("🌅 Morning cap",                   "🌙 Dinner cap"),
    # Trace section headers
    ("Morning wellness cap",             "Dinner wellness cap"),
    ("🌅 Morning wellness cap",          "🌙 Dinner wellness cap"),
    # Component registry delivery labels
    ("morning wellness capsule",         "dinner wellness capsule"),
    # Format labels in JSON-like display
    ("Morning Wellness Capsule",         "Dinner Wellness Capsule"),
    ("Morning Wellness Capsules",        "Dinner Wellness Capsules"),
    # Routing trace
    ("morning timing avoids sleep interference",
     "dinner timing for medication spacing (levothyroxine)"),
    ("Energy/metabolic/immune support components; morning timing avoids sleep interference",
     "Energy/metabolic/immune support components; dinner timing for medication spacing"),
]

# HTML-specific patches for dashboard files (client + board).
# These target patterns that appear in generated HTML but not in pipeline log text.
EVENING_HTML_PATCHES = [
    # Unit card timing labels in dashboards
    ("🌅 1× morning",                    "🌙 1× dinner"),
    ("🌅 2× morning",                    "🌙 2× dinner"),
    ("🌅 3× morning",                    "🌙 3× dinner"),
    # Board dashboard grouped header
    ("Morning Wellness Capsules",        "Dinner Wellness Capsules"),
    ("Morning Wellness Capsule",         "Dinner Wellness Capsule"),
    # Board dashboard delivery label lookup defaults
    ("🌅 Morning Capsules",              "🌙 Dinner Capsules"),
    ("🌅 Polyphenol Capsule",            "🌙 Polyphenol Capsule"),
    # Client dashboard header stats
    ('"Morning"',                         '"Dinner"'),
    ('>Morning<',                         '>Dinner<'),
    # Generic timing class (CSS border color stays, just label changes)
    ('class="unit-card morning"',         'class="unit-card evening"'),
]


def _patch_evening_labels(text: str) -> str:
    """Replace morning display labels with evening/dinner in pipeline log text.

    Applies all label patches from EVENING_LABEL_PATCHES in order.
    Idempotent: running twice produces the same result.
    """
    for old, new in EVENING_LABEL_PATCHES:
        text = text.replace(old, new)
    return text


# ─── MAIN PIPELINE FUNCTION ──────────────────────────────────────────────────

def generate_formulation(
    sample_dir: str,
    use_llm: bool = True,
    copy_to_sample: bool = True,
    force_keep: bool = False,
    compact: bool = False,
) -> Optional[Dict]:
    """Generate evening-override formulation for a sample.

    Runs the standard formulation pipeline silently, then patches all display
    output to use evening/dinner labels. The master JSON and all derivative
    files (platform, recipe, trace, dashboards) are already correct because
    the standard pipeline applies the timing override at Stage 8.5.

    Args:
        sample_dir: Path to sample directory
        use_llm: Whether to use Bedrock LLM (False for offline testing)
        copy_to_sample: Whether to copy output to sample's supplement_formulation dir
        force_keep: If True, high-severity interactions are flagged but NOT auto-removed
        compact: If True, suppress all pipeline detail — only show formulation summary

    Returns:
        Master formulation JSON dict (identical to standard pipeline output)
    """
    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name

    # ── Print banner ─────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  EVENING FORMULATION PIPELINE — {sample_id}")
    print(f"  Mode: {'LLM (Bedrock)' if use_llm else 'OFFLINE (no LLM)'}")
    print(f"  ⏰ Medication timing override active → all units to dinner")
    print(f"{'═'*60}\n")

    # ── Run standard pipeline silently ───────────────────────────────────
    # The standard pipeline produces correct JSON (Stage 8.5 applies the
    # override to the master dict). We suppress stdout during execution
    # so the wrong-label display doesn't reach the terminal.
    print("  Running standard pipeline (suppressed)...", flush=True)

    _original_stdout = sys.stdout
    _devnull = open(os.devnull, 'w')
    sys.stdout = _devnull

    master = None
    try:
        # Import here to avoid circular import at module level
        # _skip_evening_redirect=True prevents infinite recursion:
        # generate_formulation detects timing override → calls us → we call it back
        from generate_formulation import generate_formulation as _gen_standard
        master = _gen_standard(
            str(sample_dir),
            use_llm=use_llm,
            copy_to_sample=copy_to_sample,
            force_keep=force_keep,
            compact=compact,
            _skip_evening_redirect=True,
        )
    except Exception as exc:
        # Restore stdout before printing error
        sys.stdout = _original_stdout
        _devnull.close()
        print(f"\n  🚨 Evening pipeline failed: {exc}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Always restore stdout
        if sys.stdout != _original_stdout:
            sys.stdout = _original_stdout
        _devnull.close()

    if master is None:
        print(f"  ⚠️ Standard pipeline returned None for {sample_id} — no formulation generated")
        return None

    # ── Patch pipeline log with evening labels ───────────────────────────
    log_path = sample_dir / "reports" / f"pipeline_log_{sample_id}.txt"
    patched_content = ""
    if log_path.exists():
        raw_content = log_path.read_text(encoding='utf-8')
        patched_content = _patch_evening_labels(raw_content)
        log_path.write_text(patched_content, encoding='utf-8')
        print(f"  ✅ Pipeline log patched with evening labels: {log_path}")
    else:
        print(f"  ⚠️ Pipeline log not found at {log_path}")

    # ── Patch dashboard HTML files with evening labels ───────────────────
    # The standard pipeline generates dashboards from the post-override master
    # JSON, but generate_dashboards.py uses hardcoded "Morning" strings in
    # HTML templates and grouping logic. Patch those in-place.
    html_dir = sample_dir / "reports" / "reports_html"
    _html_patch_count = 0
    if html_dir.exists():
        for html_file in html_dir.glob("*.html"):
            try:
                html_content = html_file.read_text(encoding='utf-8')
                patched_html = html_content
                # Apply both general + HTML-specific patches
                for old, new in EVENING_LABEL_PATCHES + EVENING_HTML_PATCHES:
                    patched_html = patched_html.replace(old, new)
                if patched_html != html_content:
                    html_file.write_text(patched_html, encoding='utf-8')
                    _html_patch_count += 1
            except Exception as _html_err:
                print(f"  ⚠️ HTML patch failed for {html_file.name}: {_html_err}")
    if _html_patch_count > 0:
        print(f"  ✅ Patched {_html_patch_count} dashboard HTML file(s) with evening labels")

    # ── Print corrected output to terminal ───────────────────────────────
    if patched_content:
        print(patched_content)
    else:
        # Fallback: print summary from master JSON
        _print_evening_summary(master, sample_id)

    # ── Verify override was applied ──────────────────────────────────────
    med_rules = master.get("medication_rules", {})
    override = med_rules.get("timing_override")
    override_applied = med_rules.get("timing_override_applied", False)

    if override_applied:
        rule_id = override.get("rule_id", "?") if override else "?"
        medication = override.get("medication_normalized", "?") if override else "?"
        print(f"\n  ✅ EVENING OVERRIDE VERIFIED:")
        print(f"     Rule: {rule_id} ({medication.title()})")
        print(f"     Morning units: {master.get('formulation', {}).get('protocol_summary', {}).get('morning_solid_units', '?')}")
        print(f"     Evening units: {master.get('formulation', {}).get('protocol_summary', {}).get('evening_solid_units', '?')}")
    else:
        print(f"\n  🚨 WARNING: Timing override NOT applied in master JSON — check pipeline")

    return master


def _print_evening_summary(master: Dict, sample_id: str) -> None:
    """Print a condensed evening-aware summary from the master JSON.

    Fallback display when the full pipeline log is not available.
    """
    formulation = master.get("formulation", {})
    protocol = formulation.get("protocol_summary", {})
    med_rules = master.get("medication_rules", {})
    override = med_rules.get("timing_override", {})

    W = 70
    print(f"\n  DELIVERY ASSIGNMENTS (evening override):")
    print(f"  ┌" + "─" * W)

    # Jar
    jar = formulation.get("delivery_format_3_powder_jar", {})
    jar_g = jar.get("totals", {}).get("total_weight_g", 0)
    if jar:
        pb_parts = []
        for p in jar.get("prebiotics", {}).get("components", []):
            tag = "[*]" if p.get("fodmap") else ""
            pb_parts.append(f"{p['substance']} {p['dose_g']}g{tag}")
        bot_parts = []
        for b in jar.get("botanicals", {}).get("components", []):
            bot_parts.append(f"{b['substance']} {b['dose_g']}g")
        contents = " · ".join(pb_parts)
        if bot_parts:
            contents += " + " + " · ".join(bot_parts)
        print(f"  │ 🌙 Jar              {contents}   [{jar_g}g]")
        phased = jar.get("totals", {}).get("phased_dosing", {})
        if phased:
            print(f"  │                   ↑ weeks 1-2: {phased.get('weeks_1_2_g')}g/day → week 3+: {phased.get('weeks_3_plus_g')}g/day")
        if any(p.get("fodmap") for p in jar.get("prebiotics", {}).get("components", [])):
            print(f"  │                   [*]=FODMAP")

    # Probiotic capsule
    pc = formulation.get("delivery_format_1_probiotic_capsule", {})
    if pc:
        pc_totals = pc.get("totals", {})
        pc_mg = pc_totals.get("total_weight_mg", 0)
        pc_cfu = pc_totals.get("total_cfu_billions", 0)
        mix = master.get("decisions", {}).get("mix_selection", {})
        strain_parts = []
        for s in pc.get("components", []):
            genus = s.get("substance", "?").split(" ")[0]
            strain_parts.append(f"{genus} {s.get('cfu_billions', '?')}B")
        strains_str = " · ".join(strain_parts)
        print(f"  │ 🌙 Probiotic cap    Mix {mix.get('mix_id', '?')} · {strains_str} = {pc_cfu}B CFU   [{pc_mg}mg / 650mg]")

    # Softgels
    sg = formulation.get("delivery_format_2_omega_softgels", {})
    if sg:
        sg_count = sg.get("format", {}).get("daily_count", 2)
        print(f"  │ 🌙 Softgel ×{sg_count}       Omega-3 712.5mg · D3 10mcg · Vit E 7.5mg · Astaxanthin 3mg  (per softgel)")

    # Morning wellness capsules (now dinner)
    mwc = formulation.get("delivery_format_4_morning_wellness_capsules")
    if mwc:
        mwc_totals = mwc.get("totals", {})
        mwc_caps = mwc_totals.get("capsules", [])
        if len(mwc_caps) <= 1:
            contents = " · ".join(
                f"{c.get('substance', '?')} {c.get('dose', '?')}"
                for c in mwc.get("components", [])
            )
            fill = mwc_totals.get("total_weight_mg", 0)
            print(f"  │ 🌙 Dinner cap 1    {contents}   [{fill}mg / 650mg]")
        else:
            for cap in mwc_caps:
                cap_num = cap.get("capsule_number", "?")
                cap_contents = " · ".join(
                    f"{c.get('substance', '?')} {c.get('dose', '?')}"
                    for c in cap.get("components", [])
                )
                print(f"  │ 🌙 Dinner cap {cap_num}    {cap_contents}   [{cap['fill_mg']}mg / 650mg]")

    # Polyphenol capsule (also dinner now)
    pp = formulation.get("delivery_format_6_polyphenol_capsule")
    if pp:
        pp_contents = " · ".join(
            f"{c['substance']} {c['dose_mg']}mg"
            for c in pp.get("components", [])
        )
        pp_fill = pp.get("totals", {}).get("total_weight_mg", 0)
        print(f"  │ 🌙 Dinner cap       {pp_contents}   [{pp_fill}mg / 650mg]  ⚠ with food")

    # Evening capsules
    ewc = formulation.get("delivery_format_5_evening_wellness_capsules")
    if ewc:
        ewc_totals = ewc.get("totals", {})
        ewc_caps = ewc_totals.get("capsules", [])
        if len(ewc_caps) <= 1:
            contents = " · ".join(
                f"{c.get('substance', '?')} {c.get('dose_mg', '?')}mg"
                for c in ewc.get("components", [])
            )
            total_mg = ewc_totals.get("total_weight_mg", 0)
            print(f"  │ 🌙 Evening cap      {contents}   [{total_mg}mg / 650mg]")
        else:
            for cap in ewc_caps:
                cap_contents = " · ".join(
                    f"{c.get('substance', '?')} {c.get('dose_mg', '?')}mg"
                    for c in cap.get("components", [])
                )
                print(f"  │ 🌙 Evening cap {cap['capsule_number']}    {cap_contents}   [{cap['fill_mg']}mg / 650mg]")

    # Magnesium capsule
    mg = master.get("decisions", {}).get("rule_outputs", {}).get("magnesium", {})
    if mg.get("capsules", 0) > 0:
        print(f"  │ 🌙 Mg cap           Magnesium bisglycinate {mg['mg_bisglycinate_total_mg']}mg  ({mg['elemental_mg_total_mg']}mg elemental)")

    print(f"  └" + "─" * W)

    # Protocol summary
    print(f"\n  Protocol: {protocol.get('evening_solid_units', '?')} evening units, "
          f"0 morning units, total {protocol.get('total_daily_weight_g', '?')}g/day")


# ─── BATCH PROCESSING ────────────────────────────────────────────────────────

def process_batch(batch_dir: str, use_llm: bool = True, force_keep: bool = False):
    """Process all samples in a batch directory using the evening pipeline.

    Only samples with a medication timing override will differ from the
    standard pipeline. Samples without an override produce identical output
    (the standard pipeline is called directly for those).
    """
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        print(f"❌ Batch directory not found: {batch_dir}")
        return

    samples = sorted([
        d for d in batch_dir.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 13
    ])

    print(f"\nEvening Batch: {batch_dir.name} — {len(samples)} samples")

    results = {}
    for sample_dir in samples:
        try:
            result = generate_formulation(str(sample_dir), use_llm=use_llm, force_keep=force_keep)
            if result is None:
                results[sample_dir.name] = "SKIPPED"
            else:
                results[sample_dir.name] = result.get("metadata", {}).get("validation_status", "?")
        except Exception as e:
            print(f"\n❌ FAILED: {sample_dir.name} — {e}")
            results[sample_dir.name] = f"ERROR: {e}"

    # Summary
    print(f"\n{'='*60}")
    print(f"  EVENING BATCH SUMMARY — {batch_dir.name}")
    print(f"{'='*60}")
    for sample_id, status in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {sample_id}: {status}")
    print()


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate evening-override supplement formulation "
                    "(all units moved to dinner for medication spacing)"
    )
    parser.add_argument("--sample-dir", help="Path to single sample directory")
    parser.add_argument("--batch-dir", help="Path to batch directory (process all samples)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls (offline mode)")
    parser.add_argument("--force-keep", action="store_true",
                        help="Keep supplements even if high-severity interactions detected")
    parser.add_argument("--compact", action="store_true",
                        help="Compact output: only show formulation summary")
    args = parser.parse_args()

    if not args.sample_dir and not args.batch_dir:
        parser.error("Provide --sample-dir or --batch-dir")

    if args.sample_dir:
        generate_formulation(args.sample_dir, use_llm=not args.no_llm,
                             force_keep=args.force_keep, compact=args.compact)
    elif args.batch_dir:
        process_batch(args.batch_dir, use_llm=not args.no_llm, force_keep=args.force_keep)
