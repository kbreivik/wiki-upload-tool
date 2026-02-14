from __future__ import annotations

from pathlib import Path

from src.core.config import Config, load_dotenv, update_env_file


class TestLoadDotenv:
    def test_basic_loading(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n")
        result = load_dotenv(env_file)
        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_strips_quotes(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text('KEY1="quoted"\nKEY2=\'single\'\n')
        result = load_dotenv(env_file)
        assert result["KEY1"] == "quoted"
        assert result["KEY2"] == "single"

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=val\n")
        result = load_dotenv(env_file)
        assert result == {"KEY": "val"}

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = load_dotenv(tmp_path / "nonexistent")
        assert result == {}

    def test_value_with_equals_sign(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("URL=https://example.com?a=1&b=2\n")
        result = load_dotenv(env_file)
        assert result["URL"] == "https://example.com?a=1&b=2"


class TestUpdateEnvFile:
    def test_updates_existing_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=old\nKEY2=keep\n")
        update_env_file(env_file, {"KEY1": "new"})
        result = load_dotenv(env_file)
        assert result["KEY1"] == "new"
        assert result["KEY2"] == "keep"

    def test_appends_new_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=val1\n")
        update_env_file(env_file, {"KEY2": "val2"})
        result = load_dotenv(env_file)
        assert result["KEY1"] == "val1"
        assert result["KEY2"] == "val2"

    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        update_env_file(env_file, {"KEY": "val"})
        result = load_dotenv(env_file)
        assert result == {"KEY": "val"}


class TestConfig:
    def test_defaults(self) -> None:
        cfg = Config()
        assert cfg.locale == "en"
        assert cfg.index_file == "README.md"
        assert cfg.tags == []
        assert cfg.update_existing is False
        assert cfg.dry_run is False

    def test_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "WIKIJS_API_KEY=testkey\n"
            "WIKIJS_URL=https://wiki.test.com\n"
            "WIKIJS_BASE_PATH=Docs/Project\n"
            "WIKIJS_SOURCE_DIR=./content\n"
            "WIKIJS_LOCALE=nb\n"
        )
        cfg = Config.from_env(env_file)
        assert cfg.api_key == "testkey"
        assert cfg.wiki_url == "https://wiki.test.com"
        assert cfg.base_path == "Docs/Project"
        assert cfg.source_dir == "./content"
        assert cfg.locale == "nb"

    def test_from_env_defaults(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("")
        cfg = Config.from_env(env_file)
        assert cfg.api_key == ""
        assert cfg.locale == "en"

    def test_from_dict(self) -> None:
        cfg = Config.from_dict({
            "api_key": "key",
            "wiki_url": "https://wiki.test.com",
            "tags": ["tag1", "tag2"],
            "update_existing": True,
        })
        assert cfg.api_key == "key"
        assert cfg.tags == ["tag1", "tag2"]
        assert cfg.update_existing is True
        assert cfg.locale == "en"  # default

    def test_from_dict_missing_keys(self) -> None:
        cfg = Config.from_dict({})
        assert cfg.api_key == ""
        assert cfg.locale == "en"
