#!/usr/bin/env bash
# Assemble the static site into _site/: the web page plus the tested Python core and data it runs.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf _site && mkdir -p _site/app _site/data _site/prism
cp site/index.html site/styles.css site/config.js site/app.js site/bridge.py _site/
cp app/brand.py app/rules.py app/drafter.py app/ninoo.py app/run_day.py app/assistant.py _site/app/
cp data/*.json _site/data/
cp prism/prism_labeled_set.csv _site/prism/
touch _site/.nojekyll
echo "Built _site/ — preview with: python -m http.server -d _site 8000"
