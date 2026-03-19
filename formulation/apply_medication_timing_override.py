#!/usr/bin/env python3
"""
Medication Timing Override — Post-processing module.

When a Tier A medication rule (e.g., MED_001 for levothyroxine/T4) requires
ALL supplement units to be taken at a different time (typically dinner),
this module rewrites the completed formulation master JSON to reflect that.

Called automatically by generate_formulation.py at Stage 8.5 when
medication_rules.timing_override is present. Not a standalone script.

Architecture:
  generate_formulation.py builds the full formulation with standard timing
  → this module post-processes the master dict to move all units to dinner
  → downstream outputs (platform JSON, recipe, trace, dashboards) are built
    from the modified master, so they automatically reflect the override.

Why a separate module:
  The standard pipeline has deeply embedded morning/evening routing logic
  across FormulationCalculator, timing rules, delivery format assignments,
  capsule stacking, and capacity guards. Trying to thread a "move everything
  to dinner" override through all those stages would be fragile and error-prone.
  Instead, we let the pipeline run normally, then cleanly rewrite timings
  in one pass over the finished master JSON.
"""

from typing import Dict


# ─── TIMING LABELS ────────────────────────────────────────────────────────────

OVERRIDE_TIMING_LABEL = "evening (with dinner meal)"
OVERRIDE_TIMING_LABEL_SHORT = "dinner"


def apply_timing_override(master: Dict, timing_override: Dict) -> Dict:
    """Rewrite all delivery unit timings in a completed formulation master.

    This is the single entry point called by generate_formulation.py.
    It modifies the master dict in-place and returns it.

    Args:
        master: Complete formulation master dict (post-assembly, pre-save).
        timing_override: The timing_override dict from medication rules, e.g.:
            {
                "rule_id": "MED_001",
                "medication": "T4",
                "medication_normalized": "levothyroxine",
                "move_to": "dinner",
                "affects": ["all_units"],
                "exclude_from_override": [],
                "reason": "Maximum spacing from morning T4 dose...",
                "clinical_note": "T4 should be taken on empty stomach...",
                "tier": "A",
                "severity": "high",
            }

    Returns:
        Modified master dict with all timings rewritten.
    """
    move_to = timing_override.get("move_to", "dinner")
    reason = timing_override.get("reason", "Medication timing override")
    clinical_note = timing_override.get("clinical_note", "")
    rule_id = timing_override.get("rule_id", "?")
    medication = timing_override.get("medication", "?")
    medication_normalized = timing_override.get("medication_normalized", medication)
    affects = timing_override.get("affects", ["all_units"])
    exclude = set(timing_override.get("exclude_from_override", []))

    # Determine the target timing label
    if move_to == "dinner":
        target_timing = OVERRIDE_TIMING_LABEL
        target_timing_recipe = OVERRIDE_TIMING_LABEL
    else:
        target_timing = f"{move_to}"
        target_timing_recipe = f"{move_to}"

    formulation = master.get("formulation", {})

    # ── 1. Rewrite delivery format timings ───────────────────────────────

    # Track original morning/evening counts for protocol summary rewrite
    original_morning_solid = 0
    original_evening_solid = 0

    _delivery_keys_to_override = [
        "delivery_format_1_probiotic_capsule",
        "delivery_format_2_omega_softgels",
        "delivery_format_3_powder_jar",
        "delivery_format_3_daily_sachet",       # v2 fallback
        "delivery_format_4_morning_wellness_capsules",
        "delivery_format_5_evening_wellness_capsules",
        "delivery_format_5_polyphenol_capsule",
        "delivery_format_6_polyphenol_capsule",
    ]

    for key in _delivery_keys_to_override:
        unit = formulation.get(key)
        if not unit:
            continue

        fmt = unit.get("format", {})
        old_timing = fmt.get("timing", "")

        # Check exclusions
        if exclude and key in exclude:
            continue

        # Rewrite timing
        fmt["timing"] = target_timing

        # Rewrite label if it was "Morning Wellness Capsule"
        if "morning" in fmt.get("label", "").lower():
            fmt["label"] = fmt["label"].replace("Morning", "Evening").replace("morning", "evening")

    # ── 2. Rewrite protocol summary ──────────────────────────────────────

    proto = formulation.get("protocol_summary", {})
    if proto:
        # All units move to evening — morning becomes 0
        total_morning_solid = proto.get("morning_solid_units", 0)
        total_morning_jar = proto.get("morning_jar_units", 0)
        total_evening_solid = proto.get("evening_solid_units", 0)

        # Everything that was morning is now evening
        proto["morning_solid_units"] = 0
        proto["morning_jar_units"] = 0
        proto["evening_solid_units"] = total_evening_solid + total_morning_solid + total_morning_jar
        proto["medication_timing_override"] = {
            "applied": True,
            "rule_id": rule_id,
            "medication": medication_normalized,
            "original_morning_units": total_morning_solid + total_morning_jar,
            "moved_to": move_to,
            "reason": reason,
        }

    # ── 3. Add metadata flag ─────────────────────────────────────────────

    metadata = master.get("metadata", {})
    metadata["medication_timing_override_applied"] = True
    metadata["medication_timing_override_rule"] = rule_id
    if "warnings" not in metadata:
        metadata["warnings"] = []
    metadata["warnings"].append(
        f"MEDICATION TIMING OVERRIDE ({rule_id}): All units moved to {move_to} — "
        f"{medication_normalized.title()} spacing requirement. {reason}"
    )

    # ── 4. Update timing assignments in rule_outputs ─────────────────────

    decisions = master.get("decisions", {})
    rule_outputs = decisions.get("rule_outputs", {})
    timing = rule_outputs.get("timing", {})

    # Override all timing assignments to evening/dinner
    for key, assignment in timing.get("timing_assignments", {}).items():
        assignment["timing"] = "evening"
        assignment["delivery"] = "evening_hard_capsule"
        original_reason = assignment.get("reason", "")
        assignment["reason"] = (
            f"MEDICATION OVERRIDE ({rule_id}): All units moved to {move_to}. "
            f"Original: {original_reason}"
        )

    # ── 5. Update component registry delivery labels ─────────────────────

    for entry in master.get("component_registry", []):
        delivery = entry.get("delivery", "")
        if "morning" in delivery.lower() or delivery in ("sachet", "softgel", "probiotic capsule"):
            entry["delivery"] = entry["delivery"] + f" → {move_to} (medication override)"

    # ── 6. Inject override explanation into medication_rules block ────────

    med_rules = master.get("medication_rules", {})
    if med_rules:
        med_rules["timing_override_applied"] = True
        med_rules["timing_override_summary"] = (
            f"All {proto.get('medication_timing_override', {}).get('original_morning_units', '?')} "
            f"morning units moved to {move_to}. "
            f"Medication: {medication_normalized.title()}. "
            f"Reason: {reason}"
        )

    return master


