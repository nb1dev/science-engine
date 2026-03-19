#!/usr/bin/env python3
"""
Stage 5: Formulation Decisions — Mix + Supplements + Prebiotics.

Input:  PipelineContext with rule_outputs
Output: PipelineContext with mix, supplements, prebiotics populated

Architecture: Mix = ALWAYS deterministic | Supplements + Prebiotics = LLM or offline

All imports use the modular llm/ package — NO monolith dependency.
"""

from pathlib import Path
from typing import Dict

from ..models import PipelineContext
from ..llm.mix_selector import select_mix_offline, lookup_strains_for_mix
from ..llm.supplement_selector import select_supplements
from ..llm.prebiotic_designer import design_prebiotics, design_prebiotics_offline
from formulation.weight_calculator import distribute_cfu_evenly
from formulation.rules_engine import calculate_prebiotic_range


def run(ctx: PipelineContext) -> PipelineContext:
    """Run formulation decisions: deterministic mix + LLM supplements/prebiotics."""
    print(f"\n─── C. DECISIONS (mix=deterministic, supplements={'LLM' if ctx.use_llm else 'offline'}) ───")

    results = _run_formulation_decisions(ctx.unified_input, ctx.rule_outputs, use_bedrock=ctx.use_llm)

    ctx.mix = results["mix_selection"]
    ctx.supplements = results["supplement_selection"]
    ctx.prebiotics = results["prebiotic_design"]

    # Print mix summary
    mix = ctx.mix
    print(f"\n  ┌─ PROBIOTIC MIX ──────────────────────────────────────────")
    print(f"  │ Mix {mix.get('mix_id')}: {mix.get('mix_name')}")
    print(f"  │ Trigger: {mix.get('primary_trigger')}")
    print(f"  │ Confidence: {mix.get('confidence', '?')}")
    total_cfu = mix.get('total_cfu_billions', sum(s.get('cfu_billions', 0) for s in mix.get('strains', [])))
    print(f"  │ Total: {total_cfu}B CFU")
    print(f"  └────────────────────────────────────────────────────────")

    # Print supplement/prebiotic counts
    n_vm = len(ctx.supplements.get('vitamins_minerals', []))
    n_sp = len(ctx.supplements.get('supplements', []))
    print(f"\n  Vitamins [{n_vm}] · Supplements [{n_sp}] · Prebiotics [{ctx.prebiotics.get('total_grams', 0)}g]")

    ctx.add_trace("initial_selection", f"Mix {mix.get('mix_id')}",
                   f"Mix {mix.get('mix_id')} ({mix.get('mix_name')}) — {mix.get('primary_trigger', '?')}")

    return ctx


def _run_formulation_decisions(
    unified_input: Dict,
    rule_outputs: Dict,
    use_bedrock: bool = True,
) -> Dict:
    """
    Orchestrate formulation decisions: deterministic mix + 2 LLM calls (or offline fallback).

    Architecture:
      - Mix selection: ALWAYS deterministic (LLMs unreliable with numerical thresholds)
      - Strain lookup: ALWAYS from synbiotic_mixes.json knowledge base
      - Supplement selection: LLM (qualitative clinical judgment) or offline skeleton
      - Prebiotic design: LLM (customization judgment) or offline mix-aware formula

    Returns: {mix_selection, supplement_selection, prebiotic_design}
    """
    # Mix selection: ALWAYS deterministic (never LLM)
    print("  📋 Mix selection (deterministic rules — never LLM)...")
    mix_result = select_mix_offline(unified_input, rule_outputs)

    # Look up canonical strains from knowledge base
    if mix_result.get("mix_id"):
        kb_strains = lookup_strains_for_mix(mix_result["mix_id"])
        if kb_strains:
            # Merge KB strains with any LP815 already added
            existing_lp815 = [s for s in mix_result.get("strains", []) if "LP815" in s.get("name", "")]

            # Assign cfu_billions to KB strains (KB doesn't store CFU — distribute base 50B evenly)
            base_cfu = 50
            cfu_per_strain = distribute_cfu_evenly(base_cfu, len(kb_strains))
            for strain in kb_strains:
                if "cfu_billions" not in strain:
                    strain["cfu_billions"] = cfu_per_strain

            mix_result["strains"] = kb_strains + existing_lp815
            _lp815_label = f" + LP815 {existing_lp815[0]['cfu_billions']}B (psychobiotic)" if existing_lp815 else ""
            print(f"    Strains: {len(kb_strains)} base ({cfu_per_strain}B each){_lp815_label}")

    # Update prebiotic range with actual mix CFU
    prebiotic_range = calculate_prebiotic_range(
        rule_outputs["sensitivity"],
        cfu_billions=mix_result.get("total_cfu_billions", 50),
        mix_id=mix_result.get("mix_id")
    )
    rule_outputs["prebiotic_range"] = prebiotic_range

    # LLM Call 1 (of 2): Supplement selection
    if use_bedrock:
        print("  🧠 LLM Call 1/2: Supplement selection...")
        supplement_result = select_supplements(unified_input, rule_outputs)
    else:
        print("  📋 Offline: Supplement selection (skeleton)...")
        supplement_result = {
            "vitamins_minerals": [],
            "supplements": [],
            "omega3": {"dose_daily_mg": 1425, "dose_per_softgel_mg": 712.5, "rationale": "Default omega-3"},
            "existing_supplements_advice": [],
        }

    # LLM Call 2 (of 2): Prebiotic design
    if use_bedrock:
        print("  🧠 LLM Call 2/2: Prebiotic design...")
        prebiotic_result = design_prebiotics(unified_input, rule_outputs, mix_result)
    else:
        print("  📋 Offline: Prebiotic design (mix-aware)...")
        prebiotic_result = design_prebiotics_offline(unified_input, rule_outputs, mix_result)

    return {
        "mix_selection": mix_result,
        "supplement_selection": supplement_result,
        "prebiotic_design": prebiotic_result,
    }
