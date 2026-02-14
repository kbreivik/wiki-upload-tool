from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core.config import Config
from src.core.discovery import discover_files, generate_slug, parse_frontmatter
from src.core.links import build_link_map
from src.core.upload import UploadResult, process_file
from src.core.wiki_client import WikiClient, WikiClientError
from src.ui.dialogs.archive_dialog import ArchiveDialog
from src.ui.dialogs.dry_run_preview import DryRunPreviewDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.widgets.connection_indicator import ConnectionIndicator
from src.ui.widgets.wiki_path_picker import WikiPathPickerDialog
from src.ui.widgets.file_table import FileTableView
from src.ui.widgets.log_viewer import LogHandler, LogViewer
from src.ui.widgets.tag_editor import TagEditor
from src.ui.workers import TestConnectionWorker, UploadWorker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wiki.js Upload Tool")
        self.setMinimumSize(700, 600)

        self._upload_worker: UploadWorker | None = None
        self._test_worker: TestConnectionWorker | None = None
        self._settings = QSettings("wiki-upload-tool", "wiki-upload-tool")
        self._strip_patterns: list[str] = []

        self._build_ui()
        self._setup_logging()
        self._restore_settings()

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Top half: config groups
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(self._build_connection_group())
        config_layout.addWidget(self._build_source_group())
        config_layout.addWidget(self._build_options_group())

        # File table
        self._file_table = FileTableView()

        # Splitter: config top, file table bottom
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(config_widget)
        splitter.addWidget(self._file_table)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # Action buttons
        root.addLayout(self._build_action_buttons())

        # Log viewer + progress
        self._log_viewer = LogViewer()
        self._log_viewer.setMaximumHeight(150)
        root.addWidget(QLabel("Log"))
        root.addWidget(self._log_viewer)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("Connection")
        layout = QFormLayout(group)

        # Wiki URL
        url_row = QHBoxLayout()
        self._wiki_url = QLineEdit()
        self._wiki_url.setPlaceholderText("https://wiki.example.com")
        self._test_btn = QPushButton("Test")
        self._test_btn.setFixedWidth(60)
        self._test_btn.clicked.connect(self._on_test_connection)
        url_row.addWidget(self._wiki_url)
        url_row.addWidget(self._test_btn)
        layout.addRow("Wiki URL:", url_row)

        # API Key (password mode)
        key_row = QHBoxLayout()
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Bearer API key")
        self._connection_indicator = ConnectionIndicator()
        key_row.addWidget(self._api_key)
        key_row.addWidget(self._connection_indicator)
        layout.addRow("API Key:", key_row)

        return group

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("Source")
        layout = QFormLayout(group)

        # Folder picker
        folder_row = QHBoxLayout()
        self._source_dir = QLineEdit()
        self._source_dir.setPlaceholderText("Path to folder with .md files")
        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._source_dir)
        folder_row.addWidget(self._browse_btn)
        layout.addRow("Folder:", folder_row)

        # Base path
        base_row = QHBoxLayout()
        self._base_path = QLineEdit()
        self._base_path.setPlaceholderText("Documentation/MyProject")
        self._pick_path_btn = QPushButton("Pick...")
        self._pick_path_btn.setFixedWidth(60)
        self._pick_path_btn.setEnabled(False)
        self._pick_path_btn.clicked.connect(self._on_pick_path)
        base_row.addWidget(self._base_path)
        base_row.addWidget(self._pick_path_btn)
        layout.addRow("Base Path:", base_row)

        # Locale + index file
        locale_row = QHBoxLayout()
        self._locale = QComboBox()
        self._locale.setEditable(True)
        self._locale.addItems(["en", "nb", "de", "fr", "es"])
        locale_row.addWidget(QLabel("Locale:"))
        locale_row.addWidget(self._locale)
        locale_row.addSpacing(20)
        locale_row.addWidget(QLabel("Index file:"))
        self._index_file = QLineEdit("README.md")
        self._index_file.setFixedWidth(120)
        locale_row.addWidget(self._index_file)
        locale_row.addStretch()
        layout.addRow(locale_row)

        # Refresh file list when source dir changes
        self._source_dir.editingFinished.connect(self._refresh_file_list)
        self._base_path.editingFinished.connect(self._refresh_file_list)
        self._locale.currentTextChanged.connect(self._refresh_file_list)
        self._index_file.editingFinished.connect(self._refresh_file_list)

        return group

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox("Options")
        layout = QVBoxLayout(group)

        self._update_existing = QCheckBox("Update existing pages")
        layout.addWidget(self._update_existing)

        layout.addWidget(QLabel("Tags:"))
        self._tag_editor = TagEditor()
        layout.addWidget(self._tag_editor)

        return group

    def _build_action_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._dry_run_btn = QPushButton("Dry Run Preview")
        self._dry_run_btn.clicked.connect(self._on_dry_run)
        row.addWidget(self._dry_run_btn)

        self._upload_btn = QPushButton("Upload Selected")
        self._upload_btn.clicked.connect(self._on_upload)
        row.addWidget(self._upload_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._cancel_btn)

        self._archive_btn = QPushButton("Archive...")
        self._archive_btn.setEnabled(False)
        self._archive_btn.clicked.connect(self._on_archive)
        row.addWidget(self._archive_btn)

        row.addStretch()

        self._settings_btn = QPushButton("Settings...")
        self._settings_btn.clicked.connect(self._on_settings)
        row.addWidget(self._settings_btn)

        return row

    # ── Logging ────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = LogHandler(self._log_viewer)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger("src")
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)

    # ── Settings persistence (2.6) ─────────────────────────────

    def _restore_settings(self) -> None:
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self._wiki_url.setText(self._settings.value("wiki_url", ""))
        self._api_key.setText(self._settings.value("api_key", ""))
        self._source_dir.setText(self._settings.value("source_dir", ""))
        self._base_path.setText(self._settings.value("base_path", ""))
        locale = self._settings.value("locale", "en")
        idx = self._locale.findText(locale)
        if idx >= 0:
            self._locale.setCurrentIndex(idx)
        else:
            self._locale.setEditText(locale)
        self._index_file.setText(self._settings.value("index_file", "README.md"))
        self._strip_patterns = self._settings.value("strip_patterns", []) or []

        # Refresh file table if source dir was restored
        if self._source_dir.text():
            self._refresh_file_list()

    def _save_settings(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("wiki_url", self._wiki_url.text())
        self._settings.setValue("api_key", self._api_key.text())
        self._settings.setValue("source_dir", self._source_dir.text())
        self._settings.setValue("base_path", self._base_path.text())
        self._settings.setValue("locale", self._locale.currentText())
        self._settings.setValue("index_file", self._index_file.text())
        self._settings.setValue("strip_patterns", self._strip_patterns)

    def closeEvent(self, event: object) -> None:
        self._save_settings()
        super().closeEvent(event)

    # ── Config builder ─────────────────────────────────────────

    def _build_config(self, dry_run: bool = False) -> Config:
        return Config(
            api_key=self._api_key.text().strip(),
            wiki_url=self._wiki_url.text().strip().rstrip("/"),
            base_path=self._base_path.text().strip(),
            source_dir=self._source_dir.text().strip(),
            locale=self._locale.currentText().strip(),
            index_file=self._index_file.text().strip() or "README.md",
            tags=self._tag_editor.get_tags(),
            strip_footer_patterns=list(self._strip_patterns),
            update_existing=self._update_existing.isChecked(),
            dry_run=dry_run,
        )

    # ── Actions ────────────────────────────────────────────────

    def _on_browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Markdown Folder", self._source_dir.text()
        )
        if folder:
            self._source_dir.setText(folder)
            self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        source_dir = self._source_dir.text().strip()
        if not source_dir or not Path(source_dir).is_dir():
            self._file_table.file_model.clear()
            return

        config = self._build_config()
        try:
            files = discover_files(source_dir, config.index_file)
        except OSError as e:
            logger.error("Failed to scan directory: %s", e)
            self._file_table.file_model.clear()
            return

        if not files:
            self._file_table.file_model.clear()
            logger.info("No markdown files found in %s", source_dir)
            return

        # Build slug info for link map and display
        file_infos = []
        for entry in files:
            try:
                with open(entry["filepath"], "r", encoding="utf-8") as f:
                    content = f.read()
                metadata, _ = parse_frontmatter(content)
            except OSError as e:
                logger.warning("Cannot read %s: %s", entry["filename"], e)
                file_infos.append({
                    "filename": entry["filename"],
                    "filepath": entry["filepath"],
                    "slug": "",
                    "path": "",
                    "status": "ERROR",
                    "status_message": str(e),
                })
                continue

            slug = metadata.get("slug", generate_slug(entry["filename"], config.index_file))
            path = f"{config.base_path}/{slug}" if slug else config.base_path
            wiki_display = f"/{config.locale}/{path}"
            file_infos.append({
                "filename": entry["filename"],
                "filepath": entry["filepath"],
                "slug": slug,
                "path": wiki_display,
                "status": "",
            })

        self._file_table.file_model.set_files(file_infos)
        logger.info("Found %d markdown file(s)", len(file_infos))

    def _on_test_connection(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        if not url or not key:
            self._connection_indicator.set_disconnected("URL and API key required")
            return

        self._connection_indicator.set_testing()
        self._test_btn.setEnabled(False)

        self._test_worker = TestConnectionWorker(url, key)
        self._test_worker.success.connect(self._on_test_success)
        self._test_worker.failure.connect(self._on_test_failure)
        self._test_worker.start()

    def _on_test_success(self) -> None:
        self._connection_indicator.set_connected()
        self._test_btn.setEnabled(True)
        self._pick_path_btn.setEnabled(True)
        self._archive_btn.setEnabled(True)
        logger.info("Connection test passed")
        # Clear any error styling on API key field
        self._api_key.setStyleSheet("")

    def _on_test_failure(self, message: str) -> None:
        self._test_btn.setEnabled(True)
        self._pick_path_btn.setEnabled(False)
        self._archive_btn.setEnabled(False)

        if "401" in message or "403" in message:
            self._connection_indicator.set_disconnected("Auth failed")
            self._api_key.setStyleSheet("border: 2px solid red;")
            QMessageBox.warning(
                self, "Authentication Failed",
                "Authentication failed — check your API key.",
            )
        else:
            self._connection_indicator.set_disconnected("Connection failed")
            self._api_key.setStyleSheet("")
            logger.error("Connection failed: %s", message)

    def _on_archive(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        base = self._base_path.text().strip()
        locale = self._locale.currentText().strip()
        if not url or not key:
            QMessageBox.warning(
                self, "Missing Config",
                "Wiki URL and API key are required.",
            )
            return
        if not base:
            QMessageBox.warning(
                self, "Missing Base Path",
                "Enter a base path to archive pages from.",
            )
            return

        client = WikiClient(url, key)
        dialog = ArchiveDialog(
            client=client,
            base_path=base,
            locale=locale,
            parent=self,
        )
        dialog.exec()

    def _on_pick_path(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        if not url or not key:
            return

        client = WikiClient(url, key)
        dialog = WikiPathPickerDialog(
            client=client,
            current_path=self._base_path.text().strip(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.selected_path()
            if path:
                self._base_path.setText(path)
                self._refresh_file_list()

    def _on_settings(self) -> None:
        current_env = {
            "WIKIJS_URL": self._wiki_url.text().strip(),
            "WIKIJS_API_KEY": self._api_key.text().strip(),
            "WIKIJS_BASE_PATH": self._base_path.text().strip(),
            "WIKIJS_SOURCE_DIR": self._source_dir.text().strip(),
            "WIKIJS_LOCALE": self._locale.currentText().strip(),
        }
        dialog = SettingsDialog(
            strip_patterns=self._strip_patterns,
            current_env=current_env,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._strip_patterns = dialog.get_strip_patterns()

            # Apply imported .env values to main window fields
            imported = dialog.get_imported_env()
            if imported:
                if "WIKIJS_URL" in imported:
                    self._wiki_url.setText(imported["WIKIJS_URL"])
                if "WIKIJS_API_KEY" in imported:
                    self._api_key.setText(imported["WIKIJS_API_KEY"])
                if "WIKIJS_BASE_PATH" in imported:
                    self._base_path.setText(imported["WIKIJS_BASE_PATH"])
                if "WIKIJS_SOURCE_DIR" in imported:
                    self._source_dir.setText(imported["WIKIJS_SOURCE_DIR"])
                if "WIKIJS_LOCALE" in imported:
                    locale = imported["WIKIJS_LOCALE"]
                    idx = self._locale.findText(locale)
                    if idx >= 0:
                        self._locale.setCurrentIndex(idx)
                    else:
                        self._locale.setEditText(locale)
                self._refresh_file_list()
                logger.info("Applied imported .env settings")

    def _on_dry_run(self) -> None:
        config = self._build_config(dry_run=True)
        checked = self._file_table.file_model.get_checked_files()
        if not checked:
            logger.warning("No files selected")
            return

        # Create client for existence checks if credentials are available
        client = None
        if config.wiki_url and config.api_key:
            client = WikiClient(config.wiki_url, config.api_key)

        dialog = DryRunPreviewDialog(
            files=checked,
            base_path=config.base_path,
            client=client,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._on_upload()

    def _on_upload(self) -> None:
        config = self._build_config()
        if not config.wiki_url or not config.api_key:
            QMessageBox.warning(self, "Missing Config", "Wiki URL and API key are required.")
            return

        checked = self._file_table.file_model.get_checked_files()
        if not checked:
            logger.warning("No files selected for upload")
            return

        # Filter out error files
        valid_files = [f for f in checked if f.get("status") != "ERROR"]
        if not valid_files:
            logger.warning("All selected files have errors — nothing to upload")
            return

        # Process files through core pipeline
        source_dir = config.source_dir
        index_file = config.index_file
        files_for_link_map = []
        for f in valid_files:
            files_for_link_map.append({
                "filename": f["filename"],
                "filepath": f["filepath"],
                "slug": f.get("slug", ""),
            })
        link_map = build_link_map(files_for_link_map, config.base_path, config.locale, index_file)

        pages = []
        for f in valid_files:
            file_info = {"filename": f["filename"], "filepath": f["filepath"]}
            page = process_file(file_info, link_map, config)
            pages.append(page)

        # Start upload worker
        client = WikiClient(config.wiki_url, config.api_key)
        self._upload_worker = UploadWorker(client, pages, config)
        self._upload_worker.progress.connect(self._on_upload_progress)
        self._upload_worker.page_done.connect(self._on_page_done)
        self._upload_worker.finished_upload.connect(self._on_upload_finished)
        self._upload_worker.error.connect(self._on_upload_error)

        self._set_uploading(True)
        self._progress_bar.setMaximum(len(pages))
        self._progress_bar.setValue(0)
        logger.info("Starting upload of %d page(s)...", len(pages))
        self._upload_worker.start()

    def _on_cancel(self) -> None:
        if self._upload_worker:
            self._upload_worker.cancel()
            logger.info("Cancelling upload...")

    def _set_uploading(self, active: bool) -> None:
        self._upload_btn.setEnabled(not active)
        self._dry_run_btn.setEnabled(not active)
        self._cancel_btn.setEnabled(active)
        self._progress_bar.setVisible(active)

    # ── Worker signal handlers ─────────────────────────────────

    def _on_upload_progress(self, current: int, total: int, filename: str) -> None:
        self._progress_bar.setValue(current)
        logger.info("Uploading %s (%d/%d)...", filename, current + 1, total)

    def _on_page_done(self, filename: str, status: str, message: str) -> None:
        row = self._file_table.file_model.filename_to_row(filename)
        if row is not None:
            self._file_table.file_model.set_status(row, status, message)

        match status:
            case "created":
                logger.info("  Created: %s", filename)
            case "updated":
                logger.info("  Updated: %s", filename)
            case "skipped":
                logger.info("  Skipped: %s", filename)
            case "failed":
                logger.error("  FAILED: %s — %s", filename, message)

    def _on_upload_finished(self, result: UploadResult) -> None:
        self._set_uploading(False)
        self._progress_bar.setValue(result.total)

        logger.info("=" * 40)
        logger.info("  Created:  %d", result.created)
        logger.info("  Updated:  %d", result.updated)
        logger.info("  Skipped:  %d", result.skipped)
        logger.info("  Failed:   %d", result.failed)
        logger.info("  Total:    %d", result.total)
        logger.info("=" * 40)

        if result.failed > 0:
            QMessageBox.warning(
                self, "Upload Complete",
                f"Upload finished with {result.failed} failure(s).\n"
                f"Created: {result.created}, Updated: {result.updated}, "
                f"Skipped: {result.skipped}",
            )
        else:
            QMessageBox.information(
                self, "Upload Complete",
                f"All pages uploaded successfully.\n"
                f"Created: {result.created}, Updated: {result.updated}, "
                f"Skipped: {result.skipped}",
            )

    def _on_upload_error(self, message: str) -> None:
        self._set_uploading(False)
        logger.critical("Upload error: %s", message)
        QMessageBox.critical(self, "Upload Error", message)
