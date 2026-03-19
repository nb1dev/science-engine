#!/usr/bin/env python3
"""
Stage 3: Medication Screening — LLM + deterministic KB rules.

Input:  PipelineContext with unified_input
Output: PipelineContext with medication exclusions populated
"""

from pathlib import Path

from ..models import PipelineContext, MedicationExclusions
from formulation.rules_engine import apply_medication_rules

from ..llm.medication_screener import screen_medication_interactions
from ..llm.evidence_retriever import retrieve_medication_evidence


def run(ctx: PipelineContext) -> PipelineContext:
    """Run medication screening: LLM + deterministic KB rules."""

    med = MedicationExclusions()

    # ── LLM screening (A.5b) ────────────────────────────────────────────
    if ctx.use_llm:
        try:
            print("\n─── A.5b MEDICATION SCREENING ──────────────────────────")
            print("  🧠 LLM: Screening medications against supplement database...")
            result = screen_medication_interactions(ctx.unified_input, use_bedrock=ctx.use_llm)
            med.excluded_substances = result.get("excluded_substances", set())
            med.exclusion_reasons = result.get("exclusion_reasons", [])
            if result.get("skipped"):
                print("  ⚠️ Screening skipped")
            elif med.excluded_substances:
                print(f"  🚫 EXCLUDED (medication interaction):")
                for reason in med.exclusion_reasons:
                    print(f"    → {reason.get('substance', '?')} — {reason.get('medication', '?')}: {reason.get('mechanism', '?')}")
            else:
                print("  ✅ No high-severity interactions found")
        except Exception as e:
            print(f"  ⚠️ Medication screening failed: {e}")

    # ── Deterministic KB rules (A.6) ────────────────────────────────────
    print("\n─── A.6 DETERMINISTIC MEDICATION RULES (KB) ────────────────")
    kb_result = apply_medication_rules(ctx.unified_input)

    med.timing_override = kb_result.get("timing_override")
    med.substances_to_remove = kb_result.get("substances_to_remove", set())
    med.magnesium_removed = kb_result.get("magnesium_removed", False)
    med.clinical_flags = kb_result.get("clinical_flags", [])
    med.unmatched_medications = kb_result.get("unmatched_medications", [])
    med.matched_rules = kb_result.get("matched_rules", [])
    med.removal_reasons = kb_result.get("removal_reasons", [])

    if kb_result.get("matched_rules"):
        for mr in kb_result["matched_rules"]:
            rule = mr["rule"]
            print(f"  MATCHED [{rule.get('tier')}] {rule.get('rule_id')}: {mr['medication_raw']}")
    else:
        print(f"  ✅ No KB medication rules matched")

    # Merge KB exclusions into LLM set
    for substance in med.substances_to_remove:
        med.excluded_substances.add(substance)

    # ── Evidence retrieval for unmatched medications (A.6b) ──────────────
    if med.unmatched_medications and ctx.use_llm:
        try:
            print(f"\n─── A.6b EVIDENCE RETRIEVAL (unmatched medications) ─────────")
            result = retrieve_medication_evidence(
                medication_entries=med.unmatched_medications,
                selected_supplements=[],
                use_bedrock=ctx.use_llm,
            )
            med.elicit_evidence_result = result
            flags = result.get("evidence_flags", [])
            if flags:
                print(f"  ⚠️ {len(flags)} evidence flag(s) generated (Tier C — no auto-changes)")
            else:
                print(f"  ✅ No evidence flags")
        except Exception as e:
            print(f"  ⚠️ Evidence retrieval failed: {e}")

    ctx.medication = med
    return ctx
