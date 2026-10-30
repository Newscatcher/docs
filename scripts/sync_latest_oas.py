"""Fetch the News API and Agent OpenAPI specs into api-reference/.

The Agent spec comes from FastAPI with Python docstrings in its operation
descriptions, so the Args/Returns/Raises sections are cut and RST double
backticks become Markdown code. A spec without `servers` gets the host it was
fetched from, so the API playground has a base URL. The public docs never
mention full article text, so the `content` include option and text fields are
removed, with the sentences that mention them. Everything else is kept as
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
# Sentences that mention full article text, and what replaces them.
FULL_TEXT_WORDING = {
    "`description`, `content`, `entities`": "`description`, `entities`",
    "description, content, entities": "description, entities",
    "; the article text is not checked": "",
    "(then the original text is searched)": "(then the original language is searched)",
    "enterprise_field: content is not included": "enterprise_field: embedding is not included",
    "Sentiment of the article text.": "Sentiment of the article.",
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


def allows_string(schema):
    return schema.get("type") == "string" or any(
        option.get("type") == "string" for option in schema.get("anyOf", [])
    )


def hide_full_text(node):
    """Drop the `content` include option and text fields from every part of a spec."""
    if isinstance(node, dict):
        properties = node.get("properties", {})
        if "content" in properties and allows_string(properties["content"]):
            del properties["content"]
            if "content" in node.get("required", []):
                node["required"].remove("content")
        if "content" in node.get("enum", []) and "embedding" in node["enum"]:
            node["enum"].remove("content")
        for key, value in node.items():
            if isinstance(value, str):
                for wrong, right in FULL_TEXT_WORDING.items():
                    value = value.replace(wrong, right)
                node[key] = value
            else:
                hide_full_text(value)
    elif isinstance(node, list):
        for item in node:
            hide_full_text(item)


def clean(spec, server_url):
    spec.setdefault("servers", [{"url": server_url}])
    hide_full_text(spec)
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
