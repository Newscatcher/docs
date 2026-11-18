#!/usr/bin/env python3
"""Generate llms.txt for the Latest NewsCatcher docs (News API and Agent).

The file is a hand-written template (scripts/llms-txt-template.txt) with three
generated lists filled in from docs.json and the OpenAPI specs:

    {API_REFERENCE}   every endpoint of the Latest "API Reference" tab
    {DOCUMENTATION}   every page of the Latest "Documentation" tab, by group
    {CHANGELOG}       the pages of the Latest "Changelog" tab

Only the Latest version of docs.json is read: Enterprise pages are never
listed. Every link points to the Markdown version of the page (`.md`).

The output is deterministic: identical inputs always produce identical output.

Usage:
    python scripts/generate_llms_txt.py
    python scripts/generate_llms_txt.py --output path/to/llms.txt
    python scripts/generate_llms_txt.py --check   # exit 1 if file would change

Requirements:
    pip install pyyaml
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required.  Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.newscatcherapi.com/docs"
DOCS_JSON_PATH = "docs.json"
TEMPLATE_PATH = "scripts/llms-txt-template.txt"
OUTPUT_PATH = "llms.txt"

LATEST_VERSION = "Latest"
DOCUMENTATION_TAB = "Documentation"
API_REFERENCE_TAB = "API Reference"
CHANGELOG_TAB = "Changelog"

PLACEHOLDERS = ("{API_REFERENCE}", "{DOCUMENTATION}", "{CHANGELOG}")

# Navigation entries like "GET /search" are endpoint pages Mintlify generates
# from an OpenAPI spec, not MDX pages.
OPENAPI_ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---", re.DOTALL)

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter of an MDX page.

    Args:
        text (str): The whole page source.

    Returns:
        dict: The frontmatter fields, or {} when there is none or it is invalid.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        result = yaml.safe_load(match.group(1))
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def read_frontmatter(page_path: str, root: Path) -> dict:
    """Read the frontmatter of the .mdx (or .md) file of a page.

    Args:
        page_path (str): Page path as written in docs.json, without extension.
        root (Path): Repository root.

    Returns:
        dict: The frontmatter fields, or {} when the file is missing.
    """
    for ext in (".mdx", ".md"):
        candidate = root / f"{page_path}{ext}"
        if candidate.exists():
            return parse_frontmatter(candidate.read_text(encoding="utf-8"))
    return {}


def page_entry(page_path: str, root: Path, missing: list[str]) -> str:
    """Build the list line of one docs page: title, Markdown link, description.

    Args:
        page_path (str): Page path as written in docs.json.
        root (Path): Repository root.
        missing (list[str]): Receives the page when it has no description.

    Returns:
        str: The Markdown list line.
    """
    fm = read_frontmatter(page_path, root)
    slug = page_path.rsplit("/", 1)[-1]
    title = fm.get("title") or fm.get("sidebarTitle") or slug.replace("-", " ").title()
    url = f"{BASE_URL}/{page_path}.md"
    description = fm.get("description")
    if not description:
        missing.append(f"{page_path}.mdx (no description)")
        return f"- [{title}]({url})"
    return f"- [{title}]({url}): {description}"


def iter_pages(node) -> list:
    """List the entries of a navigation node in order, nested groups included.

    Args:
        node: A page path, a group dict or a list of them.

    Returns:
        list: Tuples (entry, openapi_spec_path or None), in navigation order.
    """
    found: list = []

    def walk(item, spec):
        if isinstance(item, str):
            found.append((item, spec))
        elif isinstance(item, dict):
            child_spec = item.get("openapi", spec)
            for child in item.get("pages", []):
                walk(child, child_spec)
        elif isinstance(item, list):
            for child in item:
                walk(child, spec)

    walk(node, None)
    return found


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_spec_cache: dict[str, dict] = {}


def load_spec(spec_path: str, root: Path) -> dict:
    """Load and cache an OpenAPI spec of the repository.

    Args:
        spec_path (str): Repo-relative path of the JSON or YAML spec.
        root (Path): Repository root.

    Returns:
        dict: The parsed spec, or {} when the file is missing.
    """
    if spec_path not in _spec_cache:
        full = root / spec_path
        if not full.exists():
            _spec_cache[spec_path] = {}
        else:
            text = full.read_text(encoding="utf-8")
            _spec_cache[spec_path] = json.loads(text) if spec_path.endswith(".json") else (yaml.safe_load(text) or {})
    return _spec_cache[spec_path]


def slugify(text: str) -> str:
    """Turn a tag or a summary into a URL segment, as Mintlify does.

    Args:
        text (str): The text, for example "Search news (JSON body)".

    Returns:
        str: The segment, for example "search-news-json-body".
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def endpoint_entry(entry: str, spec_path: str | None, root: Path, missing: list[str]) -> str:
    """Build the list line of one generated endpoint page.

    The page URL is /api-reference/<first tag>/<summary>, both slugified.

    Args:
        entry (str): Navigation entry such as "GET /search".
        spec_path (str | None): The spec the navigation group points to.
        root (Path): Repository root.
        missing (list[str]): Receives the entry when it cannot be resolved.

    Returns:
        str: The Markdown list line, or "" when the entry cannot be resolved.
    """
    match = OPENAPI_ENDPOINT_RE.match(entry)
    spec = load_spec(spec_path, root) if spec_path else {}
    try:
        operation = spec["paths"][match.group(2)][match.group(1).lower()]
        tag = operation["tags"][0]
        summary = operation["summary"]
    except (AttributeError, KeyError, IndexError, TypeError):
        missing.append(f"{entry} (not found in {spec_path})")
        return ""
    url = f"{BASE_URL}/api-reference/{slugify(tag)}/{slugify(summary)}.md"
    return f"- [{summary}]({url}): `{entry}`"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def latest_tabs(docs_json: dict) -> dict[str, dict]:
    """Return the tabs of the Latest version, by name.

    Args:
        docs_json (dict): The parsed docs.json.

    Returns:
        dict[str, dict]: Tab name to tab.
    """
    for version in docs_json["navigation"]["versions"]:
        if version.get("version") == LATEST_VERSION:
            return {tab.get("tab", ""): tab for tab in version.get("tabs", [])}
    raise SystemExit(f"Error: docs.json has no version named {LATEST_VERSION!r}")


