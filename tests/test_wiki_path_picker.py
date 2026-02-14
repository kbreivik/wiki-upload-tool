from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel

from src.core.upload import build_tree
from src.core.wiki_client import WikiClient, WikiClientError
from src.ui.widgets.wiki_path_picker import (
    PATH_ROLE,
    WikiPathPickerDialog,
    _TreeFilterProxy,
    populate_tree_model,
)


# ── populate_tree_model ─────────────────────────────────────────────


class TestPopulateTreeModel:
    """Test conversion of build_tree() output into QStandardItemModel."""

    def test_flat_pages(self, qapp):
        pages = [
            {"path": "page-a", "title": "Page A"},
            {"path": "page-b", "title": "Page B"},
        ]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())

        assert model.rowCount() == 2
        names = {model.item(i).text() for i in range(model.rowCount())}
        assert names == {"page-a", "page-b"}

    def test_nested_pages(self, qapp):
        pages = [
            {"path": "Docs/Setup", "title": "Setup Guide"},
            {"path": "Docs/Usage", "title": "Usage Guide"},
            {"path": "Archive/Old", "title": "Old Stuff"},
        ]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())

        # Top level: Archive, Docs (sorted)
        assert model.rowCount() == 2
        assert model.item(0).text() == "Archive"
        assert model.item(1).text() == "Docs"

        docs = model.item(1)
        assert docs.rowCount() == 2
        children = {docs.child(i).text() for i in range(docs.rowCount())}
        assert children == {"Setup", "Usage"}

    def test_deep_nesting_stores_full_path(self, qapp):
        pages = [{"path": "a/b/c/leaf", "title": "Leaf"}]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())

        item = model.item(0)
        assert item.text() == "a"
        assert item.data(PATH_ROLE) == "a"

        item = item.child(0)
        assert item.text() == "b"
        assert item.data(PATH_ROLE) == "a/b"

        item = item.child(0)
        assert item.text() == "c"
        assert item.data(PATH_ROLE) == "a/b/c"

        item = item.child(0)
        assert item.text() == "leaf"
        assert item.data(PATH_ROLE) == "a/b/c/leaf"

    def test_empty_tree(self, qapp):
        model = QStandardItemModel()
        populate_tree_model({}, model.invisibleRootItem())
        assert model.rowCount() == 0

    def test_page_title_in_tooltip(self, qapp):
        pages = [{"path": "test-page", "title": "My Test Page"}]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())
        assert model.item(0).toolTip() == "My Test Page"

    def test_items_are_not_editable(self, qapp):
        pages = [{"path": "page", "title": "T"}]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())
        assert model.item(0).isEditable() is False


# ── _TreeFilterProxy ────────────────────────────────────────────────


class TestTreeFilterProxy:
    """Test the custom filter proxy that shows matches + ancestors + subtrees."""

    def _make_model_and_proxy(self):
        pages = [
            {"path": "Documentation/Setup", "title": "Setup"},
            {"path": "Documentation/Usage", "title": "Usage"},
            {"path": "Archive/OldStuff", "title": "Old"},
        ]
        tree = build_tree(pages)
        model = QStandardItemModel()
        populate_tree_model(tree, model.invisibleRootItem())

        proxy = _TreeFilterProxy()
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        return model, proxy

    def test_no_filter_shows_all(self, qapp):
        _, proxy = self._make_model_and_proxy()
        assert proxy.rowCount() == 2  # Archive, Documentation

    def test_filter_narrows_to_matching_branch(self, qapp):
        _, proxy = self._make_model_and_proxy()
        proxy.setFilterFixedString("Setup")

        # Only Documentation shown (ancestor of match)
        assert proxy.rowCount() == 1
        idx = proxy.index(0, 0)
        assert idx.data(Qt.ItemDataRole.DisplayRole) == "Documentation"

        # Under Documentation: only Setup visible, Usage hidden
        assert proxy.rowCount(idx) == 1
        child = proxy.index(0, 0, idx)
        assert child.data(Qt.ItemDataRole.DisplayRole) == "Setup"

    def test_filter_case_insensitive(self, qapp):
        _, proxy = self._make_model_and_proxy()
        proxy.setFilterFixedString("setup")
        assert proxy.rowCount() == 1

    def test_filter_no_match_hides_all(self, qapp):
        _, proxy = self._make_model_and_proxy()
        proxy.setFilterFixedString("nonexistent")
        assert proxy.rowCount() == 0

    def test_filter_on_parent_shows_full_subtree(self, qapp):
        _, proxy = self._make_model_and_proxy()
        proxy.setFilterFixedString("Documentation")
        assert proxy.rowCount() == 1

        doc_idx = proxy.index(0, 0)
        # Both children visible because their parent matched
        assert proxy.rowCount(doc_idx) == 2

    def test_clearing_filter_restores_all(self, qapp):
        _, proxy = self._make_model_and_proxy()
        proxy.setFilterFixedString("Setup")
        assert proxy.rowCount() == 1

        proxy.setFilterFixedString("")
        assert proxy.rowCount() == 2


