# Health Report UI → JSON Field Mapping

**Purpose:** Maps every UI element in the client-facing health report to its exact JSON field path in `health_report_interpretations_{sample_id}.json`. Give this document to your frontend developer alongside the JSON file.

**Conventions:**
- `ui_text.*` — static editorial text stored in the JSON (labels, titles, intros, fallback messages)
- `[i]` — index into an array
- **FRONTEND LOGIC** — value must be computed by the frontend from available JSON data; no JSON field needed
- **FRONTEND LOGIC (pre-computable)** — Python pre-computes this, but the frontend can also derive it

---

## Metadata (available everywhere)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Sample ID | `sample_id` | string | `"1421266404096"` |
| Report date (ISO) | `report_date` | string | `"2026-03-06"` |
| Report date (display) | **FRONTEND LOGIC** — format `report_date` as `"DD Month YYYY"` | — | `"06 March 2026"` |
| Schema version | `schema_version` | string | `"3.0"` |

---

## Cover Page

### Brand & Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Brand name | `ui_text.cover.brand` | string | `"NB1 Health · Microbiome Health"` |
| Report tag prefix | `ui_text.cover.report_tag_prefix` | string | `"Personalised Report ·"` |
| Report tag date | `report_date` (formatted) | string | `"06 March 2026"` |
| Eyebrow text | `ui_text.cover.eyebrow` | string | `"Your gut health, explained simply"` |
| Headline | `ui_text.cover.headline` | string | `"Inside Your Gut"` |
| Subtitle paragraph | `ui_text.cover.subtitle` | string | `"What your microbiome is telling us…"` |

### Overall Score Dial

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Score number | `overall_score.total` | number | `68.3` |
| Score denominator | `ui_text.cover.score_denominator` | string | `"/ 100"` |
| Dial fill color | **FRONTEND LOGIC** — `≥65` → green, `≥40` → amber, `<40` → red | — | — |
| Dial fill offset | **FRONTEND LOGIC** — `283 × (1 − score/100)` | — | — |
| Score summary text | `score_summary` | string (HTML) | `"Your overall score is <strong>68.3</strong>…"` |
| Key driver note | `overall_score.score_drivers.key_note` | string | `"Your score is mainly held back by…"` |

### Score Pillars (5 chips)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Pillar label — Health Association | `ui_text.pillar_labels.health_association` | string | `"Health"` |
| Pillar label — Diversity | `ui_text.pillar_labels.diversity_resilience` | string | `"Diversity"` |
| Pillar label — Metabolic | `ui_text.pillar_labels.metabolic_function` | string | `"Metabolic"` |
| Pillar label — Guild Balance | `ui_text.pillar_labels.guild_balance` | string | `"Guild Balance"` |
| Pillar label — Safety | `ui_text.pillar_labels.safety_profile` | string | `"Safety"` |
| Pillar score (per pillar key `k`) | `overall_score.pillars.{k}.score` | number | `12.3` |
| Pillar max (per pillar key `k`) | `overall_score.pillars.{k}.max` | number | `20` |
| Pillar bar fill % | **FRONTEND LOGIC** — `score / max × 100` | — | — |
| Pillar bar color | **FRONTEND LOGIC** — same color scale as score dial | — | — |

### Profile Snapshot

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| "Profile" label | `ui_text.cover.stat_labels.profile` | string | `"Profile"` |
| Sex · Age value | `profile.sex` + `profile.age` | string + number | `"Male · 34"` |
| "Diet" label | `ui_text.cover.stat_labels.diet` | string | `"Diet"` |
| Diet value | `profile.diet` | string | `"Omnivore"` |
| "Stress" label | `ui_text.cover.stat_labels.stress` | string | `"Stress"` |
| Stress value | `profile.stress` | number | `7` |
| Stress denominator | `ui_text.cover.stress_denominator` | string | `"/ 10"` |
| "Sleep" label | `ui_text.cover.stat_labels.sleep` | string | `"Sleep"` |
| Sleep value | `profile.sleep` | number | `6` |
| Sleep denominator | `ui_text.cover.sleep_denominator` | string | `"/ 10"` |
| "Sensitivity" label | `ui_text.cover.stat_labels.sensitivity` | string | `"Sensitivity"` |
| Sensitivity value | `profile.sensitivity` | string | `"Medium"` |
| "Goals" label | `ui_text.cover.stat_labels.goals` | string | `"Goals"` |
| Goals value | `profile.goals[]` joined with ` · ` | array of strings | `["Energy", "Digestion"]` |

