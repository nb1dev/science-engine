# Health Report — Rendering Specification
**Version:** 1.1
**Schema version covered:** 3.0
**Last updated:** March 2026
**Reference implementations:** `health_report_1421029282376.html` (Polina, score 85.7), `health_report_1421266404096.html` (Kaan, score 68.3)
**Reference JSON pair:** `health_report_intro.json` + `health_report_intro.html` (universal intro page)

---

## What this document is

A **rendering specification** (also called a *frontend contract*, *template spec*, or *data–UI mapping*) is the canonical handoff document between a data/backend team and a frontend developer. It specifies exactly which JSON fields map to which UI components, what conditional logic governs rendering, and which fields are planned but not yet present in the schema. A developer reading this document — combined with the reference HTML — should be able to reproduce the template for any valid JSON without additional guidance.

---

## 1. Report Structure Overview

The report is a single-file self-contained HTML document. It does **not** fetch the JSON at runtime; all data is rendered server-side (or at build time) into the HTML. The sequence of sections is:

| Order | Section ID | Section title | Primary JSON source |
|---|---|---|---|
| 0 | `#intro` | Universal intro page | Separate file: `health_report_intro.json` |
| Cover | `#cover` | Inside Your Gut | `overall_score`, `profile`, `good_news`, `safety_profile` |
| — | `#onramp` | How to Read This Report | Static (no JSON) |
| BS | `#body-systems` | Body Systems Panel | `circle_scores`, `bacterial_groups`, `safety_profile` |
| 1 | `#section1` | The Bigger Picture | `circle_scores`, `bacterial_groups`, `metabolic_dials`, `ecological_metrics`, `overall_score.pillars` + narrative bridge from `narrative_report_*.md` |
| 2 | `#section2` | What's Working, What to Focus On | `strengths_challenges`, `good_news` |
| 3 | `#section3` | Why Your Gut Looks This Way | `root_cause_data` + narrative cascade from `narrative_report_*.md § 6.4` |
| 4 | `#section4` | What Happens Next | `timeline_phases`, `bacterial_groups`, `profile.goals`, `supplement_cards[].pills[]` |
| 5 | `#section5` | Your Protocol | `supplement_cards`, `protocol_summary` |
| 6 | `#section6` | How This Connects to Your Goals | `goal_cards` |
| — | `#glossary` | Understanding Your Report | Static content (glossary terms) |

**Section bridges:** Every section except the glossary ends with a `.bridge` div — an italic 1–2 sentence transition previewing the next section. Bridges are static text (not from JSON) and serve to make the report feel like one continuous story.

**Narrative report dependency:** Two sections draw translated content from the companion narrative file `narrative_report_*.md` (located in `reports/reports_md/`). The rendering template should accept this as pre-translated text — the backend generates both the JSON and the narrative, so the template simply slots in the relevant paragraphs.

---

## 2. Top-Level JSON Fields

| Field | Type | Used | Notes |
|---|---|---|---|
| `sample_id` | string | Cover footer, `<title>` | Unique identifier |
| `generated_at` | ISO datetime string | Internal / meta | Not currently displayed |
| `schema_version` | string | Version guard | Must be `"3.0"` |
| `report_date` | date string | Cover footer | Format as "Month YYYY" |
| `overall_score` | object | Cover, Section 1 | See §3 |
| `score_summary` | HTML string | Cover (`.gut-story` block) | Contains `<strong>` tags |
| `bacterial_groups` | object (keyed) | Section 1 guild bars | See §6 |
| `metabolic_dials` | object (keyed) | Section 1 metabolic panel | See §7 |
| `ecological_metrics` | object | Section 1 diversity row | See §8 |
| `safety_profile` | object | Cover badge, Section 2 | See §9 |
| `guild_timepoints` | array | Section 1 (future: trend chart) | Currently single-item baseline |
| `protocol_summary` | object | Section 5 header | See §10 |
| `profile` | object | Cover, throughout | See §11 |
| `circle_scores` | object (keyed) | Section 1 health dials | See §5 |
| `strengths_challenges` | object | Section 2 | See §12 |
| `good_news` | string | Section 2 highlight bar | Plain text, render as callout |
| `timeline_phases` | array | Section 4 | See §13 |
| `supplement_cards` | array | Section 5 | See §14 |
| `goal_cards` | array | Section 6 | See §15 |
| `root_cause_data` | object | Section 3 | See §16 |

