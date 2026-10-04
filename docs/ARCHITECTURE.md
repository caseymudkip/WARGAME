# WARGAME engine architecture

Status: foundation layer (data model + daily logic loops). No UI, rendering,
combat resolution or strategic AI yet.

## Layering

```
core/        enums, math, modifiers, clock, escalation rule table   (no deps)
world/       Province, World registry                               (core)
nation/      Country + NationalSpirit, Logistics, OOB, Nuclear       (core, world)
conflict/    War, WarGoal, PeaceTreaty                               (core, world, nation)
simulation   tick loop, ScenarioConfig                               (everything)
```

Runtime imports only point downward. `Country` never imports `War`. Whatever
a country needs to know about its wars arrives as a small frozen context
object (`CapitulationContext`, `DailyContext`, `NuclearContext`) that `War`
or `Simulation` builds. That lets every `Country` behaviour be tested without
a war.

## GDD pillar → code map

| GDD pillar | Where it lives |
|---|---|
| 1. Setup / spectator / time | `simulation.ScenarioConfig` (frozen), `Simulation.set_speed` (the only runtime input), `core/clock.py` |
| 2. Province map, OOB | `world/province.py` (terrain, infrastructure, tags, `strategic_value()`), `nation/military.py` |
| 3. Economy, logistics, relocation | `nation/logistics.py`, `Country.production_factor`, `Country.air_sortie_capacity`, `Country._evacuate_threatened_industry` |
| 4. Escalation tiers | `core/escalation.py` (policy table), consumed by `War` and `Country.import_factor` |
| 5. Cost/reward, morale, WMD, BMD | `conflict/war.py` (`CampaignLedger`, `MotivationProfile`), `nation/national_spirit.py`, `nation/nuclear.py` |
| 6. War goals, treaties | `conflict/war_goal.py`, `conflict/treaty.py` |

## Key design decisions

**1. Fixed 1-hour tick. Speed only affects playback.** The GDD says players can
watch "hour-by-hour for intense tactical phases". If resolution changed with
speed, the same scenario could end differently depending on how it was
watched. So the engine always steps one hour, and `TimeScale` only sets how
many ticks run per wall-clock second. Systems subscribe at HOURLY (combat,
movement), DAILY (economy, morale, war score, capitulation, peace) or WEEKLY
(diplomacy). `test_playback_speed_never_changes_the_outcome` enforces this.

**2. One valuation for everything.** `Province.strategic_value()` feeds AI
defence priority, war score, treaty costs and capitulation pressure. The AI
therefore fights hardest for exactly the ground whose loss would break its
nation, and "losing 20% of territory" is measured by *value*, not area.
Losing an empty steppe barely registers. Losing the industrial heartland does.

**3. Escalation tiers are a rule table, not `if tier == 2` branches.** Systems
ask the policy (`lend_lease_allowed`, `alliances_trigger`,
`nuclear_hesitation_shift`, ...). Adding an intermediate tier later is a data
change.

