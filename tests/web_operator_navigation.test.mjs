import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { installAgenda } from "../web/agenda.mjs";

const panelSource = readFileSync(new URL("../web/operator-panel.js", import.meta.url), "utf8");
const shellSource = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");

function fakeNode(id = "") {
  const classes = new Set(id === "chatView" ? [] : ["hidden"]);
  const handlers = new Map();
  const children = [];
  return {
    id, handlers, children, value: "", textContent: "", dataset: {},
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const enabled = force ?? !classes.has(name);
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
    addEventListener(type, callback) { handlers.set(type, callback); },
    replaceChildren(...nodes) { children.splice(0, children.length, ...nodes); },
    append(...nodes) { children.push(...nodes); },
    appendChild(node) { children.push(node); },
  };
}

test("Agenda is a valid native JavaScript module and renders independently", async () => {
  const originalDocument = globalThis.document;
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, fakeNode(id));
    return elements.get(id);
  };
  globalThis.document = {
    getElementById: get,
    createElement: (tag) => fakeNode(tag),
  };
  try {
    const ui = installAgenda({
      operatorApi: async (path) => {
        assert.equal(path, "/v1/operator/agenda");
        return { items: [] };
      },
      humanError: (error) => String(error),
    });
    await ui.refresh();
    assert.match(get("agendaTimezone").textContent, /Fuso do aparelho/);
    assert.match(get("agendaList").children[0].textContent, /Nenhuma agenda/);
    assert.ok(get("agendaCreateForm").handlers.has("submit"));
    assert.ok(get("agendaRefresh").handlers.has("click"));
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test("Chat, Pessoas and Console remain navigable even if Agenda module cannot load", async () => {
  assert.doesNotMatch(panelSource, /^import\s+\{\s*installAgenda\s*\}/m);
  assert.match(panelSource, /import\("\.\/agenda\.mjs\?v=/);
  assert.match(shellSource, /operator-panel\.js\?v=20261116-2/);

  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, fakeNode(id));
    return elements.get(id);
  };
  const tabNames = ["chatView", "peopleView", "agendaView", "consoleView"];
  const tabs = tabNames.map((name) => {
    const button = fakeNode("tab-" + name);
    button.dataset.surface = name;
    return button;
  });
  const doc = {
    getElementById: get,
    querySelectorAll: (query) => query === ".surface-tab" ? tabs : [],
  };
  class MockOperatorState {
    get() { return "test-operator-token"; }
    clear() {}
  }
  const executable = panelSource.replace(
    /^import\s+\{\s*OperatorState\s*\}\s+from\s+["'][^"']+["'];\s*\n/, "",
  );
  assert.notEqual(executable, panelSource, "test must remove only the known static import");
  const sandbox = {
    document: doc, localStorage: {}, OperatorState: MockOperatorState,
    Headers, fetch: () => new Promise(() => {}), clearInterval() {},
    setInterval() { throw new Error("no refresh timer expected in this navigation test"); },
    console, URL, Intl, Date,
  };
  runInNewContext(executable, sandbox, { filename: "operator-panel.js" });

  function click(name) {
    const button = tabs.find((node) => node.dataset.surface === name);
    button.handlers.get("click")();
    for (const view of tabNames) {
      assert.equal(get(view).classList.contains("hidden"), view !== name, view);
    }
    assert.equal(button.classList.contains("active"), true);
  }
  assert.equal(get("chatView").classList.contains("hidden"), false);
  click("peopleView");
  click("consoleView");
  click("agendaView"); // vm deliberately has no import callback; loading fails in isolation
  click("chatView");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(get("chatView").classList.contains("hidden"), false);
  assert.equal(get("peopleView").classList.contains("hidden"), true);
  assert.equal(get("consoleView").classList.contains("hidden"), true);
});
