# Health Report Redesign — Brainstorm & Recommendations
**Date:** March 2026
**Based on:** 7 reference HTML reports + JSON schemas (schema v3.0)
**Scope:** UX flow, communication design, dual-audience strategy, and backend implications

---

## 1. What the Current Report Does Well

Before diving into improvements it's worth being explicit about what already works, because these are constraints to preserve.

**Visual quality is high.** The dark cover with score dial, warm section alternation, and typographic hierarchy (Playfair Display + Nunito) create a premium, trustworthy feel. This should be kept.

**The data richness is already there.** The JSON schema is more sophisticated than the HTML uses. Fields like `non_expert`, `scientific`, `good_news`, `what_it_is`, and `all_strengths[]` / `all_challenges[]` exist in the backend but aren't rendered. This is low-hanging fruit.

**The factor card + cascade diagram (Section 3)** is a genuinely clever design — it connects *causes* to *guilds* visually. The directionality system (driver → bidirectional → consequence → associative) is scientifically meaningful.

**The section flow is logical** for someone who already understands microbiome science: Score → Composition → Causes → Protocol → Goals.

---

## 2. Core Problems

### 2.1 The "So What?" Gap

The most fundamental problem: **the report explains what is happening in the gut but doesn't consistently connect it to how the person feels or why they should care.**

The cover shows a score of 85.7 but never says "here's what that means for your energy / mood / immune resilience in practical terms." A lay reader finishes the report knowing their fiber-degraders are at 15.2% but may still be asking: *will I feel different? How does this show up in my body?*

The `profile.goals` field shows the person cares about mood, immunity, and metabolism — but these goals appear as tiny chips on the cover and in Section 6. They are not woven through as a narrative thread.

### 2.2 No "Bigger Picture" Frame

The report currently has **no section that bridges the gut findings to the rest of the body.** The gut-brain, gut-immune, and gut-metabolic axes are mentioned briefly in Goal cards (Section 6), but by that point the reader has already waded through five data-heavy sections without ever having been given the frame for why any of it matters.

A non-expert reader needs to understand: *"Your gut microbiome communicates with your brain, your immune system, and your metabolism. Here's which of those connections your results are most relevant to."* — before being shown Shannon diversity scores.

### 2.3 Dual-Audience Problem

The JSON already contains both `scientific` and `non_expert` descriptions for every pillar score. This is exactly the right infrastructure for serving two audiences. But the HTML ignores it entirely and defaults to a middle-ground language that satisfies neither.

Scientific terms appear without explanation throughout:
- "Shannon diversity" (Section 1, no definition)
- "CLR" (appears in JSON, echoed in rendering)
- "SCFA" / "short-chain fatty acids" (mentioned but not defined on first use)
- "butyrate" (central concept, defined only partially and late)
- "guild" (used throughout without introducing the metaphor early)

A clinician or researcher is fine. A motivated lay person is lost.

### 2.4 Section 3 (Story Behind Results) Is Structurally Inverted

Section 3 is the most important section — it answers *why* — but it's also the most cognitively demanding and comes third, after two dense data sections.

For non-experts, the flow should be: **narrative first, data second.** Currently: data → narrative → factors → cascade → awareness.

The "Awareness chips" (from your questionnaire) are particularly problematic: these are the most *personally relatable* items (alcohol, stress, physical activity) and the ones the person can actually act on immediately. Yet they're buried at the bottom of the most complex section.

### 2.5 The Timeline (Section 4) Is Generic

The 4-phase protocol timeline is well-written but makes no reference to the person's specific findings. It reads identically for every client. A person whose main issue is below-range fiber degraders should see "by week 5–8 your fiber-processing bacteria are expected to start responding to the prebiotic substrate" rather than a generic "Beneficial bacteria begin expanding."

### 2.6 The Formula → Findings Link Is Weak

