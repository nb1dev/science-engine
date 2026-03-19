#!/usr/bin/env python3
"""
Stage 2: Clinical Questionnaire Analysis (LLM).

Input:  PipelineContext with unified_input
Output: PipelineContext with clinical_summary populated
"""

from ..models import PipelineContext
from ..llm.clinical_analyzer import analyze_questionnaire_clinical


def run(ctx: PipelineContext) -> PipelineContext:
    """Run LLM clinical analysis of questionnaire."""
    print("\n─── A.5 CLINICAL PROFILE ───────────────────────────────────")

    if ctx.use_llm:
        try:
            print("  🧠 LLM: Analysing clinical questionnaire profile...")
            ctx.clinical_summary = analyze_questionnaire_clinical(ctx.unified_input, use_bedrock=ctx.use_llm)
        except Exception as e:
            print(f"  ⚠️ Clinical analysis failed: {e}")

    # Print profile narrative
    if ctx.clinical_summary.get("profile_narrative"):
        print(f"  CLIENT PROFILE:")
        for bullet in ctx.clinical_summary["profile_narrative"]:
            print(f"    {bullet}")

    # Print inferred signals
    inferred = ctx.clinical_summary.get("inferred_health_signals", [])
    if inferred:
        display = [s.get("signal", s) if isinstance(s, dict) else s for s in inferred]
        print(f"  Inferred health signals: {display}")

    # Print clinical review flags
    flags = ctx.clinical_summary.get("clinical_review_flags", [])
    if flags:
        print(f"\n  ┌─ 🚨 CLINICAL REVIEW REQUIRED")
        for flag in flags:
            sev = flag.get("severity", "medium").upper()
            icon = "🔴" if sev == "HIGH" else "🟡"
            print(f"  │ {icon} [{sev}] {flag.get('title', '?')}")
        print(f"  └{'─' * 70}\n")

    return ctx
