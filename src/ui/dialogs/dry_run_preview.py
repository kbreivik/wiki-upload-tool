from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
)

from src.core.wiki_client import WikiClient, WikiClientError

logger = logging.getLogger(__name__)


class _PreviewTableModel(QAbstractTableModel):
    """Table model for dry-run preview: File, Wiki Path, Status."""

    COLUMNS = ("File", "Wiki Path", "Status")

    def __init__(self, parent: QTableView | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str, str]] = []

    def set_rows(self, rows: list[tuple[str, str, str]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._rows[row][col]
        if role == Qt.ItemDataRole.ForegroundRole and col == 2:
            status = self._rows[row][2]
            if status == "NEW":
                return QColor(0, 140, 0)
            elif status == "EXISTS":
                return QColor(180, 130, 0)
            elif status == "ERROR":
                return QColor(200, 0, 0)
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None


class _FetchPagesWorker(QThread):
    """Background thread to fetch the wiki page list for existence checks."""

    finished = Signal(list)
    error = Signal(str)

    def __init__(self, client: WikiClient, parent: object = None) -> None:
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            pages = self._client.fetch_page_list()
            self.finished.emit(pages)
        except WikiClientError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))


class DryRunPreviewDialog(QDialog):
    """Modal dialog showing dry-run preview with NEW/EXISTS status.

    Args:
        files: List of file dicts from the file table (keys: filename,
            filepath, path, slug, status).
        base_path: Wiki base path (e.g. "Documentation/MyProject").
        client: Optional WikiClient for checking existing pages.
        parent: Parent widget.
    """

    def __init__(
        self,
        files: list[dict[str, str]],
        base_path: str,
        client: WikiClient | None = None,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dry Run Preview")
        self.setMinimumSize(600, 400)
        self.resize(700, 450)
        self._files = files
        self._base_path = base_path
        self._client = client
        self._worker: _FetchPagesWorker | None = None

        self._build_ui()
        self._populate(existing_paths=None)

        if client:
            self._fetch_existing_pages()
        else:
            self._status_label.setText(
                "Not connected to wiki — cannot check existing pages"
            )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        # Preview table
        self._table = QTableView()
        self._model = _PreviewTableModel(self._table)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)

        # Summary
        self._summary_label = QLabel()
        layout.addWidget(self._summary_label)

        # Buttons
        self._buttons = QDialogButtonBox()
        self._upload_btn = self._buttons.addButton(
            "Proceed to Upload", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _wiki_path_for_file(self, file_info: dict[str, str]) -> str:
        """Compute the raw wiki path (without locale prefix) for comparison."""
        slug = file_info.get("slug", "")
        return f"{self._base_path}/{slug}" if slug else self._base_path

    def _populate(self, existing_paths: set[str] | None) -> None:
        """Fill the table. Pass None for existing_paths to show '?' status."""
        rows: list[tuple[str, str, str]] = []
        new_count = 0
        exists_count = 0
        error_count = 0

        for f in self._files:
            filename = f["filename"]
            display_path = f.get("path", "")

            if f.get("status") == "ERROR":
                rows.append((filename, display_path, "ERROR"))
                error_count += 1
                continue

            if existing_paths is None:
                rows.append((filename, display_path, "?"))
            else:
                raw_path = self._wiki_path_for_file(f)
                if raw_path in existing_paths:
                    rows.append((filename, display_path, "EXISTS"))
                    exists_count += 1
                else:
                    rows.append((filename, display_path, "NEW"))
                    new_count += 1

        self._model.set_rows(rows)

        valid_count = len(self._files) - error_count
        if existing_paths is None:
            self._summary_label.setText(f"{valid_count} file(s) to upload")
        else:
            parts = []
            if new_count:
                parts.append(f"{new_count} new")
            if exists_count:
                parts.append(f"{exists_count} existing")
            if error_count:
                parts.append(f"{error_count} error(s)")
            self._summary_label.setText(
                f"{len(self._files)} file(s): {', '.join(parts)}"
            )

    def _fetch_existing_pages(self) -> None:
        self._status_label.setText("Fetching page list from wiki...")
        self._upload_btn.setEnabled(False)
        self._worker = _FetchPagesWorker(self._client, self)
        self._worker.finished.connect(self._on_pages_fetched)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_pages_fetched(self, pages: list[dict]) -> None:
        existing_paths = {p["path"] for p in pages}
        self._populate(existing_paths)
        self._status_label.setText("Page existence verified against wiki")
        self._upload_btn.setEnabled(True)

    def _on_fetch_error(self, message: str) -> None:
        self._status_label.setText(f"Could not fetch pages: {message}")
        valid_count = len([f for f in self._files if f.get("status") != "ERROR"])
        self._summary_label.setText(
            f"{valid_count} file(s) to upload (status unknown)"
        )
        self._upload_btn.setEnabled(True)
        logger.warning("Failed to fetch page list for preview: %s", message)
