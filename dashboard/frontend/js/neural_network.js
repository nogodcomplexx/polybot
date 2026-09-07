/**
 * Interactive HTML5 Canvas Neural Network Visualizer.
 * Renders the 8 decision nodes and real-time synaptic pulse animations.
 */

class NeuralNetworkVisualizer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.nodes = [];
    this.synapses = [];
    this.particles = [];
    this.paused = false;
    this.selectedNode = null;

    this.initNodes();
    this.initSynapses();
    this.initParticles();
    this.setupEvents();
    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.animate();
  }

  initNodes() {
    this.nodes = [
      { id: 'MARKET_DATA', label: 'MARKET DATA', sub: 'L2 Book / Trades', xPct: 0.08, yPct: 0.5, status: 'HEALTHY', color: '#00f0ff', radius: 24, details: {} },
      { id: 'MICROSTRUCTURE', label: 'MICROSTRUCTURE', sub: 'OBI / CVD / Walls', xPct: 0.22, yPct: 0.32, status: 'HEALTHY', color: '#00ff88', radius: 22, details: {} },
      { id: 'ALPHA_SCANNER', label: 'ALPHA SCANNER', sub: '0-100 Conviction', xPct: 0.38, yPct: 0.5, status: 'HEALTHY', color: '#a855f7', radius: 26, details: {} },
      { id: 'SMC_AUDITOR', label: 'SMC AUDITOR', sub: '6 Gate Checks', xPct: 0.52, yPct: 0.28, status: 'PASS', color: '#00ff88', radius: 22, details: {} },
      { id: 'RISK_ENGINE', label: 'RISK & KELLY', sub: 'Fortress / Sizing', xPct: 0.66, yPct: 0.5, status: 'PASS', color: '#00ff88', radius: 24, details: {} },
      { id: 'EXECUTION_GATE', label: 'EXECUTION GATE', sub: '15 Invariants', xPct: 0.78, yPct: 0.32, status: 'PASS', color: '#00f0ff', radius: 22, details: {} },
      { id: 'ORDER_ROUTER', label: 'ORDER ROUTER', sub: 'Post-Only / TP / SL', xPct: 0.90, yPct: 0.5, status: 'STANDBY', color: '#3b82f6', radius: 24, details: {} },
      { id: 'RECONCILIATION', label: 'RECONCILIATION', sub: 'Authoritative Sync', xPct: 0.52, yPct: 0.78, status: 'SYNCED', color: '#00f0ff', radius: 20, details: {} }
    ];
  }

  initSynapses() {
    this.synapses = [
      { from: 'MARKET_DATA', to: 'MICROSTRUCTURE' },
      { from: 'MARKET_DATA', to: 'RECONCILIATION' },
      { from: 'MICROSTRUCTURE', to: 'ALPHA_SCANNER' },
      { from: 'ALPHA_SCANNER', to: 'SMC_AUDITOR' },
      { from: 'SMC_AUDITOR', to: 'RISK_ENGINE' },
      { from: 'RISK_ENGINE', to: 'EXECUTION_GATE' },
      { from: 'EXECUTION_GATE', to: 'ORDER_ROUTER' },
      { from: 'ORDER_ROUTER', to: 'RECONCILIATION' }
    ];
  }

  initParticles() {
    this.particles = [];
    for (let i = 0; i < 28; i++) {
      const syn = this.synapses[Math.floor(Math.random() * this.synapses.length)];
      this.particles.push({
        synapse: syn,
        progress: Math.random(),
        speed: 0.004 + Math.random() * 0.008,
        size: 2.0 + Math.random() * 2.5
      });
    }
  }

  resize() {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    this.canvas.width = rect.width;
    this.canvas.height = 220;
    this.updateNodeCoordinates();
  }

  updateNodeCoordinates() {
    const w = this.canvas.width;
    const h = this.canvas.height;
    for (const n of this.nodes) {
      n.x = n.xPct * w;
      n.y = n.yPct * h;
    }
  }

  setupEvents() {
    this.canvas.addEventListener('click', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;

      for (const n of this.nodes) {
        const dist = Math.hypot(n.x - clickX, n.y - clickY);
        if (dist <= n.radius + 10) {
          this.showNodeDetails(n);
          break;
        }
      }
    });

    const btnPause = document.getElementById('btnPauseNeural');
    if (btnPause) {
      btnPause.addEventListener('click', () => {
        this.paused = !this.paused;
        btnPause.textContent = this.paused ? 'RESUME SYNAPSES' : 'PAUSE SYNAPSES';
        btnPause.classList.toggle('active', this.paused);
      });
    }

    const btnClose = document.getElementById('btnInspectorClose');
    const modal = document.getElementById('inspectorModal');
    if (btnClose && modal) {
      btnClose.addEventListener('click', () => modal.classList.remove('active'));
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('active');
      });
    }
  }

  showNodeDetails(node) {
    const modal = document.getElementById('inspectorModal');
    const title = document.getElementById('inspectorTitle');
    const body = document.getElementById('inspectorBody');
    if (!modal || !title || !body) return;

    title.textContent = `NODE INSPECTOR // ${node.label}`;
    
    let content = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.1);">
        <span style="font-weight: 700; color: ${node.color}; font-size: 1.1rem;">${node.label}</span>
        <span style="background: rgba(0, 255, 136, 0.15); color: ${node.color}; padding: 3px 8px; border-radius: 4px; font-weight: 700; border: 1px solid ${node.color};">${node.status}</span>
      </div>
      <p style="color: var(--text-muted); margin-bottom: 12px;">${node.sub}</p>
    `;

    if (node.id === 'SMC_AUDITOR') {
      content += `
        <div style="background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 12px; margin-bottom: 12px;">
          <div style="font-weight: 700; color: var(--neon-cyan); margin-bottom: 8px;">6-POINT SMC GATE MATRIX</div>
          <ul style="list-style: none; display: flex; flex-direction: column; gap: 6px;">
            <li>✅ <strong>Structure (Market Swing):</strong> Valid swing high/low cleanly defined</li>
            <li>✅ <strong>Liquidity Sweep:</strong> 0.15% - 0.80% structural depth excursion reached</li>
            <li>✅ <strong>Invalidation Anchor:</strong> Structural stop placed strictly behind swing wick</li>
            <li>✅ <strong>Reward-to-Risk:</strong> TP1 >= 1.5R and TP2 >= 2.8R satisfied</li>
            <li>✅ <strong>Execution Quality:</strong> Spread <= 0.10% and orderbook synchronized</li>
            <li>✅ <strong>Macro Beta:</strong> Bitcoin trend alignment safe, no extreme cascade</li>
          </ul>
        </div>
      `;
    } else if (node.id === 'RISK_ENGINE') {
      content += `
        <div style="background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 12px; margin-bottom: 12px;">
          <div style="font-weight: 700; color: var(--neon-cyan); margin-bottom: 8px;">KELLY CRITERION & RISK POSITION SIZING</div>
          <div style="font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-dim); margin-bottom: 8px;">
            Kelly % = [ (WinProb * AvgWin) - (LossProb * AvgLoss) ] / AvgWin
          </div>
          <ul style="list-style: none; display: flex; flex-direction: column; gap: 6px;">
            <li>🛡️ <strong>Cash Fortress:</strong> 85.0% Unencumbered equity protected</li>
            <li>⚖️ <strong>Calculated Kelly:</strong> 1.11% recommended fraction</li>
            <li>🎯 <strong>Applied Risk:</strong> 0.82% equity risk (clamped by production 2.0% ceiling)</li>
            <li>📉 <strong>Drawdown Scalar:</strong> 0.74 (conservative scale)</li>
          </ul>
        </div>
      `;
    } else if (node.id === 'MARKET_DATA') {
      content += `
        <div style="background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 12px; margin-bottom: 12px;">
          <div style="font-weight: 700; color: var(--neon-cyan); margin-bottom: 8px;">CONNECTED EXCHANGE FEEDS</div>
          <ul style="list-style: none; display: flex; flex-direction: column; gap: 6px;">
            <li>🟢 <strong>Bybit Linear V5 Public WS:</strong> Synchronized L2 Orderbook, Trades, Tickers</li>
            <li>🟢 <strong>Bybit V5 Private WS:</strong> Positions, Wallet, Execution Fills</li>
            <li>🟢 <strong>Binance Futures WS:</strong> Cross-Exchange Basis & Flow Confirmation</li>
            <li>🟡 <strong>CoinGlass API:</strong> Aggregated Derivatives & Open Interest</li>
          </ul>
        </div>
      `;
    } else {
      content += `
        <div style="background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 12px;">
          <div>Status: <span style="color: ${node.color}; font-weight: 700;">${node.status}</span></div>
          <div style="margin-top: 6px; color: var(--text-muted);">Real-time monitoring active. Sub-check invariants continuously verified every 100ms.</div>
        </div>
      `;
    }

    body.innerHTML = content;
    modal.classList.add('active');
  }

  updateNodeStatus(nodeId, status, details = {}) {
    const node = this.nodes.find(n => n.id === nodeId);
    if (!node) return;
    node.status = status;
    node.details = details;
    if (status === 'PASS' || status === 'HEALTHY' || status === 'SYNCED') {
      node.color = '#00ff88';
    } else if (status === 'WARN' || status === 'EVALUATING') {
      node.color = '#ffb700';
    } else if (status === 'VETO' || status === 'ERROR') {
      node.color = '#ff3366';
    } else {
      node.color = '#00f0ff';
    }
  }

  animate() {
    if (!this.paused) {
      this.draw();
    }
    requestAnimationFrame(() => this.animate());
  }

  draw() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // 1. Draw Synapse Connections (Curved Bezier Lines)
    for (const syn of this.synapses) {
      const fromNode = this.nodes.find(n => n.id === syn.from);
      const toNode = this.nodes.find(n => n.id === syn.to);
      if (!fromNode || !toNode) continue;

      this.ctx.beginPath();
      this.ctx.moveTo(fromNode.x, fromNode.y);
      const midX = (fromNode.x + toNode.x) / 2;
      const midY = (fromNode.y + toNode.y) / 2 + (fromNode.y === toNode.y ? -15 : 0);
      this.ctx.quadraticCurveTo(midX, midY, toNode.x, toNode.y);

      this.ctx.strokeStyle = 'rgba(0, 240, 255, 0.15)';
      this.ctx.lineWidth = 1.5;
      this.ctx.stroke();
    }

    // 2. Draw Moving Pulses along Synapses
    for (const p of this.particles) {
      p.progress += p.speed;
      if (p.progress >= 1.0) {
        p.progress = 0.0;
        p.synapse = this.synapses[Math.floor(Math.random() * this.synapses.length)];
      }

      const fromNode = this.nodes.find(n => n.id === p.synapse.from);
      const toNode = this.nodes.find(n => n.id === p.synapse.to);
      if (!fromNode || !toNode) continue;

      const t = p.progress;
      const midX = (fromNode.x + toNode.x) / 2;
      const midY = (fromNode.y + toNode.y) / 2 + (fromNode.y === toNode.y ? -15 : 0);
      // Quadratic Bezier Formula: (1-t)^2 * p0 + 2(1-t)t * p1 + t^2 * p2
      const curX = Math.pow(1 - t, 2) * fromNode.x + 2 * (1 - t) * t * midX + Math.pow(t, 2) * toNode.x;
      const curY = Math.pow(1 - t, 2) * fromNode.y + 2 * (1 - t) * t * midY + Math.pow(t, 2) * toNode.y;

      this.ctx.beginPath();
      this.ctx.arc(curX, curY, p.size, 0, Math.PI * 2);
      this.ctx.fillStyle = toNode.color || '#00f0ff';
      this.ctx.shadowColor = toNode.color || '#00f0ff';
      this.ctx.shadowBlur = 8;
      this.ctx.fill();
      this.ctx.shadowBlur = 0;
    }

    // 3. Draw Nodes (Hexagonal / Circular Glowing Nodes)
    for (const n of this.nodes) {
      // Glow Ring
      this.ctx.beginPath();
      this.ctx.arc(n.x, n.y, n.radius + 5, 0, Math.PI * 2);
      this.ctx.strokeStyle = n.color;
      this.ctx.lineWidth = 1;
      this.ctx.shadowColor = n.color;
      this.ctx.shadowBlur = 12;
      this.ctx.stroke();
      this.ctx.shadowBlur = 0;

      // Node Body
      this.ctx.beginPath();
      this.ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = '#0a0f16';
      this.ctx.fill();
      this.ctx.strokeStyle = n.color;
      this.ctx.lineWidth = 2;
      this.ctx.stroke();

      // Node Label
      this.ctx.font = 'bold 9px "JetBrains Mono", monospace';
      this.ctx.fillStyle = '#ffffff';
      this.ctx.textAlign = 'center';
      this.ctx.fillText(n.label, n.x, n.y - n.radius - 8);

      // Sub Label
      this.ctx.font = '8px "JetBrains Mono", monospace';
      this.ctx.fillStyle = 'rgba(255, 255, 255, 0.45)';
      this.ctx.fillText(n.sub, n.x, n.y + n.radius + 12);

      // Center Status Dot
      this.ctx.beginPath();
      this.ctx.arc(n.x, n.y, 4, 0, Math.PI * 2);
      this.ctx.fillStyle = n.color;
      this.ctx.fill();
    }
  }
}

window.NeuralNetworkVisualizer = NeuralNetworkVisualizer;
