# Platform UI → JSON Field Mapping

**Purpose:** Maps every UI element in the platform health report to its exact JSON field path in `_platform.json`. Give this document to your frontend/backend developer alongside `platform_payload_schema.json`.

---

## Tab 1: Overview

### Section: Your Gut Health at a Glance

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Summary sentence (1-2 lines) | `overview_tab.gut_health_glance.summary_sentence` | string | "Your gut shows..." |
| Overall score (big number) | `overview_tab.gut_health_glance.overall_score.total` | number | 59.4 |
| Score band (label) | `overview_tab.gut_health_glance.overall_score.band` | string | "Fair" |
| Score band description | `overview_tab.gut_health_glance.overall_score.band_description` | string | "Multiple issues..." |
| Score driver note | `overview_tab.gut_health_glance.overall_score.score_drivers.key_note` | string | "Your score is mainly held back by..." |
| Strongest pillar | `overview_tab.gut_health_glance.overall_score.score_drivers.strongest.label` | string | "Safety Profile" |
| Weakest pillar | `overview_tab.gut_health_glance.overall_score.score_drivers.weakest.label` | string | "Bacterial Group Balance" |

### Section: Score Pillars (5 bars/circles)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Health Association score | `overview_tab.gut_health_glance.pillars.health_association.score` | number | 12.3 |
| Health Association max | `overview_tab.gut_health_glance.pillars.health_association.max` | number | 20 |
| Health Association description | `overview_tab.gut_health_glance.pillars.health_association.description` | string | "This shows how many..." |
| Diversity score | `overview_tab.gut_health_glance.pillars.diversity_resilience.score` | number | 17.3 |
| Diversity max | `overview_tab.gut_health_glance.pillars.diversity_resilience.max` | number | 20 |
| Diversity description | `overview_tab.gut_health_glance.pillars.diversity_resilience.description` | string | "This measures how many..." |
| Metabolic Function score | `overview_tab.gut_health_glance.pillars.metabolic_function.score` | number | 9.5 |
| Metabolic Function max | `overview_tab.gut_health_glance.pillars.metabolic_function.max` | number | 20 |
| Guild Balance score | `overview_tab.gut_health_glance.pillars.guild_balance.score` | number | 10.6 |
| Guild Balance max | `overview_tab.gut_health_glance.pillars.guild_balance.max` | number | 30 |
| Safety Profile score | `overview_tab.gut_health_glance.pillars.safety_profile.score` | number | 9.6 |
| Safety Profile max | `overview_tab.gut_health_glance.pillars.safety_profile.max` | number | 10 |

### Section: What's Happening in Your Gut

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Overall balance label | `overview_tab.whats_happening.overall_balance.label` | string | "Transitional" |
| Overall balance description | `overview_tab.whats_happening.overall_balance.description` | string | "Your gut is in a transitional state..." |
| Diversity label | `overview_tab.whats_happening.diversity_resilience.label` | string | "High" |
| Diversity description | `overview_tab.whats_happening.diversity_resilience.description` | string | "Your gut has a rich variety..." |
| Key strengths (list) | `overview_tab.whats_happening.key_strengths[]` | array of strings | ["Your gut-lining energy producers..."] |
| Key opportunities (list) | `overview_tab.whats_happening.key_opportunities[]` | array of strings | ["Your mucus-layer bacteria..."] |
| Summary sentence | `overview_tab.whats_happening.summary_sentence` | string | "Your gut shows..." |

### Section: Why We Look at This

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Title | `overview_tab.why_we_look_at_this.title` | string | "Why We Look at This" |
| Text | `overview_tab.why_we_look_at_this.text` | string | "Your gut microbiome is..." |

### Section: Metabolic Dials (4 dials)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Intro text | `overview_tab.metabolic_dials.intro_text` | string | "These four metabolic dials..." |
| **Dial 1: Main Fuel** | | | |
| Label | `overview_tab.metabolic_dials.dials.main_fuel.label` | string | "Balanced" |
| State (for color/icon) | `overview_tab.metabolic_dials.dials.main_fuel.state` | string | "balanced" |
| Description | `overview_tab.metabolic_dials.dials.main_fuel.description` | string | "Your bacteria process both..." |
| Context (bigger picture) | `overview_tab.metabolic_dials.dials.main_fuel.context` | string | "A slight lean toward..." |
| **Dial 2: Fermentation** | | | |
| Label | `overview_tab.metabolic_dials.dials.fermentation_efficiency.label` | string | "Sluggish" |
| State | `overview_tab.metabolic_dials.dials.fermentation_efficiency.state` | string | "sluggish" |
| Description | `overview_tab.metabolic_dials.dials.fermentation_efficiency.description` | string | "Without..." |
| Context | `overview_tab.metabolic_dials.dials.fermentation_efficiency.context` | string | "Your gut's production line..." |
| **Dial 3: Gut Lining** | | | |
| Label | `overview_tab.metabolic_dials.dials.gut_lining_dependence.label` | string | "Heavily leaning on mucus" |
| State | `overview_tab.metabolic_dials.dials.gut_lining_dependence.state` | string | "heavy_mucus" |
| **Dial 4: Harsh Byproducts** | | | |
| Label | `overview_tab.metabolic_dials.dials.harsh_byproducts.label` | string | "Balanced" |
| State | `overview_tab.metabolic_dials.dials.harsh_byproducts.state` | string | "balanced" |

