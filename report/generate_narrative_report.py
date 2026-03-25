#!/usr/bin/env python3
"""
generate_narrative_report.py — Generate comprehensive narrative microbiome report via Bedrock LLM

Produces a 5,000-7,000 word Markdown report in the Concise Internal Assessment format,
generated section-by-section through multiple Bedrock API calls.

Input: _microbiome_analysis.json + _only_metrics.txt
Output: {sample_id}_narrative_report.md (+ optional PDF)

Cost optimisations (v2):
  - Hybrid model routing: Opus 4 for complex reasoning sections, Sonnet 4 for structured/data sections
  - Prompt caching: system prompt + metrics context cached across all 10 calls (~80% input token saving)
  - Skip-existing: batch mode skips samples that already have a narrative report
  - --no-cache flag: fallback if EU cross-region inference profile doesn't support caching

Usage:
  python3 generate_narrative_report.py --sample-dir /path/to/sample/
  python3 generate_narrative_report.py --batch-dir /path/to/batch/
  python3 generate_narrative_report.py --batch-dir /path/to/batch/ --parallel 3
  python3 generate_narrative_report.py --batch-dir /path/to/batch/ --no-cache
  python3 generate_narrative_report.py --sample-dir /path/to/sample/ --from-json /path/to/analysis.json
"""

import argparse
import json
import logging
import os
import re
import sys
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ── Model IDs ──────────────────────────────────────────────────────────────────
OPUS_MODEL_ID    = 'eu.anthropic.claude-opus-4-6-v1'
SONNET_MODEL_ID  = 'eu.anthropic.claude-sonnet-4-20250514-v1:0'
DEFAULT_MODEL_ID = OPUS_MODEL_ID   # fallback when section has no 'model' key
DEFAULT_REGION   = 'eu-west-1'


# ══════════════════════════════════════════════════════════════
#                    BEDROCK CLIENT
# ══════════════════════════════════════════════════════════════

def _create_bedrock_client(region: str = DEFAULT_REGION):
    """Create a boto3 Bedrock Runtime client with extended timeout for large sections."""
    try:
        import boto3
        from botocore.config import Config
        config = Config(
            read_timeout=300,   # 5 minutes — large sections need 60-90s
            connect_timeout=10,
            retries={'max_attempts': 3},
        )
        return boto3.client('bedrock-runtime', region_name=region, config=config)
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return None
    except Exception as e:
        logger.error(f"Failed to create Bedrock client: {e}")
        return None


def _call_bedrock(
    client,
    dynamic_prompt: str,
    system_prompt: str,
    model_id: str = DEFAULT_MODEL_ID,
    max_tokens: int = 4000,
    cached_context: str = None,
    use_cache: bool = True,
) -> str:
    """Call Bedrock and return text response.

    Args:
        dynamic_prompt:  Section-specific instruction + accumulated context — changes every call.
        system_prompt:   Framework + rules — identical for all 10 calls on a sample.
        cached_context:  Sample metrics data — identical for all 10 calls on a sample.
                         When use_cache=True, both system_prompt and cached_context are
                         sent with cache_control=ephemeral so Bedrock caches them after
                         the first call (~5-minute TTL — long enough for all 10 sections).
        use_cache:       Whether to use prompt caching (default True). Set False if the
                         inference profile doesn't support caching.
    """
    if client is None:
        return "[LLM unavailable]"

    if use_cache and cached_context is not None:
        # ── CACHED FORMAT ──────────────────────────────────────────────
        # System prompt: list-of-blocks with cache_control on the last block
        # User message: two content blocks — cached metrics prefix + dynamic section
        # Bedrock caches blocks marked with cache_control for ~5 minutes.
        # After the 1st call, subsequent calls pay only ~10% of the input token price
        # for the cached portions.
        body = {
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': max_tokens,
            'temperature': 0.2,
            'system': [
                {
                    'type': 'text',
                    'text': system_prompt,
                    'cache_control': {'type': 'ephemeral'},
                }
            ],
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': cached_context,
                            'cache_control': {'type': 'ephemeral'},
                        },
                        {
                            'type': 'text',
                            'text': dynamic_prompt,
                        },
                    ],
                }
            ],
        }
    else:
        # ── NON-CACHED FORMAT (legacy / --no-cache fallback) ───────────
        # Metrics context is baked into the dynamic prompt by the caller.
        body = {
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': max_tokens,
            'temperature': 0.2,
            'messages': [{'role': 'user', 'content': dynamic_prompt}],
            'system': system_prompt,
        }

    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(body),
        )
        result = json.loads(response['body'].read())

        # Log cache usage when available (Bedrock returns usage stats)
        usage = result.get('usage', {})
        cache_read = usage.get('cache_read_input_tokens', 0)
        cache_write = usage.get('cache_creation_input_tokens', 0)
        if cache_read or cache_write:
            logger.debug(
                f"    Cache: {cache_read} tokens read, {cache_write} tokens written "
                f"(input: {usage.get('input_tokens', 0)}, output: {usage.get('output_tokens', 0)})"
            )

        return result['content'][0]['text'].strip()
    except Exception as e:
        logger.error(f"Bedrock call failed: {e}")
        return f"[LLM error: {str(e)[:200]}]"


