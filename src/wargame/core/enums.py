"""Shared enumerations. Kept dependency-free so every layer can import them."""

from __future__ import annotations

from enum import Enum, IntEnum


class EscalationTier(IntEnum):
    """Chosen once by the player before the simulation starts. Never changes mid-run."""

    VACUUM = 1        # Strict 1v1. No trade, no lend-lease, no intervention.
    PROXY_WAR = 2     # Lend-lease / aid allowed; no foreign troops, no alliance triggers.
    UNRESTRICTED = 3  # Pacts trigger, opportunists may pile in.


class WarGoalType(Enum):
    BORDER_SKIRMISH = "border_skirmish"            # A handful of named provinces; ends fast once secured.
    TERRITORIAL_CONQUEST = "territorial_conquest"  # A larger named province set.
    COERCION = "coercion"                          # Force concessions (blockade/strikes); occupation optional.
    REGIME_CHANGE = "regime_change"                # Requires taking the capital.
    TOTAL_CAPITULATION = "total_capitulation"      # Requires the target to capitulate.


class Motivation(Enum):
    """Player-selected base motivation preset (see conflict.war.MOTIVATION_PRESETS)."""

    CAUTIOUS = "realistic_cautious"
    AGGRESSIVE = "epic_aggressive"


class Side(Enum):
    ATTACKER = "attacker"
    DEFENDER = "defender"

    @property
    def opposite(self) -> Side:
        return Side.DEFENDER if self is Side.ATTACKER else Side.ATTACKER


class ParticipantRole(Enum):
    PRIMARY = "primary"                # The original belligerents.
    CO_BELLIGERENT = "co_belligerent"  # Joined via pact or opportunism (Tier 3 only).


class RegimeType(Enum):
    LIBERAL_DEMOCRACY = "liberal_democracy"
    FLAWED_DEMOCRACY = "flawed_democracy"
    HYBRID = "hybrid"
    AUTHORITARIAN = "authoritarian"
    TOTALITARIAN = "totalitarian"


class Branch(Enum):
    LAND = "land"
    NAVAL = "naval"
    AIR = "air"
    STRATEGIC = "strategic"  # Missile forces / strategic bombers.


class SupplyType(Enum):
    FUEL = "fuel"
    AMMUNITION = "ammunition"
    RATIONS = "rations"
    SPARE_PARTS = "spare_parts"


class TerrainType(Enum):
    PLAINS = "plains"
    FOREST = "forest"
    HILLS = "hills"
    MOUNTAINS = "mountains"
    MARSH = "marsh"
    DESERT = "desert"
    JUNGLE = "jungle"
    URBAN = "urban"
    ARCTIC = "arctic"


class ProvinceTag(Enum):
    """Strategic tags. These drive AI priority, war score and capitulation weighting."""

    CAPITAL = "capital"
    URBAN_CENTER = "urban_center"
    INDUSTRIAL = "industrial"
    FARMLAND = "farmland"
    PORT = "port"
    NAVAL_BASE = "naval_base"
    AIRFIELD = "airfield"
    ENERGY = "energy"        # Power plants, refineries, oil/gas fields.
    RAIL_HUB = "rail_hub"


class NuclearDoctrine(Enum):
    NO_FIRST_USE = "no_first_use"
    ASSURED_RETALIATION = "assured_retaliation"
    FLEXIBLE_RESPONSE = "flexible_response"
    ESCALATE_TO_DEESCALATE = "escalate_to_deescalate"


class MissileClass(Enum):
    """Coarse threat classes; BMD systems declare which they can engage."""

    TACTICAL = "tactical"            # Rockets, SRBMs, cruise missiles.
    THEATER = "theater"              # MRBM / IRBM.
    STRATEGIC = "strategic"          # ICBM / SLBM.
    HYPERSONIC_GLIDE = "hypersonic"  # HGVs.


class TermType(Enum):
    WHITE_PEACE = "white_peace"
    PROVINCE_TRANSFER = "province_transfer"
    DEMILITARIZATION = "demilitarization"
    REPARATIONS = "reparations"
    PUPPET = "puppet"
    ANNEXATION = "annexation"
