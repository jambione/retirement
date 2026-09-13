#!/usr/bin/env bash
# ship.sh — push, deploy to the mini, and put it behind retirement.jbrasfield.com.
#
# One command for the whole path. Everything in it is idempotent: run it again
# after a failure and it picks up where it stopped.
#
#   ./scripts/ship.sh                first run and every run after
#   ./scripts/ship.sh --skip-cloudflare   deploy only, leave the tunnel alone
#   ./scripts/ship.sh --check             report state, change nothing
#
# What it will NOT do without asking: overwrite an existing ingress rule, or
# reload cloudflared with a config that does not validate. The tunnel it edits
# is the one already serving trading.jbrasfield.com, so every change to that
# file is backed up, validated, and rolled back if validation fails.
set -euo pipefail

MINI_SSH="${MINI_SSH:-jambimac@Jonathans-Mac-mini.local}"
MINI_REPO="${MINI_REPO:-/Users/jambimac/repo/retirement}"
TUNNEL="${TUNNEL:-56c84116-0ef0-47c7-bbea-25634d765487}"
HOSTNAME_PUBLIC="${HOSTNAME_PUBLIC:-retirement.jbrasfield.com}"
PORT="${RETIREMENT_PORT:-8891}"
GIT_URL="${GIT_URL:-git@github.com:jambione/retirement.git}"
TRADING_SECRETS="${TRADING_SECRETS:-/Users/jambimac/repo/trading-helper/config/secrets.json}"

DO_CLOUDFLARE=1
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-cloudflare) DO_CLOUDFLARE=0 ;;
    --check)           CHECK_ONLY=1 ;;
    -h|--help)         sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ssh_mini() { ssh -o BatchMode=yes -o ConnectTimeout=12 "$MINI_SSH" "$@"; }
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
no()  { printf '  ✗ %s\n' "$*"; }

# ── 0. can we reach the mini at all ────────────────────────────────────────
say "Mini"
if ! ssh_mini true 2>/dev/null; then
  no "cannot ssh to $MINI_SSH"
  echo "     Check it is awake and on the network, and that your key is authorized:"
  echo "       ssh $MINI_SSH"
  exit 1
fi
ok "ssh to $MINI_SSH"

FIRST_RUN=0
ssh_mini "test -d '$MINI_REPO/.git'" || FIRST_RUN=1
[ "$FIRST_RUN" = 1 ] && echo "     repo not there yet — this is a first run"

if [ "$CHECK_ONLY" = 1 ]; then
  say "State"
  [ "$FIRST_RUN" = 0 ] && ssh_mini "cd '$MINI_REPO' && git log --oneline -1 && ./retire status" || echo "  not deployed"
  ssh_mini "grep -q '$HOSTNAME_PUBLIC' ~/.cloudflared/config.yml" 2>/dev/null \
    && ok "ingress rule present" || no "no ingress rule for $HOSTNAME_PUBLIC"
  curl -sI --max-time 6 "https://$HOSTNAME_PUBLIC/healthz" >/dev/null 2>&1 \
    && ok "$HOSTNAME_PUBLIC answers" || no "$HOSTNAME_PUBLIC does not answer yet"
  exit 0
fi

# ── 1. push ────────────────────────────────────────────────────────────────
say "Push"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  no "working tree is dirty — commit first, or those changes will not ship"
  git status -sb | head -10
  exit 1
fi
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git push
else
  git push -u origin "$BRANCH"
fi
ok "pushed $BRANCH"

# ── 2. deploy ──────────────────────────────────────────────────────────────
if [ "$FIRST_RUN" = 1 ]; then
  say "First deploy"
  ssh_mini "bash -s" <<REMOTE
set -euo pipefail
mkdir -p "\$(dirname '$MINI_REPO')"
git clone '$GIT_URL' '$MINI_REPO'
cd '$MINI_REPO'
./retire setup
scripts/install_agents.sh
REMOTE
  ok "cloned, installed, LaunchAgents bootstrapped"
else
  say "Deploy"
  ./scripts/deploy_mini.sh --no-push
fi

# ── 3. cloudflare ──────────────────────────────────────────────────────────
if [ "$DO_CLOUDFLARE" = 1 ]; then
  say "Cloudflare"

  # DNS: idempotent. Already-exists is a success, not a failure.
  if ssh_mini "cloudflared tunnel route dns '$TUNNEL' '$HOSTNAME_PUBLIC'" 2>&1 | tail -1; then
    ok "DNS route for $HOSTNAME_PUBLIC"
  else
    ok "DNS route already in place (or reported as existing)"
  fi

  # Ingress: back up, insert above the catch-all, validate, reload, roll back
  # on failure. This file is what keeps trading.jbrasfield.com up.
  # Ingress: copy the editor up, back up, edit, validate, reload — rolling
  # back if the config does not validate. This file is what keeps
  # trading.jbrasfield.com up, so nothing here is done in place and unchecked.
  scp -q scripts/cloudflare_ingress.py "$MINI_SSH:/tmp/cloudflare_ingress.py"
  ssh_mini "HOSTNAME_PUBLIC='$HOSTNAME_PUBLIC' PORT='$PORT' bash -s" <<'REMOTE'
set -uo pipefail
CFG="$HOME/.cloudflared/config.yml"
[ -f "$CFG" ] || { echo "  ✗ no $CFG — is this the machine running the tunnel?"; exit 1; }