### Section: What This Means for Your Life

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Good news — resilience | `overview_tab.what_this_means.good_news.resilience` | string | "Your microbiome shows..." |
| Good news — adaptation | `overview_tab.what_this_means.good_news.adaptation_capacity` | string | "Your gut demonstrates..." |
| Good news — reversibility | `overview_tab.what_this_means.good_news.reversibility` | string | "Strong butyrate production..." |
| Possible impacts (list) | `overview_tab.what_this_means.possible_impacts[]` | array | ["Strong butyrate production may..."] |
| Is something wrong? | `overview_tab.what_this_means.is_something_wrong` | string | "Your microbiome shows..." |
| Can this be fixed? | `overview_tab.what_this_means.can_this_be_fixed` | string | "Your microbiome shows strong..." |

### Section: Why This Matters (static)

| UI Element | JSON Path | Type |
|------------|-----------|------|
| Title | `overview_tab.why_this_matters.title` | string |
| Intro | `overview_tab.why_this_matters.intro` | string |
| What tells us (list) | `overview_tab.why_this_matters.what_tells_us.items[]` | array |
| What doesn't tell us (list) | `overview_tab.why_this_matters.what_doesnt_tell_us.items[]` | array |
| Weather analogy | `overview_tab.why_this_matters.weather_analogy` | string |
| For you (list) | `overview_tab.why_this_matters.for_you.items[]` | array |

---

## Tab 2: Bacterial Groups

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Title | `bacterial_groups_tab.title` | string | "Your Gut Bacteria Groups" |
| Intro text | `bacterial_groups_tab.intro_text` | string | "Your gut bacteria don't work alone..." |

### Per Guild (6 items in `bacterial_groups_tab.guilds[]`)

| UI Element | JSON Path (per guild `[i]`) | Type | Example |
|------------|----------------------------|------|---------|
| Step number | `guilds[i].step` | int | 1 |
| Name | `guilds[i].name` | string | "Fiber Degraders" |
| What they do | `guilds[i].functional_summary` | string | "fiber-processing bacteria that..." |
| Workers (actual) | `guilds[i].capacity.actual_players` | int | 14 |
| Workers (optimal) | `guilds[i].capacity.optimal_players` | int | 51 |
| Workers (min range) | `guilds[i].capacity.min_players` | int | 38 |
| Workers (max range) | `guilds[i].capacity.max_players` | int | 64 |
| Actual % | `guilds[i].capacity.actual_pct` | number | 11.04 |
| Status | `guilds[i].status` | string | "Below range" |
| Healthy range | `guilds[i].healthy_range` | string | "30-50%" |
| Impact explanation | `guilds[i].impact_explanation` | string | "Your fiber-processing team is..." |
| Additional note | `guilds[i].additional_note` | string | "Competitive position: Balanced..." |

---

## Tab 3: Root Causes

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Title | `root_causes_tab.title` | string | "Underlying Causes of Imbalance" |
| Primary diagnosis | `root_causes_tab.primary_diagnosis` | string | "Higher protein, lower fiber..." |
| Disclaimer | `root_causes_tab.how_we_can_tell.disclaimer` | string | "This is a behavioral inference..." |

### Diagnostic Flags (list)

| UI Element | JSON Path (per flag `[i]`) | Type | Example |
|------------|---------------------------|------|---------|
| Flag text | `how_we_can_tell.diagnostic_flags[i].flag` | string | "Mucin Degraders are 4× above..." |
| Severity | `how_we_can_tell.diagnostic_flags[i].severity` | string | "critical" |
| Guild | `how_we_can_tell.diagnostic_flags[i].guild` | string | "Mucin Degraders" |

### Key Insights (3-4 items)

| UI Element | JSON Path (per insight `[i]`) | Type | Example |
|------------|------------------------------|------|---------|
| Title | `key_insights[i].title` | string | "The Narrow Bridge Problem" |
| Explanation | `key_insights[i].explanation` | string | "Cross-feeders are too small..." |

### Conclusion