# ══════════════════════════════════════════════════════════════
#                    DATA LOADING
# ══════════════════════════════════════════════════════════════

def _load_analysis_json(sample_dir: str, analysis_path: str = None) -> dict:
    """Load the _microbiome_analysis.json for a sample."""
    if analysis_path:
        with open(analysis_path) as f:
            return json.load(f)

    # Search in output dir first, then sample dir
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    automation_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')

    candidates = [
        os.path.join(sample_dir, 'reports', 'reports_json', f'microbiome_analysis_master_{sample_id}.json'),
        os.path.join(sample_dir, 'reports', 'reports_json', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(automation_dir, f'{sample_id}_microbiome_analysis.json'),
        os.path.join(sample_dir, 'report_json', f'{sample_id}_microbiome_analysis.json'),
    ]

    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)

    raise FileNotFoundError(f"No _microbiome_analysis.json found for {sample_id}")


def _load_raw_metrics(sample_dir: str) -> str:
    """Load raw _only_metrics.txt as string for LLM context."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    for subdir in ['bioinformatics/only_metrics', 'only_metrics']:
        metrics_path = os.path.join(sample_dir, subdir, f'{sample_id}_only_metrics.txt')
        if os.path.exists(metrics_path):
            with open(metrics_path) as f:
                return f.read()
    return ""


def _load_framework() -> str:
    """Load the optimized report generation framework."""
    framework_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'knowledge_base', 'concise_report_framework.md'
    )
    with open(framework_path) as f:
        return f.read()


def _has_narrative_report(sample_dir: str) -> bool:
    """Check if a narrative report already exists for this sample."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    md_path = os.path.join(sample_dir, 'reports', 'reports_md', f'narrative_report_{sample_id}.md')
    return os.path.exists(md_path)


# ══════════════════════════════════════════════════════════════
#                    METRICS CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════

def _build_priority_section(guilds: dict) -> str:
    """Build canonical priority ordering using shared guild_priority module."""
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import format_priority_text
    return format_priority_text(guilds)


def _build_section7_skeleton(guilds: dict) -> str:
    """Pre-render the Section 7 guild order skeleton so the LLM cannot reorder."""
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import build_priority_list

    items = build_priority_list(guilds)
    lines = []
    step = 0
    for item in items:
        if item["priority_score"] <= 0:
            continue  # Skip Monitor — no restoration needed
        step += 1
        lines.append(
            f"  {step}. [{item['priority_level']}] {item['guild_name']} "
            f"({item['abundance_pct']:.1f}% → target range) — "
            f"{item['scenario'].lower()}, score {item['priority_score']}"
        )
    if not lines:
        return "  All guilds at Monitor — no restoration steps needed"
    return "\n".join(lines)


