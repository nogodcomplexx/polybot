/**
 * Alert Center Component.
 * Categorizes and displays real-time operational alerts (CRITICAL, WARNING, INFO).
 */

class AlertCenterComponent {
  constructor() {
    this.list = document.getElementById('alertList');
    this.maxAlerts = 20;
  }

  addAlert(alert) {
    if (!this.list) return;

    const item = document.createElement('div');
    const timeStr = new Date(alert.timestamp * 1000).toLocaleTimeString();

    let borderCol = 'var(--neon-green)';
    let badgeText = '[INFO]';
    let badgeCol = 'var(--neon-green)';
    let bg = 'rgba(0, 255, 136, 0.08)';

    if (alert.severity === 'CRITICAL') {
      borderCol = 'var(--neon-red)';
      badgeText = '🚨 [CRITICAL]';
      badgeCol = 'var(--neon-red)';
      bg = 'rgba(255, 51, 102, 0.15)';
    } else if (alert.severity === 'WARNING') {
      borderCol = 'var(--neon-amber)';
      badgeText = '⚠ [WARNING]';
      badgeCol = 'var(--neon-amber)';
      bg = 'rgba(255, 183, 0, 0.12)';
    }

    item.style.cssText = `background: ${bg}; border-left: 3px solid ${borderCol}; padding: 4px 8px; border-radius: 2px;`;
    item.innerHTML = `
      <div style="display: flex; justify-content: space-between; font-size: 0.65rem; color: var(--text-dim); margin-bottom: 2px;">
        <span style="color: ${badgeCol}; font-weight: 700;">${badgeText} ${alert.title || ''}</span>
        <span>${timeStr}</span>
      </div>
      <div>${alert.message}</div>
    `;

    this.list.insertBefore(item, this.list.firstChild);
    while (this.list.children.length > this.maxAlerts) {
      this.list.removeChild(this.list.lastChild);
    }
  }
}

window.AlertCenterComponent = AlertCenterComponent;
