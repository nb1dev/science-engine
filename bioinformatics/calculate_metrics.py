#!/usr/bin/env python3
"""
Flexible Integrated GMWI2 + Functional Report Generator (Complete Version)
--------------------------------------------------------------------------
Auto-constructs paths from batch_id and sample_id.
Outputs a comprehensive report with compositional, diversity, guild, and pathway metrics.

Usage:
    python integrated_report_flexible_COMPLETE.py --batch_id BATCH_ID --sample_id SAMPLE_ID
    
Example:
    python integrated_report_flexible_COMPLETE.py --batch_id nb1_2026_002 --sample_id 1421266404096
"""

import argparse
import logging
import math
import os
import re
import subprocess
import sys
import textwrap
import warnings
from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
import seaborn as sns
from joblib import load

# Try to import InconsistentVersionWarning (may not exist in all sklearn versions)
try:
    from sklearn.exceptions import InconsistentVersionWarning
    HAS_INCONSISTENT_WARNING = True
except ImportError:
    HAS_INCONSISTENT_WARNING = False

# Import core pathway analysis module
from core_pathway_analysis import (
    analyze_all_categories,
    get_category_summary_table,
    format_category_table_report
)

# Suppress sklearn version warnings if available
if HAS_INCONSISTENT_WARNING:
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", None)
pd.set_option("display.max_colwidth", None)
pd.set_option("display.width", None)


# === FLEXIBLE PATH CONFIGURATION ===
# These will be set dynamically based on command-line arguments
# Global variables initialized by setup_paths() function
GMWI_RESULTS_DIR = None
KNOWLEDGE_BASE_DIR = None
FUNCTIONAL_DIR = None
MODEL_PATH = None
CORE_PATHWAYS_PATH = None
INTEGRATED_REPORT_DIR = None
INTEGRATED_PLOTS_DIR = None


# === ARGUMENT PARSING ===
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Flexible Integrated Microbiome Report Generator (Complete Version)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
            # Single sample
            python integrated_report_flexible_COMPLETE.py --batch_id nb1_2026_002 --sample_id 1421266404096
            
            # All samples in batch
            python integrated_report_flexible_COMPLETE.py --batch_id nb1_2026_001 --all-samples
            
        Single sample mode:
            - Read GMWI2 results from: analysis/nb1_2026_002/1421266404096/GMWI2/
            - Read functional data from: data/nb1_2026_002/functional_profiling/1421266404096/
            - Save metrics to: analysis/nb1_2026_002/1421266404096/only_metrics/
            
        Batch mode:
            - Automatically discovers all sample directories in analysis/{batch_id}/
            - Processes each sample sequentially
            - Reports success/failure for each sample
        """)
    )
    parser.add_argument("--batch_id", required=True, help="Batch ID (e.g., nb1_2026_002)")
    parser.add_argument("--sample_id", help="Sample ID (omit to process all samples with --all-samples)")
    parser.add_argument("--all-samples", action="store_true", help="Process all samples in batch")
    return parser.parse_args()


def setup_flexible_paths(batch_id: str, sample_id: str):
    """Setup all paths based on batch_id and sample_id, and set global variables."""
    global GMWI_RESULTS_DIR, KNOWLEDGE_BASE_DIR, FUNCTIONAL_DIR
    global MODEL_PATH, CORE_PATHWAYS_PATH, INTEGRATED_REPORT_DIR, INTEGRATED_PLOTS_DIR
    
    WORK_DIR = "/Users/pnovikova/Documents/work"
    
    # Auto-constructed paths
    OUTPUT_BASE = os.path.join(WORK_DIR, "analysis", batch_id, sample_id)
    
    # Set global paths — check both legacy and current directory structures
    if os.path.exists(os.path.join(OUTPUT_BASE, "bioinformatics", "GMWI2")):
        GMWI_RESULTS_DIR = os.path.join(OUTPUT_BASE, "bioinformatics", "GMWI2")
    else:
        GMWI_RESULTS_DIR = os.path.join(OUTPUT_BASE, "GMWI2")
    FUNCTIONAL_DIR = os.path.join(WORK_DIR, "data", batch_id, "functional_profiling", sample_id)
    # Output metrics — match input structure
    if os.path.exists(os.path.join(OUTPUT_BASE, "bioinformatics", "only_metrics")):
        INTEGRATED_REPORT_DIR = os.path.join(OUTPUT_BASE, "bioinformatics", "only_metrics")
    elif os.path.exists(os.path.join(OUTPUT_BASE, "bioinformatics")):
        INTEGRATED_REPORT_DIR = os.path.join(OUTPUT_BASE, "bioinformatics", "only_metrics")
    else:
        INTEGRATED_REPORT_DIR = os.path.join(OUTPUT_BASE, "only_metrics")
    INTEGRATED_PLOTS_DIR = os.path.join(OUTPUT_BASE, "plots")
    
    # Shared resources (fixed paths)
    KNOWLEDGE_BASE_DIR = os.path.join(WORK_DIR, "analysis", "knowledge_base")
    MODEL_PATH = Path(os.path.join(WORK_DIR, "scripts", "models", "GMWI2_model.joblib"))
    CORE_PATHWAYS_PATH = Path(KNOWLEDGE_BASE_DIR) / "core_pathways_keywords.tsv"
    
    # Create output directories
    os.makedirs(INTEGRATED_REPORT_DIR, exist_ok=True)
    os.makedirs(INTEGRATED_PLOTS_DIR, exist_ok=True)
    log_dir = os.path.join(OUTPUT_BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    return {
        "WORK_DIR": WORK_DIR,
        "BATCH_ID": batch_id,
        "SAMPLE_ID": sample_id,
        "LOG_DIR": log_dir,
        "GMWI_RESULTS_DIR": GMWI_RESULTS_DIR,
        "FUNCTIONAL_DIR": FUNCTIONAL_DIR,
        "INTEGRATED_REPORT_DIR": INTEGRATED_REPORT_DIR,
        "INTEGRATED_PLOTS_DIR": INTEGRATED_PLOTS_DIR,
        "KNOWLEDGE_BASE_DIR": KNOWLEDGE_BASE_DIR,
        "MODEL_PATH": MODEL_PATH,
        "CORE_PATHWAYS_PATH": CORE_PATHWAYS_PATH,
    }


def setup_logging(paths: dict):
    """Setup logging to file and stdout."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(paths["LOG_DIR"], f"{paths['SAMPLE_ID']}_integrated_report_{timestamp}.log")
    
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logging.info("="*60)
    logging.info("Integrated Microbiome Report Pipeline (Complete Version)")
    logging.info("="*60)
    logging.info(f"Batch ID: {paths['BATCH_ID']}")
    logging.info(f"Sample ID: {paths['SAMPLE_ID']}")
    logging.info(f"Input GMWI2: {paths['GMWI_RESULTS_DIR']}")
    logging.info(f"Input Functional: {paths['FUNCTIONAL_DIR']}")
    logging.info(f"Output Metrics: {paths['INTEGRATED_REPORT_DIR']}")
    logging.info(f"Output Plots: {paths['INTEGRATED_PLOTS_DIR']}")
    logging.info(f"Log file: {log_file}")
    logging.info("="*60)
    
    return log_file

# FFA (Fractional Functional Attribution) Weight Table
# Per Guild_analysis_v3.md Section 1.2
# Each organism contributes fractionally to multiple guilds
FFA_WEIGHTS = {
    # Butyrate-focused organisms
    "Subdoligranulum_variabile": {
        "Butyrate Producers": 1.0
    },
    "Coprococcus_comes": {
        "Butyrate Producers": 0.8,
        "Cross-Feeders": 0.2
    },
    "Coprococcus_eutactus": {
        "Butyrate Producers": 0.8,
        "Cross-Feeders": 0.2
    },
    "Coprococcus_catus": {
        "Butyrate Producers": 0.8,
        "Cross-Feeders": 0.2
    },
    "Flavonifractor_plautii": {
        "Butyrate Producers": 0.7,
        "Cross-Feeders": 0.3
    },
    "Intestinimonas_butyriciproducens": {
        "Butyrate Producers": 0.7,
        "Cross-Feeders": 0.3
    },
    "Eubacterium_eligens": {
        "Butyrate Producers": 0.75,
        "Cross-Feeders": 0.25
    },
    "Faecalibacterium_prausnitzii": {
        "Butyrate Producers": 0.75,
        "Cross-Feeders": 0.25
    },
    "Eubacterium_rectale": {
        "Butyrate Producers": 0.5,
        "Cross-Feeders": 0.1,
        "Fiber Degraders": 0.4
    },
    "Agathobacter_rectalis": {
        "Butyrate Producers": 0.5,
        "Cross-Feeders": 0.1,
        "Fiber Degraders": 0.4
    },
    "Anaerostipes_hadrus": {
        "Butyrate Producers": 0.4,
        "Cross-Feeders": 0.6
    },
    "Anaerostipes_caccae": {
        "Butyrate Producers": 0.4,
        "Cross-Feeders": 0.6
    },
    "Anaerostipes_butyraticus": {
        "Butyrate Producers": 0.4,
        "Cross-Feeders": 0.6
    },
    "Anaerobutyricum_soehngenii": {
        "Butyrate Producers": 0.35,
        "Cross-Feeders": 0.65
    },
    "Eubacterium_hallii": {
        "Butyrate Producers": 0.45,
        "Cross-Feeders": 0.55
    },
    "Anaerobutyricum_hallii": {
        "Butyrate Producers": 0.45,
        "Cross-Feeders": 0.55
    },
    
    # Roseburia and related fiber-butyrate generalists
    "Roseburia_intestinalis": {
        "Butyrate Producers": 0.4,
        "Cross-Feeders": 0.3,
        "Fiber Degraders": 0.3
    },
    "Roseburia_inulinivorans": {
        "Butyrate Producers": 0.4,
        "Cross-Feeders": 0.3,
        "Fiber Degraders": 0.3
    },
    "Roseburia_hominis": {
        "Butyrate Producers": 0.45,
        "Cross-Feeders": 0.25,
        "Fiber Degraders": 0.3
    },
    "Roseburia_faecis": {
        "Butyrate Producers": 0.45,
        "Cross-Feeders": 0.25,
        "Fiber Degraders": 0.3
    },
    
    # Fiber degraders (primary)
    "Ruminococcus_bromii": {
        "Fiber Degraders": 1.0
    },
    "Ruminococcus_champanellensis": {
        "Fiber Degraders": 1.0
    },
    "Ruminococcus_callidus": {
        "Fiber Degraders": 1.0
    },
    "Prevotella_copri": {
        "Fiber Degraders": 1.0
    },
    "Prevotella_ruminicola": {
        "Fiber Degraders": 1.0
    },
    "Bacteroides_ovatus": {
        "Fiber Degraders": 1.0
    },
    "Bacteroides_uniformis": {
        "Fiber Degraders": 1.0
    },
    "Bacteroides_xylanisolvens": {
        "Fiber Degraders": 1.0
    },
    "Parabacteroides_distasonis": {
        "Fiber Degraders": 0.6,
        "Mucin Degraders": 0.4
    },
    "Bacteroides_thetaiotaomicron": {
        "Fiber Degraders": 0.6,
        "Mucin Degraders": 0.35,
        "Proteolytic Dysbiosis Guild": 0.05
    },
    "Blautia_wexlerae": {
        "Fiber Degraders": 0.7,
        "Cross-Feeders": 0.3
    },
    "Blautia_coccoides": {
        "Fiber Degraders": 0.7,
        "Cross-Feeders": 0.3
    },
    
    # HMO/Oligosaccharide-Utilising Bifidobacteria (organisms)
    "Bifidobacterium_longum": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    "Bifidobacterium_adolescentis": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    "Bifidobacterium_bifidum": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    "Bifidobacterium_breve": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    "Bifidobacterium_catenulatum": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    "Bifidobacterium_pseudocatenulatum": {
        "Cross-Feeders": 0.5,
        "HMO/Oligosaccharide-Utilising Bifidobacteria": 0.5
    },
    
    # Cross-feed specialists (non-butyrate)
    "Phascolarctobacterium_faecium": {
        "Cross-Feeders": 1.0
    },
    "Methanobrevibacter_smithii": {
        "Cross-Feeders": 1.0
    },
    "Blautia_hydrogenotrophica": {
        "Cross-Feeders": 1.0
    },
    
    # Mucin degraders and dysbiosis-linked taxa
    "Akkermansia_muciniphila": {
        "Mucin Degraders": 1.0
    },
    "Ruminococcus_gnavus": {
        "Mucin Degraders": 0.65,
        "Proteolytic Dysbiosis Guild": 0.35
    },
    "Ruminococcus_torques": {
        "Mucin Degraders": 0.65,
        "Proteolytic Dysbiosis Guild": 0.35
    },
    "Bacteroides_fragilis": {
        "Mucin Degraders": 0.6,
        "Proteolytic Dysbiosis Guild": 0.4
    },
    
    # Proteolytic dysbiosis core organisms
    "Alistipes_putredinis": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Alistipes_finegoldii": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Bilophila_wadsworthia": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Desulfovibrio_piger": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Clostridium_perfringens": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Fusobacterium_nucleatum": {
        "Proteolytic Dysbiosis Guild": 1.0
    },
    "Escherichia_coli": {
        "Proteolytic Dysbiosis Guild": 1.0
    }
}

# Define all guild names for consistent ordering
GUILD_NAMES = [
    "Fiber Degraders",
    "HMO/Oligosaccharide-Utilising Bifidobacteria",
    "Cross-Feeders",
    "Butyrate Producers",
    "Mucin Degraders",
    "Proteolytic Dysbiosis Guild"
]