---

## 3. `overall_score`

```
overall_score.total          → Score dial value (0–100), displayed as "XX.X"
overall_score.band           → Band label: "Excellent" | "Good" | "Fair" | "Poor"
overall_score.pillars        → Object with 5 keys (see §4)
overall_score.score_drivers  → Sub-object with strongest/weakest pillar highlight
```

### Score dial rendering

The dial is an SVG circle with a `stroke-dasharray` / `stroke-dashoffset` technique. Circumference = 2π × r (r = 54 in reference implementation = 339.3px). Offset = circumference × (1 − score/100).

**Dial color by band:**

| Band | Score range | Dial color | CSS variable |
|---|---|---|---|
| Excellent | 85–100 | `#2E8B6E` (teal-green) | `--score-color` |
| Good | 65–84 | `#C97C2A` (amber) | `--score-color` |
| Fair | 45–64 | `#C97C2A` (amber) | `--score-color` |
| Poor | 0–44 | `#B94040` (red) | `--score-color` |

**Score drivers block** (below dial):
- `score_drivers.strongest.label` → "Your strongest area: [label]"
- `score_drivers.weakest.label` → "Your main opportunity: [label]"
- `score_drivers.key_note` → rendered as italic note

---

## 4. `overall_score.pillars`

Five pillar keys: `health_association`, `diversity_resilience`, `metabolic_function`, `guild_balance`, `safety_profile`.

Each pillar:
```
pillars[key].score       → Numeric value
pillars[key].max         → Maximum possible (varies: 20, 20, 20, 30, 10)
pillars[key].scientific  → Expert-mode tooltip / expandable text
pillars[key].non_expert  → Default tooltip text (shown on hover / click)
```

**Pillar chip rendering:**
- Show `score` / `max` as a fraction
- Calculate percentage: `(score / max) × 100`
- Color chip by percentage using the **status color rules** (see §17)
- On hover: show `non_expert` description (default) or `scientific` description (expert mode)

---

## 5. `circle_scores`

Four keys: `gut_lining`, `inflammation`, `fiber_processing`, `bifidobacteria`.

Each value is a score from 0–100.

```
circle_scores.gut_lining        → "Gut Lining Protection" dial
circle_scores.inflammation      → "Inflammation Control" dial
circle_scores.fiber_processing  → "Fiber Processing" dial
circle_scores.bifidobacteria    → "Bifidobacteria" dial
```

**Circle score dial color by value:**

| Range | Color | Label |
|---|---|---|
| 70–100 | `#2E8B6E` | Healthy |
| 40–69 | `#C97C2A` | Needs attention |
| 0–39 | `#B94040` | Critical |

**Impact line** *(planned field — not yet in schema)*:
Each dial needs a `health_impact_plain` string appended below the score. Example: "67 — Your gut is extracting less benefit from plant foods than it could." This field must be added to the backend.

---

## 6. `bacterial_groups`

Object keyed by guild name. Keys in current schema:
- `"Mucin Degraders"`, `"Fiber Degraders"`, `"Cross-Feeders"`, `"Bifidobacteria"`, `"Butyrate Producers"`, `"Proteolytic Guild"`

Each guild:
```
bacterial_groups[name].abundance          → Percentage (e.g. 38.39 → "38.4%")
bacterial_groups[name].healthy_range      → String (e.g. "1-4%")
bacterial_groups[name].status             → "Above range" | "Within range" | "Below range"
bacterial_groups[name].clr               → CLR value (show in expert mode only)
bacterial_groups[name].evenness           → 0–1 float
bacterial_groups[name].evenness_status    → Plain-language evenness description
bacterial_groups[name].client_interpretation → Full explanatory paragraph
```

