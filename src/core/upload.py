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

_MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class PageResult:
    """Result of uploading a single page."""

    filename: str
    path: str
    title: str
    status: str  # "created", "updated", "skipped", "failed"
    message: str = ""
    page_id: int | None = None
    is_connection_error: bool = False


@dataclass
class UploadResult:
    """Aggregate result of an upload batch."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    pages: list[PageResult] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str = ""

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

    Stops early if ``_MAX_CONSECUTIVE_FAILURES`` consecutive pages fail
    with connection errors, setting ``stopped_early`` on the result.

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
    consecutive_conn_failures = 0

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
                consecutive_conn_failures = 0
            case "updated":
                result.updated += 1
                consecutive_conn_failures = 0
            case "skipped":
                result.skipped += 1
                consecutive_conn_failures = 0
            case "failed":
                result.failed += 1
                if page_result.is_connection_error:
                    consecutive_conn_failures += 1
                else:
                    consecutive_conn_failures = 0

        if consecutive_conn_failures >= _MAX_CONSECUTIVE_FAILURES:
            reason = (
                f"Connection lost after {_MAX_CONSECUTIVE_FAILURES} "
                f"consecutive failures. {result.total} of {len(pages)} "
                f"pages processed."
            )
            logger.error(reason)
            result.stopped_early = True
            result.stop_reason = reason
            break

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
            is_connection_error=e.is_connection_error,
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
                is_connection_error=e.is_connection_error,
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
                is_connection_error=e.is_connection_error,
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


# ── Tag management ──────────────────────────────────────────────────


@dataclass
class TagOperation:
    """Describes a tag change for a single page."""

    page_id: int
    page_path: str
    page_title: str
    current_tags: list[str] = field(default_factory=list)
    new_tags: list[str] = field(default_factory=list)


@dataclass
class TagResult:
    """Aggregate result of a bulk tag operation."""

    updated: int = 0
    skipped: int = 0
    failed: int = 0
    stopped_early: bool = False
    stop_reason: str = ""

    @property
    def total(self) -> int:
        return self.updated + self.skipped + self.failed


TagProgressCallback = Callable[[int, int, TagOperation], None]


def apply_tag_changes(
    client: WikiClient,
    operations: list[TagOperation],
    progress_callback: TagProgressCallback | None = None,
) -> TagResult:
    """Apply tag changes to wiki pages.

    Skips pages where ``current_tags`` and ``new_tags`` are identical.
    Stops early if ``_MAX_CONSECUTIVE_FAILURES`` consecutive pages fail
    with connection errors.

    Args:
        client: WikiClient instance.
        operations: List of tag operations to apply.
        progress_callback: Optional ``callback(current, total, operation)``.
            Raise ``StopIteration`` to cancel.

    Returns:
        TagResult with counts of updated, skipped, and failed pages.
    """
    result = TagResult()
    consecutive_conn_failures = 0

    for i, op in enumerate(operations):
        if progress_callback:
            try:
                progress_callback(i, len(operations), op)
            except StopIteration:
                logger.info(
                    "Tag operation cancelled at page %d/%d", i, len(operations)
                )
                break

        if sorted(op.current_tags, key=str.lower) == sorted(op.new_tags, key=str.lower):
            result.skipped += 1
            consecutive_conn_failures = 0
            continue

        try:
            api_result = client.update_page_tags(op.page_id, op.new_tags)
        except WikiClientError as e:
            logger.error(
                "Failed to update tags on %s: %s", op.page_path, e
            )
            result.failed += 1
            if e.is_connection_error:
                consecutive_conn_failures += 1
            else:
                consecutive_conn_failures = 0
            if consecutive_conn_failures >= _MAX_CONSECUTIVE_FAILURES:
                reason = (
                    f"Connection lost after {_MAX_CONSECUTIVE_FAILURES} "
                    f"consecutive failures. {result.total} of "
                    f"{len(operations)} pages processed."
                )
                logger.error(reason)
                result.stopped_early = True
                result.stop_reason = reason
                break
            continue

        resp = (
            api_result.get("data", {})
            .get("pages", {})
            .get("update", {})
            .get("responseResult", {})
        )
        if resp.get("succeeded"):
            logger.info("Updated tags on %s", op.page_path)
            result.updated += 1
            consecutive_conn_failures = 0
        else:
            msg = f"{resp.get('errorCode', 'unknown')}: {resp.get('message', 'no details')}"
            logger.error("Failed to update tags on %s: %s", op.page_path, msg)
            result.failed += 1
            consecutive_conn_failures = 0

    return result


# ── Archive ─────────────────────────────────────────────────────────


@dataclass
class ArchivePageResult:
    """Result of archiving a single page."""

    old_path: str
    new_path: str
    title: str
    status: str  # "archived", "moved", or "failed"
    message: str = ""
    is_connection_error: bool = False


@dataclass
class ArchiveResult:
    """Aggregate result of an archive batch."""

    archived: int = 0
    failed: int = 0
    pages: list[ArchivePageResult] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str = ""

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


def compute_dest_path(
    page_path: str,
    source_path: str,
    dest_root: str,
    include_folder_name: bool = True,
) -> str:
    """Compute the destination path for a page being moved or archived.

    Args:
        page_path: The page's current wiki path.
        source_path: The source folder being moved (e.g. ``"A/B"``).
        dest_root: The destination container path (e.g. ``"X"``).
        include_folder_name: If ``True``, the last component of
            *source_path* is appended to *dest_root*.  For example,
            ``("A/B/C", "A/B", "X", True)`` produces ``"X/B/C"``.
            If ``False``, contents are placed directly under *dest_root*:
            ``("A/B/C", "A/B", "X", False)`` produces ``"X/C"``.
    """
    relative = page_path[len(source_path) :].lstrip("/")
    if include_folder_name:
        folder_name = source_path.rsplit("/", 1)[-1]
        base = f"{dest_root}/{folder_name}"
    else:
        base = dest_root
    if relative:
        return f"{base}/{relative}"
    return base


def _move_single_page(
    client: WikiClient,
    page: dict,
    new_path: str,
    locale: str,
    success_status: str = "archived",
) -> ArchivePageResult:
    """Copy a page to *new_path*, tag it ``"archived"``, then delete the original.

    Used by both archive and move operations.  *success_status* controls
    the status string on success (``"archived"`` or ``"moved"``).
    """
    old_path = page["path"]
    title = page.get("title", old_path)

    # Fetch full content
    try:
        full_page = client.fetch_page_content(page["id"])
    except WikiClientError as e:
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status="failed", message=f"Could not fetch content: {e}",
            is_connection_error=e.is_connection_error,
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
            is_connection_error=e.is_connection_error,
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
            status=success_status,
            message=f"Copied but could not delete original: {e}",
            is_connection_error=e.is_connection_error,
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
            status=success_status,
        )
    else:
        msg = del_resp.get("message", "unknown error")
        return ArchivePageResult(
            old_path=old_path, new_path=new_path, title=title,
            status=success_status,
            message=f"Copied but delete failed: {msg}",
        )


ArchiveProgressCallback = Callable[[int, int, dict], None]


def archive_pages(
    client: WikiClient,
    pages: list[dict],
    source_path: str,
    archive_root: str,
    locale: str,
    progress_callback: ArchiveProgressCallback | None = None,
) -> ArchiveResult:
    """Archive pages by copying to *archive_root*/{folder_name}/... and deleting originals.

    The source folder name is automatically appended to *archive_root*.

    Args:
        client: WikiClient instance.
        pages: Pages to archive (from :func:`find_pages_to_archive`).
        source_path: Source folder being archived (e.g. ``"Docs/Docker"``).
        archive_root: Container where archived pages go (e.g. ``"Docs/Arkiv"``).
        locale: Wiki locale.
        progress_callback: Optional ``callback(current, total, page)``.
            Raise ``StopIteration`` to cancel.
    """
    return _execute_page_moves(
        client, pages, source_path, archive_root, locale,
        include_folder_name=True,
        success_status="archived",
        progress_callback=progress_callback,
    )


def move_pages(
    client: WikiClient,
    pages: list[dict],
    source_path: str,
    dest_path: str,
    locale: str,
    include_source_folder: bool = True,
    progress_callback: ArchiveProgressCallback | None = None,
) -> ArchiveResult:
    """Move pages from *source_path* to *dest_path*.

    Args:
        client: WikiClient instance.
        pages: Pages to move (from :func:`find_pages_to_archive`).
        source_path: Source folder being moved.
        dest_path: Destination path.
        locale: Wiki locale.
        include_source_folder: If ``True`` (default), the source folder name
            is appended to *dest_path*. If ``False``, contents are placed
            directly under *dest_path*.
        progress_callback: Optional ``callback(current, total, page)``.
            Raise ``StopIteration`` to cancel.
    """
    return _execute_page_moves(
        client, pages, source_path, dest_path, locale,
        include_folder_name=include_source_folder,
        success_status="moved",
        progress_callback=progress_callback,
    )


def _execute_page_moves(
    client: WikiClient,
    pages: list[dict],
    source_path: str,
    dest_root: str,
    locale: str,
    include_folder_name: bool,
    success_status: str = "archived",
    progress_callback: ArchiveProgressCallback | None = None,
) -> ArchiveResult:
    """Shared implementation for archive and move operations.

    Stops early if ``_MAX_CONSECUTIVE_FAILURES`` consecutive pages fail
    with connection errors.
    """
    result = ArchiveResult()
    consecutive_conn_failures = 0

    for i, page in enumerate(pages):
        if progress_callback:
            try:
                progress_callback(i, len(pages), page)
            except StopIteration:
                logger.info(
                    "Operation cancelled at page %d/%d", i, len(pages)
                )
                break

        new_path = compute_dest_path(
            page["path"], source_path, dest_root, include_folder_name
        )
        page_result = _move_single_page(
            client, page, new_path, locale, success_status=success_status
        )
        result.pages.append(page_result)

        if page_result.status != "failed":
            result.archived += 1
            consecutive_conn_failures = 0
        else:
            result.failed += 1
            if page_result.is_connection_error:
                consecutive_conn_failures += 1
            else:
                consecutive_conn_failures = 0

        if consecutive_conn_failures >= _MAX_CONSECUTIVE_FAILURES:
            reason = (
                f"Connection lost after {_MAX_CONSECUTIVE_FAILURES} "
                f"consecutive failures. {result.total} of {len(pages)} "
                f"pages processed."
            )
            logger.error(reason)
            result.stopped_early = True
            result.stop_reason = reason
            break

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