Section 5 (Your Formula) shows supplement cards with a `why` band and `supports` tags. But visually, the connection back to Section 3's identified causes is not explicit. A reader might see "Probiotic Capsule — supports Fiber below range, Gut-Brain" and not immediately understand *why this specific strain was chosen for this specific pattern.*

### 2.7 The Report Lacks a "How to Read This" Entry Point

There is no orientation layer. A non-expert opening this report for the first time has no map of what to expect, no glossary reference, and no indication of what the most important findings are before being presented with them.

---

## 3. Proposed Structural Redesign

### 3.1 Add a "Your Gut Story" Block — Immediately After the Score Dial

**What:** A 3–5 sentence plain-language narrative block that:
- States what the score means in plain English ("Your gut is in good shape overall…")
- Names the one or two things to focus on ("…with one clear priority: rebuilding your fiber-processing bacteria")
- Connects directly to the person's stated goals ("This directly supports your mood and immune resilience goals")
- Ends with a forward-looking note ("The protocol below addresses this specifically")

**Backend source:** This maps to the existing `score_summary` field, but that field is too terse and technical. A new `plain_language_cover_narrative` field should be added — or the `good_news` field (currently unused) should be promoted to this role.

**Why this matters:** This is the first thing a non-expert needs. It frames everything that follows.

---

### 3.2 Restructure Section 1 as a True "Bigger Picture"

**Current Section 1:** "The Big Picture" — but it's actually just four metric dials + guild bars + metabolic dials. It's not a bigger picture, it's a data dump.

**Proposed restructure:**

**Part A — Body Systems Panel (new)**
Before any metric, show a simple panel: "Your gut connects to three key systems." Display three tiles: Immune System, Brain & Mood, Metabolism. Each tile shows a status (healthy, needs attention) derived from the most relevant circle scores and bacterial guild findings. This immediately answers "so what?" before showing the mechanics.

**Part B — What Your Bacteria Are Doing (current circle scores)**
Keep the four dials (Gut Lining Protection, Inflammation Control, Fiber Processing, Bifidobacteria) but add one-line plain-language impact statements beneath each score — e.g., "67 — Your gut is extracting less benefit from plant foods than it could." These statements need a new JSON field per circle score: `health_impact_plain`.

