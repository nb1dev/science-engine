#!/usr/bin/env python3
"""
Pipeline Data Models — Typed dataclasses for the formulation pipeline.

These replace the mutable dicts and loose local variables from the monolith.
Every pipeline stage receives and returns a PipelineContext, making data flow
explicit and testable.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class RemovalEntry:
    """Single record of a substance removed from the formulation."""
    substance: str
    reason: str
    stage: str          # e.g., "medication_exclusion", "vitamin_gate", "polyphenol_cap"
    severity: str = ""  # "high", "medium", "info"


@dataclass
class RemovalLog:
    """Single source of truth for ALL removed substances + reasons.
    
    Replaces the 5 separate tracking sets in the monolith:
    - capacity_trimmed_names
    - evening_overflow_dropped
    - conflict_removed_names
    - interaction_removed_names
    - polyphenol_cap_dropped
    """
    entries: List[RemovalEntry] = field(default_factory=list)

    def add(self, substance: str, reason: str, stage: str, severity: str = ""):
        self.entries.append(RemovalEntry(
            substance=substance.lower(),
            reason=reason,
            stage=stage,
            severity=severity,
        ))

    def was_removed(self, substance: str) -> bool:
        name = substance.lower()
        return any(e.substance == name or name in e.substance or e.substance in name
                   for e in self.entries)

    def reason_for(self, substance: str) -> Optional[str]:
        name = substance.lower()
        for e in self.entries:
            if e.substance == name or name in e.substance or e.substance in name:
                return e.reason
        return None

    def removed_at_stage(self, stage: str) -> List[RemovalEntry]:
        return [e for e in self.entries if e.stage == stage]

    def all_removed_names(self) -> Set[str]:
        return {e.substance for e in self.entries}

    # Backward-compat property sets for code that still references the old tracking sets
    @property
    def capacity_trimmed_names(self) -> Set[str]:
        return {e.substance for e in self.entries if e.stage == "sachet_overflow"}

    @property
    def evening_overflow_dropped(self) -> Set[str]:
        return {e.substance for e in self.entries if e.stage == "evening_overflow"}

    @property
    def conflict_removed_names(self) -> Set[str]:
        return {e.substance for e in self.entries if e.stage == "mineral_conflict"}

    @property
    def interaction_removed_names(self) -> Set[str]:
        return {e.substance for e in self.entries if e.stage == "herb_drug_interaction"}

    @property
    def polyphenol_cap_dropped(self) -> Set[str]:
        return {e.substance for e in self.entries if e.stage == "polyphenol_cap"}


@dataclass
class MedicationExclusions:
    """Medication-driven substance exclusions."""
    excluded_substances: Set[str] = field(default_factory=set)
    exclusion_reasons: List[Dict] = field(default_factory=list)
    timing_override: Optional[Dict] = None
    substances_to_remove: Set[str] = field(default_factory=set)
    magnesium_removed: bool = False
    clinical_flags: List[Dict] = field(default_factory=list)
    unmatched_medications: List[Dict] = field(default_factory=list)
    matched_rules: List[Dict] = field(default_factory=list)
    removal_reasons: List[Dict] = field(default_factory=list)
    elicit_evidence_result: Dict = field(default_factory=lambda: {"evidence_flags": [], "evidence_objects": [], "source": "skipped"})
    # Substances flagged by LLM evidence retrieval (all severities).
    # Used by S5 LLM prompt as dynamic exclusion context, and merged into
    # excluded_substances for S6 safety net.
    evidence_excluded_substances: Set[str] = field(default_factory=set)


@dataclass
class PipelineContext:
    """Central state object passed through every pipeline stage.
    
    Each stage reads what it needs and writes its outputs back.
    No stage should access data outside this context.
    """
    # ── Identity ─────────────────────────────────────────────────────────
    sample_id: str = ""
    sample_dir: str = ""
    batch_id: str = ""
    use_llm: bool = True
    compact: bool = False

    # ── Stage 1: Parsed inputs ───────────────────────────────────────────
    unified_input: Dict = field(default_factory=dict)

    # ── Stage 2: Clinical analysis (LLM) ─────────────────────────────────
    clinical_summary: Dict = field(default_factory=lambda: {
        "profile_narrative": [],
        "inferred_health_signals": [],
        "clinical_review_flags": [],
    })

    # ── Stage 3: Medication screening ────────────────────────────────────
    medication: MedicationExclusions = field(default_factory=MedicationExclusions)

    # ── Stage 4: Deterministic rules ─────────────────────────────────────
    rule_outputs: Dict = field(default_factory=dict)
    effective_goals: Dict = field(default_factory=dict)

    # ── Stage 5: Formulation decisions ───────────────────────────────────
    mix: Dict = field(default_factory=dict)
    supplements: Dict = field(default_factory=dict)
    prebiotics: Dict = field(default_factory=dict)

    # ── Stage 6: Post-processing ─────────────────────────────────────────
    removal_log: RemovalLog = field(default_factory=RemovalLog)
    excluded_polyphenols: Set[str] = field(default_factory=set)
    vitamin_gate_removed: List[str] = field(default_factory=list)
    piperine_applied: bool = False

    # ── Stage 7: Weight calculation ──────────────────────────────────────
    calc: object = None  # FormulationCalculator (can't type-hint due to circular import)
    formulation: Dict = field(default_factory=dict)
    component_registry: List[Dict] = field(default_factory=list)

    # ── Stage 8: Narratives ──────────────────────────────────────────────
    ecological_rationale: Dict = field(default_factory=dict)
    input_narratives: Dict = field(default_factory=lambda: {"microbiome_narrative": "", "questionnaire_narrative": ""})

    # ── Cross-cutting ────────────────────────────────────────────────────
    trace_events: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ── Convenience accessors ────────────────────────────────────────────
    @property
    def guilds(self) -> Dict:
        return self.unified_input.get("microbiome", {}).get("guilds", {})

    @property
    def clr(self) -> Dict:
        return self.unified_input.get("microbiome", {}).get("clr_ratios", {})

    @property
    def questionnaire(self) -> Dict:
        return self.unified_input.get("questionnaire", {})

    @property
    def goals_ranked(self) -> List[str]:
        return self.effective_goals.get("ranked", [])

    def add_trace(self, event_type: str, substance: str, description: str, **kwargs):
        """Add a trace event — canonical way to record pipeline decisions."""
        event = {
            "type": event_type,
            "substance": substance,
            "description": description,
        }
        event.update(kwargs)
        self.trace_events.append(event)
