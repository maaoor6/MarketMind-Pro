#!/usr/bin/env bash
# MarketMind-Pro local security scan — the repeatable "security agent".
# Bundles: bandit (SAST) + gitleaks (secret scan) + pip-audit (dependency CVEs).
# Each tool degrades gracefully if not installed. Exit non-zero on any finding.
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
fail=0

echo -e "${YELLOW}🔒 MarketMind-Pro security scan${NC}"

# ── 1. Bandit (Python SAST) ───────────────────────────────────────────
echo -n "  Bandit (SAST)... "
if command -v bandit >/dev/null 2>&1; then
  if bandit -r src/ -ll -q >/tmp/mm_bandit.log 2>&1; then
    echo -e "${GREEN}✓${NC}"
  else
    echo -e "${RED}✗ findings${NC}"; cat /tmp/mm_bandit.log; fail=1
  fi
else
  echo -e "${YELLOW}skipped (not installed: pip install bandit)${NC}"
fi

# ── 2. Gitleaks (secret scan) ─────────────────────────────────────────
echo -n "  Gitleaks (secrets)... "
if command -v gitleaks >/dev/null 2>&1; then
  if gitleaks detect --config .gitleaks.toml --no-banner -r /tmp/mm_gitleaks.json >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
  else
    echo -e "${RED}✗ potential secrets — see /tmp/mm_gitleaks.json${NC}"; fail=1
  fi
else
  echo -e "${YELLOW}skipped (not installed: brew install gitleaks)${NC}"
fi

# ── 3. pip-audit (dependency CVEs) ────────────────────────────────────
echo -n "  pip-audit (deps)... "
if command -v pip-audit >/dev/null 2>&1; then
  if pip-audit -r requirements.txt >/tmp/mm_pipaudit.log 2>&1; then
    echo -e "${GREEN}✓${NC}"
  else
    echo -e "${RED}✗ vulnerable dependencies${NC}"; cat /tmp/mm_pipaudit.log; fail=1
  fi
else
  echo -e "${YELLOW}skipped (not installed: pip install pip-audit)${NC}"
fi

if [ "$fail" -eq 0 ]; then
  echo -e "${GREEN}✅ security scan clean${NC}"
else
  echo -e "${RED}❌ security scan found issues${NC}"
fi
exit "$fail"
