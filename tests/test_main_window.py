"""Tests for the main window three-panel layout and features."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QSplitter

from src.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _clear_settings():
    """Ensure a clean QSettings slate for every test."""
    settings = QSettings("wiki-upload-tool", "wiki-upload-tool")
    settings.clear()
    yield
    settings.clear()


@pytest.fixture()
def window(qtbot, monkeypatch):
    """Create a MainWindow with auto-connect disabled."""
    # Prevent auto-test from firing (needs no real wiki)
    monkeypatch.setattr(
        "src.ui.main_window.QTimer.singleShot",
        staticmethod(lambda *a, **kw: None),
    )
    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    # Remove the LogHandler that MainWindow added to the "src" logger
    # to prevent use-after-free when the LogViewer C++ object is deleted
    import logging as _logging

    src_logger = _logging.getLogger("src")
    from src.ui.widgets.log_viewer import LogHandler

    for h in list(src_logger.handlers):
        if isinstance(h, LogHandler):
            src_logger.removeHandler(h)


class TestThreePanelLayout:
    def test_main_splitter_has_three_panels(self, window):
        splitter = window._main_splitter
        assert isinstance(splitter, QSplitter)
        assert splitter.count() == 3

    def test_all_panels_not_hidden(self, window):
        splitter = window._main_splitter
        for i in range(3):
            assert not splitter.widget(i).isHidden()


class TestPreview:
    def test_preview_updates_on_base_path_change(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\nContent here\n", encoding="utf-8")

        window._source_dir.setText(str(tmp_path))
        window._refresh_file_list()

        window._base_path.setText("Docs/Project")
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "page1.md" in text
        assert "Docs/Project" in text

    def test_preview_updates_on_file_check_change(self, window, tmp_path):
        (tmp_path / "page1.md").write_text("# Page 1\n", encoding="utf-8")
        (tmp_path / "page2.md").write_text("# Page 2\n", encoding="utf-8")

        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._refresh_file_list()

        window._update_preview()
        text = window._preview_edit.toPlainText()
        assert "page1.md" in text
        assert "page2.md" in text

        # Uncheck first file
        model = window._file_table.file_model
        index = model.index(0, 0)
        model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)

        window._update_preview()
        text = window._preview_edit.toPlainText()
        unchecked_filename = model._files[0]["filename"]
        assert unchecked_filename not in text

    def test_preview_empty_when_no_source(self, window):
        window._update_preview()
        assert window._preview_edit.toPlainText() == ""

    def test_preview_shows_no_files_message(self, window, tmp_path):
        # Folder exists but has no .md files
        window._source_dir.setText(str(tmp_path))
        window._refresh_file_list()
        text = window._preview_edit.toPlainText()
        assert "No markdown files found" in text

    def test_preview_shows_tags(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._refresh_file_list()

        window._tag_editor.set_wiki_tags(["docker", "linux"])
        window._tag_editor.set_tags(["docker"])
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "Tags:" in text
        assert "docker" in text

    def test_preview_shows_new_status_with_page_cache(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._refresh_file_list()

        # Simulate empty page cache (connected but no pages exist)
        window._wiki_pages_cache = []
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "Status: NEW" in text

    def test_preview_shows_exists_will_skip(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._locale.setEditText("en")
        window._refresh_file_list()

        # Wiki.js stores path WITHOUT locale prefix, locale is a separate field
        window._wiki_pages_cache = [{"path": "Docs/page1", "title": "P1", "locale": "en", "tags": []}]
        window._update_existing.setChecked(False)
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "EXISTS (will skip)" in text

    def test_preview_shows_exists_will_update(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._locale.setEditText("en")
        window._refresh_file_list()

        window._wiki_pages_cache = [{"path": "Docs/page1", "title": "P1", "locale": "en", "tags": []}]
        window._update_existing.setChecked(True)
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "EXISTS (will update)" in text

    def test_preview_shows_connect_message_when_not_connected(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._refresh_file_list()

        # No page cache and not connected
        window._wiki_pages_cache = None
        window._connected = False
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "Connect to check page status" in text

    def test_preview_updates_on_locale_change(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._refresh_file_list()

        window._locale.setEditText("nb")
        window._update_preview()

        text = window._preview_edit.toPlainText()
        assert "nb/Docs" in text

    def test_preview_updates_on_update_existing_toggle(self, window, tmp_path):
        md = tmp_path / "page1.md"
        md.write_text("# Page 1\n", encoding="utf-8")
        window._source_dir.setText(str(tmp_path))
        window._base_path.setText("Docs")
        window._locale.setEditText("en")
        window._refresh_file_list()

        window._wiki_pages_cache = [{"path": "Docs/page1", "title": "P1", "locale": "en", "tags": []}]

        window._update_existing.setChecked(False)
        window._update_preview()
        assert "will skip" in window._preview_edit.toPlainText()

        window._update_existing.setChecked(True)
        window._update_preview()
        assert "will update" in window._preview_edit.toPlainText()


class TestLogToggle:
    def test_log_hidden_by_default(self, window):
        assert window._log_viewer.isHidden()
        assert window._log_toggle_btn.text() == "Show Log"

    def test_toggle_shows_log(self, window):
        window._on_toggle_log()
        assert not window._log_viewer.isHidden()
        assert window._log_toggle_btn.text() == "Hide Log"

    def test_toggle_hides_log(self, window):
        # Show first
        window._on_toggle_log()
        assert not window._log_viewer.isHidden()

        # Hide
        window._on_toggle_log()
        assert window._log_viewer.isHidden()
        assert window._log_toggle_btn.text() == "Show Log"

    def test_log_state_persisted(self, window, qtbot):
        qtbot.mouseClick(window._log_toggle_btn, Qt.MouseButton.LeftButton)
        settings = QSettings("wiki-upload-tool", "wiki-upload-tool")
        assert settings.value("main/log_visible") in (True, "true")


class TestTagLoading:
    def test_tag_editor_shows_placeholder_initially(self, window):
        assert not window._tag_editor._placeholder.isHidden()
        assert window._tag_editor._scroll.isHidden()

    def test_tags_populate_after_simulated_fetch(self, window):
        window._on_tags_fetched(["docker", "linux", "networking"])
        assert window._wiki_tags_cache == ["docker", "linux", "networking"]
        assert not window._tag_editor._scroll.isHidden()
        assert window._tag_editor._placeholder.isHidden()
        assert len(window._tag_editor._checkboxes) == 3

    def test_custom_tag_add(self, window):
        # First populate with wiki tags
        window._on_tags_fetched(["docker", "linux"])

        # Add a custom tag
        window._tag_editor._custom_input.setText("my-custom-tag")
        window._tag_editor._add_custom()

        assert "my-custom-tag" in window._tag_editor._checkboxes
        assert window._tag_editor._checkboxes["my-custom-tag"].isChecked()

    def test_fetch_wiki_tags_called_on_test_success(self, window, monkeypatch):
        mock_fetch = MagicMock()
        monkeypatch.setattr(window, "_fetch_wiki_tags", mock_fetch)

        window._on_test_success()

        mock_fetch.assert_called_once()


class TestConnectionBar:
    def test_connection_fields_exist(self, window):
        assert window._wiki_url is not None
        assert window._api_key is not None
        assert window._test_btn is not None
        assert window._connection_indicator is not None

    def test_pick_disabled_before_connection(self, window):
        assert not window._pick_path_btn.isEnabled()

    def test_buttons_enabled_on_connection(self, window, monkeypatch):
        monkeypatch.setattr(window, "_fetch_wiki_tags", lambda: None)
        window._on_test_success()
        assert window._pick_path_btn.isEnabled()
        assert window._archive_btn.isEnabled()
        assert window._move_btn.isEnabled()
        assert window._tag_mgr_btn.isEnabled()
