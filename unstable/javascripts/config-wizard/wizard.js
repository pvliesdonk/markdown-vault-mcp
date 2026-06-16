import {
  buildEnvMap, isVisible, generateDotenv, generateClaudeJson,
  generateDockerRun, generateCompose, generateSystemd,
} from "./generators.js";

const MOUNT_ID = "cfg-wizard";
const answers = {};

function readUrlState(spec) {
  const hash = new URLSearchParams(location.hash.slice(1));
  const secrets = new Set(spec.secretKeys);
  for (const q of spec.questions) {
    if (secrets.has(q.var)) continue; // never hydrate secrets from URL
    const v = hash.get(q.id);
    if (v !== null) answers[q.id] = v;
  }
}

function writeUrlState(spec) {
  const secrets = new Set(spec.secretKeys);
  const params = new URLSearchParams();
  for (const q of spec.questions) {
    if (secrets.has(q.var)) continue;
    if (answers[q.id] !== undefined && answers[q.id] !== "") params.set(q.id, answers[q.id]);
  }
  const qs = params.toString();
  history.replaceState(null, "", qs ? `#${qs}` : location.pathname + location.search);
}

function field(q, secrets) {
  const wrap = document.createElement("label");
  wrap.className = "cfg-field";
  wrap.dataset.qid = q.id;
  const title = document.createElement("span");
  title.className = "cfg-label";
  title.textContent = q.label;
  wrap.appendChild(title);
  if (q.help) {
    const help = document.createElement("small");
    help.className = "cfg-help";
    help.textContent = q.help;
    wrap.appendChild(help);
  }
  let input;
  if (q.type === "select") {
    input = document.createElement("select");
    for (const opt of q.options) {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.label;
      input.appendChild(o);
    }
  } else if (q.type === "bool") {
    input = document.createElement("select");
    for (const v of ["", "true", "false"]) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = v || "(default)";
      input.appendChild(o);
    }
  } else {
    input = document.createElement("input");
    input.type = q.type === "number" ? "number" : "text";
  }
  if (secrets.has(q.var)) wrap.classList.add("cfg-secret");
  if (answers[q.id] !== undefined) input.value = answers[q.id];
  // One listener per control: selects/bools settle on "change"; text/number
  // fields update live on "input". Avoids the double render + double
  // history.replaceState that "input"+"change" both firing on <select> causes.
  const evt = q.type === "select" || q.type === "bool" ? "change" : "input";
  input.addEventListener(evt, () => {
    answers[q.id] = input.value;
    render();
  });
  wrap.appendChild(input);
  return wrap;
}

let SPEC = null;
let ROOT = null;

function render() {
  const spec = SPEC;
  const secrets = new Set(spec.secretKeys);
  // Capture focus + caret so a full re-render (replaceChildren) doesn't drop
  // the user mid-keystroke in a text field.
  const active = document.activeElement;
  const activeQid =
    active && active.closest ? active.closest(".cfg-field")?.dataset.qid : null;
  let selStart = null;
  let selEnd = null;
  try {
    selStart = active.selectionStart;
    selEnd = active.selectionEnd;
  } catch {
    // selects / number inputs do not expose a text selection
  }
  const core = document.createElement("div");
  core.className = "cfg-core";
  const advanced = {};
  for (const q of spec.questions) {
    if (!isVisible(q, answers)) continue;
    const el = field(q, secrets);
    if (q.advancedGroup) {
      (advanced[q.advancedGroup] ??= []).push(el);
    } else {
      core.appendChild(el);
    }
  }
  const drawer = document.createElement("details");
  drawer.className = "cfg-advanced";
  const sum = document.createElement("summary");
  sum.textContent = "Advanced tuning";
  drawer.appendChild(sum);
  for (const [group, els] of Object.entries(advanced)) {
    const h = document.createElement("h4");
    h.textContent = group;
    drawer.appendChild(h);
    els.forEach((e) => drawer.appendChild(e));
  }

  const map = buildEnvMap(spec, answers);
  const hostVault = map["MARKDOWN_VAULT_MCP_SOURCE_DIR"];
  const local = answers.deployment !== "server";
  const tabs = local
    ? [["Claude config", generateClaudeJson(map)], [".env", generateDotenv(map)]]
    : [
        ["docker run", generateDockerRun(map, hostVault)],
        ["compose", generateCompose(map, hostVault)],
        ["systemd", generateSystemd(map)],
        [".env", generateDotenv(map)],
      ];
  const out = document.createElement("div");
  out.className = "cfg-output";
  for (const [name, text] of tabs) {
    const block = document.createElement("div");
    block.className = "cfg-tab";
    const head = document.createElement("div");
    head.className = "cfg-tab-head";
    head.textContent = name;
    const pre = document.createElement("pre");
    pre.className = "cfg-pre";
    pre.textContent = text;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "cfg-copy";
    copy.textContent = "Copy";
    copy.addEventListener("click", () => {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).catch(() => {});
      } else {
        // Fallback for non-secure contexts: select the block so Ctrl/Cmd-C works.
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
    });
    head.appendChild(copy);
    block.appendChild(head);
    block.appendChild(pre);
    out.appendChild(block);
  }

  const warnings = document.createElement("div");
  warnings.className = "cfg-warnings";
  for (const g of spec.guards) {
    if (Object.entries(g.when).every(([k, vals]) => vals.includes(answers[k]))) {
      const w = document.createElement("div");
      w.className = `cfg-${g.level}`;
      w.textContent = g.message;
      warnings.appendChild(w);
    }
  }

  ROOT.replaceChildren(core, drawer, warnings, out);
  writeUrlState(spec);

  // Restore focus + caret to the field the user was editing.
  if (activeQid) {
    const restored = ROOT.querySelector(
      `.cfg-field[data-qid="${activeQid}"] input, .cfg-field[data-qid="${activeQid}"] select`,
    );
    if (restored) {
      restored.focus();
      if (selStart !== null && restored.tagName === "INPUT" && restored.type !== "number") {
        try {
          restored.setSelectionRange(selStart, selEnd);
        } catch {
          // input type does not support text selection
        }
      }
    }
  }
}

async function init() {
  ROOT = document.getElementById(MOUNT_ID);
  if (!ROOT) return;
  const specUrl = ROOT.dataset.specUrl;
  try {
    const resp = await fetch(specUrl);
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    SPEC = await resp.json();
  } catch (e) {
    ROOT.textContent = `Failed to load the configuration generator: ${e.message}`;
    return;
  }
  readUrlState(SPEC);
  render();
}

if (document.readyState !== "loading") init();
else document.addEventListener("DOMContentLoaded", init);
