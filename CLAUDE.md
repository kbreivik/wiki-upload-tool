# Project: wiki-upload-tool

Standalone tool that uploads markdown files to Wiki.js via GraphQL API. Currently a single-file CLI script, migrating to PySide6 desktop GUI with Nuitka packaging for standalone Windows distribution (no Python required on target).

## Current State (CLI — what exists today)

Single-file script `upload-and-edit-wiki-tool.py` with zero external dependencies (stdlib only: `urllib`, `json`, `argparse`, `re`, `os`). Forked from [FAJK23/proxmox-vm](https://github.com/FAJK23/proxmox-vm/tree/main/vm-clone-manager/wiki) and generalized — no Proxmox-specific code remains.

### Running (current CLI)
```bash
# Dry run (preview without uploading)
python upload-and-edit-wiki-tool.py --source-dir ./my-docs --dry-run

# Upload with CLI args
python upload-and-edit-wiki-tool.py \
  --api-key KEY --wiki-url https://wiki.example.com \
  --base-path Documentation/MyProject --source-dir ./docs

# Upload using .env (copy .env.example to .env first)
python upload-and-edit-wiki-tool.py
```

### Current Pipeline
1. **Config resolution**: CLI args override `.env` values. The `.env` is loaded from CWD first, then script directory as fallback.
2. **File discovery** (`discover_files`): Scans `--source-dir` for `*.md` files (flat, no recursion). Index file (default `README.md`) is sorted first.
3. **Frontmatter parsing** (`parse_frontmatter`): Extracts `title`, `description`, `slug` from YAML frontmatter. Falls back to first `# heading` for title, filename for slug.
4. **Slug generation** (`generate_slug`): Strips numeric prefixes (`01-`, `02-`) and replaces underscores with hyphens. Index file maps to empty slug (base path root).
5. **Link rewriting** (`rewrite_links`): Rewrites `[text](file.md)` links to wiki.js absolute paths. Only matches bare filenames (no subdirectory paths).
6. **GraphQL upload**: Creates or updates pages via Wiki.js `pages.create`/`pages.update` mutations. Checks existence first with `pages.singleByPath`.

### Current Key Behaviors
- `--update-existing` flag required to overwrite existing pages (default: skip)
- `--strip-footer-pattern` accepts regex patterns to remove trailing content from files
- `--tag` is repeatable and applies to all uploaded pages
- Link rewriting only handles `[text](filename.md)` — not relative paths like `./` or `../`
- File discovery is flat (single directory, no subdirectory recursion)

### .env Variables
`WIKIJS_API_KEY`, `WIKIJS_URL`, `WIKIJS_BASE_PATH`, `WIKIJS_SOURCE_DIR`, `WIKIJS_LOCALE`

---

## Target State (GUI migration)

### Tech Stack
- **GUI**: PySide6 (Qt 6, LGPL)
- **Packaging**: Nuitka (compiles Python to C to native binary via MSVC)
- **Performance**: C/C++ extensions for bottlenecks (cffi/pybind11)
- **Package manager**: uv (NEVER use pip directly — always `uv pip` or `uv run`)
- **Target OS**: Windows 10/11 (x64)
- **Python**: 3.12+ (build machine only)

### Environment Setup
```bash
# Create venv with uv
uv venv .venv
# Activate (Windows)
.venv\Scripts\activate
# Install project + dev deps (pytest, ruff, pytest-qt)
uv pip install -e ".[dev]"
# Install build deps (nuitka, zstandard, ordered-set) — only needed for standalone exe
uv pip install -e ".[build]"
```
IMPORTANT: All Python commands run inside the venv. Build deps (including Nuitka) MUST be installed in the venv, not globally.

### Target Architecture
```
wiki-upload-tool/
├── upload-and-edit-wiki-tool.py  # KEEP — original CLI (still usable standalone)
├── src/
│   ├── main.py                   # GUI entry point, QApplication setup
│   ├── ui/                       # PySide6 windows, dialogs, widgets
│   │   ├── main_window.py
│   │   └── widgets/
│   ├── core/                     # Business logic extracted from CLI
│   │   ├── wiki_client.py        # GraphQL API (extracted from CLI)
│   │   ├── discovery.py          # File discovery + frontmatter parsing
│   │   ├── upload.py             # Upload orchestration + progress
│   │   ├── links.py              # Link rewriting logic
│   │   └── config.py             # Settings (QSettings for GUI, .env compat for CLI)
│   ├── native/                   # C/C++ extensions (optional, for speed)
│   │   ├── CMakeLists.txt
│   │   ├── include/
│   │   └── src/
│   └── resources/                # Icons, .ui files, assets
├── tests/
├── scripts/
│   └── build_nuitka.bat          # Nuitka build script for Windows
├── .venv/                        # venv (gitignored)
├── .python-version               # Pin Python version for uv
├── CLAUDE.md
├── pyproject.toml
└── requirements.txt
```

### Migration Rules
- IMPORTANT: Extract logic from CLI into `src/core/` modules — do NOT rewrite from scratch
- IMPORTANT: The original CLI script must remain functional as a standalone fallback
- `src/core/` modules should work headless (no Qt dependency) so CLI can reuse them
- GUI adds: folder picker, progress bar, log viewer, settings dialog, dry-run preview table

## Commands
- `python upload-and-edit-wiki-tool.py --dry-run` — run original CLI
- `uv venv .venv` — create venv
- `uv pip install -e ".[dev]"` — install project + dev deps (pytest, ruff, pytest-qt)
- `uv pip install -e ".[build]"` — install build deps (nuitka, zstandard, ordered-set)
- `uv run python src/main.py` — run the GUI app in dev mode
- `uv run pytest tests/` — run all tests
- `uv run pytest tests/ -x -v` — run tests, stop on first failure, verbose
- `scripts/build_nuitka.bat` — build standalone folder (default, best AV compat)
- `scripts/build_nuitka.bat onefile` — build single exe (may trigger AV)
- `uv run ruff check src/` — lint
- `uv run ruff format src/` — format

## Code Style
- Python 3.12+ features OK (match statements, type hints, `|` union syntax)
- Type hints on all public functions; use `from __future__ import annotations`
- PySide6 signals/slots pattern; NEVER use threading.Thread for UI updates — use QThread + signals
- C extensions: follow PEP 7 for C code style
- Keep UI and business logic strictly separated (ui/ never imports from native/ directly)
- Use pathlib.Path, never os.path (new code only — CLI uses os.path, that is fine)
- Logging via `logging` module, never print() (new code only)
- NEVER use `pip install` — always use `uv pip install`

## C/C++ Extension Rules
- Extensions live in `src/native/`
- Build with setuptools Extension or cffi — must be compatible with Nuitka --include-module
- All C functions exposed to Python must have proper error handling and GIL management
- Use `ctypes` or `cffi` for simple C libs; `pybind11` for C++ with complex types
- IMPORTANT: Test C extensions both as Python imports AND in Nuitka-compiled builds
- Always provide a pure-Python fallback so the app works without compiled extensions

## Nuitka Build Notes

### Build modes
- **Default (`scripts/build_nuitka.bat`)**: Standalone folder build — best AV compatibility. Output: `dist\standalone\main.dist\`. Distribute the entire folder.
- **Onefile (`scripts/build_nuitka.bat onefile`)**: Single exe — convenient but self-extraction triggers heuristic AV detectors (Defender Wacatac, Fortinet). Not recommended for distribution.

### AV hardening flags
- `--msvc=latest` — explicitly compile with MSVC. Produces PE binaries with Rich Headers that AV vendors trust. MinGW binaries lack these and are flagged more often.
- `--windows-console-mode=attach` — GUI runs clean but console is available when launched from terminal. Using `disable` is a major AV trigger because malware hides its console window.
- `--file-description`, `--copyright`, `--company-name` — filled with real values. Nuitka defaults to placeholder text which correlates with unsigned malware in AV training data.
- `--no-deployment-flag=self-execution` — suppresses Nuitka's self-execution deployment warning (standalone only).

### Code signing
The build script automatically attempts to sign the exe with `signtool` if available. For corporate distribution:
1. **Azure Trusted Signing** (cheapest option ~$10/mo) or an **OV code certificate** from DigiCert/Sectigo
2. Install Windows SDK for `signtool.exe`
3. The script runs: `signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "<exe>"`
4. If no cert is installed, signing is silently skipped

### Pre-distribution checklist
1. Scan with [VirusTotal](https://www.virustotal.com/) — expect 0-2 detections for unsigned standalone, more for onefile
2. Submit false positives to [Microsoft WDSI](https://www.microsoft.com/en-us/wdsi/filesubmission)
3. For Fortinet environments, add the SHA256 hash as an exemption on FortiGate

### Other notes
- Include `--enable-plugin=pyside6` for Qt support
- Add `--include-data-dir=src/resources=resources` for assets
- C extensions: use `--include-module=` for each native module
- Nuitka must be installed in the venv, not globally — it needs access to the same packages
- Install `.[build]` extras for zstandard (onefile compression) and ordered-set (build performance)

## Git Workflow
- Branch per feature: `feature/description` or `fix/description`
- Commit messages: conventional commits (`feat:`, `fix:`, `refactor:`, `build:`)
- NEVER commit secrets, API keys, .env files, or .venv/
- NEVER force push to main

## Testing
- pytest for Python tests
- C extensions: test via Python bindings AND with standalone test harness
- UI tests: use `pytest-qt` for widget testing
- Mock wiki API calls in tests; never hit real endpoints in CI

## Known Gotchas
- PySide6 + Nuitka: always use `--enable-plugin=pyside6`, not the pyqt5 plugin
- Nuitka onefile extracts to temp dir — use `__compiled__` check for resource paths
- Windows Defender SmartScreen may flag unsigned exe — code signing recommended
- Original CLI uses urllib (stdlib) — GUI wiki_client.py should also avoid requests lib to keep deps minimal
- Wiki.js GraphQL API has no batch mutation — uploads are sequential per page
- uv creates .venv by default — add `.venv/` to .gitignore