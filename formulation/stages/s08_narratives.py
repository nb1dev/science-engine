#!/usr/bin/env python3
"""
Stage 8: LLM Narratives — Ecological rationale + input narratives.

Input:  PipelineContext with mix, formulation
Output: PipelineContext with ecological_rationale, input_narratives populated
"""

import json
from pathlib import Path

from ..models import PipelineContext
from ..llm.narrative_generator import generate_ecological_rationale, generate_input_narratives

KB_DIR = Path(__file__).parent.parent / "knowledge_base"


def run(ctx: PipelineContext) -> PipelineContext:
    """Generate LLM narratives (ecological rationale + input summaries)."""
    print("\n─── F. NARRATIVES ──────────────────────────────────────────")

    if ctx.use_llm:
        try:
            if ctx.mix.get("alternative_considered"):
                print("  🧠 Opus: Generating ecological rationale...")
                ctx.ecological_rationale = generate_ecological_rationale(ctx.unified_input, ctx.mix)
            print("  🧠 Opus: Generating input narratives...")
            ctx.input_narratives = generate_input_narratives(ctx.unified_input, ctx.rule_outputs)
        except Exception as e:
            print(f"  ⚠️ Narrative generation failed: {e}")

    # KB fallback for ecological rationale
    if not ctx.ecological_rationale or not ctx.ecological_rationale.get("selected_rationale"):
        try:
            with open(KB_DIR / "synbiotic_mixes.json", 'r', encoding='utf-8') as f:
                mixes_kb = json.load(f)
            mix_kb = mixes_kb.get("mixes", {}).get(str(ctx.mix.get("mix_id")), {})
            eco_kb = mix_kb.get("ecological_rationale", {})
            if eco_kb:
                ctx.ecological_rationale = {
                    "selected_rationale": eco_kb.get("scientific", ""),
                    "alternative_analysis": f"Alternative considered: {ctx.mix.get('alternative_considered', 'None')}",
                    "combined_assessment": "",
                    "recommendation": eco_kb.get("client_friendly", ""),
                    "source": "knowledge_base (deterministic)",
                }
                print(f"  📋 Ecological rationale: KB fallback for Mix {ctx.mix.get('mix_id')}")
        except Exception as e:
            print(f"  ⚠️ KB fallback failed: {e}")

    return ctx
