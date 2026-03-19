/* PolyBot V2 — Trading Dashboard with Crypto Scalper */

const { useState, useEffect, Fragment, useRef } = React;

const TradingViewWidget = () => {
    const containerRef = useRef(null);

    useEffect(() => {
        if (!window.TradingView) {
            const script = document.createElement('script');
            script.src = 'https://s3.tradingview.com/tv.js';
            script.async = true;
            script.onload = () => initWidget();
            document.head.appendChild(script);
        } else {
            initWidget();
        }

        function initWidget() {
            if (containerRef.current && window.TradingView) {
                containerRef.current.innerHTML = ''; // Clear previous
                new window.TradingView.widget({
                    "autosize": true,
                    "symbol": "BINANCE:BTCUSDT",
                    "interval": "5",
                    "timezone": "Etc/UTC",
                    "theme": "dark",
                    "style": "1",
                    "locale": "en",
                    "enable_publishing": false,
                    "backgroundColor": "#161618",
                    "gridColor": "rgba(255, 255, 255, 0.08)",
                    "hide_top_toolbar": false,
                    "save_image": false,
                    "container_id": containerRef.current.id
                });
            }
        }
    }, []);

    return <div id="tradingview_btc_5m" ref={containerRef} className="tv-widget-container" />;
};

const App = () => {
    const [data, setData] = useState({
        summary: null,
        signals: [],
        positions: [],
        recent_trades: [],
        regime: null,
        performance: null,
        crypto: {},
        scan: null,
        lastUpdate: '--:--:--'
    });
    const [tab, setTab] = useState('overview');

    useEffect(() => {
        const fetchData = async () => {
            try {
                const [summaryRes, sigRes, posRes, tradeRes, cryptoRes, regimeRes, perfRes] = await Promise.all([
                    fetch('/api/v1/summary').then(r => r.json()).catch(() => null),
                    fetch('/api/v1/opportunities').then(r => r.json()).catch(() => []),
                    fetch('/api/v1/positions').then(r => r.json()).catch(() => []),
                    fetch('/api/v1/trades?limit=20').then(r => r.json()).catch(() => []),
                    fetch('/api/v1/crypto').then(r => r.json()).catch(() => ({})),
                    fetch('/api/v1/regime').then(r => r.json()).catch(() => null),
                    fetch('/api/v1/performance').then(r => r.json()).catch(() => null),
                ]);
                const scanRes = await fetch('/api/v1/scan?limit=120').then(r => r.json()).catch(() => null);

                setData({
                    summary: summaryRes,
                    signals: sigRes || [],
                    positions: posRes || [],
                    recent_trades: tradeRes || [],
                    crypto: cryptoRes || {},
                    regime: regimeRes,
                    performance: perfRes,
                    scan: scanRes,
                    lastUpdate: new Date().toLocaleTimeString(),
                });
            } catch (e) {
                console.error('Fetch error:', e);
            }
        };

        fetchData();
        const interval = setInterval(fetchData, 5000);
        return () => clearInterval(interval);
    }, []);

    const summary = data.summary || {
        status: 'offline', mode: '...', balance: 0, total_value: 0,
        daily_pnl: 0, open_positions_count: 0, trades_today: 0, cycle_count: 0
    };

    // BTC data from crypto endpoint
    const btc = data.crypto.BTCUSDT || {};
    const btcPrice = btc.price || 0;
    const btcIndicators = btc.indicators || {};
    const btcPrediction = btc.prediction || {};

    return (
        <Fragment>
            {/* Sidebar */}
            <div className="sidebar">
                <div className="sidebar-logo">🤖 POLYBOT</div>
                <div className="nav-items">
                    {['overview', 'crypto', 'positions', 'history', 'regime'].map(t => (
                        <div key={t} className={`nav-item ${tab === t ? 'active' : ''}`}
                            onClick={() => setTab(t)}>
                            {t.charAt(0).toUpperCase() + t.slice(1)}
                        </div>
                    ))}
                </div>

                {/* BTC Ticker in Sidebar */}
                {btcPrice > 0 && (
                    <div className="btc-ticker">
                        <div className="btc-label">₿ BTC/USDT</div>
                        <div className="btc-price">${btcPrice.toLocaleString(undefined, {maximumFractionDigits: 0})}</div>
                        {btcIndicators.consensus && (
                            <div className={`btc-signal ${btcIndicators.consensus}`}>
                                {btcIndicators.consensus === 'bullish' ? '📈' : btcIndicators.consensus === 'bearish' ? '📉' : '➡️'}
                                {' '}{btcIndicators.consensus.toUpperCase()}
                            </div>
                        )}
                    </div>
                )}

                <div className="sidebar-status">
                    <div className="status-label">Bot Engine</div>
                    <div className="status-value">
                        <div className="status-dot"></div>
                        {summary.status.toUpperCase()} ({summary.mode.toUpperCase()})
                    </div>
                </div>
            </div>

            {/* Main Content */}
            <div className="main">
                <div className="header">
                    <h1>{tab === 'overview' ? 'Trading Overview' : tab === 'crypto' ? '₿ Crypto Scalper' : tab === 'positions' ? 'Open Positions' : tab === 'history' ? 'Trade History' : 'Regime & Performance'}</h1>
                    <div className="last-update">Last update: {data.lastUpdate} | Cycle #{summary.cycle_count}</div>
                </div>

                {tab === 'overview' && renderOverview(summary, data, btcPrediction)}
                {tab === 'crypto' && renderCrypto(data.crypto, data.signals, data.scan)}
                {tab === 'positions' && renderPositions(data.positions)}
                {tab === 'history' && renderHistory(data.recent_trades)}
                {tab === 'regime' && renderRegime(data.regime, data.performance)}
            </div>
        </Fragment>
    );
};