**Guild bar width:** `abundance` as a percentage of a notional 100% max. Bar element width = `Math.min(abundance, 60)` px / 60 × 100% (cap at 60% visual to prevent overflow).

**Guild bar color by status:**

| Status | Bar color | Text label color |
|---|---|---|
| Above range | `#B94040` | Red |
| Within range | `#2E8B6E` | Green |
| Below range | `#C97C2A` | Amber |

**Evenness badge color by evenness_status:**
- Contains "Low redundancy" → red badge
- Contains "Moderate redundancy" → amber badge
- Contains "High redundancy" → green badge

**Client interpretation** is shown below the bar as a `.gbar-note` paragraph (always visible, not collapsed).

**Guild role line** *(planned field)*: A one-line "what this team does" description needs an `experiential_note` field per guild. Current `client_interpretation` is the per-client detailed version; the role line should be a universal descriptor (e.g. "Breaks down dietary fibre to fuel the fermentation chain").

---

## 7. `metabolic_dials`

Four keys: `main_fuel`, `fermentation_efficiency`, `mucus_dependency`, `putrefaction_pressure`.

Each dial:
```
metabolic_dials[key].state   → State string (see state tables below)
metabolic_dials[key].label   → Human-readable state description
metabolic_dials[key].value   → CLR ratio value (show in expert mode only)
```

**State → display label → color mapping:**

`main_fuel`:
| State | Label | Color |
|---|---|---|
| `carb_driven` | Carbohydrate fermentation dominant | `#2E8B6E` |
| `balanced` | Balanced fuel use | `#2E8B6E` |
| `prot_driven` | Protein fermentation dominant | `#B94040` |

`fermentation_efficiency`:
| State | Label | Color |
|---|---|---|
| `efficient` | Efficient fermentation | `#2E8B6E` |
| `ok` | OK but can improve | `#C97C2A` |
| `inefficient` | Poor fermentation efficiency | `#B94040` |

`mucus_dependency`:
| State | Label | Color |
|---|---|---|
| `normal` | Normal mucus substrate use | `#2E8B6E` |
| `elevated` | Elevated mucus-substrate reliance | `#C97C2A` |
| `heavy_mucus` | Heavy reliance — gut lining at risk | `#B94040` |

`putrefaction_pressure`:
| State | Label | Color |
|---|---|---|
| `balanced` | Balanced byproduct activity | `#2E8B6E` |
| `moderate` | Moderate putrefaction | `#C97C2A` |
| `high` | High putrefaction pressure | `#B94040` |

**Plain English sentence** *(planned field)*: Each state needs a `plain_english` string like "Your gut is running primarily on plant-based fuel — this is the healthiest pattern." Add as `metabolic_dials[key].plain_english`.

---

## 8. `ecological_metrics`

```
ecological_metrics.shannon          → Float (e.g. 2.79) — shown as "X.XX"
ecological_metrics.pielou_evenness  → Float (shown in expert mode)
ecological_metrics.diversity_state  → "high" | "moderate" | "low"
```

**Diversity state → color:**

| State | Color |
|---|---|
| `high` | `#2E8B6E` |
| `moderate` | `#C97C2A` |
| `low` | `#B94040` |

Population benchmark for Shannon context: median 3.29 (25th pct ~2.5, 75th pct ~3.8). These benchmarks are currently hardcoded in the template — consider moving to JSON.

---

## 9. `safety_profile`

```
safety_profile.dysbiosis_markers          → Object: { F_nucleatum, S_gallolyticus, P_anaerobius, E_Shigella }
safety_profile.dysbiosis_markers[key]     → Float (relative abundance; > 0 = detected)
safety_profile.any_detected               → Boolean
```

**Cover badge:** If `any_detected === true`, show a red alert badge "⚠ Dysbiosis marker detected" on the cover. If false, no badge shown.

**Section 2:** If `any_detected === true`, the first challenge card always surfaces the detected marker(s). Detected markers are those where `dysbiosis_markers[key] > 0`. Display the marker key with underscores replaced by spaces and italicised (e.g. *E. Shigella*).

