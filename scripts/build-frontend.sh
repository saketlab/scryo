#!/bin/bash
# Build the scryo frontend (modified embedding-atlas) and update bundled static files.
#
# Usage: ./scripts/build-frontend.sh
#
# Requirements:
#   - Node.js 22+ (Node 24 has Bus error issues with vite — use Node 22 LTS)
#   - npm 9+
#   - Rust + cargo (via rustup, NOT miniconda — must support wasm32-unknown-unknown target)
#   - wasm-bindgen-cli 0.2.114: cargo install -f wasm-bindgen-cli --version 0.2.114
#   - rustup target add wasm32-unknown-unknown
#
# This script:
#   1. Builds WASM modules (density-clustering, umap)
#   2. Builds TypeScript packages (utils, component)
#   3. Builds the Svelte viewer
#   4. Copies dist/ to src/scryo/static/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
STATIC_DIR="$REPO_ROOT/src/scryo/static"

# Ensure rustup toolchain is on PATH 
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
if [ -d "$HOME/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin" ]; then
  export PATH="$HOME/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin:$PATH"
fi

# Check dependencies
echo "Checking dependencies..."
command -v node >/dev/null || { echo "Error: node not found. Install Node.js 22 LTS."; exit 1; }
command -v npm >/dev/null || { echo "Error: npm not found."; exit 1; }
command -v cargo >/dev/null || { echo "Error: cargo not found. Install Rust via rustup."; exit 1; }
command -v wasm-bindgen >/dev/null || { echo "Error: wasm-bindgen not found. Run: cargo install -f wasm-bindgen-cli --version 0.2.114"; exit 1; }
rustup target list --installed 2>/dev/null | grep -q wasm32-unknown-unknown || { echo "Error: wasm32 target missing. Run: rustup target add wasm32-unknown-unknown"; exit 1; }

NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VERSION" -ge 23 ]; then
  echo "Warning: Node $NODE_VERSION may have build issues. Recommended: Node 22 LTS."
fi

cd "$FRONTEND_DIR"

# Install npm dependencies (--ignore-scripts avoids onnxruntime postinstall failures)
if [ ! -d "node_modules" ]; then
  echo "Installing npm dependencies (--ignore-scripts)..."
  npm install --ignore-scripts
fi

# Build WASM modules
echo "Building density-clustering WASM..."
(cd packages/density-clustering && npm run build)

echo "Building umap WASM..."
(cd packages/umap/umap-wasm && npm run build)

# Build TypeScript packages
echo "Building utils..."
(cd packages/utils && npm run package)

echo "Building component..."
(cd packages/component && npm run build)

echo "Building viewer..."
(cd packages/viewer && npm run build)

# Copy dist to scryo static
echo "Copying dist to $STATIC_DIR..."
rm -rf "$STATIC_DIR"
cp -r packages/viewer/dist "$STATIC_DIR"

echo "Done. Frontend rebuilt and copied to src/scryo/static/"
echo "To test: pip install -e . && scryo /path/to/data.rds"
