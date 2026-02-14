from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from src.core.upload import (
    ArchivePageResult,
    ArchiveResult,
    _move_single_page,
    archive_pages,
    compute_dest_path,
    find_pages_to_archive,
    move_pages,
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


# ── compute_dest_path ──────────────────────────────────────────────


class TestComputeDestPath:
    """Test destination path computation for both archive and move."""

    # -- include_folder_name=True (archive mode) --

    def test_archive_base_page(self):
        # Source page itself → dest_root/folder_name
        assert compute_dest_path("Docs", "Docs", "arkiv") == "arkiv/Docs"

    def test_archive_nested_page(self):
        result = compute_dest_path("Docs/Setup", "Docs", "arkiv")
        assert result == "arkiv/Docs/Setup"

    def test_archive_deeply_nested(self):
        result = compute_dest_path("A/B/C/D", "A/B", "archive")
        assert result == "archive/B/C/D"

    def test_archive_preserves_hierarchy(self):
        """Archive Docker folder into Arkiv — folder name preserved."""
        src = "Dokumentasjon/Docker"
        root = "Dokumentasjon/Arkiv"
        assert (
            compute_dest_path("Dokumentasjon/Docker", src, root)
            == "Dokumentasjon/Arkiv/Docker"
        )
        assert (
            compute_dest_path("Dokumentasjon/Docker/Dockge", src, root)
            == "Dokumentasjon/Arkiv/Docker/Dockge"
        )
        assert (
            compute_dest_path("Dokumentasjon/Docker/Oppsett/N8N", src, root)
            == "Dokumentasjon/Arkiv/Docker/Oppsett/N8N"
        )

    def test_archive_no_path_duplication(self):
        """The exact bug scenario: no duplication of folder names."""
        src = "Dokumentasjon/ARKIV"
        root = "Dokumentasjon/ARKIV/VMCloneManager"
        # Source folder name "ARKIV" is appended to root
        assert (
            compute_dest_path("Dokumentasjon/ARKIV/Feilsoking", src, root)
            == "Dokumentasjon/ARKIV/VMCloneManager/ARKIV/Feilsoking"
        )
        # ^ This IS correct: user chose root=".../VMCloneManager", so
        # the result is VMCloneManager/ARKIV/... If the user didn't want
        # ARKIV in the path, they'd pick a different root.

    def test_archive_vmclonemanager_scenario(self):
        """Moving VMCloneManager into ARKIV."""
        src = "arkiv/Dokumentasjon/Arkiv/VMCloneManager"
        root = "Dokumentasjon/ARKIV"
        assert (
            compute_dest_path(
                "arkiv/Dokumentasjon/Arkiv/VMCloneManager", src, root
            )
            == "Dokumentasjon/ARKIV/VMCloneManager"
        )
        assert (
            compute_dest_path(
                "arkiv/Dokumentasjon/Arkiv/VMCloneManager/Feilsoking", src, root
            )
            == "Dokumentasjon/ARKIV/VMCloneManager/Feilsoking"
        )

    # -- include_folder_name=False (move without folder) --

    def test_move_without_folder_base_page(self):
        # Source page itself → just dest_root
        result = compute_dest_path("Docs", "Docs", "Target", include_folder_name=False)
        assert result == "Target"

    def test_move_without_folder_nested(self):
        # Contents dumped directly into dest
        result = compute_dest_path("Docs/Setup", "Docs", "Target", include_folder_name=False)
        assert result == "Target/Setup"

    def test_move_without_folder_deeply_nested(self):
        result = compute_dest_path("A/B/C/D", "A/B", "X", include_folder_name=False)
        assert result == "X/C/D"

    # -- preview matches "Will archive to" / "Will move to" --

    def test_preview_matches_dest_label(self):
        """Every destination path starts with the computed effective dest."""
        src = "Dokumentasjon/Docker"
        root = "Dokumentasjon/Arkiv"
        effective_dest = f"{root}/{src.rsplit('/', 1)[-1]}"
        # effective_dest = "Dokumentasjon/Arkiv/Docker"
        pages = [
            "Dokumentasjon/Docker",
            "Dokumentasjon/Docker/Dockge",
            "Dokumentasjon/Docker/N8N/Oppsett",
        ]
        for page_path in pages:
            dest = compute_dest_path(page_path, src, root, include_folder_name=True)
            assert dest.startswith(effective_dest), (
                f"{dest!r} does not start with {effective_dest!r}"
            )


# ── _move_single_page ─────────────────────────────────────────────


class TestMoveSinglePage:
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

    def test_successful_move(self):
        client = self._make_client()
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")

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

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"
        assert "fetch content" in result.message.lower()

    def test_fetch_content_returns_none(self):
        client = self._make_client()
        client.fetch_page_content.return_value = None
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"

    def test_create_fails(self):
        client = self._make_client()
        client.create_page.side_effect = WikiClientError("forbidden")
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
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

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "failed"
        assert "dup" in result.message

    def test_delete_fails_still_archived(self):
        client = self._make_client()
        client.delete_page.side_effect = WikiClientError("timeout")
        page = {"id": 1, "path": "Docs/Setup", "title": "Setup Guide"}

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
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

        result = _move_single_page(client, page, "arkiv/Docs/Setup", "en")
        assert result.status == "archived"
        assert "locked" in result.message

    def test_does_not_duplicate_archived_tag(self):
        client = self._make_client()
        client.fetch_page_content.return_value = {
            "id": 1, "path": "P", "title": "T",
            "content": "x", "tags": [{"tag": "archived"}, {"tag": "docs"}],
        }
        page = {"id": 1, "path": "P", "title": "T"}

        _move_single_page(client, page, "arkiv/P", "en")

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

        result = archive_pages(client, pages, "Docs", "arkiv", "en")

        assert result.archived == 2
        assert result.failed == 0
        assert result.total == 2
        assert len(result.pages) == 2
        # Verify archive appends folder name
        dest_paths = {p.new_path for p in result.pages}
        assert "arkiv/Docs" in dest_paths
        assert "arkiv/Docs/Setup" in dest_paths

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
            client, pages, "Docs", "arkiv", "en",
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
            client, pages, "Docs", "arkiv", "en",
            progress_callback=cb,
        )

        assert calls == [(0, 1, "Docs")]


