from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit


class LogViewer(QPlainTextEdit):
    """Read-only log viewer with timestamp prefixing and level-based coloring."""

    LEVEL_COLORS = {
        "DEBUG": QColor(128, 128, 128),
        "INFO": QColor(0, 0, 0),
        "WARNING": QColor(180, 130, 0),
        "ERROR": QColor(200, 0, 0),
        "CRITICAL": QColor(200, 0, 0),
    }

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)

    def append_log(self, message: str, level: str = "INFO") -> None:
        """Append a timestamped, colored log line."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = self.LEVEL_COLORS.get(level.upper(), QColor(0, 0, 0))

        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(f"{timestamp} ", QTextCharFormat())
        cursor.insertText(f"{message}\n", fmt)

        # Auto-scroll to bottom
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class LogHandler(logging.Handler):
    """Logging handler that forwards records to a LogViewer widget."""

    def __init__(self, viewer: LogViewer) -> None:
        super().__init__()
        self._viewer = viewer

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self._viewer.append_log(msg, record.levelname)
