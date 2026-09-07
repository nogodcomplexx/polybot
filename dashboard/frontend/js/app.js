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

    if (type === 'INITIAL_HYDRATION') {
      if (msg.state) {
        if (msg.state.SYSTEM_STATUS) hud.updateSystem(msg.state.SYSTEM_STATUS);
        if (msg.state.MARKET_TELEMETRY) marketBrain.update(msg.state.MARKET_TELEMETRY);
        if (msg.state.BRAIN_TELEMETRY) brainPanel.update(msg.state.BRAIN_TELEMETRY);
        if (msg.state.EXECUTION_TELEMETRY) hud.updatePosition(msg.state.EXECUTION_TELEMETRY);
      }
      if (msg.traces) decisionTrace.hydrate(msg.traces);
      if (msg.api_stats) apiObs.updateStats(msg.api_stats);
    } else if (type === 'SYSTEM_STATUS') {
      hud.updateSystem(msg);
      neuralNet.updateNodeStatus('RECONCILIATION', msg.bot_status === 'ONLINE' ? 'SYNCED' : 'WARN');
    } else if (type === 'MARKET_TELEMETRY') {
      marketBrain.update(msg);
      neuralNet.updateNodeStatus('MARKET_DATA', msg.orderbook_valid ? 'HEALTHY' : 'ERROR');
      neuralNet.updateNodeStatus('MICROSTRUCTURE', msg.absorption_status === 'HIGH' ? 'PASS' : 'HEALTHY');
    } else if (type === 'BRAIN_TELEMETRY') {
      brainPanel.update(msg);
      const isTrade = msg.final_decision === 'TRADE';
      neuralNet.updateNodeStatus('ALPHA_SCANNER', msg.alpha_score >= 70.0 ? 'PASS' : 'EVALUATING');
      neuralNet.updateNodeStatus('SMC_AUDITOR', isTrade ? 'PASS' : 'VETO');
      neuralNet.updateNodeStatus('RISK_ENGINE', isTrade ? 'PASS' : 'WARN');
    } else if (type === 'EXECUTION_TELEMETRY') {
      hud.updatePosition(msg);
      const hasPos = msg.position_state !== 'FLAT' && msg.position_state !== 'CLOSED';
      neuralNet.updateNodeStatus('ORDER_ROUTER', hasPos ? 'PASS' : 'STANDBY');
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
