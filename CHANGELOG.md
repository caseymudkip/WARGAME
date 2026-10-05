# Changelog

## 0.1.0a1 — Alpha V1 (working title: WARGAME)

The first playable build. You set up a modern war, then watch it play out; the only thing you
control once it starts is time.

### Play
- `wargame` opens the spectator app in your browser (a local server; nothing leaves your machine).
- Twelve real-world flashpoints:
  - Russia–Ukraine from 2022 and from 2026;
  - China–Taiwan, with and without the US;
  - North Korea;
  - Russia–Estonia under Article 5;
  - Kashmir, Syunik, Eritrea and the China–India border;
  - US regime change in Venezuela;
  - Israel coercing Iran.
- Or a custom war between any two of 145 countries, choosing:
  - the war goal (border skirmish, conquest, regime change, total capitulation, coercion);
  - the escalation tier (vacuum, proxy war, unrestricted);
  - whether nuclear weapons are enabled;
  - each side's temper;
  - the provinces claimed, by clicking the map.
- The live map shows:
  - each side's territory, occupied ground and ground being taken;
  - offensives as arrows, landings dashed;
  - nuclear strikes and the war's goals.
- Panels track each belligerent's losses, strength, posture, air superiority, drones, resolve and how
  close its government is to collapse. A war diary narrates the war, and the peace terms appear at the end.
- Playback from paused to a month per second (space pauses, 1–5 pick a speed).
- `wargame run <flashpoint>` runs a war without the viewer and prints how it went.
- A browser edition (`tools/web/build_site.py`): the flashpoints recorded day by day and replayed in the
  same viewer, with a timeline, so they can be watched without installing anything. With `--pyodide`
  it fights custom wars as well, running the engine in the browser (Python on WebAssembly, in a worker).

### Engine
- A 3,604-province world map with the real fronts of 1 January 2026, rivers, coasts, sea crossings,
  terrain, cities, ports, airfields and power plants.
- Real data for 140–145 countries at two start dates, every value sourced, with ground-force shares and
  organised wartime reserves for 45 of them (reserve armies such as Finland and Israel mobilise in days).
- Land warfare covers fronts, massed assaults, depth × frontage advance (Dupuy) and force-to-space,
  plus:
  - fieldworks and drones;
  - surprise and operational reach;
  - encirclement and rivers;
  - mobilisation, attrition and refurbishment.
- Amphibious war: sea lift, lodgements and blue-water power projection.
- An air war:
  - superiority against aircraft and SAM networks;
  - ground support;
  - strategic strikes, repair and coercion.
- A strategy layer: offensives, counteroffensives, withdrawals, opening campaigns and defenders' war aims.
- Diplomacy and escalation:
  - lend-lease coalitions, alliances, patron intervention and opportunists;
  - nuclear hesitation, MAD and BMD;
  - governments in exile;
  - armistices (180 days without ground fighting; air raids alone don't keep a war going, except a
    war of coercion) and peace treaties.
- Calibrated against the war in Ukraine (2022 and 2025) and NATO's 1999 air campaign; smoke-tested
  on twelve flashpoints.

### Known limitations
- No fleet battles, conventional missiles or occupation/partisans yet.
- The 2022 opening reaches about 40% of the real March 2022 gain (oblast-sized provinces), and the 2022 replay holds ~99,000 km² after four years (real 2025: ~116,000).
- Reservists fight like regulars once called up (Taiwan's reserve readiness is widely doubted).
- Some invasions never start, and the war freezes into an armistice after 180 days. North Korea's army
  has about a tenth of the combat power holding the DMZ against it. China lacks the naval superiority to
  land on Taiwan once the US Navy is in. Hopeless attacks are never ordered, and defenders don't
  counter-invade (as the UN did in 1950), so nobody moves.
- Runs from a source checkout (`pip install -e .`); the data files live in `data/`.
