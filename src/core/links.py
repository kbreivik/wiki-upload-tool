from __future__ import annotations

import logging
import re

from src.core.discovery import generate_slug

logger = logging.getLogger(__name__)


def build_link_map(
    files: list[dict[str, str]],
    base_path: str,
    locale: str,
    index_file: str = "README.md",
) -> dict[str, str]:
    """Build a mapping from local .md filenames to wiki.js absolute paths."""
    link_map: dict[str, str] = {}
    for file_info in files:
        filename = file_info["filename"]
        slug = file_info.get("slug", generate_slug(filename, index_file))

        if slug:
            wiki_path = f"/{locale}/{base_path}/{slug}"
        else:
            wiki_path = f"/{locale}/{base_path}"
        link_map[filename] = wiki_path

    return link_map


def rewrite_links(content: str, link_map: dict[str, str]) -> str:
    """Replace internal markdown links with wiki.js paths."""

    def replace_link(match: re.Match[str]) -> str:
        text = match.group(1)
        filename = match.group(2)
        if filename in link_map:
            return f"[{text}]({link_map[filename]})"
        return match.group(0)

    # Match [text](filename.md) patterns
    return re.sub(r"\[([^\]]+)\]\(([^)]+\.md)\)", replace_link, content)


def strip_footer_by_patterns(content: str, patterns: list[str]) -> str:
    """
    Remove footer content matching any of the given regex patterns.

    Args:
        content: The markdown content.
        patterns: List of regex patterns to match against trailing lines.

    Returns:
        Content with matching footer lines removed.
    """
    if not patterns:
        return content

    lines = content.rstrip().split("\n")

    # Compile patterns
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    # Remove trailing lines that match any pattern
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue

        matched = False
        for pattern in compiled:
            if pattern.search(last):
                matched = True
                break

        if matched:
            lines.pop()
            # Also remove preceding --- separator and blank lines
            while lines and lines[-1].strip() in ("", "---"):
                lines.pop()
        else:
            break

    return "\n".join(lines) + "\n"
