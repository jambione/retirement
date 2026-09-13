#!/usr/bin/env python3
"""Add one hostname to an existing cloudflared ingress list.

Kept out of ship.sh and in its own file so it can be tested, because this is
the one step that touches a file keeping another site up. It edits the YAML as
TEXT rather than parsing and re-emitting it: a round-trip through a YAML
library would reformat the whole file, drop the comments, and make the diff
impossible to check at a glance.

Exit codes: 0 added, 2 already present (nothing written), 1 refused.

    cloudflare_ingress.py ~/.cloudflared/config.yml retirement.jbrasfield.com 8891
"""
from __future__ import annotations

import re
import sys

CATCHALL = re.compile(r"^(\s*)-\s*service:\s*http_status:\s*404\s*$")


def insert(text: str, hostname: str, port: int) -> str:
    """Returns the new file text. Raises ValueError if it should not be edited."""
    if re.search(rf"^\s*-?\s*hostname:\s*{re.escape(hostname)}\s*$", text, re.M):
        raise AlreadyPresent(hostname)

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = CATCHALL.match(line.rstrip("\n"))
        if not match:
            continue
        indent = match.group(1)
        # Above the catch-all, always: cloudflared matches rules top to bottom
        # and everything after a service-less rule is dead config.
        lines[index:index] = [
            f"{indent}- hostname: {hostname}\n",
            f"{indent}  service: http://localhost:{port}\n",
            "\n",
        ]
        return "".join(lines)

    raise ValueError(
        "no `- service: http_status:404` catch-all found — this does not look "
        "like a cloudflared ingress file, so nothing was changed"
    )


class AlreadyPresent(Exception):
    def __init__(self, hostname: str):
        super().__init__(f"{hostname} is already in the ingress list")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 1
    path, hostname, port = argv[1], argv[2], int(argv[3])
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        updated = insert(text, hostname, port)
    except AlreadyPresent as exc:
        print(f"  ✓ {exc}")
        return 2
    except ValueError as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    print(f"  · added {hostname} -> http://localhost:{port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
