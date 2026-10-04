"""Escalation tiers as a rule table.

Systems never branch on the tier number directly. They ask the policy a
question ("may third parties ship supplies?"), which keeps tier semantics in
one place and lets us add a Tier 2.5 later without touching game logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from wargame.core.enums import EscalationTier


@dataclass(frozen=True)
class EscalationPolicy:
    tier: EscalationTier
    external_trade_allowed: bool       # Can belligerents import at all?
    lend_lease_allowed: bool           # Can third parties send supplies/weapons?
    foreign_troops_allowed: bool       # Can third parties join as co-belligerents?
    alliances_trigger: bool            # Do defensive pacts auto-fire?
    opportunistic_entry_allowed: bool  # May neighbours pile on a weakened rival?
    nuclear_fallout_multiplier: float  # Scales diplomatic/trade penalty of nuclear use.
    nuclear_hesitation_shift: float    # Added to every belligerent's hesitation.

    @staticmethod
    def for_tier(tier: EscalationTier) -> EscalationPolicy:
        return ESCALATION_POLICIES[tier]


ESCALATION_POLICIES: dict[EscalationTier, EscalationPolicy] = {
    EscalationTier.VACUUM: EscalationPolicy(
        tier=EscalationTier.VACUUM,
        external_trade_allowed=False,
        lend_lease_allowed=False,
        foreign_troops_allowed=False,
        alliances_trigger=False,
        opportunistic_entry_allowed=False,
        nuclear_fallout_multiplier=0.0,   # Nobody is watching: no sanctions to fear.
        nuclear_hesitation_shift=-0.25,
    ),
    EscalationTier.PROXY_WAR: EscalationPolicy(
        tier=EscalationTier.PROXY_WAR,
        external_trade_allowed=True,
        lend_lease_allowed=True,
        foreign_troops_allowed=False,
        alliances_trigger=False,
        opportunistic_entry_allowed=False,
        nuclear_fallout_multiplier=1.0,
        nuclear_hesitation_shift=0.0,
    ),
    EscalationTier.UNRESTRICTED: EscalationPolicy(
        tier=EscalationTier.UNRESTRICTED,
        external_trade_allowed=True,
        lend_lease_allowed=True,
        foreign_troops_allowed=True,
        alliances_trigger=True,
        opportunistic_entry_allowed=True,
        nuclear_fallout_multiplier=1.25,  # Pact members are directly implicated.
        nuclear_hesitation_shift=0.10,
    ),
}
