// ── Context Card View ────────────────────────────────────────────────────
(function() {
  let currentContextPath = null;
  let currentContextData = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  const ICON_FILE = '<svg class="li-ico" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>';
  const ICON_CHEVRON = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';

  function baseName(p) {
    if (!p) return p;
    return p.split('/').pop().replace(/\.md$/i, '');
  }
  function folderTag(p) {
    const i = String(p || '').lastIndexOf('/');
    return i > 0 ? p.slice(0, i).split('/').pop() : '';
  }
  function relTime(unixSec) {
    const s = Math.max(0, Date.now() / 1000 - unixSec);
    const day = Math.floor(s / 86400);
    if (day >= 365) return Math.floor(day / 365) + 'y ago';
    if (day >= 30) return Math.floor(day / 30) + 'mo ago';
    if (day >= 1) return day + 'd ago';
    const h = Math.floor(s / 3600);
    if (h >= 1) return h + 'h ago';
    const m = Math.floor(s / 60);
    if (m >= 1) return m + 'm ago';
    return 'just now';
  }

  function makeSection(id, title, count, bodyHtml) {
    const el = document.getElementById(id);
    if (!el) return;
    if (count === 0 && !bodyHtml) { el.style.display = 'none'; return; }
    el.style.display = '';
    const label = escapeHtml(title) + (count != null ? ' <span class="sec-count">· ' + count + '</span>' : '');
    el.innerHTML = '<div class="section-header"><span class="sec-label">' + label + '</span>'
      + '<span class="sec-chevron">' + ICON_CHEVRON + '</span></div>'
      + '<div class="section-body">' + bodyHtml + '</div>';
    el.classList.add('collapsed-section');
    // toggle handled by delegated listener on #panel-context
  }

  function renderChips(fm) {
    if (!fm) return '';
    let html = '';
    if (fm.type != null) html += '<span class="chip chip-primary">' + escapeHtml(String(fm.type)) + '</span>';
    if (fm.status != null) html += '<span class="chip">' + escapeHtml(String(fm.status)) + '</span>';
    return html;
  }

  function renderFrontmatter(fm) {
    if (!fm || Object.keys(fm).length === 0) return '';
    let rows = '';
    for (const [k, v] of Object.entries(fm)) {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
      rows += '<tr><td>' + escapeHtml(k) + '</td><td>' + escapeHtml(val) + '</td></tr>';
    }
    return '<table class="fm-table">' + rows + '</table>';
  }

  function renderTags(tags) {
    if (!tags || Object.keys(tags).length === 0) return '';
    const chips = Object.values(tags).flat()
      .map(v => '<span class="tag-pill">' + escapeHtml(v) + '</span>').join('');
    return '<div class="tag-row">' + chips + '</div>';
  }

  function renderBacklinks(backlinks) {
    if (!backlinks || backlinks.length === 0) return '';
    return backlinks.map(bl => {
      const title = bl.link_text || baseName(bl.source_path);
      const folder = folderTag(bl.source_path);
      return '<div class="link-item" data-path="' + escapeHtml(bl.source_path) + '">'
        + ICON_FILE
        + '<span class="li-title">' + escapeHtml(title) + '</span>'
        + (folder ? '<span class="li-tag">' + escapeHtml(folder) + '</span>' : '')
        + '</div>';
    }).join('');
  }

  function renderOutlinks(outlinks) {
    if (!outlinks || outlinks.length === 0) return '';
    return outlinks.map(ol => {
      const missing = ol.exists === false;
      return '<div class="link-item" data-path="' + escapeHtml(ol.target_path) + '">'
        + '<span class="li-dot' + (missing ? ' li-dot--warn' : '') + '"></span>'
        + '<span class="li-title' + (missing ? ' li-title--muted' : '') + '">' + escapeHtml(baseName(ol.target_path)) + '</span>'
        + (missing ? '<span class="li-missing">missing</span>' : '')
        + '</div>';
    }).join('');
  }

  function renderSimilar(similar) {
    if (!similar || similar.length === 0) return '';
    return similar.map(s => {
      const pct = Math.max(0, Math.min(100, Math.round((s.score || 0) * 100)));
      const score = (s.score || 0).toFixed(2).replace(/^0/, '');
      return '<div class="sim-item" data-path="' + escapeHtml(s.path) + '">'
        + '<div class="sim-row"><span class="li-title">' + escapeHtml(s.title || baseName(s.path)) + '</span>'
        + '<span class="sim-score">' + score + '</span></div>'
        + '<div class="sim-bar"><div class="sim-bar-fill" style="width:' + pct + '%"></div></div>'
        + '</div>';
    }).join('');
  }

  function renderPeers(peers) {
    if (!peers || peers.length === 0) return '';
    return peers.map(p =>
      '<div class="link-item" data-path="' + escapeHtml(p) + '">'
      + ICON_FILE
      + '<span class="li-title">' + escapeHtml(baseName(p)) + '</span>'
      + '<span class="li-tag">' + escapeHtml(folderTag(p)) + '</span>'
      + '</div>'
    ).join('');
  }

  async function loadContext(path) {
    const placeholder = document.getElementById('context-placeholder');
    const card = document.getElementById('context-card');
    if (!path) { placeholder.style.display = ''; card.style.display = 'none'; return; }
    placeholder.style.display = 'none';
    card.style.display = '';

    try {
      const result = await app.callServerTool({ name: 'app___vault_context', arguments: { path } });
      const data = parseToolResult(result);
      if (!data) { placeholder.style.display = ''; card.style.display = 'none'; return; }
      currentContextPath = path;
      currentContextData = data;
      currentPath = path; // sync shared state

      document.getElementById('ctx-title').textContent = data.title || baseName(path);
      document.getElementById('ctx-breadcrumb').textContent =
        data.folder ? data.folder.split('/').join(' / ') : '';
      document.getElementById('ctx-modified').textContent =
        data.modified_at ? relTime(data.modified_at) : '';
      document.getElementById('ctx-chips').innerHTML = renderChips(data.frontmatter);

      const fmCount = data.frontmatter ? Object.keys(data.frontmatter).length : 0;
      makeSection('ctx-frontmatter', 'Frontmatter', fmCount, renderFrontmatter(data.frontmatter));
      const tagCount = data.tags ? Object.values(data.tags).reduce((a, v) => a + v.length, 0) : 0;
      makeSection('ctx-tags', 'Tags', tagCount, renderTags(data.tags));
      makeSection('ctx-backlinks', 'Backlinks', (data.backlinks || []).length, renderBacklinks(data.backlinks));
      makeSection('ctx-outlinks', 'Outlinks', (data.outlinks || []).length, renderOutlinks(data.outlinks));
      makeSection('ctx-similar', 'Similar notes', (data.similar || []).length, renderSimilar(data.similar));
      makeSection('ctx-peers', 'Folder peers', (data.folder_notes || []).length, renderPeers(data.folder_notes));

      // Auto-expand sections with content
      for (const id of ['ctx-backlinks', 'ctx-outlinks']) {
        const el = document.getElementById(id);
        if (el && el.style.display !== 'none') el.classList.remove('collapsed-section');
      }

      window.updateContext('context card', path, data.title,
        'Backlinks: ' + (data.backlinks || []).length + ', Outlinks: ' + (data.outlinks || []).length);
    } catch (err) {
      placeholder.style.display = '';
      placeholder.textContent = 'Error loading context: ' + (err.message || String(err));
      card.style.display = 'none';
    }
  }

  // Delegated handler: section-header toggle + row navigation
  document.getElementById('panel-context').addEventListener('click', (e) => {
    const header = e.target.closest('.section-header');
    if (header) { header.closest('.context-section')?.classList.toggle('collapsed-section'); return; }
    const item = e.target.closest('[data-path]');
    if (item && item.dataset.path) loadContext(item.dataset.path);
  });

  // Copy vault link
  document.getElementById('ctx-copy-btn').addEventListener('click', async () => {
    if (!currentContextPath) return;
    const label = document.getElementById('ctx-copy-label');
    try {
      await navigator.clipboard.writeText(currentContextPath);
      if (label) { label.textContent = 'Copied ✓'; setTimeout(() => { label.textContent = 'Copy link'; }, 1500); }
    } catch (err) {
      window.showToast('Copy failed — ' + currentContextPath);
    }
  });

  // Show in Graph
  document.getElementById('ctx-graph-btn').addEventListener('click', () => {
    if (currentContextPath) window.navigateTo('graph', { path: currentContextPath });
  });

  // Open in Browser
  document.getElementById('ctx-browse-btn').addEventListener('click', () => {
    if (currentContextPath) window.navigateTo('browse', { path: currentContextPath });
  });

  // Send to Claude
  document.getElementById('ctx-send-btn').addEventListener('click', () => {
    if (!currentContextData) return;
    const d = currentContextData;
    const lines = [
      'Context for: ' + d.title + ' (' + d.path + ')',
      'Backlinks: ' + (d.backlinks || []).length,
      'Outlinks: ' + (d.outlinks || []).length,
      'Similar: ' + (d.similar || []).length,
    ];
    if (d.backlinks && d.backlinks.length > 0) {
      lines.push('Top backlinks: ' + d.backlinks.slice(0, 5).map(b => b.source_path).join(', '));
    }
    if (d.similar && d.similar.length > 0) {
      lines.push('Top similar: ' + d.similar.slice(0, 3).map(s => s.title || s.path).join(', '));
    }
    window.sendToLLM(d.path, lines.join('\n'));
  });

  // Listen for navigation events
  window.addEventListener('vault-navigate', (e) => {
    if (e.detail.view === 'context' && e.detail.path) {
      loadContext(e.detail.path);
    }
  });

  // Sync to shared currentPath when switching to this tab
  window.addEventListener('vault-tab-changed', (e) => {
    if (e.detail.tab === 'context' && currentPath && currentPath !== currentContextPath) {
      loadContext(currentPath);
    }
  });

  // Expose for cross-view use
  window.loadContext = loadContext;
})();
