from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.core.config import load_dotenv, update_env_file
from src.core.crypto import decrypt_or_plain, encrypt_or_plain

logger = logging.getLogger(__name__)

# .env key ↔ QSettings key mapping (CLI-compatible variables only)
_ENV_MAP: dict[str, str] = {
    "WIKIJS_URL": "wiki_url",
    "WIKIJS_API_KEY": "api_key",
    "WIKIJS_BASE_PATH": "base_path",
    "WIKIJS_SOURCE_DIR": "source_dir",
    "WIKIJS_LOCALE": "locale",
}


class SettingsDialog(QDialog):
    """Settings dialog with all GUI fields and .env import/export.

    All fields load from QSettings on open. Save writes to QSettings.

    Args:
        parent: Parent widget.
    """

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._settings = QSettings("wiki-upload-tool", "wiki-upload-tool")

        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Connection ──────────────────────────────────────────
        conn_group = QGroupBox("Connection")
        conn_form = QFormLayout(conn_group)

        self._wiki_url = QLineEdit()
        self._wiki_url.setPlaceholderText("https://wiki.example.com")
        conn_form.addRow("Wiki URL:", self._wiki_url)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Bearer API key")
        conn_form.addRow("API Key:", self._api_key)

        layout.addWidget(conn_group)

        # ── Paths ───────────────────────────────────────────────
        paths_group = QGroupBox("Paths")
        paths_form = QFormLayout(paths_group)

        source_row = QHBoxLayout()
        self._source_dir = QLineEdit()
        self._source_dir.setPlaceholderText("Path to folder with .md files")
        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._on_browse)
        source_row.addWidget(self._source_dir)
        source_row.addWidget(self._browse_btn)
        paths_form.addRow("Source Dir:", source_row)

        self._base_path = QLineEdit()
        self._base_path.setPlaceholderText("Documentation/MyProject")
        paths_form.addRow("Base Path:", self._base_path)

        self._locale = QComboBox()
        self._locale.setEditable(True)
        self._locale.addItems(["en", "nb", "de", "fr", "es"])
        paths_form.addRow("Locale:", self._locale)

        self._index_file = QLineEdit()
        self._index_file.setPlaceholderText("README.md")
        paths_form.addRow("Index File:", self._index_file)

        layout.addWidget(paths_group)

        # ── Defaults ────────────────────────────────────────────
        defaults_group = QGroupBox("Defaults")
        defaults_form = QFormLayout(defaults_group)

        self._archive_root = QLineEdit()
        self._archive_root.setPlaceholderText("e.g. Dokumentasjon/Arkiv")
        defaults_form.addRow("Archive Root:", self._archive_root)

        self._move_dest = QLineEdit()
        self._move_dest.setPlaceholderText("e.g. Projects/Saved")
        defaults_form.addRow("Move Dest:", self._move_dest)

        layout.addWidget(defaults_group)

        # ── Strip footer patterns ───────────────────────────────
        pattern_group = QGroupBox("Strip Footer Patterns")
        pattern_layout = QVBoxLayout(pattern_group)
        pattern_layout.addWidget(QLabel(
            "Regex patterns to strip from the end of markdown files before upload."
        ))

        input_row = QHBoxLayout()
        self._pattern_input = QLineEdit()
        self._pattern_input.setPlaceholderText("Enter regex pattern...")
        self._pattern_input.returnPressed.connect(self._add_pattern)
        self._add_pattern_btn = QPushButton("+")
        self._add_pattern_btn.setFixedWidth(30)
        self._add_pattern_btn.clicked.connect(self._add_pattern)
        input_row.addWidget(self._pattern_input)
        input_row.addWidget(self._add_pattern_btn)
        pattern_layout.addLayout(input_row)

        self._pattern_list = QListWidget()
        self._pattern_list.setMaximumHeight(80)
        pattern_layout.addWidget(self._pattern_list)

        self._remove_pattern_btn = QPushButton("Remove Selected")
        self._remove_pattern_btn.clicked.connect(self._remove_pattern)
        pattern_layout.addWidget(self._remove_pattern_btn)

        layout.addWidget(pattern_group)

        # ── .env File ───────────────────────────────────────────
        env_group = QGroupBox(".env File")
        env_layout = QVBoxLayout(env_group)

        btn_row = QHBoxLayout()
        self._import_btn = QPushButton("Import from .env...")
        self._import_btn.clicked.connect(self._on_import_env)
        self._export_btn = QPushButton("Export to .env...")
        self._export_btn.clicked.connect(self._on_export_env)
        btn_row.addWidget(self._import_btn)
        btn_row.addWidget(self._export_btn)
        btn_row.addStretch()
        env_layout.addLayout(btn_row)

        self._env_status = QLabel("")
        env_layout.addWidget(self._env_status)

        layout.addWidget(env_group)

        # ── Dialog buttons ──────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ── Load / Save ─────────────────────────────────────────────

    def _load_from_settings(self) -> None:
        """Populate all fields from QSettings."""
        self._wiki_url.setText(self._settings.value("wiki_url", "", str))
        # Decrypt API key; migrate from legacy plain text if needed
        encrypted_key = self._settings.value("api_key_encrypted", "", str)
        if encrypted_key:
            self._api_key.setText(decrypt_or_plain(encrypted_key))
        else:
            self._api_key.setText(self._settings.value("api_key", "", str))
        self._source_dir.setText(self._settings.value("source_dir", "", str))
        self._base_path.setText(self._settings.value("base_path", "", str))

        locale = self._settings.value("locale", "en", str)
        idx = self._locale.findText(locale)
        if idx >= 0:
            self._locale.setCurrentIndex(idx)
        else:
            self._locale.setEditText(locale)

        self._index_file.setText(
            self._settings.value("index_file", "README.md", str)
        )
        self._archive_root.setText(
            self._settings.value("archive/root_path", "", str)
        )
        self._move_dest.setText(
            self._settings.value("move/dest_path", "", str)
        )

        patterns = self._settings.value("strip_patterns", []) or []
        for p in patterns:
            self._pattern_list.addItem(p)

    def _on_save(self) -> None:
        """Write all fields to QSettings, then accept."""
        self._settings.setValue("wiki_url", self._wiki_url.text().strip())
        self._settings.setValue("api_key_encrypted", encrypt_or_plain(self._api_key.text().strip()))
        self._settings.remove("api_key")  # remove legacy plain text key
        self._settings.setValue("source_dir", self._source_dir.text().strip())
        self._settings.setValue("base_path", self._base_path.text().strip())
        self._settings.setValue("locale", self._locale.currentText().strip())
        self._settings.setValue(
            "index_file",
            self._index_file.text().strip() or "README.md",
        )
        self._settings.setValue(
            "archive/root_path", self._archive_root.text().strip()
        )
        self._settings.setValue(
            "move/dest_path", self._move_dest.text().strip()
        )
        self._settings.setValue("strip_patterns", self._get_strip_patterns())
        self.accept()

    # ── Pattern list actions ────────────────────────────────────

    def _add_pattern(self) -> None:
        pattern = self._pattern_input.text().strip()
        if pattern:
            self._pattern_list.addItem(pattern)
            self._pattern_input.clear()

    def _remove_pattern(self) -> None:
        for item in self._pattern_list.selectedItems():
            self._pattern_list.takeItem(self._pattern_list.row(item))

    def _get_strip_patterns(self) -> list[str]:
        return [
            self._pattern_list.item(i).text()
            for i in range(self._pattern_list.count())
        ]

    # ── Browse ──────────────────────────────────────────────────

    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Source Directory", self._source_dir.text()
        )
        if folder:
            self._source_dir.setText(folder)

    # ── .env import/export ──────────────────────────────────────

    def _on_import_env(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import .env File", "", "Env files (*.env);;All files (*)"
        )
        if not path:
            return

        try:
            values = load_dotenv(Path(path))
        except OSError as e:
            QMessageBox.warning(
                self, "Import Failed", f"Could not read file:\n{e}"
            )
            return

        if not values:
            self._env_status.setText("No values found in file")
            return

        count = 0
        if "WIKIJS_URL" in values:
            self._wiki_url.setText(values["WIKIJS_URL"])
            count += 1
        if "WIKIJS_API_KEY" in values:
            self._api_key.setText(values["WIKIJS_API_KEY"])
            count += 1
        if "WIKIJS_BASE_PATH" in values:
            self._base_path.setText(values["WIKIJS_BASE_PATH"])
            count += 1
        if "WIKIJS_SOURCE_DIR" in values:
            self._source_dir.setText(values["WIKIJS_SOURCE_DIR"])
            count += 1
        if "WIKIJS_LOCALE" in values:
            locale = values["WIKIJS_LOCALE"]
            idx = self._locale.findText(locale)
            if idx >= 0:
                self._locale.setCurrentIndex(idx)
            else:
                self._locale.setEditText(locale)
            count += 1

        self._env_status.setText(
            f"Imported {count} setting(s) from .env"
        )
        logger.info("Imported %d setting(s) from %s", count, path)

    def _on_export_env(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export .env File", ".env", "Env files (*.env);;All files (*)"
        )
        if not path:
            return

        env_values = {
            "WIKIJS_API_KEY": self._api_key.text().strip(),
            "WIKIJS_URL": self._wiki_url.text().strip(),
            "WIKIJS_BASE_PATH": self._base_path.text().strip(),
            "WIKIJS_SOURCE_DIR": self._source_dir.text().strip(),
            "WIKIJS_LOCALE": self._locale.currentText().strip(),
        }
        # Only export non-empty values
        env_values = {k: v for k, v in env_values.items() if v}

        if not env_values:
            QMessageBox.information(
                self, "Nothing to Export", "No settings to export."
            )
            return

        try:
            update_env_file(Path(path), env_values)
        except OSError as e:
            QMessageBox.warning(
                self, "Export Failed", f"Could not write file:\n{e}"
            )
            return

        self._env_status.setText(f"Exported settings to {Path(path).name}")
        logger.info("Exported .env to %s", path)
