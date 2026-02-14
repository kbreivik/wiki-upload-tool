from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class WikiClientError(Exception):
    """Base exception for WikiClient errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WikiClient:
    """GraphQL client for Wiki.js API using urllib (stdlib only)."""

    def __init__(self, url: str, api_key: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def graphql_request(
        self, query: str, variables: dict | None = None
    ) -> dict:
        """Execute a GraphQL request against Wiki.js.

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

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            logger.error("HTTP %d: %s", e.code, body[:500])
            raise WikiClientError(
                f"HTTP {e.code}: {body[:500]}", status_code=e.code
            ) from e
        except urllib.error.URLError as e:
            logger.error("Connection error: %s", e.reason)
            raise WikiClientError(f"Connection error: {e.reason}") from e

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

    def fetch_page_list(self) -> list[dict]:
        """Fetch all pages as a flat list."""
        query = """
        {
          pages {
            list(orderBy: PATH) {
              id
              path
              title
              locale
            }
          }
        }
        """
        result = self.graphql_request(query)
        pages = result.get("data", {}).get("pages", {}).get("list")
        return pages if pages else []

    def fetch_page_content(self, page_id: int) -> dict | None:
        """Fetch a single page's full content by ID."""
        query = """
        query ($id: Int!) {
          pages {
            single(id: $id) {
              id
              path
              title
              description
              content
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
