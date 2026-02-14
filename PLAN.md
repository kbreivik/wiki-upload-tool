# Migration Plan: CLI → PySide6 GUI

## Phase 0: Project Scaffolding

### 0.1 — Create `pyproject.toml`
Set up the project as an installable package with `uv pip install -e ".[dev]"`.

```toml
[project]
name = "wiki-upload-tool"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["PySide6>=6.8"]

[project.optional-dependencies]
dev = ["pytest", "pytest-qt", "ruff"]

[project.gui-scripts]
wiki-upload-tool = "src.main:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["src*"]
```

The `[tool.setuptools.packages.find]` block is required — without it, setuptools won't discover `src/` as a package and `src.main:main` won't resolve. The `where = ["."]` + `include = ["src*"]` tells setuptools to scan the project root for packages matching `src*`.

### 0.2 — Update `.gitignore`
Append the following entries (some may already exist):
```
# Build artifacts
dist/
build/
*.pyd

# Venv
.venv/
venv/

# Python bytecode
__pycache__/
*.pyc
*.pyo
*.egg-info/

# Nuitka
*.build/
*.dist/
*.onefile-build/
```

### 0.3 — Create directory structure
```
src/
├── __init__.py
├── main.py              # GUI entry point (stub)
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── discovery.py
│   ├── links.py
│   ├── wiki_client.py
│   └── upload.py
├── ui/
│   ├── __init__.py
│   ├── main_window.py
│   └── widgets/
│       └── __init__.py
├── native/              # empty for now
│   └── __init__.py
└── resources/
    └── .gitkeep
tests/
├── __init__.py
├── conftest.py
├── test_config.py
├── test_discovery.py
├── test_links.py
├── test_wiki_client.py
└── test_upload.py
scripts/
└── build_nuitka.bat
```

### 0.4 — Install project in dev mode
```bash
uv pip install -e ".[dev]"
```

Verify the entry point resolves: `uv run wiki-upload-tool` should launch the GUI stub. If it fails with an import error, rename `src/` to `wiki_upload_tool/` and update all imports and the entry point to `wiki_upload_tool.main:main`.

**Commit**: `build: scaffold project structure with pyproject.toml`

---

## Phase 1: Extract Core Modules (headless, no Qt)

Extract business logic from `upload-and-edit-wiki-tool.py` into `src/core/`. Each module is pure Python stdlib — no PySide6 import. The original CLI file stays untouched and working.

### 1.1 — `src/core/config.py`
Extract from CLI:
- `load_dotenv()` → as-is
- `_update_env_file()` → as-is
- New `Config` dataclass holding: `api_key`, `wiki_url`, `base_path`, `source_dir`, `locale`, `index_file`, `tags`, `strip_footer_patterns`, `update_existing`, `dry_run`
- `Config.from_env(env_path)` class method — loads .env and returns Config
- `Config.from_dict(d)` — for GUI settings dialog to construct Config

Key decisions:
- Config is a plain dataclass (no Qt dependency). GUI will later wrap it with QSettings persistence.
- Placeholder detection logic stays in CLI `main()` since it's interactive/CLI-specific.

### 1.2 — `src/core/discovery.py`
Extract from CLI:
- `discover_files(source_dir, index_file)` → unchanged signature, but use `pathlib.Path`
- `parse_frontmatter(content)` → unchanged
- `extract_title_from_content(content)` → unchanged
- `generate_slug(filename, index_file)` → unchanged

### 1.3 — `src/core/links.py`
Extract from CLI:
- `build_link_map(files, base_path, locale, index_file)` → unchanged
- `rewrite_links(content, link_map)` → unchanged
- `strip_footer_by_patterns(content, patterns)` → unchanged

### 1.4 — `src/core/wiki_client.py`
Extract from CLI:
- `WikiClient` class wrapping `wiki_url` and `api_key`
- Methods: `graphql_request()`, `check_page_exists()`, `create_page()`, `update_page()`, `delete_page()`, `fetch_page_list()`, `fetch_page_content()`, `test_connection()`
- Uses `urllib` (stdlib) — no `requests` dependency
- Returns structured results, raises typed exceptions instead of printing + returning None
- New: `WikiClientError` exception class
- **Timeout parameter**: `WikiClient.__init__(url, api_key, timeout=30)` — passed to `urllib.request.urlopen(req, timeout=self.timeout)`. All methods inherit it. GUI can set a longer timeout; CLI defaults to 30s. Prevents hangs on unreachable hosts.

