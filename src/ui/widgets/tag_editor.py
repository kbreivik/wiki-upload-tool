from __future__ import annotations

import logging

from PySide6.QtCore import QSettings, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_FILTER_THRESHOLD = 15
_GRID_COLUMNS = 4


class TagEditor(QWidget):
    """Tag selector with checkboxes fetched from the wiki.

    Shows a scrollable grid of checkbox tags. Supports filtering when
    there are many tags, and a custom-add field for new tags.

    Uses QGridLayout (fixed 4 columns) instead of FlowLayout to avoid
    the chicken-and-egg height-for-width bug where tags are hidden
    until the first resize event.

    Signals:
        tags_changed(): emitted when the set of checked tags changes.
    """

    tags_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings("wiki-upload-tool", "wiki-upload-tool")
        self._checkboxes: dict[str, QCheckBox] = {}
        self._wiki_tags: list[str] = []
        self._connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Filter row (shown only when > threshold tags)
        self._filter_row = QHBoxLayout()
        self._filter_label = QLabel("Filter:")
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("type to filter tags...")
        self._filter_input.setFixedWidth(160)
        self._filter_input.textChanged.connect(self._apply_filter)
        self._filter_row.addWidget(self._filter_label)
        self._filter_row.addWidget(self._filter_input)
        self._filter_row.addStretch()
        root.addLayout(self._filter_row)
        self._filter_label.setVisible(False)
        self._filter_input.setVisible(False)

        # Scrollable checkbox area using QGridLayout
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMaximumHeight(120)
        self._scroll.setMinimumHeight(24)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(6)
        self._scroll.setWidget(self._grid_container)
        root.addWidget(self._scroll)

        # Placeholder label (shown when not connected)
        self._placeholder = QLabel("Connect to wiki to load tags")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; font-style: italic;")
        self._scroll.setVisible(False)
        root.addWidget(self._placeholder)

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

        # Restore previously checked tags
        self._saved_tags: list[str] = self._settings.value("checked_tags", []) or []

    # ── Public API ──────────────────────────────────────────────

    def set_wiki_tags(self, tags: list[str]) -> None:
        """Populate checkboxes from wiki tags. Called after fetch."""
        self._wiki_tags = sorted(tags, key=str.lower)
        self._connected = True
        self._rebuild_checkboxes()

    def set_disconnected(self) -> None:
        """Show placeholder when not connected."""
        self._connected = False
        self._scroll.setVisible(False)
        self._placeholder.setVisible(True)
        self._filter_label.setVisible(False)
        self._filter_input.setVisible(False)

    def get_tags(self) -> list[str]:
        """Return list of all checked tag strings."""
        return [tag for tag, cb in self._checkboxes.items() if cb.isChecked()]

    def set_tags(self, tags: list[str]) -> None:
        """Check the given tags (uncheck all others)."""
        self._saved_tags = list(tags)
        for tag, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(tag in tags)
            cb.blockSignals(False)
        # Add custom tags not in wiki
        for tag in tags:
            if tag not in self._checkboxes:
                self._add_checkbox(tag, checked=True)

    # ── Internal ────────────────────────────────────────────────

    def _rebuild_checkboxes(self) -> None:
        """Clear and recreate all checkboxes from wiki tags + saved customs."""
        # Clear existing
        for cb in self._checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self._checkboxes.clear()

        # Combine wiki tags + any saved tags not in wiki
        all_tags = list(self._wiki_tags)
        for tag in self._saved_tags:
            if tag not in all_tags:
                all_tags.append(tag)
        all_tags.sort(key=str.lower)

        for tag in all_tags:
            checked = tag in self._saved_tags
            self._add_checkbox(tag, checked=checked)

        # Show/hide filter
        show_filter = len(all_tags) > _FILTER_THRESHOLD
        self._filter_label.setVisible(show_filter)
        self._filter_input.setVisible(show_filter)

        # Show checkboxes, hide placeholder
        self._scroll.setVisible(True)
        self._placeholder.setVisible(False)

        # Force Qt to process layout before display
        QApplication.processEvents()
        self._grid_container.updateGeometry()
        self._scroll.updateGeometry()
        self.updateGeometry()

    def _add_checkbox(self, tag: str, *, checked: bool = False) -> None:
        if tag in self._checkboxes:
            return
        cb = QCheckBox(tag)
        cb.setChecked(checked)
        cb.checkStateChanged.connect(self._on_check_changed)
        self._checkboxes[tag] = cb
        count = len(self._checkboxes) - 1
        self._grid_layout.addWidget(
            cb, count // _GRID_COLUMNS, count % _GRID_COLUMNS
        )

    def _add_custom(self) -> None:
        tag = self._custom_input.text().strip()
        if not tag:
            return
        if tag in self._checkboxes:
            # Just check the existing one
            self._checkboxes[tag].setChecked(True)
        else:
            self._add_checkbox(tag, checked=True)
            # Update filter visibility
            show_filter = len(self._checkboxes) > _FILTER_THRESHOLD
            self._filter_label.setVisible(show_filter)
            self._filter_input.setVisible(show_filter)
        self._custom_input.clear()
        self._on_check_changed()

    def _on_check_changed(self) -> None:
        checked = self.get_tags()
        self._settings.setValue("checked_tags", checked)
        self.tags_changed.emit()

    def _apply_filter(self, text: str) -> None:
        text_lower = text.lower()
        for tag, cb in self._checkboxes.items():
            cb.setVisible(text_lower in tag.lower())
