// ── Vault Browser View ──────────────────────────────────────────────────
(function() {
  function escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  let currentPreviewPath = null;
  let currentPreviewData = null;
  let treeDataCache = {};
  let isSearchMode = false;

  const treeEl = document.getElementById('browser-tree');
  const previewEl = document.getElementById('browser-preview');
  const searchInput = document.getElementById('browser-search-input');
  const searchClear = document.getElementById('browser-search-clear');

  async function loadFolder(folder) {
    const key = folder || '__root__';
    if (treeDataCache[key]) return treeDataCache[key];
    try {
      const result = await app.callServerTool({ name: 'vault___vault_list', arguments: { folder: folder || null } });
      const data = parseToolResult(result);
      if (!data) return { folders: [], notes: [] };
      treeDataCache[key] = data;
      return data;
    } catch (err) {
      console.warn('Failed to load folder:', err);
      return { folders: [], notes: [] };
    }
  }

  function renderTree(data, parentEl) {
    parentEl.innerHTML = '';
    // Folders
    for (const f of data.folders) {
      const name = f.includes('/') ? f.split('/').pop() : f;
      const folderDiv = document.createElement('div');
      folderDiv.className = 'tree-folder';
      folderDiv.innerHTML = '<span class="arrow">\u25B6</span> \uD83D\uDCC1 ' + escHtml(name);
      folderDiv.dataset.folder = f;
      parentEl.appendChild(folderDiv);

      const childrenDiv = document.createElement('div');
      childrenDiv.className = 'tree-children';
      parentEl.appendChild(childrenDiv);

      folderDiv.addEventListener('click', async () => {
        const isExpanded = folderDiv.classList.contains('expanded');
        if (isExpanded) {
          folderDiv.classList.remove('expanded');
        } else {
          folderDiv.classList.add('expanded');
          if (childrenDiv.children.length === 0) {
            const subData = await loadFolder(f);
            renderTree(subData, childrenDiv);
          }
        }
      });
    }
    // Notes
    for (const n of data.notes) {
      const noteDiv = document.createElement('div');
      noteDiv.className = 'tree-note';
      noteDiv.textContent = n.title || n.path;
      noteDiv.title = n.path;
      noteDiv.dataset.path = n.path;
      noteDiv.addEventListener('click', () => loadPreview(n.path));
      parentEl.appendChild(noteDiv);
    }
  }

  async function loadRootTree() {
    treeDataCache = {};
    const data = await loadFolder(null);
    renderTree(data, treeEl);
  }

  async function loadPreview(path, { switchToNote = true } = {}) {
    currentPreviewPath = path;
    currentPath = path; // sync shared state
    // Show the Note tab button; switch to it unless caller opts out
    document.getElementById('note-tab-btn').classList.add('visible');
    if (switchToNote) switchTab('note');
    // Highlight active note in tree
    treeEl.querySelectorAll('.tree-note').forEach(el => {
      el.classList.toggle('active', el.dataset.path === path);
    });

    try {
      const result = await app.callServerTool({ name: 'vault___vault_read', arguments: { path } });
      const data = parseToolResult(result);
      if (!data) { previewEl.innerHTML = '<div class="placeholder">Note not found</div>'; return; }
      currentPreviewData = data;

      let html = '<div class="preview-header">';
      html += '<h2>' + escHtml(data.title || path) + '</h2>';
      html += '<div class="preview-actions">';
      html += '<button class="action-btn action-btn--secondary" id="preview-browse-btn" title="Back to Browse">\u2190 Browse</button>';
      html += '<button class="action-btn" id="preview-send-btn" title="Send to Claude">\uD83D\uDCAC Send</button>';
      html += '<button class="action-btn action-btn--secondary" id="preview-ctx-btn" title="Show Context">\uD83D\uDD0D Context</button>';
      html += '<button class="action-btn action-btn--secondary" id="preview-graph-btn" title="Show in Graph">\uD83D\uDD17 Graph</button>';
      html += '<button class="edit-btn-disabled" title="Coming soon" disabled>\u270F Edit</button>';
      html += '</div></div>';

      // Frontmatter
      if (data.frontmatter && Object.keys(data.frontmatter).length > 0) {
        html += '<div class="preview-fm"><table class="fm-table">';
        for (const [k, v] of Object.entries(data.frontmatter)) {
          const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
          html += '<tr><td>' + escHtml(k) + '</td><td>' + escHtml(val) + '</td></tr>';
        }
        html += '</table></div>';
      }

      // Rendered markdown
      let rendered = '';
      if (typeof marked !== 'undefined' && data.content) {
        rendered = marked.parse(data.content);
      } else {
        rendered = '<pre>' + escHtml(data.content || '') + '</pre>';
      }
      if (typeof DOMPurify !== 'undefined') {
        rendered = DOMPurify.sanitize(rendered);
      }
      html += '<div class="preview-content">' + rendered + '</div>';

      previewEl.innerHTML = html;

      // Wire action buttons
      document.getElementById('preview-browse-btn')?.addEventListener('click', () => {
        switchTab('browse');
      });
      document.getElementById('preview-send-btn')?.addEventListener('click', () => {
        window.sendToLLM(data.path, data.content || '');
      });
      document.getElementById('preview-ctx-btn')?.addEventListener('click', () => {
        window.navigateTo('context', { path: data.path });
      });
      document.getElementById('preview-graph-btn')?.addEventListener('click', () => {
        window.navigateTo('graph', { path: data.path });
      });

      window.updateContext('browser', path, data.title);
    } catch (err) {
      const errDiv = document.createElement('div');
      errDiv.className = 'placeholder';
      errDiv.textContent = 'Error: ' + (err.message || err);
      previewEl.innerHTML = '';
      previewEl.appendChild(errDiv);
    }
  }

  // Search
  let searchTimeout = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const q = searchInput.value.trim();
    if (!q) { exitSearch(); return; }
    searchTimeout = setTimeout(() => doSearch(q), 300);
  });

  async function doSearch(query) {
    isSearchMode = true;
    searchClear.style.display = '';
    try {
      const result = await app.callServerTool({
        name: 'vault___vault_search', arguments: { query, mode: 'hybrid', limit: 20 }
      });
      const data = parseToolResult(result) || [];
      treeEl.innerHTML = '';
      for (const r of data) {
        const div = document.createElement('div');
        div.className = 'search-result';
        div.innerHTML = '<div class="search-result-title">' + escHtml(r.title || r.path) + '</div>'
          + '<div class="search-result-snippet">' + escHtml(r.snippet || '') + '</div>';
        div.addEventListener('click', () => loadPreview(r.path));
        treeEl.appendChild(div);
      }
      if (data.length === 0) {
        treeEl.innerHTML = '<div class="placeholder" style="padding:12px">No results</div>';
      }
    } catch (err) {
      treeEl.innerHTML = '<div class="placeholder" style="padding:12px">Search error</div>';
    }
  }

  function exitSearch() {
    isSearchMode = false;
    searchClear.style.display = 'none';
    searchInput.value = '';
    loadRootTree();
  }

  searchClear.addEventListener('click', exitSearch);

  // Listen for navigation events
  window.addEventListener('vault-navigate', (e) => {
    if ((e.detail.view === 'browse' || e.detail.view === 'note') && e.detail.path) {
      // When navigating to browse with a path, load the preview but stay on browse
      const switchToNote = e.detail.view !== 'browse';
      loadPreview(e.detail.path, { switchToNote });
    }
  });

  // Auto-load when browse tab is selected
  window.addEventListener('vault-tab-changed', (e) => {
    if (e.detail.tab === 'browse' && treeEl.children.length === 0 && !isSearchMode) {
      loadRootTree();
    }
  });

  window.loadBrowser = loadRootTree;
  window.loadPreview = loadPreview;
})();
