#!/usr/bin/env bash
# run_tunnel.sh — what the LaunchAgent execs.
#
# This exists because the agent used to resolve cloudflared inside the plist
# with `$(command -v cloudflared || echo /opt/homebrew/bin/cloudflared)`, which
# is a PATH lookup wearing a fallback. A one-liner in XML is also the worst
# possible place to debug: when it fails you get error 1033 and no reason.
#
# Here the search is explicit, the choice is logged, and every failure says
# which thing was missing.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/config/cloudflared-config.yml"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

CFD=""
for candidate in \
  "${CLOUDFLARED_BIN:-}" \
  /opt/homebrew/bin/cloudflared \
  /usr/local/bin/cloudflared \
  /opt/local/bin/cloudflared \
  "$HOME/.local/bin/cloudflared" \
  "$(command -v cloudflared 2>/dev/null || true)"
do
  [ -n "$candidate" ] && [ -x "$candidate" ] && { CFD="$candidate"; break; }
done

if [ -z "$CFD" ]; then
  log "FATAL: cloudflared not found. Looked in /opt/homebrew/bin, /usr/local/bin,"
  log "       /opt/local/bin, ~/.local/bin and PATH. Install it (brew install"
  log "       cloudflared) or set CLOUDFLARED_BIN."
  exit 1
fi

if [ ! -f "$CFG" ]; then
  log "FATAL: no $CFG."
  log "       This project's tunnel has not been set up on this machine yet:"
  log "       run scripts/tunnel_setup.sh (the deploy does it for you)."
  exit 1
fi

TUNNEL_ID="$(grep '^tunnel:' "$CFG" | awk '{print $2}')"

# Two processes serving ONE tunnel id is not a second copy for redundancy: the
# Cloudflare edge evicts the older one, closing every connection at once with
# "Application error 0x0 (remote)" followed by "no more connections active and
# exiting". That is an eviction, not a crash, and it reads like neither.
OTHERS="$(pgrep -fl "cloudflared.*$TUNNEL_ID" 2>/dev/null | grep -v "^$$ " || true)"
if [ -n "$OTHERS" ]; then
  log "WARNING: something else is already serving tunnel $TUNNEL_ID:"
  printf '%s\n' "$OTHERS" | while IFS= read -r line; do log "  $line"; done
  log "  The edge keeps only the newest connection, so these two will evict"
  log "  each other in a loop. Stop one of them."
fi

log "starting $CFD"
log "  config $CFG"
log "  tunnel $TUNNEL_ID"
log "  url    ${HOSTNAME_PUBLIC:-retirement.jbrasfield.com}"
exec "$CFD" --config "$CFG" --no-autoupdate tunnel run
