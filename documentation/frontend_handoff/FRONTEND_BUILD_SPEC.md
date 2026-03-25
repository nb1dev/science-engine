# Health Report — Frontend Build Spec

**Date:** 24 March 2026   
**Reference samples:** 7 JSONs + 7 HTML files in this folder  
**Validated against:** all 7 HTML reference implementations

---

## How it works

The backend outputs one file per client: `health_report_interpretations_{sample_id}.json`  
The frontend reads this file and renders the full report.

---

## Reference samples

| Sample ID |
|---|
| `1421266404096` |
| `1421029282376` |
| `1421819436544` |
| `1421425343541` |
| `1421773212865` |
| `1421093249814` |
| `1421504848853` |

---

## Global colour tokens

| Token | Hex | Used for |
|---|---|---|
| `--sand` | `#F5F0E8` | Alternating section background |
| `--warm` | `#FDFAF5` | Card background |
| `--dark` | `#1E1E2A` | Cover / driver factor |
| `--mid` | `#4A4858` | Body text |
| `--soft` | `#9A95A8` | Labels, secondary text |
| `--rule` | `#E4DDD0` | Borders |
| `--green` | `#2E8B6E` | Healthy / good |
| `--green-lt` | `#E8F5F1` | Green background tint |
| `--amber` | `#C97C2A` | Caution / moderate |
| `--amber-lt` | `#FBF1E4` | Amber background tint |
| `--red` | `#C24B3A` | Critical / bad |
| `--red-lt` | `#FCECEA` | Red background tint |
| `--blue` | `#3A6EA8` | Info / research supported |
| `--blue-lt` | `#EAF0F8` | Blue background tint |
| `--purple` | `#6B5EA8` | Inferred / bidirectional |
| `--purple-lt` | `#F0EEF8` | Purple background tint |

---

## COVER PAGE

### Score dial (large, on dark cover background)

| HTML element | JSON key | Notes |
|---|---|---|
| Dial fill | `overall_score.total` | SVG circle r=45, circumference=283. `stroke-dashoffset = 283 × (1 − total/100)` |
| Dial stroke colour | `overall_score.total` | ≥65 → `#2E8B6E` · ≥40 → `#C97C2A` · <40 → `#C24B3A` |
| Number inside dial | `overall_score.total` | |
| Summary text | `score_summary` | Set as `innerHTML` — contains `<strong>` tags |
| Key note (below summary) | `overall_score.score_drivers.key_note` | Small grey text directly below score_summary, same block |

### Pillar chips (5 bars below the dial)

Source: `overall_score.pillars` — always 5 keys:

| Pillar key | Display name |
|---|---|
| `health_association` | Health |
| `diversity_resilience` | Diversity |
| `metabolic_function` | Metabolic |
| `guild_balance` | Guild Balance |
| `safety_profile` | Safety |

Per chip: display name · `score / max` text · mini bar at `score/max × 100%` width  
Bar colour: ≥75% → green · ≥50% → amber · <50% → red (based on `score/max × 100`)

### Client profile strip (bottom of cover)

The cover-bottom strip shows stats as individual `.cover-stat` blocks:

| HTML stat label | Derived from | Notes |
|---|---|---|
| Profile | `profile.sex` + `profile.age` | Rendered as `"{sex} · {age}"` e.g. "Male · 37" — **if `profile.sex` is null, omit this stat entirely** |
| Diet | `profile.diet` | Omit if `null` |
| Stress | `profile.stress` | Display as `N / 10` |
| Sleep | `profile.sleep` | Display as `N / 10` |
| Sensitivity | `profile.sensitivity` | |
| Goals | `profile.goals[]` | Array of strings · render as inline text separated by `·` |

**Note:** `profile.first_name` is used in the HTML `<title>` tag only (`Inside Your Gut — {first_name}'s Report`), not in the visible body.

### Progress banner (time slider, bottom of cover)

Source: `guild_timepoints[]`

| HTML element | JSON key |
|---|---|
| Timepoint label | `guild_timepoints[i].label` |
| Score at timepoint | `guild_timepoints[i].score` |
| Guild fractions | `guild_timepoints[i].guilds` → keys: `fd bb cf bp pg md` (fractions, not %) |

- `length === 1` → show "Your baseline measurement" as delta text, no slider interaction
- `length > 1` → range slider with delta vs previous timepoint

---

## SECTION 1 — The Big Picture