### 1.5 — `src/core/upload.py`
Extract from CLI:
- `process_file(file_info, link_map, config)` → takes Config instead of argparse Namespace
- `UploadResult` dataclass: `created`, `updated`, `skipped`, `failed`, per-page results
- `upload_pages(client, pages, config, progress_callback=None)` — orchestration loop
  - `progress_callback(current, total, page_info)` hook for GUI progress bar
- `build_tree(pages)` and `preview_placement(...)` → moved here (used by both CLI dry-run and GUI preview)

### 1.6 — Write unit tests for core modules
- `test_config.py`: .env loading, Config construction
- `test_discovery.py`: file discovery, frontmatter parsing, slug generation
- `test_links.py`: link map building, link rewriting, footer stripping
- `test_wiki_client.py`: GraphQL request formation (mock urllib), timeout propagation
  - Mock `urllib.request.urlopen` — never hit real API
  - Test error handling (HTTP errors, connection errors, malformed responses)
- `test_upload.py`: file processing, upload orchestration (mock WikiClient)
- **`test_cli_smoke.py`**: Run `python upload-and-edit-wiki-tool.py --help` and `--dry-run` with a temp source dir to verify the original CLI still works after the extraction. This catches accidental breakage (e.g., if someone later refactors the CLI to import from `src.core`).

**Commit**: `refactor: extract core business logic into src/core/ modules`
**Commit**: `test: add unit tests for all core modules`

---

## Phase 2: PySide6 GUI Shell

### 2.1 — `src/main.py`
- `QApplication` setup, app icon, dark/light theme detection
- Launch `MainWindow`
- Logging setup (to both file and GUI log viewer)

### 2.2 — `src/ui/main_window.py` — Main Window
Layout (single window):

```
┌─────────────────────────────────────────────────────┐
│  Wiki.js Upload Tool                            [_][X]│
├─────────────────────────────────────────────────────┤
│  Connection ─────────────────────────────────────── │
│  Wiki URL: [________________________] [Test]        │
│  API Key:  [••••••••••••••••••••••••] (stored)      │
│                                                     │
│  Source ─────────────────────────────────────────── │
│  Folder:   [________________________] [Browse...]   │
│  Base Path:[________________________]               │
│  Locale:   [en ▼]  Index: [README.md    ]           │
│                                                     │
│  Options ────────────────────────────────────────── │
│  ☐ Update existing pages   Tags: [________] [+]    │
│  Footer patterns: [____________________] [+]       │
│                                                     │
│  Files ──────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────┐   │
│  │ ☑ README.md        → /en/Docs              │   │
│  │ ☑ 01-overview.md   → /en/Docs/overview     │   │
│  │ ☑ 02-setup.md      → /en/Docs/setup        │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  [Dry Run Preview]  [Upload Selected]  [Cancel]     │
│                                                     │
│  ┌─ Log ───────────────────────────────────────┐   │
│  │ 14:23:01 Found 3 markdown files             │   │
│  │ 14:23:02 Uploading README.md...             │   │
│  │ 14:23:03 ✓ Created (id: 42)                 │   │
│  └─────────────────────────────────────────────┘   │
│  [============60%===========                  ]     │
└─────────────────────────────────────────────────────┘
```

Key widgets to build:
- **Connection group**: URL field + API key field (**`QLineEdit.Password` echo mode** — dots, not plaintext) + test button
- **Source group**: folder picker (QFileDialog), base path, locale combo, index file
- **Options group**: update-existing checkbox, tag list editor, footer pattern list
- **File table**: QTableView with checkboxes, filename, computed wiki path, status
- **Action buttons**: Dry Run Preview, Upload Selected, Cancel (enabled during upload only)
- **Log viewer**: QPlainTextEdit (read-only, auto-scroll)
- **Progress bar**: QProgressBar

### 2.3 — `src/ui/widgets/` — Reusable widgets
- `log_viewer.py`: QPlainTextEdit subclass with log-level coloring, timestamp prefixing
- `file_table.py`: QTableView + QAbstractTableModel for the file list with checkboxes
- `tag_editor.py`: simple list widget for adding/removing tags
- `connection_indicator.py`: status dot (red/green) + label

### 2.4 — Upload worker thread
- `src/ui/workers.py`: `UploadWorker(QThread)` with signals:
  - `progress(int current, int total, str filename)`
  - `page_done(str filename, str status, str message)`
  - `finished(UploadResult)`
  - `error(str message)`
