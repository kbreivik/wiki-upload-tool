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

## Settings Location

Settings are stored in:

```
%APPDATA%\wiki-upload-tool\wiki-upload-tool.ini
```

Your API key is encrypted using Windows DPAPI (tied to your Windows user account).

To reset all settings, delete the file above.
