"""
parse_metrics.py — Parse all metrics from pipeline output files

Reads:
  - {sample}_only_metrics.txt (compositional, diversity, guild, CLR, vitamin, dysbiosis)
  - {sample}_functional_guild.csv (species-level guild assignments, M. smithii abundance)
  - questionnaire_{sample}.json (sample metadata)

Returns a unified dictionary with all parsed values.
"""

import re
import os
import csv
import json
import glob


def parse_only_metrics(filepath: str) -> dict:
    """Parse all metrics from _only_metrics.txt file."""
    m = {}
    
    with open(filepath) as f:
        text = f.read()
    
    m['sample_id'] = os.path.basename(filepath).replace('_only_metrics.txt', '')
    
    # ── Section 1: Compositional Metrics ──
    scalar_patterns = [
        (r'GMWI2:\s*([\+\-]?[\d\.]+)', 'GMWI2'),
        (r'HF \(Health Fraction\):\s*([\d\.]+)', 'HF'),
        (r'wGMWI2.*?:\s*([\+\-]?[\d\.]+)', 'wGMWI2'),
        (r'BR \(Bias Ratio\):\s*([\d\.]+)', 'BR'),
        (r'CB \(Count Balance\):\s*([\+\-]?[\d\.]+)', 'CB'),
        (r'SB \(Signature Balance\):\s*([\+\-]?[\d\.]+)', 'SB'),
        (r'z-score:\s*([\+\-]?[\d\.]+)', 'z_score'),
    ]
    for pattern, key in scalar_patterns:
        match = re.search(pattern, text)
        m[key] = float(match.group(1)) if match else None
    
    # HF label
    hf_label_match = re.search(r'HF.*?\[(.*?)\]', text)
    m['HF_label'] = hf_label_match.group(1) if hf_label_match else None
    
    # SB multiplier
    sb_mult_match = re.search(r'SB.*?\(([\d\.]+)×\)', text)
    m['SB_multiplier'] = float(sb_mult_match.group(1)) if sb_mult_match else None
    
    # ── Section 2: Diversity Metrics ──
    for pattern, key in [
        (r'Shannon:\s*([\d\.]+)', 'Shannon'),
        (r'Pielou evenness:\s*([\d\.]+)', 'Pielou'),
    ]:
        match = re.search(pattern, text)
        m[key] = float(match.group(1)) if match else None
    
    # ── Section 3: Guild-Level Analysis ──
    # F:B ratio
    fb_match = re.search(r'Firmicutes/Bacteroidetes ratio:\s*([\d\.]+)', text)
    m['FB_ratio'] = float(fb_match.group(1)) if fb_match else None
    
    # Total assigned / unassigned
    assigned_match = re.search(r'Total assigned.*?:\s*([\d\.]+)%', text)
    m['total_assigned_pct'] = float(assigned_match.group(1)) if assigned_match else None
    
    unassigned_match = re.search(r'Unassigned.*?:\s*([\d\.]+)%', text)
    m['unassigned_pct'] = float(unassigned_match.group(1)) if unassigned_match else None
    
    gm_match = re.search(r'Geometric mean.*?:\s*([\d\.]+)%', text)
    m['geometric_mean'] = float(gm_match.group(1)) if gm_match else None
    
    # CLR Diagnostic Ratios (handle nan)
    for pattern, key in [
        (r'CUR.*?:\s*([\+\-]?[\d\.]+|[\+\-]?nan)', 'CUR'),
        (r'FCR.*?:\s*([\+\-]?[\d\.]+|[\+\-]?nan)', 'FCR'),
        (r'MDR.*?:\s*([\+\-]?[\d\.]+|[\+\-]?nan)', 'MDR'),
        (r'PPR.*?:\s*([\+\-]?[\d\.]+|[\+\-]?nan)', 'PPR'),
    ]:
        match = re.search(pattern, text)
        if match:
            val = match.group(1).replace('+', '')
            m[key] = float(val) if val != 'nan' else None
        else:
            m[key] = None
    
    # CLR ratio labels
    for ratio in ['CUR', 'FCR', 'MDR', 'PPR']:
        label_match = re.search(rf'{ratio}.*?\[(.+?)\]', text)
        m[f'{ratio}_label'] = label_match.group(1) if label_match else None
    
    # Guild abundances, redundancy, CLR, status, axis
    guilds = {}
    # Multi-line guild block pattern
    guild_block_pattern = re.compile(
        r'  (\S[\w/ \-]+?):\s*\n'
        r'\s*Abundance:\s*([\d\.]+)%\s*\|\s*Redundancy:\s*([\d\.]+)\s*\|\s*Status:\s*(\w+)\s*\n'
        r'\s*Axis 1 \(Absolute\):\s*(\w+)\s*-\s*(.*?)\n'
        r'\s*Axis 2 \(Relative\):\s*(?:CLR\s*([\+\-]?[\d\.]+)\s*\((\w+),\s*([\d\.]+)×\s*GM\)|(\w+))',
        re.MULTILINE
    )
    for gm in guild_block_pattern.finditer(text):
        name = gm.group(1).strip()
        guild = {
            'abundance': float(gm.group(2)),
            'redundancy': float(gm.group(3)),
            'status': gm.group(4),
            'axis1_state': gm.group(5),
            'axis1_description': gm.group(6).strip(),
        }
        if gm.group(7):  # CLR value present
            guild['clr'] = float(gm.group(7))
            guild['clr_label'] = gm.group(8)
            guild['clr_fold'] = float(gm.group(9))
        else:  # Absent
            guild['clr'] = None
            guild['clr_label'] = gm.group(10)  # "Absent"
            guild['clr_fold'] = None
        guilds[name] = guild
    m['guilds'] = guilds
    
    # ── Section 4: Vitamin Signals ──
    vitamins = {}
    
    # Compositional risk indicators
    akk_match = re.search(r'Akkermansia muciniphila:\s*([\d\.]+)', text)
    vitamins['akkermansia'] = float(akk_match.group(1)) if akk_match else None
    
    bact_match = re.search(r'Bacteroides genus:\s*([\d\.]+)', text)
    vitamins['bacteroides_genus'] = float(bact_match.group(1)) if bact_match else None
    
    lr_match = re.search(r'Lachnospiraceae \+ Ruminococcaceae:\s*([\d\.]+)', text)
    vitamins['lachno_rumino'] = float(lr_match.group(1)) if lr_match else None
    
    # Folate risk score
    folate_match = re.search(r'Folate.*?Risk Score:\s*(\d)/3', text)
    vitamins['folate_risk'] = int(folate_match.group(1)) if folate_match else None
    
    # Biotin producers
    biotin_match = re.search(r'Biotin.*?Producers detected:\s*(\d)/4', text)
    vitamins['biotin_producers'] = int(biotin_match.group(1)) if biotin_match else None
    
    # B-complex risk score
    bcomplex_match = re.search(r'B1.*?Risk Score:\s*(\d)/3', text)
    vitamins['bcomplex_risk'] = int(bcomplex_match.group(1)) if bcomplex_match else None
    
    m['vitamins'] = vitamins
    
    # ── Section 5: BCFA Pathways ──
    bcfa_match = re.search(r'BCFA Fermentation Pathways\s+(\d+)', text)
    m['bcfa_pathway_count'] = int(bcfa_match.group(1)) if bcfa_match else 0
    
    # ── Section 7: Dysbiosis Taxa ──
    dysbiosis = {}
    for taxon_name, pattern in [
        ('F_nucleatum', r'Fusobacterium nucleatum.*?Relative abundance:\s*([\d\.]+)%'),
        ('S_gallolyticus', r'Streptococcus gallolyticus.*?Relative abundance:\s*([\d\.]+)%'),
        ('P_anaerobius', r'Peptostreptococcus anaerobius.*?Relative abundance:\s*([\d\.]+)%'),
        ('E_Shigella', r'Escherichia-Shigella.*?Relative abundance:\s*([\d\.]+)%'),
    ]:
        match = re.search(pattern, text, re.DOTALL)
        dysbiosis[taxon_name] = float(match.group(1)) if match else 0.0
    m['dysbiosis'] = dysbiosis
    
    return m


