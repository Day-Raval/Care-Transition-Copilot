#!/usr/bin/env bash
set -e

JAR="scripts/synthea-with-dependencies.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar"

if [ ! -f "$JAR" ]; then
  echo "Downloading Synthea..."
  curl -L -o "$JAR" "$URL"
fi

POP_SIZE=$(python3 -c "import yaml; print(yaml.safe_load(open('config.yaml'))['synthea']['population_size'])")
SEED=$(python3 -c "import yaml; print(yaml.safe_load(open('config.yaml'))['synthea']['seed'])")

echo "Generating population_size=$POP_SIZE seed=$SEED (from config.yaml)"

# Clear stale output first — otherwise leftover files from a smaller prior
# run can sit alongside the new ones and confuse record counts.
rm -rf data/raw/fhir data/raw/notes data/raw/metadata

java -jar "$JAR" -p "$POP_SIZE" -s "$SEED" \
  --exporter.clinical_note.export=true \
  --exporter.baseDirectory=./data/raw

echo "Done. Output in data/raw/fhir/ and data/raw/notes/"