"use strict";
// WARGAME engine in a Web Worker: Python (Pyodide, WebAssembly) running wargame.app.live.LiveWar.
// Messages: {id, cmd: "boot" | "start" | "advance", arg} -> {type: "reply", id, result | error}; {type: "progress", message}.

const base = new URL(".", self.location.href).href;
importScripts(base + "pyodide/pyodide.js", base + "engine-boot.js");

let LiveWar = null;
let war = null;

function progress(message) { self.postMessage({ type: "progress", message }); }

self.onmessage = async (e) => {
  const { id, cmd, arg } = e.data;
  try {
    let result = null;
    if (cmd === "boot") {
      if (!LiveWar) LiveWar = await bootWargame(base, progress);
    } else if (cmd === "start") {
      progress("Building the world and mobilising the armies…");
      if (war) war.destroy();
      war = LiveWar(arg);
      result = war.header();
    } else if (cmd === "advance") {
      result = war.advance(arg);
    }
    self.postMessage({ type: "reply", id, result });
  } catch (err) {
    self.postMessage({ type: "reply", id, error: String(err && err.message ? err.message : err) });
  }
};
