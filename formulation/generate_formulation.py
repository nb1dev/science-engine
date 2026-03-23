#!/usr/bin/env python3
"""
Formulation Pipeline Orchestrator (v4.0 — Modular Architecture)

Thin orchestrator that chains 9 pipeline stages, each with explicit inputs/outputs.
Each stage receives a PipelineContext and returns it with its outputs populated.

The original monolithic generate_formulation.py (1900 lines in one function) is
archived at archive/monolith_20260319/ and remains functional as a fallback.

Usage:
    # Single sample (with LLM)
    python generate_formulation.py --sample-dir /path/to/analysis/batch/sample/

    # Single sample (no LLM — offline mode)
    python generate_formulation.py --sample-dir /path/to/sample/ --no-llm

    # Process entire batch
    python generate_formulation.py --batch-dir /path/to/analysis/batch/

    python generate_formulation.py --sample-dir /path/to/sample/

Architecture:
    generate_formulation.py (this file) — 80 lines
    models.py              — PipelineContext, RemovalLog dataclasses
    stages/s01-s09         — 9 pipeline stages
    filters/               — Post-processing filters (Stage D)
    llm/                   — All LLM interactions
    builders/              — Output assemblers (platform, trace, recipe, dashboards)
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

# Ensure science-engine/ is on path so formulation is importable as a package
# (stages use relative imports like "from ..models import PipelineContext")
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from formulation.stages import s01_parse_inputs, s02_clinical_analysis, s03_medication_screening
from formulation.stages import s04_deterministic_rules, s05_formulation_decisions, s06_post_processing
from formulation.stages import s07_weight_calculation, s08_narratives, s09_output


def generate_formulation(
    sample_dir: str,
    use_llm: bool = True,
    compact: bool = False,
) -> Optional[Dict]:
    """Generate complete formulation for a sample.

    This is the main entry point. It chains 9 stages:
      1. Parse inputs (microbiome + questionnaire)
      2. Clinical analysis (LLM questionnaire review)
      3. Medication screening (LLM + KB rules)
      4. Deterministic rules (sensitivity, health claims, timing)
      5. Formulation decisions (mix + supplements + prebiotics)
      6. Post-processing (filters: exclusions, gates, routing)
      7. Weight calculation (FormulationCalculator + validation)
      8. Narratives (LLM ecological rationale + input summaries)
      9. Output (master JSON + platform + trace + recipe + dashboards)

    Args:
        sample_dir: Path to sample directory
        use_llm: Whether to use Bedrock LLM (False for offline testing)
        compact: If True, suppress pipeline detail

    Returns:
        Master formulation JSON dict, or None if sample cannot be processed
    """
    # Force line-buffered stdout
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    # ── Stage 1: Parse Inputs ────────────────────────────────────────────
    ctx = s01_parse_inputs.run(sample_dir, use_llm=use_llm, compact=compact)
    if ctx is None:
        return None  # Questionnaire guard — exit gracefully

    # ── Stage 2: Clinical Analysis (LLM) ─────────────────────────────────
    ctx = s02_clinical_analysis.run(ctx)

    # ── Stage 3: Medication Screening (LLM + KB) ────────────────────────
    ctx = s03_medication_screening.run(ctx)

    # ── Stage 4: Deterministic Rules ─────────────────────────────────────
    ctx = s04_deterministic_rules.run(ctx)

    # ── Stage 5: Formulation Decisions (mix + supplements + prebiotics) ──
    ctx = s05_formulation_decisions.run(ctx)

    # ── Stage 6: Post-Processing (all D-stage filters) ──────────────────
    ctx = s06_post_processing.run(ctx)

    # ── Stage 7: Weight Calculation + Validation ─────────────────────────
    ctx = s07_weight_calculation.run(ctx)

    # ── Stage 8: LLM Narratives ──────────────────────────────────────────
    ctx = s08_narratives.run(ctx)

    # ── Stage 9: Output (assemble master + save all files) ───────────────
    master = s09_output.run(ctx)

    return master


def process_batch(batch_dir: str, use_llm: bool = True):
    """Process all samples in a batch directory."""
    import traceback as _tb

    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        print(f"❌ Batch directory not found: {batch_dir}")
        return

    samples = sorted([
        d for d in batch_dir.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 13
    ])

    print(f"\nBatch: {batch_dir.name} — {len(samples)} samples")

    error_log_path = batch_dir / "formulation_batch_errors.log"
    results = {}
    for sample_dir in samples:
        try:
            result = generate_formulation(str(sample_dir), use_llm=use_llm)
            if result is None:
                results[sample_dir.name] = "SKIPPED (No questionnaire)"
            else:
                results[sample_dir.name] = result["metadata"]["validation_status"]
        except Exception as e:
            print(f"\n❌ FAILED: {sample_dir.name} — {e}")
            results[sample_dir.name] = f"ERROR: {e}"
            # Log full traceback to file for diagnosis
            with open(error_log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"SAMPLE: {sample_dir.name}\n")
                f.write(f"TIME: {datetime.now().isoformat()}\n")
                f.write(f"{'='*60}\n")
                _tb.print_exc(file=f)

    if error_log_path.exists():
        print(f"\n  📋 Error details: {error_log_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  BATCH SUMMARY — {batch_dir.name}")
    print(f"{'='*60}")
    for sample_id, status in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {sample_id}: {status}")
    print()


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate supplement formulation (modular pipeline)")
    parser.add_argument("--sample-dir", help="Path to single sample directory")
    parser.add_argument("--batch-dir", help="Path to batch directory (process all samples)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls (offline mode)")
    parser.add_argument("--compact", action="store_true", help="Compact output: suppress pipeline detail")
    args = parser.parse_args()

    if not args.sample_dir and not args.batch_dir:
        parser.error("Provide --sample-dir or --batch-dir")

    if args.sample_dir:
        generate_formulation(args.sample_dir, use_llm=not args.no_llm,
                             compact=args.compact)
    elif args.batch_dir:
        process_batch(args.batch_dir, use_llm=not args.no_llm)
