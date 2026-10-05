# Real-world data

Two snapshots of 140–145 countries, for scenarios starting in **2021** (January 2021, before the
invasion of Ukraine) and **2026** (January 2026). Every number records where it came from.

```
data/
  sources.json          every source: publisher, edition, as-of date, upstream commit, licence, caveats
  raw/                  upstream extracts, byte-for-byte reproducible (tools/data/extract_upstream.py)
  curated/              hand-researched inputs, each entry with evidence and confidence
    nuclear.json          SIPRI warhead counts + doctrine/delivery characterisation
    missile_defense.json  BMD systems (engagement envelopes, Pk estimates) and who fields them
    alliances.json        treaty pacts (NATO, CSTO, US bilaterals, 2024 RUS-PRK, 2025 SAU-PAK...) and leanings
    willingness_to_fight.json   Gallup International "would you fight for your country?"
    overrides.json        manual corrections, each with a citation (applied last)
  snapshots/2021.json   built output (tools/data/build_dataset.py); do not edit by hand
  snapshots/2026.json
```

Browse a country with sources: `python -m wargame.data RUS UKR --year 2026` (add `--all` for every field).

Rebuild after changing raw or curated inputs: `python tools/data/build_dataset.py`.

**Correcting a value:** add it to `curated/overrides.json` with a citation and rebuild. Overrides
are applied after every automatic rule, and the record keeps the full history (what the rule did,
what the override changed, and why). Never edit the snapshots by hand.
`tests/test_dataset.py` fails if a committed snapshot no longer matches its inputs.

## The province map (`map/world_map.json`)

3,604 provinces covering the whole world, built by `tools/map/build_map.py` (dev dependencies:
shapely, pyproj, pyshp; inputs are downloaded pinned and sha256-checked) from:

- **Natural Earth 5.1.2** admin-1 boundaries (public domain). Over-fragmented countries are merged to
  Natural Earth regions (UK 234 units to 17, Slovenia 193 to 12, Italy 110 to 20, Philippines, Latvia,
  North Macedonia, Malta, Uganda). Duplicate names are disambiguated ("Washington (Federal District)").
- **Adjacency** from shared borders, **coast** from borders not shared with another province, and
  **sea crossings** up to 250 km with their length (Kerch Strait 3 km, Taiwan Strait 126–142 km, Kinmen 8 km).
- **River borders**: a land border is a river crossing when at least half of it runs within ~10 km of a
  Natural Earth river or lake centreline of scalerank ≤ 7 (965 borders; the Dnipro between the two halves
  of Kherson, the Rhine, the Danube, the Dniester...). Short coastline fragments of a border are ignored.
- **Terrain** from Natural Earth physical regions (mountain ranges, deserts, plateaus, wetlands), latitude
  where no region applies, and urban terrain for dense city provinces. Coarse: forests outside the boreal
  belt are not detected yet (needs land-cover data).
- **Cities and capitals** (Natural Earth populated places), **ports**, **airports** (incl. military),
  **power plants** (WRI Global Power Plant Database, CC BY 4.0) and curated **naval bases**.
- **Control at each start date** (`curated/map_control.json`): ownership is de jure, control de facto.
  Crimea and Sevastopol are Ukrainian and Russian-held in both snapshots. For 2026, Donetsk, Luhansk,
  Zaporizhzhia, Kherson and Kharkiv are split along **DeepStateMap's real front line of 1 January 2026**
  (116,219 km2 occupied, ~19%). Where DeepState's and Natural Earth's coastlines disagree, the split leaves
  thin strips and specks (a Ukrainian-held sliver of the occupied left bank); these move to the piece they
  lie against, so right-bank Kherson touches the occupied left bank only across the Dnipro. For 2021, Donetsk and Luhansk are split along the 2015–2022 line of contact
  (approximated from front-line settlements, ±10 km). Abkhazia and Transnistria are Russian-controlled;
  Stepanakert, Khojaly and Khojavend are Armenian-backed in 2021 and Azerbaijani in 2026.
- Dependencies belong to their sovereign state (Guam and Puerto Rico to the US, Greenland to Denmark,
  Hong Kong to China, the Falklands and Diego Garcia to the UK).

The map stores geography and facts only. Population per province, industry, infrastructure and the
INDUSTRIAL / FARMLAND tags depend on the snapshot and are modelled by `wargame.data.world_map`
(formulas in its docstring). `build_real_world(2026)` assembles a playable World in under 0.1 s.