---

## 10. `protocol_summary`

```
protocol_summary.synbiotic_mix.mix_name   → Protocol name (e.g. "Fiber Expansion & Competitive Displacement")
protocol_summary.morning_solid_units      → Integer
protocol_summary.morning_jar_units        → Integer
protocol_summary.evening_solid_units      → Integer
protocol_summary.total_daily_units        → Integer
protocol_summary.total_daily_weight_g     → Float (display as "X.Xg")
```

Displayed in Section 4/5 header as a dosage summary row. `mix_name` appears as the protocol subtitle on the cover.

---

## 11. `profile`

```
profile.first_name   → Used throughout in personal address ("Your gut, [name]")
profile.age          → Integer
profile.sex          → "Male" | "Female" | other
profile.diet         → Diet string (e.g. "Omnivore")
profile.stress       → Integer 1–10
profile.sleep        → Integer 1–10
profile.goals        → Array of strings
profile.sensitivity  → "Low" | "Medium" | "High"
```

**Goals** render as chips on the cover and as section headers in Section 6. Each goal string maps to a `goal_cards[]` entry (matched by proximity of wording, not a formal key — see §15).

**Awareness chips in Section 3** use `profile.stress` and `profile.sleep` values:
- Stress chip shows "Stress — [X]/10" with color: ≥7 = red, 5–6 = amber, ≤4 = green
- Sleep chip shows "Sleep — [X]/10" with color: ≤5 = red, 6–7 = amber, ≥8 = green

---

## 12. `strengths_challenges`

```
strengths_challenges.strengths[]         → Array (typically top 2–3 for default view)
strengths_challenges.challenges[]        → Array (typically top 3–4 for default view)
strengths_challenges.all_strengths[]     → Full array for "show all" expansion
strengths_challenges.all_challenges[]    → Full array for "show all" expansion
strengths_challenges.distinct_areas[]    → String array of challenge area labels
```

Each item in `strengths[]` / `challenges[]` / `all_strengths[]` / `all_challenges[]`:
```
item.icon       → Emoji
item.title      → Heading string
item.text       → Explanatory paragraph
item.area_key   → Snake_case key (challenges only) — used for cross-referencing
item.area_label → Human-readable area label (challenges only)
item.severity   → "critical" | "high" | "moderate" | "low" (challenges only)
```

**Severity → card styling:**

| Severity | Left border color | Icon background |
|---|---|---|
| `critical` | `#B94040` | Light red |
| `high` | `#C97C2A` | Light amber |
| `moderate` | `#C97C2A` | Light amber (lighter) |
| `low` | `#2E8B6E` | Light green |

**Default view:** render `strengths[]` and `challenges[]`. Show a "Show all X strengths / challenges" toggle button if `all_strengths.length > strengths.length` or `all_challenges.length > challenges.length`.

**`good_news`** (top-level field): Render as a distinct green callout box (`.good-news-bar`) at the top of Section 2, before the strengths list. This field is currently a single string — render as-is with a "✅ Good news" label.

---

## 13. `timeline_phases`

Array of 4 objects representing the 4 protocol weeks.

```
phase.weeks      → Range string (e.g. "1–4")
phase.label      → Short label (e.g. "Settling in")
phase.color      → Hex color for timeline dot and accent
phase.dot_class  → CSS class (e.g. "wk4") for dot positioning
phase.title      → Display title with emoji (e.g. "🌱 Settling in")
phase.body       → Full descriptive paragraph (may reference specific compounds)
```

Timeline is rendered as a horizontal track with 4 labelled dots. Each dot expands into a card panel below. Default state: first phase expanded, rest collapsed. On click, expand/collapse individual phases.

**Personalized note** *(planned field)*: Add `phase.personalized_note` — a 1–2 sentence callout that references the client's primary finding directly (e.g. "Your prebiotic fibres begin reaching your fiber-processing bacteria from week 1."). Rendered as a distinct `.tl-personal` styled block within each phase card.

---

## 14. `supplement_cards`

