# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A standalone Python CLI tool that uploads a folder of markdown files to Wiki.js via its GraphQL API. Forked from [FAJK23/proxmox-vm](https://github.com/FAJK23/proxmox-vm/tree/main/vm-clone-manager/wiki) and generalized — no Proxmox-specific code remains.

Zero external dependencies (stdlib only: `urllib`, `json`, `argparse`, `re`, `os`).

## Running

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

## Architecture

Single-file script (`upload-and-edit-wiki-tool.py`) with this pipeline:

1. **Config resolution**: CLI args override `.env` values. The `.env` is loaded from CWD first, then script directory as fallback.
2. **File discovery** (`discover_files`): Scans `--source-dir` for `*.md` files (flat, no recursion). Index file (default `README.md`) is sorted first.
3. **Frontmatter parsing** (`parse_frontmatter`): Extracts `title`, `description`, `slug` from YAML frontmatter. Falls back to first `# heading` for title, filename for slug.
4. **Slug generation** (`generate_slug`): Strips numeric prefixes (`01-`, `02-`) and replaces underscores with hyphens. Index file maps to empty slug (base path root).
5. **Link rewriting** (`rewrite_links`): Rewrites `[text](file.md)` links to wiki.js absolute paths. Only matches bare filenames (no subdirectory paths).
6. **GraphQL upload**: Creates or updates pages via Wiki.js `pages.create`/`pages.update` mutations. Checks existence first with `pages.singleByPath`.

## Key Behaviors

- `--update-existing` flag required to overwrite existing pages (default: skip)
- `--strip-footer-pattern` accepts regex patterns to remove trailing content from files
- `--tag` is repeatable and applies to all uploaded pages
- Link rewriting only handles `[text](filename.md)` — not relative paths like `./` or `../`
- File discovery is flat (single directory, no subdirectory recursion)

## .env Variables

`WIKIJS_API_KEY`, `WIKIJS_URL`, `WIKIJS_BASE_PATH`, `WIKIJS_SOURCE_DIR`, `WIKIJS_LOCALE`