def _build_metrics_context(analysis: dict, raw_metrics: str) -> str:
    """Build comprehensive metrics context for the LLM.

    This block is IDENTICAL across all 10 section calls for a given sample —
    it is the primary target for prompt caching.
    """
    sample_id = analysis.get('report_metadata', {}).get('sample_id', 'UNKNOWN')

    score       = analysis.get('overall_score', {})
    eco         = analysis.get('ecological_metrics', {})
    safety      = analysis.get('safety_profile', {})
    met         = analysis.get('metabolic_function', {})
    vitamins    = analysis.get('vitamin_synthesis', {})
    guilds      = analysis.get('bacterial_groups', {})
    root_causes = analysis.get('root_causes', {})
    debug       = analysis.get('_debug', {})
    raw         = debug.get('raw_metrics', {})

    guild_lines = []
    for gname, gdata in guilds.items():
        clr_str = f"CLR {gdata.get('clr', 'N/A')}" if gdata.get('clr') is not None else "CLR undefined (<1%)"
        guild_lines.append(
            f"  {gname}: {gdata.get('abundance', 0):.2f}% "
            f"[range: {gdata.get('healthy_range', 'N/A')}] "
            f"status={gdata.get('status', 'N/A')}, {clr_str}, "
            f"J={gdata.get('evenness', 0):.2f} ({gdata.get('evenness_status', '')})"
        )

    dials = met.get('dials', {})
    dial_lines = [
        f"  {dv.get('heading', dk)}: {dv.get('label', '')} (value={dv.get('value', 'N/A')}, raw={dv.get('raw_value', 'N/A')})"
        for dk, dv in dials.items()
    ]

    dysbiosis_lines = [
        f"  {taxon}: {info.get('abundance', 0):.4f}% — {info.get('status', '')}"
        for taxon, info in safety.get('dysbiosis_markers', {}).items()
    ]

    vitamin_lines = [
        f"  {vname}: risk={vdata['risk_level']} ({vdata.get('risk_label', '')}) — {vdata.get('assessment', '')}"
        for vname, vdata in vitamins.items()
        if isinstance(vdata, dict) and 'risk_level' in vdata
    ]

    context = f"""SAMPLE ID: {sample_id}
REPORT DATE: {datetime.now().strftime('%B %d, %Y')}

═══ OVERALL SCORE ═══
Total: {score.get('total', 0)}/100 [{score.get('band', '')}]
Description: {score.get('description', '')}
Score Drivers: {json.dumps(score.get('score_drivers', {}), indent=2)}

═══ COMPOSITIONAL METRICS ═══
GMWI2: {raw.get('GMWI2', 'N/A')}
HF: {raw.get('HF', 'N/A')}
wGMWI2: {raw.get('wGMWI2', 'N/A')}
BR: {raw.get('BR', 'N/A')}
SB: {raw.get('SB', 'N/A')}
z-score: {raw.get('z_score', 'N/A')}
Shannon: {raw.get('Shannon', 'N/A')}
Pielou: {raw.get('Pielou', 'N/A')}
FB_ratio: {raw.get('FB_ratio', 'N/A')}

═══ ECOLOGICAL STATE ═══
Overall Balance: {json.dumps(eco.get('state', {}).get('overall_balance', {}), indent=2)}
Diversity: {json.dumps(eco.get('state', {}).get('diversity_resilience', {}), indent=2)}

═══ METABOLIC DIALS (CLR Ratios) ═══
CUR raw: {raw.get('CUR', 'N/A')}
FCR raw: {raw.get('FCR', 'N/A')}
MDR raw: {raw.get('MDR', 'N/A')}
PPR raw: {raw.get('PPR', 'N/A')}
{chr(10).join(dial_lines)}

═══ GUILDS (FFA-Weighted) ═══
{chr(10).join(guild_lines)}

═══ DYSBIOSIS MARKERS ═══
{chr(10).join(dysbiosis_lines)}
M. smithii: {safety.get('M_smithii_abundance', 0):.2f}%
BCFA pathways: {safety.get('bcfa_pathways_detected', 0)}

═══ VITAMIN SIGNALS ═══
{chr(10).join(vitamin_lines)}

═══ ROOT CAUSES ═══
Diagnostic flags: {json.dumps([f.get('flag', '') for f in root_causes.get('diagnostic_flags', [])], indent=2)}
Reversibility: {root_causes.get('reversibility', {}).get('label', 'N/A')} ({root_causes.get('reversibility', {}).get('estimated_timeline', 'N/A')})

═══ CANONICAL PRIORITY INTERVENTIONS (USE THIS ORDER — do NOT re-derive) ═══
{_build_priority_section(guilds)}

═══ SECTION 7 GUILD ORDER (use this EXACT order for Restoration Priorities) ═══
{_build_section7_skeleton(guilds)}

═══ SCORE DETAILS ═══
{json.dumps(score.get('details', {}), indent=2)}
"""

    if raw_metrics:
        context += f"\n═══ RAW METRICS FILE (truncated) ═══\n{raw_metrics[:3000]}\n"

    return context


# ══════════════════════════════════════════════════════════════
#     SECTION CONFIGS — HYBRID MODEL ROUTING
# ══════════════════════════════════════════════════════════════
#
# 'model' key controls which model generates each section:
#   OPUS_MODEL_ID   — complex reasoning, ecological synthesis, clinical pattern recognition
#   SONNET_MODEL_ID — structured data presentation, defined templates, less open-ended reasoning
#
# Cost per sample (approximate):
#   5 Opus sections  × ~$0.30 = ~$1.50
#   5 Sonnet sections × ~$0.06 = ~$0.30
#   ──────────────────────────────────
#   Total with caching           ~$0.55  (vs ~$1.90 all-Opus without caching)

