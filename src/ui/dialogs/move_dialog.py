from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
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

from src.core.upload import ArchiveResult, compute_dest_path, find_pages_to_archive
from src.core.wiki_client import WikiClient, WikiClientError
from src.ui.widgets.wiki_path_picker import WikiPathPickerDialog
from src.ui.workers import MoveWorker

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


class _PreviewModel(QAbstractTableModel):
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
            if status in ("archived", "moved"):
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


class MoveDialog(QDialog):
    """Dialog for moving wiki pages from one path to another.

    User picks source and destination freely.  A checkbox controls
    whether the source folder name is included in the destination.

    Args:
        client: WikiClient instance.
        source_path: Pre-filled source folder.
        locale: Wiki locale.
        parent: Parent widget.
    """

    def __init__(
        self,
        client: WikiClient,
        source_path: str,
        locale: str,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Move Pages")
        self.setMinimumSize(650, 450)
        self.resize(750, 500)

        self._client = client
        self._source_path = source_path
        self._locale = locale
        self._pages: list[dict] = []
        self._fetch_worker: _FetchPagesWorker | None = None
        self._move_worker: MoveWorker | None = None
        self._settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "wiki-upload-tool", "wiki-upload-tool")

        self._build_ui()
        self._fetch_pages()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Source folder
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source folder:"))
        self._source_input = QLineEdit(self._source_path)
        self._pick_source_btn = QPushButton("Pick...")
        self._pick_source_btn.setFixedWidth(60)
        self._pick_source_btn.clicked.connect(self._on_pick_source)
        src_row.addWidget(self._source_input)
        src_row.addWidget(self._pick_source_btn)
        layout.addLayout(src_row)

        # Destination
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Move to:"))
        saved_dest = self._settings.value("move/dest_path", "", str)
        self._dest_input = QLineEdit(saved_dest)
        self._dest_input.setPlaceholderText("e.g. Projects/Saved")
        self._pick_dest_btn = QPushButton("Pick...")
        self._pick_dest_btn.setFixedWidth(60)
        self._pick_dest_btn.clicked.connect(self._on_pick_dest)
        dest_row.addWidget(self._dest_input)
        dest_row.addWidget(self._pick_dest_btn)
        layout.addLayout(dest_row)

        # Include folder name checkbox
        self._include_folder_cb = QCheckBox(
            "Include source folder name in destination"
        )
        self._include_folder_cb.setChecked(False)
        self._include_folder_cb.toggled.connect(self._refresh_preview)
        layout.addWidget(self._include_folder_cb)

        # Computed destination label
        self._dest_label = QLabel()
        self._dest_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._dest_label)

        # Status
        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        # Preview table
        self._table = QTableView()
        self._model = _PreviewModel(self._table)
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

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Buttons
        self._buttons = QDialogButtonBox()
        self._move_btn = self._buttons.addButton(
            "Move", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._move_btn.setEnabled(False)
        self._cancel_btn = self._buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self._move_btn.clicked.connect(self._on_move)
        self._buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self._buttons)

        # Live-update preview
        self._source_input.textChanged.connect(self._on_source_changed)
        self._dest_input.textChanged.connect(self._refresh_preview)
        self._update_dest_label()

    # ── Computed destination ──────────────────────────────────────

    def _include_folder(self) -> bool:
        return self._include_folder_cb.isChecked()

    def _computed_dest(self) -> str:
        source = self._source_input.text().strip()
        dest = self._dest_input.text().strip()
        if not source or not dest:
            return ""
        if self._include_folder():
            folder_name = source.rsplit("/", 1)[-1]
            return f"{dest}/{folder_name}"
        return dest

    def _update_dest_label(self) -> None:
        dest = self._computed_dest()
        if dest:
            self._dest_label.setText(f"Will move to: {dest}")
        else:
            self._dest_label.setText("")

    # ── Fetch pages ─────────────────────────────────────────────

    def _fetch_pages(self) -> None:
        self._status_label.setText("Fetching pages from wiki...")
        self._fetch_worker = _FetchPagesWorker(self._client, self)
        self._fetch_worker.finished.connect(self._on_pages_fetched)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_pages_fetched(self, all_pages: list[dict]) -> None:
        self._all_pages = all_pages
        self._filter_and_preview()

    def _on_fetch_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")
        logger.warning("Failed to fetch pages for move: %s", message)

    def _on_source_changed(self) -> None:
        self._source_path = self._source_input.text().strip()
        self._update_dest_label()
        if hasattr(self, "_all_pages"):
            self._filter_and_preview()

    def _filter_and_preview(self) -> None:
        source = self._source_input.text().strip()
        self._pages = find_pages_to_archive(
            self._all_pages, source, self._locale
        )
        if not self._pages:
            self._status_label.setText(f"No pages found under '{source}'")
            self._summary_label.setText("Nothing to move.")
            self._model.set_rows([])
            self._move_btn.setEnabled(False)
            return

        self._status_label.setText(
            f"Found {len(self._pages)} page(s) to move"
        )
        self._summary_label.setText(
            f"{len(self._pages)} page(s) will be moved"
        )
        self._move_btn.setEnabled(True)
        self._refresh_preview()

    # ── Pick paths ───────────────────────────────────────────────

    def _on_pick_source(self) -> None:
        dialog = WikiPathPickerDialog(
            client=self._client,
            current_path=self._source_input.text().strip(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.selected_path()
            if path:
                self._source_input.setText(path)

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

    # ── Preview ──────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        self._update_dest_label()
        source = self._source_input.text().strip()
        dest = self._dest_input.text().strip()
        include = self._include_folder()
        rows = []
        for p in self._pages:
            old_path = p["path"]
            new_path = compute_dest_path(
                old_path, source, dest, include_folder_name=include
            )
            rows.append((old_path, new_path, ""))
        self._model.set_rows(rows)

    # ── Move execution ──────────────────────────────────────────

    def _on_move(self) -> None:
        dest = self._dest_input.text().strip()
        if not dest:
            QMessageBox.warning(
                self, "Missing Destination",
                "Please enter a destination path.",
            )
            return

        if not self._pages:
            return

        effective_dest = self._computed_dest()
        count = len(self._pages)
        reply = QMessageBox.question(
            self,
            "Confirm Move",
            f"This will move {count} page(s):\n\n"
            f"  From: {self._source_path}\n"
            f"  To:   {effective_dest}\n\n"
            f"Folder structure will be preserved.\n"
            f"Each page will be copied then deleted. Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._move_btn.setEnabled(False)
        self._source_input.setEnabled(False)
        self._pick_source_btn.setEnabled(False)
        self._dest_input.setEnabled(False)
        self._pick_dest_btn.setEnabled(False)
        self._include_folder_cb.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(count)
        self._progress_bar.setValue(0)

        self._move_worker = MoveWorker(
            client=self._client,
            pages=self._pages,
            source_path=self._source_path,
            dest_root=dest,
            locale=self._locale,
            include_folder_name=self._include_folder(),
            parent=self,
        )
        self._move_worker.progress.connect(self._on_progress)
        self._move_worker.page_done.connect(self._on_page_done)
        self._move_worker.finished_result.connect(self._on_finished)
        self._move_worker.error.connect(self._on_error)
        self._move_worker.start()

    def _on_cancel(self) -> None:
        if self._move_worker and self._move_worker.isRunning():
            self._move_worker.cancel()
            logger.info("Cancelling move...")
        else:
            self.reject()

    # ── Worker signals ──────────────────────────────────────────

    def _on_progress(self, current: int, total: int, path: str) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"Moving {path} ({current + 1}/{total})...")

    def _on_page_done(
        self, old_path: str, new_path: str, status: str, message: str
    ) -> None:
        for i, p in enumerate(self._pages):
            if p["path"] == old_path:
                self._model.update_status(i, status)
                break

        if status == "moved":
            if message:
                logger.warning("  %s → %s [%s]", old_path, new_path, message)
            else:
                logger.info("  Moved: %s → %s", old_path, new_path)
        else:
            logger.error("  FAILED: %s — %s", old_path, message)

    def _on_finished(self, result: ArchiveResult) -> None:
        self._progress_bar.setValue(result.total)
        self._status_label.setText("Move complete")
        self._summary_label.setText(
            f"Moved: {result.archived}  Failed: {result.failed}  "
            f"Total: {result.total}"
        )

        if result.archived > 0:
            self._settings.setValue(
                "move/dest_path", self._dest_input.text().strip()
            )

        self._move_btn.setVisible(False)
        self._cancel_btn.setText("Close")
        self._buttons.rejected.disconnect(self._on_cancel)
        self._buttons.rejected.connect(self.accept)

    def _on_error(self, message: str) -> None:
        self._status_label.setText("Move error")
        self._move_btn.setEnabled(True)
        self._source_input.setEnabled(True)
        self._pick_source_btn.setEnabled(True)
        self._dest_input.setEnabled(True)
        self._pick_dest_btn.setEnabled(True)
        self._include_folder_cb.setEnabled(True)
        self._progress_bar.setVisible(False)
        logger.critical("Move error: %s", message)
        QMessageBox.critical(self, "Move Error", message)
