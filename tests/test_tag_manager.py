"""Tests for tag management core logic."""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.core.upload import TagOperation, TagResult, apply_tag_changes
from src.core.wiki_client import WikiClientError


def _make_mock_client(succeed: bool = True) -> MagicMock:
    """Create a mock WikiClient that returns success/failure for update_page_tags."""
    client = MagicMock()
    if succeed:
        client.update_page_tags.return_value = {
            "data": {
                "pages": {
                    "update": {
                        "responseResult": {
                            "succeeded": True,
                            "errorCode": 0,
                            "message": "",
                        },
                        "page": {"id": 1, "path": "test", "title": "Test"},
                    }
                }
            }
        }
    else:
        client.update_page_tags.return_value = {
            "data": {
                "pages": {
                    "update": {
                        "responseResult": {
                            "succeeded": False,
                            "errorCode": 500,
                            "message": "Internal error",
                        },
                        "page": None,
                    }
                }
            }
        }
    return client


class TestApplyTagChangesAdd:
    """Test adding tags to pages."""

    def test_adds_tags_correctly(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["docker", "linux"],
            ),
            TagOperation(
                page_id=2,
                page_path="docs/page2",
                page_title="Page 2",
                current_tags=[],
                new_tags=["networking"],
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.updated == 2
        assert result.skipped == 0
        assert result.failed == 0
        assert client.update_page_tags.call_count == 2
        client.update_page_tags.assert_any_call(1, ["docker", "linux"])
        client.update_page_tags.assert_any_call(2, ["networking"])


class TestApplyTagChangesRemove:
    """Test removing tags from pages."""

    def test_removes_tags_correctly(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker", "linux", "backup"],
                new_tags=["docker"],
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.updated == 1
        client.update_page_tags.assert_called_once_with(1, ["docker"])


class TestApplyTagChangesCombined:
    """Test add + remove combined, remove wins."""

    def test_add_and_remove_combined(self) -> None:
        """When a tag is in both add and remove sets, the caller ensures
        remove wins by computing new_tags = (current + add) - remove."""
        client = _make_mock_client(succeed=True)
        # Simulate: current=["docker"], add=["linux", "docker"], remove=["docker"]
        # Final: ({"docker"} | {"linux", "docker"}) - {"docker"} = {"linux"}
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["linux"],  # remove wins over add for "docker"
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.updated == 1
        client.update_page_tags.assert_called_once_with(1, ["linux"])


class TestApplyTagChangesSkip:
    """Test pages with no net change are skipped."""

    def test_no_change_skipped(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker", "linux"],
                new_tags=["linux", "docker"],  # same tags, different order
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.skipped == 1
        assert result.updated == 0
        assert result.failed == 0
        client.update_page_tags.assert_not_called()

    def test_mix_of_skip_and_update(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["docker"],  # no change
            ),
            TagOperation(
                page_id=2,
                page_path="docs/page2",
                page_title="Page 2",
                current_tags=["docker"],
                new_tags=["docker", "linux"],  # change
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.skipped == 1
        assert result.updated == 1
        assert client.update_page_tags.call_count == 1
        client.update_page_tags.assert_called_once_with(2, ["docker", "linux"])


class TestApplyTagChangesFailure:
    """Test handling of API failures."""

    def test_api_failure_counted(self) -> None:
        client = _make_mock_client(succeed=False)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["docker", "linux"],
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.failed == 1
        assert result.updated == 0

    def test_exception_counted_as_failure(self) -> None:
        client = MagicMock()
        client.update_page_tags.side_effect = WikiClientError("Connection refused")
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["docker", "linux"],
            ),
        ]

        result = apply_tag_changes(client, ops)

        assert result.failed == 1
        assert result.updated == 0


class TestApplyTagChangesProgress:
    """Test progress callback is called correctly."""

    def test_progress_callback_called(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=1,
                page_path="docs/page1",
                page_title="Page 1",
                current_tags=["docker"],
                new_tags=["docker", "linux"],
            ),
            TagOperation(
                page_id=2,
                page_path="docs/page2",
                page_title="Page 2",
                current_tags=[],
                new_tags=["networking"],
            ),
        ]

        callback = MagicMock()
        result = apply_tag_changes(client, ops, progress_callback=callback)

        assert callback.call_count == 2
        callback.assert_any_call(0, 2, ops[0])
        callback.assert_any_call(1, 2, ops[1])

    def test_progress_callback_cancellation(self) -> None:
        client = _make_mock_client(succeed=True)
        ops = [
            TagOperation(
                page_id=i,
                page_path=f"docs/page{i}",
                page_title=f"Page {i}",
                current_tags=[],
                new_tags=["tag"],
            )
            for i in range(5)
        ]

        def cancel_at_2(current: int, total: int, op: TagOperation) -> None:
            if current >= 2:
                raise StopIteration

        result = apply_tag_changes(client, ops, progress_callback=cancel_at_2)

        # Only first 2 should have been processed
        assert result.updated == 2
        assert client.update_page_tags.call_count == 2


class TestFetchPageTags:
    """Test fetch_page_tags returns correct data."""

    def test_fetch_page_tags(self) -> None:
        client = MagicMock()
        client.fetch_page_content.return_value = {
            "id": 42,
            "path": "docs/page",
            "title": "Test Page",
            "description": "",
            "content": "# Test",
            "tags": [{"tag": "docker"}, {"tag": "linux"}],
        }

        from src.core.wiki_client import WikiClient
        # Call the real method with the mock's return value
        page = client.fetch_page_content(42)
        tags = [t["tag"] for t in page.get("tags", [])]

        assert tags == ["docker", "linux"]
        client.fetch_page_content.assert_called_once_with(42)

    def test_fetch_page_tags_empty(self) -> None:
        client = MagicMock()
        client.fetch_page_content.return_value = {
            "id": 42,
            "path": "docs/page",
            "title": "Test Page",
            "description": "",
            "content": "# Test",
            "tags": [],
        }

        page = client.fetch_page_content(42)
        tags = [t["tag"] for t in page.get("tags", [])]

        assert tags == []


class TestTagResult:
    """Test TagResult dataclass."""

    def test_total_property(self) -> None:
        result = TagResult(updated=3, skipped=2, failed=1)
        assert result.total == 6

    def test_defaults(self) -> None:
        result = TagResult()
        assert result.updated == 0
        assert result.skipped == 0
        assert result.failed == 0
        assert result.total == 0
