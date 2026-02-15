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

## Settings Location

Settings are stored in:

```
%APPDATA%\wiki-upload-tool\wiki-upload-tool.ini
```

Your API key is encrypted using Windows DPAPI (tied to your Windows user account).
Settings are not portable between Windows user accounts.

To reset all settings, delete the file above.

## Settings Dialog

Open via the **Settings** button. From here you can:

- Change connection details (URL, API key)
- Set default source directory, base path, locale
- Set default archive root and move destination
- Add/remove footer strip patterns
- **Import from .env** — Load settings from a `.env` file
- **Export to .env** — Save current settings to a `.env` file for CLI use or backup
