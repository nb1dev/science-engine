#!/bin/bash

################################################################################
# Microbiome Sample Analysis Pipeline
# 
# Purpose: Process microbiome samples from S3 through GMWI2 and reporting
#
# Workflow per sample:
#   1. Discover samples from S3
#   2. Download data (functional_profiling, raw_sequences, taxonomic_profiling)
#   3. Run QC precheck (saves JSON + MD report, non-blocking)
#   4. Run GMWI2 analysis
#   5. Run integrated metrics report
#   6. Delete raw_sequences locally (save space)
#   7. Generate structured JSON report (microbiome_analysis_master + platform)
#   8. Generate formulation (if questionnaire exists)
#   9. Track completion status
#
# Usage:
#   bash scripts/sh/process_pilot_samples.sh                    # Process all batches
#   bash scripts/sh/process_pilot_samples.sh --batch BATCH_ID   # Process specific batch
#   bash scripts/sh/process_pilot_samples.sh --dry-run          # Show what would be done
#
# Example:
#   bash scripts/sh/process_pilot_samples.sh --batch nb1_2026_001
################################################################################

# Exit on error
set -e

# Force Python to flush output line-by-line (prevents buffered/delayed display)
export PYTHONUNBUFFERED=1

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
WORK_DIR="/Users/pnovikova/Documents/work"
S3_BUCKET="s3://nb1-prebiomics-sample-data/incoming"
DEFAULT_BATCHES=("nb1_2026_001" "nb1_2026_002" "nb1_2026_003")

# Script paths
GMWI2_SCRIPT="$WORK_DIR/science-engine/bioinformatics/run_gmwi2.sh"
REPORT_SCRIPT="$WORK_DIR/science-engine/bioinformatics/calculate_metrics.py"
QC_SCRIPT="$WORK_DIR/science-engine/bioinformatics/qc_precheck.py"
REPORT_JSON_SCRIPT="$WORK_DIR/science-engine/report/generate_report.py"
FORMULATION_SCRIPT="$WORK_DIR/science-engine/formulation/generate_formulation.py"

# Flags
DRY_RUN=false
METRICS_ONLY=false
QC_ONLY=false
FORCE=false
SPECIFIC_BATCH=""
SPECIFIC_SAMPLE=""

################################################################################
# Parse command line arguments
################################################################################
while [[ $# -gt 0 ]]; do
    case $1 in
        --batch)
            SPECIFIC_BATCH="$2"
            shift 2
            ;;
        --sample)
            SPECIFIC_SAMPLE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --metrics-only)
            METRICS_ONLY=true
            shift
            ;;
        --qc-only)
            QC_ONLY=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --batch BATCH_ID    Process specific batch (e.g., nb1_2026_001)"
            echo "  --sample SAMPLE_ID  Process specific sample (requires --batch)"
            echo "  --metrics-only      Skip S3 download and GMWI2; recalculate metrics only"
            echo "  --qc-only           Run only QC precheck on existing local data (no GMWI2/metrics)"
            echo "  --force             Force reprocessing even if sample is already complete"
            echo "  --dry-run           Show what would be done without executing"
            echo "  --help              Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --batch nb1_2026_001"
            echo "  $0 --batch nb1_2026_004 --sample 1421029282376"
            echo "  $0 --batch nb1_2026_007 --qc-only"
            echo ""
            echo "Default batches: ${DEFAULT_BATCHES[*]}"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

################################################################################
# Helper Functions
################################################################################

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

################################################################################
# Check if sample is already processed
################################################################################
is_sample_processed() {
    local batch_id=$1
    local sample_id=$2
    
    local status_file="$WORK_DIR/analysis/$batch_id/$sample_id/logs/${sample_id}_pipeline.status"
    
    if [ -f "$status_file" ]; then
        local status=$(cat "$status_file" | head -n 1)
        if [ "$status" = "PIPELINE_SUCCESS" ]; then
            return 0  # Already processed
        fi
    fi
    
    return 1  # Not processed
}

################################################################################
# Discover samples from S3 for a given batch
################################################################################
discover_samples() {
    local batch_id=$1
    
    # List sample directories in functional_profiling (all batches have same structure)
    local s3_path="$S3_BUCKET/$batch_id/functional_profiling/"
    
    # Get list of sample directories (directories in S3 are shown as "PRE" in aws s3 ls output)
    # Format: "2026-01-23 17:00:00     PRE 1421266404096/"
    # Only return lines that are 13-digit numbers (sample IDs)
    aws s3 ls "$s3_path" 2>/dev/null | grep "PRE" | awk '{print $NF}' | sed 's/\/$//' | grep -E '^[0-9]{13}$'
}

