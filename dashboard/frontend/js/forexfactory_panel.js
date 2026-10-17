/**
 * ForexFactory Economic Calendar Component (Bloomberg Terminal Edition)
 * Fetches real-time macroeconomic announcements from /api/forexfactory
 * Supports impact filtering, currency filtering, and TradingView calendar widget toggle.
 */

class ForexFactoryComponent {
  constructor() {
    this.events = [];
    this.filterImpact = 'all'; // 'all' or 'high'
    this.filterCurrency = 'all'; // 'all' or 'USD'
    this.filterToday = false;
    this.activeView = 'table'; // 'table' or 'tv-widget'

    this.tableBody = document.getElementById('forexFactoryTableBody');
    this.statusTag = document.getElementById('ffStatusTag');
    this.nextHighEl = document.getElementById('ffNextHighImpact');
    this.sourceTag = document.getElementById('ffSourceTag');

    this.initControls();
    this.fetchEvents();
    // Auto-refresh every 3 minutes
    setInterval(() => this.fetchEvents(), 180000);
  }

  initControls() {
    // Filter by Impact
    const btnAllImpact = document.getElementById('btnFfAllImpact');
    const btnHighImpact = document.getElementById('btnFfHighImpact');
    if (btnAllImpact && btnHighImpact) {
      btnAllImpact.addEventListener('click', () => {
        this.filterImpact = 'all';
        btnAllImpact.classList.add('active');
        btnHighImpact.classList.remove('active');
        this.renderTable();
      });
      btnHighImpact.addEventListener('click', () => {
        this.filterImpact = 'high';
        btnHighImpact.classList.add('active');
        btnAllImpact.classList.remove('active');
        this.renderTable();
      });
    }

    // Filter by USD
    const btnUsdOnly = document.getElementById('btnFfUsdOnly');
    if (btnUsdOnly) {
      btnUsdOnly.addEventListener('click', () => {
        this.filterCurrency = this.filterCurrency === 'USD' ? 'all' : 'USD';
        btnUsdOnly.classList.toggle('active', this.filterCurrency === 'USD');
        this.renderTable();
      });
    }

    // Filter by Today
    const btnToday = document.getElementById('btnFfToday');
    if (btnToday) {
      btnToday.addEventListener('click', () => {
        this.filterToday = !this.filterToday;
        btnToday.classList.toggle('active', this.filterToday);
        this.renderTable();
      });
    }

    // Manual Refresh
    const btnRefresh = document.getElementById('btnFfRefresh');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        btnRefresh.textContent = '⏳ FETCHING...';
        this.fetchEvents(() => {
          btnRefresh.textContent = '🔄 REFRESH';
        });
      });
    }

    // Toggle View (Table vs TradingView Calendar Widget)
    const btnToggleTv = document.getElementById('btnFfToggleTv');
    if (btnToggleTv) {
      btnToggleTv.addEventListener('click', () => {
        this.activeView = this.activeView === 'table' ? 'tv-widget' : 'table';
        const tvContainer = document.getElementById('ffTvWidgetContainer');
        const tblContainer = document.getElementById('ffTableContainer');
        if (this.activeView === 'tv-widget') {
          btnToggleTv.textContent = '📋 VIEW DATA TABLE';
          if (tvContainer) tvContainer.style.display = 'block';
          if (tblContainer) tblContainer.style.display = 'none';
          this.ensureTvCalendarWidgetLoaded();
        } else {
          btnToggleTv.textContent = '📊 VIEW TV WIDGET';
          if (tvContainer) tvContainer.style.display = 'none';
          if (tblContainer) tblContainer.style.display = 'block';
        }
      });
    }
  }

  async fetchEvents(cb = null) {
    try {
      if (this.statusTag) this.statusTag.textContent = 'POLLING...';
      const resp = await fetch('/api/forexfactory');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      this.events = data.events || [];
      if (this.statusTag) {
        this.statusTag.textContent = `LIVE FEED (${this.events.length} EVENTS)`;
        this.statusTag.className = 'bb-pill bb-green';
      }
      if (this.sourceTag && data.source) {
        this.sourceTag.textContent = `SRC: ${data.source.toUpperCase()}`;
      }
      this.updateNextHighImpact();
      this.renderTable();
    } catch (err) {
      console.warn('Failed to fetch ForexFactory events:', err);
      if (this.statusTag) {
        this.statusTag.textContent = 'OFFLINE / CACHED';
        this.statusTag.className = 'bb-pill bb-amber';
      }
    } finally {
      if (cb) cb();
    }
  }

  updateNextHighImpact() {
    if (!this.nextHighEl || !this.events.length) return;
    const now = new Date();
    // Find next upcoming high impact event
    const upcomingHigh = this.events.find(ev => {
      const imp = (ev.impact || '').toLowerCase();
      if (imp !== 'high') return false;
      const d = new Date(ev.date);
      return !isNaN(d.getTime()) && d >= now;
    });

    if (upcomingHigh) {
      const d = new Date(upcomingHigh.date);
      const diffMs = d - now;
      const diffHrs = Math.floor(diffMs / 3600000);
      const diffMins = Math.floor((diffMs % 3600000) / 60000);
      const timeStr = diffHrs > 0 ? `${diffHrs}h ${diffMins}m` : `${diffMins}m`;
      this.nextHighEl.innerHTML = `🚨 NEXT HIGH IMPACT: <span style="color: var(--bb-amber); font-weight: 700;">${upcomingHigh.title}</span> (${upcomingHigh.country}) in <span style="color: var(--bb-red); font-weight: 800;">${timeStr}</span>`;
    } else {
      // Find today's major or display default
      this.nextHighEl.innerHTML = `🟢 NO IMMINENT HIGH-IMPACT THREATS IN NEXT 12H`;
    }
  }

  renderTable() {
    if (!this.tableBody) return;
    const now = new Date();
    const todayStr = now.toISOString().slice(0, 10);

    const filtered = this.events.filter(ev => {
      // Impact filter
      if (this.filterImpact === 'high') {
        const imp = (ev.impact || '').toLowerCase();
        if (imp !== 'high') return false;
      }
      // Currency filter
      if (this.filterCurrency === 'USD') {
        if ((ev.country || '').toUpperCase() !== 'USD') return false;
      }
      // Today filter
      if (this.filterToday && ev.date) {
        if (!ev.date.startsWith(todayStr)) return false;
      }
      return true;
    });

    if (!filtered.length) {
      this.tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--bb-dim); padding: 20px;">No events match active filter criteria</td></tr>`;
      return;
    }

    let rowsHtml = '';
    filtered.forEach(ev => {
      const d = new Date(ev.date);
      const timeStr = !isNaN(d.getTime()) 
        ? `${d.getUTCHours().toString().padStart(2, '0')}:${d.getUTCMinutes().toString().padStart(2, '0')} UTC` 
        : (ev.time || '---');
      const dateStr = !isNaN(d.getTime())
        ? `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
        : '';

      const impact = (ev.impact || 'low').toLowerCase();
      let impactBadge = '';
      if (impact === 'high') {
        impactBadge = '<span class="ff-badge ff-high">🔴 HIGH</span>';
      } else if (impact === 'medium' || impact === 'med') {
        impactBadge = '<span class="ff-badge ff-med">🟡 MED</span>';
      } else {
        impactBadge = '<span class="ff-badge ff-low">⚪ LOW</span>';
      }

      const country = ev.country || 'USD';
      const actual = ev.actual !== undefined && ev.actual !== null ? ev.actual : '';
      const forecast = ev.forecast || '';
      const previous = ev.previous || '';

      // Color actual if available
      let actualClass = '';
      if (actual && forecast) {
        const actNum = parseFloat(actual.replace(/[^0-9.-]/g, ''));
        const fctNum = parseFloat(forecast.replace(/[^0-9.-]/g, ''));
        if (!isNaN(actNum) && !isNaN(fctNum)) {
          if (actNum > fctNum) actualClass = 'bb-green';
          else if (actNum < fctNum) actualClass = 'bb-red';
        }
      }

      const rowHighlight = impact === 'high' ? 'class="ff-row-high"' : '';

      rowsHtml += `
        <tr ${rowHighlight}>
          <td class="ff-time">${dateStr ? dateStr + ' ' : ''}${timeStr}</td>
          <td class="ff-currency"><strong>${country}</strong></td>
          <td class="ff-impact">${impactBadge}</td>
          <td class="ff-title" title="${ev.title}">${ev.title}</td>
          <td class="ff-val ${actualClass}"><strong>${actual || '---'}</strong></td>
          <td class="ff-val">${forecast || '---'}</td>
          <td class="ff-val">${previous || '---'}</td>
        </tr>
      `;
    });

    this.tableBody.innerHTML = rowsHtml;
  }

  ensureTvCalendarWidgetLoaded() {
    const container = document.getElementById('ffTvWidgetInner');
    if (!container || container.children.length > 0) return;

    // Embed TradingView Economic Calendar Widget
    const script = document.createElement('script');
    script.type = 'text/javascript';
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-events.js';
    script.async = true;
    script.innerHTML = JSON.stringify({
      colorTheme: 'dark',
      isTransparent: true,
      width: '100%',
      height: '100%',
      locale: 'en',
      importanceFilter: '-1,0,1',
      countryFilter: 'us,eu,gb,jp,cn'
    });

    const widgetHolder = document.createElement('div');
    widgetHolder.className = 'tradingview-widget-container__widget';
    container.appendChild(widgetHolder);
    container.appendChild(script);
  }
}

window.ForexFactoryComponent = ForexFactoryComponent;
