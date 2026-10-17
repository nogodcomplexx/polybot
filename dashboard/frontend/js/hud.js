/**
 * System HUD, Account Fortress, and Active Position Monitor Component.
 */

class HudComponent {
  constructor() {
    this._closing = false;
    this._bindCloseHandlers();
  }

  formatPrice(p) {
    const num = Number(p || 0);
    if (num <= 0) return '0.00';
    if (num < 0.01) return num.toFixed(6);
    if (num < 1.0) return num.toFixed(4);
    if (num < 10.0) return num.toFixed(3);
    return num.toFixed(2);
  }

  _bindCloseHandlers() {
    const handleClose = async (btn) => {
      if (this._closing) return;
      if (btn.disabled) return;
      if (!confirm('Are you sure you want to manually close the active position immediately?')) return;

      this._closing = true;
      const origText = btn.textContent;
      btn.textContent = '⏳ CLOSING...';
      btn.disabled = true;
      btn.classList.add('disabled');

      try {
        const res = await fetch('/api/close-position', { method: 'POST' });
        const json = await res.json();
        if (json.success) {
          btn.textContent = '✅ CLOSED';
          const elPnl = document.getElementById('posPnl');
          if (elPnl) elPnl.textContent = 'POSITION CLOSED';
          setTimeout(() => {
            btn.textContent = origText;
            btn.disabled = true;
            btn.classList.add('disabled');
            this._closing = false;
          }, 1500);
        } else {
          alert('Close failed: ' + (json.message || json.error || 'Unknown error'));
          btn.textContent = origText;
          btn.disabled = false;
          btn.classList.remove('disabled');
          this._closing = false;
        }
      } catch (err) {
        alert('Network error closing position: ' + err.message);
        btn.textContent = origText;
        btn.disabled = false;
        btn.classList.remove('disabled');
        this._closing = false;
      }
    };

    const attach = () => {
      const btn1 = document.getElementById('btnClosePosition');
      const btn2 = document.getElementById('btnQuickClosePos');
      if (btn1 && !btn1._hasHandler) {
        btn1._hasHandler = true;
        btn1.addEventListener('click', () => handleClose(btn1));
      }
      if (btn2 && !btn2._hasHandler) {
        btn2._hasHandler = true;
        btn2.addEventListener('click', () => handleClose(btn2));
      }
    };

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', attach);
    } else {
      setTimeout(attach, 50);
    }
  }

  updateSystem(data) {
    if (!data) return;

    // Header Bot Badge & Mode
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

    const elMode = document.getElementById('deskModeBadge');
    if (elMode && data.mode) {
      const modeStr = String(data.mode).toUpperCase();
      elMode.textContent = `MODE: ${modeStr}`;
      elMode.className = modeStr === 'LIVE' ? 'bb-pill bb-red' : (modeStr === 'DEMO' ? 'bb-pill bb-amber' : 'bb-pill bb-cyan');
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

    const elRec = document.getElementById('reconcileVal');
    if (elRec && data.reconciliation_status) {
      elRec.textContent = data.reconciliation_status;
      elRec.className = data.reconciliation_status === 'FROZEN' ? 'metric-value red' : 'metric-value cyan';
    }

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
    const btn1 = document.getElementById('btnClosePosition');
    const btn2 = document.getElementById('btnQuickClosePos');

    const pos = data.active_position;
    const activeStates = ['ACTIVE', 'PROTECTION_PENDING', 'TP1_FILLED', 'PROTECTED_RUNNER', 'DAMAGE_CONTROL', 'EXITING', 'PARTIALLY_FILLED'];
    const isActuallyActive = pos && activeStates.includes(data.position_state);

    if (!isActuallyActive) {
      if (btn1 && !this._closing) {
        btn1.style.display = 'inline-flex';
        btn1.disabled = true;
        btn1.classList.add('disabled');
        btn1.textContent = '✕ CLOSE';
        btn1.title = 'No active position to close';
      }
      if (btn2 && !this._closing) {
        btn2.style.display = 'block';
        btn2.disabled = true;
        btn2.classList.add('disabled');
        btn2.textContent = '🛑 CLOSE POSITION (FLAT)';
        btn2.title = 'No active position to close';
      }
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
      if (btn1 && !this._closing) {
        btn1.style.display = 'inline-flex';
        btn1.disabled = false;
        btn1.classList.remove('disabled');
        btn1.textContent = `✕ CLOSE`;
        btn1.title = `Close active ${pos.symbol || 'position'} immediately (Market)`;
      }
      if (btn2 && !this._closing) {
        btn2.style.display = 'block';
        btn2.disabled = false;
        btn2.classList.remove('disabled');
        btn2.textContent = `🛑 CLOSE ${pos.symbol || 'POSITION'} (MARKET)`;
        btn2.title = `Close active ${pos.symbol || 'position'} immediately (Market)`;
      }
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

      if (elTp2) elTp2.textContent = pos.tp2_price ? `$${this.formatPrice(pos.tp2_price)}` : '---';
      if (elTp1) elTp1.textContent = pos.tp1_price ? `$${this.formatPrice(pos.tp1_price)}` : '---';
      if (elEntry) elEntry.textContent = pos.entry_price ? `$${this.formatPrice(pos.entry_price)}` : '---';
      if (elSl) elSl.textContent = pos.stop_price ? `$${this.formatPrice(pos.stop_price)}` : '---';
      if (elHold) elHold.textContent = `${Math.round(pos.hold_time_sec || 0)}s`;
      if (elMae) elMae.textContent = `$${this.formatPrice(pos.mae || 0)}`;
      if (elMfe) elMfe.textContent = `$${this.formatPrice(pos.mfe || 0)}`;
    }
  }
}

window.HudComponent = HudComponent;