### Progress Banner

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Banner label | `ui_text.progress_banner.label` | string | `"Your progress so far"` |
| Timepoint label (slider) | `guild_timepoints[i].label` | string | `"Baseline — Mar 2026"` |
| Score label | `ui_text.progress_banner.score_label` | string | `"Microbiome score"` |
| Score value per timepoint | `guild_timepoints[i].score` (if present) or computed | number | `68.3` |
| Slider ticks | `guild_timepoints[]` labels | array | — |

---

## Section 1: The Big Picture

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section1.sec_label` | string | `"Section 1 · The Big Picture"` |
| Title | `ui_text.section1.title` | string | `"What is happening in your gut?"` |
| Intro paragraph | `ui_text.section1.intro` | string | `"Your gut is home to trillions…"` |

### Four Circle Dials

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Gut Lining label | `ui_text.section1.dials.gut_lining.label` | string | `"Gut Lining\nProtection"` |
| Gut Lining description | `ui_text.section1.dials.gut_lining.description` | string | `"How well your gut wall…"` |
| Gut Lining score | `circle_scores.gut_lining` | number | `42` |
| Inflammation label | `ui_text.section1.dials.inflammation.label` | string | `"Inflammation\nControl"` |
| Inflammation description | `ui_text.section1.dials.inflammation.description` | string | `"How favorable your microbiome…"` |
| Inflammation score | `circle_scores.inflammation` | number | `38` |
| Fiber Processing label | `ui_text.section1.dials.fiber_processing.label` | string | `"Fiber\nProcessing"` |
| Fiber Processing description | `ui_text.section1.dials.fiber_processing.description` | string | `"How efficiently your bacteria…"` |
| Fiber Processing score | `circle_scores.fiber_processing` | number | `31` |
| Bifidobacteria label | `ui_text.section1.dials.bifidobacteria.label` | string | `"Bifidobacteria\nPresence"` |
| Bifidobacteria description | `ui_text.section1.dials.bifidobacteria.description` | string | `"The abundance of your…"` |
| Bifidobacteria score | `circle_scores.bifidobacteria` | number | `55` |
| Dial SVG fill offset | **FRONTEND LOGIC** — `232 × (1 − score/100)` | — | — |
| Dial color | **FRONTEND LOGIC** — `≥75` → green, `≥50` → amber, `<50` → red | — | — |

### Evolution Over Time Slider

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Slider label | `ui_text.section1.evolution_label` | string | `"Evolution over time"` |
| Timepoint labels (ticks) | `guild_timepoints[i].label` | array of strings | `["Baseline — Mar 2026"]` |
| Guild values per timepoint | `guild_timepoints[i].guilds.{fd\|bb\|cf\|bp\|pg\|md}` | number (fraction 0–1) | `0.131` |

### Guild Bars (6 bars)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Guild bar legend text | `ui_text.section1.guild_bar_legend` | string | `"Each bacterial group has a healthy target range…"` |
| Guild name | `bacterial_groups.{name}` key — `{name}` is the **full human-readable string**, not a slug (e.g., `"Fiber Degraders"`, `"Butyrate Producers"`, `"Proteolytic Guild"`, `"Mucin Degraders"`) | string | `"Fiber Degraders"` |
| Guild abundance % | `bacterial_groups.{name}.abundance` | number | `13.1` |
| Guild status | `bacterial_groups.{name}.status` | string | `"Below range"` |
| Guild note (bar tooltip) | `bacterial_groups.{name}.client_interpretation` (fallback: `.evenness_status`) | string | `"Your fiber-processing bacteria are low…"` |
| Badge text | **FRONTEND LOGIC** — derived from `status` + `abundance` + guild type (beneficial vs contextual) | — | `"⚠ Below range · 13.1%"` |
| Bar fill width % | **FRONTEND LOGIC** — `min(abundance / BAR_MAX × 100, 100)` | — | — |
| Healthy range position | **FRONTEND LOGIC** — derived from guild thresholds (see Notes §1) | — | — |
| Bar CSS class (color) | **FRONTEND LOGIC** — derived from `status` + guild type | — | — |

> **Notes §1 — Badge text logic by guild type:**
> - **Beneficial guilds** (fd, bb, cf, bp): `absent` if 0%, `"⚠ Below range · X%"` if below, `"✓ Healthy · X%"` if ok, `"↑ High · X%"` if above
> - **Contextual guilds** (pg, md): `"✓ Controlled · X%"` if ok, `"↑ Elevated · X%"` if above/critical

> **Key lookup note:** `bacterial_groups` keys are full display-name strings. Short slugs (`fd`, `bb`, `cf`, `bp`, `pg`, `md`) only appear in `guild_timepoints[i].guilds.*` and `guild_thresholds.*`.

---

## Section 2: Strengths & Challenges

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section2.sec_label` | string | `"Section 2 · Your Profile"` |
| Title | `ui_text.section2.title` | string | `"Your strengths and areas to improve"` |
| Intro paragraph | `ui_text.section2.intro` | string | `"Every microbiome tells a story…"` |

