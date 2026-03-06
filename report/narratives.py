"""
narratives.py — Generate client-facing narrative text via AWS Bedrock LLM

Uses Claude on Amazon Bedrock to generate personalized narrative text fields
by interpreting metrics DE NOVO using the structured knowledge base from
Framework v1.7 (not by reading existing reports).

Architecture:
  1. Load knowledge base JSONs (interpretation rules, guild rules, etc.)
  2. Build structured prompt with metrics + interpretation rules
  3. Call Bedrock for each narrative field
  4. Return validated text for JSON assembly
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = 'eu.anthropic.claude-sonnet-4-20250514-v1:0'
DEFAULT_REGION = 'eu-west-1'

# ══════════════════════════════════════════════════════════════
#                 KNOWLEDGE BASE LOADER
# ══════════════════════════════════════════════════════════════

_KB_CACHE = {}

def _load_knowledge_base(kb_dir: str = None) -> dict:
    """Load all knowledge base JSONs into a single dict."""
    if _KB_CACHE:
        return _KB_CACHE

    if kb_dir is None:
        kb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'knowledge_base')

    kb = {}
    for filename in os.listdir(kb_dir):
        if filename.endswith('.json'):
            key = filename.replace('.json', '')
            with open(os.path.join(kb_dir, filename)) as f:
                kb[key] = json.load(f)

    _KB_CACHE.update(kb)
    logger.info(f"Loaded {len(kb)} knowledge base files from {kb_dir}")
    return kb


# ══════════════════════════════════════════════════════════════
#                 BEDROCK CLIENT
# ══════════════════════════════════════════════════════════════

def _create_bedrock_client(region: str = DEFAULT_REGION):
    """Create a boto3 Bedrock Runtime client."""
    try:
        import boto3
        return boto3.client('bedrock-runtime', region_name=region)
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return None
    except Exception as e:
        logger.error(f"Failed to create Bedrock client: {e}")
        return None


def _call_bedrock(client, prompt: str, model_id: str = DEFAULT_MODEL_ID,
                  max_tokens: int = 800, system_prompt: str = None) -> str:
    """Call Bedrock with a prompt and return the text response."""
    if client is None:
        return "[LLM unavailable — install boto3 and configure AWS credentials]"

    messages = [{'role': 'user', 'content': prompt}]

    body = {
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': max_tokens,
        'temperature': 0.3,
        'messages': messages,
    }
    if system_prompt:
        body['system'] = system_prompt

    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(body),
        )
        result = json.loads(response['body'].read())
        return result['content'][0]['text'].strip()
    except Exception as e:
        logger.error(f"Bedrock call failed: {e}")
        return f"[LLM error: {str(e)[:200]}]"


# ══════════════════════════════════════════════════════════════
#            CONTEXT BUILDERS (metrics + knowledge base)
# ══════════════════════════════════════════════════════════════

def _build_system_prompt(kb: dict) -> str:
    """Build the system prompt with interpretation rules from knowledge base."""
    qa = kb.get('quality_and_accuracy', {})
    lang = qa.get('language_guidelines', {})
    caveats = qa.get('scientific_accuracy_caveats', {})

    return f"""You are a microbiome health interpreter writing client-facing text for a gut health report.

INTERPRETATION FRAMEWORK:
You have access to scientifically validated interpretation rules. Use them to interpret the metrics provided.

