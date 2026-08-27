"""openstaredit - a read/write library for StarCraft map data."""

from .chk import SECTION_HEADER_SIZE, Chk, Diagnostic, Section
from .records import Action, Condition, Location, Sprite, Trigger, Unit
from .views import (
    Dimensions,
    PlayerRaces,
    PlayerSlots,
    RecordArrayView,
    ScenarioProperties,
    StringTableView,
    TilesetRef,
    TriggerListView,
    Version,
    view_for,
    TYPED_SECTIONS,
)

__all__ = [
    # container
    "Chk", "Section", "Diagnostic", "SECTION_HEADER_SIZE",
    # records
    "Unit", "Sprite", "Location", "Condition", "Action", "Trigger",
    # views
    "Dimensions", "Version", "TilesetRef", "PlayerSlots", "PlayerRaces",
    "ScenarioProperties", "RecordArrayView", "TriggerListView",
    "StringTableView", "view_for", "TYPED_SECTIONS",
]
__version__ = "0.0.2"
