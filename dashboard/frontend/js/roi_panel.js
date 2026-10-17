/**
 * Portfolio Performance & Total ROI / Capital Growth Component.
 * Visualizes total ROI %, dollar growth, realized/unrealized P&L, win rate, and trade statistics.
 */

class RoiPanelComponent {
  constructor() {
    this.elBadge = document.getElementById('roiBadge');
    this.elTotalRoi = document.getElementById('perfTotalRoi');
    this.elTotalRoiSub = document.getElementById('perfTotalRoiSub');
    this.elTotalPnl = document.getElementById('perfTotalPnl');
    this.elDailyPnlSub = document.getElementById('perfDailyPnlSub');
    this.elRealizedPnl = document.getElementById('perfRealizedPnl');
    this.elUnrealizedPnl = document.getElementById('perfUnrealizedPnl');
    this.elWinRate = document.getElementById('perfWinRate');
    this.elProfitFactor = document.getElementById('perfProfitFactor');
    this.elTotalTrades = document.getElementById('perfTotalTrades');
    this.elTotalFees = document.getElementById('perfTotalFees');
    this.elWinLossRatio = document.getElementById('perfWinLossRatio');
    this.barWin = document.getElementById('barWinRatio');
    this.barLoss = document.getElementById('barLossRatio');

    // Trading Analytics elements
    this.elAnalyticsWinRate = document.getElementById('analyticsWinRate');
    this.elAnalyticsProfitFactor = document.getElementById('analyticsProfitFactor');
    this.elAnalyticsExpectancy = document.getElementById('analyticsExpectancy');
    this.elAnalyticsDrawdown = document.getElementById('analyticsDrawdown');
    this.elAnalyticsStrategies = document.getElementById('analyticsStrategies');

    this._bindResetHandler();
  }

