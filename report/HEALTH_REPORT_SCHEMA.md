# Health Report Interpretations JSON — Schema v3.0

> **Date**: 19 March 2026
> **Author**: Polina Novikova
> **Purpose**: Define the single-source-of-truth JSON that powers `generate_html()`.
> **Principle**: Every visible HTML element maps to exactly one JSON field. No raw file loading at render time.

---

## Architecture

```
microbiome_analysis_master.json ──┐
formulation_master.json ──────────┤
questionnaire.json ───────────────┤
                                  ▼
                    assemble_interpretations.py
                    (reads 3 files, runs computations + LLM)
                                  │
                                  ▼
                    health_report_interpretations.json  (schema v3.0)
                                  │
                                  ▼
                    generate_html()  (pure template — reads one file)
                                  │
                                  ▼
                    health_report_{sample_id}.html
```

---

## Field-by-Field Source Mapping

### Root fields

| JSON Key | Type | Source File | Source Path | Computed? |
|----------|------|------------|-------------|-----------|
| `sample_id` | str | all | directory name | No |
| `generated_at` | str | — | `datetime.now()` | Yes |
| `schema_version` | str | — | hardcoded `"3.0"` | No |
| `report_date` | str | `microbiome_analysis_master` | `.report_metadata.report_date` | No |

### `overall_score` — Cover Page Score Dial + Pillar Chips

| JSON Key | Source Path |
|----------|-------------|
| `overall_score.total` | `analysis.overall_score.total` |
| `overall_score.band` | `analysis.overall_score.band` |
| `overall_score.pillars` | `analysis.overall_score.pillars` (5 objects, each has `score`, `max`, `non_expert`) |
| `overall_score.score_drivers` | `analysis.overall_score.score_drivers` |

### `score_summary` — Cover Page Score Text (pre-rendered)

| JSON Key | Source |
|----------|--------|
| `score_summary` | Computed from `overall_score.total` + `strengths_challenges.distinct_areas` |

### `profile` — Cover Page Client Stats

| JSON Key | Source |
|----------|--------|
| `profile.first_name` | `questionnaire.first_name` |
| `profile.age` | `questionnaire.age` |
| `profile.sex` | `questionnaire.biological_sex` → display label |
| `profile.diet` | `questionnaire.questionnaire_data.step_4.diet_pattern` → display label |
| `profile.stress` | `questionnaire.questionnaire_data.step_5.overall_stress_level_1_10` |
| `profile.sleep` | `questionnaire.questionnaire_data.step_5.sleep_quality_rating_1_10` |
| `profile.sensitivity` | `formulation.input_summary.questionnaire_driven.sensitivity_classification` |
| `profile.goals` | `questionnaire.questionnaire_data.step_1.goals.main_goals_ranked` → display labels |

### `circle_scores` — Section 1 Health Dials

| JSON Key | Source |
|----------|--------|
| `circle_scores.gut_lining` | Computed by `compute_circle_scores(analysis)` |
| `circle_scores.inflammation` | Same |
| `circle_scores.fiber_processing` | Same |
| `circle_scores.bifidobacteria` | Same |

### `bacterial_groups` — Section 1 Guild Bars + SVG Pathway

| JSON Key | Source Path |
|----------|-------------|
| `bacterial_groups.{guild_name}.abundance` | `analysis.bacterial_groups.{guild_name}.abundance` |
| `bacterial_groups.{guild_name}.healthy_range` | `analysis.bacterial_groups.{guild_name}.healthy_range` |
| `bacterial_groups.{guild_name}.status` | `analysis.bacterial_groups.{guild_name}.status` |
| `bacterial_groups.{guild_name}.clr` | `analysis.bacterial_groups.{guild_name}.clr` |
| `bacterial_groups.{guild_name}.evenness` | `analysis.bacterial_groups.{guild_name}.evenness` |
| `bacterial_groups.{guild_name}.evenness_status` | `analysis.bacterial_groups.{guild_name}.evenness_status` |
| `bacterial_groups.{guild_name}.client_interpretation` | `analysis.bacterial_groups.{guild_name}.client_interpretation` |

Guild names: Cross-Feeders, Fiber Degraders, Butyrate Producers, Bifidobacteria, Proteolytic Guild, Mucin Degraders

### `metabolic_dials` — Section 1 (used by circle score computation context)

