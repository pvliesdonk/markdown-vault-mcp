// ── Note Preview View ────────────────────────────────────────────────────
(function() {
  function escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const ICON_LIST = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></svg>';
  const ICON_COPY = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
  const ICON_LINK = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>';
  const CHEVRON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';

  const PROPS_SHOWN = 3;
  const TAGS_SHOWN = 4;

  let currentPreviewPath = null;
  let currentPreviewData = null;

  const previewEl = document.getElementById('browser-preview');

  // Strip a leading YAML frontmatter block so it isn't rendered twice: the
  // frontmatter already shows in the properties panel, and vault_read's
  // `content` is the whole file including frontmatter. The server's parser
  // (python-frontmatter) fences on `^-{3,}\s*$`; this deliberately narrower
  // `-{3,}[ \t]*` covers the realistic cases (`----` and `--- ` fences) so
  // they strip too, while never over-stripping real body content.
  function stripFrontmatter(text) {
    return String(text || '').replace(/^﻿?-{3,}[ \t]*\r?\n[\s\S]*?\r?\n-{3,}[ \t]*\r?\n?/, '');
  }

  function normalizeTags(fm) {
    const t = fm && fm.tags;
    if (Array.isArray(t)) return t.map(String);
    if (typeof t === 'string') return t.split(/[,\s]+/).filter(Boolean);
    return [];
  }

  function propEntries(fm) {
    if (!fm) return [];
    return Object.entries(fm).filter(([k]) => k !== 'tags');
  }

  function renderProps(fm) {
    const entries = propEntries(fm);
    if (entries.length === 0) return '';
    const rows = entries.map(([k, v], i) => {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
      const extra = i >= PROPS_SHOWN ? ' prop-extra' : '';
      return '<div class="prop-row' + extra + '"><span class="prop-key">' + escHtml(k)
        + '</span><span class="prop-val">' + escHtml(val) + '</span></div>';
    }).join('');
    const more = entries.length - PROPS_SHOWN;
    const toggle = more > 0
      ? '<button class="prop-toggle" id="preview-props-toggle">' + CHEVRON
        + '<span id="preview-props-toggle-label">Show ' + more + ' more properties</span></button>'
      : '';
    return '<div class="preview-props collapsed" id="preview-props">' + rows + toggle + '</div>';
  }

  function renderTags(fm) {
    const tags = normalizeTags(fm);
    if (tags.length === 0) return '';
    const chips = tags.map((t, i) =>
      '<span class="tag-pill' + (i >= TAGS_SHOWN ? ' tag-extra' : '') + '">#' + escHtml(t) + '</span>'
    ).join('');
    const more = tags.length - TAGS_SHOWN;
    const toggle = more > 0
      ? '<button class="tag-toggle" id="preview-tags-toggle">+' + more + ' more</button>'
      : '';
    return '<div class="preview-tags collapsed" id="preview-tags">' + chips + toggle + '</div>';
  }

  function slugify(text, used) {
    let base = String(text).toLowerCase().trim().replace(/[^\w]+/g, '-').replace(/^-+|-+$/g, '') || 'section';
    let slug = base, n = 1;
    while (used.has(slug)) { slug = base + '-' + (++n); }
    used.add(slug);
    return slug;
  }

  // Assign ids to rendered headings and return [{label, id, level}] for the ToC.
  function buildToc(contentEl) {
    const used = new Set();
    const items = [];
    contentEl.querySelectorAll('h1, h2, h3').forEach(h => {
      const id = 'h-' + slugify(h.textContent || 'section', used);
      h.id = id;
      h.style.scrollMarginTop = '12px';
      items.push({ id, label: h.textContent || '', level: Number(h.tagName[1]) });
    });
    return items;
  }

  async function copyToClipboard(text, labelEl, doneText, restoreText) {
    try {
      await navigator.clipboard.writeText(text);
      if (labelEl) {
        labelEl.textContent = doneText;
        setTimeout(() => { labelEl.textContent = restoreText; }, 1500);
      }
    } catch (err) {
      console.warn('clipboard write failed', err);
      window.showToast('Copy failed');
    }
  }

  async function loadPreview(path, { switchToNote = true } = {}) {
    currentPreviewPath = path;
    currentPath = path; // sync shared state
    // Show the Note tab button; switch to it unless caller opts out
    document.getElementById('note-tab-btn').classList.add('visible');
    if (switchToNote) switchTab('note');
    // Highlight active note in the browser tree
    document.getElementById('browser-tree')?.querySelectorAll('.tree-note').forEach(el => {
      el.classList.toggle('active', el.dataset.path === path);
    });

    try {
      const result = await app.callServerTool({ name: 'app___vault_read', arguments: { path } });
      const data = parseToolResult(result);
      if (!data) { previewEl.innerHTML = '<div class="placeholder">Note not found</div>'; return; }
      currentPreviewData = data;

      // Rendered markdown body (frontmatter stripped so it shows only in props).
      const body = stripFrontmatter(data.content);
      let rendered;
      if (typeof marked !== 'undefined') {
        rendered = marked.parse(body);
      } else {
        rendered = '<pre>' + escHtml(body) + '</pre>';
      }
      if (typeof DOMPurify !== 'undefined') {
        rendered = DOMPurify.sanitize(rendered);
      }

      let html = '<div class="preview">';
      html += '<div class="preview-header">';
      html += '<div class="preview-head-main"><h1 class="preview-title">' + escHtml(data.title || path) + '</h1>'
        + '<div class="preview-path">' + escHtml(data.path || path) + '</div></div>';
      html += '<div class="preview-toc-wrap">'
        + '<button class="ghost-btn" id="preview-toc-btn" title="Contents">' + ICON_LIST + 'Contents</button>'
        + '<div class="preview-toc" id="preview-toc" hidden></div>'
        + '</div>';
      html += '</div>';
      html += renderProps(data.frontmatter);
      html += renderTags(data.frontmatter);
      html += '<div class="preview-content">' + rendered + '</div>';
      html += '<div class="preview-footer">';
      html += '<button class="accent-btn" id="preview-copy-md">' + ICON_COPY + '<span id="preview-copy-md-label">Copy markdown</span></button>';
      html += '<button class="ghost-btn" id="preview-copy-link">' + ICON_LINK + '<span id="preview-copy-link-label">Copy vault link</span></button>';
      html += '<button class="ghost-btn" id="preview-send-btn" title="Send to Claude">Send</button>';
      html += '<button class="ghost-btn" id="preview-browse-btn" title="Back to Browse">← Browse</button>';
      html += '<button class="ghost-btn" id="preview-ctx-btn" title="Show Context">Context</button>';
      html += '<button class="ghost-btn" id="preview-graph-btn" title="Show in Graph">Graph</button>';
      html += '<button class="edit-btn-disabled" title="Coming soon" disabled>Edit</button>';
      html += '</div></div>';

      previewEl.innerHTML = html;

      // Build the ToC from the rendered headings.
      const toc = buildToc(previewEl.querySelector('.preview-content'));
      const tocEl = document.getElementById('preview-toc');
      if (toc.length > 0) {
        tocEl.innerHTML = '<div class="toc-head">On this page</div>'
          + toc.map(it => '<a class="toc-item toc-l' + it.level + '" href="#' + it.id + '">' + escHtml(it.label) + '</a>').join('');
      } else {
        document.getElementById('preview-toc-btn').style.display = 'none';
      }

      // Contents popover
      const tocBtn = document.getElementById('preview-toc-btn');
      tocBtn?.addEventListener('click', (e) => { e.stopPropagation(); tocEl.hidden = !tocEl.hidden; });
      tocEl.addEventListener('click', (e) => {
        const link = e.target.closest('a.toc-item');
        if (link) {
          e.preventDefault();
          document.getElementById(link.getAttribute('href').slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          tocEl.hidden = true;
        }
      });

      // Collapsible properties + tags
      document.getElementById('preview-props-toggle')?.addEventListener('click', () => {
        const box = document.getElementById('preview-props');
        const open = box.classList.toggle('collapsed') === false;
        const label = document.getElementById('preview-props-toggle-label');
        if (label) label.textContent = open ? 'Show less' : ('Show ' + (propEntries(data.frontmatter).length - PROPS_SHOWN) + ' more properties');
      });
      document.getElementById('preview-tags-toggle')?.addEventListener('click', () => {
        const box = document.getElementById('preview-tags');
        const open = box.classList.toggle('collapsed') === false;
        const btn = document.getElementById('preview-tags-toggle');
        btn.textContent = open ? 'Show less' : ('+' + (normalizeTags(data.frontmatter).length - TAGS_SHOWN) + ' more');
      });

      // Copy actions
      document.getElementById('preview-copy-md')?.addEventListener('click', () => {
        copyToClipboard(data.content || '', document.getElementById('preview-copy-md-label'), 'Copied ✓', 'Copy markdown');
      });
      document.getElementById('preview-copy-link')?.addEventListener('click', () => {
        copyToClipboard(data.path || path, document.getElementById('preview-copy-link-label'), 'Copied ✓', 'Copy vault link');
      });

      // Preserved actions
      document.getElementById('preview-send-btn')?.addEventListener('click', () => {
        window.sendToLLM(data.path, data.content || '');
      });
      document.getElementById('preview-browse-btn')?.addEventListener('click', () => { switchTab('browse'); });
      document.getElementById('preview-ctx-btn')?.addEventListener('click', () => {
        window.navigateTo('context', { path: data.path });
      });
      document.getElementById('preview-graph-btn')?.addEventListener('click', () => {
        window.navigateTo('graph', { path: data.path });
      });

      window.updateContext('browser', path, data.title);
    } catch (err) {
      console.error('note preview render failed for %s', path, err);
      const errDiv = document.createElement('div');
      errDiv.className = 'placeholder';
      errDiv.textContent = 'Error: ' + (err.message || err);
      previewEl.innerHTML = '';
      previewEl.appendChild(errDiv);
    }
  }

  // Close the Contents popover on any outside click.
  document.addEventListener('click', (e) => {
    const toc = document.getElementById('preview-toc');
    if (toc && !toc.hidden && !e.target.closest('.preview-toc-wrap')) toc.hidden = true;
  });

  // Close the popover when leaving the Note tab.
  window.addEventListener('vault-tab-changed', () => {
    const toc = document.getElementById('preview-toc');
    if (toc) toc.hidden = true;
  });

  // Listen for navigation events
  window.addEventListener('vault-navigate', (e) => {
    if ((e.detail.view === 'browse' || e.detail.view === 'note') && e.detail.path) {
      // When navigating to browse with a path, load the preview but stay on browse
      const switchToNote = e.detail.view !== 'browse';
      loadPreview(e.detail.path, { switchToNote });
    }
  });

  window.loadPreview = loadPreview;
})();
