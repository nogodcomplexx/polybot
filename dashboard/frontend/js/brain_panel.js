/**
 * Quantitative Brain Panel Component.
 * Displays Alpha Conviction Score breakdown, SMC Auditor Checklist, and Risk/Kelly parameters.
 * Shows honest SCANNING state when no real telemetry data is available.
 */

class BrainPanelComponent {
  update(data) {
    if (!data) return;

    // Strategy & Alpha Score
    const elScore = document.getElementById('alphaScoreVal');
    const elStrat = document.getElementById('brainStrategyBadge');
    if (elScore) elScore.textContent = Math.round(data.alpha_score || 0);
    if (elStrat && data.strategy) elStrat.textContent = data.strategy;

    // Decision Badge — supports TRADE, VETO, SCANNING, NEUTRAL
    const elBadge = document.getElementById('finalDecisionBadge');
    if (elBadge) {
      const decision = data.final_decision || 'SCANNING';
      if (decision === 'TRADE') {
        elBadge.className = 'decision-badge trade';
        elBadge.textContent = '🟢 TRADE';
      } else if (decision === 'VETO') {
        elBadge.className = 'decision-badge veto';
        elBadge.textContent = '❌ VETO';
      } else {
        // SCANNING / NEUTRAL / NO_SETUP — honest yellow state
        elBadge.className = 'decision-badge scanning';
        elBadge.textContent = '⏳ SCANNING';
      }
    }

    // Factor Breakdown Progress Bars — use 0 as default, NOT fake values
    const bd = data.score_breakdown || {};
    this.updateBar('Sweep', bd['Liquidity Sweep'] || bd['core_edge'] || 0, 30);
    this.updateBar('Sfp', bd['SFP'] || 0, 20);
    this.updateBar('Vwap', bd['VWAP'] || 0, 20);
    this.updateBar('Obi', bd['Orderbook'] || bd['orderflow'] || 0, 20);
    this.updateBar('Wt', bd['WT'] || bd['momentum'] || 0, 15);
    this.updateBar('Btc', bd['BTC Macro'] || bd['btc_beta'] || 0, 10);

    // SMC Auditor Gate Checks — use '---' when no data
    const smc = data.smc_auditor_checks || {};
    this.updateSmcBox('smcStructure', smc.Structure || '---');
    this.updateSmcBox('smcLiquidity', smc.Liquidity || '---');
    this.updateSmcBox('smcInvalidation', smc.Invalidation || '---');
    this.updateSmcBox('smcRr', smc.RR || '---');
    this.updateSmcBox('smcExecution', smc.Execution || '---');
    this.updateSmcBox('smcMacro', smc.Macro || '---');

    // Risk & Kelly — show '---' when scanning, real values when available
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
      if (elEv) elEv.textContent = `${(data.expected_ev * 100).toFixed(2)}%`;
      if (elRr) elRr.textContent = `${data.rr_tp1 || 0} / ${data.rr_tp2 || 0}`;
      if (elRisk) elRisk.textContent = `${(data.risk_pct || 0).toFixed(2)}%`;
      if (elKelly) elKelly.textContent = `${(data.kelly_pct || 0).toFixed(2)}%`;
    }
  }

  updateBar(id, val, maxVal) {
    const pt = document.getElementById(`pt${id}`);
    const bar = document.getElementById(`bar${id}`);
    if (pt) pt.textContent = `+${Math.round(val)}`;
    if (bar) {
      const pct = val > 0 ? Math.min(100, Math.max(5, (val / maxVal) * 100)) : 5;
      bar.style.width = `${pct}%`;
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
      // '---' or unknown — neutral state
      el.className = 'smc-box-val';
    }
  }
}

window.BrainPanelComponent = BrainPanelComponent;
