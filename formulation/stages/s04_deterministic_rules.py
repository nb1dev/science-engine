#!/usr/bin/env python3
"""
Stage 4: Deterministic Rules — Apply all threshold-based rules from knowledge_base.

Input:  PipelineContext with unified_input + clinical_summary
Output: PipelineContext with rule_outputs + effective_goals populated

Delegates to rules_engine.py (already clean).
"""

from pathlib import Path

from ..models import PipelineContext
from formulation.rules_engine import apply_rules, apply_timing_rules

# Inferred signal → goal key mapping (for timing engine)
INFERRED_SIGNAL_TO_GOAL = {
    "stress_anxiety": "reduce_stress_anxiety",
    "sleep_quality": "improve_sleep_quality",
    "fatigue": "boost_energy_reduce_fatigue",
    "bowel_function": "improve_digestion_gut_comfort",
    "skin_quality": "improve_skin_health",
    "immune_system": "strengthen_immune_resilience",
    "infection_susceptibility": "strengthen_immune_resilience",
    "heart_health": "support_heart_health",
    "weight_management": "manage_weight",
    "bone_health": "support_bone_health",
    "hormone_balance": "support_hormone_balance",
    "anti_inflammatory": "reduce_inflammation",
}


def run(ctx: PipelineContext) -> PipelineContext:
    """Apply all deterministic rules."""
    print("\n─── B. RULES ───────────────────────────────────────────────")

    rule_outputs = apply_rules(ctx.unified_input)

    # Merge inferred health signals into supplement claims
    hc = rule_outputs["health_claims"]
    existing_claims = set(hc.get("supplement_claims", []))
    inferred_to_merge = []
    for sig in ctx.clinical_summary.get("inferred_health_signals", []):
        sig_str = sig.get("signal", sig) if isinstance(sig, dict) else sig
        if sig_str and sig_str not in existing_claims:
            inferred_to_merge.append(sig_str)
    if inferred_to_merge:
        hc["supplement_claims"] = hc.get("supplement_claims", []) + inferred_to_merge
        print(f"  ✅ Merged inferred signals: {inferred_to_merge}")

    # Build effective goals (explicit + inferred)
    q_goals = list(ctx.unified_input["questionnaire"]["goals"].get("ranked", []))
    goals_set = set(g.lower() for g in q_goals)
    for sig in ctx.clinical_summary.get("inferred_health_signals", []):
        sig_str = sig.get("signal", sig) if isinstance(sig, dict) else sig
        mapped = INFERRED_SIGNAL_TO_GOAL.get(sig_str)
        if mapped and mapped.lower() not in goals_set:
            q_goals.append(mapped)
            goals_set.add(mapped.lower())

    ctx.effective_goals = {
        "ranked": q_goals,
        "top_goal": ctx.unified_input["questionnaire"]["goals"].get("top_goal", ""),
    }

    # Print summary
    sens = rule_outputs["sensitivity"]
    print(f"  Sensitivity: {sens['classification'].upper()} | Prebiotics: {rule_outputs['prebiotic_range']['min_g']}–{rule_outputs['prebiotic_range']['max_g']}g")

    ctx.rule_outputs = rule_outputs
    return ctx
