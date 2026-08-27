"""Enumerations for CHK field values.

Every value here is transcribed from ``.research/SPEC.md``, which was derived from
six independent implementations and validated against a 65-map corpus. Confidence
tiers from that document are carried into the docstrings.

Two rules govern how these are used:

**Out-of-range values are legal on disk.** Ids beyond the ranges below round-trip
fine — the record keeps the raw integer and these enums are only an interpretation
layer. Never reject a map because a value is not named here.

**Never range-check ``unitType`` against 228.** The trigger-only group pseudo-types
live at 228-232, and Blizzard's own campaign maps use them: a naive ``< 228`` check
rejects 259 legitimate conditions and 55 legitimate actions in this corpus alone.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

__all__ = [
    "SlotType", "Race", "Tileset", "ConditionType", "ActionType",
    "BriefingActionType", "UnitGroup", "TriggerFlags", "ConditionFlags",
    "ActionFlags", "UnitStateFlags", "UnitValidFieldFlags", "UnitRelationFlags",
    "SpriteFlags", "OWNER_SLOT_LABELS", "NO_STRING", "NO_UNIT", "MASK_FLAG_EUD",
]

NO_STRING = 0
"""String id 0 means "no string" everywhere it appears (SPEC 5.1)."""

NO_UNIT = 0xFFFF
"""``Sc::Unit::Type::NoUnit`` (SPEC 6.11)."""

MASK_FLAG_EUD = 0x4353
"""``MaskFlag::Enabled`` — ASCII "SC" little-endian. Marks an EUD condition/action.

