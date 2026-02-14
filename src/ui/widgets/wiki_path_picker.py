from __future__ import annotations

import logging

from PySide6.QtCore import (
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
)

from src.core.upload import build_tree
from src.core.wiki_client import WikiClient, WikiClientError

logger = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole + 1


# ── Helper: tree dict → QStandardItemModel ─────────────────────────


def populate_tree_model(
    tree_data: dict,
    parent_item: QStandardItem,
    parent_path: str = "",
) -> None:
    """Recursively populate a QStandardItemModel from ``build_tree()`` output.

    Each item stores the full wiki path in ``PATH_ROLE``.
    """
    for key in sorted(tree_data.keys()):
        if key.startswith("_"):
            continue
        node = tree_data[key]
        path = f"{parent_path}/{key}" if parent_path else key

        item = QStandardItem(key)
        item.setData(path, PATH_ROLE)
        item.setEditable(False)

        page = node.get("_page")
        if page and page.get("title"):
            item.setToolTip(page["title"])

        parent_item.appendRow(item)

        children = node.get("_children", {})
        if children:
            populate_tree_model(children, item, path)


# ── Filter proxy ────────────────────────────────────────────────────


class _TreeFilterProxy(QSortFilterProxyModel):
    """Proxy that shows matching items, their ancestors, and their descendants.

    When the user types "Setup", the branch ``Documentation > Setup`` is shown.
    When the user types "Documentation", the entire ``Documentation`` subtree
    (including all children) is shown.
    """

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True

        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)

        # 1. Accept if this item itself matches
        if self._item_matches(index):
            return True

        # 2. Accept if any ancestor matches (show full subtree of matches)
        parent = source_parent
        while parent.isValid():
            if self._item_matches(parent):
                return True
            parent = parent.parent()

        # 3. Accept if any descendant matches (show path to matches)
        return self._has_matching_descendant(index)

    def _item_matches(self, index: QModelIndex) -> bool:
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        return self.filterRegularExpression().match(text).hasMatch()

    def _has_matching_descendant(self, index: QModelIndex) -> bool:
        model = self.sourceModel()
        for i in range(model.rowCount(index)):
            child = model.index(i, 0, index)
            if self._item_matches(child):
                return True
            if self._has_matching_descendant(child):
                return True
        return False


# ── Background worker ───────────────────────────────────────────────


class _FetchPagesWorker(QThread):
    """Fetch the wiki page list on a background thread."""

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


# ── Dialog ──────────────────────────────────────────────────────────


class WikiPathPickerDialog(QDialog):
    """Browse wiki page tree and pick a path.

    Args:
        client: WikiClient instance for fetching pages.
        current_path: Optional path to pre-select in the tree.
        parent: Parent widget.
    """

    def __init__(
        self,
        client: WikiClient,
        current_path: str = "",
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick Wiki Path")
        self.setMinimumSize(500, 450)
        self.resize(550, 500)

        self._client = client
        self._current_path = current_path
        self._worker: _FetchPagesWorker | None = None

        self._build_ui()
        self._fetch_pages()

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Filter box
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("Filter paths...")
        self._filter_input.setClearButtonEnabled(True)
        self._filter_input.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter_input)

        # Status label (loading / error)
        self._status_label = QLabel("Fetching pages from wiki...")
        layout.addWidget(self._status_label)

        # Tree view
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Path"])

        self._proxy = _TreeFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setHeaderHidden(True)
        self._tree.setVisible(False)
        layout.addWidget(self._tree)

        # "New Folder" button
        new_folder_row = QHBoxLayout()
        self._new_folder_btn = QPushButton("New Folder...")
        self._new_folder_btn.clicked.connect(self._on_new_folder)
        new_folder_row.addWidget(self._new_folder_btn)
        new_folder_row.addStretch()
        layout.addLayout(new_folder_row)

        # Selected path display
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Selected:"))
        self._path_field = QLineEdit()
        self._path_field.setReadOnly(True)
        if self._current_path:
            self._path_field.setText(self._current_path)
        path_row.addWidget(self._path_field)
        layout.addLayout(path_row)

        # OK / Cancel
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ── Public API ──────────────────────────────────────────────

    def selected_path(self) -> str | None:
        """Return the selected path, or ``None`` if empty."""
        text = self._path_field.text().strip()
        return text if text else None

    # ── Fetch pages ─────────────────────────────────────────────

    def _fetch_pages(self) -> None:
        self._status_label.setText("Fetching pages from wiki...")
        self._status_label.setVisible(True)
        self._tree.setVisible(False)

        self._worker = _FetchPagesWorker(self._client, self)
        self._worker.finished.connect(self._on_pages_fetched)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_pages_fetched(self, pages: list[dict]) -> None:
        if not pages:
            self._status_label.setText("No pages found on wiki.")
            return

        tree_data = build_tree(pages)
        populate_tree_model(tree_data, self._model.invisibleRootItem())

        self._status_label.setVisible(False)
        self._tree.setVisible(True)
        self._tree.expandAll()

        # Connect selection after model is populated
        self._tree.selectionModel().currentChanged.connect(
            self._on_tree_selection
        )

        if self._current_path:
            self._select_path(self._current_path)

    def _on_fetch_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")
        logger.warning("Failed to fetch pages for path picker: %s", message)

    # ── Filter ──────────────────────────────────────────────────

    def _on_filter_changed(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        if text:
            self._tree.expandAll()

    # ── Selection ───────────────────────────────────────────────

    def _on_tree_selection(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        source_index = self._proxy.mapToSource(current)
        path = source_index.data(PATH_ROLE)
        if path:
            self._path_field.setText(path)

    def _on_new_folder(self) -> None:
        current = self._path_field.text().strip()
        default = f"{current}/" if current else ""
        text, ok = QInputDialog.getText(
            self,
            "New Folder",
            "Enter wiki path (e.g. Documentation/Archive):",
            QLineEdit.EchoMode.Normal,
            default,
        )
        if ok and text.strip():
            self._path_field.setText(text.strip())

    def _select_path(self, path: str) -> None:
        """Try to select and scroll to a path in the tree."""
        root = self._model.invisibleRootItem()
        parts = path.split("/")
        item = root
        for part in parts:
            found = False
            for row in range(item.rowCount()):
                child = item.child(row)
                if child and child.text() == part:
                    item = child
                    found = True
                    break
            if not found:
                return

        source_index = self._model.indexFromItem(item)
        proxy_index = self._proxy.mapFromSource(source_index)
        if proxy_index.isValid():
            self._tree.setCurrentIndex(proxy_index)
            self._tree.scrollTo(proxy_index)
