# retirement.jbrasfield.com

This project runs **its own cloudflared tunnel**, separate from the one serving
`trading.jbrasfield.com`. `scripts/tunnel_setup.sh` does the whole thing and
`scripts/ship.sh` calls it, so there is normally nothing to do by hand.

## Why not just add a hostname to the existing tunnel

One tunnel can serve many hostnames, and that was the first design here. It was
wrong for this machine specifically: the trading tunnel's ingress list lives at
`trading-helper/config/cloudflared-config.yml`, a file **tracked in that repo**.
Adding a hostname to it leaves that working tree dirty, so the next
`deploy_mini.sh` for trading fails its `git pull --ff-only`. Beyond that, every
later edit, rollback or reload of that file becomes a shared failure mode
between two projects that have nothing to do with each other.

A tunnel costs nothing and a second `cloudflared` process costs a few MB. The
two share nothing but the Cloudflare account.

**Do not point this project's config at the trading tunnel's id.** Two processes
running the same tunnel both connect, each advertising its own ingress list, and
Cloudflare picks between them per request — the site would work roughly half the
time, which is harder to diagnose than not working at all.

## What the setup does

1. Finds `cloudflared` (Homebrew, not on the ssh PATH).
2. Checks `~/.cloudflared/cert.pem` exists — the account certificate. Getting
   one opens a browser, so if it is missing the script stops and tells you to
   run `cloudflared tunnel login` once on the mini rather than hanging on a
   prompt nobody is watching.
3. Creates the `retirement` tunnel if it does not exist, and writes
   `config/cloudflared-config.yml` in **this** repo pointing at
   `http://localhost:8891`. That file is gitignored — it carries a machine
   specific tunnel id and credentials path.
4. Validates with `cloudflared tunnel ingress validate`.
5. Routes `retirement.jbrasfield.com` to it (a proxied CNAME to
   `<tunnel-id>.cfargotunnel.com` — the dashboard's "DNS only" will not work,
   that hostname only resolves inside Cloudflare's network).

`com.jambi.retirement-tunnel` keeps the process running, installed alongside the
web and nightly-cycle agents by `scripts/install_agents.sh`.

## Access

**There is no login in this app.** The finance page carries account names,
institutions and net worth. Put Cloudflare Access in front of the hostname:
Zero Trust → Access → Applications → Add → Self-hosted, domain
`retirement.jbrasfield.com`, policy *Allow* → Emails → your two addresses.
One-time PIN needs no identity provider setup.

`ship.sh` checks whether anything is in front of the URL and says so at the end.
Until Access is on, `modules.finance.enabled: false` in `config/profile.yaml`
holds the balance sheet back while property and board stay up.

## If something is wrong

```bash
ssh jambimac@Jonathans-Mac-mini.local
cd ~/repo/retirement
cat config/cloudflared-config.yml        # id, hostname, port
tail -50 logs/tunnel.log
launchctl kickstart -k gui/$(id -u)/com.jambi.retirement-tunnel
```

The trading tunnel is a different process with a different config; nothing here
touches it.
