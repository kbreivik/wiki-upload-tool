from __future__ import annotations

from pathlib import Path


def get_resource_path(relative: str) -> Path:
    """Return the absolute path to a bundled resource file.

    In development, resources live under ``src/resources/``.
    In a Nuitka onefile build (``--include-data-dir=src/resources=resources``),
    the files are extracted to a temp directory alongside the compiled modules,
    so ``Path(__file__).parent`` resolves correctly.

    Args:
        relative: Path relative to the resources root, e.g. ``"app-icon.ico"``.
    """
    if "__compiled__" in dir():
        # Nuitka onefile: resources extracted next to compiled module
        base = Path(__file__).parent
    else:
        # Development: resources are in src/resources/
        base = Path(__file__).resolve().parent.parent / "resources"
    return base / relative
