from PySide6.QtCore import QSettings

# Use INI file instead of Windows Registry for tests (matches main.py)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