# ── WikiPathPickerDialog ────────────────────────────────────────────


class TestWikiPathPickerDialog:
    """Integration tests for the picker dialog with mocked WikiClient."""

    def test_pages_populate_tree(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.return_value = [
            {"path": "Docs/Page1", "title": "P1", "id": 1, "locale": "en"},
            {"path": "Docs/Page2", "title": "P2", "id": 2, "locale": "en"},
        ]

        dialog = WikiPathPickerDialog(client)
        qtbot.addWidget(dialog)
        qtbot.waitUntil(lambda: not dialog._tree.isHidden(), timeout=5000)

        assert dialog._model.rowCount() == 1  # "Docs"
        docs = dialog._model.item(0)
        assert docs.rowCount() == 2

    def test_error_state_on_fetch_failure(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.side_effect = WikiClientError("Connection refused")

        dialog = WikiPathPickerDialog(client)
        qtbot.addWidget(dialog)
        qtbot.waitUntil(
            lambda: "Error" in dialog._status_label.text(), timeout=5000
        )

        assert dialog._tree.isHidden()

    def test_new_folder_returns_custom_path(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.return_value = []

        dialog = WikiPathPickerDialog(client)
        qtbot.addWidget(dialog)
        qtbot.waitUntil(
            lambda: dialog._worker is None or dialog._worker.isFinished(),
            timeout=5000,
        )

        # Simulate the result of QInputDialog (bypass the modal)
        dialog._path_field.setText("Custom/Archive/NewPath")
        assert dialog.selected_path() == "Custom/Archive/NewPath"

    def test_empty_path_returns_none(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.return_value = []

        dialog = WikiPathPickerDialog(client)
        qtbot.addWidget(dialog)
        qtbot.waitUntil(
            lambda: dialog._worker is None or dialog._worker.isFinished(),
            timeout=5000,
        )

        assert dialog.selected_path() is None

    def test_pre_select_current_path(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.return_value = [
            {"path": "Docs/Setup", "title": "Setup", "id": 1, "locale": "en"},
        ]

        dialog = WikiPathPickerDialog(client, current_path="Docs/Setup")
        qtbot.addWidget(dialog)
        qtbot.waitUntil(lambda: not dialog._tree.isHidden(), timeout=5000)

        assert dialog._path_field.text() == "Docs/Setup"

    def test_filter_narrows_visible_items(self, qtbot):
        client = MagicMock(spec=WikiClient)
        client.fetch_page_list.return_value = [
            {"path": "Docs/Setup", "title": "Setup", "id": 1, "locale": "en"},
            {"path": "Archive/Old", "title": "Old", "id": 2, "locale": "en"},
        ]

        dialog = WikiPathPickerDialog(client)
        qtbot.addWidget(dialog)
        qtbot.waitUntil(lambda: not dialog._tree.isHidden(), timeout=5000)

        # Both branches visible before filtering
        assert dialog._proxy.rowCount() == 2

        # Filter narrows to matching branch
        dialog._filter_input.setText("Setup")
        assert dialog._proxy.rowCount() == 1