################################################################################
# Check if sample data already exists locally
################################################################################
check_data_exists() {
    local batch_id=$1
    local sample_id=$2
    local data_type=$3
    
    local data_dir="$WORK_DIR/data/$batch_id/$data_type/$sample_id"
    
    if [ -d "$data_dir" ] && [ "$(ls -A $data_dir 2>/dev/null)" ]; then
        return 0  # Data exists and directory is not empty
    else
        return 1  # Data doesn't exist or directory is empty
    fi
}

################################################################################
# Download sample data from S3
################################################################################
download_sample() {
    local batch_id=$1
    local sample_id=$2
    
    local data_dir="$WORK_DIR/data/$batch_id"
    mkdir -p "$data_dir"
    
    local needs_download=false
    
    # Check functional_profiling
    if check_data_exists "$batch_id" "$sample_id" "functional_profiling"; then
        log_info "Functional profiling data already exists for $sample_id - skipping download"
    else
        log_info "Downloading functional_profiling for $sample_id..."
        aws s3 sync "$S3_BUCKET/$batch_id/functional_profiling/$sample_id/" \
                    "$data_dir/functional_profiling/$sample_id/" \
                    --quiet
        needs_download=true
    fi
    
    # Check raw_sequences
    if check_data_exists "$batch_id" "$sample_id" "raw_sequences"; then
        log_info "Raw sequences already exist for $sample_id - skipping download"
    else
        log_info "Downloading raw_sequences for $sample_id..."
        aws s3 sync "$S3_BUCKET/$batch_id/raw_sequences/$sample_id/" \
                    "$data_dir/raw_sequences/$sample_id/" \
                    --quiet
        needs_download=true
    fi
    
    # Check taxonomic_profiling
    if check_data_exists "$batch_id" "$sample_id" "taxonomic_profiling"; then
        log_info "Taxonomic profiling data already exists for $sample_id - skipping download"
    else
        log_info "Downloading taxonomic_profiling for $sample_id..."
        aws s3 sync "$S3_BUCKET/$batch_id/taxonomic_profiling/$sample_id/" \
                    "$data_dir/taxonomic_profiling/$sample_id/" \
                    --quiet
        needs_download=true
    fi
    
    if [ "$needs_download" = true ]; then
        log_success "Download complete for $sample_id"
    else
        log_success "All data already present for $sample_id - no download needed"
    fi
}

################################################################################
# Validate raw sequences exist for GMWI2
################################################################################
validate_raw_sequences() {
    local batch_id=$1
    local sample_id=$2
    
    local raw_seq_dir="$WORK_DIR/data/$batch_id/raw_sequences/$sample_id"
    
    # Check if directory exists
    if [ ! -d "$raw_seq_dir" ]; then
        return 1
    fi
    
    # Check for R1 and R2 files (either .fastq or .fastq.gz)
    local r1_exists=false
    local r2_exists=false
    
    if [ -f "$raw_seq_dir/${sample_id}_R1.fastq" ] || [ -f "$raw_seq_dir/${sample_id}_R1.fastq.gz" ]; then
        r1_exists=true
    fi
    
    if [ -f "$raw_seq_dir/${sample_id}_R2.fastq" ] || [ -f "$raw_seq_dir/${sample_id}_R2.fastq.gz" ]; then
        r2_exists=true
    fi
    
    if [ "$r1_exists" = true ] && [ "$r2_exists" = true ]; then
        return 0  # Both files exist
    else
        return 1  # Files missing
    fi
}