Array ordered by `num` field (ascending). Render in Section 5 grid (2-column on desktop, 1-column on mobile).

```
card.num           → Display order integer
card.key           → Internal key (not displayed)
card.name          → Card heading
card.timing        → Display string with emoji (e.g. "🌅 Every morning")
card.timing_group  → "morning" | "evening" — used for section grouping
card.what_it_is    → One-line category descriptor (previously unused — now render as card subtitle)
card.why           → Rationale paragraph (primary display copy)
card.pills[]       → Array of ingredient objects
card.pills[].name  → Ingredient name
card.pills[].dose  → Dose string
card.supports[]    → Array of tag strings
card.color         → Hex accent color for card header
card.emoji         → Header emoji
```

**Render order:** Group by `timing_group`. Morning cards first, then evening. Within each group, maintain `num` order.

**`what_it_is`** renders as a `.supp-meta` subtitle line directly below the card heading, in a lighter weight.

**Addresses finding** *(planned field)*: Add `card.addresses_finding[]` — an array of `deviation.key` strings from `root_cause_data.deviation_cards`. Render as a distinct `.supp-addresses` band at the bottom of each card: "Addresses: [finding label 1], [finding label 2]". This provides the explicit formula→findings link currently missing.

---

## 15. `goal_cards`

Array of goal objects rendered in Section 6.

```
card.emoji          → Header emoji
card.title          → Goal title (matches wording in profile.goals[])
card.mechanism      → Explanatory paragraph on gut–goal connection
card.formula_link   → Paragraph explaining which formula components address this goal
card.inferred       → Boolean — if true, add "(inferred)" label or different styling
```

**Inferred goals:** If `card.inferred === true`, the goal was not explicitly stated by the client but was derived from their questionnaire scores. Render with a subtle "Inferred from your questionnaire" subtext.

---

## 16. `root_cause_data`

```
root_cause_data.deviation_cards[]    → Array of deviation objects
```

Each deviation card:
```
deviation_card.deviation.key              → Unique key (e.g. "Fiber Degraders__below")
deviation_card.deviation.type             → "below_range" | "above_range"
deviation_card.deviation.guild_key        → Guild name string
deviation_card.deviation.client_label     → Display name (e.g. "Fiber-Processing Bacteria")
deviation_card.deviation.icon             → Emoji
deviation_card.deviation.value_str        → Current value display (e.g. "13.1%")
deviation_card.deviation.range_str        → Healthy range display (e.g. "30–50%")
deviation_card.deviation.description      → One-sentence plain description
deviation_card.health_meaning             → Alternative one-sentence description (often same as description)
deviation_card.kb_drivers[]               → Array of factor cards (see §16.1)
```

Fields present in schema but currently empty strings (do not render, skip):
- `deviation_card.narrative`
- `deviation_card.summary_line`
- `deviation_card.personal_synthesis`
- `deviation_card.drivers`

### 16.1 `kb_drivers` — Factor Cards

Each driver within a deviation:
```
driver.icon                  → Emoji
driver.label                 → Factor name
driver.text                  → Client-facing explanatory paragraph (use this as default)
driver.kb_text               → Scientific version (same as text in current schema; use in expert mode)
driver.is_llm_text           → Boolean (internal flag, not displayed)
driver.directionality        → "driver" | "bidirectional" | "consequence" | "associative"
driver.directionality_arrow  → Descriptive arrow string (e.g. "stress ↔ microbiome")
driver.evidence_label        → "Well established" | "Research supported" | "Emerging research"
driver.evidence_strength     → "strong" | "moderate" | "weak"
driver.domain_key            → Snake_case domain key (used for cross-referencing, not displayed)
```

**Directionality display:**

| Value | Arrow symbol | Color |
|---|---|---|
| `driver` | → | Blue `#3A6EA8` |
| `bidirectional` | ↔ | Purple `#6B5EA8` |
| `consequence` | ← | Amber `#C97C2A` |
| `associative` | ~ | Gray `#888` |

**Section 3 render order (current design):**

