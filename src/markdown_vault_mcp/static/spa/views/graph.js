// ── Graph Explorer View ──────────────────────────────────────────────────
(function() {
  let network = null;
  let nodesDS = null;
  let edgesDS = null;
  let graphCenterPath = null;
  let selectedNodeId = null;
  let semanticEnabled = false;
  let _depths = new Map();
  const LABEL_ZOOM_THRESHOLD = 1.35;
  let _zoomedIn = false;
  let _hoveredId = null;

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

  // Paper palette for edges and canvas chrome, read from CSS vars so the graph
  // re-themes when the host flips theme (see refreshColors / vault-theme-changed).
  function getColors() {
    // Resolve each token to a concrete rgb() string via a probe element.
    // getComputedStyle(documentElement).getPropertyValue('--panel') can return
    // the literal "var(--color-background-primary)" when a Paper token is an
    // indirection (styles.css maps --panel/--ink/--accent/... onto --color-*).
    // Modern Chromium resolves that, but older Chromium (Claude Desktop's
    // Electron) returns the unresolved string, which canvas fillStyle can't
    // parse -> vis-network paints black. Assigning `var(...)` to a real
    // property (color) forces full var() substitution in every engine and
    // still honors host overrides + theme flips.
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
    document.body.appendChild(probe);
    const v = (name, fallback) => {
      probe.style.color = `var(${name}, ${fallback})`;
      return getComputedStyle(probe).color; // always a resolved rgb(...)
    };
    const c = {
      panel:      v('--panel', '#fbf8f1'),
      panel2:     v('--panel-2', '#f2ecdf'),
      ink:        v('--ink', '#2a2620'),
      ink2:       v('--ink-2', '#5c5446'),
      muted:      v('--muted', '#928975'),
      edge:       v('--edge', '#d3c7ad'),
      accent:     v('--accent', '#b25a35'),
      accentInk:  v('--accent-ink', '#ffffff'),
      accentSoft: v('--accent-soft', '#f3e4d8'),
      border:     v('--border', '#e4dbc9'),
    };
    probe.remove();
    return c;
  }

  // Edge styling: solid --edge for link edges (thinner + lower opacity when the
  // farthest endpoint is distant), dashed --accent for semantic similarity.
  function edgeStyle(type, depthMax, c) {
    if (type === 'semantic') {
      return { color: { color: c.accent, opacity: 0.75 }, dashes: [5, 4], width: 1 };
    }
    const far = depthMax >= 2;
    return { color: { color: c.edge, opacity: far ? 0.5 : 1 }, dashes: false, width: far ? 1 : 1.6 };
  }

  function initNetwork() {
    if (network) return;
    const container = document.getElementById('graph-container');
    if (!container || typeof vis === 'undefined') return;
    nodesDS = new vis.DataSet();
    edgesDS = new vis.DataSet();
    const options = {
      physics: { enabled: true, solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -40 } },
      nodes: {
        shape: 'dot',
        widthConstraint: { maximum: 180 }, // wrap long titles -> compact pills, not full-width bars
        scaling: { min: 8, max: 30, label: { enabled: true, min: 10, max: 16, drawThreshold: 6 } },
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

    network.on('zoom', (params) => {
      const zoomed = params.scale >= LABEL_ZOOM_THRESHOLD;
      if (zoomed !== _zoomedIn) { _zoomedIn = zoomed; applyLOD(); }  // only on crossing
    });
    network.on('hoverNode', (params) => { _hoveredId = params.node; applyLOD(); });
    network.on('blurNode', () => { _hoveredId = null; applyLOD(); });
  }

  function addGraphData(data) {
    if (!nodesDS || !edgesDS) return;
    const c = getColors();
    for (const n of data.nodes) {
      if (!nodesDS.get(n.id)) {
        const bc = n.backlink_count || 0;
        nodesDS.add({
          id: n.id, label: n.label,
          title: n.label + (n.folder ? ' (' + n.folder + ')' : '') + (bc > 0 ? ' \u2014 ' + bc + ' backlinks' : ''),
          _label: n.label, _folder: n.folder, _group: n.group,
          _folderColor: n.group === 'orphan' ? '#94a3b8' : _folderColor(n.folder),
          _backlinks: bc,
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
        const es = edgeStyle(e.type, 0, c);
        edgesDS.add({
          id: edgeId, from: e.from, to: e.to,
          color: es.color, title: e.type, dashes: es.dashes, width: es.width,
          arrows: isSemantic ? '' : { to: { enabled: true, scaleFactor: 0.5 } },
        });
      }
    }
    _depths = computeDepths();
    styleNodes(_depths);
    styleEdges(_depths);
    applyLOD();
    updateCountChip();
  }

  // BFS distance-from-focus over the accumulated edge set. Unreachable nodes are
  // absent from the returned map; callers treat a missing entry as Infinity.
  function computeDepths() {
    const depths = new Map();
    if (!nodesDS || !graphCenterPath || !nodesDS.get(graphCenterPath)) return depths;
    const adj = new Map();
    for (const e of edgesDS.get()) {
      if (!adj.has(e.from)) adj.set(e.from, []);
      if (!adj.has(e.to)) adj.set(e.to, []);
      adj.get(e.from).push(e.to);
      adj.get(e.to).push(e.from);
    }
    const queue = [graphCenterPath];
    depths.set(graphCenterPath, 0);
    while (queue.length) {
      const id = queue.shift();
      const d = depths.get(id);
      for (const nb of adj.get(id) || []) {
        if (!depths.has(nb)) { depths.set(nb, d + 1); queue.push(nb); }
      }
    }
    return depths;
  }

  // A node is "semantic-match" if every edge touching it is a semantic edge.
  function _semanticMatch(id) {
    const touching = edgesDS.get().filter(e => e.from === id || e.to === id);
    return touching.length > 0 && touching.every(e => e.title === 'semantic');
  }

  // Map each node to its Paper role by distance. Focus is the accent pill;
  // depth-1 and hub nodes are box pills, and among those the semantic-match ones
  // (every touching edge semantic) get the dashed-pill styling; all other nodes
  // are dots with `label: ''`. applyLOD then promotes/demotes labels by zoom/hover.
  function styleNodes(depths) {
    if (!nodesDS) return;
    const c = getColors();
    const updates = [];
    for (const n of nodesDS.get()) {
      const d = depths.has(n.id) ? depths.get(n.id) : Infinity;
      const folder = n._folderColor || _folderColor(n._folder);
      let u;
      if (d === 0) {
        u = { id: n.id, shape: 'box', shapeProperties: { borderRadius: 20 },
              color: { background: c.accent, border: c.accent }, borderWidth: 2,
              font: { color: c.accentInk, size: 13 },
              label: n._label };
      } else if (d === 1 || (n._group === 'hub')) {
        if (_semanticMatch(n.id)) {
          u = { id: n.id, shape: 'box', shapeProperties: { borderRadius: 20, borderDashes: [4, 3] },
                color: { background: c.accentSoft, border: c.accent }, borderWidth: 1.5,
                font: { color: c.ink2, size: 12 }, label: n._label };
        } else {
          u = { id: n.id, shape: 'box', shapeProperties: { borderRadius: 20 },
                color: { background: c.panel, border: folder },
                borderWidth: n._group === 'hub' ? 3 : 1.5,
                font: { color: c.ink2, size: 12 },
                shadow: { enabled: true, size: 6, x: 0, y: 1, color: 'rgba(0,0,0,0.12)' },
                label: n._label };
        }
      } else {
        u = { id: n.id, shape: 'dot', value: Math.max(n._backlinks, 1),
              color: { background: folder, border: c.edge }, borderWidth: 1.5,
              shapeProperties: n._group === 'orphan' ? { borderDashes: [5, 5] } : {},
              font: { color: c.ink2 },
              label: '' };
      }
      updates.push(u);
    }
    nodesDS.update(updates);
  }

  // Coherence predicate: a node's label shows iff it is near the focus, OR it
  // is a hub node, OR the graph is zoomed in past the threshold, OR the node
  // is currently hovered.
  function labelVisible(n) {
    const d = _depths.has(n.id) ? _depths.get(n.id) : Infinity;
    return d <= 1 || n._group === 'hub' || _zoomedIn || n.id === _hoveredId;
  }

  // Re-derive every node's label from labelVisible (never toggled ad hoc).
  function applyLOD() {
    if (!nodesDS) return;
    const updates = nodesDS.get().map(n => ({
      id: n.id, label: labelVisible(n) ? n._label : '',
    }));
    nodesDS.update(updates);
  }

  function styleEdges(depths) {
    if (!edgesDS) return;
    const c = getColors();
    const updates = edgesDS.get().map(e => {
      const dm = Math.max(depths.get(e.from) ?? Infinity, depths.get(e.to) ?? Infinity);
      const es = edgeStyle(e.title, dm, c);
      return { id: e.id, color: es.color, dashes: es.dashes, width: es.width };
    });
    edgesDS.update(updates);
  }

  // Re-read Paper vars and restyle everything (a canvas doesn't inherit CSS-var
  // changes, so the graph must re-read on host theme flips).
  function refreshColors() {
    if (!nodesDS) return;
    styleNodes(_depths);
    styleEdges(_depths);
    applyLOD();
  }
  window.addEventListener('vault-theme-changed', refreshColors);

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

  function updateCountChip() {
    const chip = document.getElementById('graph-count');
    if (chip && nodesDS) chip.textContent = nodesDS.length + (nodesDS.length === 1 ? ' note' : ' notes');
  }
  document.getElementById('graph-zoom-in')?.addEventListener('click', () => {
    if (network) network.moveTo({ scale: network.getScale() * 1.3 });
  });
  document.getElementById('graph-zoom-out')?.addEventListener('click', () => {
    if (network) network.moveTo({ scale: network.getScale() / 1.3 });
  });

  // Narrow-panel legend collapse chip
  document.getElementById('graph-legend-chip')?.addEventListener('click', () => {
    document.getElementById('graph-legend')?.classList.toggle('open');
  });

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
    btn.classList.toggle('active', semanticEnabled);
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
