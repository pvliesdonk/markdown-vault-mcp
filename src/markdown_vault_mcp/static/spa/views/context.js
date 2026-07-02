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

  function makeSection(id, title, count, bodyHtml) {
    const el = document.getElementById(id);
    if (!el) return;
    if (count === 0 && !bodyHtml) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.innerHTML = '<div class="section-header"><span>' + escapeHtml(title) + '</span><span class="badge">' + count + '</span></div>'
      + '<div class="section-body">' + bodyHtml + '</div>';
    el.classList.add('collapsed-section');
    // toggle handled by delegated listener on #panel-context
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
    let html = '';
    for (const [field, values] of Object.entries(tags)) {
      html += '<div class="tag-group"><div class="tag-group-label">' + escapeHtml(field) + '</div>';
      for (const v of values) {
        html += '<span class="tag-pill">' + escapeHtml(v) + '</span>';
      }
      html += '</div>';
    }
    return html;
  }

  function renderBacklinks(backlinks) {
    if (!backlinks || backlinks.length === 0) return '';
    return backlinks.map(bl =>
      '<div class="link-item" data-path="' + escapeHtml(bl.source_path) + '">'
      + '<span class="link-path">' + escapeHtml(bl.source_path) + '</span>'
      + (bl.link_text ? '<span class="link-text">' + escapeHtml(bl.link_text) + '</span>' : '')
      + '<span class="link-type-badge">' + escapeHtml(bl.link_type) + '</span>'
      + '</div>'
    ).join('');
  }

  function renderOutlinks(outlinks) {
    if (!outlinks || outlinks.length === 0) return '';
    return outlinks.map(ol =>
      '<div class="link-item" data-path="' + escapeHtml(ol.target_path) + '">'
      + '<span class="link-path">' + escapeHtml(ol.target_path) + '</span>'
      + '<span class="' + (ol.exists ? 'link-exists-yes' : 'link-exists-no') + '">' + (ol.exists ? '\u2713' : '\u2717') + '</span>'
      + '<span class="link-type-badge">' + escapeHtml(ol.link_type) + '</span>'
      + '</div>'
    ).join('');
  }

  function renderSimilar(similar) {
    if (!similar || similar.length === 0) return '';
    const maxScore = Math.max(...similar.map(s => s.score), 0.001);
    return similar.map(s =>
      '<div class="link-item" data-path="' + escapeHtml(s.path) + '">'
      + '<span class="link-path">' + escapeHtml(s.title || s.path) + '</span>'
      + '<div class="similar-score"><div class="similar-score-fill" style="width:' + Math.round((s.score / maxScore) * 100) + '%"></div></div>'
      + '</div>'
    ).join('');
  }

  function renderPeers(peers) {
    if (!peers || peers.length === 0) return '';
    return peers.map(p =>
      '<div class="link-item" data-path="' + escapeHtml(p) + '">'
      + '<span class="link-path">' + escapeHtml(p) + '</span>'
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
      const result = await app.callServerTool({ name: 'vault___vault_context', arguments: { path } });
      const data = parseToolResult(result);
      if (!data) { placeholder.style.display = ''; card.style.display = 'none'; return; }
      currentContextPath = path;
      currentContextData = data;
      currentPath = path; // sync shared state

      document.getElementById('ctx-title').textContent = data.title || path;
      document.getElementById('ctx-path').textContent = data.path;
      document.getElementById('ctx-folder').textContent = data.folder ? '\uD83D\uDCC1 ' + data.folder : '';
      document.getElementById('ctx-modified').textContent = '';
      if (data.modified_at) {
        const d = new Date(data.modified_at * 1000);
        document.getElementById('ctx-modified').textContent = d.toLocaleString();
      }

      const fmCount = data.frontmatter ? Object.keys(data.frontmatter).length : 0;
      makeSection('ctx-frontmatter', 'Frontmatter', fmCount, renderFrontmatter(data.frontmatter));
      const tagCount = data.tags ? Object.values(data.tags).reduce((a, v) => a + v.length, 0) : 0;
      makeSection('ctx-tags', 'Tags', tagCount, renderTags(data.tags));
      makeSection('ctx-backlinks', 'Backlinks', (data.backlinks || []).length, renderBacklinks(data.backlinks));
      makeSection('ctx-outlinks', 'Outlinks', (data.outlinks || []).length, renderOutlinks(data.outlinks));
      makeSection('ctx-similar', 'Similar', (data.similar || []).length, renderSimilar(data.similar));
      makeSection('ctx-peers', 'Folder Peers', (data.folder_notes || []).length, renderPeers(data.folder_notes));

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

  // Delegated handler: link-item navigation + section-header toggle
  document.getElementById('panel-context').addEventListener('click', (e) => {
    const header = e.target.closest('.section-header');
    if (header) { header.closest('.context-section')?.classList.toggle('collapsed-section'); return; }
    const item = e.target.closest('.link-item[data-path]');
    if (item) loadContext(item.dataset.path);
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
