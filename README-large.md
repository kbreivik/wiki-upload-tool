# Wiki.js Upload Tool

Bulk-upload markdown files to Wiki.js via GraphQL API.

## Quick Start

1. Launch `wiki-upload-tool.exe`
2. Enter your **Wiki URL** (e.g. `https://wiki.example.com`)
3. Enter your **API Key** and click **Test**
4. Browse to a folder containing `.md` files
5. Set the **Base Path** (wiki destination, e.g. `Documentation/MyProject`)
6. Click **Upload**

## Getting an API Key

In Wiki.js: **Administration > API Access > Create API Key**.
The key needs read/write permissions for pages.

## Main Window

The interface has three panels:

| Panel | Purpose |
|-------|---------|
| **Source** (left) | Pick a local folder and check/uncheck which `.md` files to include |
| **Destination & Options** (center) | Set base path, locale, index file, tags, and footer strip patterns |
| **Preview** (right) | Live preview of each file's wiki path and status (NEW or EXISTS) |

### Buttons

| Button | Description |
|--------|-------------|
| **Dry Run** | Preview what would happen without uploading anything |
| **Upload** | Upload checked files to the wiki |
| **Archive** | Move wiki pages from one path to an archive folder |
| **Move** | Move wiki pages between paths |
| **Tag Manager** | Bulk add/remove tags across multiple wiki pages |
| **Settings** | Configure defaults, import/export `.env` files |

### Options

- **Locale** — Wiki.js content locale (default: `en`)
- **Index File** — Which file maps to the base path root (default: `README.md`)
- **Update existing** — When checked, overwrites pages that already exist
- **Tags** — Applied to all uploaded pages. Checkboxes load from wiki after connecting
- **Footer strip patterns** — Regex patterns to remove trailing content before upload

## Preview Panel

The preview updates live as you change settings. For each checked file it shows:

```
page1.md
  Path: en/Documentation/MyProject/page1
  Status: NEW
```

Status values:

| Status | Meaning |
|--------|---------|
| **NEW** (green) | Page does not exist on the wiki — will be created |
| **EXISTS (will update)** (orange) | Page exists and "Update existing" is checked — will be overwritten |
| **EXISTS (will skip)** (grey) | Page exists but "Update existing" is unchecked — will be skipped |

If not connected to the wiki, the preview shows paths without status checks.

## How Files Are Processed

### Frontmatter

Markdown files can include YAML frontmatter to control the upload:

```markdown
---
title: My Page Title
description: A short description
slug: custom-slug
---

# Content starts here
```

- **title** — Used as the wiki page title. Falls back to the first `# heading`, then the filename.
- **description** — Page description shown in wiki search results.
- **slug** — Custom URL slug. Falls back to the filename with numeric prefixes stripped (`01-setup.md` becomes `setup`).

### Index File

The file matching the index file setting (default `README.md`) is uploaded to the base path root. All other files are uploaded as subpages.

Example with base path `Documentation/MyProject`:

| File | Wiki Path |
|------|-----------|
| `README.md` | `en/Documentation/MyProject` |
| `setup.md` | `en/Documentation/MyProject/setup` |
| `01-getting-started.md` | `en/Documentation/MyProject/getting-started` |

### Link Rewriting

Links between markdown files are automatically rewritten to wiki paths:

```markdown
See [Setup Guide](setup.md) for details.
→ See [Setup Guide](/en/Documentation/MyProject/setup) for details.
```

Only bare filenames are rewritten (not relative paths like `./` or `../`).

### Footer Stripping

Regex patterns added in Settings or the strip patterns chip editor will remove matching content from the end of each file before upload. Useful for removing auto-generated footers.

## Archive & Move

### Archive

Moves pages from a source folder into an archive root. The source folder name is appended automatically:

- Source: `Documentation/MyProject`
- Archive root: `Documentation/Archive`
- Result: pages move to `Documentation/Archive/MyProject/...`

Each page is copied to the new path, then the original is deleted. Folder structure is preserved.

### Move

Similar to Archive, but you choose both source and destination freely. An optional checkbox controls whether the source folder name is included in the destination path.

## Tag Manager

Bulk add or remove tags across multiple wiki pages at once.

1. Click **Tag Manager** (requires active connection)
2. Select pages from the table using the path filter
3. Check tags to **Add** or **Remove** in the right panel
4. Click **Apply** to execute changes

The tag manager shows a live count of how many pages will be affected.

## Settings Dialog

Open via the **Settings** button. From here you can:

- Change connection details (URL, API key)
- Set default source directory, base path, locale
- Set default archive root and move destination
- Add/remove footer strip patterns
- **Import from .env** — Load settings from a `.env` file
- **Export to .env** — Save current settings to a `.env` file for CLI use or backup

### .env File Format

The `.env` file uses these variables:

```
WIKIJS_URL=https://wiki.example.com
WIKIJS_API_KEY=your-api-key-here
WIKIJS_BASE_PATH=Documentation/MyProject
WIKIJS_SOURCE_DIR=C:\path\to\markdown\files
WIKIJS_LOCALE=en
```

## Settings Location

Settings are stored in:

```
%APPDATA%\wiki-upload-tool\wiki-upload-tool.ini
```

Your API key is encrypted using Windows DPAPI (tied to your Windows user account).
Settings are not portable between Windows user accounts.

To reset all settings, delete the file above.

## Troubleshooting

### Antivirus blocks the executable

Compiled Python executables may trigger false positives. Add an exclusion for `wiki-upload-tool.exe` in your antivirus software, or use the folder build which is less likely to be flagged.

### Connection fails

- Verify the Wiki URL includes `https://` and no trailing slash
- Confirm your API key has not expired
- Check that your network/firewall allows HTTPS connections to the wiki

### Pages show as NEW when they exist

Make sure the **Locale** setting matches the locale of the existing pages on the wiki (e.g. `en`, `nb`).

### Settings lost after Windows password change

The API key is encrypted with Windows DPAPI. If your Windows password is reset (not changed by you), DPAPI keys may be lost. Re-enter your API key in Settings.