/* ============ Overview Tab ============ */
function renderOverview(summary, data, btcPred) {
    return (
        <Fragment>
            <div className="stats-row">
                <div className="card">
                    <div className="card-label">Total Portfolio Value</div>
                    <div className="card-value">${summary.total_value.toFixed(2)}</div>
                    <div className="card-subtext">Cash: ${summary.balance.toFixed(2)}</div>
                </div>
                <div className="card">
                    <div className="card-label">Daily Profit / Loss</div>
                    <div className={`card-value ${summary.daily_pnl >= 0 ? 'text-success' : 'text-danger'}`}>
                        {summary.daily_pnl >= 0 ? '+' : ''}${summary.daily_pnl.toFixed(2)}
                    </div>
                    <div className="card-subtext">{summary.trades_today || 0} trades today</div>
                </div>
                <div className="card">
                    <div className="card-label">Active Positions</div>
                    <div className="card-value">{summary.open_positions_count}</div>
                    <div className="card-subtext text-accent">
                        {btcPred.direction ? `BTC: ${btcPred.direction}` : 'Scanning...'}
                    </div>
                </div>
            </div>

            <div className="grid">
                <div className="signals-section">
                    <h2 className="section-title">🎯 Active Signals</h2>
                    <div className="opportunities-list">
                        {data.signals.length > 0 ? data.signals.map((sig, i) => (
                            <div key={i} className="market-card">
                                <div className="market-header">
                                    <span className={`direction-badge ${sig.side === 'BUY' ? 'badge-for' : 'badge-against'}`}>
                                        {sig.side === 'BUY' ? '✅ FOR' : '❌ AGAINST'}
                                    </span>
                                    <span className="strategy-tag">{sig.strategy_name}</span>
                                </div>
                                <div className="market-question">
                                    {sig.market ? sig.market.question : sig.reasoning || 'Unknown market'}
                                </div>
                                <div className="market-metrics">
                                    <div className="metric-item">
                                        <span className="metric-label">Edge</span>
                                        <span className={`metric-value ${sig.edge > 0 ? 'text-success' : 'text-danger'}`}>
                                            {(sig.edge * 100).toFixed(1)}%
                                        </span>
                                    </div>
                                    <div className="metric-item">
                                        <span className="metric-label">Size</span>
                                        <span className="metric-value">${(sig.size_usd || 0).toFixed(2)}</span>
                                    </div>
                                    <div className="metric-item">
                                        <span className="metric-label">Conviction</span>
                                        <span className="metric-value text-accent">
                                            {typeof sig.conviction === 'string' ? sig.conviction : (sig.conviction || 'LOW')}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        )) : (
                            <div className="last-update">No active signals. Analyzing markets...</div>
                        )}
                    </div>
                </div>

                <div className="activity-section">
                    <h2 className="section-title">📋 Recent Trades</h2>
                    <div className="activity-list">
                        {data.recent_trades.length > 0 ? data.recent_trades.slice(0, 8).map((trade, i) => (
                            <div key={i} className="log-item">
                                <div className="log-header">
                                    <span className={`direction-badge-sm ${trade.side === 'BUY' ? 'badge-for' : 'badge-against'}`}>
                                        {trade.side === 'BUY' ? '📈 FOR' : '📉 AGAINST'}
                                    </span>
                                    <span className="trade-price">${(trade.price || 0).toFixed(3)}</span>
                                </div>
                                <div className="log-meta">
                                    {trade.slug ? (
                                        <a href={`https://polymarket.com/event/${trade.slug}`} target="_blank" rel="noopener noreferrer" className="market-link">
                                            {trade.market_question || 'Unknown'} ↗
                                        </a>
                                    ) : (
                                        trade.market_question || 'Unknown'
                                    )}
                                </div>
                                <div className="log-subtext">
                                    <span className="strategy-tag-sm">{trade.strategy_name}</span>
                                    {trade.pnl !== undefined && trade.pnl !== 0 && (
                                        <span className={trade.pnl > 0 ? 'text-success' : 'text-danger'}>
                                            {' '}P/L: ${trade.pnl.toFixed(3)}
                                        </span>
                                    )}
                                </div>
                            </div>
                        )) : (
                            <div className="last-update">Waiting for trades...</div>
                        )}
                    </div>
                </div>
            </div>
        </Fragment>
    );
}

