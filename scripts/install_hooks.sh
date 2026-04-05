#!/usr/bin/env bash
# Install Git pre-commit hooks for MarketMind-Pro
set -euo pipefail

HOOK_DIR="$(git rev-parse --show-toplevel)/.git/hooks"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing MarketMind-Pro Git hooks..."

cat > "$HOOK_DIR/pre-commit" << 'EOF'
#!/usr/bin/env bash
# MarketMind-Pro Pre-Commit Hook
# Runs: Ruff lint, Black format check, Bandit security scan, Unit tests
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🔍 MarketMind-Pro Pre-Commit Checks${NC}"

# ── 1. Ruff Linting ──────────────────────────────────────────────────
echo -n "  Running Ruff... "
if ruff check src/ tests/ --quiet; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED — fix lint errors before committing${NC}"
    exit 1
fi

# ── 2. Black Formatting ───────────────────────────────────────────────
echo -n "  Running Black (check)... "
if black --check src/ tests/ --quiet; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED — run 'black src/ tests/' to auto-format${NC}"
    exit 1
fi

# ── 3. Bandit Security Scan ───────────────────────────────────────────
echo -n "  Running Bandit... "
if bandit -r src/ -ll --quiet 2>/dev/null; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED — fix security issues (bandit -r src/ for details)${NC}"
    exit 1
fi

# ── 4. Unit Tests ─────────────────────────────────────────────────────
echo -n "  Running unit tests... "
if python -m pytest tests/unit/ -x -q --tb=line 2>/dev/null; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED — fix failing tests before committing${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All pre-commit checks passed!${NC}"
EOF

chmod +x "$HOOK_DIR/pre-commit"
echo "✅ Pre-commit hook installed at $HOOK_DIR/pre-commit"
