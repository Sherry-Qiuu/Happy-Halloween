#!/usr/bin/env python3
"""Check a DeepSeek API key without printing it.

Reads the key from the XJ_KEY_CHECK environment variable and prints one
line:

    valid deepseek-chat,deepseek-reasoner
    rejected 401
    unreachable URLError

Used by set-key.ps1 before the key is written to .env.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.deepseek.com/models"


def main() -> int:
    key = os.environ.get("XJ_KEY_CHECK", "").strip()
    if not key:
        print("unreachable NO_KEY")
        return 1

    request = urllib.request.Request(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "xingjian-setup/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(f"rejected {error.code}")
        return 1
    except Exception as error:  # noqa: BLE001 - network problems are not fatal
        print(f"unreachable {type(error).__name__}")
        return 2

    names = [
        item.get("id", "")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    ]
    print("valid " + ",".join(name for name in names if name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
