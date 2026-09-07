/**
 * System HUD, Account Fortress, and Active Position Monitor Component.
 */

class HudComponent {
  updateSystem(data) {
    if (!data) return;

    // Header Bot Badge
    const elBadge = document.getElementById('botStatusBadge');
    const elText = document.getElementById('botStatusText');
    if (elBadge && elText) {
      if (data.bot_status === 'ONLINE') {
        elBadge.className = 'bot-badge online';
        elText.textContent = 'BOT ONLINE';
      } else if (data.bot_status === 'FROZEN') {
        elBadge.className = 'bot-badge frozen';
        elText.textContent = `FROZEN: ${data.freeze_reason || 'UNSAFE'}`;
      } else {
        elBadge.className = 'bot-badge unknown';
        elText.textContent = 'STATUS: UNKNOWN';
      }
    }

    // Header Metrics
    const elEq = document.getElementById('headerEquity');
    const elAv = document.getElementById('headerAvail');
    const elFort = document.getElementById('headerFortress');

    if (elEq && data.account_equity !== undefined) elEq.textContent = `$${data.account_equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
    if (elAv && data.available_balance !== undefined) elAv.textContent = `$${data.available_balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
    if (elFort && data.cash_fortress_pct !== undefined) {
      elFort.textContent = `${data.cash_fortress_pct.toFixed(1)}%`;
      elFort.className = data.cash_fortress_pct >= 85.0 ? 'metric-value green' : 'metric-value red';
    }

    // System Health Grid
    const elLag = document.getElementById('loopLagVal');
    const elCpu = document.getElementById('cpuRamVal');
    const elWdog = document.getElementById('watchdogVal');
    const elKill = document.getElementById('killSwitchVal');
    const elUptime = document.getElementById('uptimeVal');

    if (elLag && data.event_loop_lag_ms !== undefined) elLag.textContent = `${data.event_loop_lag_ms}ms`;
    if (elCpu) elCpu.textContent = `${data.cpu_pct || 1.8}% / ${data.ram_mb || 65}MB`;
    if (elWdog && data.watchdog_status) elWdog.textContent = data.watchdog_status;
    if (elKill) {
      elKill.textContent = data.kill_switch_active ? 'ACTIVE (TRADING HALTED)' : 'DISARMED';
      elKill.className = data.kill_switch_active ? 'metric-value red' : 'metric-value green';
    }
    if (elUptime && data.uptime_sec !== undefined) {
      const hrs = Math.floor(data.uptime_sec / 3600);
      const mins = Math.floor((data.uptime_sec % 3600) / 60);
      elUptime.textContent = `${hrs}h ${mins}m`;
    }
  }

  updatePosition(data) {
    if (!data) return;

    const elBadge = document.getElementById('positionBadge');
    const elSym = document.getElementById('posSymbol');
    const elSize = document.getElementById('posSize');
    const elPnl = document.getElementById('posPnl');
    const elTp2 = document.getElementById('posTp2Price');
    const elTp1 = document.getElementById('posTp1Price');
    const elEntry = document.getElementById('posEntryPrice');
    const elSl = document.getElementById('posSlPrice');
    const elHold = document.getElementById('posHoldTime');
    const elMae = document.getElementById('posMae');
    const elMfe = document.getElementById('posMfe');

    const pos = data.active_position;

    if (!pos || data.position_state === 'FLAT' || data.position_state === 'CLOSED') {
      if (elBadge) { elBadge.className = 'position-badge flat'; elBadge.textContent = 'FLAT'; }
      if (elSym) elSym.textContent = 'NO ACTIVE TRADE';
      if (elSize) elSize.textContent = '0.000 QTY';
      if (elPnl) { elPnl.textContent = '$0.00 (0.00%)'; elPnl.style.color = 'var(--text-muted)'; }
      if (elTp2) elTp2.textContent = '---';
      if (elTp1) elTp1.textContent = '---';
      if (elEntry) elEntry.textContent = '---';
      if (elSl) elSl.textContent = '---';
      if (elHold) elHold.textContent = '0s';
      if (elMae) elMae.textContent = '$0.00';
      if (elMfe) elMfe.textContent = '$0.00';
    } else {
      const isBuy = pos.side === 'Buy';
      if (elBadge) {
        elBadge.className = isBuy ? 'position-badge buy' : 'position-badge sell';
        elBadge.textContent = isBuy ? 'LONG' : 'SHORT';
      }
      if (elSym) elSym.textContent = pos.symbol;
      if (elSize) elSize.textContent = `${pos.size} QTY`;

      const pnl = pos.pnl || 0.0;
      const roe = pos.roe_pct || 0.0;
      if (elPnl) {
        elPnl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${roe >= 0 ? '+' : ''}${roe.toFixed(2)}%)`;
        elPnl.style.color = pnl >= 0 ? 'var(--neon-green)' : 'var(--neon-red)';
      }

      if (elTp2) elTp2.textContent = pos.tp2_price ? `$${pos.tp2_price.toFixed(2)}` : '---';
      if (elTp1) elTp1.textContent = pos.tp1_price ? `$${pos.tp1_price.toFixed(2)}` : '---';
      if (elEntry) elEntry.textContent = pos.entry_price ? `$${pos.entry_price.toFixed(2)}` : '---';
      if (elSl) elSl.textContent = pos.stop_price ? `$${pos.stop_price.toFixed(2)}` : '---';
      if (elHold) elHold.textContent = `${Math.round(pos.hold_time_sec || 0)}s`;
      if (elMae) elMae.textContent = `$${(pos.mae || 0).toFixed(2)}`;
      if (elMfe) elMfe.textContent = `$${(pos.mfe || 0).toFixed(2)}`;
    }
  }
}

window.HudComponent = HudComponent;