################################################################################
# Run GMWI2 analysis
################################################################################
run_gmwi2() {
    local batch_id=$1
    local sample_id=$2
    
    # Validate raw sequences exist before running GMWI2
    if ! validate_raw_sequences "$batch_id" "$sample_id"; then
        log_error "Raw sequence files not found for $sample_id"
        log_info "Attempting to download raw sequences from S3..."
        
        # Try to download raw sequences
        local data_dir="$WORK_DIR/data/$batch_id"
        if aws s3 sync "$S3_BUCKET/$batch_id/raw_sequences/$sample_id/" \
                       "$data_dir/raw_sequences/$sample_id/" \
                       --quiet; then
            log_success "Raw sequences downloaded for $sample_id"
            
            # Validate again after download
            if ! validate_raw_sequences "$batch_id" "$sample_id"; then
                log_error "Raw sequence files still not found after download for $sample_id"
                return 1
            fi
        else
            log_error "Failed to download raw sequences for $sample_id"
            return 1
        fi
    fi
    
    log_info "Running GMWI2 analysis for $sample_id..."
    
    if bash "$GMWI2_SCRIPT" --batch "$batch_id" --sample "$sample_id"; then
        log_success "GMWI2 analysis complete for $sample_id"
        return 0
    else
        log_error "GMWI2 analysis failed for $sample_id"
        return 1
    fi
}

################################################################################
# Delete raw sequences to save space
################################################################################
cleanup_raw_sequences() {
    local batch_id=$1
    local sample_id=$2
    
    local raw_seq_dir="$WORK_DIR/data/$batch_id/raw_sequences/$sample_id"
    
    if [ -d "$raw_seq_dir" ]; then
        log_info "Deleting raw sequences for $sample_id to save space..."
        rm -rf "$raw_seq_dir"
        log_success "Raw sequences deleted for $sample_id"
    fi
}