### Four circle score dials

Source: `circle_scores`

| Key | Display title | Description |
|---|---|---|
| `gut_lining` | Gut Lining Protection | How well your gut wall is protected and maintained |
| `inflammation` | Inflammation Control | How favorable your microbiome is for keeping inflammatory pressure low |
| `fiber_processing` | Fiber Processing | How efficiently your bacteria ferment and process fiber |
| `bifidobacteria` | Bifidobacteria Presence | The abundance of your beneficial Bifidobacteria |

All values 0–100. SVG circle r=37, circumference=232.  
`stroke-dashoffset = 232 × (1 − score/100)`  
Colour: ≥75 → green · ≥50 → amber · <50 → red

### Time slider (Section 1)

Same `guild_timepoints[]` data as cover banner. Dark rounded box above the guild bars.

### Guild bars

Source: `bacterial_groups` — object with 6 guild entries. **Key order varies per sample — do not hard-code order.**

Per guild bar:

| HTML element | JSON key | Notes |
|---|---|---|
| Guild name | key name (e.g. `"Fiber Degraders"`) | `.gbar-name` |
| Status badge | see status table below | `.gbar-badge` |
| Bar fill width | `.abundance` / track max | `.gbar-fill` |
| Healthy range zone | hardcoded per guild | `.gbar-range` — shaded overlay on bar |
| Note text | `.client_interpretation` | `.gbar-note` below bar |

**Status → badge + colour (confirmed from HTML JS):**

The HTML JavaScript explicitly maps `'below'` → `'critical'` (red) for all beneficial guilds (non-inverted). There is no blue "below" state for beneficial guilds in the rendered HTML.

| Guild type | Status | Badge text | Card background | Bar colour |
|---|---|---|---|---|
| Beneficial | `"Within range"` | `✓ Healthy · {N}%` | `--green-lt` | green |
| Beneficial | `"Below range"` | `⚠ Below range · {N}%` | `--red-lt` | red (not blue) |
| Beneficial | `"Above range"` | `↑ High · {N}%` | `--amber-lt` | amber |
| Contextual | `"Within range"` | `✓ Controlled · {N}%` | `--green-lt` | green |
| Contextual | `"Above range"` | `↑ Elevated · {N}%` | `--red-lt` | red |

Beneficial guilds: Fiber Degraders, Bifidobacteria, Butyrate Producers, Cross-Feeders  
Contextual guilds: Mucin Degraders, Proteolytic Guild

Reference ranges and bar track maxes:

| Guild | Healthy range | Track max |
|---|---|---|
| Fiber Degraders | 30–50% | 55% |
| Bifidobacteria | 2–10% | 22% |
| Cross-Feeders | 6–12% | 30% |
| Butyrate Producers | 10–25% | 32% |
| Proteolytic Guild | 1–5% | 22% |
| Mucin Degraders | 1–4% | 14% |

Bar fill %: `abundance / trackMax × 100`, capped at 100%.  
Range zone position: `left = rangeMin/trackMax × 100%`, `width = (rangeMax-rangeMin)/trackMax × 100%`

### Metabolic dials (4 state indicators)

Source: `metabolic_dials`

| Key | Green state | Neutral state | Red state |
|---|---|---|---|
| `main_fuel` | `carb_driven` | `balanced` | `protein_driven` |
| `fermentation_efficiency` | `efficient` | `ok` | `sluggish` |
| `mucus_dependency` | `diet_fed` | `backup` | `heavy_mucus` |
| `putrefaction_pressure` | `scfa_dominant` | `balanced` | `protein_pressure` |

Display: `.label` as text, `.state` determines colour.

### Ecological metrics

Source: `ecological_metrics`

| HTML element | JSON key |
|---|---|
| Shannon diversity value | `ecological_metrics.shannon` |
| Evenness value | `ecological_metrics.pielou_evenness` |
| State label | `ecological_metrics.diversity_state` |

### Safety banner

Source: `safety_profile`

- `any_detected === true` → show red warning banner; highlight markers where value > 0.1
- `any_detected === false` → no banner

Marker keys: `F_nucleatum` · `S_gallolyticus` · `P_anaerobius` · `E_Shigella`

---

## SECTION 2 — Strengths & Challenges

### Two-column layout

**Strengths card** (`.sw-card.strengths`, green background):
Source: `strengths_challenges.strengths[]`

