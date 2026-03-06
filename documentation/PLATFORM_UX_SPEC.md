# Platform UX Specification — Health Report

**Purpose:** Describes how information should flow through the health report platform, what each section communicates, and why it's ordered that way. For designers and frontend developers.

---

## The Client's Journey (Reading Order)

The report tells a story in 5 acts. Each tab answers a specific question:

```
Tab 1: OVERVIEW         → "How is my gut doing overall?"
Tab 2: BACTERIAL GROUPS  → "Who are the players and how are they performing?"
Tab 3: ROOT CAUSES       → "Why is my gut this way? What went wrong (or right)?"
Tab 4: VITAMINS          → "Can my gut produce the vitamins I need?"
Tab 5: ACTION PLAN       → "What should I do about it?"
```

---

## Tab 1: Overview — "The Big Picture"

### Purpose
First impression. Client gets their score, main status, and a quick sense of what's working and what needs attention. No deep science — just the headlines.

### Information Flow
1. **Score ring** (85.7 / Excellent) — immediate emotional anchor
2. **One-line summary** — "Your gut is mostly healthy with one area to strengthen"
3. **5 pillars** — score breakdown showing which areas contribute most/least
4. **What's Happening** — balance status + diversity status in plain language
5. **Key strengths/opportunities** — what's good, what needs work
6. **4 metabolic dials** — how the gut processes food (this IS the metabolic function pillar explained)
7. **Is something wrong / Can it be fixed** — reassurance section

### Biology Logic for Designer
- The **5 pillars** are not separate tests — they're 5 perspectives on the same data
- **Metabolic Function pillar** = the 4 dials section below. They should visually connect.
- **Guild Balance pillar** = the Bacterial Groups tab. Consider a "See details →" link.
- The dials use color: green = healthy, amber = watch, red = needs attention

### Consistent Terms Used
- "Fiber-processing bacteria" (never "Fiber Degraders" to client)
- "Bifidobacteria" (keep scientific name — well-known brand)
- "Intermediate Processors" (never "Cross-Feeders" to client)
- "Gut-lining energy producers" (never "Butyrate Producers" to client)
- "Mucus-layer bacteria" (never "Mucin Degraders")
- "Protein-fermenting bacteria" (never "Proteolytic Guild")

---

## Tab 2: Bacterial Groups — "Meet Your Bacterial Teams"

### Purpose
Shows the 6 specialized teams in the gut, what each one does, and how strong/weak each team is. This is where "Guild Balance" from the overview is explained.

### Information Flow
1. **Intro** — "Your gut bacteria form 6 specialized teams..."
2. **Per team (6 items):**
   - Step number (1-6, they work in order like an assembly line)
   - Team name + what they do
   - Staffing level (workers vs optimal)
   - Status badge (within range / below / above)
   - Impact explanation (what it means for health)

### Biology Logic for Designer
- The 6 teams work in a CHAIN: Fiber → Bifidobacteria → Connectors → Butyrate → (Protein and Mucus are separate lines)
- Think of it as a factory assembly line — if one station is understaffed, everything downstream suffers
- The "workers / optimal" numbers are normalized to a 100-point scale where optimal = 100 total

---

## Tab 3: Root Causes — "Why Your Gut Is This Way"

### Purpose
This is the ANALYSIS tab. It explains the mechanisms behind the numbers. This is where clients learn what led to their current state and what the consequences are.

### ⚠️ CRITICAL UX REQUIREMENT
This tab must be structured as a guided story, not a data dump. The client reads top-to-bottom and each section builds on the previous one.

### Information Flow (4-Part Story)

**Part 1: "What we found" (The Evidence)**
_Links back to → Bacterial Groups tab_

Intro: "When we measured your bacterial teams, here's what stood out:"

Shows the diagnostic flags — these are the raw findings from bacterial analysis. Each flag refers to a bacterial team from Tab 2.

Example: "Your fiber-processing bacteria are at about half of the minimum healthy level"

_This tells the client: "Here are the facts from your test."_

---

**Part 2: "What this means for how your gut works" (The Metabolic Impact)**
_Links back to → Overview → Metabolic Dials_

Intro: "These bacterial imbalances affect how your gut processes food. Here's what your metabolic readings show:"

Shows the 4 metabolic dial root-cause links — each one explains WHY the dial reads the way it does, connected to the bacterial evidence from Part 1.

Example: "Your bacteria heavily rely on your gut's protective layer for fuel because not enough dietary fiber reaches them"

_This tells the client: "Here's the consequence of what we found."_

---

**Part 3: "How this probably happened" (The Chain Reaction)**
_New information — connects Parts 1 and 2 into a causal story_

Intro: "Gut changes don't happen overnight. Based on the patterns we see, here are the chain reactions at work:"

Shows feedback loops as visual flowcharts (boxes with arrows).

Each loop has:
- A name (e.g., "The Fiber Gap Cycle")
- A status badge (active / developing / stable)
- A chain of connected events

