from __future__ import annotations

import logging

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSettings,
    QThread,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.upload import TagOperation, TagResult
from src.core.wiki_client import WikiClient, WikiClientError
from src.ui.widgets.wiki_path_picker import WikiPathPickerDialog
from src.ui.workers import TagManagerWorker

logger = logging.getLogger(__name__)


# ── Background fetch workers ────────────────────────────────────────


class _FetchPagesWorker(QThread):
    """Fetch all pages under a path with their tags."""

    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self, client: WikiClient, base_path: str, locale: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._base_path = base_path
        self._locale = locale

    def run(self) -> None:
        try:
            all_pages = self._client.fetch_pages(force=True)
            # Filter to path and locale; tags are already included
            filtered = [
                p for p in all_pages
                if (p["path"] == self._base_path
                    or p["path"].startswith(self._base_path + "/"))
                and p.get("locale", "en") == self._locale
            ]
            self.finished.emit(filtered)
        except WikiClientError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))


# ── Page table model ────────────────────────────────────────────────


class _PageTagModel(QAbstractTableModel):
    """Table model: Checkbox, Page Name, Current Tags."""

    COLUMNS = ("Page Name", "Current Tags")

    def __init__(self, parent: QTableView | None = None) -> None:
        super().__init__(parent)
        self._pages: list[dict] = []
        self._checked: list[bool] = []

    def set_pages(self, pages: list[dict]) -> None:
        self.beginResetModel()
        self._pages = list(pages)
        self._checked = [True] * len(pages)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._pages) if not parent.isValid() else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS) if not parent.isValid() else 0

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        page = self._pages[row]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return page.get("title", page["path"])
            if col == 1:
                return ", ".join(sorted(page.get("tags", []), key=str.lower))
        elif role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if self._checked[row] else Qt.CheckState.Unchecked
        return None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            self._checked[index.row()] = value == Qt.CheckState.Checked
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def get_checked_pages(self) -> list[dict]:
        return [p for p, c in zip(self._pages, self._checked) if c]

    def set_all_checked(self, checked: bool) -> None:
        self.beginResetModel()
        self._checked = [checked] * len(self._pages)
        self.endResetModel()

    def all_pages(self) -> list[dict]:
        return list(self._pages)


# ── Tag checkbox section ────────────────────────────────────────────


