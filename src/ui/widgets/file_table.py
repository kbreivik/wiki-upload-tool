from __future__ import annotations

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QTableView, QHeaderView


class FileTableModel(QAbstractTableModel):
    """Table model for markdown files with checkboxes, filenames, wiki paths, and status."""

    COLUMNS = ("File", "Wiki Path", "Status")

    def __init__(self, parent: QTableView | None = None) -> None:
        super().__init__(parent)
        self._files: list[dict[str, str]] = []
        self._checked: list[bool] = []

    def set_files(self, files: list[dict[str, str]]) -> None:
        """Replace all file data. Each dict needs 'filename', 'path', and optionally 'status'."""
        self.beginResetModel()
        self._files = list(files)
        self._checked = [True] * len(files)
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._files = []
        self._checked = []
        self.endResetModel()

    def get_checked_files(self) -> list[dict[str, str]]:
        """Return only the files whose checkboxes are checked."""
        return [f for f, c in zip(self._files, self._checked) if c]

    def set_status(self, row: int, status: str, message: str = "") -> None:
        """Update the status column for a given row."""
        if 0 <= row < len(self._files):
            self._files[row]["status"] = status
            self._files[row]["status_message"] = message
            idx = self.index(row, 2)
            self.dataChanged.emit(idx, idx)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._files)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None

        row, col = index.row(), index.column()
        file_info = self._files[row]

        if role == Qt.ItemDataRole.DisplayRole:
            match col:
                case 0:
                    return file_info.get("filename", "")
                case 1:
                    return file_info.get("path", "")
                case 2:
                    return file_info.get("status", "")

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if self._checked[row] else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.ToolTipRole and col == 2:
            return file_info.get("status_message", "")

        return None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            self._checked[index.row()] = value == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def filename_to_row(self, filename: str) -> int | None:
        """Find the row index for a given filename, or None."""
        for i, f in enumerate(self._files):
            if f.get("filename") == filename:
                return i
        return None


class FileTableView(QTableView):
    """Pre-configured table view for the file list."""

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self._model = FileTableModel(self)
        self.setModel(self._model)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.clicked.connect(self._on_clicked)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def _on_clicked(self, index: QModelIndex) -> None:
        if index.column() == 0:
            current = self._model.data(index, Qt.ItemDataRole.CheckStateRole)
            new_state = (
                Qt.CheckState.Unchecked
                if current == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            self._model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)

    @property
    def file_model(self) -> FileTableModel:
        return self._model
