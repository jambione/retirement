# Putting this behind retirement.jbrasfield.com

You already run a named tunnel for `trading.jbrasfield.com`
(`56c84116-0ef0-47c7-bbea-25634d765487`). Adding this app is two changes to
that same tunnel — **no new tunnel, no new credentials file.** A second tunnel
would mean a second `cloudflared` process and a second thing to keep running.

## 1. Route the DNS name to the existing tunnel

On the mini, once:

```bash
cloudflared tunnel route dns 56c84116-0ef0-47c7-bbea-25634d765487 retirement.jbrasfield.com
```

That writes a proxied CNAME (`retirement` → `<tunnel-id>.cfargotunnel.com`) in
the `jbrasfield.com` zone. It is idempotent; if the record exists it says so.

## 2. Add the ingress rule

Edit `~/.cloudflared/config.yml` on the mini and add the hostname **above** the
catch-all. `config/cloudflared-ingress-snippet.yml` in this repo is the merged
file as it should end up. The catch-all `http_status:404` must remain the last
entry — cloudflared matches rules top to bottom and anything after a
service-less rule is dead config.

Then reload. `cloudflared` re-reads its config on SIGHUP, so this does not drop
the trading dashboard:

```bash
kill -HUP "$(pgrep -f 'cloudflared.*tunnel.*run')"
```

Verify both hostnames still answer:

```bash
curl -sI https://trading.jbrasfield.com | head -1
curl -sI https://retirement.jbrasfield.com/healthz | head -1
```

## 3. Put Cloudflare Access in front of it — do this before step 1

**This app has no login.** Anyone who guesses the hostname would get your
search criteria, your shortlist, your verdicts, and a "Scan now" button that
spends your API quota. The trading dashboard has `auth.py`; this one
deliberately does not, because Cloudflare Access does the job better and you
are already paying nothing for it (free up to 50 users).

In the Zero Trust dashboard: **Access → Applications → Add an application →
Self-hosted**

- Application domain: `retirement.jbrasfield.com`
- Session duration: 1 month (so you are not re-authenticating on every glance)
- Policy: *Allow*, include → **Emails**, and list your address and your wife's

Pick one-time PIN as the identity provider unless you have Google/GitHub SSO
already wired into Zero Trust. One-time PIN needs no setup and emails a code.

To confirm it is actually on, load the URL in a private window. If you see the
shortlist without being asked to authenticate, the policy is not applied — fix
that before you leave it running.

### Why not just skip the tunnel and use Tailscale?

You could, and it would be simpler. The reason to use the tunnel: you want to
open this on a phone in Italy next spring, possibly on hotel wifi, possibly on
a device that is not yours. A hostname plus an emailed PIN works anywhere. If
you would rather it never be reachable from the public internet at all, skip
this whole document and reach `http://Jonathans-Mac-mini.local:8891` over
Tailscale instead — the app binds 127.0.0.1, so you would also need to change
the bind address in `scripts/com.jambi.retirement-web.plist` to `0.0.0.0`.