### Strengths Card

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Card header label | `ui_text.section2.strengths_label` | string | `"Working in your favour"` |
| Card header title | `ui_text.section2.strengths_title` | string | `"Your Strengths"` |
| Strength icon (per item `[i]`) | `strengths_challenges.strengths[i].icon` | string | `"✨"` |
| Strength title | `strengths_challenges.strengths[i].title` | string | `"Healthy bifidobacteria presence"` |
| Strength text | `strengths_challenges.strengths[i].text` | string | `"Your Bifidobacteria are at 5.1%…"` |
| Empty state message | `ui_text.section2.strengths_empty` | string | `"No clear strengths identified…"` |

### Challenges Card

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Card header label | `ui_text.section2.challenges_label` | string | `"Needs attention"` |
| Card header title | `ui_text.section2.challenges_title` | string | `"Key Challenges"` |
| Challenge icon (per item `[i]`) | `strengths_challenges.challenges[i].icon` | string | `"🚨"` |
| Challenge title | `strengths_challenges.challenges[i].title` | string | `"Dysbiosis marker detected"` |
| Challenge text | `strengths_challenges.challenges[i].text` | string | `"The following dysbiosis-associated bacteria…"` |
| Challenge severity | `strengths_challenges.challenges[i].severity` | string | `"critical"` / `"high"` / `"moderate"` |
| Dot color class | **FRONTEND LOGIC** — `critical`/`high` → red dot, `moderate` → amber dot | — | — |
| Empty state message | `ui_text.section2.challenges_empty` | string | `"No significant challenges identified…"` |

### Bottom Line

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Bottom line quote | `ui_text.section2.bottom_line` | string | `"The main issue is not total collapse…"` |

---

