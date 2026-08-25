#!/usr/bin/env bash
set -e

JAR="scripts/synthea-with-dependencies.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar"

if [ ! -f "$JAR" ]; then
  echo "Downloading Synthea..."
  curl -L -o "$JAR" "$URL"
fi

java -jar "$JAR" -p 500 -s 42 \
  --exporter.clinical_note.export=true \
  --exporter.baseDirectory=./data/raw

echo "Done. Output in data/raw/fhir/ and data/raw/notes/"
