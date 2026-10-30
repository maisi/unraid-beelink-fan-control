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
    const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const id = letter => letter.repeat(24);
    let config = { version: 1, emergency_c: 85, hysteresis_c: 2, fans: {
      cpu: { enabled: false, sensor: id('a'), points: [{temp:35,percent:35},{temp:50,percent:45},{temp:65,percent:65},{temp:75,percent:85},{temp:85,percent:100}] },
      system: { enabled: false, sensor: id('b'), points: [{temp:30,percent:35},{temp:40,percent:45},{temp:50,percent:70},{temp:60,percent:100}] }
    }};
    let saves = 0, test = null;
    const state = () => ({ ok: true, config, test, sensors: [
      { id:id('a'), label:'CPU-Paket',chip:'coretemp',channel:'temp1_input',celsius:69 },
      { id:id('b'), label:'NVMe · Composite',chip:'nvme',channel:'temp1_input',celsius:48 }
    ], fans: { cpu:{label:'CPU-Lüfter',available:true,rpm:4100,mode:test?.fan==='cpu'?1:2,percent:test?.fan==='cpu'?test.percent:null}, system:{label:'Gehäuselüfter',available:true,rpm:3200,mode:2,percent:null} },
    service: { running:true,error:null,updated:Date.now()/1000,targets:test?{[test.fan]:{reason:'test',percent:test.percent}}:{} } });
    let html = fs.readFileSync(path.join(__dirname, 'BeelinkFans.page'), 'utf8').split('---\n')[1];
    html = html.replace(/<\?=[\s\S]*?\?>/g, 'test-csrf');
    await page.route('http://beelink.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/api.php')) {
        if (route.request().method() === 'POST') {
          const data = new URLSearchParams(route.request().postData());
          assert.equal(data.get('csrf_token'), 'test-csrf');
          if (data.get('action') === 'save') { config = JSON.parse(data.get('config')); saves++; }
          if (data.get('action') === 'auto') Object.values(config.fans).forEach(fan => fan.enabled = false);
          if (data.get('action') === 'test_start') test = {...JSON.parse(data.get('test')),remaining_s:60};
          if (data.get('action') === 'test_stop') test = null;
        }
        return route.fulfill({ contentType:'application/json', body:JSON.stringify(state()) });
      }
      if (url.pathname.endsWith('/app.js') || url.pathname.endsWith('/style.css'))
        return route.fulfill({ path:path.join(__dirname, path.basename(url.pathname)) });
      return route.fulfill({ contentType:'text/html', body:`<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:24px;background:#fafafa;color:#2b3035;font-family:Arial,sans-serif">${html}</body></html>` });
    });
    await page.goto('http://beelink.test/BeelinkFans');
    await page.waitForSelector('#bfc-rows-cpu tr');
    assert.equal(await page.locator('.bfc-card').count(), 2);
    assert.equal(await page.locator('#bfc-rows-cpu tr').count(), 5);
    await page.locator('#bfc-add-cpu').click();
    assert.equal(await page.locator('#bfc-rows-cpu tr').count(), 6);
    await page.locator('#bfc-rows-cpu tr').nth(1).locator('.bfc-remove').click();
    assert.equal(await page.locator('#bfc-rows-cpu tr').count(), 5);
    const secondTemp = page.locator('#bfc-rows-cpu tr').nth(1).locator('input').first();
    await secondTemp.fill('20');
    assert.equal(await page.locator('#bfc-save').isDisabled(), true);
    await secondTemp.fill('50');
    await page.locator('#bfc-sensor-cpu').selectOption(id('b'));
    await page.locator('#bfc-enabled-cpu').check();
    await page.locator('#bfc-save').click();
    await page.waitForFunction(() => document.querySelector('#bfc-message').textContent.includes('Gespeichert'));
    assert.equal(saves, 1);
    assert.equal(config.fans.cpu.sensor, id('b'));
    assert.equal(config.fans.cpu.enabled, true);
    await page.locator('#bfc-auto').click();
    await page.waitForFunction(() => !document.querySelector('#bfc-enabled-cpu').checked);
    assert.equal(config.fans.cpu.enabled, false);
    const original = JSON.stringify(config);
    await page.locator('#bfc-test-percent').fill('80');
    await page.locator('#bfc-test-start').click();
    await page.waitForFunction(() => document.querySelector('#bfc-test-status').textContent.includes('Test aktiv'));
    assert.equal(await page.locator('#bfc-mode-cpu').innerText(), 'Testmodus · feste Leistung');
    assert.equal(test.percent,80);
    assert.equal(test.duration,60);
    assert.equal(JSON.stringify(config),original);
    assert.equal(saves,1);
    await page.locator('#bfc-test-stop').click();
    await page.waitForFunction(() => document.querySelector('#bfc-test-status').textContent === 'Kein Test aktiv.');
    assert.equal(await page.locator('#bfc-mode-cpu').innerText(),'Hardware-Automatik');
    const download = page.waitForEvent('download');
    await page.locator('#bfc-diagnostics').click();
    assert.match((await download).suggestedFilename(), /^beelink-diagnose-.*\.json$/);
    await page.locator('#bfc').screenshot({ path:path.join(artifacts, 'ui-desktop.png') });
    await page.setViewportSize({width:390,height:1000});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('#bfc').screenshot({ path:path.join(artifacts, 'ui-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('UI smoke passed: curves, sensors, validation, save/auto, fixed-PWM start/stop, preserved config, diagnostics download, mobile layout.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
