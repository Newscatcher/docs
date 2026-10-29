#!/usr/bin/env python3
"""Generate llms.txt for NewsCatcher API documentation.

Reads docs.json (navigation source of truth) and MDX page frontmatter to
produce a structured llms.txt following the llms.txt spec (llmstxt.org).

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
GITHUB_BASE = "https://github.com/NewscatcherAPI/docs/blob/main"
DOCS_JSON_PATH = "docs.json"
OUTPUT_PATH = "llms.txt"

# Active OAS specs only. Maps the frontmatter identifier (e.g. the first token
# of `openapi: catch-all-api post /catchAll/initialize`) to the repo-relative
# YAML file path.  Legacy specs (events-api, news-api-v2) are intentionally
# excluded.
OAS_SPECS: dict[str, str] = {
    "catch-all-api": "web-search-api/api-reference/catch-all-api.yml",
    "news-api-v3": "news-api/api-reference/news-api-v3.yml",
    "local-news-api": "local-news-api/api-reference/local-news-api.yml",
    "news-api": "api-reference/news-api/openapi.json",
    "agent": "api-reference/agent/openapi.json",
}

BLOCKQUOTE = (
    "Enterprise APIs for web search, news, and local news data. "
    "Three products: CatchAll (Web Search API) for recall-first structured "
    "event extraction from 50,000+ web pages per job; "
    "News API for 140,000+ source news search with NLP enrichment, "
    "clustering, and deduplication; "
    "Local News API for hyper-local news with intelligent geographic "
    "detection and filtering."
)

# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

import re

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---", re.DOTALL)

# Navigation entries like "GET /search" are endpoints Mintlify generates from
# an OpenAPI spec, not MDX pages.
OPENAPI_ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /")


def parse_frontmatter(text: str) -> dict:
    """Return the YAML frontmatter block as a dict, or {} if absent/invalid."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        result = yaml.safe_load(match.group(1))
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def read_mdx_frontmatter(page_path: str, root: Path) -> dict:
    """Read and parse frontmatter from the .mdx (or .md) file for a page."""
    for ext in (".mdx", ".md"):
        candidate = root / f"{page_path}{ext}"
        if candidate.exists():
            return parse_frontmatter(candidate.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# OAS description lookup
# ---------------------------------------------------------------------------

_oas_cache: dict[str, dict] = {}


def _load_oas(spec_name: str, root: Path) -> dict:
    """Load and cache an OAS YAML file, keyed by spec name."""
    if spec_name not in _oas_cache:
        file_path = OAS_SPECS.get(spec_name)
        if not file_path:
            _oas_cache[spec_name] = {}
        else:
            full_path = root / file_path
            if full_path.exists():
                with full_path.open(encoding="utf-8") as fh:
                    _oas_cache[spec_name] = yaml.safe_load(fh) or {}
            else:
                _oas_cache[spec_name] = {}
    return _oas_cache[spec_name]


def get_oas_description(openapi_field: str, root: Path) -> str | None:
    """
    Resolve the operation description from an OAS file.

    openapi_field format:  "<spec-name> <method> <path>"
    Example:               "catch-all-api post /catchAll/initialize"

    Returns the `description` field for that operation, falling back to
    `summary` if description is absent.  Returns None if resolution fails.
    """
    parts = openapi_field.strip().split(None, 2)
    if len(parts) < 3:
        return None
    spec_name, method, path = parts[0], parts[1].lower(), parts[2]
    oas = _load_oas(spec_name, root)
    try:
        op = oas["paths"][path][method]
        return op.get("description") or op.get("summary") or None
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# OAS info-level helpers
# ---------------------------------------------------------------------------


def get_oas_info_description(spec_name: str, root: Path) -> str | None:
    """
    Return the first sentence of the top-level `info.description` for a spec.
    Used to annotate `## API Specifications` links.

    Strips leading whitespace, newlines, and markdown heading lines before
    extracting the first sentence. Returns None if the field is absent.
    """
    oas = _load_oas(spec_name, root)
    raw: str = (oas.get("info") or {}).get("description") or ""
    if not raw:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Take text up to the first full stop followed by whitespace or EOL.
        match = re.search(r"^(.+?\.)\ ", line + " ")
        if match:
            return match.group(1)
        # No full stop — return the whole line (truncated to 200 chars).
        return line[:200]
    return None


# ---------------------------------------------------------------------------
# Page resolution
# ---------------------------------------------------------------------------


def _method_suffix(slug: str) -> str:
    """
    Return '(GET)' or '(POST)' when the slug ends with -get or -post,
    otherwise return an empty string.

    This differentiates GET/POST variants of the same endpoint (e.g.
    `search-articles-get` vs `search-articles-post`) which otherwise share
    the same OAS summary.
    """
    if slug.endswith("-get"):
        return " (GET)"
    if slug.endswith("-post"):
        return " (POST)"
    return ""


def resolve_page(page_path: str, root: Path) -> tuple[str, str | None]:
    """
    Return (title, description) for a page.

    Title resolution order:
      1. `title` frontmatter field
      2. `sidebarTitle` frontmatter field
      3. Slug humanised from the last path segment

    Description resolution order:
      1. `description` frontmatter field
      2. OAS operation `description` (when `openapi` frontmatter is present)
      3. OAS operation `summary` (fallback)
      4. None (entry is emitted without a description)
    """
    fm = read_mdx_frontmatter(page_path, root)
    slug = page_path.rsplit("/", 1)[-1]

    # Title
    title = fm.get("title") or fm.get("sidebarTitle") or slug.replace("-", " ").title()

    # Append method hint for GET/POST twin pages
    title = f"{title}{_method_suffix(slug)}"

    # Description
    description: str | None = fm.get("description") or None

    if not description:
        openapi_field = fm.get("openapi") or fm.get("api")
        if openapi_field:
            description = get_oas_description(str(openapi_field), root)

    return title, description


# ---------------------------------------------------------------------------
# Navigation traversal
# ---------------------------------------------------------------------------


def iter_leaves(node) -> list[str]:
    """Recursively yield all leaf page paths from a navigation node."""
    pages: list[str] = []
    if isinstance(node, str):
        if not OPENAPI_ENDPOINT_RE.match(node):
            pages.append(node)
    elif isinstance(node, dict):
        for child in node.get("pages", []):
            pages.extend(iter_leaves(child))
    elif isinstance(node, list):
        for item in node:
            pages.extend(iter_leaves(item))
    return pages


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _entry(page_path: str, title: str, description: str) -> str:
    url = f"{BASE_URL}/{page_path}"
    return f"- [{title}]({url}): {description}"


def _render_group(
    group_name: str,
    pages: list,
    root: Path,
    missing: list[str],
    parent_label: str = "",
) -> list[str]:
    """
    Render one navigation group as markdown lines.

    If `pages` contains nested group dicts, each sub-group is emitted as its
    own H3 labelled "<parent_label> — <sub_group_name>" (or just
    "<group_name> — <sub_group_name>" when no parent_label is given).

    Flat leaf pages that appear alongside nested groups are collected under
    the group's own H3.

    Pages whose description cannot be resolved are appended to `missing` and
    emitted as bare entries so the file is still syntactically valid, but the
    caller is expected to treat any non-empty `missing` list as a hard error.
    """
    lines: list[str] = []
    flat = [p for p in pages if isinstance(p, str)]
    nested = [p for p in pages if isinstance(p, dict)]
    heading_prefix = parent_label or group_name

    def emit(page_path: str) -> str:
        title, desc = resolve_page(page_path, root)
        if not desc:
            missing.append(page_path)
            return f"- [{title}]({BASE_URL}/{page_path})"
        return _entry(page_path, title, desc)

    if flat:
        lines.append(f"### {heading_prefix}")
        lines.append("")
        for page_path in flat:
            lines.append(emit(page_path))
        lines.append("")

    for sub_group in nested:
        sub_name = sub_group.get("group", "")
        sub_pages = iter_leaves(sub_group)
        lines.append(f"### {heading_prefix} — {sub_name}")
        lines.append("")
        for page_path in sub_pages:
            lines.append(emit(page_path))
        lines.append("")

    # Simple group with no nesting
    if not flat and not nested:
        return lines

    if not flat and not nested and pages:
        lines.append(f"### {group_name}")
        lines.append("")
        for page_path in iter_leaves(pages):
            lines.append(emit(page_path))
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate(root: Path) -> tuple[str, list[str]]:
    """
    Build the llms.txt content from docs.json and MDX frontmatter.

    Returns:
        (content, missing) where `missing` is a list of page paths for which
        no description could be resolved.  Callers should treat a non-empty
        `missing` list as a hard error.
    """
    docs_json: dict = json.loads((root / DOCS_JSON_PATH).read_text(encoding="utf-8"))
    navigation: dict = docs_json["navigation"]
    # (tab, H2 label) pairs. With versions, tabs of the non-default versions
    # are labelled "<tab> (<version>)".
    if "versions" in navigation:
        tabs: list = []
        for i, version in enumerate(navigation["versions"]):
            is_default = version.get("default", i == 0)
            for tab in version.get("tabs", []):
                label = tab.get("tab", "")
                tabs.append((tab, label if is_default else f"{label} ({version['version']})"))
    else:
        tabs = [(tab, tab.get("tab", "")) for tab in navigation["tabs"]]

    lines: list[str] = []
    missing: list[str] = []

    def emit(page_path: str) -> str:
        title, desc = resolve_page(page_path, root)
        if not desc:
            missing.append(page_path)
            return f"- [{title}]({BASE_URL}/{page_path})"
        return _entry(page_path, title, desc)

    # Header
    lines.append("# NewsCatcher API Documentation")
    lines.append("")
    lines.append(f"> {BLOCKQUOTE}")
    lines.append("")

    # Home page as a preamble link (outside any H2 section)
    for tab, _ in tabs:
        if tab.get("tab") == "Home":
            for page_path in tab.get("pages", []):
                lines.append(emit(page_path))
            lines.append("")
            break

    # Product tabs
    for tab, tab_name in tabs:
        if tab.get("tab") == "Home" or not iter_leaves(tab.get("groups", []) or tab.get("pages", [])):
            continue

        lines.append(f"## {tab_name}")
        lines.append("")

        # A tab with pages and no groups (e.g. Changelog)
        for page_path in iter_leaves([p for p in tab.get("pages", []) if isinstance(p, str)]):
            lines.append(emit(page_path))
        if tab.get("pages") and not tab.get("groups"):
            lines.append("")

        for group in tab.get("groups", []):
            group_name: str = group.get("group", "")
            group_pages: list = group.get("pages", [])
            if not iter_leaves(group_pages):
                continue

            has_nested = any(isinstance(p, dict) for p in group_pages)

            if has_nested:
                lines.extend(_render_group(group_name, group_pages, root, missing))
            else:
                # Flat group — emit H3 + list
                lines.append(f"### {group_name}")
                lines.append("")
                for page_path in iter_leaves(group_pages):
                    lines.append(emit(page_path))
                lines.append("")

    # API Specifications section
    lines.append("## API Specifications")
    lines.append("")
    for spec_name, spec_path in OAS_SPECS.items():
        github_url = f"{GITHUB_BASE}/{spec_path}"
        info_desc = get_oas_info_description(spec_name, root)
        if info_desc:
            lines.append(f"- [{spec_name}]({github_url}): {info_desc}")
        else:
            lines.append(f"- [{spec_name}]({github_url})")
    lines.append("")

    return "\n".join(lines), missing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _report_missing(missing: list[str]) -> None:
    print(
        f"\nFAIL  {len(missing)} page(s) have no description. "
        "Add a `description` field to their frontmatter:\n",
        file=sys.stderr,
    )
    for p in missing:
        print(f"   {p}.mdx", file=sys.stderr)
    print(
        "\nFor API endpoint pages, ensure the corresponding OAS operation "
        "has a `description` field.",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate llms.txt for NewsCatcher API documentation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        metavar="FILE",
        help=f"Output file path (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Validate that the output file is up to date without writing it. "
            "Exits with code 1 if the file would change or descriptions are missing."
        ),
    )
    args = parser.parse_args()

    # Always run from project root regardless of invocation directory
    script_dir = Path(__file__).resolve().parent
    root = script_dir.parent if script_dir.name == "scripts" else script_dir

    output_path = root / args.output
    content, missing = generate(root)

    # Hard-fail on any page lacking a description, regardless of mode
    if missing:
        _report_missing(missing)
        sys.exit(1)

    if args.check:
        if not output_path.exists():
            print(
                f"FAIL  {args.output} does not exist.\n" "   Run: npm run llms:generate",
                file=sys.stderr,
            )
            sys.exit(1)
        existing = output_path.read_text(encoding="utf-8")
        if existing != content:
            print(
                f"FAIL  {args.output} is out of date.\n"
                "   Run: npm run llms:generate  and commit the result.",
                file=sys.stderr,
            )
            sys.exit(1)
        page_count = content.count("\n- [")
        print(f"OK  {args.output} is up to date ({page_count} entries).")
        return

    output_path.write_text(content, encoding="utf-8")
    page_count = content.count("\n- [")
    print(f"OK  Generated {args.output} ({page_count} entries).")


if __name__ == "__main__":
    main()
