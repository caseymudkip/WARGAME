"""Peace treaties (HoI4 / EU hybrid).

War score is a budget. The winner spends it on terms in the order its war
goal cares about. Once the war goal is *achieved* (or the loser has
capitulated) the goal's core terms are enforced for free; war score then only
buys extras, and how much of it the winner spends on extras is its ambition.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from wargame.conflict.war_goal import PROVINCE_GOALS, WarGoal
from wargame.core.enums import TermType, WarGoalType
from wargame.world.world import World

FLAT_TERM_COST: dict[TermType, float] = {
    TermType.WHITE_PEACE: 0.0,
    TermType.REPARATIONS: 15.0,
    TermType.DEMILITARIZATION: 25.0,
    TermType.PUPPET: 70.0,
    TermType.ANNEXATION: 100.0,
}
PROVINCE_COST_SCALE = 150.0  # War score to take 100% of the loser's strategic value.
REPARATIONS_SHARE = 0.10     # Share of the loser's daily production paid to the winner.


@dataclass(frozen=True)
class TreatyTerm:
    type: TermType
    beneficiary: str
    target: str
    cost: float
    province_ids: frozenset[int] = frozenset()


@dataclass
class PeaceTreaty:
    winner: str | None
    loser: str | None
    signed_hour: int
    terms: list[TreatyTerm] = field(default_factory=list)
    reason: str = ""

    @property
    def is_white_peace(self) -> bool:
        return all(t.type is TermType.WHITE_PEACE for t in self.terms)

    @property
    def total_cost(self) -> float:
        return sum(t.cost for t in self.terms)

    def transferred_provinces(self) -> set[int]:
        return {pid for t in self.terms for pid in t.province_ids}


def white_peace(signed_hour: int, reason: str) -> PeaceTreaty:
    return PeaceTreaty(None, None, signed_hour, [TreatyTerm(TermType.WHITE_PEACE, "", "", 0.0)], reason)


def _province_term(world: World, winner: str, loser: str, pids: Collection[int], loser_value: float) -> TreatyTerm:
    value = sum(world.provinces[pid].strategic_value() for pid in pids)
    cost = PROVINCE_COST_SCALE * value / loser_value if loser_value else 0.0
    return TreatyTerm(TermType.PROVINCE_TRANSFER, winner, loser, cost, frozenset(pids))


def _flat(term: TermType, winner: str, loser: str) -> TreatyTerm:
    return TreatyTerm(term, winner, loser, FLAT_TERM_COST[term])


def draft_treaty(
    *,
    world: World,
    goal: WarGoal,
    winner: str,
    loser: str,
    winner_side: Collection[str],
    war_score: float,
    ambition: float,
    goal_achieved: bool,
    signed_hour: int,
    reason: str,
) -> PeaceTreaty:
    """Spend the winner's war score on terms. Returns white peace if nothing is affordable."""
    loser_country = world.country(loser)
    loser_value = world.owned_value(loser)
    enforced = goal_achieved or loser_country.capitulated
    budget = max(0.0, war_score)
    terms: list[TreatyTerm] = []
    taken: set[int] = set()

    # 1. Core terms: what the war was fought for. Only the goal holder gets them.
    if winner == goal.holder and loser == goal.target:
        core: list[TreatyTerm] = []
        if goal.type in PROVINCE_GOALS:
            # Only what the loser still owns; unless enforced, only what your troops stand on.
            pids = frozenset(
                pid for pid in goal.province_ids
                if world.provinces[pid].owner == loser and (enforced or world.provinces[pid].controller in winner_side)
            )
            if pids:
                core.append(_province_term(world, winner, loser, pids, loser_value))
        elif goal.type is WarGoalType.COERCION:
            core += [_flat(TermType.DEMILITARIZATION, winner, loser), _flat(TermType.REPARATIONS, winner, loser)]
        elif goal.type is WarGoalType.TOTAL_CAPITULATION and loser_country.capitulated:
            core.append(_flat(TermType.ANNEXATION, winner, loser))
        else:  # Regime change, or total capitulation short of collapse: install a puppet.
            capital_held = world.provinces[loser_country.capital_province_id].controller in winner_side
            if enforced or capital_held:
                core.append(_flat(TermType.PUPPET, winner, loser))
        for term in core:
            if enforced:
                terms.append(term)
            elif term.cost <= budget:
                terms.append(term)
                budget -= term.cost
        taken |= {pid for t in terms for pid in t.province_ids}
        extras_budget = budget * ambition
    else:
        # The goal holder lost. The winner was not fighting for gain, so it is less greedy.
        extras_budget = budget * (0.5 + 0.5 * ambition)

    if any(t.type is TermType.ANNEXATION for t in terms):
        return PeaceTreaty(winner, loser, signed_hour, terms, reason)

    # 2. Extras: occupied provinces (highest value first), then flat terms.
    held = sorted(
        (
            p
            for p in world.owned_by(loser)
            if p.id not in taken and (loser_country.capitulated or p.controller in winner_side)
        ),
        key=lambda p: p.strategic_value(),
        reverse=True,
    )
    extras = [_province_term(world, winner, loser, [p.id], loser_value) for p in held]
    have = {t.type for t in terms}
    extras += [_flat(tt, winner, loser) for tt in (TermType.DEMILITARIZATION, TermType.REPARATIONS) if tt not in have]
    for term in extras:
        if term.cost <= extras_budget:
            terms.append(term)
            extras_budget -= term.cost

    if not terms:
        return white_peace(signed_hour, reason)
    return PeaceTreaty(winner, loser, signed_hour, terms, reason)


def apply_treaty(world: World, treaty: PeaceTreaty, participants: Collection[str], restore: bool = True) -> None:
    """Enforce terms, then (unless the war goes on) hand every still-occupied province back to its owner."""
    for term in treaty.terms:
        if term.type is TermType.PROVINCE_TRANSFER:
            for pid in term.province_ids:
                world.transfer_ownership(pid, term.beneficiary)
        elif term.type is TermType.ANNEXATION:
            for pid in [p.id for p in world.owned_by(term.target)]:
                world.transfer_ownership(pid, term.beneficiary)
        elif term.type is TermType.PUPPET:
            world.country(term.target).overlord = term.beneficiary
        elif term.type is TermType.DEMILITARIZATION:
            world.country(term.target).demilitarized = True
        elif term.type is TermType.REPARATIONS:
            debtor = world.country(term.target)
            debtor.reparations_owed[term.beneficiary] = REPARATIONS_SHARE

    if not restore:
        return
    tags = set(participants)
    for p in list(world.provinces.values()):
        if p.is_occupied and (p.owner in tags or p.controller in tags):
            world.set_controller(p.id, p.owner)