SECTION_CONFIGS = [
    {
        'name': 'Section 1: EXECUTIVE SUMMARY',
        'model': OPUS_MODEL_ID,   # OPUS — pattern classification specificity is critical here
        'instruction': """Generate Section 1: EXECUTIVE SUMMARY (400-500 words). Be CONCISE — every sentence must add value.

⚠️ STRUCTURE RULE: Each subsection MUST start with a ### heading on its own line. Do NOT use bold text (**...**) as a heading substitute. The exact headings are mandatory — the dashboard parser depends on them.

Use EXACTLY this structure:

## Section 1: Executive Summary

### Overall Pattern Classification
[Be SPECIFIC — e.g., "Protein-Driven Dysbiosis with Bifidobacteria Depletion" NOT generic "Transitional Pattern". Name the primary dysfunction. 2-3 sentences.]

### Dysbiosis-Associated Markers
[2-3 sentences. Define E-S abbreviation on first use. Include E-S CLR if elevated.]

### Critical Finding
[1-2 sentences. Most urgent single metabolic constraint.]

### Metabolic State Summary
[All 4 CLR ratios with values. Use "factory analogy" for clinical translation. 4-6 sentences.]

### Priority Interventions
[Clean CRITICAL/1A/1B/Monitor bullet hierarchy.]

### Structural Concerns
[Bullet list, max 5 items.]

### Health Implications
[ONE paragraph, max 80 words.]

### Functional Pathways & Vitamin Biosynthesis
[ONE paragraph, max 60 words.]""",
        'max_tokens': 2500,
    },
    {
        'name': 'Section 2: COMPOSITIONAL METRICS',
        'model': SONNET_MODEL_ID,  # SONNET — presenting numbers + reference ranges, well-structured
        'instruction': """Generate Section 2: COMPOSITIONAL METRICS (800-1,000 words).

Include subsections 2.1-2.5:
2.1 What We See — all presence/abundance/reference metrics with pattern classification
2.2 What This Means for Health — lay-friendly interpretation
2.3 Why This Happened — mechanistic drivers specific to this sample
2.4 Select Taxa Presence — table of 4 dysbiosis markers
2.5 Important Caveats""",
        'max_tokens': 4000,
    },
    {
        'name': 'Section 3: DIVERSITY SIGNATURES',
        'model': SONNET_MODEL_ID,  # SONNET — Shannon/Pielou interpretation, well-defined template
        'instruction': """Generate Section 3: DIVERSITY SIGNATURES (400-600 words).

Include 3.1-3.4:
3.1 What We See — Shannon, Pielou, zone classification
3.2 What This Means — ecosystem stability
3.3 Why This Pattern — drivers
3.4 Integration — link to compositional pattern""",
        'max_tokens': 2500,
    },
    {
        'name': 'Section 4 Part 1: GUILD FRAMEWORK + CLR RATIOS + STATUS TABLE',
        'model': OPUS_MODEL_ID,   # OPUS — 4-ratio CLR calculations + ecological interpretation
        'instruction': """Generate Section 4 subsections 4.1, 4.2, and 4.3 (1,200-1,500 words).

4.1: CLR methodology explanation + interpretation table
4.2: CLR Ratio Dashboard — calculate all 4 ratios with formulas, values, interpretations. Use: CUR = [(Fiber_CLR + Bifido_CLR)/2] - Proteo_CLR; FCR = [(Butyrate_CLR + Cross_CLR)/2] - Bifido_CLR; MDR = Mucin_CLR - Fiber_CLR; PPR = Proteo_CLR - Butyrate_CLR. When guild <1%, treat CLR as 0. Include dashboard summary table.
4.3: Guild Status Table (all 6 guilds) with structural assessment.""",
        'max_tokens': 5000,
    },
    {
        'name': 'Section 4 Part 2: DETAILED GUILD ASSESSMENTS (All 6 Guilds)',
        'model': OPUS_MODEL_ID,   # OPUS — trophic cascade reasoning, cross-feeding, restoration mechanisms
        'instruction': """Generate detailed assessments for ALL 6 guilds in A/B/C/D format (2,000-2,500 words TOTAL — approximately 350 words per guild).

Order by ecological priority for this sample. Each guild needs:
A) What We See (1-2 sentences: abundance, CLR, evenness)
B) What This Means (2-3 sentences: function, health impact)
C) Why This Pattern (2-3 sentences: drivers)
D) Ecological Restoration Mechanisms (150-200 words: metabolic role, cross-feeding, network consequences, recovery potential)

BE CONCISE. Each guild ~350 words total. No filler. Focus on what's UNIQUE to each guild.""",
        'max_tokens': 5000,
    },
    {
        'name': 'Section 4 Part 3: METABOLIC FLOW DIAGRAM',
        'model': SONNET_MODEL_ID,  # SONNET — structured ASCII diagram from data, well-defined format
        'instruction': """Generate Section 4.5: ASCII Parallel Metabolic Flow Diagram (300-500 words).

Show all 6 guilds with abundances, CLR values, substrate flows (Eat/Make/Effect), and system dynamics. Include key system insights (3-5 bullet points). Keep the diagram COMPACT.""",
        'max_tokens': 2000,
    },
    {
        'name': 'Section 5: FUNCTIONAL PATHWAYS & VITAMIN ASSESSMENT',
        'model': SONNET_MODEL_ID,  # SONNET — structured data presentation + tables
        'instruction': """Generate Section 5: FUNCTIONAL PATHWAYS & VITAMIN ASSESSMENT (600-800 words).

5.1: SCFA metabolism — pathways, capacity, realized efficiency
5.2: Vitamin signals — B12, Folate, Biotin, B-Complex with compositional risk indicators table and vitamin-specific signals. Include summary table.""",
        'max_tokens': 3000,
    },
    {
        'name': 'Section 6: INTEGRATED METABOLIC ASSESSMENT',
        'model': OPUS_MODEL_ID,   # OPUS — cross-module synthesis, convergent evidence, hardest section
        'instruction': """Generate Section 6: INTEGRATED METABOLIC ASSESSMENT (800-1,000 words).

6.1: Cross-Module Pattern Recognition — convergent evidence
6.2: Health Implications Q&A (Energy? Barrier? Inflammation? Symptoms?)
6.3: Dietary Context Inference from CLR ratios (with disclaimer)
6.4: Why This Overall Pattern Emerged""",
        'max_tokens': 4000,
    },
    {
        'name': 'Sections 7-8: RESTORATION PRIORITIES + MONITORING',
        'model': OPUS_MODEL_ID,   # OPUS — intervention logic (guild order protected by skeleton)
        'instruction': """Generate Section 7: ECOLOGICAL RESTORATION PRIORITIES (400-600 words) and Section 8: MONITORING GUIDANCE (400-500 words).

⚠️ SECTION 7 CRITICAL RULE: You MUST use the EXACT guild order from "SECTION 7 GUILD ORDER" in the sample data above.
Do NOT re-derive priorities or reorder guilds. The order is pre-computed and authoritative.
CRITICAL guilds come first, then 1A, then 1B. SKIP guilds at Monitor priority — they don't need restoration.

Section 7: For each guild (in the EXACT order from SECTION 7 GUILD ORDER above):
- State current abundance → target range (early/intermediate/optimal)
- Explain why this priority level was assigned
- Define success markers for this guild
- NO prescriptive content (no dosages, strains, specific foods)

Section 8: Red flags, positive indicators, final success markers table. Use "early changes"/"full stabilization" NOT weeks/months.""",
        'max_tokens': 4000,
    },
    {
        'name': 'Sections 9-10 + REPORT SUMMARY',
        'model': SONNET_MODEL_ID,  # SONNET — limitations/disclaimers, mostly templated content
        'instruction': """Generate Section 9: IMPORTANT LIMITATIONS (600-800 words), Section 10: MEDICAL DISCLAIMER (200-300 words), and REPORT SUMMARY (200-300 words).

Section 9: What we can/cannot measure, 5 mandatory scientific caveats (CLR sample-relative, abundance-function non-linear, proteolytic dose-dependent, guild capacity = healthy range max, substrate flow confounders), interpretation boundaries.

Section 10: Include verbatim: "This microbiome analysis provides insights into gut bacterial composition and potential metabolic functions based on DNA sequencing. It is NOT a diagnostic test and does NOT diagnose, treat, cure, or prevent any disease." + technical details.

REPORT SUMMARY: Brief 200-300 word summary of key findings, interventions, structural assessment, recovery potential.""",
        'max_tokens': 4000,
    },
]