This section ALSO shows the lifestyle inference: "Based on these patterns, your bacteria suggest..."

_This tells the client: "Here's the story of how your gut got here."_

---

**Part 4: "Can this be reversed?" (The Good News)**
_Links forward to → Action Plan tab_

Shows reversibility assessment with timeline.

Teaser: "Your personalized action plan and custom supplement formula target these specific gaps → See Action Plan"

_This tells the client: "There's a way forward."_

---

### Visual Design Notes for Root Causes
- Each Part should have a distinct visual section (numbered or color-coded)
- Feedback loops should use BOXES with ARROWS, not text chains
- Status badges: active = red, developing = amber, stable = green
- Cross-references to other tabs should be clickable links (in real platform) or labeled references (in prototype)
- The lifestyle inference section should feel gentle — never accusatory

---

## Tab 4: Vitamins — "What Your Gut Can Produce"

### Purpose
Shows which vitamins the gut bacteria can produce well and which may need dietary support.

### Information Flow
1. "Good news" banner — which vitamins are well-produced
2. Per vitamin: status dot (green/amber/red), name, role, assessment

### Biology Logic
- Gut bacteria produce B vitamins as a byproduct of fermentation
- When specific bacteria are depleted, their vitamin production drops
- This connects directly to Bacterial Groups — e.g., absent Bifidobacteria reduces folate

---

## Tab 5: Action Plan — "What To Do About It"

### Purpose
Prioritized intervention roadmap. Shows what needs fixing (from Root Causes) and how to fix it.

### Information Flow
1. **Reversibility assessment** — "High reversibility, 8-12 weeks"
2. **Priority interventions** — ordered steps with CRITICAL/1A/1B badges
3. **Monitoring items** — healthy guilds that just need maintenance
4. **Vitamin check** — if any vitamins flagged
5. **Forecast table** — current → target projections
6. **Next steps** — what to do now
7. **Supplement teaser** — "Your personalized supplement formula will be provided separately"

### Biology Logic
- Priority comes from: severity of deviation × how important the guild is to the chain × how fragile it is
- Steps are ordered by IMPACT, not by guild number
- CRITICAL = absent or severely imbalanced; 1A/1B = moderate; Monitor = healthy

---

## Terminology Reference (Consistent Across Entire Platform)

| Internal/Scientific | Client-Facing Name | Used In |
|--------------------|--------------------|---------|
| Fiber Degraders | Fiber-processing bacteria | All tabs |
| HMO/Oligosaccharide-Utilising Bifidobacteria | Bifidobacteria | All tabs |
| Cross-Feeders | Intermediate Processors | All tabs |
| Butyrate Producers | Gut-lining energy producers | All tabs |
| Mucin Degraders | Mucus-layer bacteria | All tabs |
| Proteolytic Dysbiosis Guild | Protein-fermenting bacteria | All tabs |
| CUR | Main fuel preference | Overview dials |
| FCR | Fermentation efficiency | Overview dials |
| MDR | Gut lining dependence | Overview dials |
| PPR | Harsh byproducts | Overview dials |
| CLR | Competitive position | Never shown to client |
| GMWI2 | Health association score | Never shown to client |
| Shannon | Species diversity | Mentioned as "bacterial variety" |

---

## Cross-Tab References

| From | To | Trigger |
|------|----|---------|
| Overview → Guild Balance pillar | Bacterial Groups tab | "See your bacterial teams →" |
| Overview → Metabolic Function pillar | Overview → Dials section | "See below ↓" |
| Overview → Safety Profile pillar | Root Causes → Diagnostic flags | "See Root Causes →" |
| Root Causes → Diagnostic flags | Bacterial Groups → specific guild | "These are the bacterial teams measured in Tab 2" |
| Root Causes → Metabolic evidence | Overview → Dials | "These patterns show up in your metabolic dials on the Overview" |
| Root Causes → Reversibility | Action Plan | "See your personalized plan →" |
| Action Plan → Priority steps | Bacterial Groups → specific guild | Each step references a bacterial team |
| Action Plan → Supplement teaser | (Future) Supplement module | "Your custom formula targets these gaps" |

---

## Healthy vs Unhealthy — What Changes

| Element | Healthy Sample (85+) | Unhealthy Sample (< 60) |
|---------|---------------------|------------------------|
| Score ring | Green, "Excellent" | Amber/Red, "Fair"/"Needs Attention" |
| Root Causes | Minimal — may show just "fiber deficit" | Rich — multiple flags, loops, cascades |
| Feedback loops | None or "Virtuous Cycle" (stable) | 2-3 active loops |
| Action plan steps | 1-2 minor items | 4-5 priority interventions |
| Lifestyle inference | "Balanced diet" | "Higher protein, lower fiber variety" |
| Tone | Maintenance-focused | Restoration-focused |

---

## JSON Data Source

Every field on the platform comes from `_platform.json`. See `PLATFORM_UI_MAPPING.md` for exact field paths.

All text is `non_expert` only — the `scientific` version exists in `_microbiome_analysis.json` for internal use.