# Healthy Reference Ranges for guild abundances
# Per Guild_analysis_v3.md Section 2.3.2
# Ranges are soft envelopes from healthy adult cohorts, not diagnostic thresholds
# IMPORTANT: Healthy varies by geography, diet, age, baseline state
# Deviations without symptoms may represent:
#   - Individual stable baseline (alternative stable state)
#   - Dietary adaptation (long-term pattern)
#   - Benign variation within healthy diversity
# Priority ranking assumes symptomatic client or optimization goal
# Always correlate deviations with symptoms before declaring pathology
HEALTHY_REFERENCE_RANGES = {
    "Fiber Degraders": {
        "min": 0.30,  # 30%
        "max": 0.50,  # 50%
        "optimal": 0.4,  # 40%
        "interpretation": "Primary substrate processors"
    },
    "HMO/Oligosaccharide-Utilising Bifidobacteria": {
        "min": 0.02,  # 2%
        "max": 0.10,  # 10%
        "optimal": 0.06,  # 6%
        "interpretation": "Early fermentation amplifiers"
    },
    "Cross-Feeders": {
        "min": 0.06,  # 6%
        "max": 0.12,  # 12%
        "optimal": 0.09,  # 9%
        "interpretation": "Critical metabolic connectors"
    },
    "Butyrate Producers": {
        "min": 0.10,  # 10%
        "max": 0.25,  # 25%
        "optimal": 0.175,  # 17.5%
        "interpretation": "Terminal SCFA producers"
    },
    "Mucin Degraders": {
        "min": 0.01,  # 1%
        "max": 0.04,  # 4%
        "optimal": 0.025,  # 2.5%
        "interpretation": "Host-substrate users"
    },
    "Proteolytic Dysbiosis Guild": {
        "min": 0.01,  # <1%
        "max": 0.05,  # 2%
        "optimal": 0.03,  # 3%
        "interpretation": "Putrefactive metabolism"
    }
}

# Pathway role keywords for functional analysis
PATHWAY_ROLE_KEYWORDS = [
    ("butyrate", "Short-chain fatty acid (butyrate) synthesis"),
    ("propanoate", "Short-chain fatty acid (propionate) synthesis"),
    ("propionate", "Short-chain fatty acid (propionate) synthesis"),
    ("acetate", "Short-chain fatty acid (acetate) metabolism"),
    ("succinate", "Succinate-associated energy metabolism"),
    ("glycolysis", "Central carbon (glycolysis) metabolism"),
    ("tca", "TCA/energy cycle"),
    ("tricarboxylic", "TCA/energy cycle"),
    ("vitamin", "Vitamin biosynthesis"),
    ("biotin", "Biotin (B7) biosynthesis"),
    ("folate", "Folate/one-carbon metabolism"),
    ("pyridoxal", "Vitamin B6 biosynthesis"),
    ("cobalamin", "Vitamin B12 biosynthesis"),
    ("bile", "Bile acid transformation"),
    ("lysine", "Amino acid (lysine) biosynthesis"),
    ("valine", "Branched-chain amino acid biosynthesis"),
    ("isoleucine", "Branched-chain amino acid biosynthesis"),
    ("methionine", "Sulfur amino acid metabolism"),
    ("adenosine", "Purine/pyrimidine metabolism"),
]


# === UTILITY FUNCTIONS ===
def assess_vitamin_supplementation_signals(
    metaphlan_df: pd.DataFrame,
    diversity_df: pd.DataFrame,
    fb_ratio: float
) -> dict:
    """
    Assess metagenomic signals for vitamin supplementation decisions.
    
    Based on vitamin-specific_assessment_signals.txt
    
    Parameters:
    -----------
    metaphlan_df : pd.DataFrame
        MetaPhlAn abundance data with taxonomic ranks
    diversity_df : pd.DataFrame
        Diversity metrics by rank
    fb_ratio : float
        Firmicutes/Bacteroidetes ratio
        
    Returns:
    --------
    dict with vitamin-specific metagenomic signals
    """
    signals = {}
    
    # Extract Shannon diversity
    species_row = diversity_df[diversity_df["rank"] == "s"]
    shannon = species_row["shannon"].iloc[0] if not species_row.empty else float("nan")
    
    # Bacteroides genus abundance
    bacteroides_genus = metaphlan_df[
        (metaphlan_df["rank"] == "g") & 
        (metaphlan_df["leaf_token"].str.contains("g__Bacteroides", case=False, na=False))
    ]["rel_abund_prop"].sum()
    signals["bacteroides_pct"] = bacteroides_genus * 100
    
    # Folate-producing Bifidobacterium species (8 specific species only)
    FOLATE_BIFIDO_SPECIES = [
        "B.adolescentis",
        "B.longum",  # includes subsp. longum
        "B.pseudocatenulatum",
        "B.catenulatum",
        "B.bifidum",
        "B.dentium",
        "B.breve",
        "B.angulatum"
    ]
    
    bifido_folate_species = metaphlan_df[
        (metaphlan_df["rank"] == "s") &  # species level
        (metaphlan_df["clade_name"].str.contains("|".join(FOLATE_BIFIDO_SPECIES), na=False))
    ]["rel_abund_prop"].sum()
    signals["bifido_genus_pct"] = bifido_folate_species * 100  # Variable name kept for compatibility
    
    # Bacteroides ovatus (species level)
    b_ovatus = metaphlan_df[
        (metaphlan_df["rank"] == "s") & 
        (metaphlan_df["leaf_token"].str.contains("s__Bacteroides_ovatus", case=False, na=False))
    ]["rel_abund_prop"].sum()
    signals["b_ovatus_pct"] = b_ovatus * 100
    
    # Lachnospiraceae + Ruminococcaceae (family level)
    lachno = metaphlan_df[
        (metaphlan_df["rank"] == "f") & 
        (metaphlan_df["leaf_token"].str.contains("f__Lachnospiraceae", case=False, na=False))
    ]["rel_abund_prop"].sum()
    
    rumino = metaphlan_df[
        (metaphlan_df["rank"] == "f") & 
        (metaphlan_df["leaf_token"].str.contains("f__Ruminococcaceae", case=False, na=False))
    ]["rel_abund_prop"].sum()
    
    signals["lachno_rumino_pct"] = (lachno + rumino) * 100
    
    # Akkermansia muciniphila (species level)
    akkermansia = metaphlan_df[
        (metaphlan_df["rank"] == "s") & 
        (metaphlan_df["leaf_token"].str.contains("s__Akkermansia_muciniphila", case=False, na=False))
    ]["rel_abund_prop"].sum()
    signals["akkermansia_pct"] = akkermansia * 100
    
    # Biotin producers (4 specific species)
    def check_species(species_name):
        abund = metaphlan_df[
            (metaphlan_df["rank"] == "s") & 
            (metaphlan_df["leaf_token"].str.contains(species_name, case=False, na=False))
        ]["rel_abund_prop"].sum()
        if abund > 0:
            return f"Present ({abund*100:.2f}%)"
        else:
            return "Absent"
    
    signals["b_fragilis_status"] = check_species("s__Bacteroides_fragilis")
    signals["p_copri_status"] = check_species("s__Prevotella_copri")
    signals["f_varium_status"] = check_species("s__Fusobacterium_varium")
    signals["c_coli_status"] = check_species("s__Campylobacter_coli")
    
    # Count biotin producers
    producer_count = sum([
        1 if "Present" in signals["b_fragilis_status"] else 0,
        1 if "Present" in signals["p_copri_status"] else 0,
        1 if "Present" in signals["f_varium_status"] else 0,
        1 if "Present" in signals["c_coli_status"] else 0
    ])
    signals["biotin_producer_count"] = producer_count
    
    # Folate risk score (0-3)
    folate_risk = 0
    if shannon < 2.0:
        folate_risk += 1
    if signals["bacteroides_pct"] < 5.0:
        folate_risk += 1
    if signals["bifido_genus_pct"] < 2.0:
        folate_risk += 1
    signals["folate_risk_score"] = folate_risk
    
    # B-complex risk score (0-3)
    b_complex_risk = 0
    if signals["bacteroides_pct"] < 10.0:
        b_complex_risk += 1
    if fb_ratio > 2.0:
        b_complex_risk += 1
    if signals["lachno_rumino_pct"] < 2.0:
        b_complex_risk += 1
    signals["b_complex_risk_score"] = b_complex_risk
    
    return signals


def clr_transform(values: np.ndarray, pseudocount: Optional[float] = None) -> np.ndarray:
    """Centered log-ratio transform with soft pseudocount for zeros."""
    x = np.asarray(values, dtype=float)
    if pseudocount is None:
        min_pos = x[x > 0].min() if np.any(x > 0) else 1e-12
        pseudocount = 0.1 * min_pos
    x = np.where(x <= 0, pseudocount, x)
    geometric_mean = np.exp(np.mean(np.log(x)))
    return np.log(x / geometric_mean)


def geom_mean(values: np.ndarray) -> float:
    """Geometric mean ignoring zeros."""
    arr = np.asarray(values, dtype=float)
    arr = arr[arr > 0]
    return float(np.exp(np.mean(np.log(arr)))) if arr.size else 0.0


def summarize_pathway_roles(df: pd.DataFrame, column: str = "pathway", top_n: int = 5) -> str:
    if df is None or df.empty or column not in df.columns:
        return "None"
    role_counts: dict[str, int] = {}
    for val in df[column].dropna():
        name = str(val).lower()
        matched = False
        for keyword, role in PATHWAY_ROLE_KEYWORDS:
            if keyword in name:
                role_counts[role] = role_counts.get(role, 0) + 1
                matched = True
        if not matched:
            role_counts["Other metabolic functions"] = role_counts.get("Other metabolic functions", 0) + 1
    top_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    formatted = [f"{role} (n={count})" for role, count in top_roles]
    return ", ".join(formatted) if formatted else "None"


# === GUILD & DIVERSITY ANALYSIS (with FFA Weighting) ===
def get_ffa_weights_for_taxon(taxa_name: str) -> dict:
    """
    Get FFA weights for a taxon from the FFA_WEIGHTS table.
    
    Returns dict mapping guild_name -> weight (0-1)
    Weights sum to 1.0 for each organism.
    """
    if pd.isna(taxa_name):
        return {}
    
    # Extract species/genus name from MetaPhlAn format
    # e.g., "k__Bacteria|p__Firmicutes|...|s__Faecalibacterium_prausnitzii"
    taxa_str = str(taxa_name).lower()
    
    # Try to match against FFA_WEIGHTS keys
    for organism_key, weights in FFA_WEIGHTS.items():
        if organism_key.lower() in taxa_str:
            return weights
    
    # No FFA weight found - assign to single guild based on simple heuristics
    # (for organisms not in FFA table, assume single-guild membership)
    return {}


