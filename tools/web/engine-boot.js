"use strict";
// Starts the WARGAME engine under Pyodide (Python compiled to WebAssembly) and returns its LiveWar class.
// Loaded after pyodide/pyodide.js, by engine-worker.js or, where workers can't run it, by the page itself.
// The page's host serves no archives, so Python's standard library and the engine (its source and the data
// it reads) come as JSON bundles of text files, {path: text}, written into Pyodide's file system.

const STDLIB_DIR = "/lib/python3.12";        // Pyodide 0.27.7 is Python 3.12.
const ENGINE_DIR = "/home/pyodide/wargame";  // Laid out as in the repository, so the engine finds data/.

async function bootWargame(base, progress) {
  const bundle = async (path) => {
    const res = await fetch(base + path);
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
  };
  const unpack = (FS, root, files) => {
    for (const [path, text] of Object.entries(files)) {
      const file = `${root}/${path}`;
      FS.mkdirTree(file.slice(0, file.lastIndexOf("/")));
      FS.writeFile(file, text);
    }
  };
  progress("Loading Python and the engine (up to 25 MB the first time)…");
  const [stdlib, engine] = await Promise.all([bundle("pyodide/python_stdlib.json"), bundle("engine.json")]);
  const py = await loadPyodide({
    indexURL: base + "pyodide/",
    // Pyodide mounts this as python312.zip; a file that isn't a zip is passed over on import, and the
    // library is already in place as plain files by then.
    stdLibURL: base + "pyodide/python_stdlib.txt",
    fsInit: (FS) => unpack(FS, STDLIB_DIR, stdlib),
  });
  progress("Starting the engine…");
  unpack(py.FS, ENGINE_DIR, engine);
  py.runPython(`import sys\nsys.path.insert(0, "${ENGINE_DIR}/src")\nfrom wargame.app.live import LiveWar`);
  return py.globals.get("LiveWar");
}