class _TagCheckboxSection(QWidget):
    """Scrollable grid of tag checkboxes with optional custom add.

    Uses QGridLayout (fixed 4 columns) instead of FlowLayout to avoid
    the chicken-and-egg height-for-width bug where tags are hidden
    until the first resize event.

    Args:
        label: Section label (unused, kept for API compat).
        style: Optional stylesheet for checkboxes.
        show_custom_add: If False, hide the Custom: [input] [Add] row.
        parent: Parent widget.
    """

    _COLUMNS = 4
    selection_changed = Signal()

    def __init__(
        self,
        label: str,
        style: str = "",
        *,
        show_custom_add: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._style = style

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(100)

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(6)
        self._scroll.setWidget(self._grid_container)
        root.addWidget(self._scroll)

        # Custom add row (only shown for Add sections, not Remove)
        if show_custom_add:
            add_row = QHBoxLayout()
            add_row.addWidget(QLabel("Custom:"))
            self._custom_input = QLineEdit()
            self._custom_input.setPlaceholderText("new tag...")
            self._custom_input.setFixedWidth(140)
            self._custom_input.returnPressed.connect(self._add_custom)
            self._add_btn = QPushButton("Add")
            self._add_btn.setFixedWidth(40)
            self._add_btn.clicked.connect(self._add_custom)
            add_row.addWidget(self._custom_input)
            add_row.addWidget(self._add_btn)
            add_row.addStretch()
            root.addLayout(add_row)
        else:
            self._custom_input = None
            self._add_btn = None

    def set_tags(self, tags: list[str]) -> None:
        """Replace all checkboxes with the given tags (all unchecked)."""
        for cb in self._checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self._checkboxes.clear()
        for tag in sorted(tags, key=str.lower):
            self._add_checkbox(tag)
        # Force Qt to process layout before the dialog is shown
        QApplication.processEvents()
        self._grid_container.updateGeometry()
        self._scroll.updateGeometry()
        self.updateGeometry()

    def _add_checkbox(self, tag: str) -> None:
        if tag in self._checkboxes:
            return
        cb = QCheckBox(tag)
        if self._style:
            cb.setStyleSheet(self._style)
        cb.checkStateChanged.connect(lambda _: self.selection_changed.emit())
        self._checkboxes[tag] = cb
        count = len(self._checkboxes) - 1
        self._grid_layout.addWidget(cb, count // self._COLUMNS, count % self._COLUMNS)

    def _add_custom(self) -> None:
        if self._custom_input is None:
            return
        tag = self._custom_input.text().strip()
        if not tag:
            return
        if tag in self._checkboxes:
            self._checkboxes[tag].setChecked(True)
        else:
            self._add_checkbox(tag)
            self._checkboxes[tag].setChecked(True)
        self._custom_input.clear()

    def get_checked(self) -> list[str]:
        return [tag for tag, cb in self._checkboxes.items() if cb.isChecked()]


# ── Dialog ──────────────────────────────────────────────────────────


class TagManagerDialog(QDialog):
    """Dialog for managing tags on existing wiki pages."""

    def __init__(
        self,
        client: WikiClient,
        locale: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tag Manager")
        self.setMinimumSize(800, 500)

        self._client = client
        self._locale = locale
        self._all_wiki_tags: list[str] | None = None  # cached wiki-wide tags
        self._worker: TagManagerWorker | None = None
        self._fetch_worker: _FetchPagesWorker | None = None
        self._settings = QSettings("wiki-upload-tool", "wiki-upload-tool")

        self._build_ui()
        self._restore_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Top bar (full width) ─────────────────────────────
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Path:"))
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("e.g. Dokumentasjon/Docker")
        path_row.addWidget(self._path_input, stretch=1)

        self._pick_btn = QPushButton("Pick...")
        self._pick_btn.setFixedWidth(60)
        self._pick_btn.clicked.connect(self._on_pick_path)
        path_row.addWidget(self._pick_btn)

        self._load_btn = QPushButton("Refresh")
        self._load_btn.setFixedWidth(60)
        self._load_btn.clicked.connect(self._on_load)
        path_row.addWidget(self._load_btn)
        root.addLayout(path_row)

        sel_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self._model.set_all_checked(True))
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(lambda: self._model.set_all_checked(False))
        sel_row.addWidget(select_all_btn)
        sel_row.addWidget(deselect_all_btn)
        sel_row.addStretch()
        root.addLayout(sel_row)

        # ── Left panel (Pages) ───────────────────────────────
        self._model = _PageTagModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.clicked.connect(self._on_table_clicked)

        # Connect model changes to update remove section and preview
        self._model.dataChanged.connect(self._on_page_selection_changed)
        self._model.modelReset.connect(self._on_page_selection_changed)

        # ── Center panel (Tag Actions) ───────────────────────
        add_group = QGroupBox("Add tags:")
        add_layout = QVBoxLayout(add_group)
        self._add_section = _TagCheckboxSection("Add")
        self._add_section.set_tags([])
        self._add_section.selection_changed.connect(self._update_preview)
        add_layout.addWidget(self._add_section)

        remove_group = QGroupBox("Remove tags (from selected pages):")
        remove_layout = QVBoxLayout(remove_group)
        self._remove_section = _TagCheckboxSection(
            "Remove",
            style="QCheckBox { color: #c00; }",
            show_custom_add=False,
        )
        self._remove_section.selection_changed.connect(self._update_preview)
        remove_layout.addWidget(self._remove_section)

        center = QSplitter(Qt.Orientation.Vertical)
        center.addWidget(add_group)
        center.addWidget(remove_group)
        center.setStretchFactor(0, 1)
        center.setStretchFactor(1, 1)

        # ── Right panel (Preview) ────────────────────────────
        preview_group = QGroupBox("Preview:")
        preview_layout = QVBoxLayout(preview_group)
        self._preview_edit = QPlainTextEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setPlaceholderText("No changes")
        preview_layout.addWidget(self._preview_edit)

        # ── Three-panel splitter ─────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(center)
        self._splitter.addWidget(preview_group)
        self._splitter.setStretchFactor(0, 4)  # 40%
        self._splitter.setStretchFactor(1, 3)  # 30%
        self._splitter.setStretchFactor(2, 3)  # 30%
        root.addWidget(self._splitter, stretch=1)

        # ── Bottom bar (centered, full width) ────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        root.addWidget(self._status_label)

    # ── Settings persistence ─────────────────────────────────

    def _restore_settings(self) -> None:
        geo = self._settings.value("tag_manager/geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1200, 600)
        splitter_state = self._settings.value("tag_manager/splitter")
        if splitter_state:
            self._splitter.restoreState(splitter_state)

    def _save_settings(self) -> None:
        self._settings.setValue("tag_manager/geometry", self.saveGeometry())
        self._settings.setValue("tag_manager/splitter", self._splitter.saveState())

    def closeEvent(self, event: object) -> None:
        self._save_settings()
        super().closeEvent(event)

    def accept(self) -> None:
        self._save_settings()
        super().accept()

    def reject(self) -> None:
        self._save_settings()
        super().reject()

    # ── Path picker ─────────────────────────────────────────────

    def _on_pick_path(self) -> None:
        dialog = WikiPathPickerDialog(
            client=self._client,
            current_path=self._path_input.text().strip(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.selected_path()
            if path:
                self._path_input.setText(path)
                self._on_load()

    # ── Load pages ──────────────────────────────────────────────

    def _on_load(self) -> None:
        path = self._path_input.text().strip()
        if not path:
            QMessageBox.warning(self, "Missing Path", "Enter a wiki path to load pages from.")
            return

        self._load_btn.setEnabled(False)
        self._status_label.setText("Loading pages...")

        self._fetch_worker = _FetchPagesWorker(
            self._client, path, self._locale, parent=self,
        )
        self._fetch_worker.finished.connect(self._on_pages_loaded)
        self._fetch_worker.error.connect(self._on_load_error)
        self._fetch_worker.start()

    def _on_pages_loaded(self, pages: list[dict]) -> None:
        self._load_btn.setEnabled(True)
        self._model.set_pages(pages)
        self._apply_btn.setEnabled(len(pages) > 0)
        self._status_label.setText(f"Loaded {len(pages)} page(s)")

        # Fetch wiki-wide tags for the Add section (cached for session).
        # The worker already populated the client's page cache via
        # fetch_pages(force=True), so fetch_tags() reads from cache
        # without a network call.
        if self._all_wiki_tags is None:
            self._all_wiki_tags = self._client.fetch_tags()
            logger.debug(
                "_on_pages_loaded: cached %d wiki-wide tags: %s",
                len(self._all_wiki_tags), self._all_wiki_tags,
            )
        self._add_section.set_tags(self._all_wiki_tags)

        # Remove section is updated by _on_page_selection_changed
        # (triggered automatically by model reset signal)

        if not pages:
            QMessageBox.information(
                self, "No Pages", "No pages found under this path."
            )

    def _on_load_error(self, message: str) -> None:
        self._load_btn.setEnabled(True)
        self._status_label.setText("Load failed")
        QMessageBox.warning(self, "Load Error", message)

    # ── Table click → toggle checkbox ─────────────────────────

    def _on_table_clicked(self, index: QModelIndex) -> None:
        check_index = index.siblingAtColumn(0)
        current = self._model.data(check_index, Qt.ItemDataRole.CheckStateRole)
        new_state = (
            Qt.CheckState.Unchecked
            if current == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self._model.setData(check_index, new_state, Qt.ItemDataRole.CheckStateRole)

    # ── Page selection changed ──────────────────────────────────

    def _on_page_selection_changed(self) -> None:
        """Update remove tags section based on checked pages."""
        checked = self._model.get_checked_pages()
        # Collect all tags from checked pages for the Remove section
        all_tags: set[str] = set()
        for page in checked:
            all_tags.update(page.get("tags", []))
        logger.debug(
            "_on_page_selection_changed: %d checked pages, remove_tags=%s",
            len(checked), sorted(all_tags, key=str.lower),
        )
        self._remove_section.set_tags(sorted(all_tags, key=str.lower))
        QApplication.processEvents()
        self._remove_section.updateGeometry()
        self._update_preview()

    # ── Preview ─────────────────────────────────────────────────

    def _update_preview(self) -> None:
        add_tags = set(self._add_section.get_checked())
        remove_tags = set(self._remove_section.get_checked())
        checked = self._model.get_checked_pages()

        lines: list[str] = []
        for page in checked:
            current = set(page.get("tags", []))
            new = (current | add_tags) - remove_tags
            if current != new:
                current_str = ", ".join(sorted(current, key=str.lower)) or "(none)"
                new_str = ", ".join(sorted(new, key=str.lower)) or "(none)"
                title = page.get("title", page["path"])
                lines.append(f"{title}:  {current_str}  \u2192  {new_str}")

        self._preview_edit.setPlainText("\n".join(lines))
        self._apply_btn.setEnabled(len(lines) > 0)

    # ── Apply ───────────────────────────────────────────────────

    def _on_apply(self) -> None:
        add_tags = set(self._add_section.get_checked())
        remove_tags = set(self._remove_section.get_checked())
        checked = self._model.get_checked_pages()

        operations: list[TagOperation] = []
        for page in checked:
            current = page.get("tags", [])
            new = sorted((set(current) | add_tags) - remove_tags, key=str.lower)
            if sorted(current, key=str.lower) != new:
                operations.append(TagOperation(
                    page_id=page["id"],
                    page_path=page["path"],
                    page_title=page.get("title", page["path"]),
                    current_tags=list(current),
                    new_tags=new,
                ))

        if not operations:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Tag Changes",
            f"Update tags on {len(operations)} page(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._apply_btn.setEnabled(False)
        self._cancel_btn.setText("Stop")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self._on_cancel_worker)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(operations))
        self._progress_bar.setValue(0)

        self._worker = TagManagerWorker(self._client, operations)
        self._worker.progress.connect(self._on_progress)
        self._worker.page_done.connect(self._on_page_done)
        self._worker.finished_result.connect(self._on_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_cancel_worker(self) -> None:
        if self._worker:
            self._worker.cancel()

    def _on_progress(self, current: int, total: int, page_path: str) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"Updating {page_path}...")

    def _on_page_done(self, page_path: str, status: str) -> None:
        logger.info("  %s: %s", status.capitalize(), page_path)

    def _on_finished(self, result: TagResult) -> None:
        self._progress_bar.setValue(self._progress_bar.maximum())
        self._progress_bar.setVisible(False)
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.accept)

        self._status_label.setText(
            f"Done: {result.updated} updated, "
            f"{result.skipped} skipped, {result.failed} failed"
        )
        logger.info(
            "Tag manager: %d updated, %d skipped, %d failed",
            result.updated, result.skipped, result.failed,
        )

        if result.failed > 0:
            QMessageBox.warning(
                self, "Tag Update Complete",
                f"Finished with {result.failed} failure(s).\n"
                f"Updated: {result.updated}, Skipped: {result.skipped}",
            )
        else:
            QMessageBox.information(
                self, "Tag Update Complete",
                f"Updated: {result.updated}, Skipped: {result.skipped}",
            )

        # Refresh page list to show updated tags
        if result.updated > 0:
            self._on_load()

    def _on_worker_error(self, message: str) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)
        self._status_label.setText("Error")
        QMessageBox.critical(self, "Error", message)
