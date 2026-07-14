#!/usr/bin/env bash
# Run the CDP Relay test suite.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "==================================="
echo " CDP Relay Server — Test Suite"
echo "==================================="
echo ""

# Grab the right Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: No Python found!"
    exit 1
fi

echo "Using: $($PYTHON --version)"
echo ""

# Ensure pytest is available
if ! $PYTHON -c "import pytest" 2>/dev/null; then
    echo "Installing pytest..."
    $PYTHON -m pip install pytest -q
    echo ""
fi

# Run tests
$PYTHON -m pytest tests/ -v --tb=short "$@"
