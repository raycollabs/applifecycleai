const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.DURAI_PLAYWRIGHT_MODULE || 'playwright-core');

async function readEnv(filename) {
  return Object.fromEntries((await fs.readFile(filename, 'utf8')).trim().split(/\r?\n/)
    .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
}

async function signIn(page, url, username, password) {
  // Sample: Sign in as oil-reader. Check that the browser opens the report.
  await page.goto(`${url}/login/`, { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="username"], input#username').fill(username);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: /sign in|log in|login/i }).click();
  await page.waitForURL(location => !location.pathname.startsWith('/login/'));
}

async function readDesk(browser, profile, desk, dest) {
  // Sample: Open the gas dashboard. Check NGF27 and 300 USD. Refuse the oil dashboard.
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  try {
    const page = await context.newPage();
    await signIn(page, profile.url, `${desk}-reader`, profile.secrets.SUPERSET_TEST_PASSWORD);
    const dashboardPath = value => `/superset/dashboard/energy-futures-close-v1-${value}${profile.suffix}/`;
    const expected = desk === 'oil' ? { book: 'SYN-OIL', contract: 'CLF27', total: '320' } :
      { book: 'SYN-GAS', contract: 'NGF27', total: '300' };
    await page.goto(`${profile.url}${dashboardPath(desk)}`, { waitUntil: 'domcontentloaded' });
    await page.locator('td').filter({ hasText: new RegExp(`^${expected.contract}$`) }).first().waitFor({ timeout: 45000 });
    const table = await page.locator('tr').filter({ hasText: expected.contract }).first().innerText();
    assert.match(table, new RegExp(expected.book));
    assert.ok(table.split(/\s+/).some(value => Number(value.replaceAll(',', '')) === Number(expected.total)));
    assert.equal(await page.locator('td').filter({ hasText: desk === 'oil' ? /^NGF27$/ : /^CLF27$/ }).count(), 0);
    await page.screenshot({ path: path.join(dest, `${profile.name}-${desk}.png`), fullPage: true });
    const denied = await page.goto(`${profile.url}${dashboardPath(desk === 'oil' ? 'gas' : 'oil')}`);
    const deniedPath = /access|permission|404|403/i.test(new URL(page.url()).pathname);
    const deniedText = await page.getByText(/access is denied|do not have access|don't have access|dashboard not found|access denied|permission/i).count();
    assert.ok([403, 404].includes(denied.status()) || deniedPath || deniedText > 0);
    return { profile: profile.name, desk, ...expected, cross_desk: 'DENIED' };
  } finally {
    await context.close();
  }
}

async function main() {
  const [dest, pgEnv, chEnv] = process.argv.slice(2);
  const profiles = [
    { name: 'postgres', url: 'http://127.0.0.1:55482', suffix: '', secrets: await readEnv(pgEnv) },
    { name: 'clickhouse', url: 'http://127.0.0.1:58061', suffix: '-clickhouse', secrets: await readEnv(chEnv) },
  ];
  const browser = await chromium.launch({ executablePath: process.env.DURAI_CHROME_PATH, headless: true });
  try {
    const results = [];
    for (const profile of profiles) {
      for (const desk of ['oil', 'gas']) {
        const result = await readDesk(browser, profile, desk, dest);
        results.push(result);
        await fs.writeFile(path.join(dest, 'browser-receipt.json'), JSON.stringify(results, null, 2));
        process.stdout.write(`${profile.name} ${desk} PASS\n`);
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
