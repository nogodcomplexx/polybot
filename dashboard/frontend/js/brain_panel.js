/**
 * Quantitative Brain Panel Component.
 * Displays Alpha Conviction Score breakdown, SMC Auditor Checklist, and Risk/Kelly parameters.
 * Shows honest SCANNING state when no real telemetry data is available.
 */

class BrainPanelComponent {
  update(data) {
    if (!data) return;

    const elScore = document.getElementById('alphaScoreVal');
    const elStrat = document.getElementById('brainStrategyBadge');
    if (elScore) elScore.textContent = Math.round(data.alpha_score || 0);
    if (elStrat && data.strategy) elStrat.textContent = data.strategy;

    const elBadge = document.getElementById('finalDecisionBadge');
    if (elBadge) {
      const decision = data.final_decision || 'SCANNING';
      if (decision === 'TRADE') {
        elBadge.className = 'decision-badge trade';
        elBadge.textContent = '🟢 TRADE';
      } else if (decision === 'IN_TRADE' || decision === 'ACTIVE') {
        elBadge.className = 'decision-badge active';
        elBadge.textContent = `🔵 ACTIVE (${data.symbol || 'IN TRADE'})`;
      } else if (decision === 'VETO') {
        elBadge.className = 'decision-badge veto';
        elBadge.textContent = '❌ VETO';
      } else {
        elBadge.className = 'decision-badge scanning';
        elBadge.textContent = '⏳ SCANNING';
      }
    }

    // Factor Breakdown Progress Bars mapped to AlphaScorer's 6 pillars (with fallback for demo keys)
    const bd = data.score_breakdown || {};
    this.updateBar('Sweep', bd.core_edge !== undefined ? bd.core_edge : (bd['Liquidity Sweep'] || 0), 35);
    this.updateBar('Sfp', bd.regime_liquidity !== undefined ? bd.regime_liquidity : (bd['SFP'] || 0), 12);
    this.updateBar('Vwap', bd.cross_venue !== undefined ? bd.cross_venue : (bd['VWAP'] || 0), 8);
    this.updateBar('Obi', bd.orderflow !== undefined ? bd.orderflow : (bd['Orderbook'] || 0), 20);
    this.updateBar('Wt', bd.momentum !== undefined ? bd.momentum : (bd['WT'] || 0), 15);
    this.updateBar('Btc', bd.btc_beta !== undefined ? bd.btc_beta : (bd['BTC Macro'] || 0), 10);

    const smc = data.smc_auditor_checks || {};
    this.updateSmcBox('smcStructure', smc.Structure || '---');
    this.updateSmcBox('smcLiquidity', smc.Liquidity || '---');
    this.updateSmcBox('smcInvalidation', smc.Invalidation || '---');
    this.updateSmcBox('smcRr', smc.RR || '---');
    this.updateSmcBox('smcExecution', smc.Execution || '---');
    this.updateSmcBox('smcMacro', smc.Macro || '---');

    const isScanning = !data.final_decision || data.final_decision === 'SCANNING';

    const elEv = document.getElementById('riskEv');
    const elRr = document.getElementById('riskRr');
    const elRisk = document.getElementById('riskPct');
    const elKelly = document.getElementById('riskKelly');

    if (isScanning) {
      if (elEv) elEv.textContent = '---';
      if (elRr) elRr.textContent = '--- / ---';
      if (elRisk) elRisk.textContent = '---';
      if (elKelly) elKelly.textContent = '---';
    } else {
      if (elEv) elEv.textContent = `${((data.expected_ev || 0) * 100).toFixed(2)}%`;
      if (elRr) elRr.textContent = `${data.rr_tp1 || 0} / ${data.rr_tp2 || 0}`;
      if (elRisk) elRisk.textContent = `${(data.risk_pct || 0).toFixed(2)}%`;
      if (elKelly) elKelly.textContent = `${(data.kelly_pct || 0).toFixed(1)}%`;
    }
  }

  updateBar(id, val, maxVal) {
    const pt = document.getElementById(`pt${id}`);
    const bar = document.getElementById(`bar${id}`);
    const num = Number(val || 0);
    if (pt) {
      pt.textContent = `${num >= 0 ? '+' : ''}${Math.round(num)}`;
      pt.style.color = num < 0 ? 'var(--neon-red)' : 'var(--bb-amber)';
    }
    if (bar) {
      const pct = num > 0 ? Math.min(100, Math.max(5, (num / maxVal) * 100)) : 5;
      bar.style.width = `${pct}%`;
      bar.style.background = num < 0 ? 'var(--neon-red)' : 'var(--bb-amber)';
    }
  }

  updateSmcBox(elemId, status) {
    const el = document.getElementById(elemId);
    if (!el) return;
    el.textContent = status;
    if (status === 'PASS') {
      el.className = 'smc-box-val pass';
    } else if (status === 'VETO' || status === 'FAIL') {
      el.className = 'smc-box-val veto';
    } else {
      el.className = 'smc-box-val';
    }
  }
}

window.BrainPanelComponent = BrainPanelComponent;
