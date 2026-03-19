# Archive: Sachet Architecture — 17 March 2026

## What this archive contains

Exact copies of the following files as they existed on 17 March 2026, **before** the powder jar + capsule stacking overhaul:

| File | Description |
|------|-------------|
| `weight_calculator.py` | FormulationCalculator with sachet-based delivery (sachet_prebiotics, sachet_vitamins, sachet_supplements) |
| `llm_decisions.py` | System prompts referencing sachet delivery; no phased dosing |
| `generate_formulation.py` | Sachet overflow resolution, evening capsule 1/2 split, rebalancing, small-capsule merge |
| `platform_mapping.py` | Recipe output using "Daily Sachet" unit label |
| `dose_optimizer.py` | Evening capsule dose optimizer (JSON-driven rules) |
| `delivery_format_rules.json` | Sachet format with hard 19g cap; vitamins/minerals assigned to sachet |

## Why archived

Manufacturing constraint change (March 2026) made sachet delivery of prebiotics + vitamins infeasible.

## What replaced it

The new architecture (same filenames in `science-engine/formulation/`) implements:

- **Powder jar**: prebiotics + non-bitter heavy botanicals (dose > 650mg), with phased dosing (weeks 1–2 = 50% of full dose, week 3+ = 100%)
- **Pooled morning capsule(s)**: vitamins + minerals + light non-bitter botanicals (dose ≤ 650mg), stacked via `CapsuleStackingOptimizer`
- **Pooled evening capsule(s)**: sleep aids + calming adaptogens + Tier 1 polyphenols, stacked via `CapsuleStackingOptimizer`
- Bitter botanicals + Tier 2 polyphenols: unchanged routing (evening capsule, polyphenol capsule)
- Unit count limits updated: preferred max = 9, absolute max = 13

## Rollback

To restore the sachet architecture, copy these files back to `science-engine/formulation/` and `science-engine/formulation/knowledge_base/`.