**Part C — Your Bacterial Teams (current guild bars)**
The guild bar visualization is good, but the word "guild" is unexplained. Add a brief introductory sentence: "Think of your gut as having six specialized teams, each with a different job." Each bar already has `client_interpretation` — this is good lay-language copy that should be more visually prominent (currently it's tiny `gbar-note` text).

**Part D — How Your Gut Is Processing Food (current metabolic dials)**
The four metabolic dials are the most opaque section for lay readers. Each dial state needs a plain-language "what this means for you" sentence. These should be added to the JSON as `metabolic_dials[key].plain_english`.

---

### 3.3 Weave Goals as a Persistent Thread

**Current state:** Goals appear on the cover as tiny chips and in Section 6 as cards. They're not referenced anywhere between.

**Proposed change:** Each section or finding that relates to a stated goal should carry a small goal-tag chip — a visual marker like "⟶ Your Mood goal" or "⟶ Your Immune goal" — inline near the relevant finding.

**Backend requirement:** A `related_goals[]` array on each deviation card, guild bar, and factor card. The goals map to `profile.goals[]`.

---

### 3.4 Redesign Section 3 Flow

**Proposed new order:**

1. **"The Short Story" block** — 3 sentences in plain language: what's out of balance, why it happened, what the protocol targets. Uses `section_summary` (already exists, currently a green box buried in the section).

2. **Awareness chips FIRST** — "What you told us matters" — move questionnaire factors to the top of Section 3. These are the most immediately understandable and personally resonant. They also prime the reader to understand the factor cards that follow.

3. **Factor cards** (keep as-is — they're good for motivated readers)

4. **Cascade diagram** — collapse by default for non-experts ("Want to see how it all connects? →"). It's a powerful visual for experts but potentially overwhelming for lay users as a default.

5. **Deviation narrative** — keep as-is within each card.

---

### 3.5 Personalize the Timeline (Section 4)

Each timeline phase should reference the person's primary finding explicitly:

- Phase 1 → "The prebiotic fibres in your sachet begin reaching your fiber-processing bacteria from week 1."
- Phase 2 → "Fiber-processing bacteria begin expanding as prebiotic substrate consistently arrives. Watch for improvements in digestive ease."
- Phase 3 → "Guild populations move toward target. Your fiber-processing bacteria are expected to move toward the 30–50% range during this phase."
- Phase 4 → "The improvements in your microbiome become self-sustaining."

**Backend requirement:** Each `timeline_phases[i]` item gets a `personalized_note` field generated from the primary finding context.

---

### 3.6 Connect the Formula Back to the Findings

In Section 5, each supplement card should carry a "This addresses" row — a clear link back to the specific finding it targets.

- Probiotic Capsule → "Addresses: Fiber-processing bacteria below range (15.2%)"
- Daily Prebiotic Sachet → "Addresses: Fiber-processing bacteria below range + Microbial diversity"
- Evening Wellness Capsule → "Addresses: Chronic Stress factor (from your questionnaire)"

**Backend:** This is partially available via `supports[]` tags but needs a more structured `addresses_finding[]` field linking to `deviation.key` or `factor_cards[].domain_key`.

---

### 3.7 Add a "How to Read This Report" Orientation Block

A collapsible block at the very top (or a floating "?" button) that explains:
- What a guild is
- What the score means
- What the color system means (green/amber/red)
- A glossary of 6–8 key terms: butyrate, SCFA, Shannon diversity, dysbiosis, prebiotic, probiotic

For non-experts this is essential. For experts it can be collapsed or ignored.

---

## 4. Communication Design Principles

### 4.1 Layered Disclosure — Expert Toggle

The JSON already supports this. Implementation:

- Default view: `non_expert` language everywhere
- A small toggle at the top of the report: "Show scientific detail" — when activated, switches all pillar descriptions, adds CLR values to guild bars, shows Shannon decimal, reveals cascade diagram by default

The `scientific` field on each pillar and the `kb_text` field on each factor card are the expert layer.

### 4.2 "Why This Matters for You" Pattern

Every metric should follow this pattern:
1. **The number** — "15.2%"
2. **The reference** — "Target: 30–50%"
3. **What it means** — "Your fiber-processing bacteria are well below where they need to be"
4. **Why you should care** — "These bacteria produce compounds that protect your gut lining and support your mood" ← **this is what's missing throughout**
5. **What's being done about it** — "Your prebiotic sachet directly feeds this group"

Currently the reports do 1–3 well, 4 partially, and 5 weakly.

### 4.3 Anchor to Symptoms, Not Just Biomarkers

Where clinically appropriate, findings should be anchored to experiences the person may recognize. For example:

- Low fiber degraders: "When these are low, you may notice bloating, less consistent digestion, and reduced energy from plant foods."
- Low diversity: "Lower diversity can contribute to the gut being less resilient — more sensitive to dietary changes, stress, and travel."
- Good butyrate producers: "These bacteria produce the main fuel for your gut lining cells — a strong result here is associated with reduced digestive discomfort."

**Backend requirement:** An `experiential_note` field per guild / circle score.

### 4.4 Separate "What It Is" from "Why You Have It" from "What to Do"

Currently these are blended across sections. The reader needs a clear mental model:

- Section 1 → *What is happening* (composition, function)
- Section 2 → *How serious is it* (strengths and challenges, prioritization)
- Section 3 → *Why it's happening* (causes — environmental, behavioral, biological)
- Section 4 → *What comes next* (protocol timeline)
- Section 5 → *The specific tools* (formula)
- Section 6 → *How this maps to your goals* (relevance)

This is the current section structure. The problem is that *within* each section, the separation isn't maintained. Section 1 bleeds into "here's what to do," Section 3 mixes symptoms with causes, Section 5 doesn't reference causes.

---

## 5. Specific Unused JSON Fields — Quick Wins

These require only frontend changes, no backend work:

| Field | Location | Proposed use |
|---|---|---|
| `non_expert` | `overall_score.pillars[key]` | Default pillar chip tooltip / expandable |
| `scientific` | `overall_score.pillars[key]` | Expert-mode pillar chip tooltip |
| `good_news` | top-level | Render as a highlight callout in Section 2, after challenges — currently totally absent |
| `what_it_is` | `supplement_cards[]` | Add as a subtle subheading in Section 5 cards — important context for lay readers |
| `all_strengths[]` / `all_challenges[]` | `strengths_challenges` | "Show all" expansion — currently only the first few items show |

---

## 6. Backend / Schema Changes Required

These require new fields or generation logic:

| Change | Field(s) needed | Priority |
|---|---|---|
| Plain-language cover narrative | `plain_language_cover_narrative` (or promote `good_news`) | High |
| Body systems panel | `body_systems_impact` — object with `immune`, `brain_mood`, `metabolism` statuses | High |
| Circle score health impact | `circle_scores[key].health_impact_plain` | High |
| Metabolic dial plain English | `metabolic_dials[key].plain_english` | Medium |
| Goal tags on findings | `related_goals[]` on deviation_cards, guild bars, factor_cards | Medium |
| Experiential anchors | `guild_bars[key].experiential_note` | Medium |
| Personalized timeline notes | `timeline_phases[i].personalized_note` | Medium |
| Formula → finding link | `supplement_cards[i].addresses_finding[]` (array of deviation keys) | Medium |
| Expert / lay toggle flag | `report_mode: "standard" \| "expert"` (or client-side) | Low |
| Glossary terms | `glossary[]` array at report level | Low |

---

## 7. Recommended Section Order Revision

| # | Current title | Proposed title | Key change |
|---|---|---|---|
| Cover | Inside Your Gut | Inside Your Gut | Add "Your Gut Story" narrative block |
| 1 | The Big Picture | The Bigger Picture | Add body systems panel; reorder sub-sections |
| 2 | Strengths & Challenges | What's Working, What to Focus On | Surface `good_news`; show all strengths/challenges |
| 3 | The Story Behind Your Results | Why Your Gut Looks This Way | Move awareness chips to top; short narrative first; cascade collapsed by default |
| 4 | The Road Ahead | What Happens Next | Personalize timeline phases to primary findings |
| 5 | Your Formula | Your Protocol | Add "addresses finding" links; show `what_it_is` |
| 6 | Your Goals | How This Connects to Your Goals | Keep as-is — this section is already strong |
| — | (new) | Understanding Your Report | Collapsible glossary + guide for lay readers |

---

## 8. Priority Summary

**Do immediately (frontend only, JSON fields already exist):**
- Render `good_news` prominently in Section 2
- Render `what_it_is` on supplement cards
- Add "show all" expansion for `all_strengths[]` / `all_challenges[]`
- Use `non_expert` descriptions on pillar chips (tooltip or expand)
- Move `section_summary` to the very top of Section 3 — not buried inside

**Do next (minor backend additions):**
- Add `health_impact_plain` to each circle score
- Add `plain_english` to each metabolic dial state
- Add `experiential_note` to each guild
- Personalize timeline phase bodies with primary-finding references

**Larger architectural additions:**
- Body systems panel (new data + new component)
- Goal tagging across sections
- Expert / lay mode toggle
- Collapsible cascade diagram
- Glossary component

---

*Document prepared for internal design + engineering review.*
*Reference implementation: `health_report_1421029282376.html` (sample ID — Polina, score 85.7)*
