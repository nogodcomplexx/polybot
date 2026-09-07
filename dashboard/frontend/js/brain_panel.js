/**
 * Quantitative Brain Panel Component.
 * Displays Alpha Conviction Score breakdown, SMC Auditor Checklist, and Risk/Kelly parameters.
 */

class BrainPanelComponent {
  update(data) {
    if (!data) return;

    // Strategy & Alpha Score
    const elScore = document.getElementById('alphaScoreVal');
    const elStrat = document.getElementById('brainStrategyBadge');
    if (elScore) elScore.textContent = Math.round(data.alpha_score || 0);
    if (elStrat && data.strategy) elStrat.textContent = data.strategy;

    // Decision Badge
    const elBadge = document.getElementById('finalDecisionBadge');
    if (elBadge) {
      if (data.final_decision === 'TRADE') {
        elBadge.className = 'decision-badge trade';
        elBadge.textContent = '🟢 TRADE';
      } else {
        elBadge.className = 'decision-badge veto';
        elBadge.textContent = '❌ VETO';
      }
    }

    // Factor Breakdown Progress Bars
    const bd = data.score_breakdown || {};
    this.updateBar('Sweep', bd['Liquidity Sweep'] || bd['core_edge'] || 20, 30);
    this.updateBar('Sfp', bd['SFP'] || 15, 20);
    this.updateBar('Vwap', bd['VWAP'] || 12, 20);
    this.updateBar('Obi', bd['Orderbook'] || bd['orderflow'] || 14, 20);
    this.updateBar('Wt', bd['WT'] || bd['momentum'] || 10, 15);
    this.updateBar('Btc', bd['BTC Macro'] || bd['btc_beta'] || 8, 10);

    // SMC Auditor Gate Checks
    const smc = data.smc_auditor_checks || {};
    this.updateSmcBox('smcStructure', smc.Structure || 'PASS');
    this.updateSmcBox('smcLiquidity', smc.Liquidity || 'PASS');
    this.updateSmcBox('smcInvalidation', smc.Invalidation || 'PASS');
    this.updateSmcBox('smcRr', smc.RR || 'PASS');
    this.updateSmcBox('smcExecution', smc.Execution || 'PASS');
    this.updateSmcBox('smcMacro', smc.Macro || 'PASS');

    // Risk & Kelly
    const elEv = document.getElementById('riskEv');
    const elRr = document.getElementById('riskRr');
    const elRisk = document.getElementById('riskPct');
    const elKelly = document.getElementById('riskKelly');

    if (elEv) elEv.textContent = `${(data.expected_ev * 100).toFixed(2)}%`;
    if (elRr) elRr.textContent = `${data.rr_tp1 || 1.9} / ${data.rr_tp2 || 3.4}`;
    if (elRisk) elRisk.textContent = `${(data.risk_pct || 0.82).toFixed(2)}%`;
    if (elKelly) elKelly.textContent = `${(data.kelly_pct || 1.11).toFixed(2)}%`;
  }

  updateBar(id, val, maxVal) {
    const pt = document.getElementById(`pt${id}`);
    const bar = document.getElementById(`bar${id}`);
    if (pt) pt.textContent = `+${Math.round(val)}`;
    if (bar) {
      const pct = Math.min(100, Math.max(5, (val / maxVal) * 100));
      bar.style.width = `${pct}%`;
    }
  }

  updateSmcBox(elemId, status) {
    const el = document.getElementById(elemId);
    if (!el) return;
    el.textContent = status;
    if (status === 'PASS') {
      el.className = 'smc-box-val pass';
    } else {
      el.className = 'smc-box-val veto';
    }
  }
}

window.BrainPanelComponent = BrainPanelComponent;
