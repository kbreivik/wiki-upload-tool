from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.core.config import Config
from src.core.discovery import (
    extract_title_from_content,
    generate_slug,
    parse_frontmatter,
)
from src.core.links import rewrite_links, strip_footer_by_patterns
from src.core.wiki_client import WikiClient, WikiClientError

logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    """Result of uploading a single page."""

    filename: str
    path: str
    title: str
    status: str  # "created", "updated", "skipped", "failed"
    message: str = ""
    page_id: int | None = None


@dataclass
class UploadResult:
    """Aggregate result of an upload batch."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    pages: list[PageResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed


def process_file(
    file_info: dict[str, str],
    link_map: dict[str, str],
    config: Config,
) -> dict[str, str]:
    """Process a single markdown file for upload.

    Args:
        file_info: Dict with 'filename' and 'filepath' keys.
        link_map: Mapping from local .md filenames to wiki.js absolute paths.
        config: Upload configuration.

    Returns:
        Dict with keys: filename, filepath, path, slug, title,
        description, content.
    """
    filepath = file_info["filepath"]
    filename = file_info["filename"]

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse frontmatter
    metadata, content = parse_frontmatter(content)

    # Strip footer if patterns provided
    if config.strip_footer_patterns:
        content = strip_footer_by_patterns(content, config.strip_footer_patterns)

    # Rewrite internal links
    content = rewrite_links(content, link_map)

    # Generate slug
    slug = metadata.get("slug", generate_slug(filename, config.index_file))

    # Build wiki.js path
    if slug:
        path = f"{config.base_path}/{slug}"
    else:
        path = config.base_path

    # Get title: frontmatter > first heading > filename
    title = (
        metadata.get("title")
        or extract_title_from_content(content)
        or filename[:-3]
    )

    # Get description from frontmatter or empty
    description = metadata.get("description", "")

    return {
        "filename": filename,
        "filepath": filepath,
        "path": path,
        "slug": slug,
        "title": title,
        "description": description,
        "content": content,
    }


ProgressCallback = Callable[[int, int, dict[str, str]], None]


def upload_pages(
    client: WikiClient,
    pages: list[dict[str, str]],
    config: Config,
    progress_callback: ProgressCallback | None = None,
) -> UploadResult:
    """Upload a list of processed pages to Wiki.js.

    Args:
        client: WikiClient instance.
        pages: List of processed page dicts (from process_file).
        config: Upload configuration.
        progress_callback: Optional callback(current, total, page_info)
            called between pages. Return value is ignored. If the callback
            raises StopIteration, the upload loop stops early.

    Returns:
        UploadResult with per-page details.
    """
    result = UploadResult()

    for i, page in enumerate(pages):
        if progress_callback:
            try:
                progress_callback(i, len(pages), page)
            except StopIteration:
                logger.info("Upload cancelled by callback at page %d/%d", i, len(pages))
                break

        page_result = _upload_single_page(client, page, config)
        result.pages.append(page_result)

        match page_result.status:
            case "created":
                result.created += 1
            case "updated":
                result.updated += 1
            case "skipped":
                result.skipped += 1
            case "failed":
                result.failed += 1

    return result


def _upload_single_page(
    client: WikiClient,
    page: dict[str, str],
    config: Config,
) -> PageResult:
    """Upload or update a single page."""
    try:
        existing = client.check_page_exists(page["path"], config.locale)
    except WikiClientError as e:
        logger.error("Failed to check page %s: %s", page["path"], e)
        return PageResult(
            filename=page["filename"],
            path=page["path"],
            title=page["title"],
            status="failed",
            message=str(e),
        )

    if existing:
        if not config.update_existing:
            logger.info(
                "Page exists (id: %s), skipping: %s",
                existing["id"],
                page["path"],
            )
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="skipped",
                page_id=existing["id"],
                message="Page exists (use update_existing to overwrite)",
            )

        # Update existing page
        try:
            api_result = client.update_page(
                page_id=existing["id"],
                content=page["content"],
                description=page["description"],
                tags=config.tags,
                title=page["title"],
            )
        except WikiClientError as e:
            logger.error("Failed to update page %s: %s", page["path"], e)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="failed",
                message=str(e),
            )

        resp = (
            api_result.get("data", {})
            .get("pages", {})
            .get("update", {})
            .get("responseResult", {})
        )
        if resp.get("succeeded"):
            page_id = (
                api_result.get("data", {})
                .get("pages", {})
                .get("update", {})
                .get("page", {})
                .get("id")
            )
            logger.info("Updated page %s (id: %s)", page["path"], page_id)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="updated",
                page_id=page_id,
            )
        else:
            msg = f"{resp.get('errorCode', 'unknown')}: {resp.get('message', 'no details')}"
            logger.error("Failed to update page %s: %s", page["path"], msg)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="failed",
                message=msg,
            )
    else:
        # Create new page
        try:
            api_result = client.create_page(
                content=page["content"],
                description=page["description"],
                editor="markdown",
                is_published=True,
                is_private=False,
                locale=config.locale,
                path=page["path"],
                tags=config.tags,
                title=page["title"],
            )
        except WikiClientError as e:
            logger.error("Failed to create page %s: %s", page["path"], e)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="failed",
                message=str(e),
            )

        resp = (
            api_result.get("data", {})
            .get("pages", {})
            .get("create", {})
            .get("responseResult", {})
        )
        if resp.get("succeeded"):
            page_id = (
                api_result.get("data", {})
                .get("pages", {})
                .get("create", {})
                .get("page", {})
                .get("id")
            )
            logger.info("Created page %s (id: %s)", page["path"], page_id)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="created",
                page_id=page_id,
            )
        else:
            msg = f"{resp.get('errorCode', 'unknown')}: {resp.get('message', 'no details')}"
            logger.error("Failed to create page %s: %s", page["path"], msg)
            return PageResult(
                filename=page["filename"],
                path=page["path"],
                title=page["title"],
                status="failed",
                message=msg,
            )


# ── Archive ─────────────────────────────────────────────────────────


@dataclass
class ArchivePageResult:
    """Result of archiving a single page."""

    old_path: str
    new_path: str
    title: str
    status: str  # "archived", "failed"
    message: str = ""


@dataclass
class ArchiveResult:
    """Aggregate result of an archive batch."""

    archived: int = 0
    failed: int = 0
    pages: list[ArchivePageResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.archived + self.failed


def find_pages_to_archive(
    all_pages: list[dict],
    base_path: str,
    locale: str,
) -> list[dict]:
    """Filter pages that fall under *base_path* and match *locale*.

    Args:
        all_pages: Full page list from ``WikiClient.fetch_page_list()``.
        base_path: Wiki path prefix to match (e.g. ``"Documentation/MyProject"``).
        locale: Locale code to filter on.

    Returns:
        Subset of *all_pages* whose path equals or is nested under *base_path*.
    """
    return [
        p
        for p in all_pages
        if (p["path"] == base_path or p["path"].startswith(base_path + "/"))
        and p.get("locale", "en") == locale
    ]


def compute_archive_path(
    old_path: str, base_path: str, archive_path: str
) -> str:
    """Compute the destination path for a page being archived.

    The portion of *old_path* after *base_path* is appended to *archive_path*.
    """
    relative = old_path[len(base_path) :].lstrip("/")
    if relative:
        return f"{archive_path}/{relative}"
    return archive_path


def _archive_single_page(
    client: WikiClient,
    page: dict,
    new_path: str,
    locale: str,
) -> ArchivePageResult:
    """Copy a page to *new_path*, tag it ``"archived"``, then delete the original."""
    old_path = page["path"]
    title = page.get("title", old_path)

    # Fetch full content
    try:
        full_page = client.fetch_page_content(page["id"])
    except WikiClientError as e:
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="failed", message=f"Could not fetch content: {e}",
        )

    if not full_page:
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="failed", message="Could not fetch page content",
        )

    # Create archive copy
    tags = [t["tag"] for t in full_page.get("tags", [])]
    if "archived" not in tags:
        tags.append("archived")

    try:
        api_result = client.create_page(
            content=full_page["content"],
            description=full_page.get("description", ""),
            editor="markdown",
            is_published=True,
            is_private=False,
            locale=locale,
            path=new_path,
            tags=tags,
            title=full_page["title"],
        )
    except WikiClientError as e:
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="failed", message=f"Could not create archive copy: {e}",
        )

    resp = (
        api_result.get("data", {})
        .get("pages", {})
        .get("create", {})
        .get("responseResult", {})
    )
    if not resp.get("succeeded"):
        msg = f"{resp.get('errorCode', 'unknown')}: {resp.get('message', 'no details')}"
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="failed", message=f"Create failed: {msg}",
        )

    # Delete original
    try:
        del_result = client.delete_page(page["id"])
    except WikiClientError as e:
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="archived",
            message=f"Archived but could not delete original: {e}",
        )

    del_resp = (
        del_result.get("data", {})
        .get("pages", {})
        .get("delete", {})
        .get("responseResult", {})
    )
    if del_resp.get("succeeded"):
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="archived",
        )
    else:
        msg = del_resp.get("message", "unknown error")
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="archived",
            message=f"Archived but delete failed: {msg}",
        )


ArchiveProgressCallback = Callable[[int, int, dict], None]


def archive_pages(
    client: WikiClient,
    pages: list[dict],
    base_path: str,
    archive_path: str,
    locale: str,
    progress_callback: ArchiveProgressCallback | None = None,
) -> ArchiveResult:
    """Archive pages by copying to *archive_path* and deleting originals.

    Args:
        client: WikiClient instance.
        pages: Pages to archive (from :func:`find_pages_to_archive`).
        base_path: Source base path.
        archive_path: Destination archive path.
        locale: Wiki locale.
        progress_callback: Optional ``callback(current, total, page)``.
            Raise ``StopIteration`` to cancel.

    Returns:
        :class:`ArchiveResult` with per-page details.
    """
    result = ArchiveResult()

    for i, page in enumerate(pages):
        if progress_callback:
            try:
                progress_callback(i, len(pages), page)
            except StopIteration:
                logger.info(
                    "Archive cancelled at page %d/%d", i, len(pages)
                )
                break

        new_path = compute_archive_path(page["path"], base_path, archive_path)
        page_result = _archive_single_page(client, page, new_path, locale)
        result.pages.append(page_result)

        if page_result.status == "archived":
            result.archived += 1
        else:
            result.failed += 1

    return result


# ── Tree helpers ────────────────────────────────────────────────────


def build_tree(pages: list[dict]) -> dict:
    """Build a nested dict tree from a flat list of page dicts with 'path' keys."""
    tree: dict = {}
    for page in pages:
        parts = page["path"].split("/")
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {"_children": {}}
            elif "_children" not in node[part]:
                node[part]["_children"] = {}
            node = node[part]["_children"]
        leaf = parts[-1]
        if leaf not in node:
            node[leaf] = {"_page": page}
        else:
            node[leaf]["_page"] = page
    return tree


def preview_placement(
    wiki_pages: list[dict],
    base_path: str,
    local_files: list[dict[str, str]],
    locale: str,
    index_file: str = "README.md",
) -> dict:
    """
    Build a merged tree of existing wiki pages + proposed new pages.

    Returns:
        Nested dict tree with '_label' markers ("[NEW]" or "[EXISTS]").
    """
    # Build set of existing paths
    existing_paths = {p["path"] for p in wiki_pages}

    # Build tree from existing pages under base_path or its parent
    parent_path = "/".join(base_path.split("/")[:-1]) if "/" in base_path else ""
    relevant_pages = []
    for p in wiki_pages:
        path = p["path"]
        if parent_path and path.startswith(parent_path):
            relevant_pages.append(p)
        elif not parent_path and "/" not in path:
            relevant_pages.append(p)

    tree = build_tree(relevant_pages)

    # Add proposed new pages to tree
    for file_info in local_files:
        filename = file_info["filename"]
        slug = generate_slug(filename, index_file)

        if slug:
            wiki_path = f"{base_path}/{slug}"
        else:
            wiki_path = base_path

        label = "[EXISTS]" if wiki_path in existing_paths else "[NEW]"

        parts = wiki_path.split("/")
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {"_children": {}}
            elif "_children" not in node[part]:
                node[part]["_children"] = {}
            node = node[part]["_children"]
        leaf = parts[-1]
        if leaf not in node:
            node[leaf] = {"_label": label}
        else:
            node[leaf]["_label"] = label

    return tree
