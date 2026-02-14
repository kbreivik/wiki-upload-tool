from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class TestCLISmoke:
    """Smoke tests to verify the original CLI script still works."""

    CLI_SCRIPT = str(Path(__file__).parent.parent / "upload-and-edit-wiki-tool.py")

    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, self.CLI_SCRIPT, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Wiki.js" in result.stdout

    def test_dry_run_with_empty_dir(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                self.CLI_SCRIPT,
                "--source-dir", str(tmp_path),
                "--dry-run",
                "--wiki-url", "https://wiki.real-server.test",
                "--api-key", "test-key-not-placeholder",
                "--base-path", "Test/Path",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should fail gracefully with "no markdown files found"
        assert result.returncode != 0 or "No markdown files" in result.stdout + result.stderr

    def test_dry_run_with_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Test\n\nHello")
        result = subprocess.run(
            [
                sys.executable,
                self.CLI_SCRIPT,
                "--source-dir", str(tmp_path),
                "--dry-run",
                "--wiki-url", "https://wiki.real-server.test",
                "--api-key", "test-key-not-placeholder",
                "--base-path", "Test/Path",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