# ══════════════════════════════════════════════════════════════
#     POST-LLM PRIORITY LABEL VALIDATION
# ══════════════════════════════════════════════════════════════

def _validate_priority_labels(section_text: str, guilds: dict) -> str:
    """Validate and auto-correct priority labels in LLM-generated text."""
    shared_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared')
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from guild_priority import build_priority_list, GUILD_DISPLAY_NAMES

    items = build_priority_list(guilds)
    VALID_LABELS = ['CRITICAL', '1A', '1B', 'Monitor']
    corrections = []
    corrected_text = section_text

    for item in items:
        if item['priority_score'] <= 0:
            continue

        correct     = item['priority_level']
        guild_name  = item['guild_name']
        guild_re    = re.escape(guild_name)
        guild_variants = [guild_re]
        display = GUILD_DISPLAY_NAMES.get(item['guild_key'], '')
        if display and display != guild_name:
            guild_variants.append(re.escape(display))

        for guild_pattern in guild_variants:
            for wrong_label in VALID_LABELS:
                if wrong_label == correct:
                    continue

                pattern = rf'(\b{re.escape(wrong_label)}\b)([\s—\-:]+)({guild_pattern})'
                match = re.search(pattern, corrected_text, re.IGNORECASE)
                if match:
                    old_text = match.group(0)
                    new_text = old_text.replace(match.group(1), correct, 1)
                    corrected_text = corrected_text.replace(old_text, new_text, 1)
                    corrections.append(
                        f"  ⚠️ PRIORITY LABEL CORRECTION: '{guild_name}' "
                        f"was '{wrong_label}' → corrected to '{correct}' "
                        f"(score {item['priority_score']:.1f})"
                    )

                pattern2 = rf'({guild_pattern})([\s—\-:]+)(\b{re.escape(wrong_label)}\b)'
                match2 = re.search(pattern2, corrected_text, re.IGNORECASE)
                if match2:
                    old_text = match2.group(0)
                    new_text = (
                        old_text[:match2.start(3) - match2.start()]
                        + correct
                        + old_text[match2.end(3) - match2.start():]
                    )
                    corrected_text = corrected_text.replace(old_text, new_text, 1)
                    corrections.append(
                        f"  ⚠️ PRIORITY LABEL CORRECTION: '{guild_name}' "
                        f"was '{wrong_label}' → corrected to '{correct}' "
                        f"(score {item['priority_score']:.1f})"
                    )

    if corrections:
        logger.warning(f"  Post-LLM priority validation: {len(corrections)} correction(s) applied")
        for c in corrections:
            logger.warning(c)
    else:
        logger.info("  Post-LLM priority validation: all labels correct ✅")

    return corrected_text


