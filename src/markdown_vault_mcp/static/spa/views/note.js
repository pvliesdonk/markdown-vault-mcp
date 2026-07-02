// ── Note Preview View ────────────────────────────────────────────────────
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

  const previewEl = document.getElementById('browser-preview');

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
      const result = await app.callServerTool({ name: 'vault___vault_read', arguments: { path } });
      const data = parseToolResult(result);
      if (!data) { previewEl.innerHTML = '<div class="placeholder">Note not found</div>'; return; }
      currentPreviewData = data;

      let html = '<div class="preview-header">';
      html += '<h2>' + escHtml(data.title || path) + '</h2>';
      html += '<div class="preview-actions">';
      html += '<button class="action-btn action-btn--secondary" id="preview-browse-btn" title="Back to Browse">← Browse</button>';
      html += '<button class="action-btn" id="preview-send-btn" title="Send to Claude">💬 Send</button>';
      html += '<button class="action-btn action-btn--secondary" id="preview-ctx-btn" title="Show Context">🔍 Context</button>';
      html += '<button class="action-btn action-btn--secondary" id="preview-graph-btn" title="Show in Graph">🔗 Graph</button>';
      html += '<button class="edit-btn-disabled" title="Coming soon" disabled>✏ Edit</button>';
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
