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
TUNNEL_NAME="${TUNNEL_NAME:-retirement}"   # our own tunnel, not the trading desk's
HOSTNAME_PUBLIC="${HOSTNAME_PUBLIC:-retirement.jbrasfield.com}"
PORT="${RETIREMENT_PORT:-8891}"
# Clone the mini from whatever THIS checkout uses, rather than assuming SSH --
# guessing git@github.com when the working setup is HTTPS fails on a machine
# with no deploy key, which is exactly the machine a first run lands on.
GIT_URL="${GIT_URL:-$(git -C "$(cd "$(dirname "$0")/.." && pwd)" remote get-url origin 2>/dev/null || echo https://github.com/jambione/retirement.git)}"
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

# What is actually missing? "The repo is there" is not the same as "it is set
# up" -- a clone with no .venv and no LaunchAgents serves nothing, and keying
# the whole bootstrap off .git existing skipped both.
NEEDS_CLONE=0; NEEDS_SETUP=0; NEEDS_AGENTS=0
ssh_mini "test -d '$MINI_REPO/.git'" 2>/dev/null || NEEDS_CLONE=1
if [ "$NEEDS_CLONE" = 0 ]; then
  # A venv left behind by a FAILED install still has a working python binary in
  # it, so testing for the binary answered yes and setup was skipped -- leaving
  # the service unable to start and the tunnel serving 502s. Ask the only
  # question that matters: can that interpreter import what we need?
  ssh_mini "'$MINI_REPO/.venv/bin/python' -c 'import retirement, fastapi, uvicorn'" \
    2>/dev/null || NEEDS_SETUP=1
fi
ssh_mini "launchctl print gui/\$(id -u)/com.jambi.retirement-web >/dev/null 2>&1 && \
          launchctl print gui/\$(id -u)/com.jambi.retirement-tunnel >/dev/null 2>&1" \
  2>/dev/null || NEEDS_AGENTS=1

if [ "$NEEDS_CLONE"  = 1 ]; then echo "     repo not there yet"; fi
if [ "$NEEDS_SETUP"  = 1 ]; then echo "     cloned but never set up — no virtualenv"; fi
if [ "$NEEDS_AGENTS" = 1 ]; then echo "     LaunchAgents not installed"; fi

if [ "$CHECK_ONLY" = 1 ]; then
  say "State"
  if [ "$NEEDS_CLONE" = 0 ]; then
    ssh_mini "cd '$MINI_REPO' && git log --oneline -1 && ./retire status" || true
  else
    echo "  not deployed"
  fi
  ssh_mini "grep -q '$HOSTNAME_PUBLIC' '$MINI_REPO/config/cloudflared-config.yml'" 2>/dev/null \
    && ok "own tunnel configured for $HOSTNAME_PUBLIC" \
    || no "tunnel not configured yet"
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

# ── 2. code onto the mini ──────────────────────────────────────────────────
say "Deploy"
if [ "$NEEDS_CLONE" = 1 ]; then
  ssh_mini "bash -s" <<REMOTE
set -euo pipefail
mkdir -p "\$(dirname '$MINI_REPO')"
git clone '$GIT_URL' '$MINI_REPO'
REMOTE
  ok "cloned from $GIT_URL"
  NEEDS_SETUP=1
else
  ./scripts/deploy_mini.sh --no-push --pull-only
fi

# ── 2b. set up, install agents, start ──────────────────────────────────────
# Each step is conditional and reported, so a rerun after a failure does the
# one thing that is still missing rather than everything again.
if [ "$NEEDS_SETUP" = 1 ]; then
  say "Virtualenv"
  # Start clean: a half-installed venv built against the wrong interpreter
  # cannot be repaired by installing into it again.
  ssh_mini "cd '$MINI_REPO' && rm -rf .venv && ./retire setup" || {
    no "setup failed — the service cannot start until this is fixed"
    exit 1
  }
  ok "dependencies installed"
fi

say "Service"
# Always re-bootstrap rather than only when missing: a changed plist (a new
# shell, a new argument) is invisible to "is it loaded", and install_agents.sh
# is bootout-then-bootstrap, so this is the same restart the deploy performs
# anyway.
if true; then
  if ssh_mini "cd '$MINI_REPO' && scripts/install_agents.sh"; then
    ok "LaunchAgents bootstrapped (web, 07:15 cycle, own tunnel)"
  else
    # gui/<uid> is unreachable when nobody is logged in at the mini's screen.
    # Start it in the foreground anyway so the tunnel has something to hit;
    # the agents can be installed later from a session on the machine.
    no "could not bootstrap the LaunchAgents (is anyone logged in at the mini?)"
    echo "     starting the service directly so the site works now"
    ssh_mini "cd '$MINI_REPO' && ./retire restart"
  fi