| UI Element | JSON Path | Type |
|------------|-----------|------|
| What this means | `root_causes_tab.conclusion.what_this_means` | string |
| Reversibility label | `root_causes_tab.conclusion.reversibility.label` | string |
| Reversibility description | `root_causes_tab.conclusion.reversibility.description` | string |
| Timeline | `root_causes_tab.conclusion.reversibility.estimated_timeline` | string |

---

## Tab 4: Vitamins

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Title | `vitamins_tab.title` | string | "Vitamins" |
| Intro | `vitamins_tab.intro` | string | "Your gut bacteria produce..." |
| Robust production (list) | `vitamins_tab.good_news.robust_production[]` | array | ["Folate (B9)", "B-Complex"] |
| Functional roles (list) | `vitamins_tab.good_news.functional_roles[]` | array | ["Rapid gut lining renewal..."] |

### Per Vitamin (4 items in `vitamins_tab.vitamins[]`)

| UI Element | JSON Path (per vitamin `[i]`) | Type | Example |
|------------|------------------------------|------|---------|
| Name | `vitamins[i].display_name` | string | "Folate (B9)" |
| Status | `vitamins[i].status` | string | "Excellent production" |
| Risk level (0-3) | `vitamins[i].risk_level` | int | 0 |
| Role | `vitamins[i].role` | string | "DNA synthesis, red blood cells" |
| Food sources (list) | `vitamins[i].food_sources[]` | array | ["Leafy greens", "Legumes"] |
| Assessment | `vitamins[i].assessment` | string | "Folate risk score 0/3..." |

---

## Tab 5: Action Plan

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Title | `action_plan_tab.title` | string | "Your Personalized Action Plan" |
| Intro | `action_plan_tab.intro` | string | "Based on your microbiome..." |
| Reversibility label | `action_plan_tab.reversibility.label` | string | "High reversibility" |
| Reversibility description | `action_plan_tab.reversibility.description` | string | "Your microbiome has strong..." |
| Timeline | `action_plan_tab.reversibility.estimated_timeline` | string | "8-12 weeks" |
| Reversibility note | `action_plan_tab.reversibility_note` | string | "Microbiome imbalances are..." |
| Next steps (list) | `action_plan_tab.next_steps[]` | array | ["Review your plan", "Complete questionnaire"] |

### Per Step (in `action_plan_tab.steps[]`)

| UI Element | JSON Path (per step `[i]`) | Type | Example |
|------------|---------------------------|------|---------|
| Step number | `steps[i].step_number` | int | 1 |
| Priority level | `steps[i].priority_level` | string | "CRITICAL" |
| Title | `steps[i].title` | string | "Restart the Amplifier" |
| Guild name | `steps[i].guild_display` | string | "Bifidobacteria" |
| Action (expand/reduce) | `steps[i].action` | string | "expand" |
| Why | `steps[i].why` | string | "Bifidobacteria produce lactate..." |
| How | `steps[i].how` | string | "TBD — from supplement pipeline" |
| Timeline | `steps[i].timeline` | string | "4-6 weeks to detect..." |
| Current workers | `steps[i].current_players` | int | 0 |
| Target workers (min) | `steps[i].target_players_min` | int | 3 |
| Target workers (max) | `steps[i].target_players_max` | int | 13 |
| Current % | `steps[i].current_pct` | number | 0.0 |
| Target range | `steps[i].target_range` | string | "2-10%" |

### Forecast (in `action_plan_tab.forecast[]`)

| UI Element | JSON Path (per forecast `[i]`) | Type | Example |
|------------|-------------------------------|------|---------|
| Guild name | `forecast[i].guild_display` | string | "Bifidobacteria" |
| Current workers | `forecast[i].current_players` | int | 0 |
| Target workers (min) | `forecast[i].target_players_min` | int | 3 |
| Target workers (max) | `forecast[i].target_players_max` | int | 13 |
| Direction arrow | `forecast[i].direction` | string | "↑" |

---

## Metadata (available on all pages)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Sample ID | `metadata.sample_id` | string | "1421500904635" |
| Report date | `metadata.report_date` | string | "2026-02-17" |
| Schema version | `metadata.platform_schema_version` | string | "1.0" |

---

## Notes for Developer

1. **All text fields are non-expert** — safe to display directly to clients
2. **`state` fields** (e.g., `main_fuel.state = "balanced"`) can be used for color coding: `carb_driven` = green, `balanced` = amber, `protein_driven` = red
3. **`priority_level`** in action plan steps uses: `CRITICAL` (red), `1A` (orange), `1B` (yellow), `Monitor` (green)
4. **Guild order** in `bacterial_groups_tab.guilds[]` is fixed: Fiber → Bifidobacteria → Cross-Feeders → Butyrate → Proteolytic → Mucin
5. **Workers scale** (100-player): optimal always sums to 100, actual varies
6. **`steps[i].how`** is currently "TBD" — will be populated when supplement prediction pipeline is automated
