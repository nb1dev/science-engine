#!/usr/bin/env python3
"""
Stage 1: Parse Inputs — Load and unify microbiome + questionnaire data.

Input:  sample_dir path
Output: PipelineContext with unified_input populated

Delegates to parse_inputs.py (already clean, no changes needed).
"""

from pathlib import Path
from typing import Optional

from ..models import PipelineContext
from formulation.parse_inputs import parse_inputs


def run(sample_dir: str, use_llm: bool = True, compact: bool = False) -> Optional[PipelineContext]:
    """Parse all inputs and create initial PipelineContext.

    Args:
        sample_dir: Path to sample directory.
        use_llm: Whether to use Bedrock LLM.
        compact: Suppress verbose pipeline output.

    Returns:
        PipelineContext with unified_input, or None if questionnaire missing.
    """
    sample_dir = Path(sample_dir)
    sample_id = sample_dir.name

    print(f"\n{'═'*60}")
    print(f"  FORMULATION PIPELINE — {sample_id}")
    print(f"  Mode: {'LLM (Bedrock)' if use_llm else 'OFFLINE (no LLM)'}")
    print(f"{'═'*60}\n")

    print("─── A. INPUTS ──────────────────────────────────────────────")
    unified_input = parse_inputs(str(sample_dir))

    # Questionnaire validation guard
    q = unified_input['questionnaire']
    q_completion = q.get('completion', {}).get('completion_pct', 0)
    if q_completion == 0:
        print(f"\n  ⚠️  QUESTIONNAIRE REQUIRED — No data available for {sample_id}")
        print(f"  This sample cannot be processed without a completed questionnaire.\n")
        return None

    # Microbiome data missing guard
    guilds = unified_input['microbiome']['guilds']
    if not guilds:
        print(f"\n  🚨 WARNING: NO MICROBIOME GUILD DATA for {sample_id}")
        print(f"  Mix selection will DEFAULT to Mix 6 (Maintenance) — review required.\n")

    ctx = PipelineContext(
        sample_id=sample_id,
        sample_dir=str(sample_dir),
        batch_id=unified_input.get("batch_id", sample_dir.parent.name),
        use_llm=use_llm,
        compact=compact,
        unified_input=unified_input,
    )

    # Print summary
    clr = ctx.clr
    print(f"  Sample: {ctx.sample_id} | Batch: {ctx.batch_id} | Q: {q_completion:.0f}%")
    print(f"  CLR: CUR={clr.get('CUR')}  FCR={clr.get('FCR')}  MDR={clr.get('MDR')}  PPR={clr.get('PPR')}")

    return ctx