1. `.short-story` — 2–3 sentences summarising the primary deviation pattern (use `score_summary` as source, or generate from top challenge)
2. `.awareness-zone` — Profile factor chips (stress, sleep, diet from `profile`) shown at the top before technical cards
3. Deviation cards (one per deviation in `deviation_cards[]`), each collapsed by default for non-experts
4. Cascade diagram — collapsible `<details>` block; collapsed by default

---

## 17. Color System Reference

Three semantic colors drive almost all status/severity rendering:

| Variable | Hex | Use |
|---|---|---|
| `--green` | `#2E8B6E` | Healthy, within range, positive |
| `--amber` | `#C97C2A` | Needs attention, borderline |
| `--red` | `#B94040` | Critical, above/below range (serious), detected threat |

Background tints (for cards, badges):
- Green tint: `rgba(46, 139, 110, 0.1)`
- Amber tint: `rgba(201, 124, 42, 0.1)`
- Red tint: `rgba(185, 64, 64, 0.1)`

Section background alternation:
- Odd sections: `#FAF8F5` (warm off-white)
- Even sections: `#FFFFFF`
- Cover: `#1E1E2A` (dark navy)

Typography:
- Headings: `'Playfair Display', Georgia, serif`
- Body: `'Nunito', 'Segoe UI', sans-serif`

---

## 18. Planned Schema Fields (Not Yet in Backend)

These fields are rendered in the reference HTML prototype but are not yet present in the JSON schema. The backend must add them before the template can be fully data-driven.

| Field path | Type | Purpose | Priority |
|---|---|---|---|
| `plain_language_cover_narrative` | string | Replaces/supplements `score_summary` with a 3–5 sentence plain-language narrative for the `.gut-story` cover block | High |
| `body_systems_impact` | object | `{ immune, brain_mood, metabolism }` each with `status` ("healthy"\|"attention") and `note` string — drives the 3-tile body systems panel | High |
| `circle_scores[key].health_impact_plain` | string | One-line "what this score means for you" displayed below each health dial | High |
| `metabolic_dials[key].plain_english` | string | Plain-language sentence for each metabolic state | Medium |
| `bacterial_groups[name].experiential_note` | string | Universal one-liner on what this guild does (not client-specific) | Medium |
| `timeline_phases[i].personalized_note` | string | 1–2 sentences linking the phase to the client's primary finding | Medium |
| `supplement_cards[i].addresses_finding[]` | string array | Array of `deviation.key` strings; drives the "Addresses:" band on supplement cards | Medium |
| `root_cause_data.deviation_cards[i].related_goals[]` | string array | Goal tags linking deviations to `profile.goals[]` items | Medium |
| `bacterial_groups[name].related_goals[]` | string array | Same — goal tags on guild bar items | Low |
| `glossary` | array of `{ term, definition }` | Source for the collapsible glossary component | Low |
| `report_mode` | `"standard"\|"expert"` | Server-side default mode flag (alternatively handled client-side) | Low |

---

## 19. Intro Page — Separate Schema (`health_report_intro.json`)

The intro page is a **universal, client-agnostic** educational diagram delivered as a separate HTML+JSON pair. It does not contain any client data.

**File locations:**
- Data: `documentation/health_report_intro.json`
- Template: `documentation/health_report_intro.html`

**Schema (`schema_version: "1.0"`):**

```
page.eyebrow              → Small label above title
page.title                → Page headline
page.title_italic_phrase  → Substring of title to render in italic
page.subtitle             → Subheading paragraph
page.subtitle_bold_phrase → Substring of subtitle to render in bold
page.footer               → Footer paragraph
page.footer_bold_phrase   → Substring of footer to render in bold
page.cta                  → Call-to-action text at bottom

hub.emoji                 → Central hub emoji (🦠)
hub.label                 → "Your Gut Microbiome"
hub.stat                  → Statistic string (e.g. "~38 trillion bacteria")

columns.inputs_label      → Left column header
columns.outputs_label     → Right column header

inputs[]                  → Array of input groups
inputs[].group            → Group label (e.g. "Daily habits")
inputs[].nodes[]          → Array of input nodes

inputs[].nodes[].id       → Unique identifier
inputs[].nodes[].icon     → Emoji
inputs[].nodes[].label    → Short display name
inputs[].nodes[].desc     → One-line description
inputs[].nodes[].bidirectional  → Boolean — if true, applies purple .bidir styling and "⇄ both ways" tag

outputs[]                 → Flat array of output nodes (no groups)
outputs[].id              → Unique identifier
outputs[].icon            → Emoji
outputs[].label           → Short display name
outputs[].desc            → One-line description
outputs[].bidirectional   → Boolean (currently always false on outputs)

legend[]                  → Array of legend items
legend[].type             → "input" | "output" | "bidirectional"
legend[].label            → Display string
```

