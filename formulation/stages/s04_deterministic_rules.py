#!/usr/bin/env python3
"""
Stage 4: Deterministic Rules — Apply all threshold-based rules from knowledge_base.

Input:  PipelineContext with unified_input + clinical_summary
Output: PipelineContext with rule_outputs + effective_goals populated

Delegates to rules_engine.py (already clean).
"""

import json
from pathlib import Path

from ..models import PipelineContext
from formulation.rules_engine import apply_rules, apply_timing_rules, assess_softgel_needs

# Inferred signal → KB health-claim category name (for supplement_claims merge)
# These MUST match the category names in supplements_nonvitamins.json exactly.
INFERRED_SIGNAL_TO_HEALTH_CLAIM = {
    "stress_anxiety": "Stress/Anxiety",
    "sleep_quality": "Sleep Quality",
    "fatigue": "Fatigue",
    "bowel_function": "Bowel Function",
    "skin_quality": "Skin Quality",
    "immune_system": "Infection Susceptibility",
    "infection_susceptibility": "Infection Susceptibility",
    "heart_health": "Blood Cholesterol",
    "weight_management": "Fullness/Satiety",
    "anti_inflammatory": "Anti-inflammatory",
    "hormone_balance": None,   # no direct KB category
    "bone_health": None,       # no direct KB category
}

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

    try:
        rule_outputs = apply_rules(ctx.unified_input)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  🚨 Knowledge base loading failed: {e}")
        raise RuntimeError(f"Stage 4: Knowledge base loading failed — {e}") from e

    # Merge inferred health signals into supplement claims
    # Map raw signal names → KB health-claim category names so the LLM
    # supplement selection and underfill companion search can match them.
    hc = rule_outputs["health_claims"]
    existing_claims = set(hc.get("supplement_claims", []))
    inferred_to_merge = []
    for sig in ctx.clinical_summary.get("inferred_health_signals", []):
        sig_str = sig.get("signal", sig) if isinstance(sig, dict) else sig
        mapped_claim = INFERRED_SIGNAL_TO_HEALTH_CLAIM.get(sig_str)
        if mapped_claim and mapped_claim not in existing_claims:
            inferred_to_merge.append(mapped_claim)
            existing_claims.add(mapped_claim)  # prevent duplicates within inferred set
    if inferred_to_merge:
        hc["supplement_claims"] = hc.get("supplement_claims", []) + inferred_to_merge
        print(f"  ✅ Merged inferred signals → KB claims: {inferred_to_merge}")

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

    # Re-evaluate softgel decision AFTER inferred signals are merged into claims
    # and effective goals are built. The initial apply_rules() call runs before
    # inferred signals are available, so softgel can be a false-negative for
    # clients with incomplete questionnaires whose goals come entirely from inference.
    updated_softgel = assess_softgel_needs(
        health_claims=rule_outputs["health_claims"],  # now includes inferred signals
        medical=ctx.unified_input["questionnaire"]["medical"],
        lifestyle=ctx.unified_input["questionnaire"]["lifestyle"],
        goals=ctx.effective_goals,  # now includes inferred goals
    )
    if updated_softgel["include_softgel"] != rule_outputs["softgel"]["include_softgel"]:
        print(
            f"  🔄 Softgel decision updated after inferred signals merge: "
            f"{rule_outputs['softgel']['include_softgel']} → {updated_softgel['include_softgel']} "
            f"(triggers: {updated_softgel['needs_identified']})"
        )
        rule_outputs["softgel"] = updated_softgel

    # Print summary
    sens = rule_outputs["sensitivity"]
    print(f"  Sensitivity: {sens['classification'].upper()} | Prebiotics: {rule_outputs['prebiotic_range']['min_g']}–{rule_outputs['prebiotic_range']['max_g']}g")

    ctx.rule_outputs = rule_outputs
    return ctx
