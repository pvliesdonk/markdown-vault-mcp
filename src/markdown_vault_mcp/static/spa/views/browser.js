// ── Vault Browser View ──────────────────────────────────────────────────
(function() {
  function escHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const ICON_CHEVRON = '<svg class="tree-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
  const ICON_FILE = '<svg class="tree-ico" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>';
  const ICON_IMAGE = '<svg class="tree-ico" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M5 17l5-4 4 3 3-2 2 2"/></svg>';

  let treeDataCache = {};
  let isSearchMode = false;

  const treeEl = document.getElementById('browser-tree');
  const searchInput = document.getElementById('browser-search-input');
  const searchClear = document.getElementById('browser-search-clear');

  async function loadFolder(folder) {
    const key = folder || '__root__';
    if (treeDataCache[key]) return treeDataCache[key];
    try {
      const result = await app.callServerTool({ name: 'app___vault_list', arguments: { folder: folder || null } });
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
      folderDiv.innerHTML = ICON_CHEVRON + '<span class="tree-label">' + escHtml(name) + '</span>';
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
      const icon = n.kind === 'attachment' ? ICON_IMAGE : ICON_FILE;
      noteDiv.innerHTML = icon + '<span class="tree-label">' + escHtml(n.title || n.path) + '</span>';
      noteDiv.title = n.path;
      noteDiv.dataset.path = n.path;
      noteDiv.addEventListener('click', () => window.loadPreview(n.path));
      parentEl.appendChild(noteDiv);
    }
  }

  async function loadRootTree() {
    treeDataCache = {};
    const data = await loadFolder(null);
    renderTree(data, treeEl);
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
        name: 'app___vault_search', arguments: { query, mode: 'hybrid', limit: 20 }
      });
      const data = parseToolResult(result) || [];
      treeEl.innerHTML = '';
      for (const r of data) {
        const div = document.createElement('div');
        div.className = 'search-result';
        div.innerHTML = '<div class="search-result-title">' + escHtml(r.title || r.path) + '</div>'
          + '<div class="search-result-snippet">' + escHtml(r.snippet || '') + '</div>';
        div.addEventListener('click', () => window.loadPreview(r.path));
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

  // Auto-load when browse tab is selected
  window.addEventListener('vault-tab-changed', (e) => {
    if (e.detail.tab === 'browse' && treeEl.children.length === 0 && !isSearchMode) {
      loadRootTree();
    }
  });

  window.loadBrowser = loadRootTree;
})();