## Section 3: Root Causes

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section3.sec_label` | string | `"Section 3 · The Story Behind Your Results"` |
| Title | `ui_text.section3.title` | string | `"What is behind this imbalance?"` |
| Intro paragraph | `ui_text.section3.intro` | string | `"We looked at everything you shared…"` |
| Section summary block | `root_cause_data.section_summary` | string | `"Your gut pattern reflects…"` |

### Metrics Strip (top data pills)

| UI Element | JSON Path (per item `[i]`) | Type | Example |
|------------|---------------------------|------|---------|
| Icon | `root_cause_data.metrics_strip[i].icon` | string | `"🛡️"` |
| Label | `root_cause_data.metrics_strip[i].client_label` | string | `"Mucus-Layer Bacteria"` |
| Value | `root_cause_data.metrics_strip[i].value_str` | string | `"38.4%"` |
| Target prefix | `ui_text.section3.metrics_target_prefix` | string | `"Target: "` |
| Target range | `root_cause_data.metrics_strip[i].range_str` | string | `"1–4%"` |
| Impact level (color) | `root_cause_data.metrics_strip[i].impact` | string | `"crit"` / `"high"` / `"low"` |

### Factor Cards

| UI Element | JSON Path (per card `[i]`) | Type | Example |
|------------|---------------------------|------|---------|
| Icon | `root_cause_data.factor_cards[i].icon` | string | `"🫧"` |
| Label | `root_cause_data.factor_cards[i].label` | string | `"Bloating & Digestive Discomfort"` |
| Subtitle | `root_cause_data.factor_cards[i].subtitle` | string | `"Affects 5 bacterial imbalances"` |
| Evidence badge | `root_cause_data.factor_cards[i].evidence_label` | string | `"Well established"` |
| Guild impact dot label | `root_cause_data.factor_cards[i].guild_impacts[j].client_label` | string | `"Mucus-Layer Bacteria"` |
| Guild impact level | `root_cause_data.factor_cards[i].guild_impacts[j].impact` | string | `"crit"` / `"high"` / `"low"` |
| Explanation text | `root_cause_data.factor_cards[i].explanation` | string | `"Bloating is a direct symptom…"` |
| "What does science say" label | `ui_text.section3.science_label` | string | `"What does science say"` |
| KB science text | `root_cause_data.factor_cards[i].kb_text` | string | `"Bloating is a direct symptom…"` |
| N guilds affected | `root_cause_data.factor_cards[i].guilds_affected_count` | number | `5` |
| Guilds affected suffix | `ui_text.section3.guilds_affected_suffix` | string | `"/ 4 guilds affected"` |

### Cascade Diagram

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Cascade section label | `ui_text.section3.cascade_label` | string | `"How these factors connect to your results"` |
| Cascade explanation text | `ui_text.section3.cascade_explanation` | string | `"Each factor pill shows the direction…"` |
| Factor pill icon | `root_cause_data.factor_cards[i].icon` | string | `"🫧"` |
| Factor pill label | `root_cause_data.factor_cards[i].label` | string | `"Bloating & Digestive Discomfort"` |
| Factor directionality arrow | **FRONTEND LOGIC** — `"bidirectional"` → `↔`, otherwise → `→` (from `root_cause_data.factor_cards[i].directionality`) | — | — |
| Center arrow label | `ui_text.section3.cascade_arrow_label` | string | `"factors"` |
| Guild pill label | `root_cause_data.cascade_guilds[i].client_label` | string | `"Mucus-Layer Bacteria"` |
| Guild pill value | `root_cause_data.cascade_guilds[i].value_str` | string | `"38.4%"` |
| Guild pill emojis | `root_cause_data.cascade_guilds[i].driving_factor_emojis[]` | array of strings | `["🫧"]` |
| Guild pill color class | `root_cause_data.cascade_guilds[i].impact` | string | `"crit"` / `"high"` / `"low"` |

### Deviation Cards (legacy layout — shown when no factor_cards)

| UI Element | JSON Path (per card `[i]`) | Type | Example |
|------------|---------------------------|------|---------|
| "Why this happened —" prefix | `ui_text.section3.why_prefix` | string | `"Why this happened — "` |
| Narrative text | `root_cause_data.deviation_cards[i].narrative` | string | — |
| Health meaning fallback | `root_cause_data.deviation_cards[i].health_meaning` | string | — |
| Driver icon | `root_cause_data.deviation_cards[i].kb_drivers[j].icon` | string | — |
| Driver label | `root_cause_data.deviation_cards[i].kb_drivers[j].label` | string | — |
| Driver explanation | `root_cause_data.deviation_cards[i].kb_drivers[j].text` | string | — |
| Driver KB science text | `root_cause_data.deviation_cards[i].kb_drivers[j].kb_text` | string | — |
| Driver evidence label | `root_cause_data.deviation_cards[i].kb_drivers[j].evidence_label` | string | — |
| Takeaway line | `root_cause_data.deviation_cards[i].summary_line` | string | — |

### Awareness Zone (questionnaire lifestyle chips)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Zone label | `ui_text.section3.awareness_label` | string | `"From your questionnaire"` |
| Zone intro | `ui_text.section3.awareness_intro` | string | `"You mentioned these factors…"` |
| Chip icon (per chip `[i]`) | `root_cause_data.awareness_chips[i].icon` | string | `"😴"` |
| Chip label | `root_cause_data.awareness_chips[i].domain_label` | string | `"Sleep quality"` |
| Chip badge | `ui_text.section3.awareness_chip_badge` | string | `"You reported this"` |
| Chip body text | `root_cause_data.awareness_chips[i].summary_text` | string | `"Poor sleep is linked to…"` |

### Empty State

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Empty message | `ui_text.section3.empty_message` | string | `"✅ Your microbiome looks healthy…"` |

---

## Section 4: The Road Ahead

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section4.sec_label` | string | `"Section 4 · The Road Ahead"` |
| Title | `ui_text.section4.title` | string | `"How do we turn this around?"` |
| Intro paragraph | `ui_text.section4.intro` | string | `"Your personalized formula is designed…"` |