**Connector lines:** Rendered by JavaScript post-layout using `getBoundingClientRect()`. Lines are drawn on an absolute-positioned `<canvas>` or SVG overlay. Input nodes connect to the hub center; hub center connects to output nodes. Bidirectional nodes use a purple stroke (`#6B5EA8`); standard nodes use the directional color (`#3A6EA8` for inputs, `#2E8B6E` for outputs).

**Note on fetch():** The intro HTML uses `fetch('health_report_intro.json')` to load data at runtime, so it must be served via HTTP (not opened as a `file://` URL). The health reports themselves are static HTML — no fetch required.

---

## 20. Expert / Lay Mode Toggle (Planned)

The JSON already fully supports dual-audience rendering via `non_expert` and `scientific` fields on each pillar. The toggle is not yet implemented but the architecture is:

- Default: render `non_expert` text everywhere
- Toggle state "Show scientific detail": switch all `non_expert` fields to `scientific`, show CLR values on guild bars, show `shannon` decimal, expand cascade diagram by default
- Store toggle state in `sessionStorage` (or as a URL param for shareable links)
- A `report_mode: "standard" | "expert"` field can be added to the JSON to set a default per client (e.g. clinician-ordered reports default to expert mode)

---

## 21. Glossary Component (Planned)

A collapsible `<details>` block at the end of the report with 8+ terms. Currently hardcoded in the HTML; should be driven by a `glossary[]` array in the JSON.

Current terms (to be migrated to JSON):
- **Guild** — a group of gut bacteria that share a functional role
- **Butyrate** — a short-chain fatty acid that fuels your gut lining cells
- **SCFA (Short-Chain Fatty Acid)** — compounds produced by bacteria fermenting fibre; key to gut and systemic health
- **Shannon diversity** — a measure of species richness and evenness (higher = more diverse)
- **Dysbiosis** — an imbalanced gut microbiome state linked to health disruption
- **Prebiotic** — dietary fibre that feeds beneficial gut bacteria
- **Probiotic** — live beneficial bacteria taken as a supplement
- **CLR (Centred Log-Ratio)** — a statistical transformation used to compare bacterial abundances across samples

---

## 22. "How to Read This Report" On-Ramp

A warm, quiet card that appears between the cover and the body systems panel. It orients the reader before any data appears.

**Component:** `.onramp` div with `.onramp-list` containing 3–5 short bullets.

**Content (static — not from JSON):**
1. "First, you'll see your overall pattern — what your gut ecosystem looks like right now."
2. "Then we'll show how it connects to your body: mood, immunity, metabolism."
3. "Next, what's working well and what needs support — with the science translated."
4. "Then the deeper story: why your gut looks this way and what's driving the pattern."
5. "Finally, your step-by-step protocol and when to expect changes."

**Styling:** Light warm background (`var(--warm)`), soft border, no heavy headers. Uses a small "How to read this report" label in uppercase.

---

## 23. Section Bridges (`.bridge`)

Each section (except the glossary) ends with a `.bridge` div — an italic, muted 1–2 sentence transition that previews the next section. The purpose is to make the report read as one continuous story rather than a set of disconnected panels.

**Styling:** `border-top: 1px solid var(--rule)`, top padding 32px, top margin 48px, font-size 14px, color `var(--mid)`, italic.

