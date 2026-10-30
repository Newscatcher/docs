"""Fetch the News API and Agent OpenAPI specs into api-reference/.

The Agent spec comes from FastAPI with Python docstrings in its operation
descriptions, so the Args/Returns/Raises sections are cut and RST double
backticks become Markdown code. A spec without `servers` gets the host it was
fetched from, so the API playground has a base URL. Everything else is kept as
served.

Usage: python scripts/sync_latest_oas.py [--base https://api.newscatcherapi.com]
"""

import argparse
import json
import re
import urllib.request
from pathlib import Path

SPECS = {
    "news-api": "api-reference/news-api/openapi.json",
    "agent": "api-reference/agent/openapi.json",
}
DOCSTRING_SECTION = re.compile(r"\n\s*(Args|Returns|Raises|Yields):\s*\n.*", re.DOTALL)
# Wrong endpoint names in served descriptions, until the service fixes them.
CORRECTIONS = {
    "follow it with `/events`": "follow it with `GET /runs/{run_id}/progress`",
}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "newscatcher-docs-sync"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def clean_description(text):
    text = DOCSTRING_SECTION.sub("", text).strip()
    text = re.sub(r"``([^`]+)``", r"`\1`", text)
    for wrong, right in CORRECTIONS.items():
        text = text.replace(wrong, right)
    return text


def clean(spec, server_url):
    spec.setdefault("servers", [{"url": server_url}])
    for operations in spec.get("paths", {}).values():
        for operation in operations.values():
            if isinstance(operation, dict) and "description" in operation:
                operation["description"] = clean_description(operation["description"])
    for schema in spec.get("components", {}).get("schemas", {}).values():
        if "description" in schema:
            schema["description"] = clean_description(schema["description"])
        for prop in schema.get("properties", {}).values():
            if "description" in prop:
                prop["description"] = clean_description(prop["description"])
    return spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://api.newscatcherapi.com")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    for name, stored_path in SPECS.items():
        spec = clean(fetch(f"{args.base}/{name}/openapi.json"), f"{args.base}/{name}")
        path = root / stored_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
        print(f"{name}: {len(spec['paths'])} paths, v{spec['info']['version']} -> {stored_path}")


if __name__ == "__main__":
    main()
