(() => {
  const MAX_EVENT_FEED = 260;
  const TRANSIENT_EDGE_TTL_MS = 18000;

  const state = {
    config: null,
    ws: null,
    nodes: [],
    edges: [],
    nodeById: new Map(),
    edgeById: new Map(),
    requestCounts: new Map(),
    attackers: new Map(),
    attackIdToIp: new Map(),
    decoySetIds: new Set(),
    transientEdges: new Map(),
    processedEventIds: new Set(),
    selectedAttackId: null,
    stats: {
      attacksDetected: 0,
      decoysSpawned: 0,
      decoysCleaned: 0,
      activeDecoySets: 0,
    },
  };

  const dom = {
    clock: document.getElementById('live-clock'),
    summaryReal: document.getElementById('summary-real'),
    summaryDecoy: document.getElementById('summary-decoy'),
    summaryGateway: document.getElementById('summary-gateway'),
    attackCounter: document.getElementById('attack-counter'),
    statAttacks: document.getElementById('stat-attacks'),
    statDecoysSpawned: document.getElementById('stat-decoys-spawned'),
    statDecoysCleaned: document.getElementById('stat-decoys-cleaned'),
    statActiveSets: document.getElementById('stat-active-sets'),
    eventFeed: document.getElementById('event-feed'),
    eventCount: document.getElementById('event-count'),
    status: document.getElementById('connection-status'),
    tooltip: document.getElementById('tooltip'),
  };

  let svg;
  let zoomLayer;
  let edgeLayer;
  let nodeLayer;
  let effectLayer;
  let simulation;
  let linkSelection;
  let nodeSelection;
  let width = 1200;
  let height = 650;

  function setConnectionStatus(text, cls) {
    dom.status.textContent = text;
    dom.status.className = `connection-status ${cls}`;
  }

  function updateClock() {
    const now = new Date();
    dom.clock.textContent = now.toLocaleTimeString();
  }

  function formatTime(ts) {
    if (!ts) {
      return '-';
    }
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) {
      return String(ts);
    }
    return d.toLocaleTimeString();
  }

  function normalizeNamespace(value) {
    return value || 'external';
  }

  function endpointId(value) {
    if (!value) {
      return null;
    }
    if (typeof value === 'object') {
      return value.id;
    }
    return String(value);
  }

  function attackerNodeId(ip) {
    return `attacker:${ip}`;
  }

  function parseTargetServiceFromEndpoint(endpoint) {
    if (!endpoint || typeof endpoint !== 'string') {
      return null;
    }
    const host = endpoint.split(':', 1)[0];
    const parts = host.split('.');
    if (parts.length < 2) {
      return null;
    }
    return `service:${parts[1]}:${parts[0]}`;
  }

  function getNodeCategory(node) {
    if (node.entityType === 'attacker' || String(node.id).startsWith('attacker:')) {
      return 'attacker';
    }
    if (node.role === 'gateway' || /router|gateway/i.test(node.name || '')) {
      return 'gateway';
    }
    if (node.role === 'monitoring' || node.namespace === 'monitoring') {
      return 'monitoring';
    }
    if (node.role === 'decoy' || node.namespace === 'decoy-pool') {
      return 'decoy';
    }
    return 'real';
  }

  function nodeRadius(node) {
    const category = getNodeCategory(node);
    if (category === 'monitoring') {
      return 7;
    }
    if (category === 'gateway') {
      return 12;
    }
    if (category === 'attacker') {
      return 11;
    }
    if (category === 'decoy') {
      return 10;
    }
    const count = state.requestCounts.get(node.id) || 1;
    return Math.min(22, 8 + Math.sqrt(count) * 1.9);
  }

  function makeEdgeId(source, target, type, suffix) {
    return `${source}__${target}__${type}${suffix ? `__${suffix}` : ''}`;
  }

  function edgeClass(edge) {
    if (edge.type === 'attack_traffic') {
      return 'attack';
    }
    if (edge.type === 'redirected_traffic') {
      return 'redirect';
    }
    if (edge.type === 'legitimate_traffic') {
      return 'legit';
    }
    return 'mesh';
  }

  function edgeColor(edge) {
    if (edge.type === 'attack_traffic') {
      return '#ff4d5f';
    }
    if (edge.type === 'redirected_traffic') {
      return '#f4b942';
    }
    if (edge.type === 'legitimate_traffic') {
      return '#2bd97f';
    }
    return '#6f7789';
  }

  function edgeWidth(edge) {
    const rate = edge.rate || 1;
    if (edge.type === 'attack_traffic') {
      return 2.3;
    }
    if (edge.type === 'redirected_traffic') {
      return 2.2;
    }
    if (edge.type === 'legitimate_traffic') {
      return Math.min(5.2, 1.2 + Math.log(rate + 1));
    }
    return 1;
  }

  function edgeMarker(edge) {
    if (edge.type === 'attack_traffic') {
      return 'url(#arrow-attack)';
    }
    if (edge.type === 'redirected_traffic') {
      return 'url(#arrow-redirect)';
    }
    return null;
  }

  function roleTargetY(node) {
    const category = getNodeCategory(node);
    if (category === 'attacker') {
      return height * 0.16;
    }
    if (category === 'gateway') {
      return height * 0.34;
    }
    if (category === 'real') {
      return height * 0.56;
    }
    if (category === 'decoy') {
      return height * 0.72;
    }
    return height * 0.84;
  }

  function namespaceTargetX(namespace) {
    const normalized = normalizeNamespace(namespace);
    const list = Array.from(
      new Set(state.nodes.map((n) => normalizeNamespace(n.namespace)))
    ).sort();
    if (list.length === 0) {
      return width / 2;
    }
    const idx = Math.max(0, list.indexOf(normalized));
    return ((idx + 1) * width) / (list.length + 1);
  }

  function shortLabel(node) {
    if (getNodeCategory(node) === 'attacker') {
      return String(node.name).replace('attacker ', '');
    }
    const name = node.name || node.id;
    return name.length > 16 ? `${name.slice(0, 13)}...` : name;
  }

  function ensureAttackerNode(ip, markActive) {
    if (!ip) {
      return null;
    }

    const id = attackerNodeId(ip);
    const existing = state.nodeById.get(id);

    const attackerMeta = state.attackers.get(ip) || {
      ip,
      id,
      attacks: 0,
      lastSeen: Date.now(),
      activeUntil: 0,
      attackIds: new Set(),
    };

    attackerMeta.attacks += 1;
    attackerMeta.lastSeen = Date.now();
    if (markActive) {
      attackerMeta.activeUntil = Date.now() + 10000;
    }

    state.attackers.set(ip, attackerMeta);

    if (!existing) {
      const node = {
        id,
        name: `attacker ${ip}`,
        namespace: 'external',
        type: 'attacker',
        role: 'attacker',
        status: markActive ? 'Active' : 'Observed',
        labels: {},
        ip,
        createdAt: new Date().toISOString(),
        entityType: 'attacker',
        renderScale: 0.3,
      };
      state.nodes.push(node);
      state.nodeById.set(node.id, node);
    } else {
      existing.status = markActive ? 'Active' : existing.status;
      existing.ip = ip;
    }

    return id;
  }

  function normalizeSnapshotNode(node) {
    const labels = node.labels || {};
    const attackId = labels['attack-id'] || node.attack_id || null;
    return {
      id: String(node.id),
      name: node.name || String(node.id),
      namespace: normalizeNamespace(node.namespace),
      type: node.type || 'pod',
      role: node.role || 'real',
      status: node.status || 'Unknown',
      labels,
      ip: node.ip || labels['attacker-ip'] || null,
      createdAt:
        node.createdAt ||
        node.created_at ||
        labels['deception-system/created-at'] ||
        null,
      attackId,
      entityType: node.entityType || null,
      renderScale: 1,
    };
  }

  function normalizeSnapshotEdge(edge) {
    const src = endpointId(edge.source);
    const dst = endpointId(edge.target);
    if (!src || !dst) {
      return null;
    }

    let type = edge.type || 'internal_mesh';
    if (type === 'service_selector') {
      type = 'internal_mesh';
    }
    if (type === 'service_dependency') {
      type = 'legitimate_traffic';
    }
    if (type === 'attacker_route') {
      type = 'redirected_traffic';
    }

    return {
      id: makeEdgeId(src, dst, type, edge.attacker_ip || ''),
      source: src,
      target: dst,
      type,
      rate: edge.rate || 1,
      attackerIp: edge.attacker_ip || null,
    };
  }

  function pruneTransientEdges() {
    const now = Date.now();
    for (const [id, edge] of state.transientEdges.entries()) {
      if (!edge.expiresAt || edge.expiresAt <= now) {
        state.transientEdges.delete(id);
      }
    }
  }

  function toUniqueById(items) {
    const map = new Map();
    items.forEach((item) => map.set(item.id, item));
    return Array.from(map.values());
  }

  function updateSummaryPanels() {
    const pods = state.nodes.filter((n) => n.type === 'pod');
    const real = pods.filter((n) => getNodeCategory(n) === 'real').length;
    const decoy = pods.filter((n) => getNodeCategory(n) === 'decoy').length;
    const gateway = pods.filter((n) => getNodeCategory(n) === 'gateway').length;

    dom.summaryReal.textContent = String(real);
    dom.summaryDecoy.textContent = String(decoy);
    dom.summaryGateway.textContent = String(gateway);

    dom.attackCounter.textContent = String(state.stats.attacksDetected);
    dom.statAttacks.textContent = String(state.stats.attacksDetected);
    dom.statDecoysSpawned.textContent = String(state.stats.decoysSpawned);
    dom.statDecoysCleaned.textContent = String(state.stats.decoysCleaned);
    dom.statActiveSets.textContent = String(state.stats.activeDecoySets);
  }

  function tooltipHtml(node) {
    const namespace = node.namespace || '-';
    const ip = node.ip || '-';
    const created = formatTime(node.createdAt);
    const status = node.status || '-';
    const attackId = node.attackId || (node.labels && node.labels['attack-id']) || '-';
    return [
      `<strong>${node.name || node.id}</strong>`,
      `Namespace: ${namespace}`,
      `IP: ${ip}`,
      `Status: ${status}`,
      `Created: ${created}`,
      `Attack ID: ${attackId}`,
    ].join('<br/>');
  }

  function positionTooltip(evt, node) {
    dom.tooltip.innerHTML = tooltipHtml(node);
    dom.tooltip.classList.remove('hidden');

    const rect = document.getElementById('graph-wrap').getBoundingClientRect();
    const x = evt.clientX - rect.left + 14;
    const y = evt.clientY - rect.top + 14;

    dom.tooltip.style.left = `${x}px`;
    dom.tooltip.style.top = `${y}px`;
  }

  function hideTooltip() {
    dom.tooltip.classList.add('hidden');
  }

  function belongsToSelectedGroupNode(node, selectedAttackId) {
    if (!selectedAttackId) {
      return false;
    }

    const attackId = node.attackId || (node.labels && node.labels['attack-id']);
    if (attackId === selectedAttackId) {
      return true;
    }

    const attackerIp = state.attackIdToIp.get(selectedAttackId);
    if (attackerIp && node.id === attackerNodeId(attackerIp)) {
      return true;
    }

    return false;
  }

  function updateGroupHighlighting() {
    const selectedAttackId = state.selectedAttackId;
    if (!nodeSelection || !linkSelection) {
      return;
    }

    if (!selectedAttackId) {
      nodeSelection.classed('in-group', false).classed('dimmed', false);
      linkSelection.classed('in-group', false).classed('dimmed', false);
      return;
    }

    const inGroupNodeIds = new Set(
      state.nodes
        .filter((node) => belongsToSelectedGroupNode(node, selectedAttackId))
        .map((node) => node.id)
    );

    nodeSelection
      .classed('in-group', (d) => inGroupNodeIds.has(d.id))
      .classed('dimmed', (d) => !inGroupNodeIds.has(d.id));

    linkSelection
      .classed('in-group', (d) => {
        const src = endpointId(d.source);
        const dst = endpointId(d.target);
        return inGroupNodeIds.has(src) || inGroupNodeIds.has(dst);
      })
      .classed('dimmed', (d) => {
        const src = endpointId(d.source);
        const dst = endpointId(d.target);
        return !inGroupNodeIds.has(src) && !inGroupNodeIds.has(dst);
      });
  }

  function applyNodeShape(selection, node) {
    const category = getNodeCategory(node);
    const radius = nodeRadius(node);

    const core = selection.select('circle.node-core');
    const pulse = selection.select('circle.pulse-ring');
    const shape = selection.select('path.node-shape');
    const icon = selection.select('text.node-icon');
    const label = selection.select('text.node-label');

    selection
      .attr('class', `node ${category}`)
      .classed('active', category === 'attacker' && (state.attackers.get(node.ip)?.activeUntil || 0) > Date.now())
      .classed('attacker-alert', false);

    pulse.attr('r', radius + 6);

    if (category === 'gateway') {
      const symbol = d3.symbol().type(d3.symbolDiamond).size((radius + 2) * (radius + 2) * 2.2);
      shape
        .attr('display', null)
        .attr('d', symbol)
        .attr('fill', '#2a4f8a')
        .attr('stroke', '#41a7ff')
        .attr('stroke-width', 2);
      core.attr('display', 'none');
      icon.text('').attr('display', 'none');
    } else if (category === 'attacker') {
      const symbol = d3.symbol().type(d3.symbolTriangle).size((radius + 4) * (radius + 4) * 2.1);
      shape
        .attr('display', null)
        .attr('d', symbol)
        .attr('fill', '#ffb347')
        .attr('stroke', '#ff7f45')
        .attr('stroke-width', 2.2);
      core.attr('display', 'none');
      icon.text('!').attr('display', null).attr('y', 1);
    } else {
      shape.attr('display', 'none');
      core
        .attr('display', null)
        .attr('r', radius)
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', category === 'decoy' ? '6 4' : null);

      if (category === 'decoy') {
        core.attr('fill', '#3a1017').attr('stroke', '#ff4d5f');
      } else if (category === 'monitoring') {
        core.attr('fill', '#2b303d').attr('stroke', '#9da6ba');
      } else {
        core.attr('fill', '#0f3520').attr('stroke', '#2bd97f');
      }
      icon.text('').attr('display', 'none');
    }

    label.text(shortLabel(node));
  }

  function dragStarted(event, d) {
    if (!event.active) {
      simulation.alphaTarget(0.25).restart();
    }
    d.fx = d.x;
    d.fy = d.y;
  }

  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }

  function dragEnded(event, d) {
    if (!event.active) {
      simulation.alphaTarget(0);
    }
    d.fx = null;
    d.fy = null;
  }

  function renderGraph(animateNodeIds) {
    linkSelection = edgeLayer.selectAll('line.edge').data(state.edges, (d) => d.id);

    const linkEnter = linkSelection
      .enter()
      .append('line')
      .attr('class', 'edge')
      .style('opacity', 0);

    linkEnter.transition().duration(260).style('opacity', 1);

    linkSelection
      .exit()
      .transition()
      .duration(420)
      .style('opacity', 0)
      .remove();

    linkSelection = linkEnter
      .merge(linkSelection)
      .attr('class', (d) => `edge ${edgeClass(d)}`)
      .attr('stroke', (d) => edgeColor(d))
      .attr('stroke-width', (d) => edgeWidth(d))
      .attr('marker-end', (d) => edgeMarker(d));

    nodeSelection = nodeLayer.selectAll('g.node').data(state.nodes, (d) => d.id);

    const nodeEnter = nodeSelection
      .enter()
      .append('g')
      .attr('class', 'node')
      .style('opacity', 0)
      .on('mousemove', (event, d) => positionTooltip(event, d))
      .on('mouseleave', hideTooltip)
      .on('click', (_event, d) => {
        const attackId = d.attackId || (d.labels && d.labels['attack-id']);
        if (!attackId || getNodeCategory(d) !== 'decoy') {
          state.selectedAttackId = null;
          updateGroupHighlighting();
          return;
        }
        state.selectedAttackId = state.selectedAttackId === attackId ? null : attackId;
        updateGroupHighlighting();
      })
      .call(
        d3
          .drag()
          .on('start', dragStarted)
          .on('drag', dragged)
          .on('end', dragEnded)
      );

    nodeEnter.append('circle').attr('class', 'pulse-ring');
    nodeEnter.append('circle').attr('class', 'node-core');
    nodeEnter.append('path').attr('class', 'node-shape');
    nodeEnter.append('text').attr('class', 'node-icon');
    nodeEnter.append('text').attr('class', 'node-label');

    nodeSelection
      .exit()
      .transition()
      .duration(420)
      .style('opacity', 0)
      .remove();

    nodeSelection = nodeEnter.merge(nodeSelection);

    nodeSelection.each(function setNodeAppearance(d) {
      applyNodeShape(d3.select(this), d);
    });

    nodeSelection
      .filter((d) => animateNodeIds.has(d.id))
      .each((d) => {
        d.renderScale = 0.25;
      })
      .style('opacity', 0)
      .transition()
      .duration(700)
      .style('opacity', 1)
      .tween('scale-up', function tweenScale(d) {
        const interp = d3.interpolateNumber(d.renderScale || 0.25, 1);
        return (t) => {
          d.renderScale = interp(t);
        };
      });

    nodeSelection
      .filter((d) => !animateNodeIds.has(d.id))
      .transition()
      .duration(260)
      .style('opacity', 1)
      .each((d) => {
        d.renderScale = d.renderScale || 1;
      });

    simulation.nodes(state.nodes);
    simulation.force('link').links(state.edges);
    simulation.alpha(0.45).restart();

    updateGroupHighlighting();
    updateSummaryPanels();
  }

  function syncMaps() {
    state.nodeById = new Map(state.nodes.map((n) => [n.id, n]));
    state.edgeById = new Map(state.edges.map((e) => [e.id, e]));
  }

  function setGraph(nextNodes, nextEdges, animateNodeIds) {
    const previousNodes = state.nodeById;

    state.nodes = toUniqueById(nextNodes).map((node) => {
      const prev = previousNodes.get(node.id);
      if (prev) {
        node.x = prev.x;
        node.y = prev.y;
        node.vx = prev.vx;
        node.vy = prev.vy;
        node.renderScale = prev.renderScale || 1;
      } else {
        node.x = width / 2 + (Math.random() * 40 - 20);
        node.y = height / 2 + (Math.random() * 40 - 20);
        node.renderScale = node.renderScale || 1;
      }
      return node;
    });

    const validIds = new Set(state.nodes.map((n) => n.id));
    state.edges = toUniqueById(nextEdges).filter((edge) => {
      const source = endpointId(edge.source);
      const target = endpointId(edge.target);
      return validIds.has(source) && validIds.has(target);
    });

    syncMaps();
    renderGraph(animateNodeIds || new Set());
  }

  function addEventFeedEntry(levelClass, text, timestamp) {
    const line = document.createElement('div');
    line.className = `event-entry ${levelClass}`;
    line.textContent = `[${formatTime(timestamp || new Date().toISOString())}] ${text}`;

    dom.eventFeed.appendChild(line);

    while (dom.eventFeed.childElementCount > MAX_EVENT_FEED) {
      dom.eventFeed.removeChild(dom.eventFeed.firstElementChild);
    }

    dom.eventFeed.scrollTop = dom.eventFeed.scrollHeight;
    dom.eventCount.textContent = String(dom.eventFeed.childElementCount);
  }

  function classifyEvent(eventType, event) {
    if (eventType === 'attack_detected' || eventType === 'decoy_interaction') {
      return 'event-attack';
    }
    if (eventType === 'routing_update' || event.type === 'add_route' || event.type === 'remove_route') {
      return 'event-routing';
    }
    if (eventType === 'pod_update') {
      if (event.watch_type === 'MODIFIED') {
        return 'event-legit';
      }
      return 'event-system';
    }
    return 'event-system';
  }

  function eventSummary(eventType, event) {
    if (eventType === 'attack_detected') {
      return `ATTACK ${event.attack_type || 'unknown'} from ${event.source_ip || 'unknown ip'} confidence=${event.confidence || '?'}`;
    }
    if (eventType === 'decoy_spawned' && event.type === 'decoy_expired') {
      return `DECOY CLEANUP attack=${event.attack_id || '-'} reason=${event.reason || '-'}`;
    }
    if (eventType === 'decoy_spawned') {
      return `DECOY SPAWN attack=${event.attack_id || '-'} ip=${event.attacker_ip || '-'} pods=${(event.decoy_pods || []).length}`;
    }
    if (eventType === 'decoy_interaction') {
      return `DECOY INTERACTION attacker=${event.attacker_ip || '-'} target=${event.decoy_pod || event.pod_name || '-'}`;
    }
    if (eventType === 'pod_update') {
      return `POD ${event.watch_type || '-'} ${event.namespace || '-'} / ${event.pod_name || '-'} status=${event.status || '-'}`;
    }
    if (eventType === 'routing_update' || event.type === 'add_route' || event.type === 'remove_route') {
      return `ROUTING ${event.type || event.event_type} ip=${event.attacker_ip || '-'} attack=${event.attack_id || '-'}`;
    }
    return `${eventType.toUpperCase()} ${JSON.stringify(event).slice(0, 140)}`;
  }

  function spawnBurst(targetNodeId) {
    const target = state.nodeById.get(targetNodeId);
    if (!target) {
      return;
    }

    const burst = effectLayer
      .append('circle')
      .attr('class', 'attack-burst')
      .attr('cx', target.x || width / 2)
      .attr('cy', target.y || height / 2)
      .attr('r', 2)
      .style('opacity', 0.95);

    burst
      .transition()
      .duration(550)
      .attr('r', 46)
      .style('opacity', 0)
      .remove();
  }

  function pulseEdge(edgeId) {
    if (!linkSelection) {
      return;
    }

    const target = linkSelection.filter((d) => d.id === edgeId);
    if (target.empty()) {
      return;
    }

    target.classed('pulse-edge', true);
    setTimeout(() => {
      target.classed('pulse-edge', false);
    }, 900);
  }

  function flashAttacker(attackerId) {
    if (!nodeSelection) {
      return;
    }

    const target = nodeSelection.filter((d) => d.id === attackerId);
    if (target.empty()) {
      return;
    }

    target.classed('attacker-alert', true);
    setTimeout(() => {
      target.classed('attacker-alert', false);
    }, 1200);
  }

  function recomputeDecoySetCountFromGraph() {
    const ids = new Set(
      state.nodes
        .filter((node) => getNodeCategory(node) === 'decoy')
        .map((node) => node.attackId || (node.labels && node.labels['attack-id']))
        .filter(Boolean)
    );

    if (ids.size > 0 || state.stats.activeDecoySets === 0) {
      state.decoySetIds = ids;
    }

    state.stats.activeDecoySets = state.decoySetIds.size;
  }

  function applyGraphSnapshot(event) {
    const previousNodeIds = new Set(state.nodes.map((n) => n.id));

    const nodes = [];
    const edges = [];

    (event.nodes || []).forEach((rawNode) => {
      nodes.push(normalizeSnapshotNode(rawNode));
    });

    (event.edges || []).forEach((rawEdge) => {
      const normalized = normalizeSnapshotEdge(rawEdge);
      if (normalized) {
        edges.push(normalized);

        if (rawEdge.type === 'attacker_route' && rawEdge.attacker_ip) {
          const attackerId = ensureAttackerNode(rawEdge.attacker_ip, false);
          state.attackers.get(rawEdge.attacker_ip).attackIds.add(rawEdge.attack_id || '');

          edges.push({
            id: makeEdgeId(attackerId, endpointId(rawEdge.target), 'redirected_traffic', rawEdge.attacker_ip),
            source: attackerId,
            target: endpointId(rawEdge.target),
            type: 'redirected_traffic',
            rate: 1,
            attackerIp: rawEdge.attacker_ip,
          });
        }
      }
    });

    for (const attacker of state.attackers.values()) {
      if (!nodes.find((n) => n.id === attacker.id)) {
        nodes.push({
          id: attacker.id,
          name: `attacker ${attacker.ip}`,
          namespace: 'external',
          type: 'attacker',
          role: 'attacker',
          status: Date.now() <= attacker.activeUntil ? 'Active' : 'Observed',
          labels: {},
          ip: attacker.ip,
          createdAt: new Date(attacker.lastSeen).toISOString(),
          entityType: 'attacker',
        });
      }
    }

    pruneTransientEdges();
    for (const transientEdge of state.transientEdges.values()) {
      edges.push({ ...transientEdge });
    }

    const animateNodeIds = new Set(
      nodes.map((n) => n.id).filter((id) => !previousNodeIds.has(id))
    );

    setGraph(nodes, edges, animateNodeIds);
    recomputeDecoySetCountFromGraph();
    updateSummaryPanels();
  }

  function handleAttackDetected(event) {
    state.stats.attacksDetected += 1;

    const attackerIp = event.source_ip || event.attacker_ip;
    const attackerId = ensureAttackerNode(attackerIp, true);
    if (!attackerId) {
      return;
    }

    if (event.attack_id) {
      state.attackIdToIp.set(event.attack_id, attackerIp);
      const attacker = state.attackers.get(attackerIp);
      if (attacker) {
        attacker.attackIds.add(event.attack_id);
      }
    }

    const candidateTargets = [
      'service:deception-gateway:traffic-router',
      'service:ecommerce-real:frontend',
    ];

    let targetId = candidateTargets.find((id) => state.nodeById.has(id));
    if (!targetId) {
      const firstService = state.nodes.find((n) => getNodeCategory(n) === 'real' || getNodeCategory(n) === 'gateway');
      targetId = firstService ? firstService.id : null;
    }

    if (targetId) {
      const edgeId = makeEdgeId(attackerId, targetId, 'attack_traffic', attackerIp);
      const existing = state.edgeById.get(edgeId) || state.transientEdges.get(edgeId);
      const rate = (existing?.rate || 0) + 1;
      const edge = {
        id: edgeId,
        source: attackerId,
        target: targetId,
        type: 'attack_traffic',
        rate,
        expiresAt: Date.now() + TRANSIENT_EDGE_TTL_MS,
      };
      state.transientEdges.set(edgeId, edge);
      state.requestCounts.set(targetId, (state.requestCounts.get(targetId) || 0) + 1);
      state.requestCounts.set(attackerId, (state.requestCounts.get(attackerId) || 0) + 1);
      pulseEdge(edgeId);
      spawnBurst(targetId);
    }

    flashAttacker(attackerId);

    pruneTransientEdges();
    const mergedEdges = [...state.edges.filter((e) => !state.transientEdges.has(e.id)), ...state.transientEdges.values()];
    setGraph([...state.nodes], mergedEdges, new Set([attackerId]));
  }

  function handleDecoySpawned(event) {
    if (event.type === 'decoy_expired') {
      state.stats.decoysCleaned += 1;
      if (event.attack_id) {
        state.decoySetIds.delete(event.attack_id);
      }
      state.stats.activeDecoySets = state.decoySetIds.size;
      updateSummaryPanels();
      return;
    }

    state.stats.decoysSpawned += 1;

    const attackerIp = event.attacker_ip;
    const attackerId = ensureAttackerNode(attackerIp, false);

    if (event.attack_id) {
      state.decoySetIds.add(event.attack_id);
      state.attackIdToIp.set(event.attack_id, attackerIp);
      const attacker = state.attackers.get(attackerIp);
      if (attacker) {
        attacker.attackIds.add(event.attack_id);
      }
    }

    const newNodeIds = new Set();
    const nodes = [...state.nodes];
    const edges = [...state.edges];

    (event.decoy_pods || []).forEach((podName) => {
      const nodeId = `pod:decoy-pool:${podName}`;
      if (!state.nodeById.has(nodeId)) {
        nodes.push({
          id: nodeId,
          name: podName,
          namespace: 'decoy-pool',
          type: 'pod',
          role: 'decoy',
          status: 'Spawning',
          labels: {
            'attack-id': event.attack_id || '',
          },
          attackId: event.attack_id || null,
          createdAt: event.timestamp || new Date().toISOString(),
          renderScale: 0.25,
        });
        newNodeIds.add(nodeId);
      }

      if (attackerId) {
        const redirectEdge = {
          id: makeEdgeId(attackerId, nodeId, 'redirected_traffic', event.attack_id || attackerIp),
          source: attackerId,
          target: nodeId,
          type: 'redirected_traffic',
          rate: 1,
          expiresAt: Date.now() + TRANSIENT_EDGE_TTL_MS,
        };
        state.transientEdges.set(redirectEdge.id, redirectEdge);
      }
    });

    pruneTransientEdges();
    const merged = [...edges.filter((e) => !state.transientEdges.has(e.id)), ...state.transientEdges.values()];
    setGraph(nodes, merged, newNodeIds);

    state.stats.activeDecoySets = state.decoySetIds.size;
    updateSummaryPanels();
  }

  function handleDecoyInteraction(event) {
    const attackerIp = event.attacker_ip || event.source_ip;
    const attackerId = ensureAttackerNode(attackerIp, true);
    if (!attackerId) {
      return;
    }

    const decoyName = event.decoy_pod || event.pod_name || event.decoy_name;
    const targetId = event.target_node_id || (decoyName ? `pod:decoy-pool:${decoyName}` : null);
    if (!targetId) {
      return;
    }

    const edge = {
      id: makeEdgeId(attackerId, targetId, 'redirected_traffic', 'interaction'),
      source: attackerId,
      target: targetId,
      type: 'redirected_traffic',
      rate: 1,
      expiresAt: Date.now() + TRANSIENT_EDGE_TTL_MS,
    };

    state.transientEdges.set(edge.id, edge);
    pruneTransientEdges();

    const edges = [...state.edges.filter((e) => !state.transientEdges.has(e.id)), ...state.transientEdges.values()];
    setGraph([...state.nodes], edges, new Set());
    pulseEdge(edge.id);
    flashAttacker(attackerId);
  }

  function fadeOutAndRemoveNode(nodeId) {
    const target = nodeSelection ? nodeSelection.filter((d) => d.id === nodeId) : null;

    const removeData = () => {
      const nodes = state.nodes.filter((n) => n.id !== nodeId);
      const valid = new Set(nodes.map((n) => n.id));
      const edges = state.edges.filter((e) => valid.has(endpointId(e.source)) && valid.has(endpointId(e.target)));
      setGraph(nodes, edges, new Set());
      recomputeDecoySetCountFromGraph();
      updateSummaryPanels();
    };

    if (!target || target.empty()) {
      removeData();
      return;
    }

    target
      .transition()
      .duration(460)
      .style('opacity', 0)
      .on('end', removeData);
  }

  function handlePodUpdate(event) {
    const nodeId = `pod:${event.namespace}:${event.pod_name}`;

    if (event.watch_type === 'DELETED') {
      fadeOutAndRemoveNode(nodeId);
      return;
    }

    const labels = event.labels || {};
    const node = {
      id: nodeId,
      name: event.pod_name,
      namespace: normalizeNamespace(event.namespace),
      type: 'pod',
      role:
        labels.role ||
        (event.namespace === 'deception-gateway'
          ? 'gateway'
          : event.namespace === 'monitoring'
          ? 'monitoring'
          : event.namespace === 'decoy-pool'
          ? 'decoy'
          : 'real'),
      status: event.status || 'Unknown',
      labels,
      ip: event.ip || null,
      createdAt: event.timestamp || new Date().toISOString(),
      attackId: labels['attack-id'] || null,
      renderScale: 1,
    };

    const nodes = state.nodes.filter((n) => n.id !== nodeId);
    nodes.push(node);

    const edges = state.edges.filter((edge) => {
      const source = endpointId(edge.source);
      const target = endpointId(edge.target);
      return source !== nodeId && target !== nodeId ? true : true;
    });

    setGraph(nodes, edges, event.watch_type === 'ADDED' ? new Set([nodeId]) : new Set());
    recomputeDecoySetCountFromGraph();
  }

  function handleRoutingUpdate(event) {
    const type = event.type || event.event_type;
    if (type === 'add_route') {
      const attackerIp = event.attacker_ip;
      const attackerId = ensureAttackerNode(attackerIp, false);
      const targetService = parseTargetServiceFromEndpoint(event.frontend_service);
      if (attackerId && targetService) {
        const edge = {
          id: makeEdgeId(attackerId, targetService, 'redirected_traffic', attackerIp),
          source: attackerId,
          target: targetService,
          type: 'redirected_traffic',
          rate: 1,
          expiresAt: Date.now() + TRANSIENT_EDGE_TTL_MS,
        };
        state.transientEdges.set(edge.id, edge);
        pruneTransientEdges();

        const edges = [...state.edges.filter((e) => !state.transientEdges.has(e.id)), ...state.transientEdges.values()];
        setGraph([...state.nodes], edges, new Set([attackerId]));
      }
    }

    if (type === 'remove_route') {
      const attackerIp = event.attacker_ip || state.attackIdToIp.get(event.attack_id || '');
      if (attackerIp) {
        for (const [edgeId, edge] of state.transientEdges.entries()) {
          if (edge.type === 'redirected_traffic' && edgeId.includes(attackerIp)) {
            state.transientEdges.delete(edgeId);
          }
        }
      }
      const edges = [...state.edges.filter((e) => !state.transientEdges.has(e.id)), ...state.transientEdges.values()];
      setGraph([...state.nodes], edges, new Set());
    }
  }

  function processEvent(event, emitToFeed) {
    if (!event || typeof event !== 'object') {
      return;
    }

    const eventId = event.event_id;
    if (eventId && state.processedEventIds.has(eventId)) {
      return;
    }
    if (eventId) {
      state.processedEventIds.add(eventId);
    }

    const eventType = event.event_type || event.type || event.channel || 'unknown';

    if (eventType === 'graph_snapshot') {
      applyGraphSnapshot(event);
    } else if (eventType === 'attack_detected') {
      handleAttackDetected(event);
    } else if (eventType === 'decoy_spawned') {
      handleDecoySpawned(event);
    } else if (eventType === 'decoy_interaction') {
      handleDecoyInteraction(event);
    } else if (eventType === 'pod_update') {
      handlePodUpdate(event);
    } else if (eventType === 'routing_update' || event.type === 'add_route' || event.type === 'remove_route') {
      handleRoutingUpdate(event);
    }

    if (emitToFeed && eventType !== 'graph_snapshot') {
      const cls = classifyEvent(eventType, event);
      addEventFeedEntry(cls, eventSummary(eventType, event), event.timestamp);
    }

    state.stats.activeDecoySets = state.decoySetIds.size;
    updateSummaryPanels();
  }

  async function fetchConfig() {
    const res = await fetch('/config');
    if (!res.ok) {
      throw new Error('Failed to fetch /config');
    }
    return res.json();
  }

  function apiUrl(path) {
    const root = state.config.eventCollectorApi.replace(/\/$/, '');
    return `${root}${path}`;
  }

  async function loadRecentEvents() {
    try {
      const res = await fetch(apiUrl('/api/events/recent'));
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const payload = await res.json();
      const events = Array.isArray(payload.events) ? payload.events : [];
      events.forEach((event) => processEvent(event, false));
      addEventFeedEntry('event-system', `Loaded ${events.length} recent events`, new Date().toISOString());
    } catch (err) {
      addEventFeedEntry('event-system', `Failed to load recent events: ${err.message}`, new Date().toISOString());
    }
  }

  function connectWebSocket() {
    if (state.ws) {
      state.ws.close();
    }

    setConnectionStatus('CONNECTING', 'status-warn');
    const ws = new WebSocket(state.config.eventCollectorWs);
    state.ws = ws;

    ws.onopen = () => {
      setConnectionStatus('LIVE', 'status-ok');
      addEventFeedEntry('event-system', 'WebSocket connected', new Date().toISOString());
    };

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        processEvent(event, true);
      } catch (err) {
        addEventFeedEntry('event-system', `Invalid WebSocket payload: ${err.message}`, new Date().toISOString());
      }
    };

    ws.onclose = () => {
      setConnectionStatus('RECONNECTING', 'status-warn');
      setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = () => {
      setConnectionStatus('DISCONNECTED', 'status-down');
    };
  }

  function initSvg() {
    svg = d3.select('#graph-svg');

    const defs = svg.append('defs');

    defs
      .append('marker')
      .attr('id', 'arrow-attack')
      .attr('viewBox', '0 0 10 10')
      .attr('refX', 11)
      .attr('refY', 5)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto-start-reverse')
      .append('path')
      .attr('d', 'M0,0 L10,5 L0,10 z')
      .attr('fill', '#ff4d5f');

    defs
      .append('marker')
      .attr('id', 'arrow-redirect')
      .attr('viewBox', '0 0 10 10')
      .attr('refX', 11)
      .attr('refY', 5)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto-start-reverse')
      .append('path')
      .attr('d', 'M0,0 L10,5 L0,10 z')
      .attr('fill', '#f4b942');

    zoomLayer = svg.append('g').attr('class', 'zoom-layer');
    edgeLayer = zoomLayer.append('g').attr('class', 'edge-layer');
    nodeLayer = zoomLayer.append('g').attr('class', 'node-layer');
    effectLayer = zoomLayer.append('g').attr('class', 'effect-layer');

    svg.call(
      d3
        .zoom()
        .scaleExtent([0.3, 3.0])
        .on('zoom', (event) => {
          zoomLayer.attr('transform', event.transform);
        })
    );

    simulation = d3
      .forceSimulation([])
      .force(
        'link',
        d3
          .forceLink([])
          .id((d) => d.id)
          .distance((d) => {
            if (d.type === 'internal_mesh') {
              return 42;
            }
            if (d.type === 'legitimate_traffic') {
              return 80;
            }
            if (d.type === 'attack_traffic') {
              return 115;
            }
            return 95;
          })
          .strength(0.42)
      )
      .force('charge', d3.forceManyBody().strength(-260))
      .force('collision', d3.forceCollide().radius((d) => nodeRadius(d) + 8).iterations(2))
      .force('x', d3.forceX((d) => namespaceTargetX(d.namespace)).strength(0.22))
      .force('y', d3.forceY((d) => roleTargetY(d)).strength(0.22))
      .force('center', d3.forceCenter(600, 320));

    simulation.on('tick', () => {
      if (linkSelection) {
        linkSelection
          .attr('x1', (d) => d.source.x)
          .attr('y1', (d) => d.source.y)
          .attr('x2', (d) => d.target.x)
          .attr('y2', (d) => d.target.y);
      }

      if (nodeSelection) {
        nodeSelection.attr(
          'transform',
          (d) => `translate(${d.x || 0},${d.y || 0}) scale(${d.renderScale || 1})`
        );
      }
    });

    const resize = () => {
      const rect = document.getElementById('graph-wrap').getBoundingClientRect();
      width = Math.max(320, rect.width);
      height = Math.max(260, rect.height);

      svg.attr('viewBox', `0 0 ${width} ${height}`);

      simulation.force('center', d3.forceCenter(width / 2, height / 2));
      simulation.force('x', d3.forceX((d) => namespaceTargetX(d.namespace)).strength(0.22));
      simulation.force('y', d3.forceY((d) => roleTargetY(d)).strength(0.22));
      simulation.alpha(0.25).restart();
    };

    window.addEventListener('resize', resize);
    resize();
  }

  async function init() {
    setConnectionStatus('CONNECTING', 'status-warn');
    updateClock();
    setInterval(updateClock, 1000);

    initSvg();

    try {
      state.config = await fetchConfig();
    } catch (err) {
      setConnectionStatus('CONFIG ERROR', 'status-down');
      addEventFeedEntry('event-system', `Config fetch error: ${err.message}`, new Date().toISOString());
      return;
    }

    await loadRecentEvents();
    connectWebSocket();
    updateSummaryPanels();
  }

  init();
})();
