from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def discover_files(
    source_dir: str | Path, index_file: str = "README.md"
) -> list[dict[str, str]]:
    """
    Discover all markdown files in a directory.

    Returns:
        List of dicts with 'filename' and 'filepath' keys,
        sorted with index file first, then alphabetically.
    """
    source_path = Path(source_dir)
    files: list[dict[str, str]] = []
    index_entry: dict[str, str] | None = None

    for path in source_path.iterdir():
        if not path.is_file() or path.suffix.lower() != ".md":
            continue

        entry = {"filename": path.name, "filepath": str(path)}

        if path.name.lower() == index_file.lower():
            index_entry = entry
        else:
            files.append(entry)

    # Sort alphabetically
    files.sort(key=lambda x: x["filename"])

    # Put index file first
    if index_entry:
        files.insert(0, index_entry)

    return files


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """
    Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (metadata_dict, content_without_frontmatter).
    """
    metadata: dict[str, str] = {}

    # Check if content starts with frontmatter delimiter
    if not content.startswith("---"):
        return metadata, content

    # Find the closing delimiter
    lines = content.split("\n")
    end_index = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = i
            break

    if end_index is None:
        return metadata, content

    # Parse the frontmatter (simple key: value parsing)
    frontmatter_lines = lines[1:end_index]
    for line in frontmatter_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if value:
                metadata[key] = value

    # Return content without frontmatter
    content_lines = lines[end_index + 1 :]
    # Remove leading blank lines
    while content_lines and not content_lines[0].strip():
        content_lines.pop(0)

    return metadata, "\n".join(content_lines)


def extract_title_from_content(content: str) -> str | None:
    """Extract the first # heading as title."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


def generate_slug(filename: str, index_file: str = "README.md") -> str:
    """
    Generate a clean URL slug from a filename.

    Examples:
        README.md -> '' (empty for index)
        01-Oversikt.md -> 'Oversikt'
        Getting-Started.md -> 'Getting-Started'
        my_page.md -> 'my-page'
    """
    # Index file maps to empty slug (base path)
    if filename.lower() == index_file.lower():
        return ""

    # Remove .md extension
    slug = filename[:-3] if filename.lower().endswith(".md") else filename

    # Remove leading number prefix (e.g., 01-, 02-)
    slug = re.sub(r"^\d+[-_]", "", slug)

    # Replace underscores with hyphens
    slug = slug.replace("_", "-")

    return slug
