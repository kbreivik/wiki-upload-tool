from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Wiki.js Upload Tool")
    window.resize(800, 600)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