# ══════════════════════════════════════════════════════════════
#                    REPORT ASSEMBLY
# ══════════════════════════════════════════════════════════════

def generate_narrative_report(
    sample_dir: str,
    analysis_path: str = None,
    model_id: str = DEFAULT_MODEL_ID,
    region: str = DEFAULT_REGION,
    use_cache: bool = True,
) -> str:
    """Generate the complete narrative report via section-by-section Bedrock calls.

    Args:
        sample_dir:    Path to the sample directory.
        analysis_path: Override path to analysis JSON (optional).
        model_id:      Default model — used only for sections without an explicit
                       'model' key in SECTION_CONFIGS. Per-section routing takes
                       precedence.
        region:        AWS region for Bedrock.
        use_cache:     Enable prompt caching (default True). Set False if the EU
                       cross-region inference profile doesn't support cache_control.
    """
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    logger.info(f"Generating narrative report for sample: {sample_id}")
    if use_cache:
        logger.info("  Prompt caching: ENABLED (system prompt + metrics context cached across all sections)")
    else:
        logger.info("  Prompt caching: DISABLED (--no-cache flag)")

    # Load data
    analysis    = _load_analysis_json(sample_dir, analysis_path)
    raw_metrics = _load_raw_metrics(sample_dir)
    framework   = _load_framework()

    # System prompt — identical for all 10 calls → primary cache target
    system_prompt = f"""You are generating a comprehensive scientific microbiome narrative report.
Follow this framework EXACTLY:

{framework}

CRITICAL RULES:
- Third-person only (NEVER "your")
- Italicize ALL taxonomic names with asterisks: *Bacteroides*, *Firmicutes*
- Non-prescriptive (no dosages, strains, specific foods, timing)
- Add space before numbers after < and >: "< 3%" not "<3%"
- Professional ecological assessment tone
- Each section MUST stand alone as complete content
- TOTAL REPORT MUST BE 5,000-7,000 WORDS. Be CONCISE. Avoid filler. Every sentence must add value.
- For pattern classification: Be SPECIFIC (e.g., "Protein-Driven Dysbiosis" not generic "Transitional"). Name the primary dysfunction.
- Use "factory analogy" in CLR dashboard integrated state: "The factory processes X but has lost Y production line..."
- DO NOT repeat information from previous sections. Each section covers NEW ground."""

    # Metrics context — identical for all 10 calls → secondary cache target
    metrics_context = _build_metrics_context(analysis, raw_metrics)

    # Create Bedrock client
    client = _create_bedrock_client(region)
    if client is None:
        logger.error("Cannot create Bedrock client — aborting narrative report generation")
        return ""

    # Report header
    report_date = datetime.now().strftime('%B %d, %Y')
    header = f"""# Microbiome Analysis Report: Sample {sample_id}

**Report Date:** {report_date}
**Analysis Scope:** Ecological assessment of microbial composition, metabolic state, and restoration priorities

---

"""

    guilds = analysis.get('bacterial_groups', {})

    PRIORITY_SECTIONS = {
        'Section 1: EXECUTIVE SUMMARY',
        'Section 4 Part 1: GUILD FRAMEWORK + CLR RATIOS + STATUS TABLE',
        'Section 4 Part 2: DETAILED GUILD ASSESSMENTS (All 6 Guilds)',
        'Sections 7-8: RESTORATION PRIORITIES + MONITORING',
    }

    # Log model routing plan
    opus_sections   = [c['name'] for c in SECTION_CONFIGS if c.get('model', model_id) == OPUS_MODEL_ID]
    sonnet_sections = [c['name'] for c in SECTION_CONFIGS if c.get('model', model_id) == SONNET_MODEL_ID]
    logger.info(f"  Model routing: {len(opus_sections)} Opus | {len(sonnet_sections)} Sonnet")

    sections = [header]
    accumulated_context = ""

    for i, config in enumerate(SECTION_CONFIGS):
        section_model = config.get('model', model_id)
        model_tag     = '🔵 Opus' if section_model == OPUS_MODEL_ID else '🟢 Sonnet'
        logger.info(f"  [{i+1}/{len(SECTION_CONFIGS)}] {model_tag}  {config['name']}...")

        if use_cache:
            # Dynamic prompt contains ONLY the section instruction + rolling context
            # (metrics context is passed separately as a cached block)
            dynamic_prompt = f"""Generate the following section for sample {sample_id}.

{config['instruction']}

{"PREVIOUS SECTIONS (for consistency — do NOT repeat content):" + chr(10) + accumulated_context[-3000:] if accumulated_context else ""}

OUTPUT: Generate ONLY the requested section content in Markdown format. Start with the section heading. Do NOT include any meta-commentary."""

            section_text = _call_bedrock(
                client,
                dynamic_prompt=dynamic_prompt,
                system_prompt=system_prompt,
                model_id=section_model,
                max_tokens=config['max_tokens'],
                cached_context=metrics_context,
                use_cache=True,
            )
        else:
            # Non-cached: bake metrics context directly into the prompt (legacy behaviour)
            prompt = f"""Generate the following section for sample {sample_id}.

{config['instruction']}

SAMPLE DATA:
{metrics_context}

{"PREVIOUS SECTIONS (for consistency — do NOT repeat content):" + chr(10) + accumulated_context[-3000:] if accumulated_context else ""}

OUTPUT: Generate ONLY the requested section content in Markdown format. Start with the section heading. Do NOT include any meta-commentary."""

            section_text = _call_bedrock(
                client,
                dynamic_prompt=prompt,
                system_prompt=system_prompt,
                model_id=section_model,
                max_tokens=config['max_tokens'],
                cached_context=None,
                use_cache=False,
            )

        # Post-LLM priority label validation
        if config['name'] in PRIORITY_SECTIONS and guilds:
            section_text = _validate_priority_labels(section_text, guilds)

        sections.append(section_text + "\n\n")
        accumulated_context += section_text + "\n\n"

        logger.info(f"    ~{len(section_text.split())} words")

    footer = f"""---

**Report Generated:** {report_date}

---

**END OF MICROBIOME ANALYSIS REPORT**
"""
    sections.append(footer)

    full_report = '\n'.join(sections)
    logger.info(f"  Total report: ~{len(full_report.split())} words")

    return full_report


