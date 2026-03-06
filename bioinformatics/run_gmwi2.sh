#!/bin/bash

# Flexible GMWI2 Calculator with Named Arguments and Logging
# Usage: bash gmwi2_flexible.sh --batch BATCH_ID --sample SAMPLE_ID
# Example: bash gmwi2_flexible.sh --batch nb1_2026_002 --sample 1421266404096

# Parse named arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --batch)
            BATCH_ID="$2"
            shift 2
            ;;
        --sample)
            SAMPLE_ID="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --batch BATCH_ID --sample SAMPLE_ID"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$BATCH_ID" ] || [ -z "$SAMPLE_ID" ]; then
    echo "Error: Missing required arguments"
    echo "Usage: $0 --batch BATCH_ID --sample SAMPLE_ID"
    echo "Example: $0 --batch nb1_2026_002 --sample 1421266404096"
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Fixed base paths
WORK_DIR="/Users/pnovikova/Documents/work"
RAW_SEQS_DIR="$WORK_DIR/data/$BATCH_ID/raw_sequences/$SAMPLE_ID"
OUTPUT_BASE="$WORK_DIR/analysis/$BATCH_ID/$SAMPLE_ID"
OUTPUT_DIR="$OUTPUT_BASE/GMWI2"
LOG_DIR="$OUTPUT_BASE/logs"
LOG_FILE="$LOG_DIR/${SAMPLE_ID}_gmwi2_${TIMESTAMP}.log"

# Create directories
mkdir -p "$OUTPUT_DIR"
mkdir -p "$LOG_DIR"

# Start logging (all output to both terminal and log file)
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================================================"
echo "GMWI2 Analysis Pipeline"
echo "================================================"
echo "Timestamp: $(date)"
echo "Batch ID: $BATCH_ID"
echo "Sample ID: $SAMPLE_ID"
echo "Input: $RAW_SEQS_DIR"
echo "Output: $OUTPUT_DIR"
echo "Log: $LOG_FILE"
echo "================================================"

# Activate conda environment
echo "[$(date)] Activating conda environment..."

# Source conda initialization (handles 'conda init' requirement)
if [ -f "$(conda info --base)/etc/profile.d/conda.sh" ]; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
else
    # Fallback: try to find conda in PATH
    CONDA_BASE=$(conda info --base 2>/dev/null)
    if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
        source "$CONDA_BASE/etc/profile.d/conda.sh"
    else
        echo "[$(date)] ✗ ERROR: Could not initialize conda"
        echo "GMWI2_FAILED: Conda initialization failed" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
        exit 1
    fi
fi

conda activate gmwi2

# Check FASTQ files
r1_file="$RAW_SEQS_DIR/${SAMPLE_ID}_R1.fastq"
r2_file="$RAW_SEQS_DIR/${SAMPLE_ID}_R2.fastq"

echo "[$(date)] Checking input files..."
if [[ ! -f "$r1_file" && ! -f "${r1_file}.gz" ]]; then
    echo "[$(date)] ✗ ERROR: R1 file not found"
    echo "GMWI2_FAILED: R1 file not found" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
    exit 1
fi

if [[ ! -f "$r2_file" && ! -f "${r2_file}.gz" ]]; then
    echo "[$(date)] ✗ ERROR: R2 file not found"
    echo "GMWI2_FAILED: R2 file not found" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
    exit 1
fi

# Decompress if needed
if [[ ! -f "$r1_file" && -f "${r1_file}.gz" ]]; then
    echo "[$(date)] Decompressing R1..."
    gunzip -c "${r1_file}.gz" > "$r1_file"
    if [ $? -ne 0 ]; then
        echo "[$(date)] ✗ ERROR: Failed to decompress R1"
        echo "GMWI2_FAILED: R1 decompression failed" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
        exit 1
    fi
fi

if [[ ! -f "$r2_file" && -f "${r2_file}.gz" ]]; then
    echo "[$(date)] Decompressing R2..."
    gunzip -c "${r2_file}.gz" > "$r2_file"
    if [ $? -ne 0 ]; then
        echo "[$(date)] ✗ ERROR: Failed to decompress R2"
        echo "GMWI2_FAILED: R2 decompression failed" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
        exit 1
    fi
fi

# Change to output directory
cd "$OUTPUT_DIR"

# Run GMWI2
echo "[$(date)] Running GMWI2 analysis..."
gmwi2 -f "$r1_file" \
      -r "$r2_file" \
      -n 8 \
      -o "${SAMPLE_ID}_run"

# Check success
if [ $? -eq 0 ]; then
    echo "[$(date)] ✓ GMWI2 calculation SUCCESS"
    echo "[$(date)] Results saved to: $OUTPUT_DIR"
    
    # Verify output files
    if [[ -f "${SAMPLE_ID}_run_GMWI2.txt" ]] && \
       [[ -f "${SAMPLE_ID}_run_GMWI2_taxa.txt" ]] && \
       [[ -f "${SAMPLE_ID}_run_metaphlan.txt" ]]; then
        echo "[$(date)] ✓ All output files generated"
        echo "GMWI2_SUCCESS" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
    else
        echo "[$(date)] ✗ WARNING: Some output files missing"
        echo "GMWI2_PARTIAL: Missing output files" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
    fi
else
    echo "[$(date)] ✗ GMWI2 calculation FAILED"
    echo "GMWI2_FAILED: gmwi2 command returned error" > "$LOG_DIR/${SAMPLE_ID}_gmwi2.status"
    exit 1
fi

echo "================================================"
echo "GMWI2 Pipeline Complete"
echo "================================================"
