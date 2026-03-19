# Formulation Pipeline — Monolith Archive (19 March 2026)

## What this is

This directory contains the **pre-refactor snapshot** of the formulation pipeline.
All files are exact copies of the production code as of 19 March 2026 (commit `fa7d197`),
before the modular restructure.

## Why it was archived

The monolithic `generate_formulation.py` (~1900 lines in a single function) became:
- Impossible to test any stage independently
- Brittle — changing one stage cascades side effects through shared mutable state
- Hard to reason about — 15+ stages sharing local variables

## What replaced it

The new modular architecture in `science-engine/formulation/` splits the pipeline into:
- `pipeline.py` — thin orchestrator (~100 lines)
- `models.py` — typed dataclasses (PipelineContext, RemovalLog)
- `stages/` — 9 pipeline stages, each a pure function: PipelineContext → PipelineContext
- `filters/` — 9 post-processing rules (Stage D), each independently testable
- `builders/` — output assemblers (master JSON, platform JSON, recipe, dashboards)
- `llm/` — all LLM interactions isolated with explicit inputs/outputs

## Files in this archive

| File | Description |
|------|-------------|
| `generate_formulation.py` | Main orchestrator (1900-line god function) |
| `generate_formulation_evening.py` | Evening timing override variant |
| `llm_decisions.py` | All LLM calls + offline fallbacks |
| `parse_inputs.py` | Input parsing (microbiome + questionnaire) |
| `rules_engine.py` | Deterministic rules |
| `platform_mapping.py` | Output JSON builders |
| `weight_calculator.py` | Weight calculations + FormulationCalculator |
| `dose_optimizer.py` | Evening capsule dose optimizer |
| `generate_dashboards.py` | HTML dashboard generation |
| `apply_medication_timing_override.py` | Post-processing timing rewrite |
| `consistency_check.py` | Cross-file consistency audit |
| `sanity_check_vs_kb.py` | Decision correctness vs KB audit |
| `sync_csv_to_json.py` | CSV → JSON knowledge base sync |

## Retirement log

- **19 March 2026** — Root-level `llm_decisions.py` deleted from `science-engine/formulation/`.
  All functions (`select_mix_offline`, `lookup_strains_for_mix`, `select_supplements`,
  `design_prebiotics`, `screen_medication_interactions`, `generate_ecological_rationale`,
  `formulation_sanity_check`) are now covered by the modular `llm/` package.
  This archived copy remains as reference.

## Revert instructions

To revert to the monolith architecture:
```bash
cp archive/monolith_20260319/*.py .
```
