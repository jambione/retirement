#!/usr/bin/env bash
# install_agents.sh — install the web + nightly-scan LaunchAgents on the mini.
#
# Same shape and the same reasoning as trading-helper's install_desk_agent.sh:
# bootstrapping into gui/<uid> puts the jobs inside the console user's session,
# which is both what makes the Keychain readable and what lets deploy_mini.sh
# kickstart them over ssh.
#
#   scripts/install_agents.sh           install / re-install both
#   scripts/install_agents.sh --status   are they loaded?
#   scripts/install_agents.sh --remove   unload and delete both
set -uo pipefail

REPO="${REPO:-/Users/jambimac/repo/retirement}"
LABELS=(com.jambi.retirement-web com.jambi.retirement-scan)
DOMAIN="gui/$(id -u)"

if [ "$(id -un 2>/dev/null || true)" != "jambimac" ]; then
  echo "❌ Mini-only (user jambimac). This machine is $(id -un)."
  echo "   Deploy from the MacBook with scripts/deploy_mini.sh instead."
  exit 1
fi

case "${1:-install}" in
  --status)
    rc=0
    for label in "${LABELS[@]}"; do
      if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
        echo "✓ $label loaded"
      else
        echo "✗ $label NOT loaded"; rc=1
      fi
    done
    exit $rc
    ;;
  --remove)
    for label in "${LABELS[@]}"; do
      launchctl bootout "$DOMAIN/$label" 2>/dev/null
      rm -f "$HOME/Library/LaunchAgents/$label.plist"
      echo "✓ removed $label"
    done
    exit 0
    ;;
esac

# gui/<uid> is unreachable when nobody is logged in at the console. Say so
# rather than installing something inert.
if ! launchctl print "$DOMAIN" >/dev/null 2>&1; then
  echo "❌ $DOMAIN is not reachable — is anyone logged in at the mini's screen?"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs" "$REPO/var"

for label in "${LABELS[@]}"; do
  src="$REPO/scripts/$label.plist"
  dest="$HOME/Library/LaunchAgents/$label.plist"
  [ -f "$src" ] || { echo "❌ missing $src"; exit 1; }
  cp "$src" "$dest"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null   # so a re-install picks up edits
  if launchctl bootstrap "$DOMAIN" "$dest" 2>&1; then
    echo "✓ $label bootstrapped into $DOMAIN"
  else
    echo "❌ bootstrap failed for $label"; exit 1
  fi
done

echo ""
echo "Web UI:      http://localhost:8891"
echo "Nightly scan: 07:15 daily (launchctl kickstart $DOMAIN/com.jambi.retirement-scan to test)"
echo "Public URL:   route the tunnel first — see scripts/cloudflare_setup.md"