**Challenges card** (`.sw-card.weaknesses`, red background):
Source: `strengths_challenges.challenges[]`

Per item (both cards):

| HTML element | JSON key | Notes |
|---|---|---|
| Dot colour | `.severity` | `"critical"` / `"high"` → `dot-red` · `"moderate"` → `dot-amber` · (strengths always `dot-green`) |
| Emoji + title + text | `.icon`, `.title`, `.text` | Rendered as bold title + plain text inline |

**Expanded lists:**
- `strengths_challenges.all_strengths[]` — full untruncated list
- `strengths_challenges.all_challenges[]` — full untruncated list

Use `.strengths[]` / `.challenges[]` for the initial view. Consider `all_*` for a "show more" expansion if needed.

### Bottom quote block

The `.sw-bottom` renders a **static phrase** that varies per sample (e.g. "The main issue is not total collapse…" or "Despite these imbalances, the pattern is addressable…"). This text is **not** from the `good_news` JSON field.

> **Note:** The `good_news` field exists in the JSON but is not currently rendered in any of the 7 reference HTML files. Reserve this field for future use if needed, but do not render it in the current Section 2 implementation.

---

## SECTION 3 — The Story Behind Your Results

### Layout decision

```
if (root_cause_data.factor_cards.length > 0)
  → PRIMARY layout (Factor-First)
else
  → FALLBACK layout (Deviation cards only)

awareness_chips always rendered at the bottom (if non-empty)
```

### Section summary block

Source: `root_cause_data.section_summary`  
Green left-border highlight block (`--green-lt` background, `--green` left border).  
**If empty string → skip entirely** (4 of 7 samples have no section summary).

---

### PRIMARY layout — Factor-First

Used when `root_cause_data.factor_cards.length > 0` (all 7 reference samples use this layout).

#### Metrics strip

Source: `root_cause_data.metrics_strip[]` (up to 4 chips)  
CSS class: `.ms-card`

| HTML element | JSON key | Notes |
|---|---|---|
| Icon | `.icon` | `.ms-icon` |
| Label | `.client_label` | `.ms-label` |
| Value | `.value_str` | `.ms-value` — coloured by impact |
| Range | `.range_str` | `.ms-range` — prefixed "Target: " |
| Card class | `.impact` | `"crit"` → `.ms-crit` (red-lt) · `"high"` → `.ms-high` (amber-lt) · `"low"` → `.ms-low` (red-lt) |

Value colour: `ms-crit` and `ms-low` → `--red` · `ms-high` → `--amber`

#### Factor cards

Source: `root_cause_data.factor_cards[]`  
CSS class: `.fc-card`

Card header (`.fc-header`):

| HTML element | JSON key | CSS class |
|---|---|---|
| Icon | `.icon` | `.fc-icon` |
| Factor name | `.label` | `.fc-label` |
| Subtitle | `.subtitle` | `.fc-subtitle` |
| Evidence badge | `.evidence_label` | `.fc-ev-badge` + colour class |
| Guild dot chips | `.guild_impacts[]` | `.fc-guild-dots` → `.fc-dot` |

Evidence badge colours:

| `evidence_label` | CSS class |
|---|---|
| `"Well established"` | `.ev-label-established` (dark background) |
| `"Research supported"` | `.ev-label-supported` (blue background) |
| `"Emerging research"` | `.ev-label-emerging` (amber background) |

Guild dot chip colours (`.fc-dot`):

| `guild_impacts[j].impact` | CSS class |
|---|---|
| `"crit"` | `.fc-dot-crit` (red) |
| `"high"` | `.fc-dot-high` (amber) |
| `"low"` | `.fc-dot-low` (red) |

**Note:** In the PRIMARY layout, `.fc-card` has **no directionality-based left border**. The `directionality` field in `factor_cards[]` is used only in the cascade diagram arrows and in the FALLBACK layout's `details.rc-factor-item` elements.

Card body (`.fc-body`):

| HTML element | JSON key | Notes |
|---|---|---|
| Explanation text | `.explanation` | `.fc-text` |
| "What does science say" expand | `.kb_text` | `details.rfc-science` — **only render if non-empty string** |
| Guilds count | `.guilds_affected_count` | `.fc-scope` — shown as `"N / 4 guilds affected"` |

#### Cascade diagram

CSS: `.cascade-section` / `.cascade-diag`

