#!/usr/bin/env python3
"""
Medication Evidence Retrieval — For unmatched medications (Tier C only).

Results are ALWAYS Tier C (flag only, never auto-modify formulation).
"""

import json
from typing import Dict

from .bedrock_client import call_bedrock, extract_json_from_response, HAS_BOTO3, OPUS_MODEL_ID


def _to_list(val) -> list:
    """Coerce a value to list — handles LLM responses where an array field is returned as dict."""
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return list(val.values())
    return []


def retrieve_medication_evidence(medication_entries: list, selected_supplements: list,
                                  use_bedrock: bool = True) -> Dict:
    """Retrieve structured evidence for medications not matched by KB rules.

    Returns:
        {evidence_flags: list, evidence_objects: list, source: str}
    """
    if not medication_entries or not use_bedrock or not HAS_BOTO3:
        return {"evidence_flags": [], "evidence_objects": [], "source": "skipped"}

    evidence_flags = []
    evidence_objects = []

    for med_entry in medication_entries:
        med_name = med_entry.get("name", "").strip()
        if not med_name:
            continue

        supplements_str = ", ".join(selected_supplements[:20]) if selected_supplements else "none"

        system_prompt = (
            "You are a clinical pharmacology evidence extractor. "
            "Return ONLY structured JSON with: medication, administration_timing, "
            "empty_stomach_required, mineral_interactions, fibre_interactions, "
            "micronutrient_interactions, supplement_contraindications, confidence."
        )
        user_prompt = (
            f"MEDICATION: {med_name} {med_entry.get('dosage', '')}\n"
            f"SUPPLEMENTS: {supplements_str}\n"
            f"Extract pharmacological evidence. Return ONLY JSON."
        )

        try:
            response_text = call_bedrock(system_prompt, user_prompt, max_tokens=1500,
                                          temperature=0.0, model_id=OPUS_MODEL_ID)
            evidence = extract_json_from_response(response_text)

            if isinstance(evidence, dict):
                from datetime import datetime
                evidence["_source"] = "bedrock_llm"
                evidence["_retrieval_timestamp"] = datetime.utcnow().isoformat() + "Z"
                evidence_objects.append(evidence)

                all_interactions = (
                    _to_list(evidence.get("mineral_interactions", [])) +
                    _to_list(evidence.get("fibre_interactions", [])) +
                    _to_list(evidence.get("micronutrient_interactions", [])) +
                    _to_list(evidence.get("supplement_contraindications", [])) +
                    _to_list(evidence.get("pharmacokinetic_interactions", []))
                )

                for interaction in all_interactions:
                    if not isinstance(interaction, dict):
                        continue  # LLM returned a string instead of structured dict — skip
                    interacting = (
                        interaction.get("mineral", "") or interaction.get("substance", "") or
                        interaction.get("nutrient", "") or interaction.get("supplement", "")
                    ).lower()
                    severity = interaction.get("severity", "low")

                    is_in_formulation = any(
                        interacting in s.lower() or s.lower() in interacting
                        for s in selected_supplements
                    ) if selected_supplements and interacting else False

                    if is_in_formulation or severity == "high":
                        evidence_flags.append({
                            "rule_id": "ELICIT_AUTO", "tier": "C",
                            "severity": severity,
                            "title": f"EXTERNAL EVIDENCE: {interacting.title()} + {med_name}",
                            "detail": interaction.get("mechanism", ""),
                            "medication": med_name,
                            "auto_executed": False,
                            "source": "bedrock_llm_evidence_retrieval",
                            "review_status": "PENDING_CLINICIAN",
                        })

                if evidence.get("empty_stomach_required"):
                    evidence_flags.append({
                        "rule_id": "ELICIT_AUTO", "tier": "C", "severity": "moderate",
                        "title": f"EXTERNAL EVIDENCE: {med_name} requires empty stomach",
                        "detail": f"Administration: {evidence.get('administration_timing', '?')}",
                        "medication": med_name,
                        "auto_executed": False,
                        "source": "bedrock_llm_evidence_retrieval",
                        "review_status": "PENDING_CLINICIAN",
                    })

        except Exception as e:
            print(f"  ⚠️ Evidence retrieval failed for {med_name}: {e}")

    return {"evidence_flags": evidence_flags, "evidence_objects": evidence_objects, "source": "bedrock_llm"}
