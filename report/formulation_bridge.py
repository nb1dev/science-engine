#!/usr/bin/env python3
"""
formulation_bridge.py — Normalize formulation_master JSON → stable FormulationContext dict.

Single integration point between the formulation pipeline and the health report.
Schema changes in formulation_master only need fixing here — the health report
consumes the stable FormulationContext shape.

Usage:
    from formulation_bridge import get_formulation_context

    ctx = get_formulation_context(formulation_master_dict)
    # ctx['clinical_summary']  → str
    # ctx['medication_removed'] → [str, ...]
    # ctx['timing_override']    → str | None
    # ctx['sleep_supplements']  → [dict, ...]
    # ctx['goal_triggered_supplements'] → [dict, ...]
    # ctx['input_narratives']   → dict
    # ctx['mix_name']           → str
    # ctx['mix_id']             → int | None
    # ctx['sensitivity']        → str
    # ctx['ecological_rationale'] → str
"""

from typing import Any, Dict, List, Optional


def get_formulation_context(formulation: Optional[Dict]) -> Dict[str, Any]:
    """
    Extract a stable, health-report-friendly context from the formulation master JSON.

    Returns a dict with safe defaults for every field — the health report never
    needs to guard against missing keys.  Gracefully handles:
    - formulation=None (no formulation generated yet)
    - Older formulation masters (pre-v4 pipeline) missing new keys
    - Empty or partial sub-dicts
    """
    if not formulation:
        return _empty_context()

    # ── Clinical summary (LLM assessment of the questionnaire) ───────────
    clinical_summary_raw = formulation.get('clinical_summary', {})
    if isinstance(clinical_summary_raw, str):
        clinical_summary_text = clinical_summary_raw
    elif isinstance(clinical_summary_raw, dict):
        # Prefer the narrative summary; fall back to assessment
        clinical_summary_text = (
            clinical_summary_raw.get('narrative_summary', '')
            or clinical_summary_raw.get('assessment', '')
            or clinical_summary_raw.get('summary', '')
        )
    else:
        clinical_summary_text = ''

    # ── Medication rules ─────────────────────────────────────────────────
    med_rules = formulation.get('medication_rules', {})
    substances_removed = med_rules.get('substances_removed', [])
    timing_override = med_rules.get('timing_override')
    clinical_flags = med_rules.get('clinical_flags', [])

    # Build human-readable medication annotations for supplement cards
    medication_annotations = []
    for substance in substances_removed:
        medication_annotations.append({
            'substance': substance,
            'note': f'{substance} was excluded due to a medication interaction.',
        })
    for flag in clinical_flags:
        if isinstance(flag, dict):
            medication_annotations.append({
                'substance': flag.get('substance', ''),
                'note': flag.get('message', flag.get('note', '')),
            })
        elif isinstance(flag, str):
            medication_annotations.append({
                'substance': '',
                'note': flag,
            })

    # ── Decision rule outputs ────────────────────────────────────────────
    decisions = formulation.get('decisions', {})
    rule_outputs = decisions.get('rule_outputs', {})

    sleep_supplements = rule_outputs.get('sleep_supplements', {})
    sleep_supplement_list = sleep_supplements.get('supplements', []) if isinstance(sleep_supplements, dict) else []

    goal_triggered = rule_outputs.get('goal_triggered_supplements', {})
    goal_triggered_list = goal_triggered.get('supplements', []) if isinstance(goal_triggered, dict) else []

    # ── Input narratives (LLM-generated) ─────────────────────────────────
    input_narratives = formulation.get('input_narratives', {})
    if not isinstance(input_narratives, dict):
        input_narratives = {}

    # ── Mix info ─────────────────────────────────────────────────────────
    mix = decisions.get('mix_selection', {})
    mix_name = mix.get('mix_name', '')
    mix_id = mix.get('mix_id')

    # ── Sensitivity ──────────────────────────────────────────────────────
    sensitivity = (
        formulation.get('input_summary', {})
        .get('questionnaire_driven', {})
        .get('sensitivity_classification', '')
    )

    # ── Ecological rationale ─────────────────────────────────────────────
    eco_raw = formulation.get('ecological_rationale', {})
    if isinstance(eco_raw, str):
        ecological_rationale = eco_raw
    elif isinstance(eco_raw, dict):
        ecological_rationale = eco_raw.get('recommendation', eco_raw.get('rationale', ''))
    else:
        ecological_rationale = ''

    # ── Inferred health signals from clinical analysis ───────────────────
    inferred_signals = []
    if isinstance(clinical_summary_raw, dict):
        for sig in clinical_summary_raw.get('inferred_health_signals', []):
            if isinstance(sig, dict):
                inferred_signals.append(sig.get('signal', str(sig)))
            elif isinstance(sig, str):
                inferred_signals.append(sig)

    return {
        'clinical_summary': clinical_summary_text,
        'inferred_health_signals': inferred_signals,
        'medication_removed': substances_removed,
        'medication_annotations': medication_annotations,
        'timing_override': timing_override,
        'clinical_flags': clinical_flags,
        'sleep_supplements': sleep_supplement_list,
        'goal_triggered_supplements': goal_triggered_list,
        'input_narratives': input_narratives,
        'mix_name': mix_name,
        'mix_id': mix_id,
        'sensitivity': sensitivity,
        'ecological_rationale': ecological_rationale,
    }


def _empty_context() -> Dict[str, Any]:
    """Return the FormulationContext with safe empty defaults."""
    return {
        'clinical_summary': '',
        'inferred_health_signals': [],
        'medication_removed': [],
        'medication_annotations': [],
        'timing_override': None,
        'clinical_flags': [],
        'sleep_supplements': [],
        'goal_triggered_supplements': [],
        'input_narratives': {},
        'mix_name': '',
        'mix_id': None,
        'sensitivity': '',
        'ecological_rationale': '',
    }