def print_timing_override_summary(timing_override: Dict, formulation: Dict) -> None:
    """Print a clear summary of the timing override for the pipeline log.

    Called by generate_formulation.py after apply_timing_override() to produce
    a scannable block in the pipeline log output.
    """
    rule_id = timing_override.get("rule_id", "?")
    medication = timing_override.get("medication", "?")
    medication_normalized = timing_override.get("medication_normalized", medication)
    move_to = timing_override.get("move_to", "dinner")
    reason = timing_override.get("reason", "")
    clinical_note = timing_override.get("clinical_note", "")

    proto = formulation.get("protocol_summary", {})
    override_info = proto.get("medication_timing_override", {})
    moved_count = override_info.get("original_morning_units", "?")

    W = 68
    print(f"\n  ┌─ ⏰ MEDICATION TIMING OVERRIDE {'─' * (W - 34)}")
    print(f"  │ Rule: {rule_id} ({medication_normalized.title()})")
    print(f"  │ Client medication: {medication}")
    print(f"  │ Action: ALL {moved_count} morning units → {move_to.upper()}")
    print(f"  │")

    # Word-wrap reason
    import textwrap
    for line in textwrap.wrap(reason, width=W - 6):
        print(f"  │ {line}")
    print(f"  │")

    if clinical_note:
        print(f"  │ Clinical note:")
        for line in textwrap.wrap(clinical_note, width=W - 6):
            print(f"  │   {line}")
        print(f"  │")

    # List affected units
    formulation_data = formulation
    print(f"  │ Affected delivery units:")
    for key in ["delivery_format_1_probiotic_capsule", "delivery_format_2_omega_softgels",
                 "delivery_format_3_powder_jar", "delivery_format_4_morning_wellness_capsules",
                 "delivery_format_5_evening_wellness_capsules", "delivery_format_6_polyphenol_capsule"]:
        unit = formulation_data.get(key)
        if unit:
            fmt = unit.get("format", {})
            label = fmt.get("label", key.replace("delivery_format_", "").replace("_", " ").title())
            timing = fmt.get("timing", "?")
            print(f"  │   → {label}: {timing}")

    print(f"  │")
    print(f"  │ Protocol: 0 morning units, all taken with dinner meal")
    print(f"  │ Consistency > complexity: single evening intake is clinically safer")
    print(f"  └{'─' * (W + 2)}\n")
