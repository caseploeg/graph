#!/bin/bash
# Test script for batch JSON graph generation
# Clones 5 popular repos and runs the batch processor
#
# Usage: ./test_batch.sh [upload_destination]
#   upload_destination: Optional GCS path (gs://bucket/prefix) or local path

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TEST_DIR="$SCRIPT_DIR/test_repos"
OUTPUT_DIR="$SCRIPT_DIR/test_output"
REPO_LIST="$SCRIPT_DIR/test_repos.txt"
UPLOAD_TO="${1:-}"

echo "=============================================="
echo "Batch Processing Test Script"
echo "=============================================="
echo "Test repos directory: $TEST_DIR"
echo "Output directory: $OUTPUT_DIR"
if [ -n "$UPLOAD_TO" ]; then
    echo "Upload destination: $UPLOAD_TO"
fi
echo ""

# Popular repos to clone (small to medium sized for testing)
REPOS=(
    "https://github.com/pallets/click"           # Python CLI framework (~15k LOC)
    "https://github.com/sindresorhus/got"        # Node.js HTTP client (~5k LOC)
    "https://github.com/rust-lang/log"           # Rust logging crate (~3k LOC)
    "https://github.com/google/go-cmp"           # Go comparison library (~5k LOC)
    "https://github.com/square/okio"             # Java I/O library (~20k LOC)
    "https://github.com/django/django"
)

# Create directories
mkdir -p "$TEST_DIR"
mkdir -p "$OUTPUT_DIR"

# Clone repos if they don't exist
echo "Step 1: Cloning test repositories..."
echo "----------------------------------------------"

for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$TEST_DIR/$repo_name"

    if [ -d "$repo_path" ]; then
        echo "  [SKIP] $repo_name (already exists)"
    else
        echo "  [CLONE] $repo_name..."
        git clone --depth 1 "$repo_url" "$repo_path" 2>/dev/null || {
            echo "    Warning: Failed to clone $repo_name"
            continue
        }
        echo "    Done."
    fi
done

# Generate repo list file
echo ""
echo "Step 2: Generating repo list..."
echo "----------------------------------------------"

> "$REPO_LIST"
for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$TEST_DIR/$repo_name"
    if [ -d "$repo_path" ]; then
        echo "$repo_path" >> "$REPO_LIST"
        echo "  Added: $repo_name"
    fi
done

echo ""
echo "Repo list written to: $REPO_LIST"
cat "$REPO_LIST"

# Run batch processor
echo ""
echo "Step 3: Running batch processor..."
echo "----------------------------------------------"

cd "$PROJECT_ROOT"

# Determine number of workers (use half of available CPUs, min 2, max 4)
WORKERS=$(python3 -c "import os; print(max(2, min(4, os.cpu_count() // 2)))")
echo "Using $WORKERS workers"
echo ""

UPLOAD_ARG=""
if [ -n "$UPLOAD_TO" ]; then
    UPLOAD_ARG="--upload-to $UPLOAD_TO"
fi

uv run python -m batch.batch_processor "$REPO_LIST" "$OUTPUT_DIR" --workers "$WORKERS" $UPLOAD_ARG

# Show results
echo ""
echo "Step 4: Results"
echo "----------------------------------------------"
echo "Output files:"
ls -lh "$OUTPUT_DIR"/*.json 2>/dev/null || echo "  (no JSON files found)"

echo ""
echo "Summary file:"
if [ -f "$OUTPUT_DIR/_batch_summary.json" ]; then
    python3 -c "
import json
with open('$OUTPUT_DIR/_batch_summary.json') as f:
    data = json.load(f)
print(f\"  Total repos: {data['total_repos']}\")
print(f\"  Successful: {data['successful']}\")
print(f\"  Failed: {data['failed']}\")
print(f\"  Total time: {data['total_time_seconds']:.1f}s\")
"
fi

echo ""
echo "=============================================="
echo "Test complete!"
echo "=============================================="
echo ""
echo "To view a graph summary, run:"
echo "  cgr graph-loader $OUTPUT_DIR/<repo_name>.json"
echo ""
echo "To clean up test files:"
echo "  rm -rf $TEST_DIR $OUTPUT_DIR $REPO_LIST"
