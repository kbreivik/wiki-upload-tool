from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)
from PySide6.QtCore import Qt


class _Chip(QWidget):
    """A single removable chip/badge displaying a text label with an x button."""

    removed = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = text

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(2)

        label = QLabel(text)
        layout.addWidget(label)

        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(16, 16)
        close_btn.setFlat(True)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("font-weight: bold; padding: 0; border: none;")
        close_btn.clicked.connect(lambda: self.removed.emit(self.text))
        layout.addWidget(close_btn)

        self.setStyleSheet(
            "QWidget { background: #e0e0e0; border-radius: 10px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _FlowLayout(QHBoxLayout):
    """Simple horizontal layout that wraps chips inline.

    For the typical number of tags/patterns (< 10), a plain QHBoxLayout
    with word-wrap on the parent is sufficient.  A true flow layout would
    need a custom QLayout subclass, but this keeps things simple.
    """


class ChipEditor(QWidget):
    """Inline input + chip display for a list of strings.

    Provides an ``[input] [+]`` field followed by removable chips.
    Used for both tags and footer-strip patterns.
    """

    items_changed = Signal()

    def __init__(
        self,
        placeholder: str = "Add item...",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items: list[str] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        # Input + add button
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.setFixedWidth(140)
        self._input.returnPressed.connect(self._add_item)
        self._layout.addWidget(self._input)

        self._add_btn = QPushButton("+")
        self._add_btn.setFixedWidth(24)
        self._add_btn.clicked.connect(self._add_item)
        self._layout.addWidget(self._add_btn)

        # Chips will be inserted before the stretch
        self._layout.addStretch()

    def _add_item(self) -> None:
        text = self._input.text().strip()
        if text and text not in self._items:
            self._items.append(text)
            self._insert_chip(text)
            self._input.clear()
            self.items_changed.emit()

    def _insert_chip(self, text: str) -> None:
        chip = _Chip(text, parent=self)
        chip.removed.connect(self._remove_item)
        # Insert before the stretch (last item in layout)
        self._layout.insertWidget(self._layout.count() - 1, chip)

    def _remove_item(self, text: str) -> None:
        self._items.remove(text)
        # Find and remove the chip widget
        for i in range(self._layout.count()):
            widget = self._layout.itemAt(i).widget()
            if isinstance(widget, _Chip) and widget.text == text:
                widget.setParent(None)
                widget.deleteLater()
                break
        self.items_changed.emit()

    def get_items(self) -> list[str]:
        return list(self._items)

    def set_items(self, items: list[str]) -> None:
        # Remove existing chips
        for i in reversed(range(self._layout.count())):
            widget = self._layout.itemAt(i).widget()
            if isinstance(widget, _Chip):
                widget.setParent(None)
                widget.deleteLater()
        self._items = list(items)
        for text in self._items:
            self._insert_chip(text)