def analyze_functional_guilds_ffa(metaphlan_df: pd.DataFrame, rank_filter: str = "s") -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Analyze functional guild composition using FFA (Fractional Functional Attribution).
    
    Per Guild_analysis_v3.md Section 1.3:
    For each guild G:
        Abundance(G) = Σᵢ (organism_abundance_i × weight_i,G)
    
    This ensures:
    - No double-counting (guilds are orthogonal)
    - Accurate metabolic flux representation
    - CLR assumptions hold
    """
    # Filter to species level
    df = metaphlan_df[metaphlan_df["rank"] == rank_filter].copy()
    
    # Initialize guild accumulation dict
    guild_abundances = {guild: 0.0 for guild in GUILD_NAMES}
    
    # Track taxa contributing to each guild (for redundancy calculation)
    guild_taxa_dict = {guild: [] for guild in GUILD_NAMES}
    
    # Apply FFA weights
    for _, row in df.iterrows():
        taxa_name = row["clade_name"]
        abundance = row["rel_abund_prop"]
        clr = row["clr"]
        
        # Get FFA weights for this organism
        ffa_weights = get_ffa_weights_for_taxon(taxa_name)
        
        if not ffa_weights:
            # Organism not in FFA table - skip (or could assign to "Unassigned")
            continue
        
        # Distribute this organism's abundance fractionally across guilds
        for guild_name, weight in ffa_weights.items():
            if guild_name in guild_abundances:
                guild_abundances[guild_name] += abundance * weight
                
                # Track this taxon's contribution to the guild
                guild_taxa_dict[guild_name].append({
                    "clade_name": taxa_name,
                    "rel_abund_prop": abundance,
                    "ffa_weight": weight,
                    "ffa_contribution": abundance * weight,
                    "clr": clr
                })
    
    # Calculate guild-level metrics
    guild_metrics = []
    guild_taxa_records = []
    
    for guild_name in GUILD_NAMES:
        taxa_list = guild_taxa_dict[guild_name]
        
        if not taxa_list:
            # Guild is absent
            guild_metrics.append({
                "guild": guild_name,
                "n_taxa": 0,
                "total_abundance": 0.0,
                "mean_clr": np.nan,
                "pielou_evenness": np.nan,
                "status": "Absent"
            })
            continue
        
        # Calculate metrics
        n_taxa = len(taxa_list)
        total_abund = guild_abundances[guild_name]
        
        # Mean CLR (weighted by FFA contribution)
        clr_values = [t["clr"] for t in taxa_list if not pd.isna(t["clr"])]
        contributions = [t["ffa_contribution"] for t in taxa_list if not pd.isna(t["clr"])]
        
        if clr_values and contributions:
            mean_clr = np.average(clr_values, weights=contributions)
        else:
            mean_clr = np.nan
        
        # Pielou evenness (based on FFA-weighted contributions within guild)
        if n_taxa > 1:
            contribs = np.array([t["ffa_contribution"] for t in taxa_list])
            p = contribs / contribs.sum()
            H = -np.sum(p * np.log(p + 1e-10))
            H_max = np.log(n_taxa)
            pielou = H / H_max
        else:
            pielou = 0.0
        
        # Guild status (based on mean CLR)
        status = "Enriched" if mean_clr > 0.5 else ("Depleted" if mean_clr < -0.5 else "Balanced")
        
        guild_metrics.append({
            "guild": guild_name,
            "n_taxa": n_taxa,
            "total_abundance": total_abund,
            "mean_clr": mean_clr,
            "pielou_evenness": pielou,
            "status": status
        })
        
        # Build detailed taxa list for this guild
        for taxon in taxa_list:
            guild_taxa_records.append({
                "guilds": guild_name,
                "clade_name": taxon["clade_name"],
                "rel_abund_prop": taxon["rel_abund_prop"],
                "ffa_weight": taxon["ffa_weight"],
                "ffa_contribution": taxon["ffa_contribution"],
                "clr": taxon["clr"]
            })
    
    guild_summary = pd.DataFrame(guild_metrics)
    guild_summary = guild_summary.sort_values("total_abundance", ascending=False)
    
    guild_taxa = pd.DataFrame(guild_taxa_records) if guild_taxa_records else pd.DataFrame()
    
    return guild_summary, guild_taxa


def compute_diversity_metrics(abundances):
    """Compute diversity indices for a composition vector."""
    x = np.array(abundances)
    x = x[x > 0]
    
    if len(x) == 0:
        return {"richness": 0, "shannon": 0, "simpson": 0, "pielou": 0}
    
    # Normalize
    p = x / x.sum()
    
    # Shannon
    shannon = -np.sum(p * np.log(p + 1e-10))
    
    # Simpson (1 - dominance)
    simpson = 1 - np.sum(p**2)
    
    # Pielou evenness
    richness = len(x)
    H_max = np.log(richness)
    pielou = shannon / H_max if richness > 1 else 0.0
    
    return {"richness": richness, "shannon": shannon, "simpson": simpson, "pielou": pielou}


def analyze_rank_diversity(metaphlan_df):
    """Compute diversity metrics at each taxonomic rank."""
    rank_order = ['k', 'p', 'c', 'o', 'f', 'g', 's']
    ranks = [r for r in rank_order if r in metaphlan_df["rank"].dropna().unique()]
    
    diversity_results = []
    for rank in ranks:
        rank_df = metaphlan_df[metaphlan_df["rank"] == rank]
        abundances = rank_df["rel_abund_prop"].values
        
        metrics = compute_diversity_metrics(abundances)
        metrics["rank"] = rank
        metrics["rank_name"] = {
            'k': 'Kingdom', 'p': 'Phylum', 'c': 'Class',
            'o': 'Order', 'f': 'Family', 'g': 'Genus', 's': 'Species'
        }.get(rank, rank)
        
        diversity_results.append(metrics)
    
    return pd.DataFrame(diversity_results)[["rank", "rank_name", "richness", "shannon", "simpson", "pielou"]]


def interpret_diversity_pattern(diversity_df):
    """Provide interpretation of diversity patterns across ranks."""
    print("\n" + "="*70)
    print("DIVERSITY PATTERN INTERPRETATION")
    print("="*70 + "\n")
    
    species_row = diversity_df[diversity_df["rank"] == "s"]
    if not species_row.empty:
        s_pielou = species_row["pielou"].iloc[0]
        s_shannon = species_row["shannon"].iloc[0]
        
        print(f"Species-level diversity:")
        print(f"  Shannon: {s_shannon:.3f}")
        print(f"  Pielou evenness: {s_pielou:.3f}")
        
        if s_pielou > 0.7:
            print(f"  → High evenness: Balanced, robust community")
        elif s_pielou > 0.4:
            print(f"  → Moderate evenness: Some dominant taxa, moderate fragility")
        else:
            print(f"  → Low evenness: Highly dominated, fragile ecosystem")
        print()
    
    # Check where dysbiosis starts
    for i in range(len(diversity_df) - 1):
        current = diversity_df.iloc[i]
        next_rank = diversity_df.iloc[i + 1]
        
        if current["pielou"] > 0.6 and next_rank["pielou"] < 0.4:
            print(f"⚠️  Dysbiosis pattern detected:")
            print(f"   {current['rank_name']} level balanced (J={current['pielou']:.2f})")
            print(f"   {next_rank['rank_name']} level imbalanced (J={next_rank['pielou']:.2f})")
            print(f"   → Suggests specific lineage overgrowth\n")


def compute_guild_correlations(guild_summary, metaphlan_df):
    """Correlate guild abundances with F/B ratio and key compositional metrics."""
    # Get F/B ratio
    phyla = metaphlan_df[metaphlan_df["rank"] == "p"].copy()
    firm = phyla[phyla["leaf_token"] == "p__Firmicutes"]["rel_abund_prop"].sum()
    bact = phyla[phyla["leaf_token"] == "p__Bacteroidetes"]["rel_abund_prop"].sum()
    fb_ratio = firm / bact if bact > 0 else np.nan
    
    print("\n" + "="*70)
    print("GUILD-LEVEL ECOLOGICAL RELATIONSHIPS")
    print("="*70 + "\n")
    
    print(f"Firmicutes/Bacteroidetes ratio: {fb_ratio:.3f}")
    print()
    
    # Guild abundance correlations with F/B
    print("Guild contributions to phylum balance:")
    for _, row in guild_summary.iterrows():
        if row["n_taxa"] > 0:
            print(f"  {row['guild']:<25} Abundance: {row['total_abundance']:.4f}, "
                  f"Redundancy: {row['pielou_evenness']:.2f}, "
                  f"Status: {row['status']}")
    
    return fb_ratio


def assess_guild_fragility(guild_summary):
    """Identify fragile guilds that need intervention."""
    print("\n" + "="*70)
    print("GUILD FRAGILITY ASSESSMENT")
    print("="*70)
    print("\nGuilds with compositional issues requiring dietary intervention:")
    print("  • Issue: Specific compositional problem identified")
    print("  • Priority: Importance level (Critical/High/Moderate)")
    print("  • Recommended Action: Suggested dietary strategy\n")
    
    fragile_guilds = []
    
    for _, row in guild_summary.iterrows():
        if row["n_taxa"] == 0:
            fragile_guilds.append({
                "Guild": row["guild"],
                "Issue": "Completely absent",
                "Priority": "Critical",
                "Recommended Action": "Introduce via diet/probiotics"
            })
        elif row["n_taxa"] == 1:
            fragile_guilds.append({
                "Guild": row["guild"],
                "Issue": "Single taxon (no redundancy)",
                "Priority": "High",
                "Recommended Action": "Diversify guild members"
            })
        elif row["pielou_evenness"] < 0.3 and row["n_taxa"] > 1:
            fragile_guilds.append({
                "Guild": row["guild"],
                "Issue": f"Low evenness (J={row['pielou_evenness']:.2f})",
                "Priority": "Moderate",
                "Recommended Action": "Balance guild composition"
            })
    
    if fragile_guilds:
        fragile_df = pd.DataFrame(fragile_guilds)
        print(fragile_df.to_string(index=False))
    else:
        print("No fragile guilds detected - all functional groups show adequate redundancy")
    
    return fragile_guilds


# === COMPOSITIONAL METRICS ===
def interpret_health_fraction(hf: float) -> str:
    """
    Interpret Health Fraction value based on compositional state.
    
    Health Fraction scale:
    - 0 → Worst possible microbiome (all opportunistic taxa)
    - 1 → Best possible microbiome (all supportive taxa)
    - <0.53 → Depleted state
    - 0.53-0.61 → Transitional
    - >0.61 → Resilient microbiome
    """
    if math.isnan(hf):
        return "undefined"
    elif hf < 0.53:
        return "Depleted"
    elif hf <= 0.61:
        return "Transitional"
    else:
        return "Resilient"


def compute_compositional_metrics(sample: str) -> dict:
    """Compute all compositional wellness metrics from GMWI2 model."""
    taxa_path = Path(GMWI_RESULTS_DIR) / f"{sample}_run_GMWI2_taxa.txt"
    gmwi_path = Path(GMWI_RESULTS_DIR) / f"{sample}_run_GMWI2.txt"
    
    if not taxa_path.exists():
        raise FileNotFoundError(f"Missing taxa file: {taxa_path}")
    
    # Load model
    model = load(MODEL_PATH)
    df = pd.DataFrame({
        "taxa_name": model.feature_names_in_,
        "coefficient": model.coef_.flatten()
    })
    
    S_max = df["coefficient"].clip(lower=0).sum()
    S_min = df["coefficient"].clip(upper=0).sum()
    
    # Load sample taxa
    present = pd.read_csv(taxa_path, sep="\t").drop_duplicates(subset="taxa_name")
    present = present[present["coefficient"] != 0]
    present_taxa = set(present["taxa_name"])
    
    # Count Balance
    n_pos = (present["coefficient"] > 0).sum()
    n_neg = (present["coefficient"] < 0).sum()
    count_balance = (n_pos - n_neg) / max(1, (n_pos + n_neg))
    
    # Bias Ratio
    pos_w = present.loc[present["coefficient"] > 0, "coefficient"].sum()
    neg_w = -present.loc[present["coefficient"] < 0, "coefficient"].sum()
    bias_ratio = pos_w / (pos_w + neg_w) if (pos_w + neg_w) else 0.5
    
    # Weighted Delta
    S_pos = df[df["coefficient"] > 0]
    S_neg = df[df["coefficient"] < 0]
    pos_weight = S_pos[S_pos["taxa_name"].isin(present_taxa)]["coefficient"].sum()
    neg_weight = abs(S_neg[S_neg["taxa_name"].isin(present_taxa)]["coefficient"].sum())
    denom = pos_weight + neg_weight
    weighted_delta = (pos_weight - neg_weight) / denom if denom else 0.0
    
    # Health Fraction (scaled position between model's theoretical min and max)
    # Range: 0 (worst - all opportunistic) to 1 (best - all supportive)
    # Thresholds: <0.53 (Depleted), 0.53-0.61 (Transitional), >0.61 (Resilient)
    S = present["coefficient"].sum()
    health_fraction = (S - S_min) / (S_max - S_min) if (S_max - S_min) else 0.5
    
    # GMWI2 Score
    gmwi2_score = float('nan')
    if gmwi_path.exists():
        try:
            gmwi2_score = float(gmwi_path.read_text().strip())
        except ValueError:
            pass
    
    # Z-score approximation
    z_approx = (gmwi2_score / 0.75) if not math.isnan(gmwi2_score) else float('nan')
    
    return {
        "gmwi2_score": gmwi2_score,
        "count_balance": count_balance,
        "bias_ratio": bias_ratio,
        "weighted_delta": weighted_delta,
        "health_fraction": health_fraction,
        "z_approx": z_approx,
    }


def identify_tiered_missing_taxa(sample: str) -> pd.DataFrame:
    """Identify missing supportive taxa stratified by importance tiers."""
    model = load(MODEL_PATH)
    all_taxa = pd.DataFrame({
        "taxa_name": model.feature_names_in_,
        "coefficient": model.coef_.flatten()
    })
    
    # Get supportive taxa only
    supportive = all_taxa[all_taxa["coefficient"] > 0].copy()
    
    # Load present taxa
    taxa_path = Path(GMWI_RESULTS_DIR) / f"{sample}_run_GMWI2_taxa.txt"
    present = pd.read_csv(taxa_path, sep="\t")
    present_taxa = set(present["taxa_name"])
    
    # Missing supportive taxa
    missing = supportive[~supportive["taxa_name"].isin(present_taxa)].copy()
    
    if missing.empty:
        return missing
    
    # Tier stratification by coefficient quantiles
    q75 = missing["coefficient"].quantile(0.75)
    q50 = missing["coefficient"].quantile(0.50)
    
    def assign_tier(coef):
        if coef >= q75:
            return "T1 (high)"
        elif coef >= q50:
            return "T2 (moderate)"
        else:
            return "T3 (lower)"
    
    missing["tier"] = missing["coefficient"].apply(assign_tier)
    missing = missing.sort_values("coefficient", ascending=False)
    
    return missing[["taxa_name", "coefficient", "tier"]]


# === FUNCTIONAL ANALYSIS ===
def load_metaphlan(path: str) -> pd.DataFrame:
    """
    Load MetaPhlAn single-sample output (relative abundances in %).

    Returns DataFrame with:
      clade_name, leaf_token, rank, rel_abund_pct, rel_abund_prop, clr
    
    After filtering out viruses/unknown, relative abundances are renormalized 
    within each taxonomic rank separately (phyla sum to 100%, classes sum to 100%, etc.).
    CLR is computed separately per taxonomic rank since each rank 
    represents a distinct compositional space.
    """
    df = pd.read_csv(
        path,
        sep="\t",
        comment="#",
        header=None,
        names=["clade_name", "ncbi_taxid", "relative_abundance", "additional_species"],
        dtype={"clade_name": str},
        engine="python",
    )
    df = df.dropna(subset=["clade_name"])
    df = df[~df["clade_name"].str.startswith(("UNKNOWN", "k__Viruses"))]
    
    df["leaf_token"] = df["clade_name"].apply(lambda x: x.split("|")[-1])
    df["rank"] = df["leaf_token"].str.extract(r"^([kpcfogs])__", expand=False)
    df["rel_abund_pct"] = pd.to_numeric(df["relative_abundance"], errors="coerce")
    
    # Renormalize relative abundances within each taxonomic rank separately
    # This ensures phyla sum to 100%, classes sum to 100%, etc.
    df["rel_abund_prop"] = np.nan
    for rank in df["rank"].dropna().unique():
        rank_mask = df["rank"] == rank
        rank_abundances = df.loc[rank_mask, "rel_abund_pct"]
        rank_sum = rank_abundances.sum()
        if rank_sum > 0:
            df.loc[rank_mask, "rel_abund_prop"] = rank_abundances / rank_sum
    
    df["rel_abund_pct"] = df["rel_abund_prop"] * 100.0
    
    # Apply CLR transformation separately for each taxonomic rank
    # This is mathematically correct since each rank is a separate compositional space
    df["clr"] = np.nan  # Initialize
    for rank in df["rank"].dropna().unique():
        rank_mask = df["rank"] == rank
        rank_data = df.loc[rank_mask, "rel_abund_prop"].to_numpy()
        if len(rank_data) > 0:
            df.loc[rank_mask, "clr"] = clr_transform(rank_data)
    
    return df.reset_index(drop=True)


def load_coeffs_gmwi2(path: str) -> pd.DataFrame:
    """Load GMWI2 coefficient table."""
    cf = pd.read_csv(path, sep="\t")
    cf.columns = [c.strip().lower() for c in cf.columns]
    cf["feature_token"] = cf["taxa_name"].apply(
        lambda x: x.split("|")[-1] if isinstance(x, str) else np.nan
    )
    cf["rank_key"] = cf["feature_token"].str.extract(r"^([kpcfogs])__", expand=False)
    cf["coef"] = pd.to_numeric(cf["coefficient"], errors="coerce")
    cf["direction"] = np.where(cf["coef"] > 0, "Supportive", "Opportunistic")
    cf = cf.dropna(subset=["coef"])
    return cf[["taxa_name", "feature_token", "rank_key", "coef", "direction"]].reset_index(drop=True)


def load_sample_gmwi2(path: str) -> pd.DataFrame:
    """Load taxa table emitted by GMWI2."""
    df = pd.read_csv(path, sep="\t")
    df.columns = [c.strip().lower() for c in df.columns]
    df["feature_token"] = df["taxa_name"].apply(
        lambda x: x.split("|")[-1] if isinstance(x, str) else np.nan
    )
    df["rank_key"] = df["feature_token"].str.extract(r"^([kpcfogs])__", expand=False)
    df["coef"] = pd.to_numeric(df.get("coefficient"), errors="coerce")
    return df.dropna(subset=["feature_token"]).reset_index(drop=True)[
        ["taxa_name", "feature_token", "rank_key", "coef"]
    ]


def map_features_to_sample(meta_df: pd.DataFrame, coeffs: pd.DataFrame) -> pd.DataFrame:
    """Align GMWI2 coefficient taxa to MetaPhlAn abundances."""
    meta = meta_df.copy()
    meta["clade_name"] = meta["clade_name"].astype(str)
    merged = coeffs.merge(
        meta[["clade_name", "rel_abund_prop", "clr"]],
        left_on="taxa_name",
        right_on="clade_name",
        how="inner",
    )
    merged = merged.rename(columns={"rel_abund_prop": "abundance_prop", "clade_name": "matched_clade"})
    merged["abundance_pct"] = merged["abundance_prop"] * 100
    merged["present"] = True
    merged["representation"] = np.where(merged["clr"] > 0, "Over", "Under")
    return merged[["taxa_name", "feature_token", "coef", "direction", "abundance_prop", "abundance_pct", "clr", "representation", "present"]]


def compute_signature_balance(mapped_df: pd.DataFrame) -> float:
    """Compute log ratio of supportive vs opportunistic abundances."""
    df = mapped_df.copy()
    total = df["abundance_prop"].sum()
    if total == 0:
        return float("nan")
    df["abundance_prop"] = df["abundance_prop"] / total
    
    min_nonzero = df.loc[df["abundance_prop"] > 0, "abundance_prop"].min()
    pseudo = 0.1 * min_nonzero if pd.notnull(min_nonzero) else 1e-6
    
    pos = df.loc[df["coef"] > 0, "abundance_prop"].add(pseudo)
    neg = df.loc[df["coef"] < 0, "abundance_prop"].add(pseudo)
    if pos.empty or neg.empty:
        return float("nan")
    return float(np.log(geom_mean(pos) / geom_mean(neg)))


def feature_token_from_stratified(val, feature_type):
    """
    Extract feature token from stratified pathway name.
    feature_type: 'pathway' or 'gene_fam' or 'comlevel_pathway'
    
    Returns:
    - "pathway" for UNMAPPED (goes to unmapped_unintegrated)
    - "comlevel_pathway" for UNINTEGRATED and unstratified pathways (goes to pathways_comlevel)
    - taxon name (e.g., "s__Species", "g__Genus") for stratified pathways
    - "unclassified" for unclassified taxa
    """
    if pd.isna(val):
        return "unclassified"
    s = str(val).strip()
    if not s:
        return "unclassified"
    
    # Standalone special case: UNMAPPED only
    if s == "UNMAPPED":
        return "pathway"
    
    # Nothing stratified (includes UNINTEGRATED)
    # UNINTEGRATED has no pipe, so it will go to comlevel_pathway
    if "|" not in s:
        return "comlevel_pathway"
    
    # Handle trailing pipe (no taxon after it)
    if s.endswith("|"):
        return "comlevel_pathway"
    
    # Look at what's after the first pipe
    after = s.split("|", 1)[1]
    if after == "unclassified":
        return "unclassified"
    
    # Otherwise find the deepest rank token (s__ preferred, else g__)
    parts = re.split(r"[|.]", s)
    for part in reversed(parts):
        part = part.strip()
        if part.startswith("s__") or part.startswith("g__"):
            return part
    
    # If nothing matched, call it unclassified
    return "unclassified"


def analyze_functional_pathways(sample: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Analyze HUMAnN pathway abundance (HUMANn v4 compatible).
    
    HUMANn v4 removes pathway coverage entirely. Classification is now based
    on abundance only, reflecting the principle that abundance encodes confidence.
    
    Returns abundance-classified pathways after renormalization (excluding UNMAPPED/UNINTEGRATED).
    """
    # FUNCTIONAL_DIR already includes the sample ID, so use it directly
    pathway_dir = FUNCTIONAL_DIR
    
    # Updated filename pattern: {sample}_4_pathabundance_relab.tsv
    path_ab = pd.read_csv(
        os.path.join(pathway_dir, f"{sample}_4_pathabundance_relab.tsv"),
        sep="\t",
        names=["pathway", "relab"],
        skiprows=1,
    )
    
    # Check if coverage file exists for backward compatibility with HUMANn v3 data
    coverage_file = os.path.join(pathway_dir, f"{sample}_pathcoverage.tsv")
    has_coverage = os.path.exists(coverage_file)
    
    if has_coverage:
        # HUMANn v3: Use coverage-based classification
        logging.info(f"Coverage file found - using HUMANn v3 logic (coverage-based)")
        path_cov = pd.read_csv(
            coverage_file,
            sep="\t",
            names=["pathway", "coverage"],
            skiprows=1,
        )
        pathways = path_cov.merge(path_ab, on="pathway", how="left")
    else:
        # HUMANn v4: Use abundance-based classification
        logging.info(f"No coverage file found - using HUMANn v4 logic (abundance-based)")
        pathways = path_ab.copy()
        # No coverage column for v4 data
    
    # Extract metacyc_id
    pathways["metacyc_id"] = pathways["pathway"].apply(
        lambda x: re.split(":", x)[0].strip() if ":" in str(x) else None
    )
    
    # Extract feature token
    pathways["feature_token"] = pathways["pathway"].apply(
        lambda x: feature_token_from_stratified(x, "pathway")
    )
    
    # Separate categories (unmapped/unintegrated are now "pathway")
    unmapped_unintegrated = pathways[pathways.feature_token == "pathway"].copy()
    pathways_comlevel = pathways[pathways.feature_token == "comlevel_pathway"].copy()
    
    # HUMANn v4: Renormalize community-level after excluding UNMAPPED/UNINTEGRATED
    # This ensures pathways sum to 1.0 for proper quantile calculation
    pathways_comlevel['relab'] = pathways_comlevel['relab'] / pathways_comlevel['relab'].sum()
    
    # Stratified pathways are everything that's not comlevel and not unmapped/unintegrated
    pathways_stratified = pathways[
        (~pathways.feature_token.isin(pathways_comlevel.feature_token)) &
        (~pathways.feature_token.isin(unmapped_unintegrated.feature_token))
    ].copy()
    
    # Renormalize stratified pathways
    if not pathways_stratified.empty:
        pathways_stratified['relab'] = pathways_stratified['relab'] / pathways_stratified['relab'].sum()
    
    # Classify pathways by abundance (HUMANn v3 vs v4)
    comlevel = pathways_comlevel.copy()
    
    if has_coverage:
        # HUMANn v3: Coverage-based classification
        absent = comlevel[comlevel["coverage"] == 0].copy()
        substantially_present = comlevel[comlevel["coverage"] >= 0.6].copy()
        fully_covered = comlevel[comlevel["coverage"] == 1].copy()
    else:
        # HUMANn v4: Abundance-based classification using quantiles
        # Calculate quantiles on renormalized abundances
        q50 = comlevel['relab'].quantile(0.50)  # Median
        q75 = comlevel['relab'].quantile(0.75)  # 75th percentile
        
        logging.info(f"HUMANn v4 quantile thresholds: 50th={q50:.6f}, 75th={q75:.6f}")
        
        # Classify by quantiles (all detected pathways have abundance > 0)
        high_abundance = comlevel[comlevel['relab'] > q75].copy()
        moderate_abundance = comlevel[(comlevel['relab'] > q50) & (comlevel['relab'] <= q75)].copy()
        marginal_abundance = comlevel[comlevel['relab'] <= q50].copy()
        
        # For compatibility with downstream code, map to old variable names
        # but note these now mean different things (abundance-based, not coverage-based)
        substantially_present = high_abundance
        fully_covered = high_abundance  # Same as substantially_present for v4
        absent = pd.DataFrame()  # Empty - all detected pathways have abundance > 0
    
    return absent, substantially_present, fully_covered, pathways_comlevel, pathways_stratified


