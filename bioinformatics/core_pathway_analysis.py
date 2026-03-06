#!/usr/bin/env python3
"""
Core Pathway Analysis Module
-----------------------------
Analyzes functional pathway architecture, carrier redundancy, and capacity indices
for core gut health pathways (SCFA, vitamins, bile acids, etc.)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple


def match_pathways_to_keywords(
    pathways_comlevel: pd.DataFrame,
    keywords_df: pd.DataFrame
) -> Dict[str, pd.DataFrame]:
    """
    Match community-level pathways to keyword categories.
    
    Compatible with both HUMANn v3 (with coverage) and v4 (abundance-only).
    
    Parameters:
    -----------
    pathways_comlevel : pd.DataFrame
        Community-level pathways with columns: pathway, relab
        Optional: coverage (HUMANn v3 only)
    keywords_df : pd.DataFrame
        Keywords table with columns: Core Pathways, Keywords
        
    Returns:
    --------
    Dict[str, pd.DataFrame]
        Dictionary mapping category name to hits_df for that category
    """
    results = {}
    
    # Check if coverage column exists (v3 vs v4)
    has_coverage = "coverage" in pathways_comlevel.columns
    
    # For HUMANn v4: Calculate tier classification based on quantiles
    if not has_coverage:
        q50 = pathways_comlevel['relab'].quantile(0.50)
        q75 = pathways_comlevel['relab'].quantile(0.75)
        
        def classify_tier(relab):
            if relab > q75:
                return "High"
            elif relab > q50:
                return "Moderate"
            else:
                return "Marginal"
        
        pathways_comlevel = pathways_comlevel.copy()
        pathways_comlevel['tier'] = pathways_comlevel['relab'].apply(classify_tier)
    
    # Precompute lowercase pathway names once
    pathways_lc = pathways_comlevel["pathway"].astype(str).str.lower()
    
    for _, row in keywords_df.iterrows():
        category = row["Core Pathways"]
        keywords_str = row["Keywords"]
        
        # Parse keywords
        keywords = [
            k.strip().strip('"').strip("'").lower() 
            for k in str(keywords_str).split(",") 
            if k.strip()
        ]
        
        if not keywords:
            continue
        
        # Match pathways to keywords
        hits = []
        for idx, pathway_name_lc in enumerate(pathways_lc):
            matched_keyword = None
            for keyword in keywords:
                if keyword in pathway_name_lc:
                    matched_keyword = keyword
                    break
            
            if matched_keyword:
                row_data = pathways_comlevel.iloc[idx]
                hit_dict = {
                    "pathway": row_data["pathway"],
                    "relab": float(row_data["relab"]),
                    "matched_keyword": matched_keyword
                }
                # Add coverage/tier depending on version
                if has_coverage:
                    hit_dict["coverage"] = float(row_data["coverage"])
                else:
                    # HUMANn v4: Use tier classification instead of coverage
                    hit_dict["tier"] = row_data["tier"]
                
                hits.append(hit_dict)
        
        if not hits:
            continue
        
        # Create hits_df
        hits_df = pd.DataFrame(hits)
        # Sort by coverage if v3, by relab if v4
        sort_col = "coverage" if has_coverage else "relab"
        hits_df = hits_df.sort_values(sort_col, ascending=False)
        
        # Rename columns based on version
        if has_coverage:
            hits_df = hits_df.rename(columns={
                "coverage": "comlevel_coverage",
                "relab": "relab_coverage"
            })
        else:
            hits_df = hits_df.rename(columns={
                "relab": "relab_coverage"
                # Keep 'tier' as-is
            })
        
        # Extract metacyc_id (part before first ":")
        hits_df["metacyc_id"] = (
            hits_df["pathway"]
            .astype(str)
            .str.split(":", n=1)
            .str[0]
            .str.strip()
        )
        
        # Reorder columns based on version
        if has_coverage:
            hits_df = hits_df[[
                "metacyc_id", "pathway", "comlevel_coverage", 
                "relab_coverage", "matched_keyword"
            ]]
        else:
            hits_df = hits_df[[
                "metacyc_id", "pathway", "tier",
                "relab_coverage", "matched_keyword"
            ]]
        
        results[category] = hits_df
    
    return results


def compute_pielou_evenness(relative_abundances: np.ndarray) -> float:
    """
    Compute Pielou evenness index (J).
    
    J = H / H_max
    where H = Shannon entropy, H_max = ln(S), S = number of species
    
    Ranges:
    - J ≈ 0-0.3: Very uneven, fragile function
    - J ≈ 0.3-0.7: Moderate redundancy
    - J ≈ 0.7-1.0: Highly even, robust function
    """
    p = np.asarray(relative_abundances)
    p = p[p > 0]  # Remove zeros
    
    if len(p) <= 1:
        return 0.0
    
    H = -np.sum(p * np.log(p + 1e-10))
    H_max = np.log(len(p))
    
    return H / H_max


def analyze_pathway_carriers(
    metacyc_id: str,
    hits_df: pd.DataFrame,
    pathways_stratified: pd.DataFrame
) -> Dict:
    """
    Analyze carrier taxa for a specific pathway.
    
    Compatible with both HUMANn v3 (with coverage) and v4 (abundance-only).
    
    Returns dict with:
    - n_carriers: number of carrier taxa
    - top1_share: share of top carrier
    - top3_share: share of top 3 carriers
    - pielou_J: Pielou evenness index
    - top_carriers: list of (taxon, share, coverage/relab) tuples
    """
    # Get metacyc_ids for this pathway
    hits_ids = (
        hits_df[hits_df["metacyc_id"] == metacyc_id]["pathway"]
        .astype(str)
        .str.split(":", n=1)
        .str[0]
        .str.strip()
    )
    
    # Filter stratified pathways
    mask = pathways_stratified["metacyc_id"].astype(str).isin(hits_ids)
    strat = pathways_stratified[mask].copy()
    
    if strat.empty:
        return None
    
    # Keep only carriers with relab > 0
    carriers = strat[strat["relab"] > 0].copy()
    
    if carriers.empty:
        return None
    
    # Normalize abundances
    total_relab = carriers["relab"].sum()
    carriers["relab_norm"] = carriers["relab"] / total_relab
    carriers = carriers.sort_values("relab_norm", ascending=False)
    
    # Compute metrics
    n_carriers = len(carriers)
    top1_share = carriers["relab_norm"].iloc[0]
    top3_share = carriers["relab_norm"].iloc[:min(3, n_carriers)].sum()
    pielou_J = compute_pielou_evenness(carriers["relab_norm"].values)
    
    # Check if coverage exists (v3 vs v4)
    has_coverage = "coverage" in carriers.columns
    
    # Get top carriers - use coverage if available (v3), otherwise use relab (v4)
    top_carriers = []
    for _, row in carriers.head(5).iterrows():
        metric_val = row.get("coverage", row["relab"]) if has_coverage else row["relab"]
        top_carriers.append((row["feature_token"], row["relab_norm"], metric_val))
    
    return {
        "n_carriers": n_carriers,
        "top1_share": top1_share,
        "top3_share": top3_share,
        "pielou_J": pielou_J,
        "top_carriers": top_carriers
    }


def analyze_category(
    category: str,
    hits_df: pd.DataFrame,
    pathways_stratified: pd.DataFrame,
    pathways_comlevel: pd.DataFrame
) -> Dict:
    """
    Complete analysis for one functional category.
    
    Returns dict with:
    - category: category name
    - pathways: list of pathway analyses
    - capacity_unweighted: sum of relab over category pathways
    - capacity_weighted: coverage-weighted capacity index (v3) or same as unweighted (v4)
    """
    if hits_df.empty:
        return None
    
    # Check if we have coverage (v3) or tier (v4)
    has_coverage = "comlevel_coverage" in hits_df.columns
    
    # Compute within-category shares
    total_category = hits_df["relab_coverage"].sum()
    if total_category <= 0:
        return None
    
    hits_df["share_category"] = hits_df["relab_coverage"] / total_category
    
    # Compute shares of total functional capacity
    total_all = pathways_comlevel["relab"].sum()
    if total_all <= 0:
        return None
    
    hits_df["share_all"] = hits_df["relab_coverage"] / total_all
    
    # Analyze each pathway
    pathway_analyses = []
    
    for _, row in hits_df.iterrows():
        metacyc_id = row["metacyc_id"]
        pathway_name = row["pathway"]
        
        # Carrier analysis
        carrier_info = analyze_pathway_carriers(
            metacyc_id, hits_df, pathways_stratified
        )
        
        pathway_analysis = {
            "metacyc_id": metacyc_id,
            "pathway": pathway_name,
            "comlevel_relab": row["relab_coverage"],
            "share_category": row["share_category"],
            "share_all": row["share_all"],
            "carriers": carrier_info
        }
        
        # Add coverage (v3) or tier (v4)
        if has_coverage:
            pathway_analysis["comlevel_coverage"] = row["comlevel_coverage"]
        else:
            pathway_analysis["tier"] = row["tier"]
        
        pathway_analyses.append(pathway_analysis)
    
    # Compute capacity indices
    if has_coverage:
        hits_df["cov_weighted_relab"] = (
            hits_df["comlevel_coverage"] * hits_df["relab_coverage"]
        )
        capacity_weighted = hits_df["cov_weighted_relab"].sum()
    else:
        # HUMANn v4: No coverage, so weighted = unweighted
        capacity_weighted = hits_df["relab_coverage"].sum()
    
    capacity_unweighted = hits_df["relab_coverage"].sum()
    
    return {
        "category": category,
        "pathways": pathway_analyses,
        "capacity_unweighted": capacity_unweighted,
        "capacity_weighted": capacity_weighted
    }


def get_category_pathways_table(analysis: Dict) -> pd.DataFrame:
    """
    Create compact pathway table for a category.
    
    Returns DataFrame with columns (v3):
    - Pathway, Coverage, Relab, Share_All, N_Carriers, Top3_Share, Pielou_J, Top_Carriers
    
    Or columns (v4):
    - Pathway, Tier, Relab, Share_All, N_Carriers, Top3_Share, Pielou_J, Top_Carriers
    """
    rows = []
    
    # Check if we have coverage (v3) or tier (v4)
    has_coverage = "comlevel_coverage" in analysis["pathways"][0] if analysis["pathways"] else False
    
    def abbreviate_taxon(taxon_name):
        """Abbreviate taxon name: s__Akkermansia_muciniphila -> A.muciniphila"""
        if pd.isna(taxon_name) or not taxon_name:
            return taxon_name
        
        # Extract the species name after s__ or g__
        if "s__" in taxon_name:
            species = taxon_name.split("s__")[1] if "s__" in taxon_name else taxon_name
        elif "g__" in taxon_name:
            species = taxon_name.split("g__")[1] if "g__" in taxon_name else taxon_name
        else:
            species = taxon_name
        
        # Split genus and species
        parts = species.split("_", 1)
        if len(parts) == 2:
            genus, sp = parts
            return f"{genus[0]}.{sp}"  # First letter of genus + species
        else:
            return species[:20]  # Fallback: truncate
    
    for pathway in analysis["pathways"]:
        # Truncate pathway name for display
        pathway_display = pathway["pathway"][:60] + "..." if len(pathway["pathway"]) > 60 else pathway["pathway"]
        
        row_dict = {
            "Pathway": pathway_display,
            "Relab": pathway["comlevel_relab"],
            "Share_All": pathway["share_all"],
        }
        
        # Add Coverage (v3) or Tier (v4)
        if has_coverage:
            row_dict["Coverage"] = pathway["comlevel_coverage"]
        else:
            row_dict["Tier"] = pathway["tier"]
        
        # Add carrier info
        if pathway["carriers"]:
            c = pathway["carriers"]
            
            # Format top 3 carriers with abbreviated names
            top_carriers_list = []
            for taxon, share, _ in c["top_carriers"][:3]:  # Top 3 only
                abbrev = abbreviate_taxon(taxon)
                top_carriers_list.append(f"{abbrev} ({share*100:.0f}%)")
            top_carriers_str = ", ".join(top_carriers_list)
            
            row_dict.update({
                "N_Carriers": c["n_carriers"],
                "Top3_Share": c["top3_share"],
                "Pielou_J": c["pielou_J"],
                "Top_Carriers": top_carriers_str
            })
        else:
            row_dict.update({
                "N_Carriers": 0,
                "Top3_Share": np.nan,
                "Pielou_J": np.nan,
                "Top_Carriers": "No carriers"
            })
        
        rows.append(row_dict)
    
    df = pd.DataFrame(rows)
    
    # Reorder columns: Pathway, Coverage/Tier, Relab, Share_All, N_Carriers, Top3_Share, Pielou_J, Top_Carriers
    if has_coverage:
        col_order = ["Pathway", "Coverage", "Relab", "Share_All", "N_Carriers", "Top3_Share", "Pielou_J", "Top_Carriers"]
    else:
        col_order = ["Pathway", "Tier", "Relab", "Share_All", "N_Carriers", "Top3_Share", "Pielou_J", "Top_Carriers"]
    
    return df[col_order]


def format_category_table_report(analysis: Dict) -> str:
    """Format analysis results as compact table report."""
    if not analysis:
        return ""
    
    lines = []
    category = analysis["category"]
    
    lines.append(f"\n{category.upper()}")
    lines.append("=" * 80)
    
    # Get pathway table
    pathway_table = get_category_pathways_table(analysis)
    
    if pathway_table.empty:
        lines.append("No pathways detected.")
    else:
        # Filter out pathways with no carriers (N_Carriers = 0)
        pathways_with_carriers = pathway_table[pathway_table['N_Carriers'] > 0].copy()
        
        if pathways_with_carriers.empty:
            lines.append(f"Detected {len(pathway_table)} pathway(s), but no carrier taxa found.")
            lines.append("")
            lines.append("INTERPRETATION:")
            lines.append("These pathways were matched by keywords in the community-level functional profile,")
            lines.append("but the taxa-stratified analysis found zero carriers contributing to them.")
            lines.append("This indicates the pathways are either:")
            lines.append("  • Present at very low abundance below detection threshold")
            lines.append("  • Not actively expressed in this sample")
            lines.append("  • Artifacts of pathway inference algorithms")
        else:
            # Check if we have Coverage (v3) or Tier (v4)
            has_coverage = "Coverage" in pathways_with_carriers.columns
            
            # Format with proper scientific notation - only show pathways with carriers
            formatted_lines = []
            if has_coverage:
                # HUMANn v3 table header
                formatted_lines.append(f"{'Pathway':<40} {'Coverage':>10} {'Relab':>10} {'Share%':>8} {'N_Carr':>8} {'Top3':>8} {'Pielou':>8} {'Top_Carriers':<60}")
                
                for _, row in pathways_with_carriers.iterrows():
                    pathway = row['Pathway'][:40]
                    coverage = f"{row['Coverage']:.2e}"
                    relab = f"{row['Relab']:.2e}"
                    share_pct = f"{row['Share_All']*100:.2f}"
                    n_carriers = f"{row['N_Carriers']:.0f}"
                    top3 = f"{row['Top3_Share']:.3f}" if not pd.isna(row['Top3_Share']) else "N/A"
                    pielou = f"{row['Pielou_J']:.3f}" if not pd.isna(row['Pielou_J']) else "N/A"
                    top_carriers = row['Top_Carriers'][:60]
                    
                    formatted_lines.append(f"{pathway:<40} {coverage:>10} {relab:>10} {share_pct:>8} {n_carriers:>8} {top3:>8} {pielou:>8} {top_carriers:<60}")
            else:
                # HUMANn v4 table header
                formatted_lines.append(f"{'Pathway':<40} {'Tier':>10} {'Relab':>10} {'Share%':>8} {'N_Carr':>8} {'Top3':>8} {'Pielou':>8} {'Top_Carriers':<60}")
                
                for _, row in pathways_with_carriers.iterrows():
                    pathway = row['Pathway'][:40]
                    tier = f"{row['Tier']:>10}"
                    relab = f"{row['Relab']:.2e}"
                    share_pct = f"{row['Share_All']*100:.2f}"
                    n_carriers = f"{row['N_Carriers']:.0f}"
                    top3 = f"{row['Top3_Share']:.3f}" if not pd.isna(row['Top3_Share']) else "N/A"
                    pielou = f"{row['Pielou_J']:.3f}" if not pd.isna(row['Pielou_J']) else "N/A"
                    top_carriers = row['Top_Carriers'][:60]
                    
                    formatted_lines.append(f"{pathway:<40} {tier:>10} {relab:>10} {share_pct:>8} {n_carriers:>8} {top3:>8} {pielou:>8} {top_carriers:<60}")
            
            lines.extend(formatted_lines)
            
            # Add note if some pathways were filtered out
            n_filtered = len(pathway_table) - len(pathways_with_carriers)
            if n_filtered > 0:
                lines.append("")
                lines.append(f"Note: {n_filtered} pathway(s) detected but excluded from table (no carrier taxa found)")
    
    return "\n".join(lines)


def format_category_report(analysis: Dict) -> str:
    """Format analysis results as human-readable report (legacy verbose format)."""
    if not analysis:
        return ""
    
    lines = []
    category = analysis["category"]
    
    # Check if we have coverage (v3) or tier (v4)
    has_coverage = "comlevel_coverage" in analysis["pathways"][0] if analysis["pathways"] else False
    
    lines.append("-" * 72)
    lines.append(f"WITHIN-SAMPLE {category.upper()} ARCHITECTURE")
    lines.append("-" * 72)
    
    for pathway in analysis["pathways"]:
        lines.append("")
        lines.append(f"{pathway['metacyc_id']} | {pathway['pathway']}")
        
        if has_coverage:
            lines.append(f"  Coverage (community): {pathway['comlevel_coverage']:.3f}")
        else:
            lines.append(f"  Tier (abundance): {pathway['tier']}")
        
        lines.append(f"  Relab (community):    {pathway['comlevel_relab']:.2e}")
        lines.append(f"  Share within {category}: {pathway['share_category']:.1%}")
        lines.append(f"  Share of all pathways: {pathway['share_all']:.2%}")
        
        if pathway["carriers"]:
            c = pathway["carriers"]
            lines.append(
                f"  Carriers: {c['n_carriers']} taxa, "
                f"top1={c['top1_share']:.1%}, top3={c['top3_share']:.1%}"
            )
            lines.append(f"  Redundancy: Pielou J={c['pielou_J']:.3f}")
            lines.append("  Top carriers:")
            
            for i, (taxon, share, cov) in enumerate(c["top_carriers"], start=1):
                lines.append(
                    f"    {i:2d}. {taxon:<45} {share:.1%} (cov={cov:.2f})"
                )
    
    lines.append("")
    lines.append(f"{category.upper()} CAPACITY INDICES")
    lines.append("-" * 72)
    lines.append(
        f"Unweighted capacity (sum relab): {analysis['capacity_unweighted']:.4e}"
    )
    lines.append(
        f"Coverage-weighted capacity:      {analysis['capacity_weighted']:.4e}"
    )
    lines.append("")
    
    return "\n".join(lines)


def analyze_all_categories(
    keywords_df: pd.DataFrame,
    pathways_comlevel: pd.DataFrame,
    pathways_stratified: pd.DataFrame,
    verbose: bool = True
) -> Dict[str, Dict]:
    """
    Analyze all functional categories.
    
    Parameters:
    -----------
    keywords_df : pd.DataFrame
        Keywords table with Core Pathways and Keywords columns
    pathways_comlevel : pd.DataFrame
        Community-level pathways
    pathways_stratified : pd.DataFrame
        Taxon-stratified pathways
    verbose : bool
        If True, print reports as they're generated
        
    Returns:
    --------
    Dict[str, Dict]
        Dictionary mapping category name to analysis results
    """
    # Match all pathways to keywords
    category_hits = match_pathways_to_keywords(pathways_comlevel, keywords_df)
    
    # Analyze each category
    results = {}
    
    for category, hits_df in category_hits.items():
        analysis = analyze_category(
            category, hits_df, pathways_stratified, pathways_comlevel
        )
        
        if analysis:
            results[category] = analysis
            
            if verbose:
                report = format_category_report(analysis)
                print(report)
    
    return results


def get_category_summary_table(results: Dict[str, Dict]) -> pd.DataFrame:
    """
    Create summary table across all categories.
    
    Returns DataFrame with columns:
    - Category
    - N_Pathways
    - Capacity_Unweighted (scientific notation)
    - Capacity_Weighted (scientific notation)
    - Mean_Pielou_J (rounded to 3 digits)
    - Median_Pielou_J (rounded to 3 digits)
    """
    rows = []
    
    for category, analysis in results.items():
        n_pathways = len(analysis["pathways"])
        
        # Mean and Median Pielou J across pathways with carriers
        pielou_values = [
            p["carriers"]["pielou_J"] 
            for p in analysis["pathways"] 
            if p["carriers"]
        ]
        mean_pielou = round(np.mean(pielou_values), 3) if pielou_values else np.nan
        median_pielou = round(np.median(pielou_values), 3) if pielou_values else np.nan
        
        rows.append({
            "Category": category,
            "N_Pathways": n_pathways,
            "Capacity_Unweighted": f"{analysis['capacity_unweighted']:.3e}",
            "Capacity_Weighted": f"{analysis['capacity_weighted']:.3e}",
            "Mean_Pielou_J": mean_pielou,
            "Median_Pielou_J": median_pielou
        })
    
    return pd.DataFrame(rows)
