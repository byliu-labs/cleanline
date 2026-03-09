#!/usr/bin/env python3
"""Extract and normalize hostname from a URL.

Reads a URL from argv[1], prints the lowercase hostname to stdout.
Exits non-zero on any error (fail closed).
"""
import sys
from urllib.parse import urlparse


def main() -> int:
    if len(sys.argv) != 2:
        return 1

    url = sys.argv[1]
    try:
        parsed = urlparse(url)
    except Exception:
        return 1

    host = parsed.hostname  # already lowercased by urlparse
    if not host:
        return 1

    # Strip trailing dots (FQDN notation)
    host = host.rstrip(".")

    # Reject IP addresses and empty hosts
    if not host or host[0].isdigit() or ":" in host:
        return 1

    print(host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