else
  ssh_mini "launchctl kickstart -k gui/\$(id -u)/com.jambi.retirement-web" >/dev/null 2>&1 \
    && ok "restarted through the LaunchAgent" \
    || ssh_mini "cd '$MINI_REPO' && ./retire restart"
fi

# ── 3. cloudflare ──────────────────────────────────────────────────────────
# This project gets its OWN tunnel. The trading desk's ingress list is a file
# tracked in its repo; adding a hostname there breaks that repo's pull and
# couples two unrelated projects' restarts. See scripts/tunnel_setup.sh.
if [ "$DO_CLOUDFLARE" = 1 ]; then
  say "Tunnel"
  ssh_mini "cd '$MINI_REPO' && HOSTNAME_PUBLIC='$HOSTNAME_PUBLIC' \
            RETIREMENT_PORT='$PORT' TUNNEL_NAME='$TUNNEL_NAME' scripts/tunnel_setup.sh" || {
    no "tunnel setup did not finish — see above"
    echo "     Everything else is deployed; rerun once that is sorted."
  }
  ssh_mini "launchctl kickstart -k gui/\$(id -u)/com.jambi.retirement-tunnel" >/dev/null 2>&1 \
    && ok "tunnel process restarted" || true
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

# ── 3.6 ai backend ─────────────────────────────────────────────────────────
# Run this through a LOGIN shell. agy, claude and grok are installed by npm or
# Homebrew, and a plain `ssh host command` sees almost none of that -- the last
# deploy reported every backend missing on a machine where they all work from a
# Terminal. The LaunchAgents use `bash -lc` for exactly this reason, so checking
# any other way measures the wrong shell.
say "AI"
ssh_mini "MINI_REPO='$MINI_REPO' bash -l -s" <<'REMOTE'
set -uo pipefail
ENV_FILE="$MINI_REPO/.env"
[ -f "$ENV_FILE" ] || cp "$MINI_REPO/.env.example" "$ENV_FILE"

# An .env copied from the example has `ASK_DEFAULT_PROVIDER=` with NOTHING after
# it. A plain grep for the key finds that line and concludes it is configured,
# which is how the last deploy left the backend unset while reporting success.
# Treat an empty value as absent.
set_env() {
  key="$1"; val="$2"
  cur="$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
  if [ -n "$cur" ]; then echo "  · $key already set ($cur)"; return 0; fi
  grep -v "^${key}=" "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null && mv "$ENV_FILE.tmp" "$ENV_FILE"
  printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  echo "  ✓ $key=$val"
}

# Pin ABSOLUTE paths. Then it does not matter which shell asks later -- launchd,
# ssh, or you -- they all find the same binary.
FOUND_AGY=""
for entry in "agy:ASK_AGY_BIN" "claude:ASK_CLAUDE_BIN" "grok:ASK_GROK_BIN"; do
  name="${entry%%:*}"; var="${entry##*:}"
  path="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$path" ]; then
    set_env "$var" "$path"
    [ "$name" = "agy" ] && FOUND_AGY="$path"
  else
    echo "  · $name not on the login PATH"
  fi
done

if [ -n "$FOUND_AGY" ]; then
  set_env ASK_DEFAULT_PROVIDER agy
else
  echo "  ✗ agy is not installed for this user."
  echo "    Install it, then rerun. Until then scoring falls back to whatever"
  echo "    else is present, which is not the subscription you asked for."
fi
REMOTE

# Report what the SERVICE will see, not what an ssh command sees.
ssh_mini "bash -lc \"cd '$MINI_REPO' && ./retire doctor\" 2>/dev/null | grep -E '^(ai|scoring)|^ +(!|default)'" || true

# ── 4. health ──────────────────────────────────────────────────────────────
say "Health"
ssh_mini "cd '$MINI_REPO' && ./retire status" || true
sleep 3
# When the origin is down the tunnel serves 502s and the useful evidence is in
# the service log, not in this script's output.
if ! ssh_mini "curl -sf --max-time 5 http://127.0.0.1:$PORT/healthz >/dev/null"; then
  no "the app is not answering on the mini — last lines of logs/web.log:"
  ssh_mini "tail -15 '$MINI_REPO/logs/web.log' 2>/dev/null | sed 's/^/      /'" || true
fi
if curl -sI --max-time 8 "https://$HOSTNAME_PUBLIC/healthz" >/dev/null 2>&1; then
  ok "https://$HOSTNAME_PUBLIC answers"
else
  no "https://$HOSTNAME_PUBLIC not answering — last lines of logs/tunnel.log:"
  ssh_mini "tail -12 '$MINI_REPO/logs/tunnel.log' 2>/dev/null | sed 's/^/      /'" || true
  echo "      (Cloudflare 1033 means no cloudflared is connected for the tunnel;"
  echo "       502 means it is connected but the app behind it is down.)"
fi
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
