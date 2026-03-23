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
            # selected_supplements=[] is intentional: evidence retrieval runs as
            # pre-selection enrichment (Stage 3), not post-selection validation.
            # Extracted substance names feed into ctx.medication.evidence_excluded_substances
            # which the S5 LLM prompt uses to avoid selecting them in the first place.
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

            # Extract interacting substance names from structured evidence objects
            # (not from flags, which embed names in title strings).
            # These feed into the S5 LLM prompt as dynamic exclusions and are
            # merged into excluded_substances for the S6 safety net.
            evidence_excluded = set()
            for eo in result.get("evidence_objects", []):
                for interaction_key in ("mineral_interactions", "fibre_interactions",
                                        "micronutrient_interactions", "supplement_contraindications",
                                        "pharmacokinetic_interactions"):
                    interactions = eo.get(interaction_key, [])
                    # Coerce to list — LLM may return dict instead of array
                    if isinstance(interactions, dict):
                        interactions = list(interactions.values())
                    elif not isinstance(interactions, list):
                        interactions = []
                    for interaction in interactions:
                        if not isinstance(interaction, dict):
                            continue  # LLM returned a string instead of structured dict — skip
                        substance = (
                            interaction.get("mineral", "") or interaction.get("substance", "") or
                            interaction.get("nutrient", "") or interaction.get("supplement", "")
                        ).lower().strip()
                        if substance:
                            evidence_excluded.add(substance)
            if evidence_excluded:
                med.evidence_excluded_substances = evidence_excluded
                # Merge into main exclusion set so S6 safety net catches LLM misses
                med.excluded_substances.update(evidence_excluded)
                print(f"  📋 Evidence-derived exclusions: {evidence_excluded}")
        except Exception as e:
            print(f"  ⚠️ Evidence retrieval failed: {e}")

    ctx.medication = med
    return ctx
