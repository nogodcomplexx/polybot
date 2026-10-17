/**
 * TradingView Real-Time Chart Component (Bloomberg Terminal Edition)
 * Dynamically synchronizes chart symbol to the bot's currently traded pair or active candidate.
 */

class TradingViewComponent {
  constructor() {
    this.currentSymbol = 'BTCUSDT';
    this.autoTrack = true;
    this.activePosition = null;
    this.tvWidget = null;
    this.lastRenderedSymbol = null;
    this.lastCandidateSwitchTs = 0;
    this.candidateSwitchCooldownMs = 20000; // 20s debounce to prevent iframe thrashing during scanning

    this.statusEl = document.getElementById('tvChartStatus');
    this.symbolTitleEl = document.getElementById('tvChartSymbolTitle');
    this.btnAutoTrack = document.getElementById('btnTvAutoTrack');
    this.posOverlay = document.getElementById('tvPosOverlay');

    this.initControls();
    setTimeout(() => {
      this.renderChart(this.currentSymbol);
    }, 100);
  }

  formatPrice(p) {
    const num = Number(p || 0);
    if (num <= 0) return '0.00';
    if (num < 0.01) return num.toFixed(6);
    if (num < 1.0) return num.toFixed(4);
    if (num < 10.0) return num.toFixed(3);
    return num.toFixed(2);
  }

  initControls() {
    if (this.btnAutoTrack) {
      this.btnAutoTrack.addEventListener('click', () => {
        this.autoTrack = !this.autoTrack;
        this.updateAutoTrackButton();
        if (this.autoTrack && this.activePosition && this.activePosition.symbol) {
          this.setBotSymbol(this.activePosition.symbol, this.activePosition);
        }
      });
    }

    const quickButtons = document.querySelectorAll('.tv-quick-sym');
    quickButtons.forEach(btn => {
      btn.addEventListener('click', (e) => {
        const sym = e.target.getAttribute('data-sym');
        if (sym === 'ACTIVE') {
          this.autoTrack = true;
          this.updateAutoTrackButton();
          if (this.activePosition && this.activePosition.symbol) {
            this.setBotSymbol(this.activePosition.symbol, this.activePosition);
          }
        } else if (sym) {
          this.autoTrack = false;
          this.updateAutoTrackButton();
          this.renderChart(sym);
        }
      });
    });
  }

  updateAutoTrackButton() {
    if (!this.btnAutoTrack) return;
    if (this.autoTrack) {
      this.btnAutoTrack.textContent = '⚡ AUTO-TRACK: ON';
      this.btnAutoTrack.classList.add('active');
    } else {
      this.btnAutoTrack.textContent = '🔒 AUTO-TRACK: OFF';
      this.btnAutoTrack.classList.remove('active');
    }
  }

  /**
   * Called when bot has an active position — switches immediately without cooldown
   */
  setBotSymbol(symbol, posData = null) {
    if (!symbol) return;
    const cleanSym = symbol.toUpperCase().trim();
    this.activePosition = posData;
    this.updatePositionOverlay(posData);

    if (this.autoTrack && cleanSym !== this.lastRenderedSymbol) {
      this.lastCandidateSwitchTs = Date.now();
      this.renderChart(cleanSym);
    }
  }

  /**
   * Called when bot evaluates a market candidate and no position is open (debounced by 20s)
   */
  setCandidateSymbol(symbol) {
    if (!symbol) return;
    const cleanSym = symbol.toUpperCase().trim();
    const isFlat = !this.activePosition || !this.activePosition.symbol || this.activePosition.state === 'FLAT';
    const now = Date.now();

    if (
      this.autoTrack &&
      isFlat &&
      cleanSym !== this.lastRenderedSymbol &&
      (now - this.lastCandidateSwitchTs >= this.candidateSwitchCooldownMs)
    ) {
      this.lastCandidateSwitchTs = now;
      this.renderChart(cleanSym);
    }
  }

  formatTvSymbol(rawSymbol) {
    let s = rawSymbol.toUpperCase().replace('/', '').replace('-', '').trim();
    if (!s.endsWith('USDT')) {
      s += 'USDT';
    }
    return `BYBIT:${s}.P`;
  }

