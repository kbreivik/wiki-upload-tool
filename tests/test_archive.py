from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from src.core.upload import (
    ArchivePageResult,
    ArchiveResult,
    _archive_single_page,
    archive_pages,
    compute_archive_path,
    find_pages_to_archive,
)
from src.core.wiki_client import WikiClient, WikiClientError


# ── find_pages_to_archive ──────────────────────────────────────────


class TestFindPagesToArchive:
    PAGES = [
        {"path": "Docs/Setup", "title": "Setup", "id": 1, "locale": "en"},
        {"path": "Docs/Usage", "title": "Usage", "id": 2, "locale": "en"},
        {"path": "Docs", "title": "Docs Index", "id": 3, "locale": "en"},
        {"path": "Other/Page", "title": "Other", "id": 4, "locale": "en"},
        {"path": "Docs/Setup", "title": "Setup NB", "id": 5, "locale": "nb"},
    ]

    def test_finds_pages_under_base_path(self):
        result = find_pages_to_archive(self.PAGES, "Docs", "en")
        paths = {p["path"] for p in result}
        assert paths == {"Docs", "Docs/Setup", "Docs/Usage"}

    def test_filters_by_locale(self):
        result = find_pages_to_archive(self.PAGES, "Docs", "nb")
        assert len(result) == 1
        assert result[0]["id"] == 5

    def test_no_match_returns_empty(self):
        result = find_pages_to_archive(self.PAGES, "Nonexistent", "en")
        assert result == []

    def test_exact_path_only(self):
        """'Docs' should not match 'DocsExtra'."""
        pages = [
            {"path": "DocsExtra/Page", "title": "X", "id": 1, "locale": "en"},
        ]
        result = find_pages_to_archive(pages, "Docs", "en")
        assert result == []

    def test_empty_page_list(self):
        result = find_pages_to_archive([], "Docs", "en")
        assert result == []


# ── compute_archive_path ────────────────────────────────────────────


class TestComputeArchivePath:
    def test_base_path_itself(self):
        assert compute_archive_path("Docs", "Docs", "arkiv/Docs") == "arkiv/Docs"

    def test_nested_page(self):
        result = compute_archive_path("Docs/Setup", "Docs", "arkiv/Docs")
        assert result == "arkiv/Docs/Setup"

    def test_deeply_nested(self):
        result = compute_archive_path("A/B/C/D", "A/B", "archive/A/B")
        assert result == "archive/A/B/C/D"

    def test_preserves_full_folder_hierarchy(self):
        """Verify the user's exact scenario: nested Docker pages."""
        base = "Dokumentasjon/Docker"
        dest = "Dokumentasjon/Arkiv/Docker"
        assert (
            compute_archive_path("Dokumentasjon/Docker/Dockge", base, dest)
            == "Dokumentasjon/Arkiv/Docker/Dockge"
        )
        assert (
            compute_archive_path("Dokumentasjon/Docker/Oppsett/N8N", base, dest)
            == "Dokumentasjon/Arkiv/Docker/Oppsett/N8N"
        )
        assert (
            compute_archive_path("Dokumentasjon/Docker/Oppsett/Dozzle", base, dest)
            == "Dokumentasjon/Arkiv/Docker/Oppsett/Dozzle"
        )


# ── _archive_single_page ───────────────────────────────────────────