- Calls `upload_pages()` from `src/core/upload.py` with progress_callback wired to signals
- NEVER do network I/O on the main thread
- **Cancellation**: `UploadWorker.cancel()` sets a `threading.Event`. The `upload_pages()` progress_callback checks `self._cancel_event.is_set()` between pages — if set, it stops the loop early and returns a partial `UploadResult` with the pages processed so far. The Cancel button in the UI calls `worker.cancel()` and is only enabled while an upload is in progress.
  - **Note**: Use `threading.Event`, not a plain bool. A bool works under CPython's GIL but is not formally thread-safe and will break on free-threaded Python 3.13+.

### 2.5 — Error handling strategy

Three categories of errors and how the GUI surfaces them:

| Error type | Detection | GUI behavior |
|---|---|---|
| **Connection error** (host unreachable, DNS failure, timeout) | `urllib.error.URLError` or socket timeout in `WikiClient` | Red status indicator next to URL field. Log message: "Connection failed: {reason}". Upload button stays disabled until Test succeeds. |
| **Auth failure** (invalid/expired API key) | HTTP 401/403 from `graphql_request` | Red status indicator. QMessageBox warning: "Authentication failed — check your API key." API key field highlighted with red border via stylesheet. |
| **API error** (page create/update fails, GraphQL error) | `responseResult.succeeded == false` or unexpected response shape | Per-page status in file table: red "FAILED" with tooltip showing error message. Log viewer shows full detail. Upload continues to next page (don't abort batch). Summary dialog at end shows success/fail counts. |
| **File access error** (permission denied, locked file, missing file) | `PermissionError` or `OSError` in `discover_files` or file read | Mark specific file as "ERROR" in file table with tooltip showing the OS error message. Log full path + error. Skip file and continue — don't abort the scan. |
| **Unexpected exception** | Uncaught exception in worker thread | `error` signal emitted → QMessageBox.critical with traceback. Worker stops. Log viewer gets full traceback. |

The Test button does a lightweight `{ __typename }` query and reports success/failure immediately, covering both connection and auth errors before the user attempts an upload.

### 2.6 — Settings persistence
- `src/core/config.py` already has the Config dataclass
- GUI adds QSettings wrapper: saves last-used wiki URL, base path, source dir, locale, window geometry
- API key stored via QSettings (or Windows Credential Manager in future)

**Commit**: `feat: add PySide6 main window with upload workflow`
**Commit**: `feat: add upload worker thread with progress/cancel signals`

---

## Phase 3a: MVP GUI Features

### 3.1 — Dry-run preview dialog
- Modal dialog showing a table: filename → wiki path → status (NEW/EXISTS)
- Uses `preview_placement()` from core
- "Proceed to Upload" button or "Cancel"

### 3.2 — Settings dialog
- Separate dialog for infrequently-changed settings
- .env file import/export
- API key management

**Commit**: `feat: add dry-run preview dialog`
**Commit**: `feat: add settings dialog`

---

## Phase 3b: Post-MVP Features

These are deferred until after the core GUI is stable and tested.

### 3.3 — Wiki browser panel
- Tree view (QTreeView) showing current wiki page structure
- Uses `fetch_page_list()` + `build_tree()` from core
- Refresh button, filter by locale

### 3.4 — Archive workflow
- Button: "Archive existing pages before upload"
- Confirmation dialog showing what will be moved
- Progress feedback during archive

**Commit**: `feat: add wiki browser tree view`
**Commit**: `feat: add archive workflow`

---

## Phase 4: C/C++ Extension Candidates

### Analysis of bottlenecks
The app is I/O-bound (network requests to Wiki.js API), not CPU-bound. Most operations are fast even in pure Python:

| Operation | Hot path? | C extension value |
|-----------|-----------|-------------------|
| Frontmatter parsing | No — runs once per file, ~µs per file | **Low** |
| Link rewriting (regex) | No — small files, fast regex | **Low** |
| Slug generation | No — trivial string ops | **None** |
| GraphQL serialization | No — `json.dumps` is already C | **None** |
| Footer stripping | No — small files | **Low** |
| Tree building | Maybe — only if wiki has 10k+ pages | **Medium** |

### Recommendation
**Defer C extensions to post-MVP.** The app processes tens of files against a network API — Python is not the bottleneck. If profiling later shows frontmatter parsing or tree building on very large wikis is slow, those are the candidates.

Placeholder `src/native/` directory stays empty with a README noting candidates for future optimization.

---

## Phase 5: Nuitka Build Pipeline

### 5.1 — `scripts/build_nuitka.bat`
```bat
@echo off
REM Build standalone exe with Nuitka (run from project root, inside venv)

python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=pyside6 ^
    --include-data-dir=src/resources=resources ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=src/resources/app-icon.ico ^
    --company-name="wiki-upload-tool" ^
    --product-name="Wiki.js Upload Tool" ^
    --file-version=0.1.0 ^
    --product-version=0.1.0 ^
    --output-dir=dist ^
    --output-filename=wiki-upload-tool.exe ^
    src/main.py
```

The `--windows-icon-from-ico`, `--company-name`, `--product-name`, and `--file-version` flags populate the PE version-info resource block. This improves SmartScreen/AV trust scoring — unsigned executables with proper metadata are flagged less often than anonymous ones.

### 5.2 — Build verification checklist
- [ ] Exe launches on clean Windows 10 machine (no Python installed)
- [ ] All Qt widgets render correctly
- [ ] Network requests work (urllib through Nuitka)
- [ ] Resources (icons) load from compiled paths
- [ ] PE metadata visible in file Properties → Details tab
- [ ] File size is reasonable (<100MB with PySide6)

### 5.3 — `__compiled__` resource path handling
```python
from pathlib import Path

def get_resource_path(relative: str) -> Path:
    if "__compiled__" in dir():
        # In Nuitka onefile mode with --include-data-dir, resources are extracted
        # to a temp directory. __file__ points to the extraction dir; sys.argv[0]
        # points to the exe itself, which is wrong.
        base = Path(__file__).parent
    else:
        base = Path(__file__).parent / "resources"
    return base / relative
```

**Commit**: `build: add Nuitka build script for Windows standalone exe`

---

## Phase 6: Testing Strategy

### Unit tests (Phase 1 — immediate)
- **`test_discovery.py`**: `discover_files`, `parse_frontmatter`, `generate_slug`, `extract_title_from_content`
  - Test with temp directories, various frontmatter formats, edge cases (no frontmatter, empty files)
- **`test_links.py`**: `build_link_map`, `rewrite_links`, `strip_footer_by_patterns`
  - Test link rewriting with/without matches, multiple patterns
- **`test_config.py`**: `load_dotenv`, `Config.from_env`
  - Test .env parsing, missing values, placeholder detection
- **`test_wiki_client.py`**: `WikiClient` methods
  - Mock `urllib.request.urlopen` — never hit real API
  - Test error handling (HTTP errors, connection errors, malformed responses)
  - Test that timeout parameter propagates to urlopen
- **`test_upload.py`**: `process_file`, `upload_pages`
  - Mock `WikiClient`, verify progress_callback calls, verify result counts

### CLI smoke test (Phase 1 — immediate)
- **`test_cli_smoke.py`**: Subprocess-based tests that run the original CLI script directly:
  - `python upload-and-edit-wiki-tool.py --help` exits 0
  - `python upload-and-edit-wiki-tool.py --source-dir {tmpdir} --dry-run --wiki-url http://fake --api-key fake --base-path test` runs without import errors or crashes (will print "No markdown files found" or process empty dir — that's fine, it proves the script still works)
  - This catches accidental breakage if someone later refactors the CLI to import from `src.core`

### Integration tests (Phase 2–3)
- **`test_ui.py`** (pytest-qt): widget rendering, button clicks, signal emissions
  - Test: MainWindow launches without crash
  - Test: folder picker populates file table
  - Test: upload button disabled when no files selected
  - Test: progress bar updates during mock upload
  - Test: cancel button stops upload worker

### Build tests (Phase 5)
- Nuitka compile completes without error
- Exe runs and shows main window
- Resource paths resolve correctly in compiled mode

### What NOT to test
- Don't test PySide6/Qt internals
- Don't test urllib itself

---

## Execution Order & Commits

| Step | Description | Branch |
|------|-------------|--------|
| 0 | Scaffold: pyproject.toml, directories, .gitignore updates | `feature/project-scaffold` |
| 1.1–1.5 | Extract core modules from CLI | `feature/extract-core` |
| 1.6 | Unit tests for core + CLI smoke test | `feature/extract-core` |
| 2.1–2.6 | GUI shell: main window, workers, error handling, settings | `feature/gui-shell` |
| 3a | MVP features: dry-run preview, settings dialog | `feature/gui-mvp` |
| 5.1–5.3 | Nuitka build script | `feature/nuitka-build` |
| 3b | Post-MVP: wiki browser, archive workflow | `feature/gui-extras` |

Each step gets one or more commits. Branches merge to master via PR (or direct merge if working solo).

---

## What's NOT Changing

- `upload-and-edit-wiki-tool.py` — stays as-is, still works standalone
- `.env` file format — fully compatible
- CLI argument interface — preserved for scripting
- Wiki.js GraphQL API usage — same queries/mutations
- urllib for HTTP — no new dependencies for networking
