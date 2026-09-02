"""chklib - a read/write library for StarCraft map data."""

from .chk import SECTION_HEADER_SIZE, Chk, Diagnostic, Section
from .records import (
    Action,
    Condition,
    IsomRect,
    Location,
    Sprite,
    Trigger,
    Unit,
)
from .settings import (
    USE_DEFAULT_NO,
    USE_DEFAULT_YES,
    SoundPaths,
    SwitchNames,
    TechSettings,
    TechSettingsExpansion,
    UnitSettings,
    UnitSettingsExpansion,
    UpgradeSettings,
    UpgradeSettingsExpansion,
    settings_for,
)
from .views import (
    Forces,
    Dimensions,
    PlayerRaces,
    PlayerSlots,
    RecordArrayView,
    ScenarioProperties,
    FogGrid,
    IsomGrid,
    StringTable,
    StringTableView,
    TileGrid,
    TilesetRef,
    TriggerListView,
    Version,
    string_table_for,
    isom_for,
    terrain_for,
    view_for,
    TYPED_SECTIONS,
)

__all__ = [
    # container
    "Chk", "Section", "Diagnostic", "SECTION_HEADER_SIZE",
    # records
    "Unit", "Sprite", "Location", "Condition", "Action", "Trigger", "IsomRect",
    # views
    "Dimensions", "Version", "TilesetRef", "PlayerSlots", "PlayerRaces",
    "ScenarioProperties", "Forces", "RecordArrayView", "TriggerListView",
    "StringTableView", "StringTable", "TileGrid", "FogGrid",
    "IsomGrid", "terrain_for", "isom_for",
    "SoundPaths", "SwitchNames", "UnitSettings", "UnitSettingsExpansion",
    "UpgradeSettings", "UpgradeSettingsExpansion", "TechSettings",
    "TechSettingsExpansion", "USE_DEFAULT_NO", "USE_DEFAULT_YES",
    "settings_for", "string_table_for", "view_for",
    "TYPED_SECTIONS",
]
__version__ = "0.1.0"
