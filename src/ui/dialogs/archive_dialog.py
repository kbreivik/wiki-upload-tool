from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from src.core.upload import ArchiveResult, compute_archive_path, find_pages_to_archive
from src.core.wiki_client import WikiClient, WikiClientError
from src.ui.widgets.wiki_path_picker import WikiPathPickerDialog
from src.ui.workers import ArchiveWorker

logger = logging.getLogger(__name__)


# ── Background fetch ────────────────────────────────────────────────


class _FetchPagesWorker(QThread):
    """Fetch the full page list on a background thread."""

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


# ── Preview table model ─────────────────────────────────────────────


class _ArchivePreviewModel(QAbstractTableModel):
    """Table model: Source Path, Destination Path, Status."""

    COLUMNS = ("Source Path", "Destination Path", "Status")

    def __init__(self, parent: QTableView | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str, str]] = []

    def set_rows(self, rows: list[tuple[str, str, str]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def update_status(self, row: int, status: str) -> None:
        if 0 <= row < len(self._rows):
            old = self._rows[row]
            self._rows[row] = (old[0], old[1], status)
            idx = self.index(row, 2)
            self.dataChanged.emit(idx, idx)

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
            if status == "archived":
                return QColor(0, 140, 0)
            elif status == "failed":
                return QColor(200, 0, 0)
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None


# ── Dialog ──────────────────────────────────────────────────────────


class ArchiveDialog(QDialog):
    """Dialog for archiving existing wiki pages before upload.

    Fetches pages under *base_path*, shows a preview table, lets the
    user choose an archive destination, then runs the archive with
    progress feedback.

    Args:
        client: WikiClient instance.
        base_path: Source base path to archive from.
        locale: Wiki locale.
        parent: Parent widget.
    """

    def __init__(
        self,
        client: WikiClient,
        base_path: str,
        locale: str,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Archive Existing Pages")
        self.setMinimumSize(650, 450)
        self.resize(750, 500)

        self._client = client
        self._base_path = base_path
        self._locale = locale
        self._pages_to_archive: list[dict] = []
        self._fetch_worker: _FetchPagesWorker | None = None
        self._archive_worker: ArchiveWorker | None = None

        self._build_ui()
        self._fetch_pages()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Status
        self._status_label = QLabel(
            f"Finding pages under: {self._base_path}"
        )
        layout.addWidget(self._status_label)

        # Preview table
        self._table = QTableView()
        self._model = _ArchivePreviewModel(self._table)
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

        # Archive destination
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Archive to:"))
        self._dest_input = QLineEdit()
        self._dest_input.setPlaceholderText("e.g. arkiv/Documentation/MyProject")
        if self._base_path:
            self._dest_input.setText(f"arkiv/{self._base_path}")
        self._pick_dest_btn = QPushButton("Pick...")
        self._pick_dest_btn.setFixedWidth(60)
        self._pick_dest_btn.clicked.connect(self._on_pick_dest)
        dest_row.addWidget(self._dest_input)
        dest_row.addWidget(self._pick_dest_btn)
        layout.addLayout(dest_row)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Buttons
        self._buttons = QDialogButtonBox()
        self._archive_btn = self._buttons.addButton(
            "Archive", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._archive_btn.setEnabled(False)
        self._cancel_btn = self._buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self._archive_btn.clicked.connect(self._on_archive)
        self._buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self._buttons)

    # ── Fetch pages ─────────────────────────────────────────────

    def _fetch_pages(self) -> None:
        self._status_label.setText(
            f"Fetching pages under: {self._base_path}..."
        )
        self._fetch_worker = _FetchPagesWorker(self._client, self)
        self._fetch_worker.finished.connect(self._on_pages_fetched)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_pages_fetched(self, all_pages: list[dict]) -> None:
        self._pages_to_archive = find_pages_to_archive(
            all_pages, self._base_path, self._locale
        )

        if not self._pages_to_archive:
            self._status_label.setText(
                f"No pages found under '{self._base_path}'"
            )
            self._summary_label.setText("Nothing to archive.")
            return

        self._refresh_preview()
        self._status_label.setText(
            f"Found {len(self._pages_to_archive)} page(s) to archive"
        )
        self._summary_label.setText(
            f"{len(self._pages_to_archive)} page(s) will be moved"
        )
        self._archive_btn.setEnabled(True)

        # Live-update preview when user edits destination
        self._dest_input.textChanged.connect(self._refresh_preview)

    def _on_fetch_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")
        logger.warning("Failed to fetch pages for archive: %s", message)

    # ── Pick destination ────────────────────────────────────────

    def _on_pick_dest(self) -> None:
        dialog = WikiPathPickerDialog(
            client=self._client,
            current_path=self._dest_input.text().strip(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.selected_path()
            if path:
                self._dest_input.setText(path)
                self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Update the destination column when the archive path changes."""
        archive_dest = self._dest_input.text().strip()
        rows = []
        for p in self._pages_to_archive:
            old_path = p["path"]
            new_path = compute_archive_path(old_path, self._base_path, archive_dest)
            rows.append((old_path, new_path, ""))
        self._model.set_rows(rows)

    # ── Archive execution ───────────────────────────────────────

    def _on_archive(self) -> None:
        archive_dest = self._dest_input.text().strip()
        if not archive_dest:
            QMessageBox.warning(
                self, "Missing Destination",
                "Please enter an archive destination path.",
            )
            return

        if not self._pages_to_archive:
            return

        count = len(self._pages_to_archive)
        reply = QMessageBox.question(
            self,
            "Confirm Archive",
            f"This will archive {count} page(s) from\n"
            f"  {self._base_path}\n"
            f"to\n"
            f"  {archive_dest}\n\n"
            f"Each page will be copied then deleted. Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._archive_btn.setEnabled(False)
        self._dest_input.setEnabled(False)
        self._pick_dest_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(count)
        self._progress_bar.setValue(0)

        self._archive_worker = ArchiveWorker(
            client=self._client,
            pages=self._pages_to_archive,
            base_path=self._base_path,
            archive_path=archive_dest,
            locale=self._locale,
            parent=self,
        )
        self._archive_worker.progress.connect(self._on_archive_progress)
        self._archive_worker.page_done.connect(self._on_archive_page_done)
        self._archive_worker.finished_archive.connect(self._on_archive_finished)
        self._archive_worker.error.connect(self._on_archive_error)
        self._archive_worker.start()

    def _on_cancel(self) -> None:
        if self._archive_worker and self._archive_worker.isRunning():
            self._archive_worker.cancel()
            logger.info("Cancelling archive...")
        else:
            self.reject()

    # ── Worker signals ──────────────────────────────────────────

    def _on_archive_progress(
        self, current: int, total: int, path: str
    ) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"Archiving {path} ({current + 1}/{total})...")

    def _on_archive_page_done(
        self, old_path: str, new_path: str, status: str, message: str
    ) -> None:
        # Find row by old_path and update status
        for i, p in enumerate(self._pages_to_archive):
            if p["path"] == old_path:
                self._model.update_status(i, status)
                break

        if status == "archived":
            if message:
                logger.warning("  %s → %s [%s]", old_path, new_path, message)
            else:
                logger.info("  Archived: %s → %s", old_path, new_path)
        else:
            logger.error("  FAILED: %s — %s", old_path, message)

    def _on_archive_finished(self, result: ArchiveResult) -> None:
        self._progress_bar.setValue(result.total)
        self._status_label.setText("Archive complete")
        self._summary_label.setText(
            f"Archived: {result.archived}  Failed: {result.failed}  "
            f"Total: {result.total}"
        )

        # Replace Archive button with Close
        self._archive_btn.setVisible(False)
        self._cancel_btn.setText("Close")
        # Disconnect reject (cancel) so Close just accepts
        self._buttons.rejected.disconnect(self._on_cancel)
        self._buttons.rejected.connect(self.accept)

    def _on_archive_error(self, message: str) -> None:
        self._status_label.setText("Archive error")
        self._archive_btn.setEnabled(True)
        self._dest_input.setEnabled(True)
        self._pick_dest_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        logger.critical("Archive error: %s", message)
        QMessageBox.critical(self, "Archive Error", message)