**Content (static — not from JSON):**

| After section | Bridge text |
|---|---|
| Body Systems Panel | "Now let's look at the numbers behind these patterns — the six bacterial teams and how they're performing." |
| Section 1 (Bigger Picture) | "With that picture in mind, here's what's working in your favour — and the priority areas that will make the biggest difference." |
| Section 2 (Strengths/Challenges) | "Understanding what needs attention is one thing — understanding *why* is what makes the solution stick." |
| Section 3 (Root Cause) | "Now that you know what's happening and why, here's what happens next — your protocol timeline." |
| Section 4 (Timeline) | "Here's exactly what's in your daily protocol — each component chosen for a specific reason." |
| Section 5 (Protocol) | "Finally, let's connect all of this back to what matters most to you — your health goals." |

---

## 24. Factory Analogy Callout (`.guild-factory-analogy`)

Appears inside Section 1, after the guild bars. A narrative block that translates the CLR ratio dashboard into a lay-language "factory" metaphor.

**Source:** `narrative_report_*.md`, Section 4.2 → "Integrated Metabolic State" paragraph. Translated to non-expert language at render time.

**JSON keys used:** `bacterial_groups.*` (for specific % values woven into the metaphor), `metabolic_dials.*` (for state references).

**Styling:** Left green border (3px solid `var(--green)`), soft green background tint, 14px body text.

**Planned field:** `factory_analogy_translated` (string) — pre-translated version for template use, avoiding runtime narrative translation.

---

## 25. Timeline Journey & Personalised Notes

The timeline (Section 4) renders as a horizontal journey track rather than a plain list.

**Visual structure:**
1. Horizontal track bar with 4 coloured dots (using `phase.color` and `phase.dot_class`)
2. 4 phase cards below, each expandable
3. Each card contains `phase.body` text + a `.tl-personal` callout

**Personalised notes (`.tl-personal`):** 1–2 sentences referencing the client's specific findings. These are currently derived at render time from:
- `bacterial_groups` (specific % values)
- `profile.goals` (goal references)
- `supplement_cards[].pills[]` (specific compound mentions, e.g. LP815 → GABA)

**Planned field:** `timeline_phases[i].personalized_note` (string) — backend pre-generates the personalised note so the template doesn't need to compose it.

**Technical note (optional render):** A small muted block at the end of the timeline section explaining the data sources: "Timeline keys: `timeline_phases[]` from `health_report_interpretations_*.json`. Personalised notes derived from `bacterial_groups`, `profile.goals`, and `supplement_cards[].pills[]`."

---

## 26. Narrative Report as a Data Source

The companion narrative markdown file (`narrative_report_*.md`, located in `reports/reports_md/`) provides rich, science-driven text that is translated into lay language for two report sections:

| Report section | Narrative source | Section § | Key used |
|---|---|---|---|
| Section 1 factory analogy | `narrative_report > § 4.2 > Integrated Metabolic State` | The factory-floor paragraph | `bacterial_groups.*`, `metabolic_dials.*` |
| Section 2 gut lining challenge | `narrative_report > § 6.2 > Barrier Integrity` | The Akkermansia paradox paragraph | `strengths_challenges.challenges[area_key="gut_lining"]`, `bacterial_groups["Mucin Degraders"]` |
| Section 3 short story | `narrative_report > § 6.4 > Why This Overall Pattern Emerged` | The 4-stage cascade (Stages 1–4) | `root_cause_data.deviation_cards[]` |

**Implementation note:** The narrative file is generated by the same pipeline that produces the JSON. For a fully data-driven template, the relevant translated paragraphs should be extracted into new JSON fields (see §18 planned fields). Until then, the template hard-renders the translated text per sample.

---

*This document covers schema v3.0. For questions about field generation logic, refer to `PIPELINE_DOCUMENTATION.md` and `SCIENTIFIC_RATIONALE.md`. For the full UX rationale behind the component decisions, refer to `health_report_redesign_brainstorm.md`. The companion narrative report (`narrative_report_*.md`) provides the scientific source text for translated bridges.*
