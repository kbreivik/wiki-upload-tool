from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds


class WikiClientError(Exception):
    """Base exception for WikiClient errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        is_connection_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.is_connection_error = is_connection_error


class WikiClient:
    """GraphQL client for Wiki.js API using urllib (stdlib only)."""

    def __init__(self, url: str, api_key: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._pages_cache: list[dict] | None = None

    def graphql_request(
        self, query: str, variables: dict | None = None
    ) -> dict:
        """Execute a GraphQL request against Wiki.js.

        Retries up to ``_MAX_RETRIES`` times on connection errors with a
        ``_RETRY_DELAY`` second delay between attempts.  HTTP errors (auth
        failures, API errors) are never retried.

        Returns:
            Parsed JSON response dict.

        Raises:
            WikiClientError: On HTTP errors, connection failures, or
                malformed responses.
        """
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/graphql",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # Auth / API errors — never retry
                body = e.read().decode("utf-8") if e.fp else ""
                logger.error("HTTP %d: %s", e.code, body[:500])
                raise WikiClientError(
                    f"HTTP {e.code}: {body[:500]}", status_code=e.code
                ) from e
            except (urllib.error.URLError, OSError) as e:
                # Connection errors — retry with delay
                reason = str(e.reason) if hasattr(e, "reason") else str(e)
                last_error = e
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Connection error (attempt %d/%d): %s — retrying in %ds",
                        attempt,
                        _MAX_RETRIES,
                        reason,
                        _RETRY_DELAY,
                    )
                    time.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "Connection error (attempt %d/%d): %s — giving up",
                        attempt,
                        _MAX_RETRIES,
                        reason,
                    )

        # All retries exhausted
        reason = (
            str(last_error.reason)
            if hasattr(last_error, "reason")
            else str(last_error)
        )
        raise WikiClientError(
            f"Connection error: {reason}", is_connection_error=True
        ) from last_error

    def test_connection(self) -> bool:
        """Test API connectivity with a lightweight query.

        Returns:
            True if connection succeeds.

        Raises:
            WikiClientError: On connection or auth failure.
        """
        self.graphql_request("{ __typename }")
        return True

    def check_page_exists(
        self, path: str, locale: str
    ) -> dict | None:
        """Check if a page exists at the given path.

        Returns:
            Page dict with 'id' and 'title' if found, None otherwise.
        """
        query = """
        query ($path: String!, $locale: String!) {
          pages {
            singleByPath(path: $path, locale: $locale) {
              id
              title
            }
          }
        }
        """
        result = self.graphql_request(query, {"path": path, "locale": locale})
        page = result.get("data", {}).get("pages", {}).get("singleByPath")
        return page if page else None

    def create_page(
        self,
        *,
        content: str,
        description: str,
        editor: str,
        is_published: bool,
        is_private: bool,
        locale: str,
        path: str,
        tags: list[str],
        title: str,
    ) -> dict:
        """Create a new page via Wiki.js GraphQL API."""
        query = """
        mutation (
          $content: String!,
          $description: String!,
          $editor: String!,
          $isPublished: Boolean!,
          $isPrivate: Boolean!,
          $locale: String!,
          $path: String!,
          $tags: [String]!,
          $title: String!
        ) {
          pages {
            create(
              content: $content,
              description: $description,
              editor: $editor,
              isPublished: $isPublished,
              isPrivate: $isPrivate,
              locale: $locale,
              path: $path,
              tags: $tags,
              title: $title
            ) {
              responseResult {
                succeeded
                errorCode
                slug
                message
              }
              page {
                id
                path
                title
              }
            }
          }
        }
        """
        variables = {
            "content": content,
            "description": description,
            "editor": editor,
            "isPublished": is_published,
            "isPrivate": is_private,
            "locale": locale,
            "path": path,
            "tags": tags,
            "title": title,
        }
        return self.graphql_request(query, variables)

    def update_page(
        self,
        *,
        page_id: int,
        content: str,
        description: str,
        tags: list[str],
        title: str,
    ) -> dict:
        """Update an existing page via Wiki.js GraphQL API."""
        query = """
        mutation (
          $id: Int!,
          $content: String,
          $description: String,
          $tags: [String],
          $title: String
        ) {
          pages {
            update(
              id: $id,
              content: $content,
              description: $description,
              tags: $tags,
              title: $title
            ) {
              responseResult {
                succeeded
                errorCode
                message
              }
              page {
                id
                path
                title
              }
            }
          }
        }
        """
        variables = {
            "id": page_id,
            "content": content,
            "description": description,
            "tags": tags,
            "title": title,
        }
        return self.graphql_request(query, variables)

    def delete_page(self, page_id: int) -> dict:
        """Delete a page by ID."""
        query = """
        mutation ($id: Int!) {
          pages {
            delete(id: $id) {
              responseResult {
                succeeded
                errorCode
                message
              }
            }
          }
        }
        """
        return self.graphql_request(query, {"id": page_id})

    def fetch_pages(self, *, force: bool = False) -> list[dict]:
        """Fetch all pages with their tags. Cached after first call.

        Each page dict has keys: ``id``, ``path``, ``title``, ``locale``,
        ``tags`` (list of strings).  If a page has no tags or the field is
        missing, ``tags`` will be an empty list.

        Args:
            force: If ``True``, bypass the cache and re-fetch.

        Returns:
            List of page dicts, ordered by path.
        """
        if self._pages_cache is not None and not force:
            return self._pages_cache

        query = """
        {
          pages {
            list(orderBy: PATH) {
              id
              path
              title
              locale
              tags
            }
          }
        }
        """
        result = self.graphql_request(query)
        raw_pages = result.get("data", {}).get("pages", {}).get("list")
        if not raw_pages:
            self._pages_cache = []
            return self._pages_cache

        # Normalise tags: may be list[str], list[dict], or missing
        pages: list[dict] = []
        for p in raw_pages:
            raw_tags = p.get("tags")
            if raw_tags is None:
                tags: list[str] = []
            elif raw_tags and isinstance(raw_tags[0], dict):
                # Some API versions return [{"tag": "x"}, ...]
                tags = [t.get("tag", "") for t in raw_tags if t.get("tag")]
            elif isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags]
            else:
                tags = []
            pages.append({
                "id": p["id"],
                "path": p["path"],
                "title": p.get("title", ""),
                "locale": p.get("locale", "en"),
                "tags": tags,
            })

        self._pages_cache = pages
        return self._pages_cache

    def invalidate_cache(self) -> None:
        """Clear the cached page list. Call after mutations."""
        self._pages_cache = None

    def fetch_page_list(self) -> list[dict]:
        """Fetch all pages as a flat list (uses cached data).

        Returns page dicts with ``id``, ``path``, ``title``, ``locale``,
        and ``tags`` keys.
        """
        return self.fetch_pages()

    def fetch_tags(self) -> list[str]:
        """Extract all unique tags across all pages, sorted alphabetically.

        Uses the cached page list to avoid a separate API call.
        """
        pages = self.fetch_pages()
        tags: set[str] = set()
        for p in pages:
            tags.update(p.get("tags", []))
        return sorted(tags, key=str.lower)

    def fetch_page_tags(self, page_id: int) -> list[str]:
        """Get tags for a specific page from the cached page list.

        Falls back to fetching page content if not in cache.
        """
        pages = self.fetch_pages()
        for p in pages:
            if p["id"] == page_id:
                return list(p.get("tags", []))
        # Not in cache — fall back to individual fetch
        page = self.fetch_page_content(page_id)
        if not page:
            return []
        return [t["tag"] for t in page.get("tags", [])]

    def fetch_page_content(self, page_id: int) -> dict | None:
        """Fetch a single page's full content and metadata by ID.

        Returns all fields needed for a full page update.
        """
        query = """
        query ($id: Int!) {
          pages {
            single(id: $id) {
              id
              path
              title
              description
              content
              locale
              isPublished
              isPrivate
              tags {
                tag
              }
            }
          }
        }
        """
        result = self.graphql_request(query, {"id": page_id})
        page = result.get("data", {}).get("pages", {}).get("single")
        return page if page else None

    def update_page_tags(self, page_id: int, tags: list[str]) -> dict:
        """Update tags on an existing page.

        Wiki.js v2 requires ALL fields in the update mutation, so this
        fetches the full page first and re-sends everything with only
        tags modified.

        Args:
            page_id: Page ID to update.
            tags: New tags as a list of plain strings.

        Returns:
            GraphQL response dict.

        Raises:
            WikiClientError: If the page cannot be fetched or updated.
        """
        # Tags must be plain strings, never dicts
        tags = [str(t) for t in tags]

        # Fetch full page — Wiki.js v2 update needs all fields
        page = self.fetch_page_content(page_id)
        if not page:
            raise WikiClientError(
                f"Cannot fetch page {page_id} for tag update"
            )

        # Extract existing tags as strings
        existing_tags = [t["tag"] for t in page.get("tags", [])]

        query = """
        mutation (
          $id: Int!,
          $content: String,
          $description: String,
          $isPublished: Boolean,
          $isPrivate: Boolean,
          $locale: String,
          $path: String,
          $tags: [String],
          $title: String
        ) {
          pages {
            update(
              id: $id,
              content: $content,
              description: $description,
              isPublished: $isPublished,
              isPrivate: $isPrivate,
              locale: $locale,
              path: $path,
              tags: $tags,
              title: $title
            ) {
              responseResult {
                succeeded
                errorCode
                message
              }
              page {
                id
                path
                title
              }
            }
          }
        }
        """
        variables = {
            "id": page_id,
            "content": page.get("content", ""),
            "description": page.get("description", ""),
            "isPublished": page.get("isPublished", True),
            "isPrivate": page.get("isPrivate", False),
            "locale": page.get("locale", "en"),
            "path": page.get("path", ""),
            "tags": tags,
            "title": page.get("title", ""),
        }

        logger.debug(
            "update_page_tags: page_id=%d, old_tags=%s, new_tags=%s",
            page_id, existing_tags, tags,
        )
        logger.debug("update_page_tags mutation variables: %s", json.dumps(
            {k: v for k, v in variables.items() if k != "content"},
            indent=2,
        ))

        result = self.graphql_request(query, variables)

        resp = (
            result.get("data", {})
            .get("pages", {})
            .get("update", {})
            .get("responseResult", {})
        )
        logger.debug(
            "update_page_tags response: succeeded=%s, errorCode=%s, message=%s",
            resp.get("succeeded"), resp.get("errorCode"), resp.get("message"),
        )

        return result
