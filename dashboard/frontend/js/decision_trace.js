/**
 * Decision Trace Terminal Component.
 * Displays live, atomic candidate evaluation steps, feature scores, and veto gate comparisons.
 */

class DecisionTraceComponent {
  constructor() {
    this.terminal = document.getElementById('decisionTraceTerminal');
    this.maxLines = 100;
    this.setupEvents();
  }

  setupEvents() {
    const btnClear = document.getElementById('btnClearTrace');
    if (btnClear && this.terminal) {
      btnClear.addEventListener('click', () => {
        this.terminal.innerHTML = '';
      });
    }
  }

  addStep(step) {
    if (!this.terminal) return;

    const line = document.createElement('div');
    line.className = 'trace-line';

    const timeStr = new Date(step.timestamp * 1000).toLocaleTimeString();
    const isVeto = step.status === 'VETO';

    line.innerHTML = `
      <span class="trace-time">${timeStr}</span>
      <span class="trace-stage ${step.stage}">${step.stage}</span>
      <span class="trace-msg ${isVeto ? 'veto' : ''}">${step.message}</span>
    `;

    this.terminal.appendChild(line);
    while (this.terminal.children.length > this.maxLines) {
      this.terminal.removeChild(this.terminal.firstChild);
    }

    // Auto-scroll to bottom
    this.terminal.scrollTop = this.terminal.scrollHeight;
  }

  hydrate(traces) {
    if (!traces || !Array.isArray(traces)) return;
    for (const t of traces) {
      this.addStep(t);
    }
  }
}

window.DecisionTraceComponent = DecisionTraceComponent;
