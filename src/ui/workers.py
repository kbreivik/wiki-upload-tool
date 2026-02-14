from __future__ import annotations

import logging
import threading
import traceback

from PySide6.QtCore import QThread, Signal

from src.core.config import Config
from src.core.upload import UploadResult, _upload_single_page
from src.core.wiki_client import WikiClient, WikiClientError

logger = logging.getLogger(__name__)


class UploadWorker(QThread):
    """Background thread for uploading pages to Wiki.js.

    Signals:
        progress(int, int, str): (current_index, total, filename)
        page_done(str, str, str): (filename, status, message)
        finished_upload(UploadResult): emitted when upload completes
        error(str): emitted on unexpected exception
    """

    progress = Signal(int, int, str)
    page_done = Signal(str, str, str)
    finished_upload = Signal(UploadResult)
    error = Signal(str)

    def __init__(
        self,
        client: WikiClient,
        pages: list[dict[str, str]],
        config: Config,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._pages = pages
        self._config = config
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. Checked between pages."""
        self._cancel_event.set()

    def run(self) -> None:
        """Upload loop with per-page signals and cancellation."""
        try:
            result = UploadResult()
            total = len(self._pages)

            for i, page in enumerate(self._pages):
                if self._cancel_event.is_set():
                    logger.info("Upload cancelled at page %d/%d", i, total)
                    break

                self.progress.emit(i, total, page.get("filename", ""))

                page_result = _upload_single_page(self._client, page, self._config)
                result.pages.append(page_result)

                match page_result.status:
                    case "created":
                        result.created += 1
                    case "updated":
                        result.updated += 1
                    case "skipped":
                        result.skipped += 1
                    case "failed":
                        result.failed += 1

                self.page_done.emit(
                    page_result.filename,
                    page_result.status,
                    page_result.message,
                )

            self.finished_upload.emit(result)
        except Exception:
            tb = traceback.format_exc()
            logger.critical("Unexpected error in upload worker:\n%s", tb)
            self.error.emit(tb)


class TestConnectionWorker(QThread):
    """Background thread for testing Wiki.js API connectivity."""

    success = Signal()
    failure = Signal(str)

    def __init__(self, url: str, api_key: str, parent: object = None) -> None:
        super().__init__(parent)
        self._url = url
        self._api_key = api_key

    def run(self) -> None:
        try:
            client = WikiClient(self._url, self._api_key, timeout=10)
            client.test_connection()
            self.success.emit()
        except WikiClientError as e:
            self.failure.emit(str(e))
        except Exception:
            self.failure.emit(traceback.format_exc())