BACKUP="$CFG.$(date +%Y%m%d-%H%M%S).bak"
cp "$CFG" "$BACKUP"

python3 /tmp/cloudflare_ingress.py "$CFG" "$HOSTNAME_PUBLIC" "$PORT"
STATUS=$?
if [ "$STATUS" = 2 ]; then rm -f "$BACKUP"; exit 0; fi          # already there
if [ "$STATUS" != 0 ]; then cp "$BACKUP" "$CFG"; exit 1; fi     # refused
echo "  · backed up to $BACKUP"

if command -v cloudflared >/dev/null 2>&1; then
  if cloudflared --config "$CFG" tunnel ingress validate; then
    echo "  ✓ config validates"
  else
    cp "$BACKUP" "$CFG"
    echo "  ✗ config did NOT validate — rolled back, tunnel untouched"
    exit 1
  fi
fi

PID="$(pgrep -f 'cloudflared.*tunnel.*run' | head -1)"
if [ -n "$PID" ]; then
  kill -HUP "$PID"
  echo "  ✓ reloaded cloudflared (pid $PID) — trading dashboard not interrupted"
else
  echo "  ! cloudflared is not running; start it and the new rule takes effect"
fi
REMOTE
fi

# ── 3.5 email ──────────────────────────────────────────────────────────────
# Same credentials as the trading desk, by pointing at its secrets file rather
# than keeping a second copy of an iCloud app-specific password in sync.
say "Email"
ssh_mini "TRADING_SECRETS='$TRADING_SECRETS' MINI_REPO='$MINI_REPO' bash -s" <<'REMOTE'
set -uo pipefail
ENV_FILE="$MINI_REPO/.env"
[ -f "$ENV_FILE" ] || cp "$MINI_REPO/.env.example" "$ENV_FILE"

if grep -q '^RETIREMENT_SECRETS=' "$ENV_FILE"; then
  echo "  ✓ already pointed at $(grep '^RETIREMENT_SECRETS=' "$ENV_FILE" | cut -d= -f2-)"
  exit 0
fi
if [ -s "$MINI_REPO/config/secrets.json" ]; then
  echo "  ✓ this project has its own config/secrets.json — leaving it alone"
  exit 0
fi
if [ ! -f "$TRADING_SECRETS" ]; then
  echo "  ! no $TRADING_SECRETS on this machine — nothing to point at"
  exit 0
fi
if ! grep -q '"smtp_host"' "$TRADING_SECRETS"; then
  echo "  ! $TRADING_SECRETS has no smtp_host block, so there is nothing to share."
  echo "    Add the smtp_* keys there (the trading desk needs them too), or give"
  echo "    this project its own config/secrets.json."
  exit 0
fi

printf '
RETIREMENT_SECRETS=%s
' "$TRADING_SECRETS" >> "$ENV_FILE"
echo "  ✓ using the trading desk's credentials ($TRADING_SECRETS)"
echo "    Digests go to its notify_to unless DIGEST_TO is set in $ENV_FILE."
REMOTE

# ── 4. health ──────────────────────────────────────────────────────────────
say "Health"
ssh_mini "cd '$MINI_REPO' && ./retire status" || true
sleep 3
curl -sI --max-time 8 "https://$HOSTNAME_PUBLIC/healthz" >/dev/null 2>&1 \
  && ok "https://$HOSTNAME_PUBLIC answers" \
  || no "https://$HOSTNAME_PUBLIC not answering yet (DNS can take a minute)"
curl -sI --max-time 8 "https://trading.jbrasfield.com" >/dev/null 2>&1 \
  && ok "trading.jbrasfield.com still fine" \
  || no "trading.jbrasfield.com not answering — check the tunnel"

# Is anything in front of it? A Cloudflare Access app answers with a redirect
# to <team>.cloudflareaccess.com; the bare app answers 200. This is a report,
# not a gate -- but an open URL and a finance page are worth saying out loud.
HEADERS="$(curl -sI --max-time 8 "https://$HOSTNAME_PUBLIC/" 2>/dev/null || true)"
if printf '%s' "$HEADERS" | grep -qi 'cloudflareaccess.com'; then
  ok "Cloudflare Access is in front of it"
elif printf '%s' "$HEADERS" | grep -qi '^HTTP/.* 200'; then
  printf '  \033[33m!\033[0m %s\n' "NO AUTH: anyone with the URL can read this site,"
  echo "    including /finance — account names, balances and net worth."
  echo "    Turn it on:  Zero Trust > Access > Applications > Add > Self-hosted"
  echo "                 domain $HOSTNAME_PUBLIC, policy: your two email addresses"
  echo "    Or hold the balance sheet back until then:"
  echo "                 set modules.finance.enabled to false in config/profile.yaml"
fi

# ── 5. what still needs you ────────────────────────────────────────────────
say "Still on you"
ssh_mini "cd '$MINI_REPO' && ./retire doctor 2>/dev/null | grep '^email'" || true
ssh_mini "grep -q '^IDEALISTA_API_KEY=.\\+' '$MINI_REPO/.env'" 2>/dev/null \
  && ok "Idealista key set" \
  || echo "  · no Idealista key in .env — scans run on manual listings only"
echo "  · Cloudflare Access on $HOSTNAME_PUBLIC — there is no login in this app.
    Zero Trust > Access > Applications > Add > Self-hosted, policy: your two
    email addresses. Check it in a private window before you leave it up."
echo ""
