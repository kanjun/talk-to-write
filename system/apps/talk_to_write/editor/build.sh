#!/usr/bin/env bash
# Rebuilds ../src/talk_to_write/static/editor.js from editor.js. The built file is what the app serves.
set -euo pipefail
cd "$(dirname "$0")"
npm install --silent --no-audit --no-fund
npx esbuild editor.js --bundle --minify --format=iife --global-name=TTWEditor --outfile=../src/talk_to_write/static/editor.js