### Timeline Phases (left column)

| UI Element | JSON Path (per phase `[i]`) | Type | Example |
|------------|----------------------------|------|---------|
| "Weeks" prefix | `ui_text.section4.weeks_prefix` | string | `"Weeks "` |
| Period (weeks) | `timeline_phases[i].weeks` | string | `"1–4"` |
| Phase title | `timeline_phases[i].title` | string | `"🌱 Settling in"` |
| Phase body | `timeline_phases[i].body` | string | `"The probiotic strains…"` |
| Phase dot color | `timeline_phases[i].color` | string | `"#3A6EA8"` |

### Lifestyle Panel (right column)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Panel label | `ui_text.section4.lifestyle_label` | string | `"While your formula works its science"` |
| Rec emoji (per item `[i]`) | `lifestyle_recommendations[i].emoji` | string | `"🌾"` |
| Rec title | `lifestyle_recommendations[i].title` | string | `"Prioritize diverse fiber sources"` |
| Rec text | `lifestyle_recommendations[i].text` | string | `"Your fiber-processing bacteria are…"` |

---

## Section 5: Your Formula

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section5.sec_label` | string | `"Section 5 · Your Formula"` |
| Title | `ui_text.section5.title` | string | `"What you are taking and why"` |
| Intro paragraph | `ui_text.section5.intro` | string | `"Your formula is organised by when…"` |
| Empty state message | `ui_text.section5.empty_message` | string | `"Formulation data not available…"` |

### Timing Group Banners

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Morning label | `ui_text.section5.timing_morning` | string | `"Morning"` |
| Morning emoji | `ui_text.section5.timing_morning_emoji` | string | `"🌅"` |
| Evening label | `ui_text.section5.timing_evening` | string | `"Evening"` |
| Evening emoji | `ui_text.section5.timing_evening_emoji` | string | `"🌙"` |
| Unit count badge | **FRONTEND LOGIC** — count cards per timing group | — | `"3 units"` |
| Timing group of each card | `supplement_cards[i].timing_group` | string | `"morning"` / `"evening"` |

### Supplement Cards (per card `[i]`)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Card number | `supplement_cards[i].num` | number | `1` |
| Card color | `supplement_cards[i].color` | string | `"#1E1E2A"` |
| Card name | `supplement_cards[i].name` | string | `"Probiotic Capsule"` |
| Card timing display | `supplement_cards[i].timing` | string | `"🌅 Every morning"` |
| "Why you're taking it" label | `ui_text.section5.why_label` | string | `"Why you're taking it:"` |
| Why text | `supplement_cards[i].why` | string | `"This probiotic blend helps…"` |
| Polyphenol warning text | `ui_text.section5.polyphenol_warning` | string | `"Take with breakfast or on full stomach."` |
| Polyphenol warning visibility | **FRONTEND LOGIC** — show if `supplement_cards[i].key` contains `"polyphenol"` AND `timing_group == "morning"` | — | — |
| Pill name (per pill `[j]`) | `supplement_cards[i].pills[j].name` | string | `"Lactobacillus plantarum 299v"` |
| Pill dose | `supplement_cards[i].pills[j].dose` | string | `"10B CFU"` |
| "Supports" label | `ui_text.section5.supports_label` | string | `"Supports"` |
| Support chip text (per chip `[j]`) | `supplement_cards[i].supports[j]` | string | `"Gut-Brain"` |

---

## Section 6: Your Goals

### Section Header

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.section6.sec_label` | string | `"Section 6 · Your Goals"` |
| Title | `ui_text.section6.title` | string | `"How it aligns with your health goals"` |
| Intro paragraph | `ui_text.section6.intro` | string | `"Everything in this protocol connects…"` |
| Empty state message | `ui_text.section6.empty_message` | string | `"Questionnaire data not available…"` |