| JSON Key | Source Path |
|----------|-------------|
| `metabolic_dials.main_fuel` | `analysis.metabolic_function.dials.main_fuel` (state, label, value) |
| `metabolic_dials.fermentation_efficiency` | `analysis.metabolic_function.dials.fermentation_efficiency` |
| `metabolic_dials.mucus_dependency` | `analysis.metabolic_function.dials.mucus_dependency` |
| `metabolic_dials.putrefaction_pressure` | `analysis.metabolic_function.dials.putrefaction_pressure` |

### `ecological_metrics` — Diversity Data

| JSON Key | Source Path |
|----------|-------------|
| `ecological_metrics.shannon` | `analysis.ecological_metrics.diversity.shannon.value` |
| `ecological_metrics.pielou_evenness` | `analysis.ecological_metrics.diversity.pielou_evenness.value` |
| `ecological_metrics.diversity_state` | `analysis.ecological_metrics.state.diversity_resilience.state` |

### `safety_profile` — Safety Markers

| JSON Key | Source Path |
|----------|-------------|
| `safety_profile.dysbiosis_markers` | `analysis.safety_profile.dysbiosis_markers` |
| `safety_profile.any_detected` | Computed: `any(v.abundance > 0.1 for v in markers)` |

### `guild_timepoints` — JavaScript Slider Data

| JSON Key | Source |
|----------|--------|
| `guild_timepoints[0].label` | `"Baseline — " + report_date formatted` |
| `guild_timepoints[0].guilds.fd` | `analysis.bacterial_groups["Fiber Degraders"].abundance / 100` |
| `guild_timepoints[0].guilds.bb` | `analysis.bacterial_groups["Bifidobacteria"].abundance / 100` |
| `guild_timepoints[0].guilds.cf` | `analysis.bacterial_groups["Cross-Feeders"].abundance / 100` |
| `guild_timepoints[0].guilds.bp` | `analysis.bacterial_groups["Butyrate Producers"].abundance / 100` |
| `guild_timepoints[0].guilds.pg` | `analysis.bacterial_groups["Proteolytic Guild"].abundance / 100` |
| `guild_timepoints[0].guilds.md` | `analysis.bacterial_groups["Mucin Degraders"].abundance / 100` |

### `strengths_challenges` — Section 2

| JSON Key | Source |
|----------|--------|
| `strengths_challenges.strengths` | Computed by `compute_strengths_challenges(analysis, circle_scores)` |
| `strengths_challenges.challenges` | Same |
| `strengths_challenges.all_strengths` | Same (untruncated) |
| `strengths_challenges.all_challenges` | Same (untruncated) |
| `strengths_challenges.distinct_areas` | Same |
| `strengths_challenges.bottom_line` | Pre-computed from challenge count |

### `good_news` — Cover Quote

| JSON Key | Source |
|----------|--------|
| `good_news` | `analysis.key_messages.good_news.resilience.non_expert` (or fallback chain) |

### `root_cause_data` — Section 3

| JSON Key | Source |
|----------|--------|
| `root_cause_data.deviation_cards` | `build_root_cause_section()` — LLM + KB |
| `root_cause_data.awareness_chips` | Same |
| `root_cause_data.section_summary` | Same — LLM |
| `root_cause_data.cited_papers` | Elicit API papers cited in LLM output |

### `timeline_phases` — Section 4 Timeline

| JSON Key | Source |
|----------|--------|
| `timeline_phases` | `build_timeline_phases(analysis, formulation)` — 4 phase objects |

### `lifestyle_recommendations` — Section 4 Lifestyle Panel

| JSON Key | Source |
|----------|--------|
| `lifestyle_recommendations` | LLM-generated (Elicit + Claude) |

### `supplement_cards` — Section 5

| JSON Key | Source |
|----------|--------|
| `supplement_cards` | `build_supplement_cards(formulation, analysis)` — one card per delivery format |

### `goal_cards` — Section 6

| JSON Key | Source |
|----------|--------|
| `goal_cards` | `build_goal_cards(questionnaire, formulation, analysis)` — one per health goal |

### `cited_papers` — References Section

| JSON Key | Source |
|----------|--------|
| `cited_papers` | Merged from `root_cause_data.cited_papers` + lifestyle cited papers |

### `protocol_summary` — Metadata

| JSON Key | Source |
|----------|--------|
| `protocol_summary` | From `formulation.formulation.protocol_summary` |
