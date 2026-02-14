from __future__ import annotations

import logging

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QThread,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
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
from src.ui.widgets.flow_layout import FlowLayout
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
            all_pages = self._client.fetch_page_list()
            # Filter to path and locale
            filtered = [
                p for p in all_pages
                if (p["path"] == self._base_path
                    or p["path"].startswith(self._base_path + "/"))
                and p.get("locale", "en") == self._locale
            ]
            # Fetch tags for each page
            results = []
            for page in filtered:
                tags = self._client.fetch_page_tags(page["id"])
                results.append({
                    "id": page["id"],
                    "path": page["path"],
                    "title": page["title"],
                    "tags": tags,
                })
            self.finished.emit(results)
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
                return ", ".join(page.get("tags", []))
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
    """Scrollable grid of tag checkboxes with custom add."""

    selection_changed = Signal()

    def __init__(
        self, label: str, style: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._style = style

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMaximumHeight(80)
        self._scroll.setMinimumHeight(30)

        self._flow_container = QWidget()
        self._flow_layout = FlowLayout(self._flow_container, margin=4, spacing=6)
        self._scroll.setWidget(self._flow_container)
        root.addWidget(self._scroll)

        # Custom add row
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

    def set_tags(self, tags: list[str]) -> None:
        """Replace all checkboxes with the given tags (all unchecked)."""
        for cb in self._checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self._checkboxes.clear()
        for tag in sorted(tags):
            self._add_checkbox(tag)

    def _add_checkbox(self, tag: str) -> None:
        if tag in self._checkboxes:
            return
        cb = QCheckBox(tag)
        if self._style:
            cb.setStyleSheet(self._style)
        cb.checkStateChanged.connect(lambda _: self.selection_changed.emit())
        self._checkboxes[tag] = cb
        self._flow_layout.addWidget(cb)

    def _add_custom(self) -> None:
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
        wiki_tags: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tag Manager")
        self.setMinimumSize(750, 600)

        self._client = client
        self._locale = locale
        self._wiki_tags = wiki_tags or []
        self._worker: TagManagerWorker | None = None
        self._fetch_worker: _FetchPagesWorker | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Path selector row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Path:"))
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("e.g. Dokumentasjon/Docker")
        path_row.addWidget(self._path_input, stretch=1)

        self._pick_btn = QPushButton("Pick...")
        self._pick_btn.setFixedWidth(60)
        self._pick_btn.clicked.connect(self._on_pick_path)
        path_row.addWidget(self._pick_btn)

        self._load_btn = QPushButton("Load")
        self._load_btn.setFixedWidth(60)
        self._load_btn.clicked.connect(self._on_load)
        path_row.addWidget(self._load_btn)
        root.addLayout(path_row)

        # Page table
        self._model = _PageTagModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)

        # Connect model changes to update remove section and preview
        self._model.dataChanged.connect(self._on_page_selection_changed)
        self._model.modelReset.connect(self._on_page_selection_changed)

        # Select all / deselect all
        sel_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self._model.set_all_checked(True))
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(lambda: self._model.set_all_checked(False))
        sel_row.addWidget(select_all_btn)
        sel_row.addWidget(deselect_all_btn)
        sel_row.addStretch()

        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addLayout(sel_row)
        table_layout.addWidget(self._table)

        # Bottom half: tag sections + preview
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        # Add tags section
        add_group = QGroupBox("Add tags:")
        add_layout = QVBoxLayout(add_group)
        self._add_section = _TagCheckboxSection("Add")
        self._add_section.set_tags(self._wiki_tags)
        self._add_section.selection_changed.connect(self._update_preview)
        add_layout.addWidget(self._add_section)
        bottom_layout.addWidget(add_group)

        # Remove tags section
        remove_group = QGroupBox("Remove tags (from selected pages):")
        remove_layout = QVBoxLayout(remove_group)
        self._remove_section = _TagCheckboxSection(
            "Remove",
            style="QCheckBox { color: #c00; }",
        )
        self._remove_section.selection_changed.connect(self._update_preview)
        remove_layout.addWidget(self._remove_section)
        bottom_layout.addWidget(remove_group)

        # Preview section
        preview_group = QGroupBox("Preview:")
        preview_layout = QVBoxLayout(preview_group)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setMaximumHeight(120)
        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_layout.setSpacing(2)
        self._preview_layout.addStretch()
        self._preview_scroll.setWidget(self._preview_container)
        preview_layout.addWidget(self._preview_scroll)
        bottom_layout.addWidget(preview_group)

        # Splitter between table and tag sections
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(table_widget)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # Bottom: buttons + progress
        btn_row = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)
        root.addLayout(btn_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        root.addWidget(self._status_label)

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

        if not pages:
            QMessageBox.information(
                self, "No Pages", "No pages found under this path."
            )

    def _on_load_error(self, message: str) -> None:
        self._load_btn.setEnabled(True)
        self._status_label.setText("Load failed")
        QMessageBox.warning(self, "Load Error", message)

    # ── Page selection changed ──────────────────────────────────

    def _on_page_selection_changed(self) -> None:
        """Update remove tags section based on selected pages."""
        checked = self._model.get_checked_pages()
        # Collect all tags from selected pages
        all_tags: set[str] = set()
        for page in checked:
            all_tags.update(page.get("tags", []))
        self._remove_section.set_tags(sorted(all_tags))
        self._update_preview()

    # ── Preview ─────────────────────────────────────────────────

    def _update_preview(self) -> None:
        # Clear existing preview labels
        while self._preview_layout.count() > 1:
            item = self._preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        add_tags = set(self._add_section.get_checked())
        remove_tags = set(self._remove_section.get_checked())
        checked = self._model.get_checked_pages()

        has_changes = False
        for page in checked:
            current = set(page.get("tags", []))
            new = (current | add_tags) - remove_tags
            if current != new:
                has_changes = True
                current_str = ", ".join(sorted(current)) or "(none)"
                new_str = ", ".join(sorted(new)) or "(none)"
                title = page.get("title", page["path"])
                label = QLabel(f"  {title}:  {current_str}  \u2192  {new_str}")
                label.setWordWrap(True)
                # Insert before the stretch
                self._preview_layout.insertWidget(
                    self._preview_layout.count() - 1, label
                )

        self._apply_btn.setEnabled(has_changes)

    # ── Apply ───────────────────────────────────────────────────

    def _on_apply(self) -> None:
        add_tags = set(self._add_section.get_checked())
        remove_tags = set(self._remove_section.get_checked())
        checked = self._model.get_checked_pages()

        operations: list[TagOperation] = []
        for page in checked:
            current = page.get("tags", [])
            new = sorted((set(current) | add_tags) - remove_tags)
            if sorted(current) != new:
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

    def _on_worker_error(self, message: str) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)
        self._status_label.setText("Error")
        QMessageBox.critical(self, "Error", message)
