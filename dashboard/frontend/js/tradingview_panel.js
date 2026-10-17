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

    this.statusEl = document.getElementById('tvChartStatus');
    this.symbolTitleEl = document.getElementById('tvChartSymbolTitle');
    this.btnAutoTrack = document.getElementById('btnTvAutoTrack');
    this.posOverlay = document.getElementById('tvPosOverlay');

    this.initControls();
    // Render initial chart once DOM is ready
    setTimeout(() => {
      this.renderChart(this.currentSymbol);
    }, 100);
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

    // Quick symbol selectors
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
   * Called when bot has an active position or execution update
   */
  setBotSymbol(symbol, posData = null) {
    if (!symbol) return;
    const cleanSym = symbol.toUpperCase().trim();
    this.activePosition = posData;
    this.updatePositionOverlay(posData);

    if (this.autoTrack && cleanSym !== this.lastRenderedSymbol) {
      this.renderChart(cleanSym);
    }
  }

  /**
   * Called when bot evaluates a market candidate and no position is open
   */
  setCandidateSymbol(symbol) {
    if (!symbol) return;
    const cleanSym = symbol.toUpperCase().trim();
    // Only auto-switch to scanned symbol if flat (no active open trade)
    const isFlat = !this.activePosition || !this.activePosition.symbol || this.activePosition.state === 'FLAT';
    if (this.autoTrack && isFlat && cleanSym !== this.lastRenderedSymbol) {
      this.renderChart(cleanSym);
    }
  }

  formatTvSymbol(rawSymbol) {
    // Bybit perpetual convention: BYBIT:BTCUSDT.P or BYBIT:SOLUSDT.P
    let s = rawSymbol.toUpperCase().replace('/', '').trim();
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
        // Fallback: Embed iframe directly
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

    this.posOverlay.innerHTML = `
      <span class="bb-pill ${sideClass}">POSITION: ${sideLabel} ${pos.symbol}</span>
      <span class="bb-pill bb-dim">SIZE: ${pos.size || 0}</span>
      <span class="bb-pill bb-dim">ENTRY: $${(pos.entry_price || 0).toFixed(2)}</span>
      <span class="bb-pill bb-dim">MARK: $${(pos.mark_price || 0).toFixed(2)}</span>
      <span class="bb-pill ${pnlClass}">PNL: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${roe >= 0 ? '+' : ''}${roe.toFixed(2)}%)</span>
      <span class="bb-pill bb-dim">SL: $${pos.stop_price ? pos.stop_price.toFixed(2) : '---'}</span>
      <span class="bb-pill bb-dim">TP1: $${pos.tp1_price ? pos.tp1_price.toFixed(2) : '---'}</span>
    `;
  }
}

window.TradingViewComponent = TradingViewComponent;
