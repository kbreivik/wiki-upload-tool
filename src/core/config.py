from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def load_dotenv(env_path: str | Path) -> dict[str, str]:
    """Load key=value pairs from a .env file into a dict."""
    env_path = Path(env_path)
    env_vars: dict[str, str] = {}
    if not env_path.is_file():
        return env_vars
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value
    return env_vars


def update_env_file(env_path: str | Path, updates: dict[str, str]) -> None:
    """Update or append key=value pairs in a .env file."""
    env_path = Path(env_path)
    lines: list[str] = []
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that weren't already in the file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


@dataclass
class Config:
    """Configuration for wiki upload operations."""

    api_key: str = ""
    wiki_url: str = ""
    base_path: str = ""
    source_dir: str = ""
    locale: str = "en"
    index_file: str = "README.md"
    tags: list[str] = field(default_factory=list)
    strip_footer_patterns: list[str] = field(default_factory=list)
    update_existing: bool = False
    dry_run: bool = False

    @classmethod
    def from_env(cls, env_path: str | Path) -> Config:
        """Create a Config from a .env file."""
        env_vars = load_dotenv(env_path)
        return cls(
            api_key=env_vars.get("WIKIJS_API_KEY", ""),
            wiki_url=env_vars.get("WIKIJS_URL", ""),
            base_path=env_vars.get("WIKIJS_BASE_PATH", ""),
            source_dir=env_vars.get("WIKIJS_SOURCE_DIR", ""),
            locale=env_vars.get("WIKIJS_LOCALE", "en"),
        )

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> Config:
        """Create a Config from a dict (e.g. GUI settings dialog)."""
        return cls(
            api_key=str(d.get("api_key", "")),
            wiki_url=str(d.get("wiki_url", "")),
            base_path=str(d.get("base_path", "")),
            source_dir=str(d.get("source_dir", "")),
            locale=str(d.get("locale", "en")),
            index_file=str(d.get("index_file", "README.md")),
            tags=list(d.get("tags", [])),
            strip_footer_patterns=list(d.get("strip_footer_patterns", [])),
            update_existing=bool(d.get("update_existing", False)),
            dry_run=bool(d.get("dry_run", False)),
        )