Left column — factor pills (`.casc-factor-pill`):

| HTML element | JSON key | Notes |
|---|---|---|
| Emoji + label | `.icon` + `.label` | from `factor_cards[]` |
| Arrow suffix | `.directionality` | `"bidirectional"` → `↔` · anything else → `→` |

Right column — guild pills (`.casc-guild-pill`):

| HTML element | JSON key | CSS class |
|---|---|---|
| Guild name | `.client_label` | `.cgp-label` |
| Value | `.value_str` | `.cgp-value` |
| Pill colour class | `.impact` | `"crit"` → `.gp-crit` (red-lt) · `"high"` → `.gp-high` (amber-lt) · `"low"` → `.gp-low` (red-lt) |
| Driving emojis | `.driving_factor_emojis[]` | `.cgp-emojis` — space-separated emoji string |

---

### FALLBACK layout — Deviation cards

Used when `root_cause_data.factor_cards.length === 0`.  
Also rendered inside `display:none` wrapper in PRIMARY layout for backward compat.

Source: `root_cause_data.deviation_cards[]`

Per deviation card (`.rc-dev-block`):

**Deviation header** — shown as plain text narrative:

| HTML element | JSON key |
|---|---|
| Narrative paragraphs | `.narrative` — split on `"\n\n"` → each part as `<p>` |
| Fallback | `.health_meaning` — single `<p>` if `.narrative === ""` |

**Contributing factor chips** — after narrative (`.rc-factors-label` + `.rc-factor-stack`):

Source: `.kb_drivers[]`  
CSS: `details.rc-factor-chip`

| HTML element | JSON key | Notes |
|---|---|---|
| Icon | `.icon` | `.rfc-icon` |
| Label | `.label` | `.rfc-title` |
| Evidence badge | `.evidence_label` | `.rfc-ev-label` + colour class (same as factor cards) |
| Body text (expanded) | `.text` | `.rfc-text` |
| "What does science say" | `.kb_text` | `details.rfc-science` — **only if non-empty** |

**Directionality left border** — on `details.rc-factor-item` in this layout only:

| `directionality` value | CSS class | Left border colour |
|---|---|---|
| `"driver"` | `.rfi-driver` | `--dark` |
| `"bidirectional"` | `.rfi-bidirectional` | `--purple` |
| `"consequence"` | `.rfi-consequence` | `--amber` |
| `"associative"` | `.rfi-associative` | `--blue` |

**Takeaway line** (`.rc-takeaway`):

| HTML element | JSON key | Notes |
|---|---|---|
| Green takeaway block | `.summary_line` | Prefixed `"✓ "` |
| Fallback | `.personal_synthesis` | If `.summary_line === ""` |

If both are empty → omit the takeaway block.

---

### Awareness chips

Source: `root_cause_data.awareness_chips[]`  
**If empty array → hide entire sub-section.**

HTML block: `.rc-awareness-zone` with label "From your questionnaire" and intro paragraph.

CSS: `details.rc-awareness-chip`

| HTML element | JSON key |
|---|---|
| Icon | `.icon` (`.rc-aw-icon`) |
| Label | `.domain_label` (`.rc-aw-title`) |
| Direction text | `.directionality_arrow` (`.rc-aw-arrow`) |
| Body text (expanded) | `.summary_text` (`.rc-aw-body`) |

Each chip also shows a "You reported this" badge pill inline in the summary.

---

## SECTION 4 — The Road Ahead

### 4-Phase timeline (left column)

Source: `timeline_phases[]` — always 4 items  
CSS: `.timeline` / `.tl-item`

| HTML element | JSON key | CSS class |
|---|---|---|
| Period label | `.weeks` | `.tl-period` |
| Phase title | `.title` (emoji + text) | `.tl-title` |
| Description | `.body` | `.tl-body` |
| Dot colour | `.color` | `style="background:{color}"` on `.tl-dot` |

### Lifestyle recommendations (right panel, dark background)

Source: `lifestyle_recommendations[]`  
CSS: `.lifestyle-panel`  
**If empty array → show 3 hardcoded fallbacks:**

| Emoji | Title | Text |
|---|---|---|
| 🥬 | Increase fiber variety | "Aim for 30g of fiber daily from diverse sources…" |
| 🏃 | Stay physically active | "Regular moderate exercise (30 min, 5 days/week)…" |
| 😴 | Prioritize sleep quality | "Maintain a consistent sleep schedule and aim for 7–9 hours…" |