Not modelled yet (listed in `map_control.json`): the Golan Heights and South Ossetia (not separable in
Natural Earth), and territory held by non-state actors (Houthis, RSF, Myanmar's resistance, Hamas), since
the engine has no non-state actors.

## Calibration benchmarks (land combat)

`tools/calibration/ukraine_2025.py` runs Russia against Ukraine from both start dates and compares with
what happened. Checked in `tests/test_land_warfare.py`.

| Benchmark | Real | Model | Source |
|---|---|---|---|
| Ground taken, 2025 | 4,336 km2 (11.9/day) | 11.9 km2/day | DeepState, 2 Jan 2026 |
| Russian casualties, 2025 | ~415,000 (1,137/day) | ~980/day | UK Defence Intelligence, 14 Jan 2026 |
| Ukrainian / Russian casualties | 500–600k vs ~1.2M since 2022 (0.42–0.5) | 0.44 | CSIS, Jan 2026 |
| Russian-held Ukraine, 31 March 2022 | 163,000–167,000 km2 (from ~43,000) | ~68,000 km2; Kyiv holds | ISW; Statista |
| Russian-held Ukraine after four years, from 2022 | ~116,000 km2 (2025) | ~99,000 km2 | DeepState |
| NATO air campaign against Serbia | concessions after 78 days (1999) | ~80 days | — |
| Ukraine's endurance with aid | fighting since 2022 | fighting for years from either start | — |
| Ukraine's endurance without aid | — | capitulates within ~1 year | model finding |
| Ukrainian war support | "fight until victory" 73% (2022) → 24% (Jul 2025) | rally, then decline | Gallup, Aug 2025 |

Benchmarks of pace from other wars, used for the advance curve and checked on the fictional map:
Dupuy's division-level advance rates (WW2 opposed attacks: 1.8 km/day in the West, 4.5 in the East;
4–11 km/day once through the line) and the CSIS comparison of 2024–25 Russian advances (15–70 m/day)
with the Somme (80 m/day). Surprise follows Dupuy's QJM (x2.24 complete, x1.10 minor, over three days).

Known deviations:
- **2025 split by region.** The model's gains go mostly to Zaporizhzhia and Donetsk, while DeepState
  had about two-thirds in Donetsk. The planner weighs provinces by economic value and has no notion of
  Russia's political priority on Donetsk.
- **The 2022 opening reaches about 40% of ISW's figure.** Oblast-sized provinces fall in sequence
  (left-bank Kherson before Melitopol), while 2022's columns ran down roads through seven oblasts at
  once; much of that ground was thin road control given up in April.
- **The 2022 replay is too slow.** ~75,000 km2 after a year (real, after the autumn counteroffensives:
  ~120,000); ~99,000 after four (real: ~116,000).

## Curated wartime posture

| File | What | Basis |
|---|---|---|
| `curated/fortifications.json` | Lines dug before a war: the 2015–22 Donbas line, the 2026 Ukrainian front, the Korean DMZ, the India–Pakistan LoC, the LAC, Israel's northern borders, the Baltic Defence Line and Poland's East Shield (under construction) | each entry states its basis; levels are judgement |
| `curated/force_posture.json` | Drone saturation at the start date (Ukraine and Russia; every belligerent then adapts in war), wartime mobilisation status, Belarus hosting Russia's 2022 offensive, active share of GFP equipment counts (IISS: Russia 3,417 battle-ready tanks of 12,420 in 2022; ~1,750 of 5,630 in 2025) | IISS via Kyiv Post, SCMP, Rubryka; Kyiv Post, Swissinfo for drones |
| `curated/personnel.json` | Ground-force share of active personnel (45 countries, Russia and Ukraine per start date) and organised wartime reserves with call-up times (Finland 280,000 wartime strength; Israel ~360,000 in October 2023; Estonia 43,700) | IISS Military Balance via snl.no, Forces News, EUAA; national MoDs; every entry lists sources and uncertain fields |
| `curated/leadership.json` | Leaders whose wartime conduct shows they will not submit (Zelensky, Putin) | documented conduct; 2021 entries use hindsight |

## Sources and how much to trust them

| Data | Source | As of | Confidence |
|---|---|---|---|
| Personnel, equipment counts, budgets, logistics (ports, rail, roads), oil, geography | Global Firepower 2026 / 2022 editions | Jan 2026 / compiled 2021 | medium |
| Cross-check and repair of the 2026 values, regions | Global Firepower 2025 edition (user-supplied CSV, identity verified against the published 2025 ranking) | Jan 2025 | medium |
| Regime type, political violence, territorial control, fiscal capacity | V-Dem v16 (academic, expert-coded) | 2025 / 2020 | high |
| Military expenditure (cross-check, 2021 only) | SIPRI Milex, constant 2019 USD | 2020 | high |
| Nuclear warheads (stockpile, deployed) | SIPRI Yearbook 2026 / 2021 | Jan 2026 / Jan 2021 | high |
| Nuclear doctrine and delivery systems | national declaratory policy (curated) | — | medium |
| Willingness to fight (used as patriotism) | Gallup International End of Year 2023 | Oct–Dec 2023 | medium/low |
| Missile defence deployments and Pk | official announcements and press (curated) | Jan 2026 / Jan 2021 | low |
| Defensive pacts | treaty texts | Jan 2026 / Jan 2021 | high |
| Province geography, rivers, cities, ports, airports | Natural Earth 5.1.2 | 2022 release | high |
| Power plants | WRI Global Power Plant Database 1.3 | ~2020 | high |
| Front line in Ukraine | DeepStateMap.Live via cyterat/deepstate-map-data | 1 Jan 2026 | high |
| 2015–2022 Donbas line, other de facto control, naval bases | curated | — | low–medium |
| Relations (−1..1) | curated judgement | Jan 2026 / Jan 2021 | medium |