def parse_b12_genera_from_metaphlan(sample_dir: str, sample_id: str) -> dict:
    """Extract B12 MR-associated genera abundances from MetaPhlAn output."""
    metaphlan_path = os.path.join(sample_dir, 'bioinformatics', 'GMWI2', f'{sample_id}_run_metaphlan.txt')
    
    # B12 MR-associated genera to search for (from Hou et al., 2025)
    b12_genera = {
        'Akkermansia': 0.0,        # FDR-significant (p=0.001)
        'Coprococcus': 0.0,        # Nominal (includes Coprococcus 2/3)
        'Enterorhabdus': 0.0,      # Nominal
        'Lactococcus': 0.0,        # Nominal
    }
    
    if not os.path.exists(metaphlan_path):
        return b12_genera
    
    try:
        with open(metaphlan_path) as f:
            for line in f:
                if line.startswith('#') or line.startswith('clade_name'):
                    continue
                parts = line.strip().split('\t')
                if len(parts) < 3:
                    continue
                
                clade = parts[0]
                abundance = float(parts[2]) if len(parts) > 2 else 0
                
                # Match genus level only (has |g__ but NOT |s__)
                for genus in b12_genera:
                    if f'|g__{genus}' in clade and '|s__' not in clade:
                        b12_genera[genus] = abundance
    except Exception:
        pass
    
    return b12_genera


