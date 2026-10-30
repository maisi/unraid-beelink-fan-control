(() => {
  'use strict';
  const root = document.getElementById('bfc');
  if (!root) return;
  const endpoint = '/plugins/beelink-fan-control/api.php';
  const keys = ['cpu', 'system'];
  const names = { cpu: 'CPU-Lüfter', system: 'Gehäuselüfter' };
  const $ = id => document.getElementById(id);
  let live = null, draft = null, dirty = false, busy = false, polling = false;
  const clone = value => JSON.parse(JSON.stringify(value));
  const temperature = value => value === null || value === undefined ? 'nicht verfügbar' : `${Number(value).toFixed(1)} °C`;

  function message(text, error = false) {
    $('bfc-message').hidden = !text;
    $('bfc-message').textContent = text;
    $('bfc-message').classList.toggle('error', error);
  }
  async function request(action, config, test) {
    const options = { credentials: 'same-origin', cache: 'no-store' };
    if (action) {
      options.method = 'POST';
      options.body = new URLSearchParams({ action, csrf_token: root.dataset.csrf });
      if (config) options.body.set('config', JSON.stringify(config));
      if (test) options.body.set('test', JSON.stringify(test));
    }
    const response = await fetch(endpoint, options);
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error('Keine gültige Antwort. Bitte die Unraid-Anmeldung prüfen.'); }
    if (!response.ok || !result.ok) throw new Error(result.error || 'Anfrage fehlgeschlagen.');
    return result;
  }
  function markDirty() {
    dirty = true;
    $('bfc-dirty').textContent = 'Ungespeicherte Änderungen';
    message('');
    updateControls();
  }
  function validate() {
    if (!draft) return '';
    if (!Number.isFinite(draft.emergency_c) || draft.emergency_c < 70 || draft.emergency_c > 95) return 'CPU-Notkühlung: 70 bis 95 °C.';
    if (!Number.isFinite(draft.hysteresis_c) || draft.hysteresis_c < 0 || draft.hysteresis_c > 5) return 'Hysterese: 0 bis 5 °C.';
    for (const key of keys) {
      const fan = draft.fans[key];
      if (fan.enabled && !fan.sensor) return `${names[key]}: Bitte einen Sensor auswählen.`;
      for (let i = 0; i < fan.points.length; i++) {
        const p = fan.points[i], previous = fan.points[i - 1];
        if (!Number.isFinite(p.temp) || !Number.isFinite(p.percent) || p.temp < 0 || p.temp > 100 || p.percent < 20 || p.percent > 100)
          return `${names[key]}: Temperatur 0–100 °C, Leistung 20–100 %.`;
        if (previous && (p.temp <= previous.temp || p.percent < previous.percent))
          return `${names[key]}: Temperaturen müssen steigen; die Leistung darf nicht sinken.`;
      }
      if (fan.points[fan.points.length - 1].percent !== 100) return `${names[key]}: Letzter Punkt muss 100 % erreichen.`;
    }
    return '';
  }
  function updateControls() {
    $('bfc-save').disabled = !draft || busy || Boolean(validate());
    $('bfc-auto').disabled = !draft || busy;
    $('bfc-test-start').disabled = !live || busy || !live.service.running;
    $('bfc-test-stop').disabled = busy || !live?.test;
    $('bfc-diagnostics').disabled = busy || !live;
    if (draft && dirty && validate()) {
      $('bfc-dirty').textContent = validate();
    } else if (dirty) {
      $('bfc-dirty').textContent = 'Ungespeicherte Änderungen';
    }
  }
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function chart(key) {
    const svg = $(`bfc-chart-${key}`);
    if (!svg) return;
    const x = t => 38 + t * 3.9, y = p => 173 - p * 1.4;
    const points = draft.fans[key].points.filter(p => Number.isFinite(p.temp) && Number.isFinite(p.percent));
    const sensors = live ? live.sensors : [];
    const sensor = sensors.find(s => s.id === draft.fans[key].sensor);
    const temp = sensor ? sensor.celsius : null;
    let content = '';
    for (const p of [0, 25, 50, 75, 100]) content += `<line class="bfc-gridline" x1="38" y1="${y(p)}" x2="428" y2="${y(p)}"/><text x="30" y="${y(p) + 4}" text-anchor="end">${p}%</text>`;
    for (const t of [0, 20, 40, 60, 80, 100]) content += `<text x="${x(t)}" y="195" text-anchor="middle">${t}°</text>`;
    if (points.length) {
      const coordinates = [[x(0), y(points[0].percent)], ...points.map(p => [x(p.temp), y(p.percent)]), [x(100), y(points[points.length - 1].percent)]];
      content += `<polygon class="bfc-area" points="38,173 ${coordinates.map(p => p.join(',')).join(' ')} 428,173"/>`;
      content += `<polyline class="bfc-line" points="${coordinates.map(p => p.join(',')).join(' ')}"/>`;
      content += points.map(p => `<circle class="bfc-point" cx="${x(p.temp)}" cy="${y(p.percent)}" r="3.5"/>`).join('');
    }
    if (temp !== null && temp >= 0 && temp <= 100) content += `<line class="bfc-current" x1="${x(temp)}" y1="26" x2="${x(temp)}" y2="173"/>`;
    svg.innerHTML = content;
  }
  function renderRows(key) {
    const body = $(`bfc-rows-${key}`);
    body.replaceChildren();
    draft.fans[key].points.forEach((point, index) => {
      const row = element('tr');
      for (const [field, unit, low, high] of [['temp', '°C', 0, 100], ['percent', '%', 20, 100]]) {
        const cell = element('td'), input = element('input');
        input.type = 'number'; input.min = low; input.max = high; input.step = 'any'; input.required = true;
        input.value = point[field];
        input.setAttribute('aria-label', `${names[key]} Punkt ${index + 1} ${field === 'temp' ? 'Temperatur' : 'Leistung'} ${unit}`);
        input.addEventListener('input', () => {
          point[field] = input.value === '' ? null : Number(input.value);
          markDirty(); chart(key);
        });
        cell.append(input); row.append(cell);
      }
      const cell = element('td'), remove = element('button', 'bfc-remove', '×');
      remove.type = 'button'; remove.disabled = draft.fans[key].points.length <= 2;
      remove.setAttribute('aria-label', `${names[key]} Punkt ${index + 1} entfernen`);
      remove.addEventListener('click', () => { draft.fans[key].points.splice(index, 1); renderRows(key); markDirty(); chart(key); });
      cell.append(remove); row.append(cell); body.append(row);
    });
    $(`bfc-add-${key}`).disabled = draft.fans[key].points.length >= 10;
  }
  function render() {
    $('bfc-fans').replaceChildren();
    for (const key of keys) {
      const card = element('article', 'bfc-card');
      card.innerHTML = `<div class="bfc-card-head"><div><h3>${names[key]}</h3><span id="bfc-mode-${key}" class="bfc-mode">Automatik</span></div><div class="bfc-rpm"><span id="bfc-rpm-${key}">—</span><small>U/min · <span id="bfc-percent-${key}">Automatik</span></small></div></div>
        <label class="bfc-toggle"><input id="bfc-enabled-${key}" type="checkbox"> Eigene Lüfterkurve aktivieren</label>
        <label for="bfc-sensor-${key}">Temperatursensor</label><select id="bfc-sensor-${key}"></select>
        <div id="bfc-sensor-reading-${key}" class="bfc-sensor-reading"></div>
        <svg id="bfc-chart-${key}" class="bfc-chart" viewBox="0 0 450 205" role="img" aria-label="Kurvenvorschau ${names[key]}"></svg>
        <table class="bfc-curve"><thead><tr><th>Temperatur · °C</th><th>Leistung · %</th><th></th></tr></thead><tbody id="bfc-rows-${key}"></tbody></table>
        <button id="bfc-add-${key}" class="bfc-add" type="button">+ Kurvenpunkt</button>
        <p class="bfc-card-help">Mindestens 20 % Leistung. Der letzte Punkt erreicht 100 %. Die gestrichelte Linie zeigt die aktuelle Sensortemperatur.</p>`;
      $('bfc-fans').append(card);
      const fan = draft.fans[key], toggle = $(`bfc-enabled-${key}`), select = $(`bfc-sensor-${key}`);
      toggle.checked = fan.enabled;
      toggle.addEventListener('change', () => { fan.enabled = toggle.checked; markDirty(); });
      select.append(new Option('Sensor auswählen …', ''));
      for (const sensor of live.sensors) {
        const option = new Option(`${sensor.label} · ${sensor.chip}`, sensor.id);
        option.disabled = sensor.celsius === null;
        select.append(option);
      }
      if (fan.sensor && !live.sensors.some(s => s.id === fan.sensor)) select.append(new Option('Gespeicherter Sensor nicht verfügbar', fan.sensor));
      select.value = fan.sensor;
      select.addEventListener('change', () => { fan.sensor = select.value; markDirty(); updateLive(); });
      $(`bfc-add-${key}`).addEventListener('click', () => {
        let at = 1, gap = -1;
        for (let i = 1; i < fan.points.length; i++) {
          const width = fan.points[i].temp - fan.points[i - 1].temp;
          if (width > gap) { gap = width; at = i; }
        }
        const a = fan.points[at - 1], b = fan.points[at];
        fan.points.splice(at, 0, { temp: (a.temp + b.temp) / 2, percent: (a.percent + b.percent) / 2 });
        renderRows(key); markDirty(); chart(key);
      });
      renderRows(key);
    }
    $('bfc-emergency').value = draft.emergency_c;
    $('bfc-hysteresis').value = draft.hysteresis_c;
    updateControls(); updateLive();
  }
  function updateLive() {
    if (!live || !draft) return;
    const service = live.service;
    $('bfc-service').textContent = service.error ? service.error : service.running ? 'Regelungsdienst bereit' : 'Regelungsdienst angehalten';
    $('bfc-service-dot').className = `bfc-dot ${service.error ? 'error' : service.running ? 'running' : ''}`;
    for (const key of keys) {
      const fan = live.fans[key], target = service.running && service.targets ? service.targets[key] : null;
      const badge = $(`bfc-mode-${key}`);
      let mode = !fan.available ? 'Treiber fehlt' : fan.mode === 2 ? 'Hardware-Automatik' : 'Manuelle Regelung';
      let warning = false;
      if (target) {
        const modes = { curve: 'Eigene Kurve', test: 'Testmodus · feste Leistung', sensor_error: 'Sensorfehler · 100 %', emergency: 'CPU-Notkühlung · 100 %', fan_error: 'Drehzahlfehler · 100 %' };
        mode = modes[target.reason] || mode;
        warning = !['curve', 'test'].includes(target.reason);
      }
      badge.textContent = mode;
      badge.className = `bfc-mode ${warning ? 'alert' : target ? 'active' : ''}`;
      $(`bfc-rpm-${key}`).textContent = fan.rpm === null ? '—' : fan.rpm.toLocaleString('de-AT');
      $(`bfc-percent-${key}`).textContent = target ? `${target.percent.toFixed(0)} %` : fan.percent !== null ? `${fan.percent.toFixed(0)} %` : 'Automatik';
      const sensor = live.sensors.find(s => s.id === draft.fans[key].sensor);
      $(`bfc-sensor-reading-${key}`).textContent = `Aktuell: ${temperature(sensor ? sensor.celsius : null)}`;
      chart(key);
    }
    $('bfc-updated').textContent = `Aktualisiert ${new Date().toLocaleTimeString('de-AT')}`;
    const test = live.test;
    $('bfc-test-status').textContent = test ? `${names[test.fan]}: ${test.percent} % angefordert · noch ${Math.ceil(test.remaining_s)} s. ${service.targets?.[test.fan]?.reason === 'test' ? 'Test aktiv.' : 'Übernahme ausstehend oder Schutzfunktion aktiv.'}` : 'Kein Test aktiv.';
    updateControls();
  }
  async function poll() {
    if (polling || busy) return;
    polling = true;
    try {
      live = await request();
      if (!draft) { draft = clone(live.config); render(); }
      else updateLive();
    } catch (error) {
      $('bfc-service').textContent = 'Verbindung unterbrochen';
      $('bfc-service-dot').className = 'bfc-dot error';
      $('bfc-test-status').textContent = 'Verbindung unterbrochen. Ein laufender Test endet nach seiner Zeitbegrenzung.';
      message(error.message, true);
    } finally { polling = false; }
  }
  async function testAction(action) {
    if (busy) return;
    const test = { fan: $('bfc-test-fan').value, percent: Number($('bfc-test-percent').value), duration: Number($('bfc-test-duration').value) };
    busy = true; updateControls();
    try {
      live = await request(action, undefined, action === 'test_start' ? test : undefined);
      updateLive();
      message(action === 'test_stop' ? 'Test beendet. Die bisherige Regelung wird innerhalb von zwei Sekunden übernommen.' : 'Test angefordert. Temperatur- und Ausfallschutz bleiben aktiv.');
    } catch (error) { message(error.message, true); }
    finally { busy = false; updateControls(); }
  }
  $('bfc-test-form').addEventListener('submit', event => { event.preventDefault(); testAction('test_start'); });
  $('bfc-test-stop').addEventListener('click', () => testAction('test_stop'));
  $('bfc-diagnostics').addEventListener('click', async () => {
    try {
      const response = await fetch(`${endpoint}?action=diagnostics`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Diagnose nicht verfügbar.');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = element('a'); link.href = url; link.download = `beelink-diagnose-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { message(error.message, true); }
  });
  async function apply(action) {
    const error = action === 'save' ? validate() : '';
    if (error) { message(error, true); return; }
    busy = true; updateControls();
    for (const field of root.querySelectorAll('input, select, .bfc-add, .bfc-remove')) field.disabled = true;
    try {
      live = await request(action, action === 'save' ? draft : undefined);
      draft = clone(live.config); dirty = false;
      $('bfc-dirty').textContent = '';
      render();
      message(action === 'auto' ? 'Gespeichert. Beide Lüfter kehren zur Hardware-Automatik zurück.' : 'Gespeichert. Die Regelung übernimmt die Einstellungen innerhalb von zwei Sekunden.');
    } catch (failure) { message(failure.message, true); }
    finally {
      busy = false;
      for (const field of root.querySelectorAll('input, select')) field.disabled = false;
      keys.forEach(renderRows);
      updateControls();
    }
  }
  $('bfc-form').addEventListener('submit', event => { event.preventDefault(); apply('save'); });
  $('bfc-auto').addEventListener('click', () => apply('auto'));
  $('bfc-emergency').addEventListener('input', event => { draft.emergency_c = event.target.value === '' ? null : Number(event.target.value); markDirty(); });
  $('bfc-hysteresis').addEventListener('input', event => { draft.hysteresis_c = event.target.value === '' ? null : Number(event.target.value); markDirty(); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  poll();
  setInterval(poll, 3000);
})();
