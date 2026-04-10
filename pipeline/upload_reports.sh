#!/bin/bash

BATCH="$1"

if [[ -z "$BATCH" ]]; then
  echo "Usage: ./upload_reports.sh <batch_name>"
  exit 1
fi


BASE_ROOT="${WORK_DIR:-/Users/pnovikova/Documents/work}/analysis"
BASE_DIR="$BASE_ROOT/$BATCH"

if [[ -z "$NB1_TOKEN" ]]; then
  echo "NB1_TOKEN is not set"
  exit 1
fi

cd "$BASE_DIR" || exit 1

for sample in */; do
  sample=${sample%/}

  JSON_DIR="$BASE_DIR/$sample/reports/reports_json"
  PDF_DIR="$BASE_DIR/$sample/reports/reports_pdf"

  REPORT_JSON="$JSON_DIR/microbiome_platform_${sample}.json"
  GUIDE_JSON="$JSON_DIR/formulation_platform_${sample}.json"
  GUIDE_PDF="$PDF_DIR/manufacturing_recipe_${sample}.pdf"
  REPORT_PDF="$PDF_DIR/narrative_report_${sample}.pdf"

  if [[ -f "$REPORT_JSON" && -f "$GUIDE_JSON" && -f "$GUIDE_PDF" && -f "$REPORT_PDF" ]]; then
    echo "Uploading $sample (batch: $BATCH)"

    # report_json: send as string value (not file upload) using < prefix
    # report_pdf: send as file upload using @ prefix
    curl -s -X PATCH "https://api.nb1.com/lab/report-data" \
      -H "accept: application/json" \
      -H "Authorization: Bearer $NB1_TOKEN" \
      -F "kit_number=${sample}" \
      -F "report_json=<${REPORT_JSON}" \
      -F "report_pdf=@${REPORT_PDF};type=application/pdf" \
      -F "guide_json=<${GUIDE_JSON}" \
      -F "guide_pdf=@${GUIDE_PDF};type=application/pdf"

    echo ""
  else
    echo "Missing files for $sample — skipping"
  fi
done