# ── move_pages ─────────────────────────────────────────────────────


class TestMovePages:
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

    def test_move_with_folder_name(self):
        client = self._make_client()
        pages = [
            {"id": 1, "path": "Docs", "title": "Index"},
            {"id": 2, "path": "Docs/Setup", "title": "Setup"},
        ]

        result = move_pages(
            client, pages, "Docs", "Target", "en",
            include_source_folder=True,
        )

        assert result.archived == 2
        dest_paths = {p.new_path for p in result.pages}
        assert "Target/Docs" in dest_paths
        assert "Target/Docs/Setup" in dest_paths

    def test_move_without_folder_name(self):
        client = self._make_client()
        pages = [
            {"id": 1, "path": "Docs", "title": "Index"},
            {"id": 2, "path": "Docs/Setup", "title": "Setup"},
        ]

        result = move_pages(
            client, pages, "Docs", "Target", "en",
            include_source_folder=False,
        )

        assert result.archived == 2
        dest_paths = {p.new_path for p in result.pages}
        assert "Target" in dest_paths
        assert "Target/Setup" in dest_paths

    def test_move_nested_structure(self):
        client = self._make_client()
        pages = [
            {"id": 1, "path": "A/B", "title": "B"},
            {"id": 2, "path": "A/B/C", "title": "C"},
            {"id": 3, "path": "A/B/C/D", "title": "D"},
        ]

        result = move_pages(
            client, pages, "A/B", "X/Y", "en",
            include_source_folder=True,
        )

        dest_paths = {p.new_path for p in result.pages}
        assert dest_paths == {"X/Y/B", "X/Y/B/C", "X/Y/B/C/D"}
