(() => {
  'use strict';
  function start() {
    const root = document.getElementById('bfc-dashboard');
    if (!root || root.dataset.bfcInitialized) return;
    root.dataset.bfcInitialized = 'true';
    const keys = ['cpu', 'system'];
    const field = name => root.querySelector(`[data-bfc="${name}"]`);
    const summary = root.querySelector('.bfc-dash-summary');
    const formatter = new Intl.NumberFormat('de-AT', { maximumFractionDigits: 0 });
    const temp = value => Number.isFinite(value) ? `${value.toLocaleString('de-AT', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} °C` : 'Sensor nicht verfügbar';
    let polling = false, lastGood = null;

    function render(state, serverTime) {
      const service = state.service || {};
      const fans = state.fans || {};
      const configured = state.config?.fans || {};
      const age = Number.isFinite(service.updated) ? Math.max(0, (serverTime / 1000) - service.updated) : Infinity;
      const fresh = Boolean(service.running) && age <= 10 && !service.error;
      let active = 0, protection = false, missing = false, manual = false;
      for (const key of keys) {
        const row = root.querySelector(`[data-fan="${key}"]`);
        const fan = fans[key] || {};
        const target = fresh && fan.available && fan.mode !== 2 ? service.targets?.[key] : null;
        const sensor = state.sensors?.find(item => item.id === configured[key]?.sensor);
        row.querySelector('[data-bfc="rpm"]').textContent = Number.isFinite(fan.rpm) ? formatter.format(fan.rpm) : '—';
        const details = [];
        const percent = target?.percent ?? fan.percent;
        if (fan.mode !== 2 && Number.isFinite(percent)) details.push(`${Math.round(percent)} %`);
        if (sensor) details.push(`${sensor.label}: ${temp(sensor.celsius)}`);
        else details.push('Sensor nicht verfügbar');
        row.querySelector('[data-bfc="detail"]').textContent = details.join(' · ');
        let text = 'Hardware-Automatik', tone = '';
        if (!fan.available) { text = 'Treiber fehlt'; tone = 'error'; missing = true; }
        else if (target) {
          active++;
          const reasons = {
            curve: ['Eigene Kurve aktiv', 'active'],
            sensor_error: ['Sensorfehler · 100 %', 'error'],
            fan_error: ['Drehzahlfehler · 100 %', 'error'],
            emergency: ['CPU-Notkühlung · 100 %', 'warning']
          };
          [text, tone] = reasons[target.reason] || ['Eigene Steuerung aktiv', 'active'];
          protection ||= target.reason !== 'curve';
        } else if (fan.mode !== 2) {
          manual = true;
          text = fan.mode === 0 ? 'Vollgas' : fan.mode === 1 ? 'Manuelle Regelung' : 'Modus nicht verfügbar';
          tone = 'warning';
        }
        const badge = row.querySelector('[data-bfc="mode"]');
        badge.textContent = text;
        badge.className = `bfc-dash-mode ${tone}`;
      }

      let label = 'Hardware-Automatik', tone = '';
      if (service.error) { label = 'Regelungsfehler'; tone = 'error'; }
      else if (missing) { label = 'Treiber fehlt'; tone = 'error'; }
      else if (!service.running) { label = 'Dienst angehalten'; tone = 'warning'; }
      else if (age > 10) { label = 'Status veraltet'; tone = 'warning'; }
      else if (protection) { label = 'Schutzfunktion aktiv'; tone = 'warning'; }
      else if (active) { label = `Steuerung aktiv · ${active}/2 Lüfter`; tone = 'active'; }
      else if (keys.some(key => configured[key]?.enabled)) { label = 'Eigene Steuerung wartet'; tone = 'warning'; }
      else if (manual) { label = 'Manuelle Regelung'; tone = 'warning'; }
      field('summary').textContent = label;
      summary.className = `bfc-dash-summary ${tone}`;
      field('service').textContent = service.running ? 'Regelungsdienst läuft' : 'Regelungsdienst aus';
      field('warning').textContent = service.error || (service.running && age > 10 ? 'Der Regler liefert noch keinen aktuellen Status.' : '');
      field('warning').hidden = !field('warning').textContent;
      lastGood = new Date().toLocaleTimeString('de-AT');
      field('updated').textContent = `Live · ${lastGood}`;
    }

    function offline(message) {
      field('summary').textContent = 'Verbindung unterbrochen';
      summary.className = 'bfc-dash-summary error';
      field('service').textContent = 'Dienststatus unbekannt';
      field('updated').textContent = lastGood ? `Zuletzt ${lastGood}` : 'Keine Live-Daten';
      field('warning').textContent = message;
      field('warning').hidden = false;
      for (const key of keys) {
        const row = root.querySelector(`[data-fan="${key}"]`);
        row.querySelector('[data-bfc="rpm"]').textContent = '—';
        row.querySelector('[data-bfc="detail"]').textContent = 'Keine aktuellen Messwerte';
        const badge = row.querySelector('[data-bfc="mode"]');
        badge.textContent = 'Status unbekannt';
        badge.className = 'bfc-dash-mode';
      }
    }

    async function poll() {
      if (polling || document.hidden || !root.isConnected) return;
      polling = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch('/plugins/beelink-fan-control/api.php', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal
        });
        if (!response.ok) throw new Error('Status konnte nicht geladen werden.');
        const state = await response.json();
        if (!state.ok) throw new Error(state.error || 'Lüfterstatus nicht verfügbar.');
        const serverTime = Date.parse(response.headers.get('Date'));
        render(state, Number.isFinite(serverTime) ? serverTime : Date.now());
      } catch (error) {
        offline(error.name === 'AbortError' ? 'Zeitüberschreitung beim Laden des Lüfterstatus.' : 'Status nicht verfügbar. Bitte die Unraid-Anmeldung und den Lüfterdienst prüfen.');
      } finally {
        clearTimeout(timeout);
        polling = false;
      }
    }

    const onVisible = () => { if (!document.hidden) poll(); };
    const interval = setInterval(() => {
      if (root.isConnected) poll();
      else { clearInterval(interval); document.removeEventListener('visibilitychange', onVisible); }
    }, 5000);
    document.addEventListener('visibilitychange', onVisible);
    // Browsers freeze timers in their back/forward cache and resume them later.
    window.addEventListener('pageshow', onVisible);
    poll();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
