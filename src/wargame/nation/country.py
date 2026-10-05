"""Country: a sovereign actor composed of spirit, logistics, military and nuclear components.

A Country never imports War. Whatever it needs to know about the wars it is
in arrives through small frozen context objects (CapitulationContext,
DailyContext), which War/Simulation build. That keeps the dependency graph
one-way and makes every Country method testable without a War.

Capitulation model
------------------
threshold  How much punishment this nation can absorb (0..1). Dynamic: it is
           recomputed every day from patriotism, stability and war support,
           so a nation whose public turns against the war gets more brittle.
pressure   How much punishment it is currently absorbing (0..1): a noisy-OR
           of territory lost (by strategic value, not area), the capital,
           casualties, industry, supply and exhaustion.
collapse   While pressure > threshold, collapse_progress accumulates (faster
           the bigger the overshoot); it decays when pressure recedes. The
           government falls at 1.0. This gives the spectator a visible
           "government is collapsing" phase instead of an instant flip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wargame.core.enums import ProvinceTag
from wargame.core.escalation import EscalationPolicy
from wargame.core.mathutil import clamp, noisy_or
from wargame.nation.logistics import LogisticsStockpile
from wargame.nation.military import OrderOfBattle
from wargame.nation.national_spirit import NationalSpirit
from wargame.nation.nuclear import NuclearPosture, NuclearShockResponse, nuclear_shock_response

if TYPE_CHECKING:
    from wargame.world.province import Province
    from wargame.world.world import World


# ---------------------------------------------------------------------------
# Capitulation tuning and contexts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapitulationTuning:
    # Threshold = min + (max - min) * resolve ** curve, plus situational bonuses.
    min_threshold: float = 0.15
    max_threshold: float = 0.98
    resolve_curve: float = 1.5
    existential_bonus: float = 0.10       # x patriotism: fighting for survival hardens patriots.
    external_support_bonus: float = 0.08  # x lend-lease coverage of own consumption.
    ally_bonus_per_ally: float = 0.02     # Co-belligerents fighting alongside (Tier 3).
    ally_bonus_cap: float = 0.06
    defiance_bonus: float = 0.15          # x leadership defiance: a leader who will not submit.

    # Pressure contributions (each is weight x raw 0..1 input, then noisy-OR'd).
    w_territory: float = 1.0              # Losing everything alone is enough.
    w_capital: float = 0.20
    w_capital_with_government_evacuated: float = 0.08
    w_manpower: float = 0.60              # x casualty sensitivity.
    w_industry: float = 0.25
    w_supply: float = 0.25
    w_exhaustion: float = 0.3
    w_nuclear_shock: float = 1.0          # Acute shock of nuclear strikes on a nation that cannot answer.

    # Collapse dynamics (per day).
    collapse_base_rate: float = 1.0 / 7.0  # A week just over the line topples the government.
    collapse_overshoot_rate: float = 2.0
    collapse_recovery_rate: float = 0.10


DEFAULT_CAPITULATION_TUNING = CapitulationTuning()


@dataclass(frozen=True)
class CapitulationContext:
    now_hour: int
    hostile_tags: frozenset[str]
    existential_threat: bool = False    # The enemy's war goal is regime change / total capitulation.
    external_support_level: float = 0.0 # 0..1 share of own consumption covered by lend-lease.
    allied_belligerents: int = 0


@dataclass(frozen=True)
class CapitulationAssessment:
    tag: str
    threshold: float
    pressure: float
    components: dict[str, float]
    collapse_progress: float
    last_stand: bool
    fully_occupied: bool
    capitulates: bool

    @property
    def proximity(self) -> float:
        """pressure / threshold; >=1 means the nation is actively collapsing."""
        return self.pressure / self.threshold if self.threshold > 0 else 1.0

    def narrative(self) -> str:
        top = max(self.components, key=self.components.__getitem__) if self.components else "none"
        if self.capitulates:
            return f"{self.tag} capitulates (pressure {self.pressure:.2f} vs threshold {self.threshold:.2f}; main driver: {top})."
        if self.last_stand:
            return f"{self.tag} vows to fight to the last; only total occupation will end its resistance."
        if self.collapse_progress > 0:
            return f"{self.tag}'s government is collapsing ({self.collapse_progress:.0%}); main driver: {top}."
        return f"{self.tag} holds firm (pressure {self.pressure:.2f} of {self.threshold:.2f})."


@dataclass(frozen=True)
class DailyContext:
    now_hour: int
    hostile_tags: frozenset[str] = frozenset()
    policy: EscalationPolicy | None = None  # None = not at war; normal trade rules.
    existential_threat: bool = False

    @property
    def at_war(self) -> bool:
        return bool(self.hostile_tags)


# ---------------------------------------------------------------------------
# Strategic relocation
# ---------------------------------------------------------------------------

EVACUATION_TRIGGER_LOSS = 0.10   # Start evacuating once 10% of national value is occupied...
RALLY_WAR_SUPPORT = 0.3      # x patriotism, fading over RALLY_DAYS.
RALLY_STABILITY = 0.1
RALLY_DAYS = 730
EVACUATION_FRONT_HOPS = 1        # ...for industry adjacent to hostile-held ground...
EVACUATION_MIN_DEPTH = 4         # ...to destinations at least this far from the front.
EVACUATION_LOSS = 0.30           # Share of output permanently lost in transit.
EVACUATION_DAYS = 45

PEACETIME_TEMPO = 0.2
NUCLEAR_SHOCK_DAILY_RETENTION = 0.95  # Half-life of about two weeks; the physical damage is permanent.
PERSONNEL_POWER = 0.001               # Military power per active soldier (equipment dominates).


@dataclass
class RelocationOrder:
    from_province: int
    to_province: int
    output_in_transit: float
    arrival_hour: int


# ---------------------------------------------------------------------------
# Country
# ---------------------------------------------------------------------------


@dataclass
class Country:
    tag: str
    name: str
    capital_province_id: int
    spirit: NationalSpirit
    oob: OrderOfBattle = field(default_factory=OrderOfBattle)
    logistics: LogisticsStockpile = field(default_factory=LogisticsStockpile)
    nuclear: NuclearPosture = field(default_factory=NuclearPosture)
    government_seat_id: int | None = None  # Moves when the government evacuates the capital.

    # Diplomacy / geopolitics (scenario data)
    alignment: str | None = None           # Bloc label, e.g. a treaty organisation.
    relations: dict[str, float] = field(default_factory=dict)  # How *this* country views others, -1..1.
    defensive_pacts: set[str] = field(default_factory=set)

    # Economic exposure
    trade_dependence: float = 0.3          # Share of economy exposed to sanctions.
    seaborne_import_share: float = 0.5     # Share of imports a blockade can cut. 0 if landlocked.
    blockade_interdiction: float = 0.0     # 0..1, written by the naval system.
    sanction_severity: float = 0.0         # 0..1, written by diplomatic fallout.

    # Wartime posture (curated: data/curated/force_posture.json, leadership.json)
    drone_saturation: float = 0.0          # 0..1: how densely small drones watch and strike its front.
    mobilised: bool = False                # Already on a war footing at the start date.
    leadership_defiance: float = 0.0       # 0..1: a leader who has shown he will not submit.
    hosts: frozenset[str] = frozenset()    # Belligerents allowed to attack from our soil (Belarus 2022)...
    hosting_days: int | None = None        # ...for this many days into their war (None: for its duration).
    aid_coverage: float = 0.0              # Share of our munitions and spares that arrived as aid today.
    strategic_damage: float = 0.0          # Mean damage of our most valuable provinces (air war): coercive leverage.
    rallied: bool = False                  # Has rallied against an existential invasion.

    # War-time state
    prewar_industrial_capacity: float | None = None
    relocations: list[RelocationOrder] = field(default_factory=list)
    collapse_progress: float = 0.0
    capitulated: bool = False
    nuclear_shock: float = 0.0
    nuclear_strikes_suffered: int = 0

    # Government in exile (a "Free <name>" faction that fights on after its government gave up)
    is_exile: bool = False
    exile_of: str | None = None

    # Post-war treaty state
    overlord: str | None = None
    demilitarized: bool = False
    reparations_owed: dict[str, float] = field(default_factory=dict)

    tuning: CapitulationTuning = DEFAULT_CAPITULATION_TUNING

    def __post_init__(self) -> None:
        if self.government_seat_id is None:
            self.government_seat_id = self.capital_province_id

    # --- derived economy / military ----------------------------------------

    def _held_core(self, world: World) -> list[Province]:
        return [p for p in world.owned_by(self.tag) if p.controller == self.tag]

    def industrial_capacity(self, world: World) -> float:
        """Only owned AND controlled provinces produce. Captured enemy industry is not usable yet."""
        return sum(p.effective_industry for p in self._held_core(world))

    def production_factor(self, world: World) -> float:
        if not self.prewar_industrial_capacity:
            return 1.0
        return clamp(self.industrial_capacity(world) / self.prewar_industrial_capacity, 0.0, 2.0)

    def air_sortie_capacity(self, world: World) -> float:
        """Sorties/day from every airfield we hold. Losing airfields directly cuts air power."""
        return self.oob.sorties_per_airfield * sum(
            p.infrastructure * (1.0 - p.damage) for p in world.controlled_by(self.tag) if p.has(ProvinceTag.AIRFIELD)
        )

    def import_factor(self, policy: EscalationPolicy | None) -> float:
        if policy is not None and not policy.external_trade_allowed:
            return 0.0  # Tier 1 vacuum: belligerents are sealed off.
        blockade_loss = self.blockade_interdiction * self.seaborne_import_share
        return clamp((1.0 - blockade_loss) * (1.0 - self.sanction_severity))

    def military_power(self) -> float:
        """Coarse fighting power: equipment (quality-weighted) plus manpower. Used for strategic comparisons."""
        equipment = sum(e.effective_strength for e in self.oob.equipment.values())
        return equipment + PERSONNEL_POWER * self.oob.active_personnel

    def mark_prewar_baseline(self, world: World) -> None:
        if self.prewar_industrial_capacity is None:
            self.prewar_industrial_capacity = self.industrial_capacity(world)

    def occupied_fraction(self, world: World, hostile_tags: frozenset[str]) -> float:
        total = lost = 0.0
        for p in world.owned_by(self.tag):
            v = p.strategic_value()
            total += v
            if p.controller in hostile_tags:
                lost += v
        return lost / total if total > 0 else 1.0

    # --- capitulation --------------------------------------------------------

    def capitulation_threshold(self, ctx: CapitulationContext) -> tuple[float, bool]:
        """Return (threshold, last_stand).

        A last-stand nation returns 1.0: pressure can never strictly exceed
        it, so only total occupation ends the war.
        """
        t = self.tuning
        spirit = self.spirit
        if ctx.existential_threat and spirit.fights_to_last_man(ctx.now_hour):
            return 1.0, True

        resolve = spirit.resolve(ctx.now_hour)
        threshold = t.min_threshold + (t.max_threshold - t.min_threshold) * resolve**t.resolve_curve
        if ctx.existential_threat:
            threshold += t.existential_bonus * spirit.patriotism
        threshold += t.external_support_bonus * clamp(ctx.external_support_level)
        threshold += min(t.ally_bonus_cap, t.ally_bonus_per_ally * ctx.allied_belligerents)
        threshold += t.defiance_bonus * self.leadership_defiance
        return clamp(threshold, t.min_threshold, t.max_threshold), False

    def capitulation_pressure(self, world: World, ctx: CapitulationContext) -> tuple[float, dict[str, float]]:
        """Return (pressure, weighted components). Components are what the spectator log explains."""
        t = self.tuning
        hostile = ctx.hostile_tags

        capital_term = 0.0
        capital = world.provinces[self.capital_province_id]
        if capital.controller in hostile:
            seat = world.provinces[self.government_seat_id] if self.government_seat_id is not None else capital
            evacuated = seat.id != capital.id and seat.controller not in hostile
            capital_term = t.w_capital_with_government_evacuated if evacuated else t.w_capital

        industry_loss = 1.0 - self.production_factor(world) if self.prewar_industrial_capacity else 0.0

        components = {
            "territory": t.w_territory * self.occupied_fraction(world, hostile),
            "capital": capital_term,
            "casualties": t.w_manpower * clamp(self.oob.casualty_ratio * self.spirit.casualty_sensitivity),
            "industry": t.w_industry * clamp(industry_loss),
            "supply": t.w_supply * clamp(1.0 - self.logistics.supply_ratio),
            "exhaustion": t.w_exhaustion * self.spirit.war_exhaustion,
            "nuclear_shock": t.w_nuclear_shock * self.nuclear_shock,
        }
        return noisy_or(components.values()), components

    def evaluate_capitulation(
        self, world: World, ctx: CapitulationContext, dt_days: float = 1.0
    ) -> CapitulationAssessment:
        t = self.tuning
        threshold, last_stand = self.capitulation_threshold(ctx)
        pressure, components = self.capitulation_pressure(world, ctx)
        fully_occupied = all(p.controller in ctx.hostile_tags for p in world.owned_by(self.tag))

        if fully_occupied:
            self.collapse_progress = 1.0  # Debellatio: no state left to keep fighting.
        elif pressure > threshold:
            overshoot = pressure - threshold
            self.collapse_progress += dt_days * (t.collapse_base_rate + t.collapse_overshoot_rate * overshoot)
        else:
            self.collapse_progress -= dt_days * t.collapse_recovery_rate
        self.collapse_progress = clamp(self.collapse_progress)

        if self.collapse_progress >= 1.0:
            self.capitulated = True

        return CapitulationAssessment(
            tag=self.tag,
            threshold=threshold,
            pressure=pressure,
            components=components,
            collapse_progress=self.collapse_progress,
            last_stand=last_stand,
            fully_occupied=fully_occupied,
            capitulates=self.capitulated,
        )

    # --- daily loop ------------------------------------------------------------

    def on_daily_tick(self, world: World, ctx: DailyContext) -> None:
        """Economy -> logistics -> morale, in that order (morale reads today's supply)."""
        self._advance_relocations(world, ctx.now_hour)
        if ctx.at_war:
            self._rally_against_invasion(ctx)
            self._react_to_capital_loss(world, ctx)
            if ctx.existential_threat or self.occupied_fraction(world, ctx.hostile_tags) >= EVACUATION_TRIGGER_LOSS:
                self._evacuate_threatened_industry(world, ctx)

        self.nuclear_shock *= NUCLEAR_SHOCK_DAILY_RETENTION
        tempo = 1.0 if ctx.at_war else PEACETIME_TEMPO
        arms_imports = 0.0 if ctx.at_war else 1.0  # At war, arms arrive only as aid (War._deliver_external_support).
        self.logistics.tick_day(self.production_factor(world), self.import_factor(ctx.policy), tempo, arms_imports)

        casualties_today = self.oob.roll_day()
        casualty_ratio = casualties_today / self.oob.mobilizable_manpower if self.oob.mobilizable_manpower else 0.0
        self.spirit.daily_update(
            now_hour=ctx.now_hour,
            at_war=ctx.at_war,
            casualty_ratio_today=casualty_ratio,
            occupied_fraction=self.occupied_fraction(world, ctx.hostile_tags) if ctx.at_war else 0.0,
            supply_ratio=self.logistics.supply_ratio,
        )

    def absorb_nuclear_strike(self, world: World, attacker: Country, now_hour: int) -> NuclearShockResponse:
        """Apply the political shock of a detonation on our soil: collapse, or a rally for revenge."""
        attacker_pop = sum(p.population for p in world.owned_by(attacker.tag))
        own_pop = sum(p.population for p in world.owned_by(self.tag))
        response = nuclear_shock_response(
            can_retaliate=self.nuclear.is_nuclear_power,
            population_ratio=own_pop / attacker_pop if attacker_pop else 1.0,
            stability=self.spirit.effective_stability(now_hour),
        )
        self.nuclear_strikes_suffered += 1
        # Unique id per strike so repeated strikes compound instead of refreshing.
        self.spirit.apply_shock(
            f"nuclear_strike_{self.nuclear_strikes_suffered}", now_hour,
            war_support=response.war_support, stability=response.stability, duration_days=180,
        )
        self.nuclear_shock = clamp(self.nuclear_shock + response.capitulation_shock)
        return response

    def on_peace(self) -> None:
        self.collapse_progress = 0.0
        self.capitulated = False
        self.prewar_industrial_capacity = None

    # --- strategic relocation (government and industry) -------------------------

    def _safest_destination(self, world: World, hostile: frozenset[str], min_depth: int) -> Province | None:
        best: Province | None = None
        best_key: tuple[float, float] = (-1.0, -1.0)
        for p in self._held_core(world):
            depth = world.hops_to_hostile(p.id, hostile)
            depth_score = float("inf") if depth is None else float(depth)
            if depth_score < min_depth:
                continue
            key = (min(depth_score, 99.0), p.infrastructure)
            if key > best_key:
                best, best_key = p, key
        return best

    def _rally_against_invasion(self, ctx: DailyContext) -> None:
        """A nation newly attacked for its existence rallies; patriots most. Ukraine 2022: 70% wanted to
        fight until victory (Gallup), against 24% by July 2025. A country already at war at the start
        date (mobilised) spent its rally long ago."""
        if self.rallied or not ctx.existential_threat or self.mobilised:
            return
        self.rallied = True
        self.spirit.apply_shock("invasion_rally", ctx.now_hour, war_support=RALLY_WAR_SUPPORT * self.spirit.patriotism,
                                stability=RALLY_STABILITY * self.spirit.patriotism, duration_days=RALLY_DAYS)

    def _react_to_capital_loss(self, world: World, ctx: DailyContext) -> None:
        seat = world.provinces[self.government_seat_id] if self.government_seat_id is not None else None
        if seat is None or seat.controller not in ctx.hostile_tags:
            return
        refuge = self._safest_destination(world, ctx.hostile_tags, min_depth=1)
        self.spirit.apply_shock("capital_fallen", ctx.now_hour, war_support=-0.10, stability=-0.10, duration_days=90)
        if refuge is not None:
            self.government_seat_id = refuge.id

    def _evacuate_threatened_industry(self, world: World, ctx: DailyContext) -> None:
        hostile = ctx.hostile_tags
        destination = self._safest_destination(world, hostile, EVACUATION_MIN_DEPTH)
        if destination is None:
            return  # No strategic depth: industry must be defended where it stands.
        for p in self._held_core(world):
            if p.industrial_output <= 0 or p.id == destination.id:
                continue
            hops = world.hops_to_hostile(p.id, hostile, max_hops=EVACUATION_FRONT_HOPS)
            if hops is None:
                continue
            moved = p.industrial_output
            p.industrial_output = 0.0
            self.relocations.append(
                RelocationOrder(
                    from_province=p.id,
                    to_province=destination.id,
                    output_in_transit=moved * (1.0 - EVACUATION_LOSS),
                    arrival_hour=ctx.now_hour + EVACUATION_DAYS * 24,
                )
            )

    def _advance_relocations(self, world: World, now_hour: int) -> None:
        pending: list[RelocationOrder] = []
        for order in self.relocations:
            if now_hour >= order.arrival_hour:
                world.provinces[order.to_province].industrial_output += order.output_in_transit
            else:
                pending.append(order)
        self.relocations = pending