class TestArchiveSinglePage:
    def _make_client(self):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_content.return_value = {
            "id": 1,
            "path": "Docs/Setup",
            "title": "Setup Guide",
            "description": "How to set up",
            "content": "# Setup\nContent here",
            "tags": [{"tag": "docs"}],
        }
        client.create_page.return_value = {
            "data": {"pages": {"create": {
                "responseResult": {"succeeded": True},
                "page": {"id": 100, "path": "arkiv/Docs/Setup", "title": "Setup Guide"},
            }}}
        }
        client.delete_page.return_value = {
            "data": {"pages": {"delete": {
                "responseResult": {"succeeded": True},
            }}}
        }
        return client

    def test_successful_archive(self):
        client = self._make_client()
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")

        assert result.status == "archived"
        assert result.old_path == "Docs/Setup"
        assert result.new_path == "arkiv/Docs/Setup"
        assert result.message == ""

        # Verify "archived" tag was added
        create_call = client.create_page.call_args
        assert "archived" in create_call.kwargs["tags"]

    def test_fetch_content_fails(self):
        client = self._make_client()
        client.fetch_page_content.side_effect = WikiClientError("timeout")
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"
        assert "fetch content" in result.message.lower()

    def test_fetch_content_returns_none(self):
        client = self._make_client()
        client.fetch_page_content.return_value = None
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"

    def test_create_fails(self):
        client = self._make_client()
        client.create_page.side_effect = WikiClientError("forbidden")
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"
        assert "archive copy" in result.message.lower()

    def test_create_returns_failure_response(self):
        client = self._make_client()
        client.create_page.return_value = {
            "data": {"pages": {"create": {
                "responseResult": {"succeeded": False, "errorCode": "ERR", "message": "dup"},
            }}}
        }
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"
        assert "dup" in result.message

    def test_delete_fails_still_archived(self):
        client = self._make_client()
        client.delete_page.side_effect = WikiClientError("timeout")
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "archived"
        assert "could not delete" in result.message.lower()

    def test_delete_response_fails_still_archived(self):
        client = self._make_client()
        client.delete_page.return_value = {
            "data": {"pages": {"delete": {
                "responseResult": {"succeeded": False, "message": "locked"},
            }}}
        }
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _archive_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "archived"
        assert "locked" in result.message

    def test_does_not_duplicate_archived_tag(self):
        client = self._make_client()
        client.fetch_page_content.return_value = {
            "id": 1, "path": "P", "title": "T",
            "content": "x", "tags": [{"tag": "archived"}, {"tag": "docs"}],
        }
        page = {"id": 1, "path": "P", "title": "T"}

        _archive_single_page(client, page, "arkiv/P", "en")

        tags = client.create_page.call_args.kwargs["tags"]
        assert tags.count("archived") == 1


# ── archive_pages ──────────────────────────────────────────────────


class TestArchivePages:
    def _make_client(self):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_content.return_value = {
            "id": 1, "path": "p", "title": "T",
            "content": "c", "tags": [],
        }
        client.create_page.return_value = {
            "data": {"pages": {"create": {
                "responseResult": {"succeeded": True},
                "page": {"id": 100},
            }}}
        }
        client.delete_page.return_value = {
            "data": {"pages": {"delete": {
                "responseResult": {"succeeded": True},
            }}}
        }
        return client

    def test_archives_all_pages(self):
        client = self._make_client()
        pages = [
            {"id": 1, "path": "Docs", "title": "Index"},
            {"id": 2, "path": "Docs/Setup", "title": "Setup"},
        ]

        result = archive_pages(client, pages, "Docs", "arkiv/Docs", "en")

        assert result.archived == 2
        assert result.failed == 0
        assert result.total == 2
        assert len(result.pages) == 2

    def test_cancel_via_callback(self):
        client = self._make_client()
        pages = [
            {"id": 1, "path": "Docs", "title": "A"},
            {"id": 2, "path": "Docs/B", "title": "B"},
            {"id": 3, "path": "Docs/C", "title": "C"},
        ]

        call_count = 0

        def cancel_after_one(current, total, page):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise StopIteration

        result = archive_pages(
            client, pages, "Docs", "arkiv/Docs", "en",
            progress_callback=cancel_after_one,
        )

        # Only the first page should have been processed
        assert result.total == 1
        assert result.archived == 1

    def test_progress_callback_receives_args(self):
        client = self._make_client()
        pages = [{"id": 1, "path": "Docs", "title": "T"}]
        calls = []

        def cb(current, total, page):
            calls.append((current, total, page["path"]))

        archive_pages(
            client, pages, "Docs", "arkiv/Docs", "en",
            progress_callback=cb,
        )

        assert calls == [(0, 1, "Docs")]
