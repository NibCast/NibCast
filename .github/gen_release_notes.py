"""Generate release_body.md for the GitHub Release from CHANGELOG.md.

Usage:  python .github/gen_release_notes.py v2.4.2

Extracts the changelog section matching the tag's version and appends the
standard Downloads / First Run / Requirements boilerplate. Written for the
Build & Release workflow so release notes fill themselves in — no more
"<!-- fill in -->" placeholders to edit after publishing. Falls back to a
generic line (never fails the release) if the version has no changelog entry.
"""

import re
import sys


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    version = tag.lstrip("v")

    try:
        text = open("CHANGELOG.md", encoding="utf-8").read()
        m = re.search(
            rf"## \[{re.escape(version)}\][^\n]*\n(.*?)(?=\n---|\n## \[|\Z)",
            text, re.S,
        )
        whats_new = m.group(1).strip() if m else ""
    except OSError:
        whats_new = ""

    if not whats_new:
        whats_new = (f"See [CHANGELOG.md](https://github.com/NibCast/NibCast/"
                     f"blob/main/CHANGELOG.md) for details.")
        print(f"WARNING: no CHANGELOG.md section found for [{version}] — "
              f"using fallback text.", file=sys.stderr)

    body = f"""## What's New

{whats_new}

## Downloads

| File | Description |
|------|-------------|
| `NibCast-{tag}.exe` | **Single file** — just double-click. Slower first launch (~5s). |
| `NibCast-{tag}-windows.zip` | **Folder install** — extract and run `NibCast.exe`. Faster launch. |

## First Run
1. Double-click `NibCast.exe`
2. Windows will show a SmartScreen warning → click **More info → Run anyway**
3. A setup wizard opens automatically — get a free Groq key at [console.groq.com](https://console.groq.com) and paste it in
4. Press `Ctrl+Alt+V` and speak

## Requirements
- Windows 10 / 11 (64-bit)
- Microphone
- Free [Groq API key](https://console.groq.com) (7,200 min/day free)

## Updating from an older version
Fully quit NibCast (tray icon → Quit) before replacing the files — the
dashboard will remind you with a banner if an older version is still running.
"""

    with open("release_body.md", "w", encoding="utf-8") as f:
        f.write(body)
    print(f"release_body.md written ({len(body)} chars) for {tag}")


if __name__ == "__main__":
    main()
