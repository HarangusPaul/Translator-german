"""
Helper script – list available Microsoft Edge TTS voices filtered by locale.

Usage:
    python list_voices.py          # list all voices
    python list_voices.py de       # filter by locale prefix (e.g. 'de', 'en-US')
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    import edge_tts

    voices = await edge_tts.list_voices()
    prefix = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    matches = [
        v for v in voices
        if not prefix or v["Locale"].lower().startswith(prefix)
    ]

    col = "{:<35} {:<15} {}"
    print(col.format("ShortName", "Locale", "Gender"))
    print("-" * 70)
    for v in sorted(matches, key=lambda x: x["Locale"]):
        print(col.format(v["ShortName"], v["Locale"], v["Gender"]))

    print(f"\n{len(matches)} voice(s) matched.")


if __name__ == "__main__":
    asyncio.run(main())