def analyze_core_pathways(sample: str, substantially_present: pd.DataFrame, all_pathways: pd.DataFrame, absent_pathways: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """
    Analyze core gut health pathways using keyword matching.
    
    Compatible with both HUMANn v3 (coverage-based) and v4 (abundance-based) data.
    """
    # Load core pathways keywords
    if not CORE_PATHWAYS_PATH.exists():
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
    
    keywords_df = pd.read_csv(CORE_PATHWAYS_PATH, sep="\t")
    
    # Ensure we only have community-level pathways (no taxa stratification)
    substantially_present = substantially_present[~substantially_present["pathway"].str.contains(r"\|", regex=True)].copy()
    all_pathways = all_pathways[~all_pathways["pathway"].str.contains(r"\|", regex=True)].copy()
    
    # Handle empty absent_pathways (HUMANn v4 case)
    if not absent_pathways.empty and "pathway" in absent_pathways.columns:
        absent_pathways = absent_pathways[~absent_pathways["pathway"].str.contains(r"\|", regex=True)].copy()
    else:
        absent_pathways = pd.DataFrame()  # Keep empty
    
    # Check if we're using coverage (v3) or abundance (v4) based classification
    # Use substantially_present to check since all_pathways might have artificially added coverage
    has_coverage = "coverage" in substantially_present.columns
    
    present_core_list = []
    fractured_core_list = []
    absent_core_list = []
    
    # Process each core pathway category
    for _, row in keywords_df.iterrows():
        category = row["Core Pathways"]
        keywords_str = row["Keywords"]
        
        # Parse keywords (comma-separated, may have quotes)
        keywords = [kw.strip().strip('"').strip("'") for kw in keywords_str.split(",")]
        keywords = [kw for kw in keywords if kw]  # Remove empty strings
        
        # Find matching pathways for this category
        category_present = set()
        category_fractured = set()
        category_absent = set()
        
        # Check high abundance pathways (v3: >=60% coverage, v4: >75th percentile)
        for _, pathway_row in substantially_present.iterrows():
            pathway_name = str(pathway_row["pathway"]).lower()
            for keyword in keywords:
                if keyword.lower() in pathway_name:
                    category_present.add(pathway_row["pathway"])
                    break
        
        if has_coverage:
            # HUMANn v3: Check fractured pathways (0% < coverage < 60%)
            fractured_pathways = all_pathways[(all_pathways["coverage"] > 0) & (all_pathways["coverage"] < 0.6)]
        else:
            # HUMANn v4: Check moderate/marginal abundance pathways (detected but not high)
            # These are in all_pathways but not in substantially_present
            substantially_present_names = set(substantially_present["pathway"])
            fractured_pathways = all_pathways[~all_pathways["pathway"].isin(substantially_present_names)]
        
        for _, pathway_row in fractured_pathways.iterrows():
            pathway_name = str(pathway_row["pathway"]).lower()
            for keyword in keywords:
                if keyword.lower() in pathway_name:
                    category_fractured.add(pathway_row["pathway"])
                    break
        
        # Check absent pathways (v3: 0% coverage, v4: not in file)
        for _, pathway_row in absent_pathways.iterrows():
            pathway_name = str(pathway_row["pathway"]).lower()
            for keyword in keywords:
                if keyword.lower() in pathway_name:
                    category_absent.add(pathway_row["pathway"])
                    break
        
        # Add to results with full details
        for pathway in category_present:
            pathway_data = substantially_present[substantially_present["pathway"] == pathway].iloc[0]
            result_dict = {
                "category": category,
                "pathway": pathway,
                "relab": pathway_data["relab"]
            }
            if has_coverage:
                result_dict["coverage"] = pathway_data["coverage"]
            present_core_list.append(result_dict)
        
        for pathway in category_fractured:
            pathway_data = fractured_pathways[fractured_pathways["pathway"] == pathway].iloc[0]
            result_dict = {
                "category": category,
                "pathway": pathway,
                "relab": pathway_data["relab"]
            }
            if has_coverage:
                result_dict["coverage"] = pathway_data["coverage"]
            fractured_core_list.append(result_dict)
        
        for pathway in category_absent:
            pathway_data = absent_pathways[absent_pathways["pathway"] == pathway].iloc[0]
            result_dict = {
                "category": category,
                "pathway": pathway,
                "relab": pathway_data.get("relab", 0.0)
            }
            if has_coverage:
                result_dict["coverage"] = pathway_data.get("coverage", 0.0)
            absent_core_list.append(result_dict)
    
    # Convert to DataFrames
    present_core = pd.DataFrame(present_core_list) if present_core_list else pd.DataFrame()
    fractured_core = pd.DataFrame(fractured_core_list) if fractured_core_list else pd.DataFrame()
    absent_core = pd.DataFrame(absent_core_list) if absent_core_list else pd.DataFrame()
    
    # Summarize by category
    category_summary = {}
    for category in keywords_df["Core Pathways"]:
        present_count = len(present_core[present_core["category"] == category]) if not present_core.empty else 0
        fractured_count = len(fractured_core[fractured_core["category"] == category]) if not fractured_core.empty else 0
        absent_count = len(absent_core[absent_core["category"] == category]) if not absent_core.empty else 0
        total_count = present_count + fractured_count + absent_count
        
        category_summary[category] = {
            "total": total_count,
            "present": present_count,
            "fractured": fractured_count,
            "absent": absent_count,
            "present_pct": (present_count / total_count * 100) if total_count > 0 else 0
        }
    
    return present_core, fractured_core, absent_core, category_summary


# === PLOTTING FUNCTIONS ===
def plot_core_pathways_heatmap(core_category_summary: dict, sample: str) -> str:
    """Generate heatmap showing core pathway status across categories."""
    if not core_category_summary:
        return ""
    
    # Prepare data for heatmap
    categories = []
    present_counts = []
    fractured_counts = []
    absent_counts = []
    
    for category, stats in core_category_summary.items():
        categories.append(category)
        present_counts.append(stats['present'])
        fractured_counts.append(stats['fractured'])
        absent_counts.append(stats['absent'])
    
    # Create DataFrame for heatmap
    heatmap_data = pd.DataFrame({
        'Present': present_counts,
        'Fractured': fractured_counts,
        'Absent': absent_counts
    }, index=categories)
    
    # Create figure
    fig, ax = plt.subplots(figsize=(8, max(6, len(categories) * 0.4)))
    
    # Create heatmap
    sns.heatmap(
        heatmap_data,
        annot=True,
        fmt='d',
        cmap=['#2ecc71', '#f39c12', '#e74c3c'],  # Green, Yellow, Red
        cbar_kws={'label': 'Pathway Count'},
        linewidths=0.5,
        linecolor='white',
        ax=ax
    )
    
    ax.set_title(f'Core Pathway Status: {sample}', fontsize=14, fontweight='bold', pad=20)
    ax.set_xlabel('Pathway Status', fontsize=12)
    ax.set_ylabel('Functional Category', fontsize=12)
    
    plt.tight_layout()
    
    # Save plot
    os.makedirs(INTEGRATED_PLOTS_DIR, exist_ok=True)
    plot_path = os.path.join(INTEGRATED_PLOTS_DIR, f"{sample}_core_pathways_heatmap.png")
    plt.savefig(plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return plot_path


def plot_missing_taxa_by_tier(tiered_missing: pd.DataFrame, sample: str) -> str:
    """Generate bar chart showing missing supportive taxa by importance tier."""
    if tiered_missing.empty:
        return ""
    
    # Count taxa per tier
    tier_counts = tiered_missing["tier"].value_counts().sort_index()
    
    # Create figure
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Define colors for tiers
    tier_colors = {
        'T1 (high)': '#e74c3c',      # Red - highest priority
        'T2 (moderate)': '#f39c12',   # Orange - moderate priority
        'T3 (lower)': '#95a5a6'       # Gray - lower priority
    }
    
    tiers = ['T1 (high)', 'T2 (moderate)', 'T3 (lower)']
    counts = [tier_counts.get(tier, 0) for tier in tiers]
    colors = [tier_colors[tier] for tier in tiers]
    
    bars = ax.bar(range(len(tiers)), counts, color=colors, edgecolor='black', linewidth=1.5)
    
    # Add count labels on bars
    for bar, count in zip(bars, counts):
        height = bar.get_height()
        if height > 0:
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{int(count)}',
                   ha='center', va='bottom', fontsize=12, fontweight='bold')
    
    ax.set_xticks(range(len(tiers)))
    ax.set_xticklabels(tiers, fontsize=11)
    ax.set_ylabel('Number of Missing Taxa', fontsize=12)
    ax.set_title(f'Missing Supportive Taxa by Importance Tier: {sample}', 
                fontsize=14, fontweight='bold', pad=20)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    ax.set_axisbelow(True)
    
    # Add total count annotation
    total_missing = sum(counts)
    ax.text(0.95, 0.95, f'Total Missing: {total_missing}',
           transform=ax.transAxes,
           ha='right', va='top',
           fontsize=11,
           bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout()
    
    # Save plot
    os.makedirs(INTEGRATED_PLOTS_DIR, exist_ok=True)
    plot_path = os.path.join(INTEGRATED_PLOTS_DIR, f"{sample}_missing_taxa_by_tier.png")
    plt.savefig(plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    return plot_path


def plot_guild_analysis(guild_summary: pd.DataFrame, sample: str) -> plt.Figure:
    """Visualize guild-level metrics in a comprehensive dashboard."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Filter out absent guilds for plotting
    present_guilds = guild_summary[guild_summary["n_taxa"] > 0].copy()
    
    # Plot 1: Guild Abundance
    ax1 = axes[0, 0]
    colors_map = {'Enriched': '#2ecc71', 'Balanced': '#f39c12', 'Depleted': '#e74c3c'}
    guild_colors = [colors_map.get(s, '#95a5a6') for s in present_guilds["status"]]
    
    ax1.barh(present_guilds["guild"], present_guilds["total_abundance"], color=guild_colors)
    ax1.set_xlabel("Total Abundance (proportion)")
    ax1.set_title("Guild Total Abundance")
    ax1.grid(axis='x', alpha=0.3)
    
    # Plot 2: Guild Redundancy (Pielou Evenness)
    ax2 = axes[0, 1]
    ax2.barh(present_guilds["guild"], present_guilds["pielou_evenness"], color='steelblue')
    ax2.set_xlabel("Pielou's Evenness (0-1)")
    ax2.set_title("Guild Redundancy/Robustness")
    ax2.axvline(0.7, color='green', linestyle='--', label='High redundancy', alpha=0.5)
    ax2.axvline(0.3, color='orange', linestyle='--', label='Low redundancy', alpha=0.5)
    ax2.legend()
    ax2.grid(axis='x', alpha=0.3)
    
    # Plot 3: Guild Enrichment (mean CLR)
    ax3 = axes[1, 0]
    clr_colors = ['#2ecc71' if x > 0 else '#e74c3c' for x in present_guilds["mean_clr"]]
    ax3.barh(present_guilds["guild"], present_guilds["mean_clr"], color=clr_colors)
    ax3.axvline(0, color='black', linestyle='-', linewidth=1)
    ax3.set_xlabel("Mean CLR (enrichment)")
    ax3.set_title("Guild Enrichment Status")
    ax3.grid(axis='x', alpha=0.3)
    
    # Plot 4: Guild Dominance Pattern  
    ax4 = axes[1, 1]
    ax4.scatter(present_guilds["n_taxa"], present_guilds["pielou_evenness"], 
                s=present_guilds["total_abundance"]*1000, alpha=0.6, c=guild_colors)
    ax4.set_xlabel("Number of Taxa in Guild")
    ax4.set_ylabel("Pielou Evenness")
    ax4.set_title("Guild Diversity vs Evenness")
    ax4.axhline(0.7, color='green', linestyle='--', alpha=0.3, label='High evenness')
    ax4.legend()
    ax4.grid(alpha=0.3)
    
    plt.suptitle(f'Functional Guild Analysis: {sample}', fontsize=14, fontweight='bold', y=0.995)
    plt.tight_layout()
    
    return fig


def plot_rank_diversity(diversity_df: pd.DataFrame, sample: str) -> plt.Figure:
    """Visualize diversity metrics across taxonomic ranks."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    
    ranks = diversity_df["rank_name"]
    
    # Plot 1: Shannon Diversity
    ax1 = axes[0, 0]
    ax1.plot(ranks, diversity_df["shannon"], marker='o', linewidth=2, markersize=8, color='steelblue')
    ax1.set_ylabel("Shannon Index (H')")
    ax1.set_title("Shannon Diversity Across Ranks")
    ax1.grid(alpha=0.3)
    ax1.tick_params(axis='x', rotation=45)
    
    # Plot 2: Simpson's Index
    ax2 = axes[0, 1]
    ax2.plot(ranks, diversity_df["simpson"], marker='s', linewidth=2, markersize=8, color='seagreen')
    ax2.set_ylabel("Simpson's Index")
    ax2.set_title("Simpson's Diversity (1 - Dominance)")
    ax2.axhline(0.8, color='green', linestyle='--', alpha=0.3, label='High diversity')
    ax2.legend()
    ax2.grid(alpha=0.3)
    ax2.tick_params(axis='x', rotation=45)
    
    # Plot 3: Pielou Evenness
    ax3 = axes[1, 0]
    colors = ['#2ecc71' if p > 0.7 else '#f39c12' if p > 0.4 else '#e74c3c' 
              for p in diversity_df["pielou"]]
    ax3.bar(ranks, diversity_df["pielou"], color=colors)
    ax3.set_ylabel("Pielou's Evenness (J)")
    ax3.set_title("Community Evenness by Rank")
    ax3.axhline(0.7, color='green', linestyle='--', alpha=0.3, label='Even (robust)')
    ax3.axhline(0.4, color='orange', linestyle='--', alpha=0.3, label='Moderate')
    ax3.legend()
    ax3.grid(axis='y', alpha=0.3)
    ax3.tick_params(axis='x', rotation=45)
    
    # Plot 4: Richness
    ax4 = axes[1, 1]
    ax4.bar(ranks, diversity_df["richness"], color='mediumpurple', alpha=0.7)
    ax4.set_ylabel("Richness (# Taxa)")
    ax4.set_title("Taxa Richness by Rank")
    ax4.grid(axis='y', alpha=0.3)
    ax4.tick_params(axis='x', rotation=45)
    
    plt.suptitle(f'Rank-Specific Diversity Analysis: {sample}', 
                fontsize=14, fontweight='bold', y=0.995)
    plt.tight_layout()
    
    return fig


def save_all_plots_to_pdf(sample: str, guild_summary: pd.DataFrame, diversity_df: pd.DataFrame,
                          core_category_summary: dict, tiered_missing: pd.DataFrame) -> str:
    """Save all analysis plots to a single PDF file."""
    pdf_path = os.path.join(INTEGRATED_PLOTS_DIR, f"{sample}_integrated_analysis.pdf")
    
    with PdfPages(pdf_path) as pdf:
        # Plot 1: Guild analysis (4 subplots)
        guild_fig = plot_guild_analysis(guild_summary, sample)
        pdf.savefig(guild_fig, bbox_inches='tight', dpi=300)
        plt.close(guild_fig)
        
        # Plot 2: Diversity analysis (4 subplots)
        diversity_fig = plot_rank_diversity(diversity_df, sample)
        pdf.savefig(diversity_fig, bbox_inches='tight', dpi=300)
        plt.close(diversity_fig)
        
        # Plot 3: Core pathways heatmap
        if core_category_summary:
            fig3, ax3 = plt.subplots(figsize=(8, max(6, len(core_category_summary) * 0.4)))
            
            categories = list(core_category_summary.keys())
            present_counts = [core_category_summary[c]['present'] for c in categories]
            fractured_counts = [core_category_summary[c]['fractured'] for c in categories]
            absent_counts = [core_category_summary[c]['absent'] for c in categories]
            
            heatmap_data = pd.DataFrame({
                'Present': present_counts,
                'Fractured': fractured_counts,
                'Absent': absent_counts
            }, index=categories)
            
            sns.heatmap(heatmap_data, annot=True, fmt='d',
                       cmap=['#2ecc71', '#f39c12', '#e74c3c'],
                       cbar_kws={'label': 'Pathway Count'},
                       linewidths=0.5, linecolor='white', ax=ax3)
            
            ax3.set_title(f'Core Pathway Status: {sample}', fontsize=14, fontweight='bold', pad=20)
            ax3.set_xlabel('Pathway Status', fontsize=12)
            ax3.set_ylabel('Functional Category', fontsize=12)
            
            plt.tight_layout()
            pdf.savefig(fig3, bbox_inches='tight', dpi=300)
            plt.close(fig3)
        
        # Plot 4: Missing taxa by tier
        if not tiered_missing.empty:
            tier_counts = tiered_missing["tier"].value_counts().sort_index()
            
            fig4, ax4 = plt.subplots(figsize=(10, 6))
            
            tier_colors = {
                'T1 (high)': '#e74c3c',
                'T2 (moderate)': '#f39c12',
                'T3 (lower)': '#95a5a6'
            }
            
            tiers = ['T1 (high)', 'T2 (moderate)', 'T3 (lower)']
            counts = [tier_counts.get(tier, 0) for tier in tiers]
            colors = [tier_colors[tier] for tier in tiers]
            
            bars = ax4.bar(range(len(tiers)), counts, color=colors, edgecolor='black', linewidth=1.5)
            
            for bar, count in zip(bars, counts):
                height = bar.get_height()
                if height > 0:
                    ax4.text(bar.get_x() + bar.get_width()/2., height,
                           f'{int(count)}', ha='center', va='bottom', 
                           fontsize=12, fontweight='bold')
            
            ax4.set_xticks(range(len(tiers)))
            ax4.set_xticklabels(tiers, fontsize=11)
            ax4.set_ylabel('Number of Missing Taxa', fontsize=12)
            ax4.set_title(f'Missing Supportive Taxa by Importance Tier: {sample}', 
                        fontsize=14, fontweight='bold', pad=20)
            ax4.grid(axis='y', alpha=0.3, linestyle='--')
            ax4.set_axisbelow(True)
            
            total_missing = sum(counts)
            ax4.text(0.95, 0.95, f'Total Missing: {total_missing}',
                   transform=ax4.transAxes, ha='right', va='top',
                   fontsize=11, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
            
            plt.tight_layout()
            pdf.savefig(fig4, bbox_inches='tight', dpi=300)
            plt.close(fig4)
    
    return pdf_path


def verify_and_correct_arithmetic(ratio_name: str, operand1: float, operator: str, operand2: float, computed_result: float) -> Tuple[float, bool]:
    """
    Verify arithmetic correctness and auto-correct if needed.
    
    Returns: (corrected_value, was_corrected)
    """
    if operator == '-':
        expected = operand1 - operand2
    elif operator == '+':
        expected = operand1 + operand2
    else:
        logging.warning(f"Unknown operator in {ratio_name}: {operator}")
        return computed_result, False
    
    tolerance = 0.001
    if abs(expected - computed_result) > tolerance:
        logging.warning(f"⚠️  ARITHMETIC AUTO-CORRECTED in {ratio_name}:")
        logging.warning(f"   Computed: {operand1} {operator} ({operand2}) = {computed_result}")
        logging.warning(f"   Corrected to: {expected:.4f}")
        return expected, True
    
    return computed_result, False


def calculate_clr_diagnostic_ratios(guild_summary: pd.DataFrame, guild_clr_data: dict) -> dict:
    """
    Calculate CLR-based diagnostic ratios for metabolic state assessment.
    
    Per Guild_analysis_v3.md Section 2.2:
    - CUR (Carbohydrate Utilization Ratio): Fiber vs Protein dominance
    - FCR (Fermentation Completion Ratio): Intermediate conversion efficiency
    - MDR (Mucus Dependency Ratio): Host vs diet substrate dependence
    - PPR (Putrefaction Pressure Ratio): Putrefaction vs SCFA balance
    
    v3.1 CHANGE (January 2026): Use guild-level CLRs from guild_clr_data, not species-averaged.
    This provides:
    - Consistent CLR usage across all metrics
    - Transparent, verifiable calculations  
    - Structural info captured by evenness instead
    - Standard compositional data analysis
    
    v1.4 FIX: Guild absence determined by raw abundance, not CLR=0
    CLR ranges:
    - CLR = 0: Guild at geometric mean (balanced, not absent)
    - CLR > +2: Highly enriched (>7× geometric mean)
    - CLR < -2: Highly depleted (<0.14× geometric mean, but still present)
    
    Returns dict with ratio values and interpretations.
    """
    # Extract guild data including abundance (for presence check)
    def get_guild_data(guild_name):
        row = guild_summary[guild_summary["guild"] == guild_name]
        if row.empty:
            return {"n_taxa": 0, "abundance": 0.0}
        return {
            "n_taxa": int(row["n_taxa"].iloc[0]),
            "abundance": float(row["total_abundance"].iloc[0])
        }
    
    # Extract guild-level CLRs from guild_clr_data (v3.1)
    def get_guild_clr(guild_name):
        return guild_clr_data['clr_values'].get(guild_name, np.nan)
    
    # Get data for all guilds (for presence check)
    fiber = get_guild_data("Fiber Degraders")
    bifido = get_guild_data("HMO/Oligosaccharide-Utilising Bifidobacteria")
    proteolytic = get_guild_data("Proteolytic Dysbiosis Guild")
    butyrate = get_guild_data("Butyrate Producers")
    cross = get_guild_data("Cross-Feeders")
    mucin = get_guild_data("Mucin Degraders")
    
    # v3.2 CHANGE (March 2026): All guilds are now "present" for ratio calculation
    # purposes. calculate_guild_clr_with_unassigned() now assigns real CLR values
    # to all guilds (using pseudocount for truly absent ones), so diagnostic ratios
    # always compute. The _present flags are kept for backward compatibility but
    # set to True for any guild with detected abundance OR pseudocount CLR.
    fiber_present = True  # CLR always available now
    bifido_present = bifido["abundance"] > 0 or not pd.isna(get_guild_clr("HMO/Oligosaccharide-Utilising Bifidobacteria"))
    proteolytic_present = True  # CLR always available now
    butyrate_present = True  # CLR always available now
    cross_present = True  # CLR always available now
    mucin_present = True  # CLR always available now
    
    # Get guild-level CLRs (v3.1 - from guild_clr_data, not mean_clr)
    fiber_clr = get_guild_clr("Fiber Degraders")
    bifido_clr = get_guild_clr("HMO/Oligosaccharide-Utilising Bifidobacteria")
    proteolytic_clr = get_guild_clr("Proteolytic Dysbiosis Guild")
    butyrate_clr = get_guild_clr("Butyrate Producers")
    cross_clr = get_guild_clr("Cross-Feeders")
    mucin_clr = get_guild_clr("Mucin Degraders")
    
    # CUR: CLR(Fiber + Bifido) - CLR(Proteolytic)
    # ALWAYS average, even when one guild absent (treat absent as 0)
    # Per CLR_Ratio_Averaging_Correction_Required.md: Mandatory averaging rule
    if bifido_present and fiber_present:
        carb_clr = (fiber_clr + bifido_clr) / 2.0
    elif fiber_present and not bifido_present:
        # Bifido absent: treat as 0, still average
        carb_clr = (fiber_clr + 0.0) / 2.0
    elif bifido_present and not fiber_present:
        # Fiber absent: treat as 0, still average
        carb_clr = (bifido_clr + 0.0) / 2.0
    else:
        carb_clr = np.nan  # Both absent
    
    # Calculate CUR and verify arithmetic
    if proteolytic_present and not pd.isna(carb_clr):
        cur_computed = carb_clr - proteolytic_clr
        cur, was_corrected = verify_and_correct_arithmetic("CUR", carb_clr, '-', proteolytic_clr, cur_computed)
    else:
        cur = np.nan
    
    # FCR: CLR(Butyrate + Cross) - CLR(Bifido)
    if butyrate_present and cross_present:
        terminal_clr = (butyrate_clr + cross_clr) / 2.0
    elif butyrate_present and not cross_present:
        terminal_clr = butyrate_clr
    elif cross_present and not butyrate_present:
        terminal_clr = cross_clr
    else:
        terminal_clr = np.nan
    
    # FCR calculation - interpretation differs when Bifido absent
    if not pd.isna(terminal_clr) and not pd.isna(bifido_clr):
        fcr_computed = terminal_clr - bifido_clr
        fcr, was_corrected = verify_and_correct_arithmetic("FCR", terminal_clr, '-', bifido_clr, fcr_computed)
    elif not pd.isna(terminal_clr) and not bifido_present:
        # Bifido absent: FCR measures terminal processors vs theoretical lactate producers
        # This is a MODEL-BASED INFERENCE, not direct measurement
        fcr = terminal_clr  # Compare to 0 (geometric mean) - no verification needed
    else:
        fcr = np.nan
    
    # MDR: CLR(Mucin) - CLR(Fiber) [v3.1: using guild-level CLR]
    if mucin_present and fiber_present:
        mdr_computed = mucin_clr - fiber_clr
        mdr, was_corrected = verify_and_correct_arithmetic("MDR", mucin_clr, '-', fiber_clr, mdr_computed)
    else:
        mdr = np.nan
    
    # PPR: CLR(Proteolytic) - CLR(Butyrate)
    if proteolytic_present and butyrate_present:
        ppr_computed = proteolytic_clr - butyrate_clr
        ppr, was_corrected = verify_and_correct_arithmetic("PPR", proteolytic_clr, '-', butyrate_clr, ppr_computed)
    else:
        ppr = np.nan
    
    # Interpret ratios
    def interpret_cur(val):
        if pd.isna(val):
            return "Undefined (key guilds absent)"
        elif val > 0.5:
            return "Carbohydrate-driven"
        elif val < -0.5:
            return "Protein-driven"
        else:
            return "Balanced"
    
    def interpret_fcr(val, bifido_status):
        if pd.isna(val):
            return "Undefined (terminal processors absent)"
        
        if not bifido_status:
            # Bifido absent: different interpretation
            if val > 0.3:
                return "Model inference: Direct pathway dominance (lactate-independent fermentation)"
            elif val < -0.3:
                return "Model inference: Low terminal processing capacity"
            else:
                return "Model inference: Moderate direct pathway capacity"
        else:
            # Bifido present: standard interpretation
            if val > 0.3:
                return "Efficient intermediate conversion"
            elif val < -0.3:
                return "Intermediate accumulation risk"
            else:
                return "Moderate efficiency"
    
    def interpret_mdr(val):
        # Recalibrated 2026-03-06: diet_fed threshold moved from -0.5 to -1.0
        # matching compute_metabolic_dials() in overview_fields.py
        if pd.isna(val):
            return "Undefined (key guilds absent)"
        elif val > 0.2:
            return "Host-substrate dependent"
        elif val < -1.0:
            return "Diet-fed"
        else:
            return "Partial mucus reliance"
    
    def interpret_ppr(val):
        if pd.isna(val):
            return "Undefined (key guilds absent)"
        elif val > 0.5:
            return "Putrefaction dominance"
        elif val < -0.5:
            return "SCFA dominance"
        else:
            return "Balanced"
    
    return {
        "CUR": cur,
        "CUR_interpretation": interpret_cur(cur),
        "FCR": fcr,
        "FCR_interpretation": interpret_fcr(fcr, bifido_present),
        "bifido_present": bifido_present,
        "bifido_abundance": bifido["abundance"],
        "MDR": mdr,
        "MDR_interpretation": interpret_mdr(mdr),
        "PPR": ppr,
        "PPR_interpretation": interpret_ppr(ppr)
    }


def assess_absolute_capacity(guild_name: str, abundance: float) -> dict:
    """
    Compare guild abundance to healthy reference ranges (Axis 1).
    
    Per Guild_analysis_v3.md Section 2.3.2
    
    Returns dict with status, distance from optimal, and interpretation.
    """
    if guild_name not in HEALTHY_REFERENCE_RANGES:
        return {
            "status": "no_reference",
            "interpretation": "No reference data available"
        }
    
    ref = HEALTHY_REFERENCE_RANGES[guild_name]
    
    # Handle absence
    if abundance < 0.001:  # <0.1%
        return {
            "status": "absent",
            "abundance": abundance,
            "expected_min": ref["min"],
            "expected_max": ref["max"],
            "distance_from_optimal": abundance - ref["optimal"],
            "interpretation": f"Biologically absent (expected: {ref['min']*100:.1f}-{ref['max']*100:.1f}%)"
        }
    
    # Within range
    if ref["min"] <= abundance <= ref["max"]:
        return {
            "status": "within_range",
            "abundance": abundance,
            "expected_min": ref["min"],
            "expected_max": ref["max"],
            "optimal": ref["optimal"],
            "distance_from_optimal": abundance - ref["optimal"],
            "interpretation": f"Within healthy range ({abundance*100:.2f}% vs {ref['min']*100:.0f}-{ref['max']*100:.0f}%)"
        }
    
    # Below range
    elif abundance < ref["min"]:
        deficit = ref["min"] - abundance
        return {
            "status": "below_range",
            "abundance": abundance,
            "expected_min": ref["min"],
            "expected_max": ref["max"],
            "deficit": deficit,
            "distance_from_optimal": abundance - ref["optimal"],
            "interpretation": f"Below healthy range ({abundance*100:.2f}% vs minimum {ref['min']*100:.0f}%)"
        }
    
    # Above range
    else:  # abundance > ref["max"]
        excess = abundance - ref["max"]
        return {
            "status": "above_range",
            "abundance": abundance,
            "expected_min": ref["min"],
            "expected_max": ref["max"],
            "excess": excess,
            "distance_from_optimal": abundance - ref["optimal"],
            "interpretation": f"Above healthy range ({abundance*100:.2f}% vs maximum {ref['max']*100:.0f}%)"
        }


def calculate_guild_clr_with_unassigned(guild_summary: pd.DataFrame) -> dict:
    """
    Calculate guild CLR including "Unassigned" category for complete sample coverage.
    
    Per Guild_analysis_v3.md Section 2.3.3
    
    Returns dict with:
    - guild_abundances: Dict of all abundances including Unassigned
    - clr_values: CLR for each guild vs whole sample
    - geometric_mean: Reference point for CLR
    - total_assigned: Proportion of sample in defined guilds
    """
    # Extract guild abundances
    guild_abundances = {}
    total_assigned = 0.0
    
    for _, row in guild_summary.iterrows():
        guild_name = row["guild"]
        abundance = float(row["total_abundance"])
        guild_abundances[guild_name] = abundance
        if row["n_taxa"] > 0:  # Only count if guild present
            total_assigned += abundance
    
    # Add Unassigned category (all bacteria NOT in FFA table)
    unassigned = 1.0 - total_assigned
    guild_abundances["Unassigned"] = unassigned
    
    # v3.2 CHANGE (March 2026): Remove artificial 1% threshold for CLR calculation.
    # CLR is mathematically valid for any positive abundance. The previous threshold
    # caused 3/4 diagnostic ratios to report nan for samples with low-abundance
    # contextual guilds (e.g., Proteolytic at 0.67%, Mucin at 0.72%).
    #
    # Strategy:
    # - All categories with abundance > 0 are included in GM and get real CLR values
    # - Truly absent guilds (0.000%) get pseudocount of 0.001% (0.00001 as proportion)
    # - This ensures all diagnostic ratios (CUR, FCR, MDR, PPR) always compute
    PSEUDOCOUNT = 0.00001  # 0.001% as proportion — for truly absent guilds

    # Include all categories with any detected abundance in GM calculation
    all_abundances = []
    for guild, abund in guild_abundances.items():
        effective_abund = abund if abund > 0 else PSEUDOCOUNT
        all_abundances.append(effective_abund)

    # Calculate geometric mean of ALL categories (including Unassigned)
    if all_abundances:
        geometric_mean = np.exp(np.mean(np.log(all_abundances)))
    else:
        geometric_mean = np.nan

    # Calculate CLR for each guild — no threshold, all guilds get real values
    clr_values = {}
    for guild, abund in guild_abundances.items():
        effective_abund = abund if abund > 0 else PSEUDOCOUNT
        clr_values[guild] = np.log(effective_abund / geometric_mean) if not np.isnan(geometric_mean) else np.nan
    
    return {
        "guild_abundances": guild_abundances,
        "clr_values": clr_values,
        "geometric_mean": geometric_mean,
        "total_assigned": total_assigned,
        "unassigned": unassigned
    }


# === MAIN PIPELINE ===
def run_integrated_analysis(sample: str) -> Tuple[str, str]:
    """Generate metrics-only integrated report and guild taxa list."""
    print(f"\n{'='*60}")
    print(f"Integrated Microbiome Analysis (metrics-only): {sample}")
    print(f"{'='*60}\n")

    # 1. Compositional metrics
    comp_metrics = compute_compositional_metrics(sample)

    # 2. Tiered missing taxa
    tiered_missing = identify_tiered_missing_taxa(sample)

    # 3. Load functional data
    metaphlan_path = os.path.join(GMWI_RESULTS_DIR, f"{sample}_run_metaphlan.txt")
    metaphlan = load_metaphlan(metaphlan_path)

    coeffs_path = os.path.join(KNOWLEDGE_BASE_DIR, "GMWI2_taxa_coefficients.tsv")
    gmwi_model = load_coeffs_gmwi2(coeffs_path)
    gmwi_model = gmwi_model[gmwi_model["coef"] != 0].copy()

    gmwi_sample_path = os.path.join(GMWI_RESULTS_DIR, f"{sample}_run_GMWI2_taxa.txt")
    gmwi_sample = load_sample_gmwi2(gmwi_sample_path)

    # 4. Map features and compute abundance-weighted scores
    fx = map_features_to_sample(metaphlan, gmwi_model)

    signature_balance = compute_signature_balance(fx)
    fx["abundance_weighted_score"] = fx["coef"] * fx["abundance_prop"]
    abundance_weighted_gmwi2 = fx["abundance_weighted_score"].sum()
    supportive_weighted = fx.loc[fx["coef"] > 0, "abundance_weighted_score"].sum()
    opportunistic_weighted = fx.loc[fx["coef"] < 0, "abundance_weighted_score"].sum()

    # 5. Taxa presence/absence counts
    supportive = gmwi_model[gmwi_model["direction"] == "Supportive"]
    supportive_total_count = len(supportive)
    supportive_missing = supportive[~supportive.feature_token.isin(gmwi_sample.feature_token)]
    supportive_present = fx[fx["direction"] == "Supportive"]
    supportive_underrepresented = fx[(fx.representation == "Under") & (fx.direction == "Supportive")]

    opportunistic_present = fx[fx["direction"] == "Opportunistic"]
    opportunistic_overrepresented = fx[(fx.representation == "Over") & (fx.direction == "Opportunistic")]

    supportive_missing_count = len(supportive_missing)
    supportive_present_count = len(supportive_present)
    supportive_under_count = len(supportive_underrepresented)
    opportunistic_present_count = len(opportunistic_present)
    opportunistic_over_count = len(opportunistic_overrepresented)

    # 6. Functional pathway analysis
    absent, substantially_present, fully_covered, pathways_comlevel, pathways_stratified = analyze_functional_pathways(sample)

    # Get all pathways for detailed summaries
    # FUNCTIONAL_DIR already includes the sample ID
    pathway_dir = FUNCTIONAL_DIR
    
    # Updated filename pattern
    path_ab = pd.read_csv(
        os.path.join(pathway_dir, f"{sample}_4_pathabundance_relab.tsv"),
        sep="\t",
        names=["pathway", "relab"],
        skiprows=1,
    )
    
    # Check if coverage file exists
    coverage_file = os.path.join(pathway_dir, f"{sample}_pathcoverage.tsv")
    if os.path.exists(coverage_file):
        path_cov = pd.read_csv(
            coverage_file,
            sep="\t",
            names=["pathway", "coverage"],
            skiprows=1,
        )
        all_pathways = path_cov.merge(path_ab, on="pathway", how="left")
    else:
        # No coverage file - assume 100% coverage
        all_pathways = path_ab.copy()
        all_pathways["coverage"] = 1.0
    all_pathways = all_pathways[~all_pathways["pathway"].str.contains(r"\|", regex=True)].copy()
    all_pathways = all_pathways[~all_pathways["pathway"].isin(["UNMAPPED", "UNINTEGRATED"])]

    # Detailed pathway architecture analysis
    keywords_df = pd.read_csv(CORE_PATHWAYS_PATH, sep="\t")
    detailed_pathway_results = analyze_all_categories(
        keywords_df,
        pathways_comlevel,
        pathways_stratified,
        verbose=False
    )

    # Guild compositional analysis with FFA weighting (NEW in v1.3)
    guild_summary, guild_taxa = analyze_functional_guilds_ffa(metaphlan, rank_filter="s")

    # Diversity analysis
    diversity_df = analyze_rank_diversity(metaphlan)
    species_row = diversity_df[diversity_df["rank"] == "s"]
    shannon = species_row["shannon"].iloc[0] if not species_row.empty else float("nan")
    pielou = species_row["pielou"].iloc[0] if not species_row.empty else float("nan")

    # Firmicutes/Bacteroidetes ratio
    phyla = metaphlan[metaphlan["rank"] == "p"].copy()
    firm = phyla[phyla["leaf_token"] == "p__Firmicutes"]["rel_abund_prop"].sum()
    bact = phyla[phyla["leaf_token"] == "p__Bacteroidetes"]["rel_abund_prop"].sum()
    fb_ratio = firm / bact if bact > 0 else float("nan")

    # Calculate multi-axis guild metrics (NEW in v1.4)
    # Includes "Unassigned" category for complete sample CLR calculation
    guild_clr_data = calculate_guild_clr_with_unassigned(guild_summary)
    
    # Calculate CLR-based diagnostic ratios (v3.1 - uses guild-level CLR)
    # These ratios quantify metabolic state: carbohydrate vs protein utilization,
    # fermentation efficiency, mucus dependency, and putrefaction pressure
    clr_ratios = calculate_clr_diagnostic_ratios(guild_summary, guild_clr_data)

    # Vitamin supplementation signals assessment
    vitamin_signals = assess_vitamin_supplementation_signals(metaphlan, diversity_df, fb_ratio)

    # Analyze core pathways for category summary (needed for plotting)
    present_core, fractured_core, absent_core, core_category_summary = analyze_core_pathways(
        sample, substantially_present, all_pathways, absent
    )

    # Functional pathway summary table
    summary_table = get_category_summary_table(detailed_pathway_results) if detailed_pathway_results else pd.DataFrame()

    # Build metrics-only report text
    os.makedirs(INTEGRATED_REPORT_DIR, exist_ok=True)
    report_path = os.path.join(INTEGRATED_REPORT_DIR, f"{sample}_only_metrics.txt")

    # Get Health Fraction interpretation
    hf_state = interpret_health_fraction(comp_metrics['health_fraction'])
    
    lines: list[str] = []
    lines.append(f"Integrated Microbiome Analysis: {sample}")
    lines.append("")
    lines.append("1. COMPOSITIONAL METRICS")
    lines.append("")
    lines.append("Presence-based (what's there):")
    lines.append(f"  GMWI2: {comp_metrics['gmwi2_score']:+.4f}")
    lines.append(f"  BR (Bias Ratio): {comp_metrics['bias_ratio']:.3f}")
    lines.append(f"  CB (Count Balance): {comp_metrics['count_balance']:+.3f}")
    lines.append("")
    lines.append("Abundance-based (how what's there behaves):")
    lines.append(f"  wGMWI2 (Abundance-weighted GMWI2): {abundance_weighted_gmwi2:+.4f}")
    if not math.isnan(signature_balance):
        fold = math.exp(abs(signature_balance))
        lines.append(f"  SB (Signature Balance): {signature_balance:+.3f} ({fold:.1f}×)")
    else:
        lines.append(f"  SB (Signature Balance): N/A")
    lines.append("")
    lines.append("Reference comparisons (where the sample sits vs model range):")
    lines.append(f"  HF (Health Fraction): {comp_metrics['health_fraction']:.3f} [{hf_state}]")
    lines.append(f"  z-score: {comp_metrics['z_approx']:+.2f}")
    lines.append("")
    lines.append("2. DIVERSITY SIGNATURES")
    lines.append("")
    lines.append("Species-level diversity:")
    lines.append(f"  Shannon: {shannon:.3f}")
    lines.append(f"  Pielou evenness: {pielou:.3f}")
    lines.append("")
    lines.append("3. GUILD-LEVEL ANALYSIS (Multi-Axis Framework)")
    lines.append("")
    lines.append("NOTE: Reference ranges represent approximate guild-level relative abundance ranges")
    lines.append("      in healthy adults extracted from published literature.")
    lines.append("      These are soft envelopes, not diagnostic thresholds. Individual variation exists.")
    lines.append("")
    lines.append(f"Firmicutes/Bacteroidetes ratio: {fb_ratio:.3f}")
    lines.append(f"Total assigned to defined guilds: {guild_clr_data['total_assigned']*100:.1f}%")
    lines.append(f"Unassigned bacteria: {guild_clr_data['unassigned']*100:.1f}%")
    lines.append(f"Geometric mean (including Unassigned): {guild_clr_data['geometric_mean']*100:.2f}%")
    lines.append("")
    lines.append("CLR-Based Diagnostic Ratios:")
    lines.append(f"  CUR (Carbohydrate Utilization): {clr_ratios['CUR']:+.3f} [{clr_ratios['CUR_interpretation']}]")
    lines.append(f"  FCR (Fermentation Completion): {clr_ratios['FCR']:+.3f} [{clr_ratios['FCR_interpretation']}]")
    lines.append(f"  MDR (Mucus Dependency): {clr_ratios['MDR']:+.3f} [{clr_ratios['MDR_interpretation']}]")
    lines.append(f"  PPR (Putrefaction Pressure): {clr_ratios['PPR']:+.3f} [{clr_ratios['PPR_interpretation']}]")
    lines.append("")
    lines.append("CLR Ratio Formulas Used (for validation):")
    lines.append("  CUR = [(Fiber_CLR + Bifido_CLR) / 2] - Proteolytic_CLR")
    lines.append("  FCR = [(Butyrate_CLR + CrossFeed_CLR) / 2] - Bifido_CLR")
    lines.append("  MDR = Mucin_CLR - Fiber_CLR")
    lines.append("  PPR = Proteolytic_CLR - Butyrate_CLR")
    lines.append("  Source: integrated_report_flexible_COMPLETE.py, calculate_clr_diagnostic_ratios()")
    lines.append("")
    lines.append("Multi-Axis Guild Assessment:")
    lines.append("")
    
    # For each guild, add multi-axis metrics
    for _, row in guild_summary.iterrows():
        guild_name = row['guild']
        abundance = row['total_abundance']
        redundancy = row["pielou_evenness"] if not pd.isna(row["pielou_evenness"]) else 0.0
        
        # Axis 1: Absolute capacity
        abs_cap = assess_absolute_capacity(guild_name, abundance)
        
        # Axis 2: Relative dominance (CLR including Unassigned)
        clr_with_unassigned = guild_clr_data['clr_values'].get(guild_name, np.nan)
        if not pd.isna(clr_with_unassigned):
            fold_change = np.exp(clr_with_unassigned)
            clr_status = "Enriched" if clr_with_unassigned > 0.5 else ("Depleted" if clr_with_unassigned < -0.5 else "Balanced")
        else:
            fold_change = np.nan
            clr_status = "Absent"
        
        lines.append(f"  {guild_name}:")
        lines.append(f"    Abundance: {abundance*100:.2f}% | Redundancy: {redundancy:.2f} | Status: {row['status']}")
        lines.append(f"    Axis 1 (Absolute): {abs_cap['status']} - {abs_cap['interpretation']}")
        if not pd.isna(clr_with_unassigned):
            lines.append(f"    Axis 2 (Relative): CLR {clr_with_unassigned:+.2f} ({clr_status}, {fold_change:.2f}× GM)")
        else:
            # v3.2: This branch should rarely trigger since all guilds now get real CLR values
            lines.append(f"    Axis 2 (Relative): CLR unavailable (guild not in CLR calculation)")
        lines.append("")
    lines.append("")
    lines.append("4. VITAMIN SUPPLEMENTATION METAGENOMIC SIGNALS")
    lines.append("")
    lines.append("4.1 Compositional Risk Indicators:")
    lines.append(f"  Shannon diversity: {shannon:.3f} {'[<2.0 FLAG]' if shannon < 2.0 else ''}")
    lines.append(f"  Bacteroides genus: {vitamin_signals['bacteroides_pct']:.2f}%")
    lines.append(f"  F:B ratio: {fb_ratio:.3f} {'[>2.0 DYSBIOSIS]' if fb_ratio > 2.0 else ''}")
    lines.append(f"  Lachnospiraceae + Ruminococcaceae: {vitamin_signals['lachno_rumino_pct']:.2f}% {'[<2% FLAG]' if vitamin_signals['lachno_rumino_pct'] < 2 else ''}")
    lines.append(f"  Akkermansia muciniphila: {vitamin_signals['akkermansia_pct']:.2f}% {'[<0.5% FLAG]' if vitamin_signals['akkermansia_pct'] < 0.5 else ''}")
    lines.append("")
    lines.append("4.2 Vitamin-Specific Signals:")
    lines.append("")
    lines.append("B12 (Cobalamin) - INVERSE SIGNAL:")
    lines.append(f"  B. ovatus: {vitamin_signals['b_ovatus_pct']:.2f}%")
    lines.append(f"  Shannon: {shannon:.3f}")
    inverse_flag = "ELEVATED B.ovatus + LOW diversity" if (vitamin_signals['b_ovatus_pct'] > 2.0 and shannon < 2.5) else "No inverse signal"
    lines.append(f"  Flag: {inverse_flag}")
    lines.append("")
    lines.append("Folate (B9) - DIVERSITY-DEPENDENT:")
    lines.append(f"  Shannon: {shannon:.3f} {'[<2.0 FLAG]' if shannon < 2.0 else ''}")
    lines.append(f"  Bacteroides: {vitamin_signals['bacteroides_pct']:.2f}% {'[<5% FLAG]' if vitamin_signals['bacteroides_pct'] < 5 else ''}")
    lines.append(f"  Bifidobacterium: {vitamin_signals['bifido_genus_pct']:.2f}%")
    lines.append(f"  Risk Score: {vitamin_signals['folate_risk_score']}/3")
    lines.append("")
    lines.append("Biotin (B7) - LIMITED PRODUCER:")
    lines.append(f"  Producers detected: {vitamin_signals['biotin_producer_count']}/4")
    lines.append(f"  B. fragilis: {vitamin_signals['b_fragilis_status']}")
    lines.append(f"  P. copri: {vitamin_signals['p_copri_status']}")
    lines.append(f"  F. varium: {vitamin_signals['f_varium_status']}")
    lines.append(f"  C. coli: {vitamin_signals['c_coli_status']}")
    lines.append("")
    lines.append("B1, B2, B5, B6 - COMPOSITION-DEPENDENT:")
    b_complex_protective = "PROTECTIVE" if vitamin_signals['bacteroides_pct'] >= 10 else f"[<10% FLAG]"
    lines.append(f"  Bacteroides: {vitamin_signals['bacteroides_pct']:.2f}% {b_complex_protective}")
    lines.append(f"  F:B ratio: {fb_ratio:.3f} {'[>2.0 DYSBIOSIS]' if fb_ratio > 2.0 else ''}")
    lines.append(f"  Lachno + Rumino: {vitamin_signals['lachno_rumino_pct']:.2f}% {'[<2% FLAG]' if vitamin_signals['lachno_rumino_pct'] < 2 else ''}")
    lines.append(f"  Risk Score: {vitamin_signals['b_complex_risk_score']}/3")
    lines.append("")
    lines.append("Vitamin K2 - POOREST SIGNAL:")
    lines.append("  Note: Metagenomic data insufficient for K2 assessment")
    lines.append("")
    lines.append("")
    
    # Determine which HUMANn version for pathway reporting
    has_coverage = os.path.exists(coverage_file)
    
    if has_coverage:
        # HUMANn v3 reporting
        lines.append("5. FUNCTIONAL PATHWAYS (HUMANn v3)")
        lines.append("")
        lines.append("NOTE: Coverage-based pathway classification. Pathway abundance and coverage together")
        lines.append("      indicate metabolic capacity. High coverage (≥60%) = complete pathway.")
    else:
        # HUMANn v4 reporting
        lines.append("5. FUNCTIONAL PATHWAYS (HUMANn v4)")
        lines.append("")
        lines.append("NOTE: Abundance-only classification (no coverage data). Pathways classified by")
        lines.append("      quantiles: High (>75th %ile), Moderate (50-75th %ile), Marginal (<50th %ile).")
        lines.append("      Abundance encodes confidence - higher abundance = more confident detection.")
    
    lines.append("")
    lines.append("NOTE: Pathway presence data retained for reference but NOT used for vitamin supplementation decisions.")
    lines.append("")
    lines.append("Summary across categories:")
    if not summary_table.empty:
        lines.append(summary_table.to_string(index=False))
    lines.append("")
    if detailed_pathway_results:
        for category, analysis in detailed_pathway_results.items():
            lines.append(format_category_table_report(analysis).rstrip())
    
    # Add section 7: Select Taxa Abundance
    lines.append("")
    lines.append("")
    lines.append("7. SELECT TAXA ABUNDANCE")
    lines.append("")
    
    # Function to extract and report taxon abundance
    def report_taxon(taxon_name, search_pattern, rank="s"):
        taxon_data = metaphlan[
            (metaphlan["rank"] == rank) & 
            (metaphlan["leaf_token"].str.contains(search_pattern, case=False, na=False))
        ]
        
        lines.append(f"{taxon_name}:")
        
        if not taxon_data.empty:
            abund = taxon_data["rel_abund_prop"].sum()
            pct = abund * 100
            clr = taxon_data["clr"].iloc[0] if len(taxon_data) == 1 else taxon_data["clr"].mean()
            
            lines.append(f"  Relative abundance: {pct:.4f}%")
            lines.append(f"  CLR (enrichment): {clr:+.3f}")
            
            # Interpretation
            if pct > 0.1:
                lines.append(f"  Status: Present (detectable)")
            elif pct > 0.01:
                lines.append(f"  Status: Low abundance")
            else:
                lines.append(f"  Status: Trace amounts")
        else:
            lines.append("  Relative abundance: 0.0000%")
            lines.append("  CLR: N/A")
            lines.append("  Status: Not detected")
        
        lines.append("")
    
    # Report each taxon
    report_taxon("Fusobacterium nucleatum", "s__Fusobacterium_nucleatum", "s")
    report_taxon("Streptococcus gallolyticus", "s__Streptococcus_gallolyticus", "s")
    report_taxon("Peptostreptococcus anaerobius", "s__Peptostreptococcus_anaerobius", "s")
    report_taxon("Escherichia-Shigella", "Escherichia|Shigella", "s")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    # Save functional guild taxa list with FFA weighting (NEW in v1.3)
    os.makedirs(INTEGRATED_REPORT_DIR, exist_ok=True)
    guild_output_path = os.path.join(INTEGRATED_REPORT_DIR, f"{sample}_functional_guild.txt")
    guild_csv_path = os.path.join(INTEGRATED_REPORT_DIR, f"{sample}_functional_guild.csv")
    
    if not guild_taxa.empty:
        guild_taxa = guild_taxa.sort_values(["guilds", "ffa_contribution"], ascending=[True, False])
        # Save as tab-delimited TXT
        guild_taxa[["guilds", "clade_name", "rel_abund_prop", "ffa_weight", "ffa_contribution", "clr"]].to_csv(
            guild_output_path, sep="\t", index=False
        )
        # Also save as CSV for easier analysis
        guild_taxa[["guilds", "clade_name", "rel_abund_prop", "ffa_weight", "ffa_contribution", "clr"]].to_csv(
            guild_csv_path, index=False
        )
    else:
        with open(guild_output_path, "w") as f:
            f.write("guilds\tclade_name\trel_abund_prop\tffa_weight\tffa_contribution\tclr\n")
        with open(guild_csv_path, "w") as f:
            f.write("guilds,clade_name,rel_abund_prop,ffa_weight,ffa_contribution,clr\n")

    # Generate all plots and save to PDF
    logging.info("Generating analysis plots...")
    pdf_path = save_all_plots_to_pdf(sample, guild_summary, diversity_df, 
                                     core_category_summary, tiered_missing)
    
    print(f"✓ Metrics report saved to: {report_path}")
    print(f"✓ Guild taxa list saved to: {guild_output_path}")
    print(f"✓ Guild taxa CSV saved to: {guild_csv_path}")
    print(f"✓ Plots PDF saved to: {pdf_path}\n")

    return report_path, guild_output_path


def discover_samples_in_batch(batch_id: str) -> List[str]:
    """Discover all sample directories in a batch."""
    work_dir = "/Users/pnovikova/Documents/work"
    batch_dir = os.path.join(work_dir, "analysis", batch_id)
    
    if not os.path.exists(batch_dir):
        raise FileNotFoundError(f"Batch directory not found: {batch_dir}")
    
    samples = []
    for item in os.listdir(batch_dir):
        item_path = os.path.join(batch_dir, item)
        # Check if it's a directory and looks like a sample ID (starts with digit)
        if os.path.isdir(item_path) and item[0].isdigit():
            # Verify it has GMWI2 results (check both directory structures)
            gmwi_dir = os.path.join(item_path, "GMWI2")
            gmwi_dir_bio = os.path.join(item_path, "bioinformatics", "GMWI2")
            if os.path.exists(gmwi_dir) or os.path.exists(gmwi_dir_bio):
                samples.append(item)
    
    return sorted(samples)


if __name__ == "__main__":
    # Parse arguments
    args = parse_arguments()
    
    # Validate arguments
    if args.all_samples and args.sample_id:
        print("Error: Cannot specify both --sample_id and --all-samples")
        sys.exit(1)
    
    if not args.all_samples and not args.sample_id:
        print("Error: Must specify either --sample_id or --all-samples")
        sys.exit(1)
    
    # Determine samples to process
    if args.all_samples:
        # Batch mode: discover all samples
        try:
            samples = discover_samples_in_batch(args.batch_id)
            if not samples:
                print(f"No samples found in batch {args.batch_id}")
                sys.exit(1)
            
            print("="*60)
            print(f"BATCH MODE: Processing {len(samples)} samples in {args.batch_id}")
            print("="*60)
            print()
            
            # Track results
            success_count = 0
            fail_count = 0
            failed_samples = []
            
            # Process each sample
            for i, sample_id in enumerate(samples, 1):
                print(f"[{i}/{len(samples)}] Processing {sample_id}...")
                
                # Setup paths for this sample
                paths = setup_flexible_paths(args.batch_id, sample_id)
                log_file = setup_logging(paths)
                
                try:
                    logging.info("Starting integrated analysis...")
                    report_path, guild_path = run_integrated_analysis(sample_id)
                    
                    logging.info("✓ Analysis complete!")
                    logging.info(f"Log file: {log_file}")
                    
                    # Write success status
                    status_file = os.path.join(paths["LOG_DIR"], f"{sample_id}_integrated_report.status")
                    with open(status_file, "w") as f:
                        f.write("INTEGRATED_REPORT_SUCCESS\n")
                    
                    logging.info("="*60)
                    logging.info("Integrated Report Pipeline Complete")
                    logging.info("="*60)
                    
                    print(f"  ✓ {sample_id} completed successfully\n")
                    success_count += 1
                    
                except Exception as e:
                    logging.error(f"✗ Analysis failed: {e}", exc_info=True)
                    
                    # Write failure status
                    status_file = os.path.join(paths["LOG_DIR"], f"{sample_id}_integrated_report.status")
                    with open(status_file, "w") as f:
                        f.write(f"INTEGRATED_REPORT_FAILED\n{str(e)}\n")
                    
                    print(f"  ✗ {sample_id} FAILED: {str(e)}\n")
                    fail_count += 1
                    failed_samples.append(sample_id)
            
            # Print summary
            print("="*60)
            print("BATCH PROCESSING COMPLETE")
            print("="*60)
            print(f"Success: {success_count}/{len(samples)} samples")
            print(f"Failed: {fail_count}/{len(samples)} samples")
            
            if failed_samples:
                print("\nFailed samples:")
                for sample_id in failed_samples:
                    print(f"  - {sample_id}")
                sys.exit(1)
            else:
                print("\nAll samples processed successfully!")
                sys.exit(0)
                
        except Exception as e:
            print(f"Batch processing error: {e}")
            sys.exit(1)
    
    else:
        # Single sample mode
        paths = setup_flexible_paths(args.batch_id, args.sample_id)
        log_file = setup_logging(paths)
        
        try:
            logging.info("Starting integrated analysis...")
            
            # Run integrated analysis
            report_path, guild_path = run_integrated_analysis(args.sample_id)
            
            logging.info("✓ Analysis complete!")
            logging.info(f"Log file: {log_file}")
            
            # Write success status
            status_file = os.path.join(paths["LOG_DIR"], f"{paths['SAMPLE_ID']}_integrated_report.status")
            with open(status_file, "w") as f:
                f.write("INTEGRATED_REPORT_SUCCESS\n")
            
            logging.info("="*60)
            logging.info("Integrated Report Pipeline Complete")
            logging.info("="*60)
            
        except Exception as e:
            logging.error(f"✗ Analysis failed: {e}", exc_info=True)
            
            # Write failure status
            status_file = os.path.join(paths["LOG_DIR"], f"{paths['SAMPLE_ID']}_integrated_report.status")
            with open(status_file, "w") as f:
                f.write(f"INTEGRATED_REPORT_FAILED\n{str(e)}\n")
            
            sys.exit(1)
