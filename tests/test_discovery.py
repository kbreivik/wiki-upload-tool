from __future__ import annotations

from pathlib import Path

from src.core.discovery import (
    discover_files,
    extract_title_from_content,
    generate_slug,
    parse_frontmatter,
)


class TestDiscoverFiles:
    def test_finds_markdown_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Index")
        (tmp_path / "page.md").write_text("# Page")
        (tmp_path / "notes.txt").write_text("not markdown")
        files = discover_files(tmp_path)
        filenames = [f["filename"] for f in files]
        assert "README.md" in filenames
        assert "page.md" in filenames
        assert "notes.txt" not in filenames

    def test_index_file_first(self, tmp_path: Path) -> None:
        (tmp_path / "zzz.md").write_text("")
        (tmp_path / "README.md").write_text("")
        (tmp_path / "aaa.md").write_text("")
        files = discover_files(tmp_path)
        assert files[0]["filename"] == "README.md"
        assert files[1]["filename"] == "aaa.md"
        assert files[2]["filename"] == "zzz.md"

    def test_custom_index_file(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text("")
        (tmp_path / "other.md").write_text("")
        files = discover_files(tmp_path, index_file="index.md")
        assert files[0]["filename"] == "index.md"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = discover_files(tmp_path)
        assert files == []

    def test_no_index_file(self, tmp_path: Path) -> None:
        (tmp_path / "page.md").write_text("")
        files = discover_files(tmp_path)
        assert len(files) == 1
        assert files[0]["filename"] == "page.md"

    def test_filepath_is_absolute(self, tmp_path: Path) -> None:
        (tmp_path / "test.md").write_text("")
        files = discover_files(tmp_path)
        assert Path(files[0]["filepath"]).is_absolute()


class TestParseFrontmatter:
    def test_basic_frontmatter(self) -> None:
        content = "---\ntitle: My Page\ndescription: A test\n---\n\nBody text"
        metadata, body = parse_frontmatter(content)
        assert metadata["title"] == "My Page"
        assert metadata["description"] == "A test"
        assert body == "Body text"

    def test_no_frontmatter(self) -> None:
        content = "# Just a heading\n\nBody text"
        metadata, body = parse_frontmatter(content)
        assert metadata == {}
        assert body == content

    def test_unclosed_frontmatter(self) -> None:
        content = "---\ntitle: Broken\nNo closing delimiter"
        metadata, body = parse_frontmatter(content)
        assert metadata == {}
        assert body == content

    def test_frontmatter_with_slug(self) -> None:
        content = "---\ntitle: Test\nslug: custom-slug\n---\n\nContent"
        metadata, body = parse_frontmatter(content)
        assert metadata["slug"] == "custom-slug"

    def test_strips_leading_blank_lines(self) -> None:
        content = "---\ntitle: Test\n---\n\n\nBody"
        metadata, body = parse_frontmatter(content)
        assert body == "Body"

    def test_quoted_values(self) -> None:
        content = '---\ntitle: "Quoted Title"\n---\nBody'
        metadata, body = parse_frontmatter(content)
        assert metadata["title"] == "Quoted Title"

    def test_empty_content(self) -> None:
        metadata, body = parse_frontmatter("")
        assert metadata == {}
        assert body == ""


class TestExtractTitleFromContent:
    def test_finds_h1(self) -> None:
        assert extract_title_from_content("# My Title\nBody") == "My Title"

    def test_skips_h2(self) -> None:
        assert extract_title_from_content("## Not H1\nBody") is None

    def test_first_h1_only(self) -> None:
        content = "Some text\n# First\n# Second"
        assert extract_title_from_content(content) == "First"

    def test_no_heading(self) -> None:
        assert extract_title_from_content("Just text") is None

    def test_empty_content(self) -> None:
        assert extract_title_from_content("") is None


class TestGenerateSlug:
    def test_readme_gives_empty(self) -> None:
        assert generate_slug("README.md") == ""

    def test_readme_case_insensitive(self) -> None:
        assert generate_slug("readme.md") == ""

    def test_strips_numeric_prefix(self) -> None:
        assert generate_slug("01-Overview.md") == "Overview"
        assert generate_slug("02_setup.md") == "setup"

    def test_replaces_underscores(self) -> None:
        assert generate_slug("my_page.md") == "my-page"

    def test_preserves_hyphens(self) -> None:
        assert generate_slug("Getting-Started.md") == "Getting-Started"

    def test_custom_index_file(self) -> None:
        assert generate_slug("index.md", index_file="index.md") == ""
        assert generate_slug("README.md", index_file="index.md") == "README"

    def test_no_md_extension(self) -> None:
        assert generate_slug("plain") == "plain"
