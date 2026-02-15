from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class StatusDot(QWidget):
    """Small colored circle indicator."""

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color = QColor(128, 128, 128)  # grey = unknown

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 10, 10)


class ConnectionIndicator(QWidget):
    """Status dot + label showing connection state."""

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._dot = StatusDot()
        self._label = QLabel("Not connected")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch()

    def set_connected(self) -> None:
        self._dot.set_color(QColor(0, 180, 0))
        self._label.setText("Connected")

    def set_disconnected(self, message: str = "Not connected") -> None:
        self._dot.set_color(QColor(200, 0, 0))
        self._label.setText(message)

    def set_testing(self) -> None:
        self._dot.set_color(QColor(200, 200, 0))
        self._label.setText("Testing...")

    def set_unstable(self) -> None:
        self._dot.set_color(QColor(255, 165, 0))
        self._label.setText("Connection unstable")

    def set_idle(self) -> None:
        self._dot.set_color(QColor(128, 128, 128))
        self._label.setText("Not connected")
