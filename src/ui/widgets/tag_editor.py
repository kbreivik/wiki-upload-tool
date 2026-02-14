from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TagEditor(QWidget):
    """Simple widget for adding and removing string tags."""

    tags_changed = Signal()

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Input row
        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Add tag...")
        self._input.returnPressed.connect(self._add_tag)
        self._add_btn = QPushButton("+")
        self._add_btn.setFixedWidth(30)
        self._add_btn.clicked.connect(self._add_tag)
        input_row.addWidget(self._input)
        input_row.addWidget(self._add_btn)
        layout.addLayout(input_row)

        # Tag list
        self._list = QListWidget()
        self._list.setMaximumHeight(80)
        layout.addWidget(self._list)

        # Remove button
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._remove_selected)
        layout.addWidget(self._remove_btn)

    def _add_tag(self) -> None:
        tag = self._input.text().strip()
        if tag and not self._has_tag(tag):
            self._list.addItem(tag)
            self._input.clear()
            self.tags_changed.emit()

    def _has_tag(self, tag: str) -> bool:
        for i in range(self._list.count()):
            if self._list.item(i).text() == tag:
                return True
        return False

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
        self.tags_changed.emit()

    def get_tags(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_tags(self, tags: list[str]) -> None:
        self._list.clear()
        for tag in tags:
            self._list.addItem(tag)
