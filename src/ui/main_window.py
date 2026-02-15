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
    QPlainTextEdit,
    QTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core.config import Config
from src.core.crypto import decrypt_or_plain, encrypt_or_plain
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
from src.ui.workers import FetchPagesWorker, FetchTagsWorker, TestConnectionWorker, UploadWorker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wiki.js Upload Tool")
        self.setMinimumSize(900, 500)

        self._upload_worker: UploadWorker | None = None
        self._test_worker: TestConnectionWorker | None = None
        self._health_worker: TestConnectionWorker | None = None
        self._tags_worker: FetchTagsWorker | None = None
        self._pages_worker: FetchPagesWorker | None = None
        self._wiki_tags_cache: list[str] | None = None
        self._wiki_pages_cache: list[dict] | None = None
        self._last_upload_result: UploadResult | None = None
        self._last_upload_pages: list[dict] | None = None
        self._settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "wiki-upload-tool", "wiki-upload-tool")
        self._auto_testing = False
        self._connected = False

        self._build_ui()
        self._setup_logging()
        self._restore_settings()

        # Debounce timer for live preview
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._update_preview)

        # Connection health monitor (60s periodic ping)
        self._health_timer = QTimer()
        self._health_timer.setInterval(60_000)
        self._health_timer.timeout.connect(self._health_check)

        # Auto-test connection after the window renders
        QTimer.singleShot(500, self._auto_test_connection)

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ── Connection bar (full width, above splitter) ─────────
        conn_row = QHBoxLayout()
        conn_row.addWidget(QLabel("Wiki URL:"))
        self._wiki_url = QLineEdit()
        self._wiki_url.setPlaceholderText("https://wiki.example.com")
        conn_row.addWidget(self._wiki_url, stretch=1)

        self._test_btn = QPushButton("Test")
        self._test_btn.setFixedWidth(60)
        self._test_btn.clicked.connect(self._on_test_connection)
        conn_row.addWidget(self._test_btn)

        conn_row.addSpacing(12)
        conn_row.addWidget(QLabel("API Key:"))
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Bearer API key")
        conn_row.addWidget(self._api_key, stretch=1)

        self._connection_indicator = ConnectionIndicator()
        conn_row.addWidget(self._connection_indicator)
        root.addLayout(conn_row)

        # ── Left panel (Source) ─────────────────────────────────
        left_panel = QGroupBox("Source")
        left_layout = QVBoxLayout(left_panel)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self._source_dir = QLineEdit()
        self._source_dir.setPlaceholderText("Path to folder with .md files")
        folder_row.addWidget(self._source_dir, stretch=1)
        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._browse_btn)
        left_layout.addLayout(folder_row)

        self._file_table = FileTableView()
        left_layout.addWidget(self._file_table, stretch=1)

        self._source_dir.editingFinished.connect(self._refresh_file_list)

        # ── Center panel (Destination & Options) ────────────────
        center_panel = QGroupBox("Destination && Options")
        center_layout = QVBoxLayout(center_panel)

        form = QFormLayout()
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
        form.addRow("Base Path:", base_row)

        # Locale
        self._locale = QComboBox()
        self._locale.setEditable(True)
        self._locale.addItems(["en", "nb", "de", "fr", "es"])
        form.addRow("Locale:", self._locale)

        # Index file
        self._index_file = QLineEdit("README.md")
        form.addRow("Index file:", self._index_file)

        center_layout.addLayout(form)

        # Options row
        self._update_existing = QCheckBox("Update existing pages")
        center_layout.addWidget(self._update_existing)

        # Footer strip
        strip_row = QHBoxLayout()
        strip_row.addWidget(QLabel("Footer strip:"))
        self._strip_editor = ChipEditor(placeholder="regex pattern...")
        strip_row.addWidget(self._strip_editor, stretch=1)
        center_layout.addLayout(strip_row)

        # Tags section (expands to fill remaining space)
        center_layout.addWidget(QLabel("Tags:"))
        self._tag_editor = TagEditor()
        center_layout.addWidget(self._tag_editor, stretch=1)
        center_layout.addStretch()

        # Connect signals for preview updates
        # Text fields use textChanged → debounced preview (300ms)
        self._base_path.textChanged.connect(self._schedule_preview)
        self._index_file.textChanged.connect(self._schedule_preview)
        # Instant triggers (combo, checkbox, tags)
        self._locale.currentTextChanged.connect(self._schedule_preview)
        self._tag_editor.tags_changed.connect(self._schedule_preview)
        self._update_existing.checkStateChanged.connect(self._schedule_preview)

        # Refresh file list when config changes that affect slug computation
        self._base_path.editingFinished.connect(self._refresh_file_list)
        self._locale.currentTextChanged.connect(self._refresh_file_list)
        self._index_file.editingFinished.connect(self._refresh_file_list)

        # ── Right panel (Preview) ──────────────────────────────
        right_panel = QGroupBox("Preview")
        right_layout = QVBoxLayout(right_panel)
        self._preview_edit = QTextEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setPlaceholderText(
            "Select a source folder to preview"
        )
        right_layout.addWidget(self._preview_edit)

        # ── Three-panel splitter ───────────────────────────────
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(left_panel)
        self._main_splitter.addWidget(center_panel)
        self._main_splitter.addWidget(right_panel)
        self._main_splitter.setStretchFactor(0, 30)   # 30%
        self._main_splitter.setStretchFactor(1, 35)   # 35%
        self._main_splitter.setStretchFactor(2, 35)   # 35%
        root.addWidget(self._main_splitter, stretch=1)

        # Connect file table model changes for preview updates
        self._file_table.file_model.dataChanged.connect(self._schedule_preview)

        # ── Bottom bar (centered action buttons) ───────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._dry_run_btn = QPushButton("Dry Run Preview")
        self._dry_run_btn.clicked.connect(self._on_dry_run)
        btn_row.addWidget(self._dry_run_btn)

        self._upload_btn = QPushButton("Upload Selected")
        self._upload_btn.clicked.connect(self._on_upload)
        btn_row.addWidget(self._upload_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)

        self._retry_btn = QPushButton("Retry Failed")
        self._retry_btn.setEnabled(False)
        self._retry_btn.setVisible(False)
        self._retry_btn.clicked.connect(self._on_retry_failed)
        btn_row.addWidget(self._retry_btn)

        btn_row.addSpacing(20)

        self._archive_btn = QPushButton("Archive...")
        self._archive_btn.setEnabled(False)
        self._archive_btn.clicked.connect(self._on_archive)
        btn_row.addWidget(self._archive_btn)

        self._move_btn = QPushButton("Move...")
        self._move_btn.setEnabled(False)
        self._move_btn.clicked.connect(self._on_move)
        btn_row.addWidget(self._move_btn)

        self._tag_mgr_btn = QPushButton("Tag Manager...")
        self._tag_mgr_btn.setEnabled(False)
        self._tag_mgr_btn.clicked.connect(self._on_tag_manager)
        btn_row.addWidget(self._tag_mgr_btn)

        self._settings_btn = QPushButton("Settings...")
        self._settings_btn.clicked.connect(self._on_settings)
        btn_row.addWidget(self._settings_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Log toggle + viewer ────────────────────────────────
        log_row = QHBoxLayout()
        self._log_toggle_btn = QPushButton("Show Log")
        self._log_toggle_btn.clicked.connect(self._on_toggle_log)
        log_row.addWidget(self._log_toggle_btn)
        log_row.addStretch()
        root.addLayout(log_row)

        self._log_viewer = LogViewer()
        self._log_viewer.setMaximumHeight(150)
        self._log_viewer.setVisible(False)
        root.addWidget(self._log_viewer)

        # ── Progress bar ───────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

    # ── Log toggle ─────────────────────────────────────────────

    def _on_toggle_log(self) -> None:
        # Use isHidden() instead of isVisible() so toggle works even when
        # the parent window hasn't been shown yet (e.g. in tests).
        will_show = self._log_viewer.isHidden()
        self._log_viewer.setVisible(will_show)
        self._log_toggle_btn.setText("Hide Log" if will_show else "Show Log")
        self._settings.setValue("main/log_visible", will_show)

    # ── Logging ────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = LogHandler(self._log_viewer)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger("src")
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)

    # ── Settings persistence ───────────────────────────────────

    def _restore_settings(self) -> None:
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        splitter_state = self._settings.value("main/splitter")
        if splitter_state:
            self._main_splitter.restoreState(splitter_state)
        log_visible = self._settings.value("main/log_visible", False)
        # QSettings returns strings on some platforms
        if log_visible in (True, "true"):
            self._log_viewer.setVisible(True)
            self._log_toggle_btn.setText("Hide Log")
        self._wiki_url.setText(self._settings.value("wiki_url", ""))
        # Decrypt API key; migrate from legacy plain text if needed
        encrypted_key = self._settings.value("api_key_encrypted", "")
        if encrypted_key:
            self._api_key.setText(decrypt_or_plain(encrypted_key))
        else:
            # Migration: read legacy plain text key, will be encrypted on next save
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
        self._settings.setValue("main/splitter", self._main_splitter.saveState())
        self._settings.setValue("main/log_visible", self._log_viewer.isVisible())
        self._settings.setValue("wiki_url", self._wiki_url.text())
        self._settings.setValue("api_key_encrypted", encrypt_or_plain(self._api_key.text()))
        self._settings.remove("api_key")  # remove legacy plain text key
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

    # ── Live preview ───────────────────────────────────────────

    def _schedule_preview(self, *_args: object) -> None:
        """Restart the 300ms debounce timer for preview updates."""
        self._preview_timer.start(300)

    def _update_preview(self) -> None:
        """Build preview showing each checked file's destination, status and tags."""
        source_dir = self._source_dir.text().strip()

        # Empty states
        if not source_dir:
            self._preview_edit.setPlainText("")
            return

        all_files = self._file_table.file_model._files
        if not all_files:
            self._preview_edit.setPlainText("No markdown files found")
            return

        checked = self._file_table.file_model.get_checked_files()
        if not checked:
            self._preview_edit.setPlainText("No files selected")
            return

        base_path = self._base_path.text().strip()
        locale = self._locale.currentText().strip()
        update_existing = self._update_existing.isChecked()
        tags = self._tag_editor.get_tags()
        tag_str = ", ".join(sorted(tags, key=str.lower)) if tags else ""

        # Build set of existing wiki paths for status lookup.
        # Wiki.js stores path WITHOUT locale prefix (e.g. "Docs/page1")
        # and locale as a separate field. We match on path + locale.
        existing_paths: set[str] = set()
        if self._wiki_pages_cache is not None:
            for page in self._wiki_pages_cache:
                page_locale = page.get("locale", "").lower()
                page_path = page.get("path", "").lower()
                existing_paths.add(f"{page_locale}/{page_path}")
            if not self._wiki_pages_cache:
                logger.debug("Page cache is empty — all pages will show as NEW")
            else:
                logger.debug(
                    "Page cache: %d pages, sample paths: %s",
                    len(self._wiki_pages_cache),
                    [p.get("path", "") for p in self._wiki_pages_cache[:5]],
                )

        html_parts: list[str] = []
        for f in checked:
            filename = f.get("filename", "")
            slug = f.get("slug", "")
            # page_path = path without locale (matches Wiki.js format)
            page_path = f"{base_path}/{slug}" if slug else base_path
            # display_path = full path with locale (shown to user)
            display_path = f"{locale}/{page_path}"
            # lookup_key = locale/path for matching against cache
            lookup_key = f"{locale}/{page_path}".lower()

            html_parts.append(f"<b>{filename}</b><br>")
            html_parts.append(f"\u2192 {display_path}<br>")

            # Status based on page existence
            if self._wiki_pages_cache is not None:
                if lookup_key in existing_paths:
                    if update_existing:
                        html_parts.append(
                            '<span style="color: #d4a017;">Status: EXISTS (will update)</span><br>'
                        )
                    else:
                        html_parts.append(
                            '<span style="color: #888;">Status: EXISTS (will skip)</span><br>'
                        )
                else:
                    html_parts.append(
                        '<span style="color: #2e8b57;">Status: NEW</span><br>'
                    )
            elif self._connected:
                html_parts.append("Status: ...<br>")
            else:
                html_parts.append(
                    '<span style="color: #888; font-style: italic;">'
                    "Connect to check page status</span><br>"
                )

            if tag_str:
                html_parts.append(f"Tags: {tag_str}<br>")

            html_parts.append("<br>")  # blank line between entries

        self._preview_edit.setHtml("".join(html_parts))

    # ── Tag loading ────────────────────────────────────────────

    def _fetch_wiki_tags(self) -> None:
        """Fetch wiki tags in the background after a successful connection."""
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
        self._wiki_tags_cache = tags
        self._tag_editor.set_wiki_tags(tags)
        logger.info("Loaded %d wiki tags", len(tags))

    def _on_tags_fetch_failed(self, message: str) -> None:
        logger.warning("Failed to fetch wiki tags: %s", message)

    # ── Page list fetch (for preview status) ──────────────────

    def _fetch_wiki_pages(self) -> None:
        """Fetch the wiki page list for preview existence checks."""
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        if not url or not key:
            return

        client = WikiClient(url, key)
        self._pages_worker = FetchPagesWorker(client)
        self._pages_worker.pages_fetched.connect(self._on_pages_fetched)
        self._pages_worker.failure.connect(self._on_pages_fetch_failed)
        self._pages_worker.start()

    def _on_pages_fetched(self, pages: list[dict]) -> None:
        self._wiki_pages_cache = pages
        logger.info("Loaded %d wiki pages for preview", len(pages))
        self._update_preview()

    def _on_pages_fetch_failed(self, message: str) -> None:
        logger.warning("Failed to fetch wiki pages: %s", message)

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
            self._update_preview()
            return

        config = self._build_config()
        try:
            files = discover_files(source_dir, config.index_file)
        except OSError as e:
            logger.error("Failed to scan directory: %s", e)
            self._file_table.file_model.clear()
            self._update_preview()
            return

        if not files:
            self._file_table.file_model.clear()
            logger.info("No markdown files found in %s", source_dir)
            self._update_preview()
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
        self._update_preview()

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
        self._connected = True
        self._connection_indicator.set_connected()
        self._test_btn.setEnabled(True)
        self._pick_path_btn.setEnabled(True)
        self._archive_btn.setEnabled(True)
        self._move_btn.setEnabled(True)
        self._tag_mgr_btn.setEnabled(True)
        logger.info("Connection test passed")
        # Clear any error styling on API key field
        self._api_key.setStyleSheet("")
        # Fetch wiki tags and page list after successful connection
        self._fetch_wiki_tags()
        self._fetch_wiki_pages()
        # Start health monitoring
        self._health_timer.start()

    def _on_test_failure(self, message: str) -> None:
        was_auto = self._auto_testing
        self._auto_testing = False
        self._connected = False
        self._health_timer.stop()
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
        dialog = TagManagerDialog(
            client=client,
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
        self._last_upload_pages = pages
        client = WikiClient(config.wiki_url, config.api_key)
        self._upload_worker = UploadWorker(client, pages, config)
        self._upload_worker.progress.connect(self._on_upload_progress)
        self._upload_worker.page_done.connect(self._on_page_done)
        self._upload_worker.finished_upload.connect(self._on_upload_finished)
        self._upload_worker.batch_stopped.connect(self._on_batch_stopped)
        self._upload_worker.error.connect(self._on_upload_error)

        self._set_uploading(True)
        self._retry_btn.setVisible(False)
        self._retry_btn.setEnabled(False)
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
        self._last_upload_result = result
        if result.stopped_early:
            # Don't hide progress bar — _on_batch_stopped handles UI
            return
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

    def _on_batch_stopped(self, reason: str) -> None:
        """Handle batch stop due to consecutive connection failures."""
        # Keep progress bar visible at current position (don't hide)
        self._upload_btn.setEnabled(True)
        self._dry_run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        # Show Retry Failed button
        self._retry_btn.setVisible(True)
        self._retry_btn.setEnabled(True)
        self._connection_indicator.set_unstable()
        QMessageBox.warning(
            self,
            "Connection Lost",
            f"{reason}\n\nUse 'Retry Failed' to resume.",
        )

    def _on_retry_failed(self) -> None:
        """Re-run upload for only the pages that failed."""
        if not self._last_upload_result or not self._last_upload_pages:
            return

        config = self._build_config()
        if not config.wiki_url or not config.api_key:
            QMessageBox.warning(
                self, "Missing Config", "Wiki URL and API key are required."
            )
            return

        # Collect filenames of failed pages
        failed_filenames = {
            p.filename
            for p in self._last_upload_result.pages
            if p.status == "failed"
        }
        if not failed_filenames:
            logger.info("No failed pages to retry")
            return

        # Filter to only the failed pages from the original list
        retry_pages = [
            p for p in self._last_upload_pages
            if p.get("filename") in failed_filenames
        ]
        if not retry_pages:
            return

        # Hide retry button, start upload
        self._retry_btn.setVisible(False)
        self._retry_btn.setEnabled(False)

        client = WikiClient(config.wiki_url, config.api_key)
        self._upload_worker = UploadWorker(client, retry_pages, config)
        self._upload_worker.progress.connect(self._on_upload_progress)
        self._upload_worker.page_done.connect(self._on_page_done)
        self._upload_worker.finished_upload.connect(self._on_upload_finished)
        self._upload_worker.batch_stopped.connect(self._on_batch_stopped)
        self._upload_worker.error.connect(self._on_upload_error)

        self._set_uploading(True)
        self._progress_bar.setMaximum(len(retry_pages))
        self._progress_bar.setValue(0)
        logger.info("Retrying %d failed page(s)...", len(retry_pages))
        self._upload_worker.start()

    # ── Connection health monitoring ──────────────────────────

    def _health_check(self) -> None:
        """Periodic connection health check (called every 60s)."""
        if not self._connected:
            return
        url = self._wiki_url.text().strip().rstrip("/")
        key = self._api_key.text().strip()
        if not url or not key:
            return

        self._health_worker = TestConnectionWorker(url, key)
        self._health_worker.success.connect(self._on_health_success)
        self._health_worker.failure.connect(self._on_health_failure)
        self._health_worker.start()

    def _on_health_success(self) -> None:
        """Health check passed — ensure indicator shows connected."""
        if self._connected:
            self._connection_indicator.set_connected()

    def _on_health_failure(self, message: str) -> None:
        """Health check failed — update indicator."""
        if "401" in message or "403" in message:
            # Auth failure — mark disconnected
            self._connected = False
            self._health_timer.stop()
            self._connection_indicator.set_disconnected("Auth failed")
            logger.warning("Health check: authentication failed")
        else:
            # Transient connection issue
            self._connection_indicator.set_unstable()
            logger.warning("Health check failed: %s", message)
