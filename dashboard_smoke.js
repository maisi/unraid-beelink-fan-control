const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const artifacts = path.join(__dirname, 'artifacts');
fs.mkdirSync(artifacts, { recursive: true });

(async () => {
  const options = { headless: true };
  if (process.env.BEELINK_CHROMIUM_EXECUTABLE) options.executablePath = process.env.BEELINK_CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch(options);
  try {
    const page = await browser.newPage({viewport:{width:420,height:700}});
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    const initial = {
      ok:true,
      config:{fans:{cpu:{enabled:true,sensor:'cpu'},system:{enabled:true,sensor:'nvme'}}},
      sensors:[{id:'cpu',label:'CPU-Paket',celsius:68},{id:'nvme',label:'NVMe · Composite',celsius:51.85}],
      fans:{cpu:{available:true,rpm:3994,mode:1,percent:58},system:{available:true,rpm:2454,mode:1,percent:44}},
      service:{running:true,error:null,updated:Date.now()/1000,targets:{cpu:{percent:58,reason:'curve'},system:{percent:44,reason:'curve'}}}
    };
    let state = structuredClone(initial), failed = false;
    const tile = fs.readFileSync(path.join(__dirname,'BeelinkFans.Dashboard.page'),'utf8').split("<<<'HTML'\n")[1].split('\nHTML;')[0];
    await page.route('http://beelink.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('api.php')) {
        requests.push(route.request().method());
        return route.fulfill({status:failed?503:200,contentType:'application/json',headers:{Date:new Date().toUTCString()},body:JSON.stringify(state)});
      }
      if (url.pathname.endsWith('dashboard.js') || url.pathname.endsWith('dashboard.css'))
        return route.fulfill({path:path.join(__dirname,path.basename(url.pathname))});
      return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;background:#fafafa;color:#2b3035;margin:20px}.tile-header{display:flex;justify-content:space-between;padding:8px 0 16px}.tile-header-left{display:flex;gap:12px}.tile-header-main{font-size:18px;margin:0 0 6px}.tile-header-right{font-size:13px}table{width:360px;max-width:100%;border:1px solid #ddd;border-radius:8px;padding:16px}td{padding:0}</style></head><body><table>${tile}</table></body></html>`});
    });
    await page.goto('http://beelink.test/Dashboard');
    const summary = page.locator('#bfc-dashboard [data-bfc=summary]');
    const row = key => page.locator(`#bfc-dashboard [data-fan=${key}]`);
    const refresh = async expected => {
      await page.waitForTimeout(100);
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      await page.waitForFunction(text => document.querySelector('#bfc-dashboard [data-bfc=summary]').textContent === text, expected);
    };
    await page.waitForFunction(() => document.querySelector('#bfc-dashboard [data-bfc=summary]').textContent === 'Steuerung aktiv · 2/2 Lüfter');
    assert.equal((await row('cpu').locator('[data-bfc=rpm]').innerText()).replace(/\D/g,''),'3994');
    assert.match(await row('cpu').locator('[data-bfc=detail]').innerText(),/58 %.*CPU-Paket.*68/);
    assert.equal(await row('system').locator('[data-bfc=mode]').innerText(),'Eigene Kurve aktiv');
    await page.locator('#bfc-dashboard').screenshot({path:path.join(artifacts,'dashboard-preview.png')});
    state.config.fans.cpu.enabled=false;
    state.service.targets.cpu={percent:80,reason:'test'};state.fans.cpu.percent=80;
    await refresh('Testmodus aktiv');
    assert.equal(await row('cpu').locator('[data-bfc=mode]').innerText(),'Testmodus · feste Leistung');
    state=structuredClone(initial);
    state.config.fans.cpu.enabled=false; delete state.service.targets.cpu;
    state.fans.cpu.mode=2;state.fans.cpu.percent=null;
    await refresh('Steuerung aktiv · 1/2 Lüfter');
    assert.equal(await row('cpu').locator('[data-bfc=mode]').innerText(),'Hardware-Automatik');
    assert.equal((await row('cpu').locator('[data-bfc=detail]').innerText()).includes('%'),false);
    state.config.fans.system.enabled=false;state.service.targets={};state.fans.system.mode=2;state.fans.system.percent=null;
    await refresh('Hardware-Automatik');
    state=structuredClone(initial);
    state.service.targets.cpu={percent:100,reason:'sensor_error'};state.fans.cpu.mode=0;state.fans.cpu.percent=100;state.sensors[0].celsius=null;
    await refresh('Schutzfunktion aktiv');
    assert.equal(await row('cpu').locator('[data-bfc=mode]').innerText(),'Sensorfehler · 100 %');
    state.service.running=false;state.fans.cpu.mode=2;state.fans.system.mode=2;
    await refresh('Dienst angehalten');
    assert.equal(await row('cpu').locator('[data-bfc=mode]').innerText(),'Hardware-Automatik');
    state=structuredClone(initial);state.service.updated=Date.now()/1000-60;
    await refresh('Status veraltet');
    assert.equal(await row('cpu').locator('[data-bfc=mode]').innerText(),'Manuelle Regelung');
    state=structuredClone(initial);state.service.error='Ein anderer Regler ist aktiv.';state.service.targets={};
    await refresh('Regelungsfehler');
    assert.equal(await page.locator('#bfc-dashboard [data-bfc=warning]').innerText(),'Ein anderer Regler ist aktiv.');
    state=structuredClone(initial);state.service.targets={};state.fans.cpu.mode=2;state.fans.system.mode=2;
    await refresh('Eigene Steuerung wartet');
    failed=true;await refresh('Verbindung unterbrochen');
    assert.equal(await row('cpu').locator('[data-bfc=rpm]').innerText(),'—');
    failed=false;state=structuredClone(initial);state.service.updated=Date.now()/1000;
    await refresh('Steuerung aktiv · 2/2 Lüfter');
    await page.setViewportSize({width:340,height:700});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await page.locator('#bfc-dashboard a').getAttribute('href'),'/Settings/BeelinkFans');
    assert.ok(requests.length>=9 && requests.every(method=>method==='GET'));
    assert.deepEqual(errors,[]);
    console.log('Dashboard smoke passed: active/partial/automatic/protection/stopped/stale/error/waiting/offline states, mobile layout and GET-only polling.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