################################################################################
# Run QC precheck (saves JSON + MD report to analysis/{batch}/{sample}/qc/)
################################################################################
run_qc_precheck() {
    local batch_id=$1
    local sample_id=$2
    
    log_info "Running QC precheck for $sample_id..."
    
    # Run QC with --local-only (data already downloaded) and --skip-fastq (FASTQ check not needed)
    if python "$QC_SCRIPT" --batch "$batch_id" --sample "$sample_id" --local-only --skip-fastq; then
        # Check QC output exists
        local qc_json="$WORK_DIR/analysis/$batch_id/$sample_id/qc/${sample_id}_qc_precheck.json"
        local qc_md="$WORK_DIR/analysis/$batch_id/$sample_id/qc/${sample_id}_qc_precheck.md"
        
        if [ -f "$qc_json" ] && [ -f "$qc_md" ]; then
            log_success "QC precheck complete for $sample_id (JSON + MD reports saved)"
        else
            log_warning "QC precheck ran but reports not found for $sample_id"
        fi
        
        # Log QC confidence level (informational — does not block pipeline)
        if [ -f "$qc_json" ]; then
            local qc_overall=$(python3 -c "import json; d=json.load(open('$qc_json')); print(d.get('confidence',{}).get('tiers',{}).get('overall','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
            local fes=$(python3 -c "import json; d=json.load(open('$qc_json')); print(d.get('functional_evidence_score',{}).get('score',0))" 2>/dev/null || echo "?")
            log_info "QC confidence: $qc_overall | Functional Evidence Score: $fes/100"
            
            if [ "$qc_overall" = "LOW" ]; then
                log_warning "LOW QC confidence for $sample_id — downstream metrics will have reduced reliability"
            fi
        fi
        
        return 0
    else
        log_warning "QC precheck failed for $sample_id — continuing pipeline (QC is non-blocking)"
        return 0  # Non-blocking: QC failure should not stop the pipeline
    fi
}

################################################################################
# Run integrated report
################################################################################
run_integrated_report() {
    local batch_id=$1
    local sample_id=$2
    
    log_info "Running integrated report for $sample_id..."
    
    if python "$REPORT_SCRIPT" --batch_id "$batch_id" --sample_id "$sample_id"; then
        log_success "Integrated report complete for $sample_id"
        return 0
    else
        log_error "Integrated report failed for $sample_id"
        return 1
    fi
}

################################################################################
# Mark sample as successfully processed
################################################################################
mark_sample_complete() {
    local batch_id=$1
    local sample_id=$2
    
    local log_dir="$WORK_DIR/analysis/$batch_id/$sample_id/logs"
    mkdir -p "$log_dir"
    
    local status_file="$log_dir/${sample_id}_pipeline.status"
    echo "PIPELINE_SUCCESS" > "$status_file"
    echo "$(date)" >> "$status_file"
    
    log_success "Sample $sample_id marked as complete"
}

################################################################################
# Mark sample as failed
################################################################################
mark_sample_failed() {
    local batch_id=$1
    local sample_id=$2
    local error_msg=$3
    
    local log_dir="$WORK_DIR/analysis/$batch_id/$sample_id/logs"
    mkdir -p "$log_dir"
    
    local status_file="$log_dir/${sample_id}_pipeline.status"
    echo "PIPELINE_FAILED" > "$status_file"
    echo "$(date)" >> "$status_file"
    echo "Error: $error_msg" >> "$status_file"
    
    log_error "Sample $sample_id marked as failed"
}

################################################################################
# Process a single sample through the entire pipeline
################################################################################
process_sample() {
    local batch_id=$1
    local sample_id=$2
    
    echo ""
    echo "========================================================================"
    echo "Processing Sample: $sample_id (Batch: $batch_id)"
    echo "========================================================================"
    
    # --metrics-only mode: skip download/GMWI2, just recalculate metrics
    if [ "$METRICS_ONLY" = true ]; then
        # Verify GMWI2 results exist
        local gmwi2_dir="$WORK_DIR/analysis/$batch_id/$sample_id/GMWI2"
        if [ ! -d "$gmwi2_dir" ] || [ ! -f "$gmwi2_dir/${sample_id}_run_GMWI2.txt" ]; then
            log_error "No GMWI2 results for $sample_id — cannot recalculate metrics without GMWI2. Run full pipeline first."
            return 1
        fi
        
        if [ "$DRY_RUN" = true ]; then
            log_info "[DRY RUN] Would recalculate metrics for $sample_id (GMWI2 exists)"
            return 0
        fi
        
        log_info "Recalculating metrics only for $sample_id (using existing GMWI2)..."
        if ! run_integrated_report "$batch_id" "$sample_id"; then
            log_error "Metrics recalculation failed for $sample_id"
            return 1
        fi
        log_success "Metrics recalculated for $sample_id"
        return 0
    fi
    
    # --qc-only mode: run only QC precheck on existing local data
    if [ "$QC_ONLY" = true ]; then
        # Verify local data exists (functional + taxonomic profiling needed for QC)
        if ! check_data_exists "$batch_id" "$sample_id" "functional_profiling" && \
           ! check_data_exists "$batch_id" "$sample_id" "taxonomic_profiling"; then
            log_error "No local data for $sample_id — need functional_profiling or taxonomic_profiling for QC."
            return 1
        fi
        
        if [ "$DRY_RUN" = true ]; then
            log_info "[DRY RUN] Would run QC precheck for $sample_id"
            return 0
        fi
        
        log_info "Running QC precheck only for $sample_id..."
        run_qc_precheck "$batch_id" "$sample_id"
        log_success "QC precheck complete for $sample_id"
        return 0
    fi
    
    # Full pipeline mode
    # Check if already processed (--force bypasses this check)
    if [ "$FORCE" = false ] && is_sample_processed "$batch_id" "$sample_id"; then
        log_warning "Sample $sample_id already processed. Skipping. (use --force to reprocess)"
        return 0
    fi
    
    if [ "$FORCE" = true ] && is_sample_processed "$batch_id" "$sample_id"; then
        log_warning "Sample $sample_id already processed — FORCE mode: reprocessing anyway"
    fi
    
    # In dry-run mode, just show what would be done
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would download sample: $sample_id"
        log_info "[DRY RUN] Would run GMWI2 analysis"
        log_info "[DRY RUN] Would delete raw sequences"
        log_info "[DRY RUN] Would run integrated report"
        return 0
    fi
    
    # Step 1: Download from S3
    if ! download_sample "$batch_id" "$sample_id"; then
        mark_sample_failed "$batch_id" "$sample_id" "Download failed"
        return 1
    fi
    
    # Step 2: QC precheck (non-blocking — saves QC report, logs confidence)
    run_qc_precheck "$batch_id" "$sample_id"
    
    # Step 3: Run GMWI2
    if ! run_gmwi2 "$batch_id" "$sample_id"; then
        mark_sample_failed "$batch_id" "$sample_id" "GMWI2 failed"
        return 1
    fi
    
    # Step 4: Run integrated report
    if ! run_integrated_report "$batch_id" "$sample_id"; then
        mark_sample_failed "$batch_id" "$sample_id" "Integrated report failed"
        return 1
    fi
    
    # Step 5: Delete raw sequences (only after integrated report succeeds)
    cleanup_raw_sequences "$batch_id" "$sample_id"
    
    # Step 6: Generate structured JSON report (microbiome analysis master + platform)
    local sample_analysis_dir="$WORK_DIR/analysis/$batch_id/$sample_id"
    if [ -f "$REPORT_JSON_SCRIPT" ]; then
        log_info "Running structured report generation for $sample_id..."
        if python "$REPORT_JSON_SCRIPT" --sample-dir "$sample_analysis_dir"; then
            log_success "Structured report generated for $sample_id"
        else
            log_warning "Structured report generation failed for $sample_id — continuing pipeline"
        fi
    fi
    
    # Step 7: Generate formulation (only if questionnaire exists)
    local questionnaire_dir="$sample_analysis_dir/questionnaire"
    if [ -f "$FORMULATION_SCRIPT" ] && [ -d "$questionnaire_dir" ] && [ "$(ls -A $questionnaire_dir 2>/dev/null)" ]; then
        log_info "Questionnaire found — running formulation for $sample_id..."
        if python "$FORMULATION_SCRIPT" --sample-dir "$sample_analysis_dir"; then
            log_success "Formulation generated for $sample_id"
        else
            log_warning "Formulation generation failed for $sample_id — continuing pipeline"
        fi
    else
        if [ ! -d "$questionnaire_dir" ] || [ -z "$(ls -A $questionnaire_dir 2>/dev/null)" ]; then
            log_info "No questionnaire for $sample_id — skipping formulation (run manually when questionnaire arrives)"
        fi
    fi
    
    # Step 8: Mark as complete
    mark_sample_complete "$batch_id" "$sample_id"
    
    echo "========================================================================"
    log_success "Sample $sample_id processing complete!"
    echo "========================================================================"
    echo ""
    
    return 0
}

################################################################################
# Process all samples in a batch
################################################################################
process_batch() {
    local batch_id=$1
    
    echo ""
    echo "###################################################################"
    echo "# Processing Batch: $batch_id"
    echo "###################################################################"
    echo ""
    
    # Create batch log directory
    local batch_log_dir="$WORK_DIR/analysis/$batch_id"
    mkdir -p "$batch_log_dir"
    
    local batch_log="$batch_log_dir/batch_summary.log"
    echo "Batch Processing Started: $(date)" > "$batch_log"
    echo "Batch ID: $batch_id" >> "$batch_log"
    echo "" >> "$batch_log"
    
    # Discover samples
    local samples=$(discover_samples "$batch_id")
    
    if [ -z "$samples" ]; then
        log_warning "No samples found for batch $batch_id"
        echo "No samples found" >> "$batch_log"
        return 1
    fi
    
    # Count samples
    local total_samples=$(echo "$samples" | wc -l | tr -d ' ')
    log_info "Found $total_samples samples in batch $batch_id"
    echo "Total samples: $total_samples" >> "$batch_log"
    echo "" >> "$batch_log"
    
    # Process each sample
    local processed=0
    local failed=0
    local skipped=0
    
    for sample_id in $samples; do
        # Skip "already processed" check for --qc-only and --metrics-only modes
        if [ "$FORCE" = false ] && [ "$QC_ONLY" = false ] && [ "$METRICS_ONLY" = false ] && is_sample_processed "$batch_id" "$sample_id"; then
            ((skipped++))
            echo "SKIPPED: $sample_id (already processed)" >> "$batch_log"
            log_info "Skipped: $sample_id ($skipped/$total_samples)"
            continue
        fi
        
        if process_sample "$batch_id" "$sample_id"; then
            ((processed++))
            echo "SUCCESS: $sample_id" >> "$batch_log"
        else
            ((failed++))
            echo "FAILED: $sample_id" >> "$batch_log"
        fi
    done
    
    # Summary
    echo "" >> "$batch_log"
    echo "Batch Processing Complete: $(date)" >> "$batch_log"
    echo "Processed: $processed" >> "$batch_log"
    echo "Failed: $failed" >> "$batch_log"
    echo "Skipped: $skipped" >> "$batch_log"
    echo "Total: $total_samples" >> "$batch_log"
    
    echo ""
    echo "###################################################################"
    log_success "Batch $batch_id complete!"
    log_info "  Processed: $processed"
    log_info "  Failed: $failed"
    log_info "  Skipped: $skipped"
    log_info "  Total: $total_samples"
    echo "###################################################################"
    echo ""
}

################################################################################
# Main Execution
################################################################################
main() {
    echo ""
    echo "###################################################################"
    echo "#                                                                  #"
    echo "#         Microbiome Sample Analysis Pipeline              #"
    echo "#                                                                  #"
    echo "###################################################################"
    echo ""
    echo "Start Time: $(date)"
    echo "Work Directory: $WORK_DIR"
    echo "S3 Bucket: $S3_BUCKET"
    
    if [ "$DRY_RUN" = true ]; then
        echo ""
        log_warning "DRY RUN MODE - No actual processing will occur"
    fi
    
    echo ""
    
    # Check if specific sample is requested
    if [ -n "$SPECIFIC_SAMPLE" ]; then
        if [ -z "$SPECIFIC_BATCH" ]; then
            log_error "--sample requires --batch to be specified"
            exit 1
        fi
        log_info "Processing specific sample: $SPECIFIC_SAMPLE in batch: $SPECIFIC_BATCH"
        
        # Pre-flight checks (relaxed for --qc-only and --metrics-only modes)
        if [ "$QC_ONLY" = false ] && [ "$METRICS_ONLY" = false ]; then
            if [ ! -f "$GMWI2_SCRIPT" ]; then
                log_error "GMWI2 script not found: $GMWI2_SCRIPT"
                exit 1
            fi
            if ! aws s3 ls "$S3_BUCKET" > /dev/null 2>&1; then
                log_error "Cannot access S3 bucket. Check AWS CLI configuration."
                exit 1
            fi
        fi
        
        if [ "$QC_ONLY" = true ]; then
            if [ ! -f "$QC_SCRIPT" ]; then
                log_error "QC script not found: $QC_SCRIPT"
                exit 1
            fi
            log_info "QC-ONLY MODE"
        fi
        
        log_success "Pre-flight checks passed"
        echo ""
        
        # Process single sample
        if process_sample "$SPECIFIC_BATCH" "$SPECIFIC_SAMPLE"; then
            log_success "Sample $SPECIFIC_SAMPLE processing complete!"
        else
            log_error "Sample $SPECIFIC_SAMPLE processing failed"
            exit 1
        fi
        
        echo ""
        echo "###################################################################"
        echo "#                                                                  #"
        echo "#              Sample Processing Complete!                        #"
        echo "#                                                                  #"
        echo "###################################################################"
        echo ""
        echo "End Time: $(date)"
        echo ""
        return 0
    fi
    
    # Determine which batches to process
    local batches_to_process
    if [ -n "$SPECIFIC_BATCH" ]; then
        batches_to_process=("$SPECIFIC_BATCH")
        log_info "Processing specific batch: $SPECIFIC_BATCH"
    else
        batches_to_process=("${DEFAULT_BATCHES[@]}")
        log_info "Processing all default batches: ${DEFAULT_BATCHES[*]}"
    fi
    
    # Pre-flight checks (relaxed for --qc-only and --metrics-only modes)
    if [ "$QC_ONLY" = true ]; then
        if [ ! -f "$QC_SCRIPT" ]; then
            log_error "QC script not found: $QC_SCRIPT"
            exit 1
        fi
        log_info "QC-ONLY MODE — skipping GMWI2/metrics/S3 checks"
    else
        if [ ! -f "$GMWI2_SCRIPT" ]; then
            log_error "GMWI2 script not found: $GMWI2_SCRIPT"
            exit 1
        fi
        if [ ! -f "$REPORT_SCRIPT" ]; then
            log_error "Report script not found: $REPORT_SCRIPT"
            exit 1
        fi
        # Check AWS CLI is configured
        if ! aws s3 ls "$S3_BUCKET" > /dev/null 2>&1; then
            log_error "Cannot access S3 bucket. Check AWS CLI configuration."
            exit 1
        fi
    fi
    
    log_success "Pre-flight checks passed"
    echo ""
    
    # Process each batch sequentially
    for batch_id in "${batches_to_process[@]}"; do
        process_batch "$batch_id"
    done
    
    echo ""
    echo "###################################################################"
    echo "#                                                                  #"
    echo "#              All Batches Processing Complete!                   #"
    echo "#                                                                  #"
    echo "###################################################################"
    echo ""
    echo "End Time: $(date)"
    echo ""
}

# Run main function
main
