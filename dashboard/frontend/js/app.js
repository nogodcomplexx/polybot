/**
 * Master Application Coordinator & WebSocket Client.
 * Connects all UI components to the real-time telemetry stream.
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Initialize Sub-Components
  const neuralNet = new NeuralNetworkVisualizer('neuralCanvas');
  const apiObs = new ApiObservatoryComponent();
  const brainPanel = new BrainPanelComponent();
  const decisionTrace = new DecisionTraceComponent();
  const marketBrain = new MarketBrainComponent();
  const hud = new HudComponent();
  const alerts = new AlertCenterComponent();
  const roiPanel = new RoiPanelComponent();
  const tvChart = new TradingViewComponent();
  const forexFactory = new ForexFactoryComponent();

  // Bloomberg Terminal Real-Time UTC Clock
  function updateBloombergClock() {
    const el = document.getElementById('bbClockUtc');
    if (el) {
      const now = new Date();
      el.textContent = `${now.toISOString().slice(0, 10)} ${now.toTimeString().slice(0, 8)} UTC`;
    }
  }
  setInterval(updateBloombergClock, 1000);
  updateBloombergClock();

  // Audio Ping via Web Audio API
  let audioPingEnabled = false;
  let audioCtx = null;

  const btnAudio = document.getElementById('btnAudioPing');
  if (btnAudio) {
    btnAudio.addEventListener('click', () => {
      audioPingEnabled = !audioPingEnabled;
      btnAudio.textContent = audioPingEnabled ? '🔊 AUDIO PING: ON' : '🔊 AUDIO PING: OFF';
      btnAudio.classList.toggle('active', audioPingEnabled);
      if (audioPingEnabled && !audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
    });
  }

  function playPingSound(freq = 880, duration = 0.04) {
    if (!audioPingEnabled || !audioCtx) return;
    try {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.05, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    } catch (e) {}
  }

  // 2. WebSocket Connection Management
  let ws = null;
  let reconnectInterval = 1000;

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`Connecting to Telemetry WebSocket: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('✅ Telemetry WebSocket connected.');
      reconnectInterval = 1000;
      const elStatus = document.getElementById('botStatusText');
      if (elStatus) elStatus.textContent = 'CONNECTED';
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (err) {
        console.error('Error parsing telemetry message:', err);
      }
    };

    ws.onclose = () => {
      console.warn(`WebSocket closed. Retrying in ${reconnectInterval / 1000}s...`);
      const elBadge = document.getElementById('botStatusBadge');
      const elStatus = document.getElementById('botStatusText');
      if (elBadge && elStatus) {
        elBadge.className = 'bot-badge unknown';
        elStatus.textContent = 'DISCONNECTED (RECONNECTING...)';
      }
      setTimeout(connectWebSocket, reconnectInterval);
      reconnectInterval = Math.min(reconnectInterval * 1.5, 8000);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      ws.close();
    };
  }

  function handleMessage(msg) {
    const type = msg.type;

    const latestTickers = {};

    function updateTickerTrack(mkt) {
      if (!mkt || !mkt.symbol) return;
      latestTickers[mkt.symbol] = {
        price: mkt.last_price,
        regime: mkt.regime || 'RANGE',
        basis_pct: mkt.basis_pct || 0.0,
      };
      const track = document.querySelector('.bb-ticker-track');
      if (!track) return;

      let itemsHtml = '';
      for (const [sym, info] of Object.entries(latestTickers)) {
        const pStr = tvChart.formatPrice(info.price);
        const sign = info.basis_pct >= 0 ? '+' : '';
        const bClass = info.basis_pct >= 0 ? 'bb-green' : 'bb-red';
        itemsHtml += `<span class="bb-tick-item"><strong class="bb-amber">${sym}.P</strong> $${pStr} <span class="${bClass}">${sign}${info.basis_pct.toFixed(2)}%</span> <span class="bb-dim">[${info.regime}]</span></span>`;
      }
      itemsHtml += `<span class="bb-tick-item"><strong class="bb-amber">DESK ENGINE</strong> <span class="bb-green">ONLINE</span> | RECONCILE: <span class="bb-cyan">SYNCED</span></span>`;
      itemsHtml += `<span class="bb-tick-item"><strong class="bb-amber">RISK PARITY</strong> CASH FORTRESS: <span class="bb-green">85.0%+</span> | KILL SWITCH: <span class="bb-green">DISARMED</span></span>`;
      track.innerHTML = itemsHtml;
    }

    if (type === 'INITIAL_HYDRATION') {
      if (msg.mode) {
        const elMode = document.getElementById('deskModeBadge');
        if (elMode) {
          const modeStr = String(msg.mode).toUpperCase();
          elMode.textContent = `MODE: ${modeStr}`;
          elMode.className = modeStr === 'LIVE' ? 'bb-pill bb-red' : (modeStr === 'DEMO' ? 'bb-pill bb-amber' : 'bb-pill bb-cyan');
        }
      }
      if (msg.state) {
        if (msg.state.SYSTEM_STATUS) {
          hud.updateSystem(msg.state.SYSTEM_STATUS);
          neuralNet.setSystemStatus(msg.state.SYSTEM_STATUS);
        }
        if (msg.state.MARKET_TELEMETRY) {
          marketBrain.update(msg.state.MARKET_TELEMETRY);
          tvChart.setCandidateSymbol(msg.state.MARKET_TELEMETRY.symbol);
          updateTickerTrack(msg.state.MARKET_TELEMETRY);
        }
        if (msg.state.BRAIN_TELEMETRY) {
          brainPanel.update(msg.state.BRAIN_TELEMETRY);
          if (msg.state.BRAIN_TELEMETRY.symbol) tvChart.setCandidateSymbol(msg.state.BRAIN_TELEMETRY.symbol);
        }
        if (msg.state.EXECUTION_TELEMETRY) {
          hud.updatePosition(msg.state.EXECUTION_TELEMETRY);
          const actStates = ['ACTIVE', 'PROTECTION_PENDING', 'TP1_FILLED', 'PROTECTED_RUNNER', 'DAMAGE_CONTROL', 'EXITING', 'PARTIALLY_FILLED'];
          const isAct = actStates.includes(msg.state.EXECUTION_TELEMETRY.position_state);
          if (isAct && msg.state.EXECUTION_TELEMETRY.active_position && msg.state.EXECUTION_TELEMETRY.active_position.symbol) {
            tvChart.setBotSymbol(msg.state.EXECUTION_TELEMETRY.active_position.symbol, msg.state.EXECUTION_TELEMETRY.active_position);
          }
        }
        if (msg.state.PERFORMANCE_TELEMETRY) roiPanel.update(msg.state.PERFORMANCE_TELEMETRY);
      }
      if (msg.traces) decisionTrace.hydrate(msg.traces);
      if (msg.api_stats) {
        apiObs.updateStats(msg.api_stats);
        if (msg.api_stats.recent_pings) apiObs.hydrate(msg.api_stats.recent_pings);
      }
    } else if (type === 'SYSTEM_STATUS') {
      hud.updateSystem(msg);
      neuralNet.setSystemStatus(msg);
      neuralNet.updateNodeStatus('RECONCILIATION', msg.bot_status === 'ONLINE' ? 'SYNCED' : 'WARN');
    } else if (type === 'MARKET_TELEMETRY') {
      marketBrain.update(msg);
      tvChart.setCandidateSymbol(msg.symbol);
      updateTickerTrack(msg);
      neuralNet.updateNodeStatus('MARKET_DATA', msg.orderbook_valid ? 'HEALTHY' : 'ERROR');
      neuralNet.updateNodeStatus('MICROSTRUCTURE', msg.absorption_status === 'HIGH' ? 'PASS' : 'HEALTHY');
    } else if (type === 'BRAIN_TELEMETRY') {
      brainPanel.update(msg);
      if (msg.symbol) tvChart.setCandidateSymbol(msg.symbol);
      const isTrade = msg.final_decision === 'TRADE';
      neuralNet.updateNodeStatus('ALPHA_SCANNER', msg.alpha_score >= 70.0 ? 'PASS' : 'EVALUATING');
      neuralNet.updateNodeStatus('SMC_AUDITOR', isTrade ? 'PASS' : 'VETO');
      neuralNet.updateNodeStatus('RISK_ENGINE', isTrade ? 'PASS' : 'WARN');
    } else if (type === 'EXECUTION_TELEMETRY') {
      hud.updatePosition(msg);
      const activeStates = ['ACTIVE', 'PROTECTION_PENDING', 'TP1_FILLED', 'PROTECTED_RUNNER', 'DAMAGE_CONTROL', 'EXITING', 'PARTIALLY_FILLED'];
      const hasPos = activeStates.includes(msg.position_state);
      if (hasPos && msg.active_position && msg.active_position.symbol) {
        tvChart.setBotSymbol(msg.active_position.symbol, msg.active_position);
      } else {
        tvChart.updatePositionOverlay(null);
      }
      neuralNet.updateNodeStatus('ORDER_ROUTER', hasPos ? 'PASS' : 'STANDBY');
    } else if (type === 'PERFORMANCE_TELEMETRY') {
      roiPanel.update(msg);
    } else if (type === 'DECISION_TRACE') {
      decisionTrace.addStep(msg);
    } else if (type === 'API_PING') {
      apiObs.handlePing(msg);
      playPingSound(msg.venue === 'Bybit' ? 920 : 680);
    } else if (type === 'ALERT') {
      alerts.addAlert(msg);
    }
  }

  // Connect on load
  connectWebSocket();
});
