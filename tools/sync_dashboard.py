#!/usr/bin/env python3
"""Keep root preview HTML aligned with the GitHub Pages /docs entry point."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def root_html():
    text = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for asset in ("theme.js", "terminal.css", "terminal.js", "manifest.json", "icon.svg", "settings.html"):
        text = text.replace(f'"./{asset}"', f'"./docs/{asset}"')
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    target = ROOT / "index.html"
    if args.write:
        target.write_text(root_html(), encoding="utf-8")
    elif not target.exists() or target.read_text(encoding="utf-8") != root_html():
        raise SystemExit("Root preview is out of sync; run npm run sync")
    print("Root preview / GitHub Pages dashboard: in sync")