def parse_functional_guild(filepath: str) -> dict:
    """Parse functional_guild.csv for species-level data and M. smithii abundance."""
    result = {
        'species': [],
        'smithii_abundance': 0.0,
    }
    
    with open(filepath, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            species_entry = {
                'guild': row.get('guilds', ''),
                'taxonomy': row.get('clade_name', ''),
                'rel_abund': float(row.get('rel_abund_prop', 0)),
                'ffa_weight': float(row.get('ffa_weight', 0)),
                'ffa_contribution': float(row.get('ffa_contribution', 0)),
                'clr': float(row.get('clr', 0)),
            }
            result['species'].append(species_entry)
            
            # Check for M. smithii (raw abundance, not guild-weighted)
            if 'Methanobrevibacter_smithii' in row.get('clade_name', ''):
                result['smithii_abundance'] = float(row.get('rel_abund_prop', 0)) * 100  # Convert to %
    
    return result


def parse_questionnaire(filepath: str) -> dict:
    """Parse questionnaire JSON for sample metadata."""
    with open(filepath) as f:
        data = json.load(f)
    
    return {
        'sample_id': data.get('kit_code', ''),
        'first_name': data.get('first_name', ''),
        'last_name': data.get('last_name', ''),
        'age': data.get('age'),
        'biological_sex': data.get('biological_sex', ''),
        'created_at': data.get('created_at', ''),
        'updated_at': data.get('updated_at', ''),
    }


def find_sample_files(sample_dir: str) -> dict:
    """Locate all required files for a sample directory."""
    sample_id = os.path.basename(sample_dir.rstrip('/'))
    
    files = {
        'metrics': None,
        'guild_csv': None,
        'questionnaire': None,
    }
    
    # _only_metrics.txt (new path: bioinformatics/only_metrics/, fallback: only_metrics/)
    metrics_candidates = (
        glob.glob(os.path.join(sample_dir, 'bioinformatics', 'only_metrics', f'{sample_id}_only_metrics.txt')) or
        glob.glob(os.path.join(sample_dir, 'only_metrics', f'{sample_id}_only_metrics.txt'))
    )
    if metrics_candidates:
        files['metrics'] = metrics_candidates[0]
    
    # _functional_guild.csv
    guild_candidates = (
        glob.glob(os.path.join(sample_dir, 'bioinformatics', 'only_metrics', f'{sample_id}_functional_guild.csv')) or
        glob.glob(os.path.join(sample_dir, 'only_metrics', f'{sample_id}_functional_guild.csv'))
    )
    if guild_candidates:
        files['guild_csv'] = guild_candidates[0]
    
    # questionnaire .json
    q_candidates = glob.glob(os.path.join(sample_dir, 'questionnaire', f'questionnaire_{sample_id}.json'))
    if q_candidates:
        files['questionnaire'] = q_candidates[0]
    
    return files


def parse_all(sample_dir: str) -> dict:
    """Parse all available files for a sample and return unified data dict."""
    files = find_sample_files(sample_dir)
    
    data = {
        'sample_id': os.path.basename(sample_dir.rstrip('/')),
        'sample_dir': sample_dir,
        'files_found': {k: v is not None for k, v in files.items()},
    }
    
    # Metrics (required)
    if files['metrics']:
        metrics = parse_only_metrics(files['metrics'])
        data.update(metrics)
    else:
        raise FileNotFoundError(f"No _only_metrics.txt found in {sample_dir}")
    
    # Guild CSV (optional but valuable)
    if files['guild_csv']:
        guild_data = parse_functional_guild(files['guild_csv'])
        data['guild_species'] = guild_data['species']
        data['smithii_abundance'] = guild_data['smithii_abundance']
    else:
        data['guild_species'] = []
        data['smithii_abundance'] = 0.0
    
    # Questionnaire (optional)
    if files['questionnaire']:
        data['questionnaire'] = parse_questionnaire(files['questionnaire'])
    else:
        data['questionnaire'] = None
    
    # B12 MR-associated genera from MetaPhlAn
    data['b12_genera'] = parse_b12_genera_from_metaphlan(sample_dir, data['sample_id'])
    
    return data


# ── CLI test ──
if __name__ == '__main__':
    import sys
    import json as json_mod
    
    if len(sys.argv) < 2:
        print("Usage: python parse_metrics.py <sample_dir>")
        print("Example: python parse_metrics.py /path/to/analysis/nb1_2026_001/1421263814738/")
        sys.exit(1)
    
    sample_dir = sys.argv[1]
    data = parse_all(sample_dir)
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"Sample: {data['sample_id']}")
    print(f"Files found: {data['files_found']}")
    print(f"\n── Compositional ──")
    print(f"  GMWI2: {data.get('GMWI2')}")
    print(f"  HF: {data.get('HF')} [{data.get('HF_label')}]")
    print(f"  wGMWI2: {data.get('wGMWI2')}")
    print(f"  BR: {data.get('BR')}")
    print(f"  SB: {data.get('SB')} ({data.get('SB_multiplier')}×)")
    print(f"  z-score: {data.get('z_score')}")
    print(f"\n── Diversity ──")
    print(f"  Shannon: {data.get('Shannon')}")
    print(f"  Pielou: {data.get('Pielou')}")
    print(f"\n── CLR Ratios ──")
    for r in ['CUR', 'FCR', 'MDR', 'PPR']:
        print(f"  {r}: {data.get(r)} [{data.get(f'{r}_label')}]")
    print(f"\n── Guilds ──")
    for gname, gdata in data.get('guilds', {}).items():
        clr_str = f"CLR {gdata['clr']:+.2f}" if gdata['clr'] is not None else "CLR absent"
        print(f"  {gname}: {gdata['abundance']:.2f}% | J={gdata['redundancy']:.2f} | {clr_str}")
    print(f"\n── Special Taxa ──")
    print(f"  M. smithii: {data.get('smithii_abundance', 0):.2f}%")
    print(f"  BCFA pathways: {data.get('bcfa_pathway_count', 0)}")
    print(f"\n── Dysbiosis ──")
    for taxon, abund in data.get('dysbiosis', {}).items():
        print(f"  {taxon}: {abund:.4f}%")
    print(f"{'='*60}")