def render_groups(tab: dict, root: Path, missing: list[str]) -> str:
    """Render the groups of a tab as H3 sections of list lines.

    Docs pages and generated endpoint pages are both supported; a nested group
    is listed under its top-level group, in navigation order.

    Args:
        tab (dict): The tab from docs.json.
        root (Path): Repository root.
        missing (list[str]): Receives entries that cannot be resolved.

    Returns:
        str: The rendered sections, without a trailing newline.
    """
    blocks: list[str] = []
    for group in tab.get("groups", []):
        lines = []
        for entry, spec_path in iter_pages(group):
            if OPENAPI_ENDPOINT_RE.match(entry):
                line = endpoint_entry(entry, spec_path, root, missing)
            else:
                line = page_entry(entry, root, missing)
            if line:
                lines.append(line)
        if lines:
            blocks.append(f"### {group.get('group', '')}\n\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def render_pages(tab: dict, root: Path, missing: list[str]) -> str:
    """Render the pages of a tab with no groups (such as Changelog) as list lines.

    Args:
        tab (dict): The tab from docs.json.
        root (Path): Repository root.
        missing (list[str]): Receives pages that cannot be resolved.

    Returns:
        str: The list lines, without a trailing newline.
    """
    return "\n".join(page_entry(entry, root, missing) for entry, _ in iter_pages(tab.get("pages", [])))


def generate(root: Path) -> tuple[str, list[str]]:
    """Build llms.txt from the template, docs.json and the OpenAPI specs.

    Args:
        root (Path): Repository root.

    Returns:
        tuple[str, list[str]]: The content, and the entries that could not be
        resolved. A non-empty list is a hard error.
    """
    docs_json = json.loads((root / DOCS_JSON_PATH).read_text(encoding="utf-8"))
    template = (root / TEMPLATE_PATH).read_text(encoding="utf-8")
    tabs = latest_tabs(docs_json)
    missing: list[str] = []

    for placeholder in PLACEHOLDERS:
        if template.count(placeholder) != 1:
            missing.append(f"{TEMPLATE_PATH}: {placeholder} must appear exactly once")
    for name in (DOCUMENTATION_TAB, API_REFERENCE_TAB, CHANGELOG_TAB):
        if name not in tabs:
            missing.append(f"docs.json: the {LATEST_VERSION} version has no {name!r} tab")
    if missing:
        return "", missing

    content = (
        template.replace("{BASE_URL}", BASE_URL)
        .replace("{API_REFERENCE}", render_groups(tabs[API_REFERENCE_TAB], root, missing))
        .replace("{DOCUMENTATION}", render_groups(tabs[DOCUMENTATION_TAB], root, missing))
        .replace("{CHANGELOG}", render_pages(tabs[CHANGELOG_TAB], root, missing))
    )
    return content.rstrip("\n") + "\n", missing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def report_missing(missing: list[str]) -> None:
    """Print the entries that could not be resolved.

    Args:
        missing (list[str]): The entries, each with the reason.
    """
    print(f"\nFAIL  {len(missing)} problem(s):\n", file=sys.stderr)
    for item in missing:
        print(f"   {item}", file=sys.stderr)
    print(
        "\nDocs pages need a `description` in their frontmatter; endpoints need a "
        "`summary` and a tag in their OpenAPI spec.",
        file=sys.stderr,
    )


def main() -> None:
    """Write llms.txt, or check that it is up to date with --check."""
    parser = argparse.ArgumentParser(
        description="Generate llms.txt for the Latest NewsCatcher docs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--output", default=OUTPUT_PATH, metavar="FILE", help=f"Output file path (default: {OUTPUT_PATH})")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if the output file would change or an entry cannot be resolved.",
    )
    args = parser.parse_args()

    # Always run from the project root, wherever the script is called from
    script_dir = Path(__file__).resolve().parent
    root = script_dir.parent if script_dir.name == "scripts" else script_dir

    output_path = root / args.output
    content, missing = generate(root)
    if missing:
        report_missing(missing)
        sys.exit(1)

    entry_count = content.count("\n- [")
    if args.check:
        if not output_path.exists() or output_path.read_text(encoding="utf-8") != content:
            print(
                f"FAIL  {args.output} is out of date.\n   Run: npm run llms:generate  and commit the result.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"OK  {args.output} is up to date ({entry_count} entries).")
        return

    output_path.write_text(content, encoding="utf-8")
    print(f"OK  Generated {args.output} ({entry_count} entries).")


if __name__ == "__main__":
    main()