  renderChart(symbol) {
    const container = document.getElementById('tradingview_chart_container');
    if (!container) return;

    this.currentSymbol = symbol;
    this.lastRenderedSymbol = symbol;
    const tvSymbol = this.formatTvSymbol(symbol);

    if (this.symbolTitleEl) {
      this.symbolTitleEl.textContent = tvSymbol;
    }
    if (this.statusEl) {
      this.statusEl.textContent = this.autoTrack ? `[SYNCED WITH BOT ENGINE: ${symbol}]` : `[MANUAL LOCK: ${symbol}]`;
    }

    container.innerHTML = '';
    const widgetDiv = document.createElement('div');
    widgetDiv.id = 'tv_widget_inner';
    widgetDiv.style.width = '100%';
    widgetDiv.style.height = '100%';
    container.appendChild(widgetDiv);

    try {
      if (typeof TradingView !== 'undefined' && TradingView.widget) {
        new TradingView.widget({
          autosize: true,
          symbol: tvSymbol,
          interval: '15',
          timezone: 'Etc/UTC',
          theme: 'dark',
          style: '1',
          locale: 'en',
          toolbar_bg: '#000000',
          enable_publishing: false,
          allow_symbol_change: true,
          container_id: 'tv_widget_inner',
          hide_side_toolbar: false,
          studies: [
            'MASimple@tv-basicstudies',
            'RSI@tv-basicstudies'
          ],
          disabled_features: ['header_saveload'],
          overrides: {
            'paneProperties.background': '#000000',
            'paneProperties.vertGridProperties.color': '#111722',
            'paneProperties.horzGridProperties.color': '#111722',
            'scalesProperties.textColor': '#ff9900',
            'scalesProperties.lineColor': '#1a2332',
          }
        });
      } else {
        const iframe = document.createElement('iframe');
        iframe.src = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(tvSymbol)}&interval=15&theme=dark&style=1&timezone=Etc%2FUTC`;
        iframe.style.width = '100%';
        iframe.style.height = '100%';
        iframe.style.border = 'none';
        widgetDiv.appendChild(iframe);
      }
    } catch (err) {
      console.warn('TradingView initialization error:', err);
      container.innerHTML = `<div style="padding: 20px; color: var(--bb-amber); font-family: monospace;">
        TradingView load standby for ${tvSymbol}...
      </div>`;
    }
  }

  updatePositionOverlay(pos) {
    if (!this.posOverlay) return;
    if (!pos || !pos.symbol || pos.state === 'FLAT' || pos.state === 'CLOSED') {
      this.ActivePosition = null;
      this.posOverlay.innerHTML = `
        <span class="bb-pill bb-dim">DESK: SCANNING CANDIDATES</span>
        <span class="bb-pill bb-amber">ACTIVE PAIR: ${this.currentSymbol}</span>
        <span class="bb-pill bb-dim">EXPOSURE: FLAT</span>
      `;
      return;
    }

    const isBuy = pos.side === 'Buy';
    const sideClass = isBuy ? 'bb-green' : 'bb-red';
    const sideLabel = isBuy ? 'LONG' : 'SHORT';
    const pnl = pos.pnl || 0.0;
    const roe = pos.roe_pct || 0.0;
    const pnlClass = pnl >= 0 ? 'bb-green' : 'bb-red';
    const levStr = pos.leverage ? ` (${pos.leverage}x)` : '';

    this.posOverlay.innerHTML = `
      <span class="bb-pill ${sideClass}">POSITION: ${sideLabel} ${pos.symbol}${levStr}</span>
      <span class="bb-pill bb-dim">SIZE: ${pos.size || 0}</span>
      <span class="bb-pill bb-dim">ENTRY: $${this.formatPrice(pos.entry_price)}</span>
      <span class="bb-pill bb-dim">MARK: $${this.formatPrice(pos.mark_price)}</span>
      <span class="bb-pill ${pnlClass}">PNL: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${roe >= 0 ? '+' : ''}${roe.toFixed(2)}%)</span>
      <span class="bb-pill bb-dim">SL: $${pos.stop_price ? this.formatPrice(pos.stop_price) : '---'}</span>
      <span class="bb-pill bb-dim">TP1: $${pos.tp1_price ? this.formatPrice(pos.tp1_price) : '---'}</span>
    `;
  }
}

window.TradingViewComponent = TradingViewComponent;
