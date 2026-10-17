/**
 * Market Brain & Orderbook L2 Microstructure Component.
 * Visualizes live price anchors, depth balance bars, and liquidity levels.
 */

class MarketBrainComponent {
  formatPrice(p) {
    const num = Number(p || 0);
    if (num <= 0) return '0.00';
    if (num < 0.01) return num.toFixed(6);
    if (num < 1.0) return num.toFixed(4);
    if (num < 10.0) return num.toFixed(3);
    return num.toFixed(2);
  }

  update(data) {
    if (!data) return;

    // Symbol & Price
    const elSym = document.getElementById('mktSymbol');
    const elPrice = document.getElementById('mktPrice');
    const elSpread = document.getElementById('mktSpread');
    const elCvd = document.getElementById('mktCvd');
    const elObi = document.getElementById('mktObi');
    const elWobi = document.getElementById('mktWobi');
    const elRegime = document.getElementById('marketRegimeBadge');

    if (elSym && data.symbol) elSym.textContent = data.symbol;
    if (elPrice && data.last_price) elPrice.textContent = `$${this.formatPrice(data.last_price)}`;
    if (elSpread) elSpread.textContent = `${(data.spread_pct || 0.02).toFixed(3)}%`;
    if (elCvd) {
      const cvdVal = data.cvd || 0;
      elCvd.textContent = `${cvdVal >= 0 ? '+' : ''}${(cvdVal / 1000).toFixed(1)}k`;
      elCvd.style.color = cvdVal >= 0 ? 'var(--neon-green)' : 'var(--neon-red)';
    }
    if (elObi) elObi.textContent = (data.orderbook_imbalance || 1.0).toFixed(2);
    if (elWobi) elWobi.textContent = (data.weighted_obi || 1.0).toFixed(2);
    if (elRegime && data.regime) elRegime.textContent = data.regime;

    // L2 Depth Bars
    const bidFill = document.getElementById('bidDepthFill');
    const askFill = document.getElementById('askDepthFill');
    const bidVal = document.getElementById('bidDepthVal');
    const askVal = document.getElementById('askDepthVal');

    const totalDepth = (data.bid_depth || 1) + (data.ask_depth || 1);
    const bidPct = Math.round(((data.bid_depth || 1) / totalDepth) * 100);
    const askPct = 100 - bidPct;

    if (bidFill) bidFill.style.width = `${bidPct}%`;
    if (askFill) askFill.style.width = `${askPct}%`;
    if (bidVal) bidVal.textContent = `$${(data.bid_walls_usd || 1.5).toFixed(1)}M`;
    if (askVal) askVal.textContent = `$${(data.ask_walls_usd || 1.1).toFixed(1)}M`;

    // Price Anchors
    const elVwap = document.getElementById('mktVwap');
    const elFvg = document.getElementById('mktFvg');
    const elLiq = document.getElementById('mktLiq');

    if (elVwap && data.vwap_24h) elVwap.textContent = `$${this.formatPrice(data.vwap_24h)}`;
    if (elFvg && data.nearest_fvg) elFvg.textContent = `$${this.formatPrice(data.nearest_fvg)}`;
    if (elLiq && data.external_liq) elLiq.textContent = `$${this.formatPrice(data.external_liq)}`;
  }
}

window.MarketBrainComponent = MarketBrainComponent;
