#!/usr/bin/env bash
# deploy_mini.sh — ship local retirement changes to the Mac mini and reload.
#
# Same loop as trading-helper: edit here on the MacBook, commit, run this.
#   ./scripts/deploy_mini.sh              push + pull on mini + restart web
#   ./scripts/deploy_mini.sh --no-push    mini pull + restart only
#   ./scripts/deploy_mini.sh --pull-only  pull on mini, no restart
#   ./scripts/deploy_mini.sh --status     remote status + health only
#
# Config-only edits (config/*.yaml) can use --pull-only: the pipeline reads the
# YAML at the start of every run and the web UI reads it per request, so a
# restart buys nothing.
#
# Env overrides:
#   MINI_SSH   default: jambimac@Jonathans-Mac-mini.local
#   MINI_REPO  default: /Users/jambimac/repo/retirement
set -euo pipefail

MINI_SSH="${MINI_SSH:-jambimac@Jonathans-Mac-mini.local}"
MINI_REPO="${MINI_REPO:-/Users/jambimac/repo/retirement}"
PORT="${RETIREMENT_PORT:-8891}"
PUBLIC_URL="https://retirement.jbrasfield.com"

DO_PUSH=1; DO_RESTART=1; STATUS_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-push)   DO_PUSH=0 ;;
    --pull-only) DO_RESTART=0 ;;
    --status)    STATUS_ONLY=1; DO_PUSH=0; DO_RESTART=0 ;;
    -h|--help)   sed -n '2,18p' "$0"; exit 0 ;;
    *)           echo "Unknown flag: $arg (try --help)"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

ssh_mini() {
  ssh -o BatchMode=yes -o ConnectTimeout=12 \
    -o IdentitiesOnly=yes -i "${HOME}/.ssh/id_ed25519" "$MINI_SSH" "$@"
}

echo ""
echo "============================================"
echo "  Deploy → Mac mini"
echo "  local : $ROOT"
echo "  remote: $MINI_SSH:$MINI_REPO"
echo "============================================"

if ! ssh_mini "test -d '$MINI_REPO/.git'"; then
  echo "❌ Cannot reach mini or repo missing at $MINI_REPO"
  echo "   First time? On the mini:"
  echo "     git clone git@github.com:jambione/retirement.git ~/repo/retirement"
  echo "     cd ~/repo/retirement && ./retire setup && scripts/install_agents.sh"
  exit 1
fi
echo "✓ SSH + remote repo OK"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "✓ Local branch: $BRANCH"
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠ Working tree is dirty. Uncommitted changes will NOT deploy."
  git status -sb | head -n 20
fi

if [ "$STATUS_ONLY" = 1 ]; then
  ssh_mini "cd '$MINI_REPO' && git log --oneline -3 && ./retire status"
  exit 0
fi

if [ "$DO_PUSH" = 1 ]; then
  echo "[1/4] git push"
  git fetch origin
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    BEHIND="$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
    if [ "${BEHIND:-0}" -gt 0 ]; then
      echo "❌ Local is behind origin by $BEHIND commit(s). Rebase first."; exit 1
    fi
    AHEAD="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    if [ "${AHEAD:-0}" -gt 0 ]; then git push; echo "   ✓ pushed"
    else echo "   • nothing to push"; fi
  else
    git push -u origin HEAD
  fi
else
  echo "[1/4] skip push (--no-push)"
fi

echo "[2/4] git pull --ff-only on mini ($BRANCH)"
ssh_mini "bash -s" <<REMOTE
set -euo pipefail
cd '$MINI_REPO'
git fetch origin
if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
  git checkout "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  git pull --ff-only
fi
echo "   HEAD: \$(git log -1 --oneline)"
REMOTE

# Only reinstall when the dependency set actually moved -- `uv pip install -e`
# on every deploy adds twenty seconds for nothing on a plain code edit.
echo "[3/4] dependencies"
if ssh_mini "cd '$MINI_REPO' && git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q pyproject.toml"; then
  ssh_mini "cd '$MINI_REPO' && ./retire setup"
else
  echo "   • pyproject unchanged, skipping reinstall"
fi

if [ "$DO_RESTART" = 0 ]; then
  echo "[4/4] skip restart (--pull-only)"
elif ssh_mini "launchctl print gui/\$(id -u)/com.jambi.retirement-web >/dev/null 2>&1"; then
  # Restart THROUGH the LaunchAgent, for the same reason the trading desk does:
  # a job in gui/<uid> runs inside the console session and can read the login
  # Keychain, so anything credential-backed survives an ssh deploy.
  echo "[4/4] launchctl kickstart (GUI session)"
  ssh_mini "launchctl kickstart -k gui/\$(id -u)/com.jambi.retirement-web"
  sleep 2
else
  echo "[4/4] ./retire restart"
  echo "   (run scripts/install_agents.sh on the mini to get the LaunchAgent path)"
  ssh_mini "cd '$MINI_REPO' && ./retire restart"
fi

echo ""
echo "Health (on mini):"
ssh_mini "cd '$MINI_REPO' && ./retire status"
echo ""
echo "Done.  local: http://localhost:$PORT   public: $PUBLIC_URL"
