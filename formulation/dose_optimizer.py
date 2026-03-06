#!/usr/bin/env python3
"""
Dose Optimizer — JSON-driven rule engine for evening capsule dose adjustments.

This is the ONLY layer in the pipeline allowed to change upstream-selected doses.
All changes are:
  - Within KB min-max ranges
  - Documented with explicit reasons (optimized_reason, optimized_from_mg, optimized_to_mg)
  - Driven by rules in knowledge_base/dose_optimization_rules.json

The capsule builder (generate_formulation.py) treats capsules as dose-preserving
containers and fills unused capacity with MCC excipient.

Date: 05 March 2026
"""

import json
from pathlib import Path
from typing import Dict, List, Optional


KB_DIR = Path(__file__).parent / "knowledge_base"
RULES_PATH = KB_DIR / "dose_optimization_rules.json"


class DoseOptimizer:
    """Applies JSON-configured dose optimization rules to supplement lists."""

    def __init__(self, rules_path: str = None):
        """Load rules from JSON config."""
        path = Path(rules_path) if rules_path else RULES_PATH
        with open(path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        self.rules = config.get("rules", [])
        self.version = config.get("metadata", {}).get("version", 0)

    def optimize(self, evening_components: List[Dict]) -> Dict:
        """Apply matching optimization rules to evening components.

        Args:
            evening_components: List of dicts, each with at least:
                - substance (str): Component name (e.g., "Ashwagandha (Withania somnifera)")
                - dose_mg (float): Currently selected dose
                - weight_mg (float): Weight (typically == dose_mg for non-probiotic)
                - rationale (str): Why this dose was chosen

        Returns:
            Dict with:
                - components (list): The (potentially modified) component list
                - applied_rules (list): IDs of rules that fired
                - log (list): Human-readable log lines
        """
        applied_rules = []
        log = []

        for rule in self.rules:
            if not rule.get("enabled", True):
                continue

            result = self._apply_rule(rule, evening_components)
            if result["applied"]:
                evening_components = result["components"]
                applied_rules.append(rule["id"])
                log.extend(result["log"])

        return {
            "components": evening_components,
            "applied_rules": applied_rules,
            "log": log,
        }

    def _apply_rule(self, rule: Dict, components: List[Dict]) -> Dict:
        """Try to apply a single rule. Returns dict with applied=bool, components, log."""
        scope = rule["scope"]
        constraints = rule["constraints"]
        action = rule["action"]
        log = []

        # ── Scope matching ───────────────────────────────────────────
        # Check requires: all must be present
        for required_name in scope.get("requires", []):
            if not self._find_component(components, required_name):
                return {"applied": False, "components": components, "log": []}

        # Check forbidden: none must be present
        for forbidden_name in scope.get("forbidden", []):
            if self._find_component(components, forbidden_name):
                return {"applied": False, "components": components, "log": []}

        # ── Build combination candidate ──────────────────────────────
        capsule_constraint = constraints.get("capsule", {})
        capacity = capsule_constraint.get("capacity_mg", 650)
        target_fill = capsule_constraint.get("target_fill_mg", 650)

        combo = []
        total_target = 0

        for entry in action.get("combination", []):
            comp = self._find_component(components, entry["name"])
            if comp is None:
                return {"applied": False, "components": components, "log": []}

            source_dose = comp["dose_mg"]

            # Determine target dose
            if entry.get("use_source_dose"):
                target_dose = source_dose
            else:
                target_dose = entry.get("target_mg", source_dose)

            # Validate against ingredient constraints
            ingr_key = entry["name"]
            ingr_constraints = constraints.get(ingr_key, {})

            if ingr_constraints.get("respect_source_dose"):
                target_dose = source_dose  # Enforce: don't change this one

            kb_min = ingr_constraints.get("kb_min_mg")
            kb_max = ingr_constraints.get("kb_max_mg")
            if kb_min is not None and target_dose < kb_min:
                log.append(f"  Rule {rule['id']}: {entry['name']} target {target_dose}mg < KB min {kb_min}mg — rule skipped")
                return {"applied": False, "components": components, "log": log}
            if kb_max is not None and target_dose > kb_max:
                log.append(f"  Rule {rule['id']}: {entry['name']} target {target_dose}mg > KB max {kb_max}mg — rule skipped")
                return {"applied": False, "components": components, "log": log}

            direction = ingr_constraints.get("allowed_direction")
            if direction == "decrease_only" and target_dose > source_dose:
                log.append(f"  Rule {rule['id']}: {entry['name']} increase not allowed ({source_dose}→{target_dose}) — rule skipped")
                return {"applied": False, "components": components, "log": log}
            elif direction == "increase_only" and target_dose < source_dose:
                log.append(f"  Rule {rule['id']}: {entry['name']} decrease not allowed ({source_dose}→{target_dose}) — rule skipped")
                return {"applied": False, "components": components, "log": log}

            combo.append((comp, source_dose, target_dose))
            total_target += target_dose

        # ── Fit check ────────────────────────────────────────────────
        if total_target > capacity:
            log.append(f"  Rule {rule['id']}: total {total_target}mg > capacity {capacity}mg — rule skipped")
            return {"applied": False, "components": components, "log": log}

        # For exact-fit rules, check if total matches target_fill
        if action.get("type") == "set_combination_dose_if_exact_fit":
            if total_target != target_fill:
                # Check if excipient can bridge the gap
                if capsule_constraint.get("allow_excipient") and total_target <= capacity:
                    pass  # Excipient will fill the gap — rule can apply
                else:
                    log.append(f"  Rule {rule['id']}: total {total_target}mg ≠ target_fill {target_fill}mg and no excipient — rule skipped")
                    return {"applied": False, "components": components, "log": log}

        # ── Apply optimization ───────────────────────────────────────
        meta = action.get("metadata", {})
        reason = meta.get("optimized_reason", rule["id"])
        note = meta.get("note", "")

        for comp, source_dose, target_dose in combo:
            if target_dose != source_dose:
                comp["optimized"] = True
                comp["optimized_reason"] = reason
                comp["optimized_from_mg"] = source_dose
                comp["optimized_to_mg"] = target_dose
                comp["dose_mg"] = target_dose
                comp["weight_mg"] = target_dose
                log.append(f"  📐 Optimizer [{rule['id']}]: {comp['substance']} {source_dose}mg → {target_dose}mg ({note})")
            else:
                log.append(f"  📐 Optimizer [{rule['id']}]: {comp['substance']} kept at {source_dose}mg (source dose preserved)")

        log.append(f"  📐 Optimizer [{rule['id']}]: Total actives = {total_target}mg / {capacity}mg capacity")

        return {"applied": True, "components": components, "log": log}

    @staticmethod
    def _find_component(components: List[Dict], name: str) -> Optional[Dict]:
        """Find a component by partial name match (case-insensitive)."""
        name_lower = name.lower()
        for comp in components:
            if name_lower in comp.get("substance", "").lower():
                return comp
        return None


# ─── MCC EXCIPIENT HELPER ────────────────────────────────────────────────────

def add_excipient_if_needed(components: List[Dict], capacity_mg: int = 650) -> List[Dict]:
    """Add MCC excipient to fill unused capsule capacity.

    MCC (Microcrystalline Cellulose) is the standard pharmaceutical excipient
    for capsule filling — inert, widely used for weight uniformity and flow.

    Args:
        components: List of active ingredient dicts (each has dose_mg)
        capacity_mg: Capsule capacity in mg (default 650)

    Returns:
        Components list with MCC appended if needed (modifies in place too)
    """
    active_total = sum(c.get("dose_mg", 0) for c in components
                       if c.get("type") != "excipient")

    if active_total > capacity_mg:
        raise ValueError(
            f"FormulationError: Evening capsule actives ({active_total}mg) exceed "
            f"capacity ({capacity_mg}mg). Adjust upstream doses/timing before assembly."
        )

    excipient_mg = round(capacity_mg - active_total, 2)
    if excipient_mg > 0:
        # Remove any existing excipient first (idempotent)
        components = [c for c in components if c.get("type") != "excipient"]
        components.append({
            "substance": "Microcrystalline Cellulose (MCC)",
            "dose_mg": excipient_mg,
            "weight_mg": excipient_mg,
            "type": "excipient",
            "rationale": "Capsule filler for weight uniformity (standard pharmaceutical practice)",
        })

    return components


# ─── CLI TEST ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick test with mock data
    optimizer = DoseOptimizer()
    print(f"Loaded {len(optimizer.rules)} optimization rule(s) (v{optimizer.version})")

    # Test case 1: Ashwagandha 600mg + L-Theanine 200mg, no Quercetin → should flex
    test_components = [
        {"substance": "Ashwagandha (Withania somnifera)", "dose_mg": 600, "weight_mg": 600, "rationale": "Sleep support"},
        {"substance": "L-Theanine", "dose_mg": 200, "weight_mg": 200, "rationale": "Relaxation"},
    ]
    result = optimizer.optimize(test_components)
    print("\nTest 1: Ashwagandha 600 + L-Theanine 200 (no Quercetin)")
    for line in result["log"]:
        print(line)
    for comp in result["components"]:
        print(f"  → {comp['substance']}: {comp['dose_mg']}mg{' [OPTIMIZED]' if comp.get('optimized') else ''}")

    # Test case 2: Ashwagandha 600mg + L-Theanine 200mg + Quercetin 300mg → should NOT fire
    test_components_2 = [
        {"substance": "Ashwagandha (Withania somnifera)", "dose_mg": 600, "weight_mg": 600, "rationale": "Sleep support"},
        {"substance": "L-Theanine", "dose_mg": 200, "weight_mg": 200, "rationale": "Relaxation"},
        {"substance": "Quercetin", "dose_mg": 300, "weight_mg": 300, "rationale": "Gut-brain"},
    ]
    result2 = optimizer.optimize(test_components_2)
    print("\nTest 2: Ashwagandha 600 + L-Theanine 200 + Quercetin 300")
    if not result2["applied_rules"]:
        print("  No rules applied (Quercetin present → forbidden scope)")
    for line in result2["log"]:
        print(line)

    # Test excipient
    print("\nTest 3: Excipient filling")
    comps = [
        {"substance": "Quercetin", "dose_mg": 300, "weight_mg": 300},
        {"substance": "L-Theanine", "dose_mg": 200, "weight_mg": 200},
    ]
    filled = add_excipient_if_needed(comps)
    for c in filled:
        print(f"  → {c['substance']}: {c.get('dose_mg')}mg {'[excipient]' if c.get('type') == 'excipient' else ''}")
