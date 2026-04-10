#!/usr/bin/env python3
"""
Recipe Renderer — Generate manufacturing recipe MD and PDF from recipe JSON.

Standalone module that converts a manufacturing_recipe_{sample_id}.json into:
  - reports/reports_md/manufacturing_recipe_{sample_id}.md   (Markdown)
  - reports/reports_pdf/manufacturing_recipe_{sample_id}.pdf (PDF via pandoc)

Can be called:
  1. From the pipeline (s09_output.py) after JSON is saved — no extra args needed
  2. Standalone to regenerate MD/PDF from any existing recipe JSON (no LLM, no pipeline)

Usage (standalone):
    python recipe_renderer.py analysis/nb1_2026_004/1421012391191

Usage (from code):
    from formulation.recipe_renderer import render_and_save
    render_and_save(recipe_dict, sample_id, sample_dir, q_coverage=None)
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


# ─── MARKDOWN GENERATOR ───────────────────────────────────────────────────────

def generate_md(recipe: Dict, sample_id: str, q_coverage: Optional[Dict] = None) -> str:
    """Convert a manufacturing_recipe dict to a human-readable Markdown string.

    Args:
        recipe:    manufacturing_recipe_{sample_id}.json dict
        sample_id: Sample ID string (for header)
        q_coverage: Optional questionnaire coverage dict (for low-coverage warning block).
                    Pass None or omit to skip the warning.

    Returns:
        Full Markdown string ready to write to .md file.
    """
    lines = []

    # ── Header ───────────────────────────────────────────────────────────
    lines.append("# Manufacturing Recipe")
    lines.append("")
    lines.append(f"**Client Code:** {sample_id}  ")
    lines.append(f"**Date:** {datetime.now().strftime('%B %d, %Y')}  ")
    lines.append(f"**Protocol:** {recipe.get('protocol_summary', 'N/A')}  ")
    lines.append(f"**Duration:** {recipe.get('protocol_duration_weeks', 16)} weeks  ")
    lines.append(f"**Validation:** {recipe.get('validation', 'N/A')}")

    # ── Questionnaire coverage warning (optional) ─────────────────────────
    if q_coverage and q_coverage.get("coverage_level") in ("MINIMAL", "LOW"):
        lines.append("")
        lines.append(f"> ⚠️ **LIMITED QUESTIONNAIRE DATA** ({q_coverage['coverage_level']})")
        lines.append(f"> {q_coverage.get('summary', '')}")
        for area in q_coverage.get("missing_data_areas", []):
            lines.append(f"> - {area}")
        if q_coverage.get("recommendation"):
            lines.append(f"> **Recommendation:** {q_coverage['recommendation']}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Delivery units ────────────────────────────────────────────────────
    for unit in recipe.get("units", []):
        unit_num = unit.get("unit_number", "?")
        label = unit.get("label", "Unknown")
        fmt = unit.get("format", {})
        timing = unit.get("timing", "morning")
        qty = unit.get("quantity", 1)

        lines.append(f"## Unit {unit_num}: {label}")
        lines.append("")

        # Format line
        fmt_type = fmt.get("type", "").replace("_", " ").title()
        fmt_size = fmt.get("size", "")
        fmt_material = fmt.get("material", "")
        if fmt_type:
            lines.append(
                f"**Format:** {f'Size {fmt_size} ' if fmt_size else ''}{fmt_material} {fmt_type}".strip()
            )

        # Fill weight
        fill_mg = unit.get("fill_weight_mg") or unit.get("fill_weight_per_capsule_mg") or unit.get("fill_weight_per_unit_mg")
        fill_g = unit.get("fill_weight_g") or unit.get("fill_weight_per_unit_g")
        if fill_mg:
            lines.append(f"**Fill Weight:** {fill_mg}mg")
        elif fill_g:
            lines.append(f"**Fill Weight:** {fill_g}g")

        lines.append(f"**Timing:** {qty}× {timing}")

        dosing_instr = unit.get("dosing_instruction")
        if dosing_instr:
            lines.append(f"**Dosing:** {dosing_instr}")

        if unit.get("timing_note"):
            lines.append(f"**Note:** {unit['timing_note']}")

        if unit.get("storage"):
            lines.append(f"**Storage:** {unit['storage']}")

        if unit.get("note"):
            lines.append(f"**Note:** {unit['note']}")

        lines.append("")

        # ── Ingredients table ─────────────────────────────────────────────
        ingredients = unit.get("ingredients") or unit.get("ingredients_per_unit") or []

        capsule_layout = unit.get("capsule_layout", [])

        if ingredients:
            has_cfu = any("cfu_billions" in ing for ing in ingredients)

            if capsule_layout and qty > 1:
                lines.append(f"*Total across all {qty} capsules:*")
                lines.append("")

            header = "| Component | Amount |"
            separator = "|-----------|--------|"
            if has_cfu:
                header += " CFU |"
                separator += "-----|"

            lines.append(header)
            lines.append(separator)

            for ing in ingredients:
                component = ing.get("component", "?")

                # Resolve amount
                amount_g = ing.get("amount_g")
                amount_mg = ing.get("amount_mg")
                amount_str_raw = ing.get("amount", "")
                dose_per = ing.get("dose_per_softgel", "")
                weight_note = ing.get("weight_note", "")

                if weight_note == "negligible" and amount_str_raw:
                    amount_str = str(amount_str_raw)
                elif amount_g is not None:
                    amount_str = f"{amount_g}g"
                elif amount_mg is not None:
                    amount_str = f"{amount_mg}mg"
                elif amount_str_raw:
                    amount_str = str(amount_str_raw)
                elif dose_per:
                    amount_str = str(dose_per)
                else:
                    amount_str = "—"

                row = f"| {component} | {amount_str} |"
                if has_cfu:
                    cfu = ing.get("cfu_billions")
                    row += f" {cfu}B CFU |" if cfu else " — |"
                lines.append(row)

        # ── Per-capsule breakdown (multi-capsule units only) ──────────────
        if capsule_layout and qty > 1:
            lines.append("")
            lines.append(f"*Per-capsule breakdown ({qty} capsules):*")
            lines.append("")
            for cap in capsule_layout:
                cap_num = cap.get("capsule_number", "?")
                fill = cap.get("fill_mg", 0)
                util = cap.get("utilization_pct", 0)
                lines.append(f"**Capsule {cap_num} of {qty}** — {fill}mg ({util}% capacity)")
                lines.append("")
                lines.append("| Component | Dose per capsule |")
                lines.append("|-----------|-----------------|")
                for comp in cap.get("components", []):
                    substance = comp.get("substance", "?")
                    dose_str = comp.get("dose", "") or f"{comp.get('dose_mg', comp.get('weight_mg', 0))}mg"
                    lines.append(f"| {substance} | {dose_str} |")
                lines.append("")

        lines.append("")

        # ── Unit total ────────────────────────────────────────────────────
        total_mg = unit.get("total_weight_mg")
        total_g = unit.get("total_weight_g")
        total_cfu = unit.get("total_cfu_billions")

        if total_mg is not None:
            cfu_str = f", {total_cfu}B CFU" if total_cfu else ""
            lines.append(f"**Unit {unit_num} Total:** {total_mg}mg{cfu_str}")
        elif total_g is not None:
            lines.append(f"**Unit {unit_num} Total:** {total_g}g")

        # Phased dosing (jar units)
        phased = unit.get("phased_dosing", {})
        if phased and phased.get("instruction"):
            lines.append("")
            lines.append(f"**Phased Dosing:** {phased['instruction']}")

        # Daily totals (softgel/Mg)
        daily = unit.get("daily_totals", {})
        if daily:
            lines.append("")
            lines.append("**Daily Totals:**")
            DAILY_TOTAL_LABELS = {
                "fish_oil_mg": "Fish Oil mg",
                "omega3_mg": "Omega-3 (EPA+DHA) mg",
                "vitamin_d_mcg": "Vitamin D3 mcg",
                "vitamin_e_mg": "Vitamin E mg",
                "astaxanthin_mg": "Astaxanthin (active) mg",
                "mg_bisglycinate_mg": "Magnesium bisglycinate mg",
                "elemental_mg_mg": "Elemental Magnesium mg",
            }
            for k, v in daily.items():
                key_display = DAILY_TOTAL_LABELS.get(k, k.replace("_", " "))
                lines.append(f"- {key_display}: {v}")

        lines.append("")
        lines.append("---")
        lines.append("")

    # ── Grand total ───────────────────────────────────────────────────────
    grand = recipe.get("grand_total", {})
    if grand:
        total_units = grand.get("total_units", "?")
        morning_units = grand.get("morning_units", "?")
        evening_units = grand.get("evening_units", "?")
        total_weight = grand.get("total_daily_weight_g", "?")

        lines.append("## Grand Total (Daily)")
        lines.append("")
        lines.append(f"- **Total units/day:** {total_units} ({morning_units} morning + {evening_units} evening)")
        lines.append(f"- **Total daily weight:** {total_weight}g")

        dosing = grand.get("dosing_summary", {})
        morning_parts = dosing.get("morning", []) if isinstance(dosing, dict) else []
        morning_jar   = dosing.get("morning_jar") if isinstance(dosing, dict) else None
        evening_parts = dosing.get("evening", []) if isinstance(dosing, dict) else []

        if morning_parts or morning_jar:
            jar_note = " + powder serving" if morning_jar else ""
            lines.append("")
            lines.append(f"**Morning ({morning_units} units{jar_note}):**")
            for part in morning_parts:
                lines.append("")
                lines.append(f"- {part}")
            if morning_jar:
                qty = morning_jar.get("qty", 1)
                label_short = morning_jar.get("label_short", "Prebiotic & Botanical")
                lines.append(f"- {qty}\u00d7 {label_short}")
                phased = morning_jar.get("phased_dosing", {})
                if phased and phased.get("weeks_1_2_g") and phased.get("weeks_3_plus_g"):
                    lines.append(f"- Powder Jar serving (weeks 1-2: {phased['weeks_1_2_g']}g -> week 3+: {phased['weeks_3_plus_g']}g)")
                else:
                    total_g = morning_jar.get("total_weight_g", "")
                    lines.append(f"- Powder Jar serving ({total_g}g)" if total_g else "- Powder Jar serving")

        if evening_parts:
            lines.append("")
            lines.append(f"**Evening ({evening_units} units):**")
            for part in evening_parts:
                lines.append("")
                lines.append(f"- {part}")

        lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Pipeline v3.0*")

    return "\n".join(lines)


# ─── PDF CONVERTER ────────────────────────────────────────────────────────────

def markdown_to_pdf(md_path: str, pdf_path: str) -> bool:
    """Convert a Markdown file to PDF using pandoc.

    Tries xelatex first (best quality), falls back to default PDF engine.
    Returns True if PDF was created, False otherwise (e.g. pandoc not installed).

    Args:
        md_path:  Absolute path to source .md file
        pdf_path: Absolute path to output .pdf file
    """
    pandoc_path = shutil.which("pandoc")
    if not pandoc_path:
        print(f"  ⚠️ PDF generation skipped — pandoc not found. Install with: brew install pandoc")
        return False

    # Ensure output directory exists
    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)

    # Attempt 1: xelatex (best font support)
    try:
        result = subprocess.run(
            [
                pandoc_path, md_path, "-o", pdf_path,
                "--pdf-engine=xelatex",
                "-V", "geometry:margin=2cm",
                "-V", "fontsize=11pt",
                "-V", "mainfont=Helvetica Neue",
                "-V", "monofont=Courier New",
                "--highlight-style=tango",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, Exception):
        pass

    # Attempt 2: default engine (no xelatex dependency)
    try:
        result2 = subprocess.run(
            [pandoc_path, md_path, "-o", pdf_path, "-V", "geometry:margin=2cm"],
            capture_output=True, text=True, timeout=60,
        )
        if result2.returncode == 0:
            return True
        print(f"  ⚠️ PDF generation failed: {result2.stderr[:200]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ PDF generation timed out")
        return False
    except Exception as e:
        print(f"  ⚠️ PDF generation failed: {e}")
        return False


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def render_and_save(
    recipe: Dict,
    sample_id: str,
    sample_dir: str,
    q_coverage: Optional[Dict] = None,
) -> Dict[str, Optional[str]]:
    """Render manufacturing recipe to MD + PDF and save to reports directories.

    This is the primary function called by the pipeline (s09_output.py) and by
    standalone regeneration scripts. It reads only from the passed recipe dict —
    no LLM calls, no pipeline state, no imports from other pipeline stages.

    Args:
        recipe:     manufacturing_recipe dict (already built by build_manufacturing_recipe())
        sample_id:  Sample ID string
        sample_dir: Path to sample root directory (e.g. analysis/nb1_2026_004/1421012391191)
        q_coverage: Optional questionnaire coverage dict for low-coverage warning block.

    Returns:
        dict with keys:
            "md_path":  str path to written .md file (or None on error)
            "pdf_path": str path to written .pdf file (or None if pandoc unavailable)
    """
    sample_path = Path(sample_dir)
    md_dir = sample_path / "reports" / "reports_md"
    pdf_dir = sample_path / "reports" / "reports_pdf"
    md_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    md_path = md_dir / f"manufacturing_recipe_{sample_id}.md"
    pdf_path = pdf_dir / f"manufacturing_recipe_{sample_id}.pdf"

    # Generate Markdown
    md_content = generate_md(recipe, sample_id, q_coverage)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  📄 Recipe MD:  {md_path.relative_to(sample_path.parent.parent)}")

    # Generate PDF
    pdf_written = markdown_to_pdf(str(md_path), str(pdf_path))
    if pdf_written:
        print(f"  📄 Recipe PDF: {pdf_path.relative_to(sample_path.parent.parent)}")

    return {
        "md_path": str(md_path),
        "pdf_path": str(pdf_path) if pdf_written else None,
    }


def render_from_sample_dir(sample_dir: str) -> Dict[str, Optional[str]]:
    """Convenience function: load recipe JSON from disk and render to MD + PDF.

    Useful for standalone regeneration — loads
    reports/reports_json/manufacturing_recipe_{sample_id}.json
    and optionally formulation_master_{sample_id}.json for q_coverage.

    Args:
        sample_dir: Path to sample root directory

    Returns:
        Same dict as render_and_save()
    """
    sample_path = Path(sample_dir)
    sample_id = sample_path.name
    json_dir = sample_path / "reports" / "reports_json"

    recipe_path = json_dir / f"manufacturing_recipe_{sample_id}.json"
    if not recipe_path.exists():
        print(f"  ❌ Recipe JSON not found: {recipe_path}")
        return {"md_path": None, "pdf_path": None}

    with open(recipe_path, encoding="utf-8") as f:
        recipe = json.load(f)

    # Try to load q_coverage from master JSON (optional — doesn't fail if absent)
    q_coverage = None
    master_path = json_dir / f"formulation_master_{sample_id}.json"
    if master_path.exists():
        try:
            with open(master_path, encoding="utf-8") as f:
                master = json.load(f)
            q_coverage = master.get("questionnaire_coverage")
        except Exception:
            pass  # q_coverage is optional — continue without it

    return render_and_save(recipe, sample_id, sample_dir, q_coverage)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python recipe_renderer.py <sample_dir> [<sample_dir2> ...]")
        print("  e.g. python recipe_renderer.py analysis/nb1_2026_004/1421012391191")
        sys.exit(1)

    results = []
    for sample_dir_arg in sys.argv[1:]:
        print(f"\n── {Path(sample_dir_arg).name} ─────────────────────────────────")
        result = render_from_sample_dir(sample_dir_arg)
        results.append((sample_dir_arg, result))

    print(f"\n{'='*60}")
    print(f"  RECIPE RENDERER SUMMARY")
    print(f"{'='*60}")
    for sd, res in results:
        sid = Path(sd).name
        md_ok = "✅" if res.get("md_path") else "❌"
        pdf_ok = "✅" if res.get("pdf_path") else "⚠️ (pandoc unavailable)"
        print(f"  {sid}:  MD {md_ok}  PDF {pdf_ok}")