# ══════════════════════════════════════════════════════════════
#                    PDF GENERATION (OPTIONAL)
# ══════════════════════════════════════════════════════════════

def _markdown_to_pdf(md_path: str, pdf_path: str) -> bool:
    """Convert Markdown to PDF using pandoc (must be installed via brew)."""
    import subprocess
    import shutil

    pandoc_path = shutil.which('pandoc')
    if not pandoc_path:
        logger.warning("  PDF generation skipped — pandoc not found. Install with: brew install pandoc")
        return False

    try:
        result = subprocess.run(
            [pandoc_path, md_path, '-o', pdf_path,
             '--pdf-engine=xelatex',
             '-V', 'geometry:margin=2cm',
             '-V', 'fontsize=11pt',
             '-V', 'mainfont=Helvetica Neue',
             '-V', 'monofont=Courier New',
             '--highlight-style=tango'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info(f"  PDF saved: {pdf_path}")
            return True
        result2 = subprocess.run(
            [pandoc_path, md_path, '-o', pdf_path, '-V', 'geometry:margin=2cm'],
            capture_output=True, text=True, timeout=60
        )
        if result2.returncode == 0:
            logger.info(f"  PDF saved: {pdf_path}")
            return True
        logger.warning(f"  PDF generation failed: {result2.stderr[:200]}")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("  PDF generation timed out")
        return False
    except Exception as e:
        logger.warning(f"  PDF generation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════
#                    MAIN PROCESSING
# ══════════════════════════════════════════════════════════════

def process_sample(sample_dir: str, analysis_path: str = None, **kwargs) -> str:
    """Generate narrative report for a single sample."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))

    report_md = generate_narrative_report(sample_dir, analysis_path, **kwargs)

    if not report_md:
        logger.error(f"  Failed to generate narrative report for {sample_id}")
        return ""

    md_dir  = os.path.join(sample_dir, 'reports', 'reports_md')
    pdf_dir = os.path.join(sample_dir, 'reports', 'reports_pdf')
    os.makedirs(md_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    md_path = os.path.join(md_dir, f'narrative_report_{sample_id}.md')
    with open(md_path, 'w') as f:
        f.write(report_md)
    logger.info(f"  Saved: {md_path}")

    _markdown_to_pdf(md_path, os.path.join(pdf_dir, f'narrative_report_{sample_id}.pdf'))

    return md_path


def _has_analysis_json(sample_dir: str) -> bool:
    """Check if a sample has the required analysis JSON."""
    sample_id = os.path.basename(sample_dir)
    candidates = [
        os.path.join(sample_dir, 'reports', 'reports_json', f'microbiome_analysis_master_{sample_id}.json'),
        os.path.join(sample_dir, 'reports', 'reports_json', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', f'{sample_id}_microbiome_analysis.json'),
        os.path.join(sample_dir, 'report_json', f'{sample_id}_microbiome_analysis.json'),
    ]
    return any(os.path.exists(p) for p in candidates)


def _process_sample_safe(sample_dir: str, **kwargs) -> dict:
    """Thread-safe wrapper for process_sample — catches exceptions and returns result dict."""
    sample_id = os.path.basename(sample_dir)
    try:
        path = process_sample(sample_dir, **kwargs)
        return {'sample_id': sample_id, 'status': 'success', 'output': path}
    except Exception as e:
        logger.error(f"  Failed {sample_id}: {e}")
        return {'sample_id': sample_id, 'status': 'error', 'error': str(e)}


def process_batch(batch_dir: str, parallel: int = 1, force: bool = False, **kwargs):
    """Process all samples in a batch directory.

    Args:
        batch_dir: Path to batch directory containing sample subdirectories.
        parallel:  Number of samples to process concurrently (default 1 = sequential).
                   Recommended: 3 for Bedrock Opus (stays within rate limits).
        force:     If True, regenerate even if narrative_report already exists.
        **kwargs:  Passed to process_sample (model_id, region, use_cache, etc.)
    """
    sample_dirs = sorted(glob.glob(os.path.join(batch_dir, '*')))
    sample_dirs = [d for d in sample_dirs if os.path.isdir(d) and not d.endswith('.DS_Store')]

    eligible = []
    skipped_no_json = []
    skipped_exists  = []

    for sample_dir in sample_dirs:
        sample_id = os.path.basename(sample_dir)
        if not _has_analysis_json(sample_dir):
            skipped_no_json.append(sample_id)
            continue
        if not force and _has_narrative_report(sample_dir):
            skipped_exists.append(sample_id)
            continue
        eligible.append(sample_dir)

    if skipped_no_json:
        logger.warning(f"  Skipped (no analysis JSON): {skipped_no_json}")
    if skipped_exists:
        logger.info(f"  Skipped (report already exists, use --force to regenerate): {skipped_exists}")

    logger.info(
        f"Batch narrative report generation: {len(eligible)} to process"
        f"{f' (parallel={parallel})' if parallel > 1 else ''}"
        f" | {len(skipped_exists)} already done | {len(skipped_no_json)} no-data"
    )

    if not eligible:
        logger.info("  Nothing to do.")
        return []

    if parallel > 1 and len(eligible) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            future_to_sample = {
                executor.submit(_process_sample_safe, sd, **kwargs): os.path.basename(sd)
                for sd in eligible
            }
            for future in as_completed(future_to_sample):
                sample_id = future_to_sample[future]
                result    = future.result()
                results.append(result)
                status_icon = '✅' if result['status'] == 'success' else '❌'
                logger.info(
                    f"  {status_icon} {sample_id} — {result['status']}"
                    f" ({len(results)}/{len(eligible)})"
                )
    else:
        results = [_process_sample_safe(sd, **kwargs) for sd in eligible]

    success = sum(1 for r in results if r['status'] == 'success')
    errors  = sum(1 for r in results if r['status'] == 'error')
    logger.info(f"\nBatch complete: {success} success, {errors} errors out of {len(eligible)} processed")
    return results


def main():
    parser = argparse.ArgumentParser(
        description='Generate narrative microbiome report via Bedrock LLM',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--sample-dir', help='Path to single sample directory')
    group.add_argument('--batch-dir', help='Path to batch directory')

    parser.add_argument('--from-json', help='Path to specific _microbiome_analysis.json')
    parser.add_argument('--model-id', default=DEFAULT_MODEL_ID,
                        help='Default Bedrock model ID (per-section routing in SECTION_CONFIGS takes precedence)')
    parser.add_argument('--region', default=DEFAULT_REGION, help='AWS region')
    parser.add_argument('--parallel', type=int, default=1,
                        help='Number of samples to process concurrently in batch mode '
                             '(default: 1 = sequential). Recommended: 3.')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable prompt caching (fallback if EU inference profile '
                             'does not support cache_control)')
    parser.add_argument('--force', action='store_true',
                        help='Regenerate even if narrative_report already exists (batch mode)')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    kwargs = {
        'model_id':  args.model_id,
        'region':    args.region,
        'use_cache': not args.no_cache,
    }

    if args.sample_dir:
        process_sample(args.sample_dir, analysis_path=args.from_json, **kwargs)
    elif args.batch_dir:
        process_batch(args.batch_dir, parallel=args.parallel, force=args.force, **kwargs)


if __name__ == '__main__':
    main()
