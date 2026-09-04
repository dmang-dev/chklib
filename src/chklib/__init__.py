"""chklib - a read/write library for StarCraft map data."""

from .chk import SECTION_HEADER_SIZE, Chk, Diagnostic, Section
from .records import (
    DOODAD_DISABLED,
    DOODAD_ENABLED,
    MAX_CUWPS,
    Action,
    Condition,
    Cuwp,
    Doodad,
    IsomRect,
    Location,
    Sprite,
    Trigger,
    Unit,
)
from .restrictions import (
    AVAILABLE_NO,
    AVAILABLE_YES,
    TOTAL_PLAYERS,
    TechRestrictions,
    TechRestrictionsExpansion,
    UnitRestrictions,
    UpgradeRestrictions,
    UpgradeRestrictionsExpansion,
    restrictions_for,
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
    TYPED_SECTIONS,
    CuwpUsage,
    Dimensions,
    EditorVersion,
    FogGrid,
    Forces,
    IsomGrid,
    PlayerColors,
    PlayerRaces,
    PlayerSlots,
    RecordArrayView,
    RemasteredColors,
    ScenarioProperties,
    ScenarioType,
    StringTable,
    StringTableView,
    TileGrid,
    TilesetRef,
    TriggerListView,
    ValidationCode,
    Version,
    isom_for,
    string_table_for,
    terrain_for,
    view_for,
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
    # restrictions
    "UnitRestrictions", "UpgradeRestrictions", "UpgradeRestrictionsExpansion",
    "TechRestrictions", "TechRestrictionsExpansion", "restrictions_for",
    "TOTAL_PLAYERS", "AVAILABLE_NO", "AVAILABLE_YES",
    # trigger unit properties, doodads, colours, versions, validation
    "Cuwp", "Doodad", "MAX_CUWPS", "DOODAD_ENABLED", "DOODAD_DISABLED",
    "CuwpUsage", "PlayerColors", "RemasteredColors",
    "ScenarioType", "EditorVersion", "ValidationCode",
    "SoundPaths", "SwitchNames", "UnitSettings", "UnitSettingsExpansion",
    "UpgradeSettings", "UpgradeSettingsExpansion", "TechSettings",
    "TechSettingsExpansion", "USE_DEFAULT_NO", "USE_DEFAULT_YES",
    "settings_for", "string_table_for", "view_for",
    "TYPED_SECTIONS",
]
__version__ = "0.2.0"
