from __future__ import annotations

from src.ui.widgets.chip_editor import ChipEditor


class TagEditor(ChipEditor):
    """Chip editor pre-configured for tags. Backward-compatible wrapper."""

    def __init__(self, parent: object = None) -> None:
        super().__init__(placeholder="Add tag...", parent=parent)

    def get_tags(self) -> list[str]:
        return self.get_items()

    def set_tags(self, tags: list[str]) -> None:
        self.set_items(tags)
