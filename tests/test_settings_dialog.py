from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from src.ui.dialogs.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def _clear_settings():
    """Ensure a clean QSettings slate for every test."""
    settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "wiki-upload-tool", "wiki-upload-tool")
    settings.clear()
    yield
    settings.clear()


# ── .env import ─────────────────────────────────────────────────────


class TestEnvImport:
    def _write_env(self, tmp_path: Path, lines: list[str]) -> Path:
        p = tmp_path / ".env"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_import_populates_fields(self, qtbot, tmp_path, monkeypatch):
        env_path = self._write_env(tmp_path, [
            "WIKIJS_URL=https://wiki.example.com",
            "WIKIJS_API_KEY=secret-key-123",
            "WIKIJS_BASE_PATH=Docs/MyProject",
            "WIKIJS_SOURCE_DIR=D:\\docs",
            "WIKIJS_LOCALE=nb",
        ])
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **kw: (str(env_path), "")),
        )

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._on_import_env()

        assert dialog._wiki_url.text() == "https://wiki.example.com"
        assert dialog._api_key.text() == "secret-key-123"
        assert dialog._base_path.text() == "Docs/MyProject"
        assert dialog._source_dir.text() == "D:\\docs"
        assert dialog._locale.currentText() == "nb"
        assert "5 setting(s)" in dialog._env_status.text()

    def test_partial_import_preserves_existing(self, qtbot, tmp_path, monkeypatch):
        """Missing .env variables should not blank out existing fields."""
        # Pre-set some values in QSettings
        settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "wiki-upload-tool", "wiki-upload-tool")
        settings.setValue("wiki_url", "https://existing.com")
        settings.setValue("api_key", "old-key")
        settings.setValue("locale", "en")

        env_path = self._write_env(tmp_path, [
            "WIKIJS_BASE_PATH=NewPath",
        ])
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **kw: (str(env_path), "")),
        )

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._on_import_env()

        # Imported field updated
        assert dialog._base_path.text() == "NewPath"
        # Non-imported fields preserved from QSettings
        assert dialog._wiki_url.text() == "https://existing.com"
        assert dialog._api_key.text() == "old-key"
        assert "1 setting(s)" in dialog._env_status.text()


# ── .env export ─────────────────────────────────────────────────────


class TestEnvExport:
    def test_export_writes_correct_format(self, qtbot, tmp_path, monkeypatch):
        out_path = tmp_path / "exported.env"
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getSaveFileName",
            staticmethod(lambda *a, **kw: (str(out_path), "")),
        )

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._wiki_url.setText("https://wiki.test.com")
        dialog._api_key.setText("key-abc")
        dialog._base_path.setText("Docs")
        dialog._source_dir.setText("C:\\src")
        dialog._locale.setEditText("nb")

        dialog._on_export_env()

        content = out_path.read_text(encoding="utf-8")
        assert "WIKIJS_URL=https://wiki.test.com" in content
        assert "WIKIJS_API_KEY=key-abc" in content
        assert "WIKIJS_BASE_PATH=Docs" in content
        assert "WIKIJS_SOURCE_DIR=C:\\src" in content
        assert "WIKIJS_LOCALE=nb" in content

    def test_export_excludes_gui_only_settings(self, qtbot, tmp_path, monkeypatch):
        """Archive root and move dest should NOT appear in .env export."""
        settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "wiki-upload-tool", "wiki-upload-tool")
        settings.setValue("archive/root_path", "Docs/Arkiv")
        settings.setValue("move/dest_path", "Projects/Old")

        out_path = tmp_path / "exported.env"
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getSaveFileName",
            staticmethod(lambda *a, **kw: (str(out_path), "")),
        )

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._wiki_url.setText("https://wiki.test.com")
        dialog._api_key.setText("key")

        dialog._on_export_env()

        content = out_path.read_text(encoding="utf-8")
        assert "archive" not in content.lower() or "root_path" not in content
        assert "move" not in content.lower() or "dest_path" not in content
        assert "Docs/Arkiv" not in content
        assert "Projects/Old" not in content


# ── Round-trip ──────────────────────────────────────────────────────


class TestEnvRoundTrip:
    def test_export_then_import_preserves_values(self, qtbot, tmp_path, monkeypatch):
        env_path = tmp_path / "roundtrip.env"

        # Export phase
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getSaveFileName",
            staticmethod(lambda *a, **kw: (str(env_path), "")),
        )
        dialog1 = SettingsDialog()
        qtbot.addWidget(dialog1)
        dialog1._wiki_url.setText("https://wiki.round.com")
        dialog1._api_key.setText("round-key")
        dialog1._base_path.setText("Round/Path")
        dialog1._source_dir.setText("C:\\round")
        dialog1._locale.setEditText("de")
        dialog1._on_export_env()

        # Import phase into fresh dialog
        monkeypatch.setattr(
            "src.ui.dialogs.settings_dialog.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **kw: (str(env_path), "")),
        )
        dialog2 = SettingsDialog()
        qtbot.addWidget(dialog2)
        dialog2._on_import_env()

        assert dialog2._wiki_url.text() == "https://wiki.round.com"
        assert dialog2._api_key.text() == "round-key"
        assert dialog2._base_path.text() == "Round/Path"
        assert dialog2._source_dir.text() == "C:\\round"
        assert dialog2._locale.currentText() == "de"
