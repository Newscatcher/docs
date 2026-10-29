#!/usr/bin/env python3
"""Generate sitemap.xml for NewsCatcher API documentation.

Reads docs.json (navigation source of truth) and produces a sitemap.xml
following the Sitemaps protocol (https://www.sitemaps.org/protocol.html).

The output is deterministic: identical docs.json inputs always produce
identical output. <lastmod> is intentionally omitted so the committed file
only becomes stale when navigation actually changes, not on every new day.

Usage:
    python scripts/generate_sitemap.py
    python scripts/generate_sitemap.py --output path/to/sitemap.xml
    python scripts/generate_sitemap.py --check   # exit 1 if file would change

Requirements:
    Python 3.8+, stdlib only
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.newscatcherapi.com/docs"
DOCS_JSON_PATH = "docs.json"
OUTPUT_PATH = "sitemap.xml"

# Navigation entries like "GET /search" are endpoints Mintlify generates from
# an OpenAPI spec, not MDX pages.
OPENAPI_ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /")

# ---------------------------------------------------------------------------
# Navigation traversal (mirrors generate_llms_txt.py)
# ---------------------------------------------------------------------------


def nav_tabs(navigation: dict) -> list:
    """Return the navigation's tabs, from every version when it has versions."""
    if "versions" in navigation:
        return [tab for version in navigation["versions"] for tab in version.get("tabs", [])]
    return navigation.get("tabs", [])


def iter_leaves(node) -> list:
    """Recursively return all leaf page path strings from a navigation node."""
    if isinstance(node, str):
        return [] if OPENAPI_ENDPOINT_RE.match(node) else [node]
    if isinstance(node, dict):
        result = []
        for p in node.get("pages", []):
            result.extend(iter_leaves(p))
        for g in node.get("groups", []):
            result.extend(iter_leaves(g))
        return result
    if isinstance(node, list):
        result = []
        for item in node:
            result.extend(iter_leaves(item))
        return result
    return []


def collect_pages(docs_json: dict) -> list:
    """Return all leaf page paths from docs.json navigation in document order."""
    pages = []
    for tab in nav_tabs(docs_json.get("navigation", {})):
        pages.extend(iter_leaves(tab))
    return pages


# ---------------------------------------------------------------------------
# Sitemap generation
# ---------------------------------------------------------------------------


def build_sitemap(pages: list) -> str:
    """Build a sitemap XML string from a list of page paths.

    Emits <loc> only — no <lastmod> — so the output is fully deterministic
    and the committed file stays current until navigation actually changes.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for page_path in pages:
        lines.append("    <url>")
        lines.append(f"        <loc>{BASE_URL}/{page_path}</loc>")
        lines.append("    </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help=f"Output path (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the committed sitemap.xml does not match generated output (CI mode)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    docs_json_path = root / DOCS_JSON_PATH
    if not docs_json_path.exists():
        print(f"Error: {DOCS_JSON_PATH} not found at {root}", file=sys.stderr)
        sys.exit(1)

    try:
        docs_json = json.loads(docs_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: {DOCS_JSON_PATH} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    pages = collect_pages(docs_json)

    if not pages:
        print("Error: no pages found in docs.json navigation", file=sys.stderr)
        sys.exit(1)

    sitemap = build_sitemap(pages)
    output_path = root / args.output

    if args.check:
        if not output_path.exists():
            print(
                f"Error: {args.output} does not exist. "
                "Run 'python scripts/generate_sitemap.py' locally and commit the file.",
                file=sys.stderr,
            )
            sys.exit(1)
        existing = output_path.read_text(encoding="utf-8")
        if existing != sitemap:
            print(
                f"\n✗  {args.output} is out of date.",
                file=sys.stderr,
            )
            print(
                "Run 'python scripts/generate_sitemap.py' locally and commit the updated file.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"✓  {args.output} is up to date.")
        return

    output_path.write_text(sitemap, encoding="utf-8")
    print(f"✓  Generated {args.output} ({len(pages)} URLs).")


if __name__ == "__main__":
    main()
