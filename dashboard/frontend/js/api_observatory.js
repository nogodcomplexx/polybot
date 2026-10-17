/**
 * API Observatory Client Component.
 * Streams real-time API and WebSocket pings, latencies, and venue metrics.
 */

class ApiObservatoryComponent {
  constructor() {
    this.tbody = document.getElementById('apiStreamBody');
    this.maxRows = 25;
  }

  handlePing(ping) {
    if (!this.tbody) return;

    const row = document.createElement('tr');
    const timeStr = new Date(ping.timestamp * 1000).toLocaleTimeString();

    let latencyClass = 'green';
    if (ping.latency_ms > 120) latencyClass = 'red';
    else if (ping.latency_ms > 60) latencyClass = 'amber';
    else if (ping.latency_ms > 25) latencyClass = 'cyan';

    row.innerHTML = `
      <td style="color: var(--text-dim);">${timeStr}</td>
      <td style="font-weight: 700;">${ping.venue}</td>
      <td style="color: var(--neon-cyan);">${ping.endpoint}</td>
      <td><span class="latency-pill ${latencyClass}">${ping.latency_ms}ms</span></td>
      <td style="color: var(--text-muted);">${ping.status}</td>
    `;

    this.tbody.insertBefore(row, this.tbody.firstChild);
    while (this.tbody.children.length > this.maxRows) {
      this.tbody.removeChild(this.tbody.lastChild);
    }
  }

  updateStats(stats) {
    if (!stats || !stats.venues) return;

    const bybit = stats.venues.bybit;
    if (bybit) {
      const elPing = document.getElementById('bybitWsLatency');
      const elHdr = document.getElementById('headerBybitPing');
      const elLimit = document.getElementById('bybitRateLimit');
      if (elPing) elPing.textContent = `${bybit.ws_latency_ms}ms`;
      if (elHdr) elHdr.textContent = `${bybit.ws_latency_ms}ms`;
      if (elLimit) elLimit.textContent = `Limit: ${bybit.rate_limit_pct}%`;
    }

    const binance = stats.venues.binance;
    if (binance) {
      const elPing = document.getElementById('binanceWsLatency');
      const elHdr = document.getElementById('headerBinancePing');
      const elMsgs = document.getElementById('binanceMsgs');
      if (elPing) elPing.textContent = `${binance.ws_latency_ms}ms`;
      if (elHdr) elHdr.textContent = `${binance.ws_latency_ms}ms`;
      if (elMsgs) elMsgs.textContent = `${binance.messages_per_sec} msg/s`;
    }

    const coinglass = stats.venues.coinglass;
    if (coinglass) {
      const elPing = document.getElementById('coinglassLatency');
      const elHdr = document.getElementById('headerCoinGlassPing');
      const elErrors = document.getElementById('coinglassErrors');
      if (elPing) elPing.textContent = `${coinglass.rest_latency_ms}ms`;
      if (elHdr) elHdr.textContent = `${coinglass.rest_latency_ms}ms`;
      if (elErrors) elErrors.textContent = `${coinglass.errors} errs`;
    }
  }

  hydrate(recentPings) {
    if (!this.tbody || !Array.isArray(recentPings)) return;
    this.tbody.innerHTML = '';
    for (const ping of recentPings) {
      const row = document.createElement('tr');
      const timeStr = new Date(ping.timestamp * 1000).toLocaleTimeString();

      let latencyClass = 'green';
      if (ping.latency_ms > 120) latencyClass = 'red';
      else if (ping.latency_ms > 60) latencyClass = 'amber';
      else if (ping.latency_ms > 25) latencyClass = 'cyan';

      row.innerHTML = `
        <td style="color: var(--text-dim);">${timeStr}</td>
        <td style="font-weight: 700;">${ping.venue}</td>
        <td style="color: var(--neon-cyan);">${ping.endpoint}</td>
        <td><span class="latency-pill ${latencyClass}">${ping.latency_ms}ms</span></td>
        <td style="color: var(--text-muted);">${ping.status}</td>
      `;
      this.tbody.appendChild(row);
    }
  }
}

window.ApiObservatoryComponent = ApiObservatoryComponent;
