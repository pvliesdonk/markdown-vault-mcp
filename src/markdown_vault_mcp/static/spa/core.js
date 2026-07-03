
// Static import required for Android webview compatibility (dynamic import()
// fails there).  SDK is vendored inline via import map (see issue #302).
import { App, applyDocumentTheme, applyHostStyleVariables, applyHostFonts }
  from "https://unpkg.com/@modelcontextprotocol/ext-apps@1.3.1/app-with-deps";

try {

// ── Globals ──────────────────────────────────────────────────────────────
// autoResize:true (the SDK default) — the SDK observes our content size and
// reports it to the host via size-changed so the host can size the iframe. The
// app pairs this with containerDimensions-aware CSS (see applySizeMode): a
// fixed-height host frame is filled with internal scroll; a flexible/unbounded
// frame (mobile inline, sidebars) grows to content. Disabling autoResize + a
// hard height:100vh left mobile hosts unable to learn our height (#859).
const app = new App(
  { name: "Vault Explorer", version: "1.0.0" },
  {},
  { autoResize: true }
);
let currentTab = 'context';
let isFullscreen = false;
let currentPath = null; // shared active-note state across views

// ── Utilities ─────────────────────────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/** Extract parsed JSON from a CallToolResult ({content: [{type:'text', text:'...'}]}).
 *  Returns the parsed object, or null if no text content found.
 *  Throws Error with descriptive message on malformed JSON. */
function parseToolResult(result) {
  if (!result) return null;
  if (result.content && Array.isArray(result.content)) {
    const tb = result.content.find(c => c.type === 'text');
    if (tb) {
      try { return JSON.parse(tb.text); }
      catch (e) { throw new Error('Invalid JSON in tool result: ' + e.message); }
    }
    return null;
  }
  if (typeof result === 'string') {
    try { return JSON.parse(result); }
    catch (e) { throw new Error('Invalid JSON in tool result: ' + e.message); }
  }
  return null;
}

// ── Host-driven sizing ────────────────────────────────────────────────────
// The host reports containerDimensions (per-axis: a fixed `height`/`width`, or a
// `maxHeight`/`maxWidth` cap, or neither = unbounded) and safeAreaInsets. We
// derive a size mode from the height axis: a fixed height means the host owns
// our frame size and we fill it (internal scroll); anything else means the app
// controls its own height and the SDK's autoResize reports it so the host sizes
// the iframe to content. The CSS branches on html[data-size-mode]; the base
// (attribute unset) is the fixed/fill layout, so before the first context
// arrives there is no collapsed-content flash. The steady-state mode is chosen
// here from the reported height axis, independent of `platform`. Must receive
// the complete (merged) host context — see the call in handleHostContext. #859.
function applySizeMode(ctx) {
  const cd = ctx.containerDimensions;
  const fixedHeight = !!cd && typeof cd.height === 'number';
  document.documentElement.dataset.sizeMode = fixedHeight ? 'fixed' : 'flexible';
  const s = ctx.safeAreaInsets;
  const root = document.documentElement.style;
  root.setProperty('--safe-top', (s?.top || 0) + 'px');
  root.setProperty('--safe-right', (s?.right || 0) + 'px');
  root.setProperty('--safe-bottom', (s?.bottom || 0) + 'px');
  root.setProperty('--safe-left', (s?.left || 0) + 'px');
}

// ── Host theming ─────────────────────────────────────────────────────────
function handleHostContext(ctx) {
  if (!ctx) return;
  // onhostcontextchanged delivers a PARTIAL delta (only the changed fields);
  // the SDK has already merged it into getHostContext(). Derive size mode +
  // insets from the merged snapshot, not the delta, so an unrelated change
  // (e.g. a theme-only delta) can't reset them to their absent-field defaults.
  // Fall back to ctx if no snapshot is available yet.
  applySizeMode(app.getHostContext() || ctx);
  if (ctx.theme) {
    applyDocumentTheme(ctx.theme);
    window.dispatchEvent(new CustomEvent('vault-theme-changed', { detail: { theme: ctx.theme } }));
  }
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
  if (ctx.displayMode !== undefined) {
    isFullscreen = ctx.displayMode === 'fullscreen';
    fullscreenBtn.textContent = isFullscreen ? '\u2716 Exit Fullscreen' : '\u26F6 Fullscreen';
  }
}

// ── Tab navigation ───────────────────────────────────────────────────────
function switchTab(tabName) {
  currentTab = tabName;
  document.querySelectorAll('.tab-bar button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.tab === tabName);
  });
  // Emit event for views to react
  window.dispatchEvent(new CustomEvent('vault-tab-changed', { detail: { tab: tabName } }));
}

document.getElementById('tabBar').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-tab]');
  if (btn) switchTab(btn.dataset.tab);
});

// ── Cross-view navigation API ────────────────────────────────────────────
window.navigateTo = function navigateTo(view, params = {}) {
  switchTab(view);
  window.dispatchEvent(new CustomEvent('vault-navigate', {
    detail: { view, ...params }
  }));
};

// ── Display mode (fullscreen toggle) ─────────────────────────────────────
const fullscreenBtn = document.getElementById('fullscreenBtn');

function updateFullscreenButton(available) {
  fullscreenBtn.style.display = available ? 'block' : 'none';
}