KEY RULES:
1. Use "your" language (second person, warm, encouraging)
2. No technical jargon (no CLR, GMWI2, FFA, guild names without simple explanation)
3. Frame findings as optimization opportunities, not diseases
4. Be honest but encouraging — state reality without alarming
5. Use hedging language: "may", "suggests", "consistent with" — never "causes", "proves", "definitely"
6. Abundance-function relationship is non-linear (don't assume proportional)
7. CLR values are sample-relative, not absolute measures of function

SCOPE:
- IN scope: what we see, what it means for health, ecological restoration strategies, microbial rebalancing
- OUT of scope: ANY dietary advice (no food recommendations, no fiber/protein suggestions, no meal plans), supplement dosing, medical diagnoses

CRITICAL — NO DIETARY ADVICE:
- NEVER suggest specific foods, dietary changes, or eating patterns
- NEVER mention "fiber intake", "protein intake", "dietary shifts", "eating more/less of X"
- ONLY describe ecological restoration: "expanding fiber-processing bacteria", "restoring Bifidobacteria", "rebalancing bacterial communities"
- Focus on WHAT needs to change in the microbial ecosystem, not HOW the person should eat

GUILD DESCRIPTIONS (use these simple names in non_expert text):
- Fiber degraders = "fiber-processing bacteria"
- Bifidobacteria = "Bifidobacteria" (well-known, keep as-is)
- Cross-Feeders = "connector bacteria that bridge fermentation stages"
- Butyrate Producers = "butyrate-producing bacteria" or "bacteria that produce energy for your gut lining"
- Mucin Degraders = "mucus-layer bacteria"
- Proteolytic Guild = "protein-fermenting bacteria"

METABOLIC DIALS (simple terms for non_expert):
- CUR → "main fuel preference" (carbs vs protein)
- FCR → "fermentation efficiency"
- MDR → "dietary fiber dependence vs mucus reliance"
- PPR → "gentle vs harsh byproduct balance"

DUAL INTERPRETATION FORMAT:
Every response must contain BOTH a scientific and a non-expert version.
- "scientific": Technical, references CLR, guild names, metrics. For practitioners.
- "non_expert": Warm, simple, like explaining to a friend. No scary language.
  Use this style: "Your fiber-processing team needs more support" (not "Fiber Degraders at 11% below 30% minimum").
  Model the tone on: "Break down plant foods into raw materials" / "Convert sugars into lactate — fuel for downstream teams"

MEDICAL DISCLAIMER (include concept, not verbatim):
{qa.get('quality_requirements', {}).get('stool_snapshot', '')}"""


def _build_metrics_context(data: dict, score_result: dict, fields: dict, kb: dict) -> str:
    """Build compact metrics context string with interpretation guidance."""
    guilds = data.get('guilds', {})
    gi = kb.get('guild_interpretation', {})

    # Guild lines with interpretation
    guild_lines = []
    for gname, gdata in guilds.items():
        clr_str = f"CLR {gdata['clr']:+.2f}" if gdata.get('clr') is not None else "CLR absent (<1%)"
        # Find healthy range
        range_str = ""
        for cfg_name, cfg in gi.get('guild_definitions', {}).items():
            if cfg_name in gname or gname in cfg_name:
                hr = cfg.get('healthy_range', {})
                range_str = f" [healthy: {hr.get('min')}-{hr.get('max')}%]"
                break
        guild_lines.append(f"  - {gname}: {gdata['abundance']:.1f}%{range_str} (J={gdata['redundancy']:.2f}, {clr_str})")

    # Identify FCR bottleneck
    fcr_bottleneck = _identify_fcr_bottleneck(data, kb)

    # Dietary pattern inference
    dietary = _infer_dietary_pattern(data, kb)

    return f"""SAMPLE METRICS:
Overall Score: {score_result['total']}/100 [{score_result['band']}]
  P1 Health Association: {score_result['pillars']['health_association']['score']}/{score_result['pillars']['health_association']['max']}
  P2 Diversity & Resilience: {score_result['pillars']['diversity_resilience']['score']}/{score_result['pillars']['diversity_resilience']['max']}
  P3 Metabolic Function: {score_result['pillars']['metabolic_function']['score']}/{score_result['pillars']['metabolic_function']['max']}
  P4 Guild Balance: {score_result['pillars']['guild_balance']['score']}/{score_result['pillars']['guild_balance']['max']}
  P5 Safety Profile: {score_result['pillars']['safety_profile']['score']}/{score_result['pillars']['safety_profile']['max']}

COMPOSITIONAL STATE:
  GMWI2: {data.get('GMWI2',0):+.3f} | HF: {data.get('HF',0):.3f} [{data.get('HF_label','')}] | wGMWI2: {data.get('wGMWI2',0):+.4f}
  Pattern: {fields['overall_balance']['label']} | BR: {data.get('BR',0):.3f} | SB: {data.get('SB',0):+.3f}

DIVERSITY:
  Shannon: {data.get('Shannon',0):.3f} | Pielou: {data.get('Pielou',0):.3f} → {fields['diversity_resilience']['label']}

METABOLIC DIALS:
  Main fuel: {fields['metabolic_dials']['main_fuel']['label']} (CUR={fields['metabolic_dials']['main_fuel']['value']})
  Fermentation: {fields['metabolic_dials']['fermentation_efficiency']['label']} (FCR={fields['metabolic_dials']['fermentation_efficiency']['value']})
  Gut lining: {fields['metabolic_dials']['mucus_dependency']['label']} (MDR={fields['metabolic_dials']['mucus_dependency']['value']})
  Byproducts: {fields['metabolic_dials']['putrefaction_pressure']['label']} (PPR={fields['metabolic_dials']['putrefaction_pressure']['value']})

FCR BOTTLENECK: {fcr_bottleneck}

GUILDS:
{chr(10).join(guild_lines)}

KEY STRENGTHS: {'; '.join(s.get('scientific', str(s)) if isinstance(s, dict) else s for s in fields['key_strengths'][:4])}
KEY OPPORTUNITIES: {'; '.join(o.get('scientific', str(o)) if isinstance(o, dict) else o for o in fields['key_opportunities'][:4])}

DIETARY INFERENCE: {dietary}

DYSBIOSIS MARKERS: {'All absent' if all(v == 0 for v in data.get('dysbiosis', {}).values()) else ', '.join(f"{k}: {v:.2f}%" for k, v in data.get('dysbiosis', {}).items() if v > 0)}
M. smithii: {data.get('smithii_abundance', 0):.1f}%"""


# ══════════════════════════════════════════════════════════════
#            DETERMINISTIC HELPERS
# ══════════════════════════════════════════════════════════════

def _identify_fcr_bottleneck(data: dict, kb: dict) -> str:
    """Deterministic identification of FCR bottleneck for Sluggish description."""
    guilds = data.get('guilds', {})
    fcr = data.get('FCR')

    # Get guild abundances
    bifido = 0
    cross = 0
    butyrate = 0
    for gname, gdata in guilds.items():
        if 'Bifidobacteria' in gname or 'HMO' in gname:
            bifido = gdata['abundance']
        elif 'Cross' in gname:
            cross = gdata['abundance']
        elif 'Butyrate' in gname:
            butyrate = gdata['abundance']

    gi = kb.get('guild_interpretation', {}).get('clr_diagnostic_ratios', {}).get('FCR', {})
    bottlenecks = gi.get('bottleneck_identification', {})

    if bifido < 1.0:
        return bottlenecks.get('bifido_absent', 'Bifidobacteria absent — primary lactate pathway missing')
    elif cross < 6.0:
        return bottlenecks.get('cross_feeders_low', 'Cross-feeders below minimum — intermediate processing limited')
    elif butyrate < 10.0:
        return bottlenecks.get('butyrate_low', 'Butyrate producers depleted — terminal conversion limited')
    elif fcr is not None and fcr < -0.3:
        return bottlenecks.get('general', 'Fermentation intermediates accumulating')
    else:
        return 'No specific bottleneck identified — fermentation adequate'


def _infer_dietary_pattern(data: dict, kb: dict) -> str:
    """Deterministic dietary pattern inference from CLR ratios."""
    di = kb.get('dietary_inference', {})
    templates = di.get('integrated_templates', {})

    cur = data.get('CUR') or 0
    fcr = data.get('FCR') or 0
    mdr = data.get('MDR') or 0
    ppr = data.get('PPR') or 0

    # Match against templates
    if cur < -0.3 and ppr > 0.5 and mdr >= -0.5:
        return templates.get('western_diet', {}).get('client_summary', 'Western diet pattern')
    elif cur > 0.5 and fcr > 0.3 and mdr < -0.5 and ppr < -0.5:
        return templates.get('plant_forward_optimal', {}).get('client_summary', 'Plant-forward optimal')
    elif cur > 0.3 and fcr < -0.3:
        return templates.get('supplement_driven_monoculture', {}).get('client_summary', 'Supplement-driven monoculture')
    elif cur < -0.3 and mdr > 0.5:
        return templates.get('fiber_starved', {}).get('client_summary', 'Fiber-starved pattern')
    else:
        # Check individual patterns
        patterns = []
        for key, info in di.get('clr_dietary_patterns', {}).items():
            trigger = info.get('trigger', '')
            if 'CUR > +0.5' in trigger and cur > 0.5:
                patterns.append(info.get('likely_diet', ''))
            elif 'CUR < -0.5' in trigger and cur < -0.5:
                patterns.append(info.get('likely_diet', ''))
            elif 'MDR > +0.5' in trigger and mdr > 0.5:
                patterns.append(info.get('likely_diet', ''))
        return '; '.join(patterns) if patterns else 'Mixed pattern — moderate fiber and protein balance'


# ══════════════════════════════════════════════════════════════
#                 NARRATIVE GENERATORS
# ══════════════════════════════════════════════════════════════

def generate_all_narratives(data: dict, score_result: dict, fields: dict,
                            model_id: str = DEFAULT_MODEL_ID,
                            region: str = DEFAULT_REGION) -> dict:
    """Generate all narrative fields using Bedrock LLM with knowledge base context."""
    client = _create_bedrock_client(region)
    kb = _load_knowledge_base()

    system_prompt = _build_system_prompt(kb)
    metrics_context = _build_metrics_context(data, score_result, fields, kb)

    logger.info(f"Generating narratives with model {model_id} in {region}")

    narratives = {}

    # Helper to parse dual JSON responses
    def _parse_dual(text, fallback_key='text'):
        """Parse {scientific, non_expert} JSON. Falls back to raw text if parsing fails."""
        try:
            clean = text.strip()
            if clean.startswith('```'):
                clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
                if clean.endswith('```'):
                    clean = clean[:-3]
                clean = clean.strip()
            result = json.loads(clean)
            if 'scientific' in result and 'non_expert' in result:
                return result
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
        return {'scientific': text[:500], 'non_expert': text[:500]}

    DUAL_INSTRUCTION = """
Return a JSON object with exactly two keys:
  "scientific": Technical version for practitioners (references metrics, guild names, CLR values)
  "non_expert": Simple version for the client (warm, encouraging, no jargon, no scary language)

The non_expert tone should be like: "Your fiber-processing team needs more support" or "Your gut lining energy producers are working well."
OUTPUT: Just the JSON object, nothing else."""

    # 1. Summary sentence (dual)
    narratives['summary_sentence'] = _parse_dual(_call_bedrock(client, f"""Generate 1-2 sentences describing this person's overall gut health pattern.
Mention the main pattern and primary opportunity. Maximum 50 words per version.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    # 2. What's happening summary (dual)
    narratives['whats_happening_summary'] = _parse_dual(_call_bedrock(client, f"""Generate 1-2 sentences describing what's happening in this person's gut.
Integrate diversity, metabolic function, and bacterial group balance. Maximum 60 words per version.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    # 3. Good news (dual — each sub-item has scientific + non_expert)
    good_news_text = _call_bedrock(client, f"""Generate the "Good News" section. For each of the 3 items, provide both a scientific and non_expert version.

{metrics_context}

Return a JSON object:
{{"resilience": {{"scientific": "...", "non_expert": "..."}},
 "adaptation_capacity": {{"scientific": "...", "non_expert": "..."}},
 "reversibility": {{"scientific": "...", "non_expert": "..."}}}}

Each sentence max 30 words. Be honest — only state strengths that actually exist.
OUTPUT: Just the JSON object, nothing else.""", model_id, 600, system_prompt)
    try:
        clean = good_news_text.strip()
        if clean.startswith('```'):
            clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
            if clean.endswith('```'):
                clean = clean[:-3]
            clean = clean.strip()
        narratives['good_news'] = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        narratives['good_news'] = {
            'resilience': {'scientific': good_news_text[:200], 'non_expert': good_news_text[:200]},
            'adaptation_capacity': {'scientific': '', 'non_expert': ''},
            'reversibility': {'scientific': '', 'non_expert': ''},
        }

    # 4. Possible impacts (dual)
    impacts_text = _call_bedrock(client, f"""List 2-3 potential downstream effects of the current gut patterns.
Frame as possibilities ("may", "could"), not diagnoses. Be specific to THIS person's data.

{metrics_context}

Return a JSON object:
{{"scientific": ["impact1", "impact2", "impact3"],
 "non_expert": ["impact1 in simple words", "impact2 in simple words", "impact3 in simple words"]}}

Each item max 25 words.
OUTPUT: Just the JSON object, nothing else.""", model_id, 500, system_prompt)
    try:
        clean = impacts_text.strip()
        if clean.startswith('```'):
            clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
            if clean.endswith('```'):
                clean = clean[:-3]
            clean = clean.strip()
        narratives['possible_impacts'] = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        narratives['possible_impacts'] = {'scientific': [impacts_text[:200]], 'non_expert': [impacts_text[:200]]}

    # 5. Is something wrong? (dual)
    narratives['is_something_wrong'] = _parse_dual(_call_bedrock(client, f"""Write 2-3 sentences for "Is Something Wrong?" section.
Provide honest, contextualized assessment. If there are issues, acknowledge them. If mostly healthy, say so.
The non_expert version must NOT be scary — reassure while being honest.
Maximum 60 words per version.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    # 6. Can this be fixed? (dual)
    narratives['can_this_be_fixed'] = _parse_dual(_call_bedrock(client, f"""Write 2-3 sentences for "Can This Be Fixed?" about recovery and improvement potential.
Base on actual diversity and guild redundancy data. Be encouraging but realistic.
The non_expert version should be warm and hopeful.
Maximum 60 words per version.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    # 7. Bacterial group dual interpretations (scientific + client)
    guild_data_lines = []
    for gname, gdata in fields.get('bacterial_groups', {}).items():
        guild_data_lines.append(
            f"{gname}: {gdata['abundance']}% [range: {gdata['healthy_range']}] "
            f"status={gdata['status']}, CLR={gdata.get('clr','N/A')} ({gdata['clr_status']}), "
            f"J={gdata['evenness']} ({gdata['evenness_status']})"
        )

    guild_interp_text = _call_bedrock(client, f"""For each of the 6 bacterial groups below, generate dual interpretation.

BACTERIAL GROUPS:
{chr(10).join(guild_data_lines)}

FCR BOTTLENECK: {_identify_fcr_bottleneck(data, kb)}

For EACH group, generate a JSON object with:
- "scientific": 2-3 sentences, technical, references CLR competitive status, evenness/redundancy, and range position together
- "client": 1-2 sentences, simple language explaining what it means for the person's health

Return as a JSON object where keys are the group names.

IMPORTANT: No dietary advice. Only describe ecological state and what the microbial balance means.

{metrics_context}

OUTPUT: Just the JSON object, nothing else.""", model_id, 2000, system_prompt)

    try:
        clean = guild_interp_text.strip()
        if clean.startswith('```'):
            clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
            if clean.endswith('```'):
                clean = clean[:-3]
            clean = clean.strip()
        narratives['guild_interpretations'] = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        narratives['guild_interpretations'] = {}
        logger.warning("Failed to parse guild interpretations JSON")

    # 8. Metabolic function interpretation (dual)
    narratives['metabolic_interpretation'] = _parse_dual(_call_bedrock(client, f"""Write a 3-4 sentence integrative interpretation of this person's metabolic function.
Consider all 4 metabolic dials together and explain what they mean as a whole pattern.
Maximum 80 words per version. No dietary advice.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 500, system_prompt))

    # 9. Vitamin synthesis interpretation (dual)
    vitamin_lines = []
    for vname, vdata in fields.get('vitamin_risks', {}).items():
        vitamin_lines.append(f"{vname}: risk_level={vdata['risk_level']} ({vdata['risk_label']}) — {vdata['assessment']}")

    narratives['vitamin_interpretation'] = _parse_dual(_call_bedrock(client, f"""Write 2-3 sentences summarizing this person's vitamin synthesis capacity.

VITAMIN DATA:
{chr(10).join(vitamin_lines)}

Focus on what the microbiome can and cannot produce. No dietary advice.
Maximum 60 words per version.

{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    # 10. Root causes — primary diagnosis (dual)
    narratives['root_causes_diagnosis'] = _parse_dual(_call_bedrock(client, f"""Generate a primary diagnosis statement about the likely underlying cause of imbalances.

{metrics_context}

Scientific: Technical headline referencing specific metrics (max 25 words)
Non_expert: Simple headline like "Your gut bacteria suggest recent conditions have shifted the balance" (max 20 words)

{DUAL_INSTRUCTION}""", model_id, 300, system_prompt))

    # 11. Root causes — key insights (3-4 dynamic insights, dual per insight)
    root_insights_text = _call_bedrock(client, f"""Generate 3-4 key mechanistic insights about this person's gut imbalances.
Each insight should have a memorable title and explanations in both scientific and non_expert versions.

These should be specific to THIS person's data — only include relevant insights.

{metrics_context}

Return a JSON array where each element has:
  "title": "Memorable title",
  "scientific": "2-3 sentence technical explanation with metrics",
  "non_expert": "1-2 sentence simple explanation, warm tone, no jargon"

No dietary advice — only describe microbial ecosystem dynamics.

OUTPUT: Just the JSON array, nothing else.""", model_id, 1000, system_prompt)

    try:
        clean = root_insights_text.strip()
        if clean.startswith('```'):
            clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
            if clean.endswith('```'):
                clean = clean[:-3]
            clean = clean.strip()
        narratives['root_causes_insights'] = json.loads(clean)
    except (json.JSONDecodeError, IndexError):
        narratives['root_causes_insights'] = []
        logger.warning("Failed to parse root causes insights JSON")

    # 12. Root causes — conclusion (dual)
    narratives['root_causes_conclusion'] = _parse_dual(_call_bedrock(client, f"""Write 2-3 sentences summarizing what the bacterial imbalances mean for this person.
End with a note about reversibility. Be honest but encouraging.
Maximum 50 words per version. No dietary advice.

{metrics_context}
{DUAL_INSTRUCTION}""", model_id, 400, system_prompt))

    logger.info("Narrative generation complete")
    return narratives


def generate_placeholder_narratives() -> dict:
    """Return placeholder narratives when LLM is skipped (--no-llm mode).
    All fields use dual {scientific, non_expert} format to match LLM output."""
    _skip = {'scientific': '[LLM skipped]', 'non_expert': '[LLM skipped]'}
    return {
        'summary_sentence': _skip.copy(),
        'whats_happening_summary': _skip.copy(),
        'good_news': {
            'resilience': _skip.copy(),
            'adaptation_capacity': _skip.copy(),
            'reversibility': _skip.copy(),
        },
        'possible_impacts': {'scientific': ['[LLM skipped]'], 'non_expert': ['[LLM skipped]']},
        'is_something_wrong': _skip.copy(),
        'can_this_be_fixed': _skip.copy(),
        'metabolic_interpretation': _skip.copy(),
        'vitamin_interpretation': _skip.copy(),
        'root_causes_diagnosis': _skip.copy(),
        'root_causes_insights': [],
        'root_causes_conclusion': _skip.copy(),
        'guild_interpretations': {},
    }


# ── Exported helpers ──
def get_fcr_bottleneck(data: dict) -> str:
    """Public access to FCR bottleneck identification."""
    kb = _load_knowledge_base()
    return _identify_fcr_bottleneck(data, kb)

def get_dietary_inference(data: dict) -> str:
    """Public access to dietary pattern inference."""
    kb = _load_knowledge_base()
    return _infer_dietary_pattern(data, kb)
