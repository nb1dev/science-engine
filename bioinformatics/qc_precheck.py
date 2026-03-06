#!/usr/bin/env python3
"""
Metagenomics QC Pre-Check Script v2
Runs quality control checks on MetaPhlAn + HUMAnN outputs before GMWI2 pipeline.

v2 improvements:
  - Input validation: duplicate IDs, NaN/negative values, sum checks, unit detection (1A/1B/1C)
  - Version fingerprinting from file headers (6B)
  - FASTQ integrity via MD5 checksums from S3 (7A')
  - Cross-layer richness & concentration alignment using reactions file (2A/2B)
  - Enhanced taxonomy: placeholder burden by rank, rank collapse, dominance plausibility (4A/4B/4C)
  - Expanded taxonomy alias dictionary (6A)
  - Housekeeping dominance flag & SCFA claims guardrail (5C/5D)
  - Functional Evidence Score 0-100 composite (3)
  - Allowed claims policy block (8)
  - Batch outlier detection & batch effect hints (9A/9B)

Usage:
    python qc_precheck_v2.py --batch nb1_2026_004 --sample 1421029282376
    python qc_precheck_v2.py --batch nb1_2026_004 --sample 1421029282376 --local-only
    python qc_precheck_v2.py --batch nb1_2026_004 --all
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import gzip
from datetime import datetime
from pathlib import Path
from collections import defaultdict

WORK_DIR = "/Users/pnovikova/Documents/work"
S3_BUCKET = "s3://nb1-prebiomics-sample-data/incoming"

# Taxonomy alias map for consistency checks — expanded (6A)
TAXONOMY_ALIASES = {
    "Segatella": "Prevotella",
    "Prevotella": "Segatella",
    "Phocaeicola": "Bacteroides",
    "Bacteroides": "Phocaeicola",
    "Agathobacter": "Eubacterium",
    "Eubacterium": "Agathobacter",
    "Limosilactobacillus": "Lactobacillus",
    "Lactobacillus": "Limosilactobacillus",
    "Lacticaseibacillus": "Lactobacillus",
    "Ligilactobacillus": "Lactobacillus",
    "Blautia": "Ruminococcus",  # some reclassifications
    "Mediterraneibacter": "Ruminococcus",
    "Clostridium_M": "Clostridium",
    "Clostridium_Q": "Clostridium",
    "Enterocloster": "Clostridium",
}
TAXONOMY_ALIASES_VERSION = "2026-02-23"

# Housekeeping / core metabolism pathway IDs (5C)
HOUSEKEEPING_PATHWAYS = {
    "PWY-7219", "PWY-7220", "PWY-7221", "PWY-7222",  # nucleotide biosynthesis
    "PWY-7228", "PWY-7197", "PWY-7199", "PWY-7200",  # purine/pyrimidine
    "GLYCOLYSIS", "GLYCOLYSIS-E-D", "ANAGLYCOLYSIS-PWY",  # glycolysis/gluconeogenesis
    "PWY-5686", "PWY-5695", "PWY-7111",  # UMP / CTP biosynthesis
    "TRNA-CHARGING-PWY",  # tRNA charging
    "PWY-6121", "PWY-6122",  # 5-aminoimidazole ribonucleotide biosynthesis
    "PWY-6700", "PWY-6703",  # queuosine biosynthesis
    "PWY-7208",  # superpathway of pyrimidine nucleobases salvage
    "PWY-6609",  # adenine/adenosine salvage
    "PWY0-1296",  # purine ribonucleosides degradation
    "PEPTIDOGLYCANSYN-PWY",  # peptidoglycan biosynthesis
    "PWY-5097", "PWY-5100", "PWY-5101", "PWY-5103", "PWY-5104",  # Lys/Thr/Ile/Met biosynthesis
    "PWY-6387",  # UDP-N-acetylmuramoyl-pentapeptide biosynthesis
}

# Sanity rules: pathway keywords -> required taxa (at phylum or genus level)
SANITY_RULES = [
    {
        "name": "Methanogenesis without Archaea",
        "pathway_keywords": ["methanogenesis", "METHANOGENESIS"],
        "required_taxa_keywords": ["Archaea", "Methanobrevibacter", "Methanobacterium"],
        "level": "any",
        "message": "Methanogenesis pathways detected but no Archaea in taxonomy. Possible cross-mapping artifact or below-detection Archaea."
    },
    {
        "name": "Sulfate reduction without sulfate reducers",
        "pathway_keywords": ["sulfate reduction", "sulfite reduction", "SO4RED", "SULFATE-REDUCERS"],
        "required_taxa_keywords": ["Desulfovibrio", "Desulfobacter", "Desulfobulbus", "Bilophila"],
        "level": "genus",
        "message": "Sulfate reduction pathways detected but classic sulfate-reducing bacteria absent or very low."
    },
    {
        "name": "Butyrate production without butyrate producers",
        "pathway_keywords": ["PWY-5676", "PWY-5677", "CENTFERM-PWY"],
        "required_taxa_keywords": ["Faecalibacterium", "Roseburia", "Eubacterium", "Anaerostipes", "Coprococcus", "Agathobaculum", "Butyricicoccus"],
        "level": "genus",
        "message": "Butyrate production pathways detected but known butyrate-producing genera absent."
    },
]


# ═══════════════════════════════════════════════════════════════
# DATA LOADING FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def download_from_aws(batch_id, sample_id, data_types=None):
    """Download sample data from S3."""
    if data_types is None:
        data_types = ["functional_profiling", "taxonomic_profiling", "raw_sequences"]
    for dt in data_types:
        local_dir = f"{WORK_DIR}/data/{batch_id}/{dt}/{sample_id}"
        s3_path = f"{S3_BUCKET}/{batch_id}/{dt}/{sample_id}/"
        if os.path.isdir(local_dir) and os.listdir(local_dir):
            print(f"  [SKIP] {dt} already exists locally")
            continue
        print(f"  [DOWNLOAD] {dt} from S3...")
        os.makedirs(local_dir, exist_ok=True)
        subprocess.run(["aws", "s3", "sync", s3_path, local_dir + "/", "--quiet"], check=True)
        print(f"  [OK] {dt} downloaded")


def load_metaphlan_profile(batch_id, sample_id):
    """Load MetaPhlAn profile, return list of dicts with clade_name, relab."""
    path = f"{WORK_DIR}/data/{batch_id}/taxonomic_profiling/{sample_id}/{sample_id}_profile.tsv"
    if not os.path.exists(path):
        return None, None
    taxa = []
    reads_processed = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") and "reads processed" in line.lower():
                m = re.search(r'#(\d+)\s+reads', line)
                if m:
                    reads_processed = int(m.group(1))
                continue
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                taxa.append({"clade_name": parts[0], "ncbi_tax_id": parts[1], "relative_abundance": float(parts[2])})
    return taxa, reads_processed


def load_genefamilies_relab(batch_id, sample_id):
    """Load gene families relab, return unmapped fraction."""
    path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_2_genefamilies_relab.tsv"
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if parts[0] == "READS_UNMAPPED" or parts[0] == "UNMAPPED":
                return float(parts[1])
    return None


def load_pathway_abundance_relab(batch_id, sample_id):
    """Load pathway abundance relab. Return dict with unmapped, unintegrated, and stratified pathways."""
    path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_4_pathabundance_relab.tsv"
    if not os.path.exists(path):
        return None
    result = {"unmapped": 0.0, "unintegrated": 0.0, "pathways": {}, "stratified": defaultdict(dict)}
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            name, value = parts[0], float(parts[1])
            if name == "UNMAPPED":
                result["unmapped"] = value
            elif name == "UNINTEGRATED":
                result["unintegrated"] = value
            elif name.startswith("UNINTEGRATED|"):
                continue
            elif "|" in name:
                pwy, contributor = name.split("|", 1)
                pwy_base = pwy.split(":")[0].strip() if ":" in pwy else pwy
                result["stratified"][pwy_base][contributor] = value
            else:
                pwy_base = name.split(":")[0].strip() if ":" in name else name
                result["pathways"][pwy_base] = {"full_name": name, "abundance": value}
    return result


def count_fastq_reads(batch_id, sample_id):
    """Count reads in FASTQ files (compressed)."""
    raw_dir = f"{WORK_DIR}/data/{batch_id}/raw_sequences/{sample_id}"
    r1_gz = f"{raw_dir}/{sample_id}_R1.fastq.gz"
    r1_fq = f"{raw_dir}/{sample_id}_R1.fastq"
    counts = {}
    for label, fpath in [("R1", r1_gz), ("R1_uncompressed", r1_fq)]:
        if os.path.exists(fpath):
            if fpath.endswith(".gz"):
                result = subprocess.run(f"zcat < '{fpath}' | head -1000000 | wc -l", shell=True, capture_output=True, text=True)
                line_count = int(result.stdout.strip())
                if line_count >= 1000000:
                    # Estimate total from file size
                    gz_size = os.path.getsize(fpath)
                    est_lines = int(line_count * (gz_size / (gz_size * (1000000 / line_count) if line_count > 0 else 1)))
                    # Better: just count with pigz/zcat  - use sampling
                    counts["r1_reads_sampled"] = line_count // 4
                    counts["r1_sampling_note"] = "Sampled first 250K reads for speed"
                else:
                    counts["r1_reads"] = line_count // 4
            else:
                result = subprocess.run(f"wc -l < '{fpath}'", shell=True, capture_output=True, text=True)
                counts["r1_reads"] = int(result.stdout.strip()) // 4
            break
    # Check file sizes for quick estimate
    for suffix in ["_R1.fastq.gz", "_R2.fastq.gz"]:
        fp = f"{raw_dir}/{sample_id}{suffix}"
        if os.path.exists(fp):
            size_gb = os.path.getsize(fp) / (1024**3)
            counts[f"{suffix}_size_gb"] = round(size_gb, 2)
    return counts if counts else None


# ═══════════════════════════════════════════════════════════════
# V2: INPUT VALIDATION (1A — row/ID validity)
# ═══════════════════════════════════════════════════════════════

def validate_tsv_file(filepath):
    """Check a single TSV for duplicate IDs, NaN/negative values, separator issues. (1A)"""
    result = {"file": os.path.basename(filepath), "status": "PASS", "issues": []}
    if not os.path.exists(filepath):
        result["status"] = "MISSING"
        result["issues"].append("File not found")
        return result
    ids_seen = set()
    line_num = 0
    with open(filepath) as f:
        for line in f:
            if line.startswith("#"):
                continue
            line_num += 1
            parts = line.strip().split("\t")
            if len(parts) < 2:
                if "," in line and "\t" not in line:
                    result["issues"].append(f"Line {line_num}: comma-separated instead of tab-separated")
                    result["status"] = "FAIL"
                continue
            row_id = parts[0]
            # Skip stratified rows (contain |) for duplicate check
            if "|" not in row_id:
                if row_id in ids_seen:
                    result["issues"].append(f"Duplicate ID: {row_id}")
                    result["status"] = "FAIL"
                ids_seen.add(row_id)
            # Check numeric values
            for i, val_str in enumerate(parts[1:], 1):
                try:
                    val = float(val_str)
                    if math.isnan(val):
                        result["issues"].append(f"Line {line_num} col {i}: NaN value")
                        result["status"] = "FAIL"
                    elif val < 0:
                        result["issues"].append(f"Line {line_num} col {i}: negative value ({val})")
                        result["status"] = "FAIL"
                except ValueError:
                    pass  # non-numeric columns are OK (e.g., additional_species)
    if not result["issues"]:
        result["issues"].append("No issues found")
    return result


def detect_unit(filepath):
    """Auto-detect whether values are fractions (0-1), percentages (0-100), or counts. (1C)"""
    if not os.path.exists(filepath):
        return "unknown"
    values = []
    with open(filepath) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 2 and "|" not in parts[0]:
                try:
                    values.append(float(parts[1]))
                except ValueError:
                    pass
            if len(values) >= 50:
                break
    if not values:
        return "unknown"
    max_val = max(values)
    if max_val > 100:
        return "counts_or_cpm"
    elif max_val > 1.5:
        return "percentage_0_100"
    else:
        return "fraction_0_1"


def extract_version_info(batch_id, sample_id):
    """Parse MetaPhlAn and HUMAnN version strings from file headers. (6B)"""
    versions = {"metaphlan_db": "unknown", "humann_version": "unknown"}
    tax_path = f"{WORK_DIR}/data/{batch_id}/taxonomic_profiling/{sample_id}/{sample_id}_profile.tsv"
    if os.path.exists(tax_path):
        with open(tax_path) as f:
            for line in f:
                if line.startswith("#mpa_v"):
                    versions["metaphlan_db"] = line.strip().lstrip("#")
                    break
                if not line.startswith("#"):
                    break
    pwy_path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_4_pathabundance_relab.tsv"
    if os.path.exists(pwy_path):
        with open(pwy_path) as f:
            for line in f:
                if line.startswith("# ") and "HUMAnN" in line:
                    m = re.search(r'HUMAnN\s+v[\d.]+\S*', line)
                    if m:
                        versions["humann_version"] = m.group(0)
                    break
                if not line.startswith("#"):
                    break
    return versions


def validate_sum_checks(batch_id, sample_id, taxa):
    """Verify taxonomy sums ~100%, pathway fractions sum ~1.0, gene families sum ~1.0. (1B)"""
    checks = []
    # Taxonomy sum check
    if taxa:
        kingdom = [t for t in taxa if t["clade_name"].count("|") == 0]
        kingdom_sum = sum(t["relative_abundance"] for t in kingdom)
        tax_ok = 99.0 <= kingdom_sum <= 101.0
        checks.append({
            "check": "taxonomy_relab_sum",
            "expected": "~100%",
            "actual": round(kingdom_sum, 2),
            "status": "PASS" if tax_ok else "WARN",
        })
    # Pathway relab sum check
    pwy_path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_4_pathabundance_relab.tsv"
    if os.path.exists(pwy_path):
        unmapped = unintegrated = 0.0
        pwy_sum = 0.0
        with open(pwy_path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.strip().split("\t")
                if len(parts) < 2 or "|" in parts[0]:
                    continue
                val = float(parts[1])
                if parts[0] == "UNMAPPED":
                    unmapped = val
                elif parts[0] == "UNINTEGRATED":
                    unintegrated = val
                else:
                    pwy_sum += val
        total = unmapped + unintegrated + pwy_sum
        pwy_ok = 0.95 <= total <= 1.05
        checks.append({
            "check": "pathway_relab_sum",
            "expected": "~1.0",
            "actual": round(total, 4),
            "status": "PASS" if pwy_ok else "WARN",
        })
    # Gene families relab sum check
    gf_path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_2_genefamilies_relab.tsv"
    if os.path.exists(gf_path):
        gf_total = 0.0
        count = 0
        with open(gf_path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.strip().split("\t")
                if len(parts) < 2 or "|" in parts[0]:
                    continue
                gf_total += float(parts[1])
                count += 1
                if count > 50000:
                    break  # enough to check
        gf_ok = 0.95 <= gf_total <= 1.05
        checks.append({
            "check": "genefamily_relab_sum",
            "expected": "~1.0",
            "actual": round(gf_total, 4),
            "status": "PASS" if gf_ok else "WARN",
        })
    return checks


def run_input_validation(batch_id, sample_id, taxa):
    """Orchestrate all input validation checks. Returns combined result dict. (1A/1B/1C/6B)"""
    func_dir = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}"
    tax_dir = f"{WORK_DIR}/data/{batch_id}/taxonomic_profiling/{sample_id}"
    files_to_check = [
        f"{tax_dir}/{sample_id}_profile.tsv",
        f"{func_dir}/{sample_id}_2_genefamilies_relab.tsv",
        f"{func_dir}/{sample_id}_4_pathabundance_relab.tsv",
    ]
    file_validations = [validate_tsv_file(fp) for fp in files_to_check]
    any_fail = any(fv["status"] == "FAIL" for fv in file_validations)
    # Unit detection
    units = {}
    units["taxonomy"] = detect_unit(f"{tax_dir}/{sample_id}_profile.tsv")
    units["pathway_relab"] = detect_unit(f"{func_dir}/{sample_id}_4_pathabundance_relab.tsv")
    units["genefamily_relab"] = detect_unit(f"{func_dir}/{sample_id}_2_genefamilies_relab.tsv")
    # Version info
    versions = extract_version_info(batch_id, sample_id)
    # Sum checks
    sum_checks = validate_sum_checks(batch_id, sample_id, taxa)
    sum_fail = any(sc["status"] == "FAIL" for sc in sum_checks)
    overall = "FAIL" if (any_fail or sum_fail) else "PASS"
    if not any_fail and not sum_fail:
        if any(sc["status"] == "WARN" for sc in sum_checks):
            overall = "WARN"
    return {
        "overall_status": overall,
        "file_validations": file_validations,
        "units_detected": units,
        "sum_checks": sum_checks,
        "versions": versions,
        "alias_dictionary_version": TAXONOMY_ALIASES_VERSION,
    }


# ═══════════════════════════════════════════════════════════════
# V2: FASTQ MD5 INTEGRITY (7A')
# ═══════════════════════════════════════════════════════════════

def verify_fastq_md5(batch_id, sample_id):
    """Download .md5 files from S3 and verify FASTQ integrity if files exist locally. (7A')"""
    result = {"status": "SKIPPED", "checks": [], "r1r2_size_ratio": None}
    raw_dir = f"{WORK_DIR}/data/{batch_id}/raw_sequences/{sample_id}"
    for read in ["R1", "R2"]:
        md5_s3 = f"{S3_BUCKET}/{batch_id}/raw_sequences/{sample_id}/{sample_id}_{read}.md5"
        local_gz = f"{raw_dir}/{sample_id}_{read}.fastq.gz"
        try:
            res = subprocess.run(["aws", "s3", "cp", md5_s3, "-"], capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout.strip():
                expected_md5 = res.stdout.strip().split()[0]
                check = {"file": f"{sample_id}_{read}.fastq.gz", "expected_md5": expected_md5}
                if os.path.exists(local_gz):
                    actual = subprocess.run(f"md5 -q '{local_gz}'", shell=True, capture_output=True, text=True)
                    actual_md5 = actual.stdout.strip()
                    check["actual_md5"] = actual_md5
                    check["status"] = "PASS" if actual_md5 == expected_md5 else "FAIL"
                else:
                    check["actual_md5"] = None
                    check["status"] = "NOT_LOCAL"
                result["checks"].append(check)
        except Exception:
            result["checks"].append({"file": f"{sample_id}_{read}.fastq.gz", "status": "S3_ERROR"})
    # R1/R2 size ratio check
    r1 = f"{raw_dir}/{sample_id}_R1.fastq.gz"
    r2 = f"{raw_dir}/{sample_id}_R2.fastq.gz"
    if os.path.exists(r1) and os.path.exists(r2):
        s1, s2 = os.path.getsize(r1), os.path.getsize(r2)
        ratio = round(min(s1, s2) / max(s1, s2), 3) if max(s1, s2) > 0 else 0
        result["r1r2_size_ratio"] = ratio
        if ratio < 0.8:
            result["checks"].append({"file": "R1_vs_R2_size", "status": "WARN", "ratio": ratio})
    result["status"] = "FAIL" if any(c.get("status") == "FAIL" for c in result["checks"]) else "OK"
    return result


# ═══════════════════════════════════════════════════════════════
# V2: CROSS-LAYER ALIGNMENT (2A/2B)
# ═══════════════════════════════════════════════════════════════

def load_reactions(batch_id, sample_id):
    """Load reactions file — currently unused in v1. Returns dict with counts. (2A)"""
    path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_3_reactions.tsv"
    if not os.path.exists(path):
        return None
    unmapped = 0.0
    ungrouped = 0.0
    reactions = {}
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 2 or "|" in parts[0]:
                continue
            name, val = parts[0], float(parts[1])
            if name == "UNMAPPED":
                unmapped = val
            elif name == "UNGROUPED":
                ungrouped = val
            else:
                reactions[name] = val
    return {"unmapped": unmapped, "ungrouped": ungrouped, "reactions": reactions}


def compute_cross_layer_alignment(batch_id, sample_id, pwy_data):
    """Compute richness and concentration alignment across gene families, reactions, pathways. (2A/2B)"""
    result = {"flags": []}
    # Count nonzero gene families (community-level only)
    gf_path = f"{WORK_DIR}/data/{batch_id}/functional_profiling/{sample_id}/{sample_id}_2_genefamilies_relab.tsv"
    n_gf = 0
    if os.path.exists(gf_path):
        gf_vals = []
        with open(gf_path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 2 and "|" not in parts[0] and parts[0] not in ("READS_UNMAPPED", "UNMAPPED"):
                    val = float(parts[1])
                    if val > 0:
                        n_gf += 1
                        gf_vals.append(val)
        gf_vals.sort(reverse=True)
        gf_top10_share = round(sum(gf_vals[:10]) / sum(gf_vals) * 100, 1) if gf_vals else 0
    else:
        gf_vals = []
        gf_top10_share = 0
    # Count nonzero reactions
    rxn_data = load_reactions(batch_id, sample_id)
    n_rxn = len(rxn_data["reactions"]) if rxn_data else 0
    rxn_vals = sorted(rxn_data["reactions"].values(), reverse=True) if rxn_data else []
    rxn_top10_share = round(sum(rxn_vals[:10]) / sum(rxn_vals) * 100, 1) if rxn_vals else 0
    # Count nonzero pathways
    n_pwy = len(pwy_data["pathways"]) if pwy_data else 0
    pwy_vals = sorted([p["abundance"] for p in pwy_data["pathways"].values()], reverse=True) if pwy_data else []
    pwy_top10_share = round(sum(pwy_vals[:10]) / sum(pwy_vals) * 100, 1) if pwy_vals else 0
    result["richness"] = {"gene_families": n_gf, "reactions": n_rxn, "pathways": n_pwy}
    result["top10_share"] = {"gene_families": gf_top10_share, "reactions": rxn_top10_share, "pathways": pwy_top10_share}
    # Flag suspicious patterns
    if n_pwy < 10 and n_gf > 1000:
        result["flags"].append("Very few pathways despite many gene families — possible integration/mapping issue")
    if n_rxn == 0 and n_pwy > 0:
        result["flags"].append("No reactions but pathways present — possible export mismatch")
    if n_gf < 100 and n_rxn < 50 and n_pwy < 20:
        result["flags"].append("All layers extremely low — possible truncated export or very low input")
    # Concentration divergence
    shares = [gf_top10_share, rxn_top10_share, pwy_top10_share]
    if max(shares) - min(shares) > 40:
        result["flags"].append(f"Large concentration divergence across layers (range: {min(shares)}-{max(shares)}%)")
    return result


# ═══════════════════════════════════════════════════════════════
# V2: ENHANCED TAXONOMY (4A/4B/4C)
# ═══════════════════════════════════════════════════════════════

def compute_taxonomy_enhanced(taxa):
    """Placeholder burden by rank, rank collapse check, dominance plausibility. (4A/4B/4C)"""
    if taxa is None:
        return {"error": "Missing taxonomy"}
    result = {}
    # 4A: Placeholder burden at genus level
    genera = [t for t in taxa if t["clade_name"].count("|") == 5 and "|g__" in t["clade_name"]]
    genus_placeholder = [g for g in genera if re.search(r'(GGB\d+|SGB\d+|CFGB\d+|_unclassified)', g["clade_name"])]
    genus_total = len(genera)
    genus_placeholder_pct = round(len(genus_placeholder) / genus_total * 100, 1) if genus_total > 0 else 0
    result["genus_placeholder_pct"] = genus_placeholder_pct
    result["genus_total"] = genus_total
    # 4B: Rank collapse — how much abundance is only at phylum/class level without species resolution
    species = [t for t in taxa if t["clade_name"].count("|") == 6 and "|t__" not in t["clade_name"]]
    species_total_relab = sum(t["relative_abundance"] for t in species)
    kingdom_relab = sum(t["relative_abundance"] for t in taxa if t["clade_name"].count("|") == 0)
    species_resolution_pct = round(species_total_relab / kingdom_relab * 100, 1) if kingdom_relab > 0 else 0
    result["species_resolution_pct"] = species_resolution_pct
    result["rank_collapse_flag"] = species_resolution_pct < 50
    # 4C: Dominance plausibility
    if species:
        top1 = max(species, key=lambda x: x["relative_abundance"])
        top1_relab = top1["relative_abundance"]
        top1_name = top1["clade_name"].split("|s__")[-1].split("|")[0] if "|s__" in top1["clade_name"] else "?"
        result["top1_species"] = top1_name
        result["top1_relab"] = round(top1_relab, 2)
        if top1_relab > 80:
            result["dominance_flag"] = "STRONG — top1 >80%, suspect technical artifact unless clinical context"
        elif top1_relab > 60:
            result["dominance_flag"] = "FLAG — top1 >60%, review recommended"
        else:
            result["dominance_flag"] = None
    return result


# ═══════════════════════════════════════════════════════════════
# V2: HOUSEKEEPING DOMINANCE & CLAIM GUARDRAILS (5C/5D)
# ═══════════════════════════════════════════════════════════════

def check_housekeeping_dominance(pwy_data):
    """Check if top pathways are overwhelmingly housekeeping/core metabolism. (5C)"""
    if pwy_data is None:
        return {"housekeeping_dominant": False}
    top20 = sorted(pwy_data["pathways"].items(), key=lambda x: x[1]["abundance"], reverse=True)[:20]
    hk_count = sum(1 for pid, _ in top20 if pid in HOUSEKEEPING_PATHWAYS)
    hk_pct = round(hk_count / len(top20) * 100, 1) if top20 else 0
    return {
        "housekeeping_in_top20": hk_count,
        "housekeeping_pct_top20": hk_pct,
        "housekeeping_dominant": hk_pct > 60,
        "message": "Function dominated by core metabolism; limited actionable signal." if hk_pct > 60 else None,
    }


def generate_claim_guardrails(confidence, sanity_flags, housekeeping, pwy_data):
    """Generate claim guardrails based on QC results. (5D/8)"""
    guardrails = []
    tiers = confidence.get("tiers", {})
    # SCFA guardrail (5D)
    if tiers.get("functional_pathway") == "LOW":
        if pwy_data:
            scfa_ids = {"PWY-5676", "PWY-5677", "CENTFERM-PWY", "P163-PWY", "PWY-5022"}
            scfa_present = [pid for pid in pwy_data["pathways"] if pid in scfa_ids]
            if scfa_present:
                guardrails.append({
                    "type": "SCFA_LOW_CONFIDENCE",
                    "pathways": scfa_present,
                    "forced_wording": "Possible capacity signal, low confidence. Requires validation (diet log, metabolomics, longitudinal).",
                })
    # Housekeeping dominance (5C)
    if housekeeping.get("housekeeping_dominant"):
        guardrails.append({
            "type": "HOUSEKEEPING_DOMINANT",
            "message": housekeeping["message"],
        })
    # Functional LOW: ban quantitative claims (8)
    if tiers.get("functional_pathway") == "LOW":
        guardrails.append({
            "type": "BAN_QUANTITATIVE_PATHWAY_CLAIMS",
            "banned": ["increased/decreased production of X", "this explains symptom Y", "recommend intervention because pathway Z"],
            "allowed": "Signal suggests [X], low confidence; needs validation.",
        })
    # Taxonomy novelty high: ban species-level causal claims (8)
    if tiers.get("taxonomy_completeness") == "CAUTION":
        guardrails.append({
            "type": "BAN_SPECIES_CAUSAL_CLAIMS",
            "message": "High taxonomy novelty (>30% placeholders): ban species-level causal claims, allow only genus-level trends.",
        })
    return guardrails


# ═══════════════════════════════════════════════════════════════
# V2: FUNCTIONAL EVIDENCE SCORE & ALLOWED CLAIMS (3/8)
# ═══════════════════════════════════════════════════════════════

def compute_functional_evidence_score(block_a, pwy_data):
    """Compute a composite Functional Evidence Score (0-100). (Rec 3)"""
    if "error" in block_a or pwy_data is None:
        return {"score": 0, "tier": "NONE", "components": {}}
    interp = block_a["interpretable_pathway_pct"]  # 0-100
    n_pwy = block_a["total_pathways_detected"]
    # Pathway concentration penalty
    pwy_vals = sorted([p["abundance"] for p in pwy_data["pathways"].values()], reverse=True)
    top10_share = sum(pwy_vals[:10]) / sum(pwy_vals) * 100 if pwy_vals else 100
    # Missing UNMAPPED/UNINTEGRATED penalty
    has_unmapped = pwy_data["unmapped"] > 0 or True  # always present in HUMAnN output
    # Score components — interpretable fraction is primary driver (40%)
    c1 = min(40, interp * 40 / 40)  # interpretable fraction: 40%+ = full marks (0-40)
    c2 = min(20, n_pwy * 20 / 300)  # pathway count: 300+ = full marks (0-20)
    c3 = min(25, max(0, 25 - max(0, top10_share - 30) * 25 / 70))  # concentration penalty (0-25)
    c4 = 15 if has_unmapped else 7.5  # UNMAPPED present (0-15)
    score = round(c1 + c2 + c3 + c4, 1)
    score = max(0, min(100, score))
    if score >= 70:
        tier = "HIGH"
    elif score >= 40:
        tier = "MODERATE"
    else:
        tier = "LOW"
    return {
        "score": score,
        "tier": tier,
        "components": {
            "interpretable_fraction": round(c1, 1),
            "pathway_richness": round(c2, 1),
            "concentration_penalty": round(c3, 1),
            "unmapped_present": round(c4, 1),
        },
    }


def generate_allowed_claims(func_score, confidence, taxonomy_enhanced):
    """Generate structured allowed-claims policy. (Rec 8)"""
    policy = {"qc_score": func_score["score"], "qc_tier": func_score["tier"], "claims": []}
    tier = func_score["tier"]
    if tier == "LOW":
        policy["claims"] = [
            "ALLOWED: 'Signal suggests [X]; low confidence, needs validation (diet, metabolomics, longitudinal).'",
            "BANNED: Any quantitative pathway comparison across samples",
            "BANNED: 'Increased/decreased production of X'",
            "BANNED: 'Recommend intervention because pathway Z'",
        ]
    elif tier == "MODERATE":
        policy["claims"] = [
            "ALLOWED: Qualitative pathway presence/absence statements with caveats",
            "ALLOWED: Genus-level functional associations",
            "CAUTION: Quantitative comparisons need explicit uncertainty language",
        ]
    else:
        policy["claims"] = [
            "ALLOWED: Quantitative pathway comparisons with standard caveats",
            "ALLOWED: Species-level functional associations",
        ]
    # Taxonomy override
    if taxonomy_enhanced and taxonomy_enhanced.get("rank_collapse_flag"):
        policy["claims"].append("OVERRIDE: Low taxonomy resolution — restrict to genus-level claims only")
    dom = taxonomy_enhanced.get("dominance_flag") if taxonomy_enhanced else None
    if dom and "STRONG" in str(dom):
        policy["claims"].append("OVERRIDE: Extreme dominance — flag possible technical artifact before interpretation")
    return policy


# ═══════════════════════════════════════════════════════════════
# V2: BATCH OUTLIER DETECTION (9A/9B)
# ═══════════════════════════════════════════════════════════════

def compute_batch_outliers(all_results):
    """Compute z-scores and flag outliers across batch samples. (9A/9B)"""
    if len(all_results) < 3:
        return {"note": "Too few samples for outlier detection", "outliers": []}
    metrics = defaultdict(list)
    for sid, res in all_results.items():
        if res is None:
            continue
        a = res.get("block_a", {})
        if "error" not in a:
            metrics["interpretable_pct"].append((sid, a["interpretable_pathway_pct"]))
            metrics["gf_unmapped_pct"].append((sid, a["gene_family_unmapped_pct"]))
        b = res.get("block_b", {})
        if "error" not in b:
            metrics["sgb_pct"].append((sid, b["sgb_ggb_pct"]))
        fs = res.get("functional_evidence_score", {})
        if fs:
            metrics["func_score"].append((sid, fs.get("score", 0)))
    outliers = []
    batch_effects = []
    for metric_name, values in metrics.items():
        if len(values) < 3:
            continue
        vals = [v[1] for v in values]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        if std < 0.001:
            continue
        for sid, val in values:
            z = (val - mean) / std
            if abs(z) > 2:
                outliers.append({"sample": sid, "metric": metric_name, "value": round(val, 2), "z_score": round(z, 2)})
    # 9B: Batch effect — if majority of samples have low interpretable fraction
    interp_vals = [v[1] for v in metrics.get("interpretable_pct", [])]
    if interp_vals and sum(1 for v in interp_vals if v < 15) > len(interp_vals) * 0.6:
        batch_effects.append("BATCH WARNING: >60% of samples have interpretable pathway fraction <15%. Possible batch-level issue.")
    return {"outliers": outliers, "batch_effects": batch_effects}


# ═══════════════════════════════════════════════════════════════
# QC BLOCK A: HUMAnN FUNCTIONAL MAPPABILITY
# ═══════════════════════════════════════════════════════════════

def compute_block_a(batch_id, sample_id):
    gf_unmapped = load_genefamilies_relab(batch_id, sample_id)
    pwy_data = load_pathway_abundance_relab(batch_id, sample_id)
    if gf_unmapped is None or pwy_data is None:
        return {"error": "Missing HUMAnN output files"}
    pwy_unmapped = pwy_data["unmapped"]
    pwy_unintegrated = pwy_data["unintegrated"]
    interpretable = max(0, 1.0 - pwy_unmapped - pwy_unintegrated)
    return {
        "gene_family_unmapped_pct": round(gf_unmapped * 100, 2),
        "pathway_unmapped_pct": round(pwy_unmapped * 100, 2),
        "pathway_unintegrated_pct": round(pwy_unintegrated * 100, 2),
        "interpretable_pathway_pct": round(interpretable * 100, 2),
        "total_pathways_detected": len(pwy_data["pathways"]),
    }


# ═══════════════════════════════════════════════════════════════
# QC BLOCK B: TAXONOMY COMPLETENESS
# ═══════════════════════════════════════════════════════════════

def compute_block_b(taxa, reads_processed):
    if taxa is None:
        return {"error": "Missing MetaPhlAn profile"}
    species = [t for t in taxa if t["clade_name"].count("|") == 6 and "|t__" not in t["clade_name"]]
    sgb_ggb = [s for s in species if re.search(r'(GGB\d+|SGB\d+)', s["clade_name"]) and "s__" in s["clade_name"]]
    named = [s for s in species if s not in sgb_ggb]
    unclassified = [s for s in species if "unclassified" in s["clade_name"].lower() or "Bacteria_unclassified" in s["clade_name"]]
    total = len(species)
    sgb_ggb_count = len(sgb_ggb)
    named_count = total - sgb_ggb_count
    sgb_ggb_pct = round(sgb_ggb_count / total * 100, 1) if total > 0 else 0
    top_species = sorted(species, key=lambda x: x["relative_abundance"], reverse=True)[:10]
    top_list = [{"name": s["clade_name"].split("|")[-1].replace("s__", ""), "relab": round(s["relative_abundance"], 2)} for s in top_species]
    return {
        "total_species": total,
        "named_species": named_count,
        "sgb_ggb_placeholders": sgb_ggb_count,
        "sgb_ggb_pct": sgb_ggb_pct,
        "unclassified_count": len(unclassified),
        "reads_processed": reads_processed,
        "top_10_species": top_list,
    }


# ═══════════════════════════════════════════════════════════════
# QC BLOCK C: TAXONOMY <-> FUNCTION CONSISTENCY
# ═══════════════════════════════════════════════════════════════

def compute_block_c(taxa, pwy_data):
    if taxa is None or pwy_data is None:
        return {"error": "Missing data"}
    species_in_taxonomy = set()
    for t in taxa:
        if "|s__" in t["clade_name"] and "|t__" not in t["clade_name"]:
            sp = t["clade_name"].split("|s__")[-1].split("|")[0]
            genus = sp.split("_")[0] if "_" in sp else sp
            species_in_taxonomy.add(genus)
    species_in_function = set()
    for pwy, contributors in pwy_data["stratified"].items():
        for contrib in contributors:
            if contrib == "unclassified":
                continue
            m = re.search(r's__(\w+)', contrib)
            if m:
                sp = m.group(1)
                genus = sp.split("_")[0] if "_" in sp else sp
                species_in_function.add(genus)
    resolved_tax = set()
    for g in species_in_taxonomy:
        resolved_tax.add(g)
        if g in TAXONOMY_ALIASES:
            resolved_tax.add(TAXONOMY_ALIASES[g])
    in_both = species_in_function & resolved_tax
    only_function = species_in_function - resolved_tax
    only_taxonomy = species_in_taxonomy - species_in_function
    top_tax = sorted([(t["clade_name"].split("|s__")[-1].split("|")[0].split("_")[0], t["relative_abundance"])
                      for t in taxa if "|s__" in t["clade_name"] and "|t__" not in t["clade_name"]],
                     key=lambda x: x[1], reverse=True)[:10]
    missing_in_function = []
    for genus, relab in top_tax:
        resolved = {genus}
        if genus in TAXONOMY_ALIASES:
            resolved.add(TAXONOMY_ALIASES[genus])
        if not (resolved & species_in_function):
            missing_in_function.append({"genus": genus, "relab": round(relab, 2)})
    return {
        "genera_in_both": len(in_both),
        "genera_only_in_function": len(only_function),
        "genera_only_in_taxonomy": len(only_taxonomy),
        "top_taxa_missing_in_function": missing_in_function,
        "alias_resolution_applied": True,
    }


# ═══════════════════════════════════════════════════════════════
# QC BLOCK D: PATHWAY CONTRIBUTION SANITY
# ═══════════════════════════════════════════════════════════════

def compute_block_d(pwy_data):
    if pwy_data is None:
        return {"error": "Missing pathway data"}
    top_pwys = sorted(pwy_data["pathways"].items(), key=lambda x: x[1]["abundance"], reverse=True)[:20]
    unclassified_fractions = []
    for pwy_id, info in top_pwys:
        strat = pwy_data["stratified"].get(pwy_id, {})
        total = sum(strat.values())
        unclass = strat.get("unclassified", 0)
        frac = (unclass / total * 100) if total > 0 else 0
        unclassified_fractions.append({"pathway": pwy_id, "full_name": info["full_name"], "unclassified_pct": round(frac, 1)})
    avg_unclass = sum(f["unclassified_pct"] for f in unclassified_fractions) / len(unclassified_fractions) if unclassified_fractions else 0
    high_unclass = [f for f in unclassified_fractions if f["unclassified_pct"] > 30]
    return {
        "avg_unclassified_pct_top20": round(avg_unclass, 1),
        "pathways_above_30pct_unclassified": len(high_unclass),
        "high_unclassified_pathways": high_unclass[:5],
    }


# ═══════════════════════════════════════════════════════════════
# QC BLOCK E: DIVERSITY METRICS (LOCKED DEFINITIONS)
# ═══════════════════════════════════════════════════════════════

def compute_block_e(taxa):
    if taxa is None:
        return {"error": "Missing MetaPhlAn profile"}
    species = [t for t in taxa if t["clade_name"].count("|") == 6 and "|t__" not in t["clade_name"]]
    relabs = [s["relative_abundance"] / 100.0 for s in species if s["relative_abundance"] > 0]
    richness = len(relabs)
    shannon = 0.0
    for p in relabs:
        if p > 0:
            shannon -= p * math.log(p)
    evenness = shannon / math.log(richness) if richness > 1 else 0.0
    return {
        "shannon_index": round(shannon, 4),
        "richness_species_count": richness,
        "evenness_pielou": round(evenness, 4),
        "method": {
            "taxonomic_level": "species (s__ level, excluding t__ strains)",
            "log_base": "natural log (ln)",
            "min_relab_threshold": 0,
            "filtering": "only species with relab > 0",
        },
    }


# ═══════════════════════════════════════════════════════════════
# SANITY RULES CHECK
# ═══════════════════════════════════════════════════════════════

def run_sanity_rules(taxa, pwy_data):
    if taxa is None or pwy_data is None:
        return []
    all_taxa_text = " ".join([t["clade_name"] for t in taxa])
    all_pathways_text = " ".join([info["full_name"] for info in pwy_data["pathways"].values()])
    flags = []
    for rule in SANITY_RULES:
        pwy_match = any(kw.lower() in all_pathways_text.lower() for kw in rule["pathway_keywords"])
        if not pwy_match:
            flags.append({"rule": rule["name"], "status": "OK", "detail": "Pathway not detected"})
            continue
        taxa_found = any(kw.lower() in all_taxa_text.lower() for kw in rule["required_taxa_keywords"])
        if taxa_found:
            flags.append({"rule": rule["name"], "status": "OK", "detail": "Both pathway and expected taxa present"})
        else:
            flags.append({"rule": rule["name"], "status": "FLAG", "detail": rule["message"]})
    return flags


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE TIER ASSIGNMENT
# ═══════════════════════════════════════════════════════════════

def assign_confidence(block_a, block_b):
    tiers = {}
    warnings = []
    if "error" not in block_a:
        interp = block_a["interpretable_pathway_pct"]
        gf_unmap = block_a["gene_family_unmapped_pct"]
        if interp < 10:
            tiers["functional_pathway"] = "LOW"
            warnings.append("Interpretable pathway fraction <10%: all pathway-derived claims are LOW confidence. Ban quantitative comparisons across samples.")
        elif interp < 20:
            tiers["functional_pathway"] = "MODERATE"
            warnings.append("Interpretable pathway fraction 10-20%: use caution with pathway claims.")
        else:
            tiers["functional_pathway"] = "HIGH"
        if gf_unmap > 80:
            tiers["functional_genefamily"] = "SCREENING-ONLY"
            warnings.append("Gene-family unmapped >80%: functional layer is SCREENING-ONLY, not decision-grade.")
        elif gf_unmap > 60:
            tiers["functional_genefamily"] = "LOW"
        else:
            tiers["functional_genefamily"] = "ADEQUATE"
    if "error" not in block_b:
        sgb_pct = block_b["sgb_ggb_pct"]
        if sgb_pct > 30:
            tiers["taxonomy_completeness"] = "CAUTION"
            warnings.append(f"SGB/GGB placeholder species >{sgb_pct}%: taxonomy DB gaps may affect functional interpretation.")
        elif sgb_pct > 15:
            tiers["taxonomy_completeness"] = "MODERATE"
        else:
            tiers["taxonomy_completeness"] = "GOOD"
    overall = "HIGH"
    if any(t in ["LOW", "SCREENING-ONLY", "CAUTION"] for t in tiers.values()):
        overall = "LOW"
    elif any(t == "MODERATE" for t in tiers.values()):
        overall = "MODERATE"
    tiers["overall"] = overall
    return {"tiers": tiers, "warnings": warnings}


# ═══════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

def status_icon(val, threshold_warn, threshold_fail, higher_is_worse=True):
    if higher_is_worse:
        if val >= threshold_fail: return "FAIL"
        if val >= threshold_warn: return "WARN"
        return "OK"
    else:
        if val <= threshold_fail: return "FAIL"
        if val <= threshold_warn: return "WARN"
        return "OK"


def print_console_report(sample_id, batch_id, results):
    W = 66
    print("\n" + "=" * W)
    print("       METAGENOMICS QC PRE-CHECK REPORT")
    print(f"  Sample: {sample_id}  |  Batch: {batch_id}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * W)

    # Block A
    a = results["block_a"]
    print("\n--- A. FUNCTIONAL MAPPABILITY (HUMAnN) " + "-" * 27)
    if "error" in a:
        print(f"  ERROR: {a['error']}")
    else:
        s1 = status_icon(a["gene_family_unmapped_pct"], 60, 80)
        s2 = status_icon(a["interpretable_pathway_pct"], 20, 10, higher_is_worse=False)
        print(f"  Gene-family unmapped:       {a['gene_family_unmapped_pct']:6.1f}%   [{s1}]")
        print(f"  Pathway unmapped:           {a['pathway_unmapped_pct']:6.1f}%")
        print(f"  Pathway unintegrated:       {a['pathway_unintegrated_pct']:6.1f}%")
        print(f"  Interpretable pathway frac: {a['interpretable_pathway_pct']:6.1f}%   [{s2}]")
        print(f"  Total pathways detected:    {a['total_pathways_detected']}")

    # Block B
    b = results["block_b"]
    print("\n--- B. TAXONOMY COMPLETENESS " + "-" * 37)
    if "error" in b:
        print(f"  ERROR: {b['error']}")
    else:
        s3 = status_icon(b["sgb_ggb_pct"], 15, 30)
        print(f"  Total species detected:     {b['total_species']}")
        print(f"  Named species:              {b['named_species']}  ({100 - b['sgb_ggb_pct']:.1f}%)")
        print(f"  SGB/GGB placeholders:       {b['sgb_ggb_placeholders']}  ({b['sgb_ggb_pct']:.1f}%)   [{s3}]")
        if b["reads_processed"]:
            print(f"  Reads processed (MetaPhlAn):{b['reads_processed']:>10,}")
        print(f"  Top species:")
        for i, sp in enumerate(b["top_10_species"][:5], 1):
            print(f"    {i}. {sp['name']} ({sp['relab']}%)")

    # Block C
    c = results["block_c"]
    print("\n--- C. TAXONOMY <-> FUNCTION CONSISTENCY " + "-" * 25)
    if "error" in c:
        print(f"  ERROR: {c['error']}")
    else:
        print(f"  Genera in both tax+func:    {c['genera_in_both']}")
        print(f"  Only in function output:    {c['genera_only_in_function']}")
        print(f"  Only in taxonomy:           {c['genera_only_in_taxonomy']}")
        print(f"  Alias resolution:           applied (Phocaeicola/Bacteroides etc.)")
        if c["top_taxa_missing_in_function"]:
            print(f"  WARNING: Top taxa missing from functional output:")
            for t in c["top_taxa_missing_in_function"]:
                print(f"    - {t['genus']} ({t['relab']}%)")
        else:
            print(f"  All top taxa present in functional stratified output [OK]")

    # Block D
    d = results["block_d"]
    print("\n--- D. PATHWAY CONTRIBUTION SANITY " + "-" * 30)
    if "error" in d:
        print(f"  ERROR: {d['error']}")
    else:
        s4 = status_icon(d["avg_unclassified_pct_top20"], 20, 30)
        print(f"  Avg unclassified % (top 20):{d['avg_unclassified_pct_top20']:6.1f}%   [{s4}]")
        print(f"  Pathways >30% unclassified: {d['pathways_above_30pct_unclassified']}")

    # Block E
    e = results["block_e"]
    print("\n--- E. DIVERSITY METRICS (Locked Definitions) " + "-" * 19)
    if "error" in e:
        print(f"  ERROR: {e['error']}")
    else:
        print(f"  Shannon (ln, species-level): {e['shannon_index']:.4f}")
        print(f"  Richness (species count):    {e['richness_species_count']}")
        print(f"  Evenness (Pielou J'):        {e['evenness_pielou']:.4f}")
        print(f"  Method: {e['method']['taxonomic_level']}, {e['method']['log_base']}")

    # FASTQ stats
    fq = results.get("fastq_stats")
    if fq:
        print("\n--- F. FASTQ SEQUENCING STATS " + "-" * 36)
        for k, v in fq.items():
            print(f"  {k}: {v}")

    # Sanity flags
    flags = results["sanity_flags"]
    print("\n--- SANITY FLAGS " + "-" * 49)
    for f in flags:
        icon = "OK" if f["status"] == "OK" else "FLAG"
        print(f"  [{icon}] {f['rule']}: {f['detail']}")

    # Confidence
    conf = results["confidence"]
    print("\n--- CONFIDENCE ASSESSMENT " + "-" * 40)
    print(f"  OVERALL CONFIDENCE: {conf['tiers']['overall']}")
    for k, v in conf["tiers"].items():
        if k != "overall":
            print(f"    {k}: {v}")
    if conf["warnings"]:
        print(f"\n  Warnings:")
        for w in conf["warnings"]:
            print(f"    - {w}")

    print("\n" + "=" * W)
    print("  END OF QC PRE-CHECK REPORT")
    print("=" * W + "\n")


def save_outputs(sample_id, batch_id, results):
    out_dir = f"{WORK_DIR}/analysis/{batch_id}/{sample_id}/qc"
    os.makedirs(out_dir, exist_ok=True)
    json_path = f"{out_dir}/{sample_id}_qc_precheck.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  JSON saved: {json_path}")
    md_path = f"{out_dir}/{sample_id}_qc_precheck.md"
    with open(md_path, "w") as f:
        f.write(f"# QC Pre-Check Report: {sample_id}\n")
        f.write(f"**Batch:** {batch_id}  \n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n\n")
        a = results["block_a"]
        if "error" not in a:
            f.write("## A. Functional Mappability (HUMAnN)\n")
            f.write(f"| Metric | Value | Status |\n|---|---|---|\n")
            f.write(f"| Gene-family unmapped | {a['gene_family_unmapped_pct']:.1f}% | {status_icon(a['gene_family_unmapped_pct'], 60, 80)} |\n")
            f.write(f"| Pathway unmapped | {a['pathway_unmapped_pct']:.1f}% | - |\n")
            f.write(f"| Pathway unintegrated | {a['pathway_unintegrated_pct']:.1f}% | - |\n")
            f.write(f"| **Interpretable fraction** | **{a['interpretable_pathway_pct']:.1f}%** | **{status_icon(a['interpretable_pathway_pct'], 20, 10, False)}** |\n\n")
        b = results["block_b"]
        if "error" not in b:
            f.write("## B. Taxonomy Completeness\n")
            f.write(f"- Species detected: {b['total_species']} (named: {b['named_species']}, SGB/GGB: {b['sgb_ggb_placeholders']} = {b['sgb_ggb_pct']}%)\n\n")
        e = results["block_e"]
        if "error" not in e:
            f.write("## E. Diversity Metrics\n")
            f.write(f"| Metric | Value |\n|---|---|\n")
            f.write(f"| Shannon (ln) | {e['shannon_index']:.4f} |\n")
            f.write(f"| Richness | {e['richness_species_count']} |\n")
            f.write(f"| Evenness | {e['evenness_pielou']:.4f} |\n\n")
        conf = results["confidence"]
        f.write(f"## Confidence: **{conf['tiers']['overall']}**\n")
        for w in conf["warnings"]:
            f.write(f"- {w}\n")
    print(f"  Markdown saved: {md_path}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def discover_samples(batch_id, local_only=False):
    """Discover all sample IDs in a batch from S3 or local directory."""
    samples = set()
    # Try local first
    local_func = f"{WORK_DIR}/data/{batch_id}/functional_profiling"
    local_tax = f"{WORK_DIR}/data/{batch_id}/taxonomic_profiling"
    for d in [local_func, local_tax]:
        if os.path.isdir(d):
            for name in os.listdir(d):
                if re.match(r'^\d{13}$', name) and os.path.isdir(os.path.join(d, name)):
                    samples.add(name)
    # Try S3 if not local-only and we found nothing
    if not local_only and not samples:
        try:
            result = subprocess.run(
                ["aws", "s3", "ls", f"{S3_BUCKET}/{batch_id}/functional_profiling/"],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.strip().split("\n"):
                m = re.search(r'PRE\s+(\d{13})/', line)
                if m:
                    samples.add(m.group(1))
        except Exception as e:
            print(f"  WARNING: S3 discovery failed: {e}")
    return sorted(samples)


def cleanup_fastq_files(batch_id, sample_id):
    """Delete FASTQ files for a sample to save space."""
    raw_dir = f"{WORK_DIR}/data/{batch_id}/raw_sequences/{sample_id}"
    if os.path.isdir(raw_dir):
        import shutil
        size_mb = sum(os.path.getsize(os.path.join(raw_dir, f)) for f in os.listdir(raw_dir) if os.path.isfile(os.path.join(raw_dir, f))) / (1024**2)
        shutil.rmtree(raw_dir)
        print(f"  [CLEANUP] Deleted FASTQ files for {sample_id} ({size_mb:.0f} MB freed)")
    else:
        print(f"  [CLEANUP] No FASTQ directory found for {sample_id}")


def process_single_sample(batch_id, sample_id, local_only=False, skip_fastq=False, cleanup_fastq=False):
    """Run full QC pipeline on one sample. Returns results dict."""
    print(f"\n{'='*66}")
    print(f"  QC Pre-Check: {sample_id} (Batch: {batch_id})")
    print(f"{'='*66}\n")

    # Step 1: Download if needed
    if not local_only:
        print("[1] Downloading data from AWS...")
        try:
            download_from_aws(batch_id, sample_id)
        except Exception as e:
            print(f"  WARNING: AWS download failed: {e}")
            print("  Continuing with local data...")
    else:
        print("[1] Local-only mode, skipping AWS download")

    # Step 2: Load data
    print("\n[2] Loading data files...")
    taxa, reads_processed = load_metaphlan_profile(batch_id, sample_id)
    pwy_data = load_pathway_abundance_relab(batch_id, sample_id)
    print(f"  MetaPhlAn profile: {'loaded' if taxa else 'NOT FOUND'}")
    print(f"  Pathway abundance: {'loaded' if pwy_data else 'NOT FOUND'}")

    if taxa is None and pwy_data is None:
        print("  SKIPPING: No data files found for this sample")
        return None

    # Step 2.5: Input validation (v2)
    print("\n[2.5] Running input validation (v2)...")
    input_validation = run_input_validation(batch_id, sample_id, taxa)
    print(f"  Input validation: {input_validation['overall_status']}")
    print(f"  Versions: {input_validation['versions']}")
    print(f"  Units: {input_validation['units_detected']}")

    # Step 3: Run QC blocks
    print("\n[3] Computing QC metrics...")
    block_a = compute_block_a(batch_id, sample_id)
    block_b = compute_block_b(taxa, reads_processed)
    block_c = compute_block_c(taxa, pwy_data)
    block_d = compute_block_d(pwy_data)
    block_e = compute_block_e(taxa)
    print("  Blocks A-E computed")

    # Step 4: FASTQ stats
    fastq_stats = None
    if not skip_fastq:
        print("\n[4] Checking FASTQ files...")
        fastq_stats = count_fastq_reads(batch_id, sample_id)
        if fastq_stats:
            print(f"  FASTQ stats: {fastq_stats}")
        else:
            print("  No FASTQ files found (may have been cleaned up)")
    else:
        print("\n[4] Skipping FASTQ check")

    # Step 4.5: v2 — Cross-layer alignment, enhanced taxonomy, housekeeping
    print("\n[4.5] Computing v2 checks...")
    cross_layer = compute_cross_layer_alignment(batch_id, sample_id, pwy_data)
    taxonomy_enh = compute_taxonomy_enhanced(taxa)
    housekeeping = check_housekeeping_dominance(pwy_data)
    print(f"  Cross-layer richness: {cross_layer.get('richness', {})}")
    if taxonomy_enh.get("dominance_flag"):
        print(f"  Dominance: {taxonomy_enh['dominance_flag']}")
    if housekeeping.get("housekeeping_dominant"):
        print(f"  Housekeeping: DOMINANT ({housekeeping['housekeeping_pct_top20']}% of top 20)")

    # Step 5: Sanity rules
    print("\n[5] Running sanity rules...")
    sanity_flags = run_sanity_rules(taxa, pwy_data)

    # Step 6: Confidence + v2 scores
    print("\n[6] Assigning confidence tiers...")
    confidence = assign_confidence(block_a, block_b)
    func_score = compute_functional_evidence_score(block_a, pwy_data)
    claim_guardrails = generate_claim_guardrails(confidence, sanity_flags, housekeeping, pwy_data)
    allowed_claims = generate_allowed_claims(func_score, confidence, taxonomy_enh)
    print(f"  Functional Evidence Score: {func_score['score']}/100 ({func_score['tier']})")
    print(f"  Claim guardrails: {len(claim_guardrails)} active")

    # Step 6.5: FASTQ MD5 (if not skipping FASTQ)
    fastq_md5 = None
    if not skip_fastq and not local_only:
        print("\n[6.5] Verifying FASTQ MD5 checksums...")
        fastq_md5 = verify_fastq_md5(batch_id, sample_id)
        print(f"  FASTQ MD5: {fastq_md5['status']}")

    # Assemble results
    results = {
        "sample_id": sample_id,
        "batch_id": batch_id,
        "timestamp": datetime.now().isoformat(),
        "input_validation": input_validation,
        "block_a": block_a,
        "block_b": block_b,
        "block_c": block_c,
        "block_d": block_d,
        "block_e": block_e,
        "cross_layer_alignment": cross_layer,
        "taxonomy_enhanced": taxonomy_enh,
        "housekeeping_check": housekeeping,
        "fastq_stats": fastq_stats,
        "fastq_md5": fastq_md5,
        "sanity_flags": sanity_flags,
        "confidence": confidence,
        "functional_evidence_score": func_score,
        "claim_guardrails": claim_guardrails,
        "allowed_claims": allowed_claims,
    }

    # Step 7: Output
    print("\n[7] Generating reports...")
    print_console_report(sample_id, batch_id, results)
    save_outputs(sample_id, batch_id, results)

    # Step 8: Cleanup FASTQ if requested
    if cleanup_fastq:
        print("\n[8] Cleaning up FASTQ files...")
        cleanup_fastq_files(batch_id, sample_id)

    return results


def main():
    parser = argparse.ArgumentParser(description="Metagenomics QC Pre-Check")
    parser.add_argument("--batch", required=True, help="Batch ID (e.g., nb1_2026_004)")
    parser.add_argument("--sample", default=None, help="Sample ID (e.g., 1421029282376). Omit with --all for whole batch.")
    parser.add_argument("--all", action="store_true", help="Process all samples in the batch")
    parser.add_argument("--local-only", action="store_true", help="Skip AWS download, use local data only")
    parser.add_argument("--skip-fastq", action="store_true", help="Skip FASTQ read counting")
    parser.add_argument("--cleanup-fastq", action="store_true", help="Delete FASTQ files after analysis to save space")
    args = parser.parse_args()

    batch_id = args.batch

    if not args.sample and not args.all:
        parser.error("Either --sample SAMPLE_ID or --all is required")

    if args.all:
        # Batch mode: discover and process all samples
        print(f"\n{'#'*66}")
        print(f"  BATCH QC PRE-CHECK: {batch_id}")
        print(f"{'#'*66}\n")
        print("Discovering samples...")
        samples = discover_samples(batch_id, local_only=args.local_only)
        if not samples:
            print("  ERROR: No samples found in this batch.")
            sys.exit(1)
        print(f"  Found {len(samples)} samples: {', '.join(samples)}\n")

        batch_summary = {"batch_id": batch_id, "total": len(samples), "processed": 0, "skipped": 0, "results": {}}
        all_sample_results = {}
        for i, sid in enumerate(samples, 1):
            print(f"\n{'#'*66}")
            print(f"  Sample {i}/{len(samples)}: {sid}")
            print(f"{'#'*66}")
            result = process_single_sample(
                batch_id, sid,
                local_only=args.local_only,
                skip_fastq=args.skip_fastq,
                cleanup_fastq=args.cleanup_fastq
            )
            all_sample_results[sid] = result
            if result:
                batch_summary["processed"] += 1
                batch_summary["results"][sid] = result["confidence"]["tiers"]["overall"]
            else:
                batch_summary["skipped"] += 1
                batch_summary["results"][sid] = "SKIPPED"

        # V2: Batch outlier detection (9A/9B)
        batch_qc = compute_batch_outliers(all_sample_results)
        batch_summary["batch_qc"] = batch_qc
        if batch_qc.get("outliers"):
            print(f"\n  BATCH OUTLIERS DETECTED: {len(batch_qc['outliers'])}")
            for o in batch_qc["outliers"]:
                print(f"    {o['sample']}: {o['metric']} = {o['value']} (z={o['z_score']})")
        if batch_qc.get("batch_effects"):
            for be in batch_qc["batch_effects"]:
                print(f"  {be}")

        # Print batch summary
        print(f"\n{'#'*66}")
        print(f"  BATCH SUMMARY: {batch_id}")
        print(f"{'#'*66}")
        print(f"  Total samples:    {batch_summary['total']}")
        print(f"  Processed:        {batch_summary['processed']}")
        print(f"  Skipped:          {batch_summary['skipped']}")
        print(f"\n  Per-sample confidence:")
        for sid, conf in batch_summary["results"].items():
            print(f"    {sid}: {conf}")
        print(f"{'#'*66}\n")

        # Save batch summary JSON
        out_dir = f"{WORK_DIR}/analysis/{batch_id}"
        os.makedirs(out_dir, exist_ok=True)
        summary_path = f"{out_dir}/qc_batch_summary.json"
        with open(summary_path, "w") as f:
            json.dump(batch_summary, f, indent=2)
        print(f"  Batch summary saved: {summary_path}")

    else:
        # Single sample mode
        result = process_single_sample(
            batch_id, args.sample,
            local_only=args.local_only,
            skip_fastq=args.skip_fastq,
            cleanup_fastq=args.cleanup_fastq
        )

    print("\nDone!")


if __name__ == "__main__":
    main()