  _bindResetHandler() {
    const attach = () => {
      const btn = document.getElementById('btnResetPerf');
      if (btn && !btn._hasHandler) {
        btn._hasHandler = true;
        btn.addEventListener('click', async () => {
          if (!confirm('Are you sure you want to archive old test trade history and start a fresh performance session anchored to current account equity?')) return;
          btn.textContent = '⏳ RESETTING...';
          btn.disabled = true;
          try {
            const res = await fetch('/api/reset-performance', { method: 'POST' });
            const json = await res.json();
            if (json.success) {
              btn.textContent = '✅ RESET DONE';
              setTimeout(() => {
                btn.textContent = '🔄 RESET SESSION';
                btn.disabled = false;
              }, 1500);
            } else {
              alert('Reset failed: ' + (json.error || json.message));
              btn.textContent = '🔄 RESET SESSION';
              btn.disabled = false;
            }
          } catch (e) {
            alert('Network error resetting performance: ' + e.message);
            btn.textContent = '🔄 RESET SESSION';
            btn.disabled = false;
          }
        });
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', attach);
    } else {
      setTimeout(attach, 50);
    }
  }

  formatCurrency(val, includeSign = true) {
    if (val === undefined || val === null || isNaN(val)) return '$0.00';
    const absVal = Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (!includeSign) return `$${absVal}`;
    if (val > 0.001) return `+$${absVal}`;
    if (val < -0.001) return `-$${absVal}`;
    return `$0.00`;
  }

  formatPercent(val, includeSign = true) {
    if (val === undefined || val === null || isNaN(val)) return '0.00%';
    const absVal = Math.abs(val).toFixed(2);
    if (!includeSign) return `${absVal}%`;
    if (val > 0.001) return `+${absVal}%`;
    if (val < -0.001) return `-${absVal}%`;
    return `0.00%`;
  }

  update(data) {
    if (!data) return;

    const totalRoi = Number(data.total_roi_pct || 0);
    const totalPnl = Number(data.total_pnl_usd || 0);
    const dailyPnl = Number(data.daily_pnl_usd || 0);
    const dailyRoi = Number(data.daily_roi_pct || 0);
    const realized = Number(data.realized_pnl_usd || 0);
    const unrealized = Number(data.unrealized_pnl_usd || 0);
    const startingEq = Number(data.starting_equity || 0);
    const winCount = Number(data.win_count || 0);
    const lossCount = Number(data.loss_count || 0);
    const totalTrades = Number(data.total_trades || (winCount + lossCount));
    const winRate = Number(data.win_rate_pct || 0);
    const profitFactor = Number(data.profit_factor || 0);
    const fees = Number(data.total_fees || 0);

    // Color decisions
    const greenColor = 'var(--neon-green)';
    const redColor = 'var(--neon-red)';
    const dimColor = 'var(--text-dim)';

    // 1. Total ROI
    if (this.elTotalRoi) {
      this.elTotalRoi.textContent = this.formatPercent(totalRoi);
      if (totalRoi > 0.001) {
        this.elTotalRoi.style.color = greenColor;
        this.elTotalRoi.style.textShadow = '0 0 10px rgba(0, 255, 136, 0.4)';
      } else if (totalRoi < -0.001) {
        this.elTotalRoi.style.color = redColor;
        this.elTotalRoi.style.textShadow = '0 0 10px rgba(255, 68, 68, 0.4)';
      } else {
        this.elTotalRoi.style.color = dimColor;
        this.elTotalRoi.style.textShadow = 'none';
      }
    }

    // 2. Header Badge
    if (this.elBadge) {
      this.elBadge.textContent = this.formatPercent(totalRoi);
      if (totalRoi > 0.001) {
        this.elBadge.style.color = greenColor;
        this.elBadge.style.background = 'rgba(0, 255, 136, 0.12)';
        this.elBadge.style.borderColor = greenColor;
      } else if (totalRoi < -0.001) {
        this.elBadge.style.color = redColor;
        this.elBadge.style.background = 'rgba(255, 68, 68, 0.12)';
        this.elBadge.style.borderColor = redColor;
      } else {
        this.elBadge.style.color = dimColor;
        this.elBadge.style.background = 'rgba(255, 255, 255, 0.05)';
        this.elBadge.style.borderColor = 'rgba(255, 255, 255, 0.1)';
      }
    }

    // 3. Baseline Equity Subtext
    if (this.elTotalRoiSub) {
      this.elTotalRoiSub.textContent = startingEq > 0 
        ? `Baseline: $${startingEq.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : 'Baseline: $---';
    }

    // 4. Net Growth ($)
    if (this.elTotalPnl) {
      this.elTotalPnl.textContent = this.formatCurrency(totalPnl);
      if (totalPnl > 0.001) {
        this.elTotalPnl.style.color = greenColor;
        this.elTotalPnl.style.textShadow = '0 0 10px rgba(0, 255, 136, 0.4)';
      } else if (totalPnl < -0.001) {
        this.elTotalPnl.style.color = redColor;
        this.elTotalPnl.style.textShadow = '0 0 10px rgba(255, 68, 68, 0.4)';
      } else {
        this.elTotalPnl.style.color = dimColor;
        this.elTotalPnl.style.textShadow = 'none';
      }
    }

    // 5. Daily P&L Subtext
    if (this.elDailyPnlSub) {
      this.elDailyPnlSub.textContent = `Today: ${this.formatCurrency(dailyPnl)} (${this.formatPercent(dailyRoi)})`;
      this.elDailyPnlSub.style.color = dailyPnl > 0 ? greenColor : (dailyPnl < 0 ? redColor : 'var(--neon-cyan)');
    }

    // 6. Realized P&L
    if (this.elRealizedPnl) {
      this.elRealizedPnl.textContent = this.formatCurrency(realized);
      this.elRealizedPnl.style.color = realized > 0 ? greenColor : (realized < 0 ? redColor : dimColor);
    }

    // 7. Unrealized P&L
    if (this.elUnrealizedPnl) {
      this.elUnrealizedPnl.textContent = this.formatCurrency(unrealized);
      this.elUnrealizedPnl.style.color = unrealized > 0 ? greenColor : (unrealized < 0 ? redColor : dimColor);
    }

    // 8. Win Rate
    if (this.elWinRate) {
      this.elWinRate.textContent = totalTrades > 0 ? `${winRate.toFixed(1)}%` : '---';
      this.elWinRate.style.color = winRate >= 50 ? greenColor : (totalTrades > 0 ? redColor : dimColor);
    }

    // 9. Profit Factor
    if (this.elProfitFactor) {
      this.elProfitFactor.textContent = (profitFactor > 0 && totalTrades > 0) ? profitFactor.toFixed(2) : '---';
      this.elProfitFactor.style.color = profitFactor >= 1.5 ? greenColor : (profitFactor > 0 ? 'var(--neon-cyan)' : dimColor);
    }

    // 10. Total Trades
    if (this.elTotalTrades) {
      this.elTotalTrades.textContent = `${totalTrades} (${winCount}W - ${lossCount}L)`;
    }

    // 11. Total Fees
    if (this.elTotalFees) {
      this.elTotalFees.textContent = fees > 0 ? `-$${fees.toFixed(2)}` : '$0.00';
    }

    // 12. Win / Loss Bar & Ratio
    if (this.elWinLossRatio) {
      this.elWinLossRatio.textContent = `${winCount}W / ${lossCount}L`;
    }

    if (this.barWin && this.barLoss) {
      if (totalTrades > 0) {
        const winPct = (winCount / totalTrades) * 100;
        const lossPct = (lossCount / totalTrades) * 100;
        this.barWin.style.width = `${winPct}%`;
        this.barLoss.style.width = `${lossPct}%`;
      } else {
        this.barWin.style.width = '50%';
        this.barLoss.style.width = '50%';
      }
    }

    // 13. Synchronize Trading Analytics Card
    if (this.elAnalyticsWinRate) {
      this.elAnalyticsWinRate.textContent = totalTrades > 0 ? `${winRate.toFixed(1)}%` : '0.0%';
      this.elAnalyticsWinRate.style.color = winRate >= 50 ? greenColor : (totalTrades > 0 ? redColor : dimColor);
    }
    if (this.elAnalyticsProfitFactor) {
      this.elAnalyticsProfitFactor.textContent = (profitFactor > 0 && totalTrades > 0) ? profitFactor.toFixed(2) : '0.00';
      this.elAnalyticsProfitFactor.style.color = profitFactor >= 1.5 ? greenColor : (profitFactor > 0 ? 'var(--neon-cyan)' : dimColor);
    }
    if (this.elAnalyticsExpectancy) {
      const exp = Number(data.expectancy || 0);
      this.elAnalyticsExpectancy.textContent = `${exp >= 0 ? '+' : ''}${exp.toFixed(2)}R`;
      this.elAnalyticsExpectancy.style.color = exp > 0 ? greenColor : (exp < 0 ? redColor : dimColor);
    }
    if (this.elAnalyticsDrawdown) {
      const dd = Number(data.max_drawdown_pct || 0);
      this.elAnalyticsDrawdown.textContent = `${dd.toFixed(2)}%`;
      this.elAnalyticsDrawdown.style.color = dd < 3.0 ? greenColor : (dd < 5.0 ? 'var(--neon-amber)' : redColor);
    }
    if (this.elAnalyticsStrategies) {
      const strats = data.strategy_stats ? Object.entries(data.strategy_stats) : [];
      if (strats.length > 0) {
        this.elAnalyticsStrategies.innerHTML = strats.map(([name, s]) => {
          const pnlVal = Number(s.pnl || 0);
          const pnlStr = pnlVal >= 0 ? `+$${pnlVal.toFixed(2)}` : `-$${Math.abs(pnlVal).toFixed(2)}`;
          const pnlCol = pnlVal > 0 ? greenColor : (pnlVal < 0 ? redColor : dimColor);
          const cleanName = name.replace(/_/g, ' ');
          return `<div style="display: flex; justify-content: space-between; align-items: baseline; padding: 2px 0;">
            <span>${cleanName} <span style="color: var(--text-dim); font-size: 0.62rem;">(${s.trades}t)</span></span>
            <span style="color: ${pnlCol}; font-weight: 700;">${pnlStr} (PF ${s.profit_factor})</span>
          </div>`;
        }).join('');
      } else {
        this.elAnalyticsStrategies.innerHTML = `<div style="color: var(--text-dim); font-size: 0.68rem;">Awaiting executed trades in current session...</div>`;
      }
    }
  }
}

window.RoiPanelComponent = RoiPanelComponent;
