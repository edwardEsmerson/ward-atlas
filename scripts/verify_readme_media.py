#!/usr/bin/env python3
"""Check README media, local links and literal accepted Cypher. No app/DB needed.

The first source-only run installs the pinned Pillow wheel project-locally if
no decoder is available. Every image is fully decoded, not just header-sniffed.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from .readme_media_dependencies import image_module
except ImportError:
    from readme_media_dependencies import image_module

ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 600_000
MAX_TOTAL_BYTES = 5_000_000
ACCEPTED_QUERY_SOURCE = "scripts/wardatlas_shift_board.py"


class MediaError(ValueError):
    pass


class HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = "src" if tag == "img" else "href" if tag == "a" else None
        if key and attrs.get(key):
            self.links.append((attrs[key], tag == "img"))


def references(text):
    """Inline/reference Markdown links and HTML images/anchors, excluding code."""
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    definitions = {m[1].strip().casefold(): m[2] for m in re.finditer(
        r'^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?', text, re.M)}
    result = []
    for match in re.finditer(r'(!?)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+["\'][^\n]*?["\'])?\s*\)', text):
        result.append((match[2] or match[3], bool(match[1])))
    for match in re.finditer(r'(!?)\[([^\]]+)\]\[([^\]]*)\]', text):
        key = (match[3] or match[2]).strip().casefold()
        if key not in definitions:
            raise MediaError(f"Undefined relative link reference: {key}")
        result.append((definitions[key], bool(match[1])))
    parser = HTMLLinks()
    parser.feed(text)
    return result + parser.links


def local_target(root, url):
    parsed = urlsplit(html.unescape(url))
    if parsed.scheme or parsed.netloc:
        return None
    target = (root / unquote(parsed.path)).resolve() if parsed.path else root / "README.md"
    if not target.is_relative_to(root.resolve()):
        raise MediaError(f"Relative link escapes repository: {url}")
    if not target.exists():
        raise MediaError(f"Missing image or broken relative link: {url}")
    if parsed.fragment and target.suffix.lower() == ".md":
        content = target.read_text(encoding="utf-8")
        anchors = set(re.findall(r'\bid=["\']([^"\']+)["\']', content))
        seen = {}
        for heading in re.findall(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", content, re.M):
            heading = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
            heading = re.sub(r"<[^>]*>|[`*_]", "", html.unescape(heading)).casefold()
            slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
            count = seen.get(slug, 0)
            anchors.add(slug + (f"-{count}" if count else ""))
            seen[slug] = count + 1
        if unquote(parsed.fragment) not in anchors:
            raise MediaError(f"Broken relative link anchor: {url}")
    return target


def verify(root=ROOT):
    root = Path(root).resolve()
    text = (root / "README.md").read_text(encoding="utf-8")
    plain = re.sub(r"[*_]|<[^>]*>", "", html.unescape(text))
    plain = re.sub(r"\s+", " ", plain).casefold()
    for forbidden in ("have not been verified", "may be rejected"):
        if forbidden in plain:
            raise MediaError(f"Forbidden sentence: {forbidden}")

    all_refs = references(text)
    images = set()
    for url, is_image in all_refs:
        target = local_target(root, url)
        if is_image:
            if target is None:
                raise MediaError(f"README images must be committed locally: {url}")
            images.add(target)
    opening_images = {local_target(root, url) for url, image in references("\n".join(text.splitlines()[:100]))
                      if image and urlsplit(url).path.startswith("docs/media/")}
    if len(opening_images) < 6:
        raise MediaError("At least 6 distinct docs/media screenshots must appear in the first 100 lines")

    Image = image_module()
    total = 0
    decoded = []
    for path in sorted(images):
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise MediaError(f"Oversized image: {path.name}: {size} > {MAX_IMAGE_BYTES}")
        total += size
        try:
            with Image.open(path) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise MediaError(f"Unsupported image format: {path.name}")
                image.load()
                decoded.append({"path": str(path.relative_to(root)), "bytes": size,
                                "format": image.format, "width": image.width, "height": image.height})
        except (OSError, ValueError) as exc:
            raise MediaError(f"Image does not decode: {path.name}: {exc}") from exc
    if total > MAX_TOTAL_BYTES:
        raise MediaError(f"Media total exceeds {MAX_TOTAL_BYTES} bytes: {total}")

    opening = text.split("## Start with your FalkorDB server", 1)[0]
    blocks = re.findall(r"^```cypher\s*\n(.*?)\n```", opening, re.M | re.S)
    if not blocks:
        raise MediaError("Missing opening fenced cypher block")
    source = (root / ACCEPTED_QUERY_SOURCE).read_text(encoding="utf-8")
    # A literal accepted query, not a substring assembled from a comment or example.
    constants = [node.value.strip() for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and node.value.lstrip().startswith("MATCH ")]
    if len(blocks) != 1 or blocks[0].strip() not in constants:
        raise MediaError("Opening Cypher is not verbatim accepted query source")
    if len(re.findall(r"-\[[^\]]*\]->", blocks[0])) < 2:
        raise MediaError("Opening Cypher must be multi-hop")
    return {"status": "PASS", "image_count": len(images), "opening_screenshots": len(opening_images),
            "relative_links_checked": sum(local_target(root, u) is not None for u, _ in all_refs),
            "total_bytes": total, "query_source": ACCEPTED_QUERY_SOURCE, "images": decoded}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-on-zero", action="store_true", help="Always enforced; accepted for workshop checks")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root), indent=2))
        return 0
    except (MediaError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