Confidence C: zero occurrences in the corpus, so every EUD path is unexercised.
"""


class SlotType(IntEnum):
    """``OWNR`` / ``IOWN`` player slot type (SPEC 2.6).

    Note ``Human`` and ``GameOpen`` are both 6 with nothing to disambiguate them,
    so a name->value->name map is not round-trippable. Values are authoritative.
    """

    Inactive = 0
    GameComputer = 1
    GameHuman = 2
    RescuePassive = 3
    Unused = 4
    Computer = 5
    GameOpen = 6  # also named Human - one value, two names
    Neutral = 7
    GameClosed = 8


class Race(IntEnum):
    """``SIDE`` player race (SPEC 2.9). Zerg is 0 -- not the UI ordering."""

    Zerg = 0
    Terran = 1
    Protoss = 2
    Independent = 3
    Neutral = 4
    UserSelectable = 5
    Random = 6
    Inactive = 7


class Tileset(IntEnum):
    """``ERA`` tileset (SPEC 2.7)."""

    Badlands = 0
    SpacePlatform = 1
    Installation = 2
    Ashworld = 3
    Jungle = 4
    Desert = 5
    Arctic = 6
    Twilight = 7


class ConditionType(IntEnum):
    """``Condition.conditionType`` (SPEC 6.10). Ids 0-23, no gaps, Confidence A.

    Id 13 behaves as Never inside ``TRIG`` and marks a briefing trigger inside
    ``MBRF``.
    """

    NoCondition = 0
    CountdownTimer = 1
    Command = 2
    Bring = 3
    Accumulate = 4
    Kill = 5
    CommandTheMost = 6
    CommandTheMostAt = 7
    MostKills = 8
    HighestScore = 9
    MostResources = 10
    Switch = 11
    ElapsedTime = 12
    IsBriefing = 13
    Opponents = 14
    Deaths = 15
    CommandTheLeast = 16
    CommandTheLeastAt = 17
    LeastKills = 18
    LowestScore = 19
    LeastResources = 20
    Score = 21
    Always = 22
    Never = 23


class ActionType(IntEnum):
    """``Action.actionType`` inside ``TRIG`` (SPEC 6.11). Ids 0-59, Confidence A.

    This numbering space is NOT shared with :class:`BriefingActionType`. An
    implementation must know which section a trigger came from before reading
    this field.
    """

    NoAction = 0
    Victory = 1
    Defeat = 2
    PreserveTrigger = 3
    Wait = 4
    PauseGame = 5
    UnpauseGame = 6
    Transmission = 7
    PlaySound = 8
    DisplayTextMessage = 9
    CenterView = 10
    CreateUnitWithProperties = 11
    SetMissionObjectives = 12
    SetSwitch = 13
    SetCountdownTimer = 14
    RunAiScript = 15
    RunAiScriptAtLocation = 16
    LeaderboardCtrl = 17
    LeaderboardCtrlAtLoc = 18
    LeaderboardResources = 19
    LeaderboardKills = 20
    LeaderboardPoints = 21
    KillUnit = 22
    KillUnitAtLocation = 23
    RemoveUnit = 24
    RemoveUnitAtLocation = 25
    SetResources = 26
    SetScore = 27
    MinimapPing = 28
    TalkingPortrait = 29
    MuteUnitSpeech = 30
    UnmuteUnitSpeech = 31
    LeaderboardCompPlayers = 32
    LeaderboardGoalCtrl = 33
    LeaderboardGoalCtrlAtLoc = 34
    LeaderboardGoalResources = 35
    LeaderboardGoalKills = 36
    LeaderboardGoalPoints = 37
    MoveLocation = 38
    MoveUnit = 39
    LeaderboardGreed = 40
    SetNextScenario = 41
    SetDoodadState = 42
    SetInvincibility = 43
    CreateUnit = 44
    SetDeaths = 45
    Order = 46
    Comment = 47
    GiveUnitsToPlayer = 48
    ModifyUnitHitpoints = 49
    ModifyUnitEnergy = 50
    ModifyUnitShieldPoints = 51
    ModifyUnitResourceAmount = 52
    ModifyUnitHangarCount = 53
    PauseTimer = 54
    UnpauseTimer = 55
    Draw = 56
    SetAllianceStatus = 57
    DisableDebugMode = 58
    EnableDebugMode = 59


class BriefingActionType(IntEnum):
    """``Action.actionType`` inside ``MBRF`` (SPEC 6.12). A SEPARATE id space.

    Ids 0-9 collide numerically with :class:`ActionType` 0-9 and mean entirely
    different things. Confirmed empirically: no MBRF action byte in the corpus
    exceeds 8, while TRIG action bytes reach 57.
    """

    BriefingNoAction = 0
    BriefingWait = 1
    BriefingPlaySound = 2
    BriefingTextMessage = 3
    BriefingMissionObjectives = 4
    BriefingShowPortrait = 5
    BriefingHidePortrait = 6
    BriefingDisplaySpeakingPortrait = 7
    BriefingTransmission = 8
    BriefingSkipTutorialEnabled = 9


class UnitGroup(IntEnum):
    """Trigger-only group pseudo-types occupying the unit id space (SPEC 6.11).

    These are why ``unitType`` must never be range-checked against 228.
    """

    Id228 = 228
    AnyUnit = 229
    Men = 230
    Buildings = 231
    Factories = 232


class TriggerFlags(IntFlag):
    """``Trigger.flags`` (SPEC 6.3). **Confidence C -- unexercised by the corpus.**

    All 740 corpus triggers have ``flags == 0``; vanilla maps use the
    PreserveTrigger *action* instead. Only bit 2 is independently corroborated.
    Bits 7-31 are unnamed and must be preserved bit-for-bit.
    """

    IgnoreConditionsOnce = 0x00000001
    IgnoreDefeatDraw = 0x00000002
    PreserveTrigger = 0x00000004
    Disabled = 0x00000008
    IgnoreMiscActions = 0x00000010
    Paused = 0x00000020
    IgnoreWaitSkipOnce = 0x00000040


class ConditionFlags(IntFlag):
    """``Condition.flags`` (SPEC 6.8). Bits 0, 2, 3, 5, 6, 7 are undocumented."""

    Disabled = 0x02
    UnitTypeUsed = 0x10


class ActionFlags(IntFlag):
    """``Action.flags`` (SPEC 6.8). Bits 0, 5, 6, 7 are undocumented."""

    Disabled = 0x02
    AlwaysDisplay = 0x04
    UnitPropertiesUsed = 0x08
    UnitTypeUsed = 0x10


class UnitStateFlags(IntFlag):
    """``Unit.stateFlags`` and ``Unit.validStateFlags`` (SPEC 4.1.2).

    Bits 5-15 are unnamed by every source. Preserve verbatim.
    """

    Cloak = 0x0001
    Burrow = 0x0002
    InTransit = 0x0004
    Hallucinated = 0x0008
    Invincible = 0x0010


class UnitValidFieldFlags(IntFlag):
    """``Unit.validFieldFlags`` (SPEC 4.1.3) -- which optional fields are meaningful.

    Bit 0 (``Owner``) rests on Chkdraft alone. Bits 6-15 unnamed.
    """

    Owner = 0x0001
    Hitpoints = 0x0002
    Shields = 0x0004
    Energy = 0x0008
    Resources = 0x0010
    Hangar = 0x0020


class UnitRelationFlags(IntFlag):
    """``Unit.relationFlags`` (SPEC 4.1.1). Bits 0-8 and 11-15 unnamed."""

    NydusLink = 0x0200
    AddonLink = 0x0400


class SpriteFlags(IntFlag):
    """``THG2`` sprite flags (SPEC 4.2).

    ``DrawAsSprite`` is the authoritative discriminator, and its polarity is easy
    to invert: bit 12 SET means a pure sprite, CLEAR means a sprite-unit --
    regardless of ``IsUnit``.
    """

    BitZero = 0x0001
    BitFour = 0x0010
    BitSeven = 0x0080
    BitEight = 0x0100
    BitNine = 0x0200
    BitTen = 0x0400
    DrawAsSprite = 0x1000
    IsUnit = 0x2000
    OverlayFlipped = 0x4000
    SpriteUnitDisabled = 0x8000


OWNER_SLOT_LABELS: tuple[str, ...] = (
    "Player1", "Player2", "Player3", "Player4", "Player5", "Player6",
    "Player7", "Player8", "Player9", "Player10", "Player11", "Player12_Neutral",
    "None", "CurrentPlayer", "Foes", "Allies", "NeutralPlayers", "AllPlayers",
    "Force1", "Force2", "Force3", "Force4",
    "Unused1", "Unused2", "Unused3", "Unused4",
    "NonAlliedVictoryPlayers",
)
"""Index -> meaning for the 27 bytes of ``Trigger.owners`` (SPEC 6.4).

Note the gap at index 12 and that 22-25 also carry Chkdraft's extended-data index.
The bytes are NOT strictly boolean -- store them as raw ``u8``, never ``bool``.
"""
