# WARGAME engine architecture

Status: engine with daily and hourly loops, calibrated land warfare, an air war and a
strategy layer on the real map, smoke-tested on twelve real-world flashpoints, and a spectator
app (`wargame`) to set wars up and watch them. No fleet battles, conventional missiles or occupation yet.

## Layering

```
core/        enums, math, modifiers, clock, escalation rule table   (no deps)
world/       Province, World registry                               (core)
nation/      Country + NationalSpirit, Logistics, OOB, Nuclear       (core, world)
conflict/    War, WarGoal, PeaceTreaty                               (core, world, nation)
data/        real-world snapshots + province map -> World            (core, world, nation)
simulation   tick loop, ScenarioConfig                               (everything)
scenarios    flashpoints and custom scenarios on the real map        (simulation, data)
app/         spectator app: local HTTP server, session thread, viewer (scenarios)
```

Runtime imports only point downward. `Country` never imports `War`. Whatever
a country needs to know about its wars arrives as a small frozen context
object (`CapitulationContext`, `DailyContext`, `NuclearContext`) that `War`
or `Simulation` builds. That lets every `Country` behaviour be tested without
a war.

## GDD pillar → code map

| GDD pillar | Where it lives |
|---|---|
| 1. Setup / spectator / time | `simulation.ScenarioConfig` (frozen), `Simulation.set_speed` (the only runtime input), `core/clock.py`, `app/` (setup screen, live map, speed control) |
| 2. Province map, OOB | `world/province.py` (terrain, rivers, infrastructure, tags, `strategic_value()`), `nation/military.py`, `data/world_map.py` |
| 2/5. Land combat | `conflict/land_warfare.py` (planning, hourly assaults, encirclement, fortification, rivers, amphibious, blockade, attrition) |
| 2/5. Strategy | `conflict/strategy.py` (posture: offensive, halted, counteroffensive, active defence; withdrawals) |
| 2/5. Air war | `conflict/air_war.py` (superiority, ground support, strategic strikes, repair, coercion leverage) |
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
   - **Massing:** planners call off assaults they can't give 1.75:1 and hand the troops to the rest
     (doctrine asks 3:1 at the point of attack; advance grows with the square of the excess).
   - **Force-to-space:** a line too thin for its length is bypassed on a broad front: frontage grows as
     √(300 per km / density), up to ×3 (Ukraine's south, February 2022, held at a tenth of that).
   - **Fieldworks:** dug over months, or curated pre-war lines (the DMZ, the 2015–22 Donbas line).
   - **Drones:** a defender's drone saturation cuts depth by up to 96%. With fieldworks, a 2025-like
     front grinds at Somme pace (CSIS: 15–70 m/day). Saturation is curated at the start date and grows
     0.001 a day for every belligerent: from nothing to the 2025 front in ~2.5 years, as Ukraine went
     from almost no FPV drones in early 2022 to 1.3 million in 2024.
   - **Surprise:** Dupuy's QJM ×1.6 for an unmobilised defender, fading over three days. It belongs to
     whoever struck first: a counterattack catches no one napping.
   - **Operational reach:** strength ×0.6 per province beyond rail-restored ground (90 days), and per
     150 km inside the province being taken (a neck such as Perekop doesn't make it deeper).
   - **Bridgeheads fan out:** once through a neck or off a beach, the front widens across the province.
   - **Pinning:** troops under assault can only slowly be thinned out to reinforce elsewhere.
   - **Basing:** attacking from a host's soil (Belarus, the first 45 days of 2022).
   - **Peacetime posture:** an unmobilised defender starts manning its existing lines; capitals keep
     a garrison.

   Casualties fall on troops in contact only: no more attackers than the front can take. At
   overwhelming odds they are bounded by what the defence can still shoot, falling as 1/R beyond 3:1.

   2025 from the 2026 front: 11.9 km²/day, ~980 Russian and 0.44× Ukrainian casualties a day.
   2022 from the 2021 map: ~68,000 km² in 36 days (ISW: ~163,000); Kyiv holds. Russia takes left-bank
   Kherson in days and holds ~75,000 km² after a year (real, after the autumn counteroffensives:
   ~120,000), ~99,000 after four (real 2025: ~116,000). Oblast-sized provinces fall in sequence where
   2022's columns ran down roads through seven oblasts at once.
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
16. **Personnel, mobilisation and storage.** Only ground troops count as infantry: the ground share of
   active personnel is curated from the IISS Military Balance for 45 countries (`curated/personnel.json`:
   Russia 2021 40%, the US 48%, Ukraine 2026 93%; the median, 71%, elsewhere), and everyone called up
   since the start date fights on the ground. A nation fighting for survival, or with the enemy on its
   soil, calls up 2% of its pre-war strength a day, up to 3.5× (Ukraine: ~250,000 to ~700,000 by May
   2022); others 0.3% a day, up to 1.3×. Reserve armies with a standing wartime structure field it
   first, over the days a source states (Israel 2023: ~300,000 in 48 hours) or 14 (Finland: 280,000
   against ~24,000 active), never faster than 3; paper reserves (Ukraine's 234,000 registered in 2021)
   only raise the ceiling. Allies next door to the aggressor defend their own border with their whole
   army, not an expeditionary share. Countries already on a war footing (curated) only replace losses. GFP's equipment counts include
   storage. IISS has Russia going to war with 3,417 battle-ready tanks of 12,420, so the curated
   active share sets what fights; stored equipment is refurbished at 0.05% a day.
17. **Encirclement follows supply, not the capital.** Pockets are ground cut off from the main body
   of held territory and from friendly neutral borders (aid through Poland). A surrounded capital
   is itself the pocket, as Sarajevo was.
18. **A strategy layer decides when to attack and when to give ground up** (`conflict/strategy.py`).
   - **Posture:** attackers press with what their motivation allows; halted offensives regroup for at
     least two weeks (hysteresis), and an all-out invasion's opening campaign runs 30 days before it is
     reassessed (Russia declared its "first stage" complete on 25 March 2022). Defenders counterattack
     with a small share, go over to the offensive where a feasible concentration reaches ~2:1 against a
     thin sector (Kharkiv, September 2022), and only where it would actually move (Ukraine's restraint
     against drone-watched lines, 2024–25). A defender that outnumbers the invader attacks broadly.
   - **Defenders' war aims:** this war's losses first (liberation). Ground lost before the war waits
     (Ukraine offered to set Crimea aside in March 2022), and while the enemy is breaking through
     anywhere (≥1 km/day into its soil) a defender mounts no side shows: no incursions, no reaching
     back. Incursions into the aggressor's homeland are limited (Kursk 2024) unless the war is total.
     Defenders land from the sea only to liberate.
   - **Withdrawals** (reviewed weekly): ground two hops beyond supply when outgunned 1.5:1 (Kyiv,
     April 2022), or a bridgehead supplied only across a major river (right-bank Kherson, November 2022).
     A nation never abandons its own soil.
19. **Wars can end without a winner, and widen.** 180 days without ground fighting bring an armistice
   on current lines (a frozen treaty). Air raids alone don't keep a war going (a frozen front under
   bombardment used to grind the bombed side into a capitulation after a year or more), except in a war
   of coercion, where the raids are the war. Peace hands back only what this war took. At Tier 3, patrons
   committed to the victim intervene; co-belligerents fight from their ally's soil (expeditionary fronts) and never
   invade a neutral's soil. Opportunists join only the stronger side (Italy, June 1940): a coalition's
   collapsing member is no opportunity if the coalition would crush the jackal.
20. **Power projection and amphibious war.** Coasts facing enemy shipping are manned. A navy with
   naval superiority (1.5×) lands across sea crossings of up to 250 km; one with two or more big decks
   (carriers, helicopter carriers) anywhere within 2,000 km of a friendly coast, and supplies the
   lodgement across the ocean. Lift puts 8 troops per unit of naval power ashore a day (PLA: about a
   division per lift), filling the main beachhead before the next; waves still at sea sail for
   whichever beach the plan names. The first echelon goes in only once it can win a lodgement at the
   assault ratio (Dieppe failed piecemeal; Normandy put 156,000 ashore on the first day). The beach is
   the hard part (×0.35 down to ×0.15 by crossing length); once ashore the fight is on land, supplied
   over the beach (×0.8), and planners stand by a beachhead they have troops on.
21. **The air war** (`conflict/air_war.py`). Superiority s = A / (A + A_enemy + G_enemy): air power
   against the enemy's aircraft and long-range SAM battalions (100 air-power units each: Russia's
   ~1,500 combat aircraft never won the sky over Ukraine's ~30 battalions) plus every army's organic
   air defence. Net superiority multiplies ground combat by up to ×1.5 / ×0.67 (contested skies give
   an edge, dominance is decisive: 1991). A third of air power flies strikes, getting through at s³, on
   the five most valuable enemy provinces in range of own or allied airfields (Aviano, 1999) or of
   carriers with command of the sea. Damage cuts output and sorties and is repaired at 1.5% a day; the
   mean damage of the target's five most valuable provinces is coercion leverage, full at 0.5.
   Calibration: NATO against Serbia forces concessions in ~80 days (78 in 1999); Russia's strikes
   degrade Ukraine but cannot cripple it.
22. **Who dictates the peace.** A conqueror that broke its enemy may take land it never reached. A
   defender that outlasts an invader keeps what its troops stand on and asks for reparations
   (Iran–Iraq 1988, Ethiopia–Eritrea 2000). A regime-change war that crushes the government in exile
   reunifies its land under the installed regime; it is not an annexation.
23. **Supply reach is national.** Operational reach falls ×0.6 per 150 km beyond consolidated ground
   for rail-bound armies (Russia: Vershinin, 2021), ×0.85 for the US, whose truck- and air-borne
   logistics carried the 3rd Infantry Division ~300–350 miles to Baghdad in 14–17 days of combat
   (`curated/force_posture.json`). A US regime change in Venezuela costs ~4,000 US casualties.
24. **The spectator app** (`app/`, `wargame`). A standard-library HTTP server runs one war at a time
   in a background thread; the browser polls a JSON view (province control only when it changed)
   and draws the map from `data/map/geometry.json`, a display layer written by the map build and
   never read by the engine. Setup is the only place the scenario can be changed; afterwards the
   viewer can only change the speed, as the GDD's spectator contract requires.
25. **Flashpoint sweep** (`tools/scenarios/sweep.py`, the presets in `wargame/scenarios.py`): twelve
   real-world wars (Russia–Ukraine from 2022 and from 2026, Taiwan with and without the US, Korea,
   Russia–Estonia under Article 5, Kashmir, Syunik, Eritrea, Venezuela, Israel–Iran, the LAC) run for
   a year as a smoke test far from the calibration case. Current outcomes: an unaided Taiwan falls in
   about six months; with the US in, China cannot win the sea and the war stalls; North Korea cannot
   break the DMZ and is ground down; NATO holds Estonia, though Russia keeps the Finnish border regions it
   grabs before Finland's reservists arrive; limited wars end in small gains or white
   peace; Venezuela's government falls in about a week and the war is over in about forty days.

## Deliberately stubbed (data recorded, not yet consumed)

- `NationalSpirit.occupation_resistance`: for the occupation/partisan system.
- `Country.overlord`, `demilitarized`, `reparations_owed`: for post-war systems.
- `WarGoal.requires_occupation`: coercion goals are pursued by strikes and blockade; land warfare
  gives them low ground-offensive relevance. There is no branch choice beyond that yet.
- Conventional ballistic and cruise missiles, SEAD and strike drones are not simulated (no inventory
  data); drones appear only as saturation on the ground. Israel's coercion of Iran is therefore slow.
- Lend-lease does not yet drain the supporter's own stockpile.
- Governments in exile hosted abroad (Poland 1939–45 style, no free territory) are not modelled;
  only the free-territory variant is.

## Known deviations

- **Some invasions never start.** Planners order no assault they expect to go in below 1.75:1, and no
  landing without 1.5× naval superiority. North Korea's army is about a tenth of what holds the DMZ (local
  ratios of 0.1), and China can't out-sail the US Navy, so both wars freeze into an armistice after 180
  days. Real regimes have launched hopeless offensives, and defenders have counter-invaded (Korea, 1950),
  but here defenders fight only to restore their own borders.
- **Reservists fight like regulars.** Taiwan's 260,000 first-response reservists count as fully as its
  active army, although their readiness is widely doubted.

## Suggested next tasks

1. Naval operations: fleet battles and sea control (blockade and landings use fleet ratios today).
2. Conventional missiles, SEAD and strike drones; air defence that depletes.
3. Occupation and partisans; post-war treaty enforcement.
4. Data: land-cover terrain (forests), per-system equipment quality, non-state actors.