/* ============ Crypto Tab ============ */
function renderCrypto(crypto, signals, scan) {
    const symbols = Object.keys(crypto);
    const cryptoSignals = signals.filter(s => s.strategy_name === 'crypto_scalper');
    const scanRows = (scan && scan.rows) ? scan.rows : [];

    return (
        <Fragment>
            {/* Price Cards */}
            <div className="stats-row">
                {symbols.map(symbol => {
                    const c = crypto[symbol];
                    const ind = c.indicators || {};
                    const pred = c.prediction || {};
                    const name = symbol.replace('USDT', '');
                    return (
                        <div key={symbol} className="card crypto-card">
                            <div className="card-label">{name}/USDT</div>
                            <div className="card-value">${c.price.toLocaleString(undefined, {maximumFractionDigits: 2})}</div>
                            <div className="crypto-indicators">
                                <span className={`indicator-pill ${ind.consensus || 'neutral'}`}>
                                    {(ind.consensus || 'neutral').toUpperCase()}
                                </span>
                                <span className="indicator-detail">RSI: {(ind.rsi || 50).toFixed(0)}</span>
                                <span className="indicator-detail">Vol: {(ind.volume_ratio || 1).toFixed(1)}x</span>
                            </div>
                            {pred.direction && (
                                <div className={`prediction-bar ${pred.direction === 'UP' ? 'pred-up' : 'pred-down'}`}>
                                    {pred.direction === 'UP' ? '📈' : '📉'} {pred.direction}
                                    {' | '}Prob: {((pred.probability || 0) * 100).toFixed(0)}%
                                    {' | '}Edge: {((pred.edge || 0) * 100).toFixed(1)}%
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Chart & Indicators Grid */}
            <div className="grid">
                {/* TradingView Chart */}
                <div className="chart-section">
                    <h2 className="section-title">📈 Real-Time 5m Chart (BTC/USDT)</h2>
                    <TradingViewWidget />
                </div>

                {/* Indicator Details */}
                {symbols.length > 0 && (
                    <div className="indicator-grid">
                        <h2 className="section-title">📊 Technical Indicators</h2>
                    {(() => {
                        const btc = crypto.BTCUSDT || {};
                        const ind = btc.indicators || {};
                        return (
                            <div className="indicator-table">
                                {[
                                    ['RSI(14)', (ind.rsi || 50).toFixed(1), ind.rsi < 30 ? 'bullish' : ind.rsi > 70 ? 'bearish' : 'neutral'],
                                    ['EMA Trend', ind.ema_trend || 'neutral', ind.ema_trend || 'neutral'],
                                    ['MACD Histogram', (ind.macd_histogram || 0).toFixed(4), ind.macd_histogram > 0 ? 'bullish' : 'bearish'],
                                    ['Bollinger %B', (ind.bollinger_pct_b || 0.5).toFixed(2), ind.bollinger_pct_b < 0.2 ? 'bullish' : ind.bollinger_pct_b > 0.8 ? 'bearish' : 'neutral'],
                                    ['Volume Ratio', (ind.volume_ratio || 1).toFixed(2) + 'x', ind.volume_ratio > 1.2 ? 'bullish' : 'neutral'],
                                    ['VWAP Dev', (ind.vwap_deviation || 0).toFixed(3) + '%', ind.vwap_deviation > 0 ? 'bullish' : 'bearish'],
                                    ['Consensus', `${ind.bullish_count || 0}B / ${ind.bearish_count || 0}R`, ind.consensus || 'neutral'],
                                ].map(([name, val, status], i) => (
                                    <div key={i} className="indicator-row">
                                        <span className="ind-name">{name}</span>
                                        <span className="ind-value">{val}</span>
                                        <span className={`ind-status ${status}`}>
                                            {status === 'bullish' ? '🟢' : status === 'bearish' ? '🔴' : '⚪'}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        );
                    })()}
                </div>
            )}
            </div>

            {/* Crypto scalper signals */}
            {cryptoSignals.length > 0 && (
                <div className="signals-section" style={{marginTop: '24px'}}>
                    <h2 className="section-title">🎰 Crypto Scalper Signals</h2>
                    {cryptoSignals.map((sig, i) => (
                        <div key={i} className="market-card">
                            <div className="market-header">
                                <span className={`direction-badge ${sig.side === 'BUY' ? 'badge-for' : 'badge-against'}`}>
                                    {sig.side === 'BUY' ? '✅ FOR' : '❌ AGAINST'}
                                </span>
                                <span className="strategy-tag">crypto_scalper</span>
                            </div>
                            <div className="market-question">
                                {sig.market && sig.market.slug ? (
                                    <a href={`https://polymarket.com/event/${sig.market.slug}`} target="_blank" rel="noopener noreferrer" className="market-link">
                                        {sig.reasoning || sig.market.question} ↗
                                    </a>
                                ) : (
                                    sig.reasoning || (sig.market && sig.market.question) || ''
                                )}
                            </div>
                            <div className="market-metrics">
                                <div className="metric-item">
                                    <span className="metric-label">Edge</span>
                                    <span className="metric-value text-success">{(sig.edge * 100).toFixed(1)}%</span>
                                </div>
                                <div className="metric-item">
                                    <span className="metric-label">Size</span>
                                    <span className="metric-value">${(sig.size_usd || 0).toFixed(2)}</span>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* 5-min scan decisions */}
            <div className="signals-section" style={{marginTop: '24px'}}>
                <h2 className="section-title">🧭 5-min Scan (latest)</h2>
                <div className="table-wrap" style={{maxHeight: '320px'}}>
                    <table className="perf-table scan-table">
                        <thead>
                            <tr>
                                <th>Time (CET)</th>
                                <th>Decision</th>
                                <th>Symbol</th>
                                <th>Session</th>
                                <th>t+ (sec)</th>
                                <th>Price</th>
                                <th>Liq</th>
                                <th>Vol24h</th>
                                <th>Funding</th>
                                <th>Basis</th>
                                <th>Edge</th>
                                <th>Reason</th>
                            </tr>
                        </thead>
                        <tbody>
                            {scanRows.length > 0 ? scanRows.slice(0, 60).map((r, i) => (
                                <tr key={i} className={r.decision === 'SIGNAL' ? 'scan-signal' : 'scan-skip'}>
                                    <td style={{whiteSpace: 'nowrap'}}>
                                        {r.ts ? new Date((r.ts * 1000)).toLocaleString(undefined, {
                                            timeZone: 'Europe/Prague',
                                            hour12: false,
                                            year: '2-digit',
                                            month: '2-digit',
                                            day: '2-digit',
                                            hour: '2-digit',
                                            minute: '2-digit',
                                            second: '2-digit'
                                        }) : '—'}
                                    </td>
                                    <td>
                                        <span className={`status-pill ${r.decision === 'SIGNAL' ? 'status-filled' : 'status-pending'}`}>
                                            {r.decision}
                                        </span>
                                    </td>
                                    <td>{r.symbol}</td>
                                    <td>{r.session}</td>
                                    <td>{(r.sec_into_5m !== undefined && r.sec_into_5m !== null) ? r.sec_into_5m : '—'}</td>
                                    <td>{(r.yes_price || 0).toFixed(3)}</td>
                                    <td>{r.liquidity ? `$${Math.round(r.liquidity).toLocaleString()}` : '—'}</td>
                                    <td>{r.volume_24h ? `$${Math.round(r.volume_24h).toLocaleString()}` : '—'}</td>
                                    <td className={(r.funding_rate || 0) >= 0 ? 'text-success' : 'text-danger'}>
                                        {r.funding_rate !== undefined ? `${((r.funding_rate || 0) * 100).toFixed(3)}%` : '—'}
                                    </td>
                                    <td className={(r.basis_pct || 0) >= 0 ? 'text-success' : 'text-danger'}>
                                        {r.basis_pct !== undefined ? `${(r.basis_pct || 0).toFixed(3)}%` : '—'}
                                    </td>
                                    <td className={(r.post_edge || 0) >= 0 ? 'text-success' : 'text-danger'}>
                                        {r.post_edge !== undefined ? `${((r.post_edge || 0) * 100).toFixed(2)}%` : '—'}
                                    </td>
                                    <td className="scan-reason">
                                        {r.slug ? (
                                            <a href={`https://polymarket.com/event/${r.slug}`} target="_blank" rel="noopener noreferrer" className="market-link">
                                                {r.reason} ↗
                                            </a>
                                        ) : (
                                            r.reason
                                        )}
                                    </td>
                                </tr>
                            )) : (
                                <tr>
                                    <td colSpan="12" className="last-update">Waiting for scan data...</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
                <div className="last-update" style={{marginTop: '8px'}}>
                    This table shows why each 5-min market was skipped or selected.
                </div>
            </div>
        </Fragment>
    );
}

/* ============ Positions Tab ============ */
function renderPositions(positions) {
    const openPositions = positions || [];
    return (
        <Fragment>
            <div className="positions-list">
                {openPositions.length > 0 ? openPositions.map((p, i) => (
                    <div key={i} className="position-card">
                        <div className="position-header">
                            <span className={`direction-badge ${p.side === 'BUY' ? 'badge-for' : 'badge-against'}`}>
                                {p.side === 'BUY' ? '✅ FOR' : '❌ AGAINST'}
                            </span>
                            <span className="strategy-tag">{p.strategy_name}</span>
                        </div>
                        <div className="position-market">
                            {p.slug ? (
                                <a href={`https://polymarket.com/event/${p.slug}`} target="_blank" rel="noopener noreferrer" className="market-link">
                                    {p.market_question || 'Unknown market'} ↗
                                </a>
                            ) : (
                                p.market_question || 'Unknown market'
                            )}
                        </div>
                        <div className="position-details">
                            <span>Entry: ${(p.entry_price || 0).toFixed(3)}</span>
                            <span>Current: ${(p.current_price || p.entry_price || 0).toFixed(3)}</span>
                            <span>Size: {(p.size || 0).toFixed(2)} shares</span>
                            <span>Cost: ${(p.cost_usd || 0).toFixed(2)}</span>
                            {p.unrealized_pnl !== undefined && p.unrealized_pnl !== 0 && (
                                <span className={p.unrealized_pnl > 0 ? 'text-success' : 'text-danger'}>
                                    U-P/L: ${(p.unrealized_pnl || 0).toFixed(2)}
                                </span>
                            )}
                        </div>
                    </div>
                )) : (
                    <div className="last-update">No open positions.</div>
                )}
            </div>
        </Fragment>
    );
}

/* ============ History Tab ============ */
function renderHistory(trades) {
    return (
        <Fragment>
            <div className="history-list">
                {trades.length > 0 ? trades.map((t, i) => {
                    const isWin = t.pnl !== undefined && t.pnl > 0;
                    const isLoss = t.pnl !== undefined && t.pnl < 0;
                    
                    return (
                        <div key={i} className={`log-item ${isWin ? 'log-win' : isLoss ? 'log-loss' : ''}`}>
                            <div className="log-header">
                                <span className={`direction-badge-sm ${t.side === 'BUY' ? 'badge-for' : 'badge-against'}`}>
                                    {t.side === 'BUY' ? '📈 FOR' : '📉 AGAINST'}
                                </span>
                                <span className="trade-price">${(t.price || 0).toFixed(3)} × {(t.size || 0).toFixed(1)}</span>
                            </div>
                            <div className="log-meta">
                                {t.slug ? (
                                    <a href={`https://polymarket.com/event/${t.slug}`} target="_blank" rel="noopener noreferrer" className="market-link">
                                        {t.market_question || 'Unknown'} ↗
                                    </a>
                                ) : (
                                    t.market_question || 'Unknown'
                                )}
                            </div>
                            <div className="log-subtext">
                                <span className="strategy-tag-sm">{t.strategy_name}</span>
                                <span className={`status-pill ${t.status === 'FILLED' || t.status === 'filled' ? 'status-filled' : 'status-pending'}`}>
                                    {t.status}
                                </span>
                                {isWin && <span className="outcome-badge outcome-win">🏆 WIN</span>}
                                {isLoss && <span className="outcome-badge outcome-loss">💀 LOSS</span>}
                                {t.pnl !== undefined && t.pnl !== 0 && (
                                    <span className={isWin ? 'text-success' : 'text-danger'} style={{fontWeight: 600}}>
                                        P/L: ${(t.pnl).toFixed(3)}
                                    </span>
                                )}
                            </div>
                        </div>
                    );
                }) : (
                    <div className="last-update">No trade history yet.</div>
                )}
            </div>
        </Fragment>
    );
}

/* ============ Regime Tab ============ */
function renderRegime(regime, performance) {
    const r = regime || {};
    const rows = (performance && performance.rows) ? performance.rows : [];

    return (
        <Fragment>
            <div className="stats-row">
                <div className="card">
                    <div className="card-label">Current Session (UTC)</div>
                    <div className="card-value">{(r.session || '—')}</div>
                    <div className="card-subtext">Dominance bucket: {(r.dominance_bucket || '—')}</div>
                </div>
                <div className="card">
                    <div className="card-label">BTC Dominance</div>
                    <div className="card-value">{r.btc_dominance_pct ? `${r.btc_dominance_pct.toFixed(2)}%` : '—'}</div>
                    <div className={`card-subtext ${((r.btc_dominance_change_1h_pp || 0) >= 0) ? 'text-success' : 'text-danger'}`}>
                        1h: {r.btc_dominance_change_1h_pp !== undefined ? `${r.btc_dominance_change_1h_pp >= 0 ? '+' : ''}${r.btc_dominance_change_1h_pp.toFixed(2)}pp` : '—'}
                    </div>
                </div>
                <div className="card">
                    <div className="card-label">Tuner Base Min Edge</div>
                    <div className="card-value">{performance && performance.base_min_edge ? `${(performance.base_min_edge * 100).toFixed(1)}%` : '—'}</div>
                    <div className="card-subtext">Only tunes buckets with ≥20 resolved trades</div>
                </div>
            </div>

            <div className="signals-section">
                <h2 className="section-title">📊 Performance by Regime</h2>
                <div className="table-wrap">
                    <table className="perf-table">
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Session</th>
                                <th>BTC Dom</th>
                                <th>N</th>
                                <th>Win%</th>
                                <th>ROI</th>
                                <th>Tuned minEdge</th>
                                <th>Tuned Kelly</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length > 0 ? rows.slice(0, 60).map((x, i) => (
                                <tr key={i}>
                                    <td>{x.underlying_symbol}</td>
                                    <td>{x.session}</td>
                                    <td>{x.dominance_bucket}</td>
                                    <td>{x.n}</td>
                                    <td className={x.win_rate >= 0.5 ? 'text-success' : 'text-danger'}>
                                        {(x.win_rate * 100).toFixed(0)}%
                                    </td>
                                    <td className={x.roi >= 0 ? 'text-success' : 'text-danger'}>
                                        {(x.roi * 100).toFixed(1)}%
                                    </td>
                                    <td>{x.tuned_min_edge !== null && x.tuned_min_edge !== undefined ? `${(x.tuned_min_edge * 100).toFixed(1)}%` : '—'}</td>
                                    <td>{x.tuned_kelly_mult !== null && x.tuned_kelly_mult !== undefined ? `${x.tuned_kelly_mult.toFixed(2)}×` : '—'}</td>
                                </tr>
                            )) : (
                                <tr>
                                    <td colSpan="8" className="last-update">Waiting for resolved trades (WIN/LOSS)...</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </Fragment>
    );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
