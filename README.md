# Wiki.js Upload Tool

Desktop tool for uploading markdown files to Wiki.js via GraphQL API. Built with PySide6, packaged as a standalone Windows executable with Nuitka.

## Requires

- **Python 3.12+**
- **uv** (Python package manager)
- **MSVC** (Visual Studio Build Tools — for Nuitka compilation only)

### Installing prerequisites with PowerShell

```powershell
# Install uv
irm https://astral.sh/uv/install.ps1 | iex

# Install Python 3.12 via uv
uv python install 3.12

# From the project root (where pyproject.toml is):
# -e = editable install (changes to src/ take effect without reinstalling)
# .[dev] = install project + dev extras defined in pyproject.toml
#          under [project.optional-dependencies] (pytest, ruff, pytest-qt)
uv venv .venv
.venv\Scripts\activate
uv pip install -e ".[dev]"

# Install Nuitka (only needed for building the standalone exe)
uv pip install nuitka
```

To build the standalone executable you also need the **MSVC C/C++ compiler**. Install the Visual Studio Build Tools if you don't have them:

```powershell
# Download and run the Visual Studio Build Tools installer
irm https://aka.ms/vs/17/release/vs_BuildTools.exe -OutFile vs_BuildTools.exe
Start-Process -Wait -FilePath .\vs_BuildTools.exe -ArgumentList `
    "--quiet", "--wait", "--norestart", `
    "--add", "Microsoft.VisualStudio.Workload.VCTools", `
    "--includeRecommended"
Remove-Item .\vs_BuildTools.exe
```

## Building

### Build Modes

The build script supports two modes. Choose based on your deployment environment.

#### Onefile (default)

```bash
scripts\build_nuitka.bat
```

Produces a single `dist\wiki-upload-tool.exe`. At runtime, it extracts DLLs to a temp folder (`%LOCALAPPDATA%\Temp\onefile_*`). This extraction behavior may trigger Windows Defender or corporate AV false positives.

#### Standalone Folder

```bash
scripts\build_nuitka.bat folder
```

Produces `dist\folder\main.dist\` containing `main.exe` alongside its DLLs. No temp extraction at runtime, so it is less likely to trigger AV. Zip the folder for distribution.

### Windows Defender Exclusions

If the onefile build triggers Defender, add exclusions for the project directory and the temp extraction path:

```powershell
# Dev machine — exclude project and build output
Add-MpPreference -ExclusionPath "C:\path\to\wiki-upload-tool"

# End-user machine — exclude onefile temp extraction
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\Temp\onefile_*"
```

For org-wide deployment, use Group Policy:

1. Open **Group Policy Editor** (`gpedit.msc`)
2. Navigate to **Computer Configuration > Administrative Templates > Windows Components > Microsoft Defender Antivirus > Exclusions**
3. Add the installation path and temp extraction path to **Path Exclusions**

For corporate environments where AV policy cannot be modified, use the **folder build mode** instead.

## Running (Development)

```bash
uv run python src/main.py
```

## CLI (Standalone)

The original CLI script still works without any dependencies:

```bash
python upload-and-edit-wiki-tool.py --source-dir ./docs --dry-run
```

See `python upload-and-edit-wiki-tool.py --help` for all options.

## Testing

```bash
uv run pytest tests/
uv run pytest tests/ -x -v   # stop on first failure, verbose
```
