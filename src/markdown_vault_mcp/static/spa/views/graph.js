// ── Graph Explorer View ──────────────────────────────────────────────────
(function() {
  let network = null;
  let nodesDS = null;
  let edgesDS = null;
  let graphCenterPath = null;
  let selectedNodeId = null;
  let semanticEnabled = false;

  // Folder-based node colors — chosen to stand out on both light and dark backgrounds
  const _FOLDER_COLORS = [
    '#6366f1', // indigo
    '#0ea5e9', // sky
    '#10b981', // emerald
    '#f59e0b', // amber
    '#ef4444', // red
    '#8b5cf6', // violet
    '#06b6d4', // cyan
    '#f97316', // orange
  ];
  function _folderColor(folder) {
    if (!folder) return _FOLDER_COLORS[0];
    let h = 0;
    for (let i = 0; i < folder.length; i++) h = (h * 31 + folder.charCodeAt(i)) & 0x7fffffff;
    return _FOLDER_COLORS[h % _FOLDER_COLORS.length];
  }

  // Color scheme for edges and UI chrome
  function getColors() {
    const s = getComputedStyle(document.documentElement);
    return {
      fg: s.getPropertyValue('--color-text-primary').trim() || '#1a1a1a',
      accent: s.getPropertyValue('--color-text-info').trim() || '#6366f1',
      muted: s.getPropertyValue('--color-text-secondary').trim() || '#6b7280',
      border: s.getPropertyValue('--color-border-primary').trim() || '#e0e0e0',
    };
  }

  const _SEMANTIC_EDGE_COLOR = '#a855f7'; // purple — distinct from folder node colors

  function edgeColorByType(type, c) {
    if (type === 'semantic') return _SEMANTIC_EDGE_COLOR;
    if (type === 'wikilink') return c.accent;
    if (type === 'reference') return c.muted;
    return c.border; // markdown = default
  }

  function initNetwork() {
    if (network) return;
    const container = document.getElementById('graph-container');
    if (!container || typeof vis === 'undefined') return;
    const c = getColors();
    nodesDS = new vis.DataSet();
    edgesDS = new vis.DataSet();
    const options = {
      physics: { enabled: true, solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -40 } },
      nodes: {
        shape: 'dot',
        scaling: { min: 8, max: 30, label: { enabled: true, min: 10, max: 16 } },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.5 } }, font: { size: 9 }, smooth: { type: 'continuous' },
      },
      interaction: { hover: true, tooltipDelay: 200 },
    };
    network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, options);

    // Click: expand neighbors + center on clicked node
    network.on('click', async (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        selectedNodeId = nodeId;
        currentPath = nodeId; // sync shared state
        await expandNode(nodeId);
        network.focus(nodeId, { scale: 1.2, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
        showMiniCard(nodeId);
      } else {
        hideMiniCard();
        selectedNodeId = null;
      }
    });

    // Hover: tooltip is built-in via node.title
    // Double-click: focus mode — clear graph and reload only this node's neighborhood
    network.on('doubleClick', (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        currentPath = nodeId;
        loadGraph(nodeId);
      }
    });
  }

  function addGraphData(data) {
    if (!nodesDS || !edgesDS) return;
    const c = getColors();
    for (const n of data.nodes) {
      if (!nodesDS.get(n.id)) {
        const bc = n.backlink_count || 0;
        const baseColor = n.group === 'orphan' ? '#94a3b8' : _folderColor(n.folder);
        nodesDS.add({
          id: n.id, label: n.label,
          value: Math.max(bc, 1),
          title: n.label + (n.folder ? ' (' + n.folder + ')' : '') + (bc > 0 ? ' \u2014 ' + bc + ' backlinks' : ''),
          color: {
            background: baseColor,
            border: n.group === 'hub' ? '#ffffff' : baseColor,
            highlight: { background: baseColor, border: '#ffffff' },
            hover: { background: baseColor, border: '#ffffff' },
          },
          font: { color: '#ffffff', size: 12 },
          borderWidth: n.group === 'hub' ? 3 : (n.group === 'orphan' ? 1 : 2),
          borderWidthSelected: 4,
          shapeProperties: n.group === 'orphan' ? { borderDashes: [5, 5] } : {},
        });
      }
    }
    for (const e of data.edges) {
      const isSemantic = e.type === 'semantic';
      // Semantic edges are undirected — use canonical key to avoid duplicates
      const edgeId = isSemantic
        ? 'sem:' + [e.from, e.to].sort().join('<>')
        : e.from + '->' + e.to;
      if (!edgesDS.get(edgeId)) {
        edgesDS.add({
          id: edgeId, from: e.from, to: e.to,
          color: { color: edgeColorByType(e.type, c), highlight: _SEMANTIC_EDGE_COLOR },
          title: e.type,
          dashes: isSemantic,
          width: isSemantic ? 1 : 1.5,
          arrows: isSemantic ? '' : { to: { enabled: true, scaleFactor: 0.5 } },
        });
      }
    }
  }

  async function expandNode(path) {
    try {
      const result = await app.callServerTool({
        name: 'vault___vault_graph_neighborhood',
        arguments: { path, depth: 1, include_semantic: semanticEnabled },
      });
      const data = parseToolResult(result);
      if (!data) return;
      addGraphData(data);
      const truncSuffix = data.truncated ? ' (truncated — zoom in for more)' : '';
      window.updateContext('graph explorer', path, nodesDS.get(path)?.label,
        'Visible: ' + nodesDS.length + ' notes, ' + edgesDS.length + ' links' + truncSuffix);
    } catch (err) {
      console.warn('Graph expand failed:', err);
    }
  }

  async function loadHubs() {
    try {
      const result = await app.callServerTool({ name: 'vault___vault_graph_hubs', arguments: {} });
      const data = parseToolResult(result);
      if (!data) return;
      addGraphData(data);
      if (data.nodes.length > 0) {
        window.updateContext('graph explorer', '(hub view)', null,
          'Showing ' + data.nodes.length + ' most-linked notes');
      }
    } catch (err) {
      console.warn('Graph hubs failed:', err);
    }
  }

  async function loadGraph(path) {
    initNetwork();
    if (!nodesDS || !edgesDS) return;  // vis CDN failed to load
    nodesDS.clear();
    edgesDS.clear();
    graphCenterPath = path || null;
    if (path) {
      await expandNode(path);
      network.fit({ animation: true });
    } else {
      await loadHubs();
      network.fit({ animation: true });
    }
  }

  // Mini context card on single click
  function showMiniCard(nodeId) {
    const card = document.getElementById('graph-mini-card');
    app.callServerTool({ name: 'vault___vault_context', arguments: { path: nodeId } }).then(result => {
      const data = parseToolResult(result);
      if (!data) { card.style.display = 'none'; return; }
      const bl = (data.backlinks || []).slice(0, 3);
      const ol = (data.outlinks || []).slice(0, 3);
      let html = '<h4>' + escHtml(data.title || nodeId) + '</h4>';
      if (data.tags && Object.keys(data.tags).length > 0) {
        const allTags = Object.values(data.tags).flat().slice(0, 5);
        html += '<div>' + allTags.map(t => '<span class="tag-pill" style="font-size:10px">' + escHtml(t) + '</span>').join(' ') + '</div>';
      }
      if (bl.length > 0) {
        html += '<div class="mini-links"><strong>Backlinks:</strong>';
        for (const b of bl) html += '<div class="mini-link" data-path="' + escHtml(b.source_path) + '">' + escHtml(b.source_path) + '</div>';
        html += '</div>';
      }
      if (ol.length > 0) {
        html += '<div class="mini-links"><strong>Outlinks:</strong>';
        for (const o of ol) html += '<div class="mini-link" data-path="' + escHtml(o.target_path) + '">' + escHtml(o.target_path) + '</div>';
        html += '</div>';
      }
      html += '<div class="mini-actions">'
        + '<button id="mini-full-ctx">Full Context</button>'
        + '<button id="mini-open-browser">Open in Browser</button>'
        + '</div>';
      card.innerHTML = html;
      card.style.display = '';

      card.querySelector('#mini-full-ctx')?.addEventListener('click', () => {
        window.navigateTo('context', { path: nodeId });
        hideMiniCard();
      });
      card.querySelector('#mini-open-browser')?.addEventListener('click', () => {
        window.navigateTo('browse', { path: nodeId });
        hideMiniCard();
      });
      card.querySelectorAll('.mini-link').forEach(el => {
        el.addEventListener('click', () => expandNode(el.dataset.path));
      });
    }).catch(() => { card.style.display = 'none'; });
  }

  function hideMiniCard() {
    document.getElementById('graph-mini-card').style.display = 'none';
  }

  // Send graph summary to Claude
  document.getElementById('graph-send-btn').addEventListener('click', () => {
    if (!nodesDS || nodesDS.length === 0) return;
    const center = graphCenterPath || 'hub view';
    const nodeLabels = nodesDS.get().map(n => n.label).slice(0, 20).join(', ');
    const summary = 'Graph around ' + center + ': ' + nodesDS.length + ' notes, '
      + edgesDS.length + ' links\nNotes: ' + nodeLabels;
    window.sendToLLM(center, summary);
  });

  // Fullscreen for graph
  document.getElementById('graph-fullscreen-btn').addEventListener('click', async () => {
    try { await app.requestDisplayMode({ mode: 'fullscreen' }); } catch (e) { console.warn(e); }
  });

  // Semantic toggle: reload graph with/without semantic edges
  document.getElementById('graph-semantic-btn').addEventListener('click', () => {
    semanticEnabled = !semanticEnabled;
    const btn = document.getElementById('graph-semantic-btn');
    if (semanticEnabled) {
      btn.style.background = _SEMANTIC_EDGE_COLOR;
      btn.style.color = '#ffffff';
      btn.style.border = 'none';
    } else {
      btn.style.background = 'var(--color-background-secondary)';
      btn.style.color = 'var(--color-text-primary)';
      btn.style.border = '1px solid var(--color-border-primary)';
    }
    // Reload current graph with updated setting
    if (graphCenterPath) loadGraph(graphCenterPath);
  });

  // Listen for navigation events
  window.addEventListener('vault-navigate', (e) => {
    if (e.detail.view === 'graph') {
      loadGraph(e.detail.path || null);
    }
  });

  // Sync to shared currentPath when switching to this tab
  window.addEventListener('vault-tab-changed', (e) => {
    if (e.detail.tab === 'graph') {
      if (currentPath && currentPath !== graphCenterPath) {
        loadGraph(currentPath);
      } else if (!nodesDS || nodesDS.length === 0) {
        loadGraph(graphCenterPath);
      }
    }
  });

  window.loadGraph = loadGraph;
})();
