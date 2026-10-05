# WARGAME engine architecture

Status: engine with daily and hourly loops and calibrated land warfare on the real
map. No UI, rendering, air/naval operations or strategic AI yet.

## Layering

```
core/        enums, math, modifiers, clock, escalation rule table   (no deps)
world/       Province, World registry                               (core)
nation/      Country + NationalSpirit, Logistics, OOB, Nuclear       (core, world)
conflict/    War, WarGoal, PeaceTreaty                               (core, world, nation)
data/        real-world snapshots + province map -> World            (core, world, nation)
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
| 2. Province map, OOB | `world/province.py` (terrain, rivers, infrastructure, tags, `strategic_value()`), `nation/military.py`, `data/world_map.py` |
| 2/5. Land combat | `conflict/land_warfare.py` (planning, hourly assaults, encirclement, fortification, rivers, amphibious, blockade, attrition) |
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

## Design decisions (GDD review, resolved)

1. **"Maximum realism" vs "Epic/Aggressive"** (confirmed). Motivation is actor
   risk appetite (casualty tolerance, ambition, morale bonus). It never changes
   world physics.
2. **Nuclear use in a proxy war ratchets the tier.** Any launch in Tier 2
   promotes the war to Tier 3 (`EscalationPolicy.nuclear_use_escalates_to`).
   Patrons committed to the victim (relation ≥ 0.6) intervene
   *conventionally*; whether they then go nuclear is decided by the ordinary
   hesitation model, and MAD normally says no. Before launching, a Tier 2
   belligerent weighs a `patron_intervention` term proportional to the enemy's
   backers' military power. Basis: in 2022 the US warned Russia privately of
   "catastrophic consequences" for nuclear use in Ukraine (Sullivan); the
   response publicly sketched by Petraeus was a NATO conventional campaign
   against Russian forces in Ukraine and the Black Sea Fleet, "not nuclear for
   nuclear". CNN (March 2024) reported the US "prepared rigorously" for this
   contingency in late 2022.
3. **Nuclear victims collapse, unless they can strike back.** Resilience =
   0.65 × retaliation capability + 0.35 × size relative to the attacker.
   Below 0.5 the nation suffers a large, slowly fading capitulation shock and a
   stability hit that can break even a last-stand nation. Two strikes break the
   most patriotic small state (Japan 1945, where Hasegawa weighs the Soviet
   entry as heavily as the bombs). At or above 0.5 the nation is devastated but
   rallies for revenge (Pearl Harbor, 9/11).
   **Compellence:** a much stronger nation (power ratio 1.5 → 6) may "seal the
   deal" against a smaller, non-nuclear enemy once a war has dragged past 60
   days with conventional pressure failing. In a vacuum this is a real option.
   Sagan & Valentino (2017) found ~60% of Americans approved a nuclear strike
   killing 2M Iranian civilians to avoid 20,000 US deaths, so domestic restraint
   alone is weak. Diplomatic fallout makes it rare in Tier 2 and absent in
   Tier 3. Use stays sparing: each prior strike adds hesitation, coercive
   strikes are spaced 72h apart unless answered (Hiroshima → Nagasaki: 3 days),
   and nobody re-strikes a ruin. No-first-use pledges hold until the enemy goes
   nuclear. Calibration: unstable small nations capitulate within two days of
   one strike; patriotic ones after 2–3 strikes.
4. **Governments in exile (Free France pattern).** When an existential war
   topples the government (capitulation, or the capital falls to a
   regime-change war) but morale in free territory holds (0.7 × patriotism +
   0.3 × war support ≥ 0.55, free territory ≥ 5% of national value), a
   "Free <name>" state forms. It owns the free provinces and takes the loyal
   share of the forces and the deterrent. The old government signs a separate
   surrender (`War.settlements`; e.g. becomes a puppet) and occupied land stays
   occupied. **The attacking nation decides** whether to pursue (needs resolve
   ≥ 0.35, a 1.5x power edge, and a goal that covers the free territory) or to
   accept a ceasefire that leaves a rump state. Limited wars never produce
   exiles: losing a border war is a treaty, not the end of the state.
5. **Opportunists bring their own claims.** A Tier 3 opportunist claims the
   victim's most valuable border provinces (up to 3). It digs in once it holds
   them and receives them at the peace table if its side wins.
6. **Real-world data** lives in `data/` with per-value provenance. See
   [`data/README.md`](../data/README.md) for sources, cross-checks, upstream
   errors found and fixed, and known gaps.
7. **Aggression changes opinion.** When a war is declared (Tier 2+), third
   states friendly to the victim (relation ≥ 0.3) warm further toward it and
   turn against the attacker, as Western opinion did after 24 February 2022.
   With the real data this produces the historical coalitions: a 2026
   Russia–Ukraine proxy war draws 18 Western suppliers to Ukraine and Belarus,
   Iran and North Korea to Russia.
8. **Real geography.** `data/map/world_map.json` holds 3,604 provinces with
   real adjacency, sea crossings, terrain, cities, ports, airfields, power
   plants and de facto control at each start date (including the real
   1 January 2026 front in Ukraine). `wargame.data.world_map.build_real_world`
   combines it with a snapshot into a World; a daily tick of a real war costs
   about 4 ms.
9. **Performance budget.** A simulated year is 8,760 ticks. Hourly combat only
   touches provinces under assault, so a year of the real Russia–Ukraine war
   runs in about 3 s. Everything is keyed by plain IDs so hot loops can later
   move to NumPy arrays or a native core if global wars need it.
10. **Land warfare is calibrated to both paces of the war in Ukraine** (`conflict/land_warfare.py`,
   `tools/calibration/ukraine_2025.py`). Advance is depth × frontage. Depth is 1.2 × (R − 1)² km/day:
   about 2 km/day at R ≈ 2.3, Dupuy's WW2 division average, and up to 40 km/day in exploitation.
   Frontage is the border with the attacker's ground, limited by attacking troops at about 1,500/km
   and widening in pursuit. Conditions decide the pace, not one fitted curve:
   - **Fieldworks:** dug over months, or curated pre-war lines (the DMZ, the 2015–22 Donbas line).
   - **Drones:** a defender's drone saturation (curated) cuts depth by up to 88%. With fieldworks,
     a 2025-like front grinds at Somme pace (CSIS: 15–70 m/day).
   - **Surprise:** Dupuy's QJM ×1.6 for an unmobilised defender, fading over three days.
   - **Operational reach:** strength ×0.6 per province beyond rail-restored ground (90 days).
   - **Basing:** attacking from a host's soil (Belarus, the first 45 days of 2022).
   - **Peacetime posture:** an unmobilised defender starts manning its existing lines.

   2025 from the 2026 front: 11.1 km²/day, ~1,170 Russian and 0.46× Ukrainian casualties a day.
   2022 from the 2021 map: ~97,000 km² in 36 days, about 60% of the real gain, because oblast-sized
   provinces fall in sequence where 2022's columns ran down roads through seven oblasts at once.
   Kyiv holds in both.
11. **Rivers matter.** The map marks 965 land borders that run along a major river
   (Natural Earth scalerank ≤ 7). Assaults across them fight at 0.5× (scalerank ≤ 4:
   Dnipro, Rhine, Oder, Danube) or 0.7×, and planners prefer a dry route. Ukraine's
   Krynky bridgehead (Oct 2023 – Jul 2024) is why the Dnipro front stays quiet.
12. **War goals can carry demands.** A regime-change or total-capitulation goal may
   list provinces (`WarGoal.province_ids`): the attacker prioritises them, and they are
   annexed alongside the puppet government at the peace table (Russia's claim to the
   four oblasts it declared annexed in September 2022).
13. **War exhaustion from occupation is calibrated to Ukraine.** Each day of fighting adds
   0.005 × the occupied fraction. At Ukraine's ~19%, war support falls by about as much per year as
   Gallup measured: "fight until victory" went from 73% (2022) to 24% (July 2025). A frozen front
   (no fighting that day) adds nothing, as the 2015–21 Donbas line showed. Exhaustion counts
   0.3 toward capitulation pressure, so a 2025-like war is survivable for years, as 2023–25 was.
   Stability erosion is calibrated the same way: V-Dem-based stability went from 0.63 (2021) to
   0.53 (2026).
14. **Foreign aid decides long wars.** At war, munitions and spare parts arrive from abroad only as
   aid: arming a belligerent is a political decision, not trade. Supporters send what the
   recipient's industry can't make, up to 15% of their own output, and aid also replaces lost
   tanks and guns. Ukraine's munitions production covers 57% of its needs in the model; Zelensky put
   the domestic share at 40–60% in 2025. With aid, Ukraine fights on for years from either start
   date. If the West walks away, its supply falls to ~60% and its effectiveness halves, and it
   capitulates within about a year.
15. **Nations and leaders who will not submit.**
   - **Rally:** a nation newly invaded for its existence rallies, +0.3 × patriotism war support
     fading over two years (Ukraine 2022, Britain 1940).
   - **Home soil:** defenders fight with +0.2 × patriotism morale (Gallup International's willingness
     to fight: Ukraine 62%, Russia 32%).
   - **Defiant leaders** (curated: Zelensky, Putin) raise the capitulation threshold by 0.15. As
     attackers they never let resolve fall below 0.4: they halt, mobilise and try again, as Russia
     did in 2022–23. Only offensive losses count against an offensive.
16. **Mobilisation and storage.** A nation fighting for survival calls up 2% of its pre-war strength
   a day, up to 3.5× (Ukraine: ~250,000 to ~700,000 by May 2022); others 0.3% a day, up to 1.3×.
   Countries already on a war footing (curated) only replace losses. GFP's equipment counts include
   storage. IISS has Russia going to war with 3,417 battle-ready tanks of 12,420, so the curated
   active share sets what fights; stored equipment is refurbished at 0.05% a day.
17. **Encirclement follows supply, not the capital.** Pockets are ground cut off from the main body
   of held territory and from friendly neutral borders (aid through Poland). A surrounded capital
   is itself the pocket, as Sarajevo was.
## Deliberately stubbed (data recorded, not yet consumed)

- `NationalSpirit.occupation_resistance`: for the occupation/partisan system.
- `Country.overlord`, `demilitarized`, `reparations_owed`: for post-war systems.
- `WarGoal.requires_occupation`: for the strategic AI (blockade and strikes instead of invading).
  Land warfare already gives coercion goals low ground-offensive relevance, and a halted
  offensive (`WarParticipant.offensive_halted`) commits only 10% of the force to attacks.
- Air power enters land combat only as an air-superiority modifier (±10%); sorties,
  strikes on infrastructure and air defence are not simulated.
- Lend-lease does not yet drain the supporter's own stockpile.
- Governments in exile hosted abroad (Poland 1939–45 style, no free territory) are not modelled;
  only the free-territory variant is.

## Suggested next tasks

1. Strategic AI: theatre planning across several wars, branch choice (invade vs blockade vs strike),
   operational reserves and timing of offensives.
2. Naval and air operations: fleet battles and sea control (blockade is a fleet ratio today), sorties,
   conventional strikes on infrastructure, air defence.
3. Occupation and partisans; post-war treaty enforcement.
4. Data: land-cover terrain (forests), drones, per-system equipment quality, non-state actors.