**4. Explainable numbers.** Capitulation pressure, war score and nuclear
hesitation are all stored as `dict[str, float]` component breakdowns, so the
spectator feed can always say *why* something happened ("main driver:
territory"). Every war keeps a narrative `events` log.

**5. Determinism.** There is one seeded RNG per simulation, and any iteration
whose order could affect results is sorted. Two runs with the same seed
produce identical histories.

## Capitulation model

```
threshold = 0.15 + 0.83 * resolve^1.5             resolve = 0.4·patriotism + w_s·stability + w_ws·war_support
          + 0.10 * patriotism    (existential)     w_s / w_ws depend on regime type:
          + 0.08 * lend-lease coverage             democracies lean on war support,
          + 0.02 per allied belligerent (≤0.06)    autocracies on regime stability
          → 1.0 if patriotism ≥ 0.9 and survival is at stake (last stand)

pressure  = 1 − Π(1 − c_i)   (noisy-OR) over
            territory (by value) · capital (reduced if government evacuated) ·
            casualties × sensitivity · industry lost · supply shortfall · war exhaustion

collapse_progress += (1/7 + 2·overshoot) per day while pressure > threshold
                  −= 0.10 per day otherwise; government falls at 1.0
full occupation   → immediate capitulation (debellatio), even for a last-stand nation
```

The noisy-OR means losing all your territory is enough on its own, and several
moderate pressures compound instead of simply adding. Calibration
(see `tests/test_capitulation.py`): an unstable hybrid regime surrenders after
losing about 23% of its value in a week. A patriotic democracy shrugs off the
same losses. A last-stand nation holds out until the final province falls.

## How wars end (`War._try_conclude`, checked daily)

1. A primary belligerent capitulated. The winner drafts a treaty and the goal's core terms are enforced.
2. The war goal is achieved (goal provinces held / capital taken). Core terms are enforced and war score buys extras.
3. The attacker's resolve is broken by the cost/reward ledger. The defender dictates terms if ahead. If the attacker is ahead, there's a ceasefire on current lines. Otherwise it's white peace.
4. Mutual exhaustion with a near-even score ends in white peace.

The winner spends `war_score × ambition` on extras. Ambition comes from the
player's motivation preset, so an "epic/aggressive" attacker grabs extra
provinces at the table and a "realistic/cautious" one takes only what it came
for.

## GDD review: gaps, tensions, open questions

These need a design decision before or during the next tasks:

1. **"Maximum realism" vs "Epic/Aggressive".** Resolved here by treating
   motivation as actor risk appetite (casualty tolerance, ambition, morale
   bonus). It never changes world physics. Confirm that's the intent.
2. **Tier 2 "risks MAD": from whom?** In a proxy war no foreign power fights.
   Currently MAD only comes from the belligerents' own arsenals, plus
   extended deterrence in Tier 3. Decide whether nuclear use can *ratchet* a
   Tier 2 war into Tier 3 (suggest a scenario flag, default off), or whether
   tiers are absolute.
3. **Morale effect of being nuked.** It could cause a rally or a collapse.
   Currently: −0.10 war support, −0.15 stability on the victim. This needs a
   ruling, ideally per-regime.
4. **Government in exile.** A fully occupied nation always capitulates. HoI4
   lets a nation with fighting allies keep going in exile. Probably wanted
   for Tier 3.
5. **Coalition war goals.** Tier 3 joiners currently fight for the primary's
   goal. Opportunists should probably bring their own claim (their own
   `WarGoal`) and get a say in the peace.
6. **Real-world data.** Pillar 2 asks for "highly accurate real-world"
   OOBs. This needs (a) a schema with provenance on every number (source,
   as-of date, confidence), (b) separate 2021 and 2026 snapshots, since
   several major militaries changed drastically in between, and (c) a
   licensing check: some authoritative OOB references are commercial and
   copyrighted, so we can't ship them verbatim. All test data here is
   fictional.
7. **Performance budget.** A simulated year is 8,760 ticks. Daily systems are
   fine in Python. Hourly combat over a global map of thousands of provinces
   is not. Everything here is keyed by plain IDs so hot loops can later move
   to NumPy arrays or a native core without changing the model.

## Deliberately stubbed (data recorded, not yet consumed)

- `NationalSpirit.occupation_resistance`: for the occupation/partisan system.
- `Country.overlord`, `demilitarized`, `reparations_owed`: for post-war systems.
- `MotivationProfile.morale_bonus`, `LogisticsStockpile.combat_effectiveness()`,
  `OrderOfBattle.branch_power()`: for combat.
- `WarGoal.requires_occupation`, `WarParticipant.offensive_halted`: for the strategic AI
  (blockade instead of invading, pausing offensives).
- Lend-lease does not yet drain the supporter's own stockpile.

## Suggested next tasks

1. Data schema and scenario loader (JSON, with provenance), plus 2021/2026 snapshots.
2. Hourly combat and movement: terrain, supply effectiveness, quality exponent, frontlines.
3. Strategic AI: theatre planning from `strategic_value`, branch superiority
   (invade vs blockade vs strike), `offensive_halted` consolidation.
4. Naval and air: write `blockade_interdiction`, consume sorties, conventional strikes on infrastructure.
5. Occupation and partisans; post-war treaty enforcement.