Per item:

| HTML element | JSON key | CSS class |
|---|---|---|
| Emoji | `.emoji` | `.lp-emoji` |
| Title | `.title` | `.lp-title` |
| Text | `.text` | `.lp-text` |

---

## SECTION 5 — Your Formula

### Morning / Evening grouping

CSS: `.supp-timing-group` / `.supp-timing-banner`

Split `supplement_cards[]` into two groups based on `timing_group`:

| `timing_group` value | Banner class | Banner label |
|---|---|---|
| `"morning"` | `.morning` | 🌅 Morning |
| `"evening"` | `.evening` | 🌙 Evening |
| missing/empty | infer from `.timing` string | contains "bed" or "🌙" → evening · else → morning |

**Important:** The Magnesium card (`delivery_format_magnesium_capsule`) frequently has no `timing_group` — always infer from `.timing`.  
**Important:** Morning Wellness Capsule card uses 🌙 in its own `emoji` field despite being morning — never use the card's `emoji` field for timing group inference; use `timing_group` only.

Render Morning group first, then Evening group. Each banner shows a count badge: "{N} units".

### Supplement cards

Source: `supplement_cards[]`  
CSS: `.supp-unit`

| HTML element | JSON key | CSS class | Notes |
|---|---|---|---|
| Number block | `.num` | `.supp-num-block` | Background: `.color` |
| Card title | `.name` | `.supp-unit-name` | |
| Timing text | `.timing` | `.supp-unit-when` | |
| "Why you're taking it" band | `.why` | `.supp-why-band` | **Omit entire band if `.why === ""`** |
| Ingredient chips | `.pills[]` | `.pill` | `.name` bold + `·` + `.dose` |
| Support tags | `.supports[]` | `.sc-chip` | **Omit entire row if empty array** |

> **Note on `what_it_is`:** This field exists in the JSON but is **not rendered** in any of the 7 reference HTML files. The current HTML only shows: header, why-band, pills, supports.

Pill dose rule: if dose is a bare number (no unit), append "g".

### Delivery format variants

| `key` value | Card name |
|---|---|
| `delivery_format_1_probiotic_capsule` | Probiotic Capsule |
| `delivery_format_2_omega_softgels` | Omega & Antioxidant Softgel |
| `delivery_format_3_daily_sachet` | Daily Prebiotic Sachet (includes vitamins/minerals in pills) |
| `delivery_format_3_powder_jar` | Daily Prebiotic Powder (fiber + botanicals only) |
| `delivery_format_4_evening_capsule` | Evening Capsule |
| `delivery_format_4_morning_wellness_capsules` | Morning Wellness Capsule |
| `delivery_format_5_evening_wellness_capsules` | Evening Wellness Capsule |
| `delivery_format_6_polyphenol_capsule` | Polyphenol Capsule — if `timing_group === "morning"` → show amber warning: "Take with breakfast or on full stomach." |
| `delivery_format_magnesium_capsule` | Magnesium Bisglycinate — no `timing_group`, always infer from `.timing` |

---

## SECTION 6 — Your Goals

Source: `goal_cards[]` — 3 or 4 items  
CSS: `.goal-card`

| HTML element | JSON key | CSS class | Notes |
|---|---|---|---|
| Emoji | `.emoji` | `.goal-emoji` | |
| "Also addressed" badge | `.inferred` | `.goal-inferred-tag` | Show only if `true` — rendered before `.goal-title` |
| Title | `.title` | `.goal-title` | |
| Mechanism text | `.mechanism` | `.goal-mechanism` | |
| Formula box | `.formula_link` | `.goal-formula` | Purple left-border box, prefixed `<strong>Your formula:</strong>` |

---

## REFERENCES

Source: `cited_papers[]` (top-level key — not `root_cause_data.cited_papers`)  
**If empty array → hide entire References section.**

Format as APA: `Surname, I. (year). Title. *Journal*. https://doi.org/…`  
DOI as clickable `<a>` link. Journal in italics.

---

## FOOTER

CSS: `.footer`

| HTML element | Source |
|---|---|
| Brand | Static: "NB1 Health" (`.footer-brand`) |
| Disclaimer | Static: "This report is for informational purposes only and does not constitute medical advice…" |
| Date | `report_date` → parse `YYYY-MM-DD` → display as `DD Month YYYY` |