### Goal Cards (per card `[i]`)

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Emoji | `goal_cards[i].emoji` | string | `"⚡"` |
| Inferred tag text | `ui_text.section6.inferred_tag` | string | `"Also addressed by your formula"` |
| Inferred tag visibility | `goal_cards[i].inferred` | boolean | `false` |
| Goal title | `goal_cards[i].title` | string | `"Boost energy and reduce fatigue"` |
| Mechanism text | `goal_cards[i].mechanism` | string | `"The gut microbiome influences energy…"` |
| "Your formula:" prefix | `ui_text.section6.formula_prefix` | string | `"Your formula:"` |
| Formula link text | `goal_cards[i].formula_link` | string | `"Your formula targets this through…"` |

---

## References

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Section label | `ui_text.references.sec_label` | string | `"References"` |
| Title | `ui_text.references.title` | string | `"Scientific References"` |
| Intro text | `ui_text.references.intro` | string | `"The following peer-reviewed papers…"` |
| Paper authors | `cited_papers[i].authors` | string | `"Smith, J., & Jones, A."` |
| Paper year | `cited_papers[i].year` | number | `2023` |
| Paper title | `cited_papers[i].title` | string | `"Gut microbiome and…"` |
| Paper journal | `cited_papers[i].venue` | string | `"Nature Medicine"` |
| Paper DOI | `cited_papers[i].doi` | string | `"10.1038/…"` |
| APA formatted citation | **FRONTEND LOGIC** — format from above fields as APA: `Authors (Year). Title. *Journal*. DOI` | — | — |

---

## Footer

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Brand name | `ui_text.footer.brand` | string | `"NB1 Health"` |
| Disclaimer text | `ui_text.footer.disclaimer` | string | `"This report is for informational purposes only…"` |
| Date | `report_date` (formatted) | string | `"06 March 2026"` |

---

## Pathway Diagram (Interactive JS section in Section 1)

This section is fully interactive and driven by `guild_timepoints`. All text content is now available in `ui_text`.