Cross-checks that pass in the test suite:
- Per-country SIPRI 2026 stockpiles sum exactly to SIPRI's published **9,745**; 2021 inventories to **13,080**.
- GFP 2022 budgets agree with SIPRI 2020 spending within 2x for **93%** of the 121 countries both cover
  (GFP is much higher for Russia, 2.3x, consistent with a PPP-style estimate).
- NATO grows from 30 to 32 between snapshots (Finland, Sweden).

## Problems found in upstream data, and what was done

| Problem | Handling |
|---|---|
| GFP 2022 copy: "Fighters/Interceptors" column is a duplicate of total aircraft for every country | Column discarded; 2021 fighters estimated from each country's 2026 fighter share (labelled `model`) |
| GFP 2022: Sri Lanka listed with 2,022 aircraft carriers (spreadsheet date-parse) | Rejected by plausibility bounds; null with a note |
| GFP 2026 "armored vehicles" now counts every military vehicle (US 409,660 vs 45,193) | Kept as `military_vehicles`; `armored_vehicles` estimated from the 2022 count x tank trend (labelled `model`) |
| GFP naval tonnage of 0 means "not reported" | Stored as null |
| GFP aircraft sub-categories overlap (multirole jets counted twice) | Documented; the engine never sums sub-categories |
| Reserves reported as 0 in one edition and large in the other (Germany 15,000 vs 860,000) | Kept, with an automatic "changes sharply between editions" note on the country |
| GFP 2022 already shows Afghanistan after the August 2021 collapse | Noted on the record |
| GFP 2026 budgets far from SIPRI's audited level where the 2025 edition agrees (Angola $31.2bn vs $1.1bn; also Mongolia, Nicaragua, Kosovo, Latvia, C.A.R., Ivory Coast, Ghana, Kyrgyzstan) | 2025 value used, with a note. Lithuania was caught by the same rule but its 2026 figure is real (EUR 4.79bn approved): restored by a cited override |
| Values missing from the 2026 scrape | Filled from the 2025 edition (labelled `gfp2025`), never for estimated or redefined fields |
| Sharp one-year changes between the 2025 and 2026 editions (e.g. Myanmar MLRS 180 -> 1,520; US SP artillery 671 -> 1,521; Netherlands F-35s moved from "fighter" to "attack") | Kept as published, with a note naming both values |

## Derived values (computed by the engine, not data)

Formulas live in `src/wargame/data/profile.py` with their rationale: stability (from V-Dem state
capacity), regime type (from V-Dem Regimes of the World), equipment quality (spending per soldier),
combat weights per equipment type, supply consumption/production (force size, oil self-sufficiency,
economy size), mobilisable manpower, seaborne import share.

## Known gaps, in priority order

1. **Patriotism coverage.** Only 14 countries have their own Gallup value; the rest use the published
   regional (EU 32%, Middle East 73%, West Asia 77%) or global (50%) figure. Twelve further values
   found in search results were excluded because they matched the 2015 survey instead (listed in
   `willingness_to_fight.json`). The primary Gallup PDF and World Values Survey wave 7 would cover ~90 countries.
2. **Equipment quality is a proxy.** No per-system data (T-90M vs T-62, F-35 vs MiG-21). IISS Military Balance
   has this but is paywalled.
3. **Drones and loitering munitions are not counted by any source used here**, a major gap for the 2026
   snapshot (Russia–Ukraine). Needs a curated file.
4. **Trade dependence** (sanctions exposure) is a flat default; World Bank trade-to-GDP would fix it.
5. **Military expenditure for 2021–2025** from SIPRI (only 2020 is vendored).
6. **Reserves/paramilitaries** are inconsistently defined across GFP editions (see notes). Ground shares
   and organised reserves are curated for 45 countries (`curated/personnel.json`) from search-result
   snippets quoting the IISS Military Balance; each entry lists the fields its researcher flagged as
   uncertain (e.g. Taiwan's reserve readiness, Russia's 2021 BARS reserve, Syria's post-2024 army).

Most of these are reachable sources that the build environment's network policy blocked
(Wikipedia, World Bank, SIPRI, OWID, Gallup, IISS); only GitHub was reachable. Allowing those hosts
would let the next pass replace estimates with data.

## Licensing

V-Dem is CC BY-SA 4.0; OWID packaging is CC BY 4.0; SIPRI data is free with attribution. Natural Earth
is public domain; the WRI power plant database is CC BY 4.0. The DeepState archive repository is GPL-3.0
and DeepStateMap's own terms apply to the underlying data. Global
Firepower content is © GlobalFirePower.com and the scrape repositories carry no licence: factual counts
with attribution are used here, but **confirm terms before any commercial release**.
