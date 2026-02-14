from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt
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
from src.ui.dialogs.move_dialog import MoveDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.dialogs.tag_manager_dialog import TagManagerDialog
from src.ui.widgets.connection_indicator import ConnectionIndicator
from src.ui.widgets.wiki_path_picker import WikiPathPickerDialog
from src.ui.widgets.file_table import FileTableView
from src.ui.widgets.log_viewer import LogHandler, LogViewer
from src.ui.widgets.chip_editor import ChipEditor
from src.ui.widgets.tag_editor import TagEditor
from src.ui.workers import FetchTagsWorker, TestConnectionWorker, UploadWorker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wiki.js Upload Tool")
        self.setMinimumSize(700, 600)

        self._upload_worker: UploadWorker | None = None
        self._test_worker: TestConnectionWorker | None = None
        self._tags_worker: FetchTagsWorker | None = None
        self._settings = QSettings("wiki-upload-tool", "wiki-upload-tool")
        self._auto_testing = False

        self._build_ui()
        self._setup_logging()
        self._restore_settings()

        # Auto-test connection after the window renders
        QTimer.singleShot(500, self._auto_test_connection)

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
        config_layout.addWidget(self._build_destination_group())
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

        self._source_dir.editingFinished.connect(self._refresh_file_list)

        return group

    def _build_destination_group(self) -> QGroupBox:
        group = QGroupBox("Destination")
        layout = QFormLayout(group)

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

        # Locale
        self._locale = QComboBox()
        self._locale.setEditable(True)
        self._locale.addItems(["en", "nb", "de", "fr", "es"])
        layout.addRow("Locale:", self._locale)

        # Index file
        self._index_file = QLineEdit("README.md")
        layout.addRow("Index file:", self._index_file)

        self._base_path.editingFinished.connect(self._refresh_file_list)
        self._locale.currentTextChanged.connect(self._refresh_file_list)
        self._index_file.editingFinished.connect(self._refresh_file_list)

        return group

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox("Options")
        layout = QVBoxLayout(group)

        # Row 1: checkbox + footer strip on the same line
        top_row = QHBoxLayout()
        self._update_existing = QCheckBox("Update existing pages")
        top_row.addWidget(self._update_existing)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Footer strip:"))
        self._strip_editor = ChipEditor(placeholder="regex pattern...")
        top_row.addWidget(self._strip_editor, stretch=1)
        layout.addLayout(top_row)

        # Tags section
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

        self._move_btn = QPushButton("Move...")
        self._move_btn.setEnabled(False)
        self._move_btn.clicked.connect(self._on_move)
        row.addWidget(self._move_btn)

        self._tag_mgr_btn = QPushButton("Tag Manager...")
        self._tag_mgr_btn.setEnabled(False)
        self._tag_mgr_btn.clicked.connect(self._on_tag_manager)
        row.addWidget(self._tag_mgr_btn)

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
        strip_patterns = self._settings.value("strip_patterns", []) or []
        self._strip_editor.set_items(strip_patterns)

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
        self._settings.setValue("strip_patterns", self._strip_editor.get_items())

    def closeEvent(self, event: object) -> None:
        self._save_settings()
        super().closeEvent(event)

    # ── Auto-connect on startup ─────────────────────────────────

    def _auto_test_connection(self) -> None:
        """Check credentials and auto-test or guide the user."""
        url = self._wiki_url.text().strip()
        key = self._api_key.text().strip()

        if not url or not key:
            # First run or cleared settings — guide user to empty fields
            logger.info("Enter your Wiki.js URL and API key to get started")
            if not url:
                self._wiki_url.setFocus()
            else:
                self._api_key.setFocus()
            return

        # Returning user — auto-test silently
        self._auto_testing = True
        self._connection_indicator.set_testing()
        self._test_btn.setEnabled(False)

        self._test_worker = TestConnectionWorker(url.rstrip("/"), key)
        self._test_worker.success.connect(self._on_test_success)
        self._test_worker.failure.connect(self._on_test_failure)
        self._test_worker.start()

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
            strip_footer_patterns=self._strip_editor.get_items(),
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
        self._auto_testing = False
        self._connection_indicator.set_connected()
        self._test_btn.setEnabled(True)
        self._pick_path_btn.setEnabled(True)
        self._archive_btn.setEnabled(True)
        self._move_btn.setEnabled(True)
        self._tag_mgr_btn.setEnabled(True)
        logger.info("Connection test passed")
        # Clear any error styling on API key field
        self._api_key.setStyleSheet("")
        # Fetch wiki tags
        self._fetch_wiki_tags()

    def _on_test_failure(self, message: str) -> None:
        was_auto = self._auto_testing
        self._auto_testing = False
        self._test_btn.setEnabled(True)
        self._pick_path_btn.setEnabled(False)
        self._archive_btn.setEnabled(False)
        self._move_btn.setEnabled(False)
        self._tag_mgr_btn.setEnabled(False)

        url = self._wiki_url.text().strip().rstrip("/")

        if "401" in message or "403" in message:
            short = "Auth failed"
            detail = "Connected to server but API key was rejected — check your key"
            self._api_key.setStyleSheet("border: 2px solid red;")
        elif "getaddrinfo" in message or "Name or service not known" in message or "nodename nor servname" in message:
            hostname = url.split("//")[-1].split("/")[0] if url else "unknown"
            short = "DNS error"
            detail = f"Could not resolve {hostname} — check the URL"
            self._api_key.setStyleSheet("")
        elif "Connection refused" in message or "timed out" in message or "timeout" in message.lower():
            short = "Unreachable"
            detail = f"Could not reach {url} — server may be offline or URL is incorrect"
            self._api_key.setStyleSheet("")
        else:
            short = "Connection failed"
            detail = f"Connection failed: {message}"
            self._api_key.setStyleSheet("")

        self._connection_indicator.set_disconnected(short)
        self._tag_editor.set_disconnected()
        logger.error(detail)

        if not was_auto:
            QMessageBox.warning(self, "Connection Failed", detail)

    def _fetch_wiki_tags(self) -> None:
        """Fetch tags from wiki in background after connection succeeds."""
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        if not url or not key:
            return
        client = WikiClient(url, key)
        self._tags_worker = FetchTagsWorker(client)
        self._tags_worker.tags_fetched.connect(self._on_tags_fetched)
        self._tags_worker.failure.connect(self._on_tags_fetch_failed)
        self._tags_worker.start()

    def _on_tags_fetched(self, tags: list[str]) -> None:
        self._tag_editor.set_wiki_tags(tags)
        logger.info("Loaded %d tag(s) from wiki", len(tags))

    def _on_tags_fetch_failed(self, message: str) -> None:
        logger.warning("Failed to fetch tags: %s", message)

    def _on_archive(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        source = self._base_path.text().strip()
        locale = self._locale.currentText().strip()
        if not url or not key:
            QMessageBox.warning(
                self, "Missing Config",
                "Wiki URL and API key are required.",
            )
            return

        client = WikiClient(url, key)
        dialog = ArchiveDialog(
            client=client,
            source_path=source,
            locale=locale,
            parent=self,
        )
        dialog.exec()

    def _on_move(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        source = self._base_path.text().strip()
        locale = self._locale.currentText().strip()
        if not url or not key:
            QMessageBox.warning(
                self, "Missing Config",
                "Wiki URL and API key are required.",
            )
            return

        client = WikiClient(url, key)
        dialog = MoveDialog(
            client=client,
            source_path=source,
            locale=locale,
            parent=self,
        )
        dialog.exec()

    def _on_tag_manager(self) -> None:
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        locale = self._locale.currentText().strip()
        if not url or not key:
            QMessageBox.warning(
                self, "Missing Config",
                "Wiki URL and API key are required.",
            )
            return

        client = WikiClient(url, key)
        wiki_tags = self._tag_editor.get_tags()
        dialog = TagManagerDialog(
            client=client,
            locale=locale,
            wiki_tags=wiki_tags,
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
        # Snapshot current connection values to detect changes
        old_url = self._wiki_url.text().strip()
        old_key = self._api_key.text().strip()

        # Save current main window values to QSettings so the dialog sees them
        self._save_settings()

        dialog = SettingsDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Dialog already wrote to QSettings — reload into main window
            self._restore_settings()

            # Re-test connection if URL or API key changed
            new_url = self._wiki_url.text().strip()
            new_key = self._api_key.text().strip()
            if (new_url != old_url or new_key != old_key) and new_url and new_key:
                self._auto_test_connection()

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
