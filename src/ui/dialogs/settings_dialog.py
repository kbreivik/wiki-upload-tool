from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Settings dialog for infrequently-changed options and .env management.

    Args:
        strip_patterns: Current list of footer-stripping regex patterns.
        current_env: Dict of current env-style settings for .env export.
        parent: Parent widget.
    """

    def __init__(
        self,
        strip_patterns: list[str] | None = None,
        current_env: dict[str, str] | None = None,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self._current_env = current_env or {}
        self._imported_env: dict[str, str] | None = None

        self._build_ui()

        if strip_patterns:
            for p in strip_patterns:
                self._pattern_list.addItem(p)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Strip footer patterns ──────────────────────────────────
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
        self._pattern_list.setMaximumHeight(100)
        pattern_layout.addWidget(self._pattern_list)

        self._remove_pattern_btn = QPushButton("Remove Selected")
        self._remove_pattern_btn.clicked.connect(self._remove_pattern)
        pattern_layout.addWidget(self._remove_pattern_btn)

        layout.addWidget(pattern_group)

        # ── .env management ────────────────────────────────────────
        env_group = QGroupBox(".env File")
        env_layout = QVBoxLayout(env_group)
        env_layout.addWidget(QLabel(
            "Import settings from or export current settings to a .env file."
        ))

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

        # ── Dialog buttons ─────────────────────────────────────────
        layout.addStretch()
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ── Pattern list actions ───────────────────────────────────────

    def _add_pattern(self) -> None:
        pattern = self._pattern_input.text().strip()
        if pattern:
            self._pattern_list.addItem(pattern)
            self._pattern_input.clear()

    def _remove_pattern(self) -> None:
        for item in self._pattern_list.selectedItems():
            self._pattern_list.takeItem(self._pattern_list.row(item))

    def get_strip_patterns(self) -> list[str]:
        """Return the current list of footer-stripping patterns."""
        return [
            self._pattern_list.item(i).text()
            for i in range(self._pattern_list.count())
        ]

    # ── .env import/export ─────────────────────────────────────────

    def get_imported_env(self) -> dict[str, str] | None:
        """Return the imported .env values, or None if nothing was imported."""
        return self._imported_env

    def _on_import_env(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import .env File", "", "Env files (*.env);;All files (*)"
        )
        if not path:
            return

        try:
            values = load_dotenv(Path(path))
        except OSError as e:
            QMessageBox.warning(self, "Import Failed", f"Could not read file:\n{e}")
            return

        if not values:
            self._env_status.setText("No values found in file")
            return

        self._imported_env = values
        keys = ", ".join(values.keys())
        self._env_status.setText(f"Imported: {keys}")
        logger.info("Imported .env from %s: %s", path, keys)

    def _on_export_env(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export .env File", ".env", "Env files (*.env);;All files (*)"
        )
        if not path:
            return

        env_values = {k: v for k, v in self._current_env.items() if v}
        if not env_values:
            QMessageBox.information(
                self, "Nothing to Export", "No settings to export."
            )
            return

        try:
            update_env_file(Path(path), env_values)
        except OSError as e:
            QMessageBox.warning(self, "Export Failed", f"Could not write file:\n{e}")
            return

        self._env_status.setText(f"Exported to {Path(path).name}")
        logger.info("Exported .env to %s", path)