| UI Element | JSON Path | Type | Example |
|------------|-----------|------|---------|
| Guild description (tooltip, per key) | `ui_text.guild_descriptions.{fd\|bb\|cf\|bp\|pg\|md}` | string | `"First in the fiber chain…"` |
| Q&A pill question (per key) | `ui_text.qdata.{fd\|substrate\|bp\|pg\|md\|scfa}.q` | string | `"Why is fiber important?"` |
| Q&A pill answer (per key) | `ui_text.qdata.{fd\|substrate\|bp\|pg\|md\|scfa}.a` | string | `"Dietary fiber is the main fuel…"` |
| Info popup title (per guild key) | `ui_text.info_data.{fd\|bb\|cf\|bp\|pg\|md}.title` | string | `"🌾 Fiber Degraders — why they matter"` |
| Info popup row label (per row `[j]`) | `ui_text.info_data.{key}.rows[j].label` | string | `"Healthy"` / `"Too low"` / `"High"` |
| Info popup row text | `ui_text.info_data.{key}.rows[j].text` | string | `"Breaking down plant fibers efficiently…"` |
| Info popup row tag (for styling) | `ui_text.info_data.{key}.rows[j].tag` | string | `"ok"` / `"low"` / `"high"` / `"inv"` |
| Guild values per timepoint | `guild_timepoints[i].guilds.{fd\|bb\|cf\|bp\|pg\|md}` | number (0–1 fraction) | `0.131` |
| Guild status (ok/below/above/critical) | **FRONTEND LOGIC** — computed from guild value + thresholds | — | — |
| Bar fill, colors, SVG animations | **FRONTEND LOGIC** — rendering | — | — |
| Dynamic callout messages | **FRONTEND LOGIC** — computed from combination of guild statuses | — | — |
| Dominance state labels | **FRONTEND LOGIC** — computed from guild values | — | — |
| Output badges in pathway | **FRONTEND LOGIC** — computed from guild values | — | — |
| Delta labels in banner | **FRONTEND LOGIC** — computed from current vs previous timepoint | — | — |

---

## Notes for Developer

1. **All `ui_text.*` fields are editorial text** — safe to display directly. Never contains HTML (use as plain text).
2. **`score_summary`** is the one exception — it contains inline `<strong>` HTML tags for bold formatting. Render as HTML or strip tags.
3. **Guild thresholds** are now in the JSON at `guild_thresholds.*`. Keys are short guild IDs (`fd`, `bb`, `cf`, `bp`, `pg`, `md`) matching `guild_timepoints[i].guilds.{key}`. Each entry has: `display_name` (string), `min` (number), `max` (number), `track_max` (number), `invert` (boolean). All values are decimal fractions (0–1). The HTML report's inline `GCFG` constant uses these same values.
4. **Badge text** for guild bars must be computed from `bacterial_groups.{name}.status` + `abundance`. Full logic in Note 10 below; quick reference in Notes §1 (Guild Bars section).
5. **APA citations** are generated from raw fields in `cited_papers[]`. The frontend must format them as: `Authors (Year). Title. *Journal*. https://doi.org/DOI`.
6. **Guild order** for bars: Fiber Degraders → Bifidobacteria → Cross-Feeders → Butyrate Producers → Proteolytic Guild → Mucin Degraders (or as they appear in `bacterial_groups`).
7. **Timing group fallback**: if `supplement_cards[i].timing_group` is absent, infer from `supplement_cards[i].timing` — if it contains `"evening"`, `"bed"`, or `"🌙"` → evening; otherwise → morning.
8. **`timeline_phases[i].color`** is already in the JSON — do not hardcode phase colors.
9. **`supplement_cards[i].color`** is already in the JSON — do not hardcode card colors.
10. **Badge text logic — full reference (applies to all guild UI):**
    - **Beneficial guilds** (`"Fiber Degraders"`, `"Bifidobacteria"`, `"Cross-Feeders"`, `"Butyrate Producers"`):
      - `abundance == 0` → `"Absent"`, CSS class `badge-absent`
      - `status` = `"Below range"` → `"⚠ Below range · X.X%"`, CSS class `badge-critical`
      - `status` = `"Above range"` → `"↑ High · X.X%"`, CSS class `badge-above`
      - otherwise (within range) → `"✓ Healthy · X.X%"`, CSS class `badge-ok`
    - **Contextual guilds** (`"Proteolytic Guild"`, `"Mucin Degraders"`):
      - `status` = `"Above range"` → `"↑ Elevated · X.X%"`, CSS class `badge-critical`
      - otherwise → `"✓ Controlled · X.X%"`, CSS class `badge-ok`
    - `BAR_MAX = 55.0` — bar fill % = `min(abundance / 55.0 × 100, 100)`
    - `abundance` values are percentages (0–100); `guild_thresholds` values are fractions (0–1) — do not mix.