fullscreenBtn.addEventListener('click', async () => {
  try {
    const mode = isFullscreen ? 'inline' : 'fullscreen';
    await app.requestDisplayMode({ mode });
  } catch (err) {
    console.warn('Display mode change failed:', err);
  }
});

// ── Toast helper ─────────────────────────────────────────────────────────
window.showToast = function showToast(message, durationMs = 2000) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), durationMs);
};

// ── Shared send-to-LLM helper ───────────────────────────────────────────
window.sendToLLM = async function sendToLLM(path, content) {
  const MAX_LEN = 4000;
  let body = content;
  if (body.length > MAX_LEN) {
    body = body.slice(0, MAX_LEN) + "\n... [truncated \u2014 use read('" + path + "') for full content]";
  }
  try {
    await app.sendMessage({
      role: 'user',
      content: { type: 'text', text: '[From Vault App] Note: ' + path + '\n\n' + body }
    });
    window.showToast('Sent to Claude');
  } catch (err) {
    window.showToast('Send failed: ' + (err.message || err));
  }
};

// ── Shared ambient context helper ────────────────────────────────────────
window.updateContext = async function updateContext(viewName, path, title, extras) {
  const lines = ['User is viewing ' + viewName + ': ' + path];
  if (title) lines.push('Title: ' + title);
  if (extras) lines.push(extras);
  try {
    await app.updateModelContext({
      content: [{ type: 'text', text: lines.join('\n') }]
    });
  } catch (err) {
    console.warn('updateModelContext failed:', err);
  }
};

// ── Expose app for views ─────────────────────────────────────────────────
window.vaultApp = app;

// ── Handler registration (before connect) ────────────────────────────────
let connected = false;
let pendingToolInput = null;

function processToolInput(args) {
  const validViews = ['context', 'graph', 'browse', 'note'];
  const view = (validViews.includes(args.view) ? args.view
    : (args.path ? 'context' : 'browse'));
  if (args.path) currentPath = args.path;
  if (view === 'note') {
    document.getElementById('note-tab-btn').classList.add('visible');
  }
  switchTab(view);
  if (args.path) {
    window.dispatchEvent(new CustomEvent('vault-navigate', {
      detail: { view, path: args.path }
    }));
  } else if (view === 'browse') {
    if (typeof window.loadBrowser === 'function') window.loadBrowser();
  }
}

app.ontoolinput = (params) => {
  const args = params?.arguments || {};
  if (connected) {
    processToolInput(args);
  } else {
    // Last-wins: only the final pre-connect input is kept. Multiple
    // ontoolinput calls before connect() are not expected in MCP semantics.
    pendingToolInput = args;
  }
};

app.ontoolresult = (result) => {
  window.dispatchEvent(new CustomEvent('vault-tool-result', { detail: result }));
};

app.ontoolcancelled = (params) => {
  console.info('Tool call cancelled:', params?.reason);
};

app.onhostcontextchanged = handleHostContext;

app.onerror = (err) => {
  console.error('MCP App error:', err);
};

app.onteardown = () => {
  return {};
};

/*@@FILE:views/context.js@@*//*@@FILE:views/graph.js@@*//*@@FILE:views/browser.js@@*//*@@FILE:views/note.js@@*/// ── Connect ──────────────────────────────────────────────────────────────
await app.connect();
connected = true;
const hostContext = app.getHostContext();
if (hostContext) handleHostContext(hostContext);

const fullscreenAvailable = hostContext?.availableDisplayModes?.includes('fullscreen') === true;
updateFullscreenButton(fullscreenAvailable);
// Auto-expand on first load — a complex multi-view app benefits from more space
// on desktop/web. NOT on mobile: there fullscreen is the whole screen with no
// working return path on some hosts (leaving it can tear the app down), and the
// content-driven inline layout is the right default (#859). platform may be
// undefined on older hosts — treat that as non-mobile so desktop is unchanged.
if (fullscreenAvailable && !isFullscreen && hostContext?.platform !== 'mobile') {
  app.requestDisplayMode({ mode: 'fullscreen' }).catch(() => {});
}

// Process any tool input received during connection handshake.
// If none arrived (host did not send ontoolinput), default to browse view so
// the app shows data without requiring the LLM to pass explicit arguments.
if (pendingToolInput) {
  processToolInput(pendingToolInput);
  pendingToolInput = null;
} else {
  switchTab('browse');
  if (typeof window.loadBrowser === 'function') window.loadBrowser();
}

console.log('Vault Explorer connected');

} catch (err) {
  console.error('Vault Explorer error:', err);
  const container = document.querySelector('.app-container');
  const el = document.createElement('div');
  el.style.cssText = 'padding:24px;text-align:center;color:var(--color-text-secondary);';
  const h = document.createElement('h2');
  h.textContent = 'Vault Explorer';
  const p1 = document.createElement('p');
  p1.textContent = 'This app requires a compatible MCP client with ext-apps SDK support.';
  const p2 = document.createElement('p');
  p2.style.cssText = 'font-size:12px;margin-top:8px;';
  p2.textContent = 'Error: ' + (err.message || err);
  el.append(h, p1, p2);
  (container || document.body).replaceChildren(el);
}
