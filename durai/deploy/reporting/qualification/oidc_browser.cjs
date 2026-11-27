const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.DURAI_PLAYWRIGHT_MODULE || 'playwright-core');

async function login(page, base, user) {
  // Sample: Sign in to Keycloak as sso-gas. Check the redirect to Superset.
  await page.goto(`${base}/login/keycloak`, { waitUntil: 'domcontentloaded' });
  await page.locator('#username').fill(user.username);
  await page.locator('#password').fill(user.credentials[0].value);
  await page.locator('#kc-login').click();
  await page.waitForURL(location => location.origin === base && !/login|oauth-authorized/.test(location.pathname));
}

async function dashboard(page, base, slug, isAllowed, book) {
  // Sample: Open the oil dashboard without the oil group. Check access denial.
  const response = await page.goto(`${base}/superset/dashboard/${slug}/`, { waitUntil: 'domcontentloaded' });
  if (!isAllowed) {
    const refused = [401, 403, 404].includes(response.status()) || /access|permission|404/.test(new URL(page.url()).pathname);
    assert.ok(refused, `Expected access denial for ${slug}, got HTTP ${response.status()}`);
    return;
  }
  assert.equal(response.status(), 200);
  await page.locator('td').filter({ hasText: new RegExp(`^${book}$`) }).first().waitFor({ timeout: 40000 });
  assert.equal(await page.locator('td').filter({ hasText: book === 'SYN-OIL' ? /^SYN-GAS$/ : /^SYN-OIL$/ }).count(), 0);
}

async function sqlLab(context, base, sql, databaseId) {
  const csrf = await context.request.get(`${base}/api/v1/security/csrf_token/`);
  assert.equal(csrf.status(), 200);
  const token = (await csrf.json()).result;
  return context.request.post(`${base}/api/v1/sqllab/execute/`, {
    headers: { 'X-CSRFToken': token, Referer: `${base}/sqllab/` },
    data: { database_id: databaseId, sql, runAsync: false, queryLimit: 10, client_id: randomUUID().slice(0, 8) },
  });
}

async function analyst(context, base) {
  // Sample: Query SYN-OIL. Check the row. Ask for SYN-GAS and check refusal.
  const response = await context.request.get(`${base}/api/v1/database/`);
  assert.equal(response.status(), 200);
  const names = (await response.json()).result;
  const oil = names.find(source => source.database_name === 'Durai PostgreSQL oil');
  const gas = names.find(source => source.database_name === 'Durai PostgreSQL gas');
  assert.ok(oil && !gas, 'Only the oil connection is visible to the analyst');
  const oilSql = "SELECT book, net_pnl FROM oil.energy_futures_close WHERE business_date = '2026-11-03'";
  const read = await sqlLab(context, base, oilSql, oil.id);
  const data = await read.json();
  assert.equal(read.status(), 200, JSON.stringify(data));
  assert.deepEqual(data.data.map(row => [row.book, Number(row.net_pnl)]), [['SYN-OIL', 320]]);
  const refusals = [];
  for (const sql of ['SELECT book FROM gas.energy_futures_close', 'DELETE FROM oil.energy_futures_close WHERE false']) {
    const refused = await sqlLab(context, base, sql, oil.id);
    const detail = await refused.text();
    assert.ok([400, 403, 422, 500].includes(refused.status()) &&
      /permission denied|access denied|not allowed|read.only|only.*select|insufficient privilege|access to.*not|DML_NOT_ALLOWED_ERROR/i.test(detail),
    `Expected authorization refusal for ${sql}: HTTP ${refused.status()} ${detail.slice(0, 600)}`);
    refusals.push(refused.status());
  }
  return { oil: read.status(), gas_and_write: refusals };
}

async function main() {
  const [dest, realmFile, mode] = process.argv.slice(2);
  const realm = JSON.parse(await fs.readFile(realmFile, 'utf8'));
  const browser = await chromium.launch({ executablePath: process.env.DURAI_CHROME_PATH, headless: true,
    args: ['--no-proxy-server', '--host-resolver-rules=MAP oidc.durai.test 127.0.0.1'] });
  const output = [];
  try {
    for (const [profile, port, suffix] of [['postgres', 55482, ''], ['clickhouse', 58061, '-clickhouse']]) {
      const base = `http://127.0.0.1:${port}`;
      const slug = desk => `energy-futures-close-v1-${desk}${suffix}`;
      const roles = mode === 'revoked' ? ['gas'] : (profile === 'postgres' ? ['oil', 'gas', 'unscoped', 'analyst'] : ['oil', 'gas', 'unscoped']);
      for (const role of roles) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        try {
          const page = await context.newPage();
          await login(page, base, realm.users.find(item => item.username === `sso-${role}`));
          const oil = mode !== 'revoked' && ['oil', 'analyst'].includes(role);
          const gas = mode !== 'revoked' && role === 'gas';
          await dashboard(page, base, slug('oil'), oil, 'SYN-OIL');
          await dashboard(page, base, slug('gas'), gas, 'SYN-GAS');
          const record = { profile, role, oil, gas };
          if (role === 'analyst') record.sql_lab = await analyst(context, base);
          if (profile === 'postgres' && role === 'oil') {
            const denied = await sqlLab(context, base, 'SELECT 1', 1);
            assert.equal(denied.status(), 403);
            record.reader_sql_lab = 'DENIED';
          }
          output.push(record);
          await fs.writeFile(path.join(dest, mode === 'revoked' ? 'oidc-revocation.json' : 'oidc-receipt.json'),
            JSON.stringify(output, null, 2));
          process.stdout.write(`${profile} ${role} PASS\n`);
        } finally {
          await context.close();
        }
      }
    }
    if (mode !== 'revoked') {
      // Sample: Sign in on PostgreSQL. Open ClickHouse in the same browser. Check that Keycloak does not ask for a password again.
      const context = await browser.newContext();
      try {
        const page = await context.newPage();
        const pg = 'http://127.0.0.1:55482';
        const ch = 'http://127.0.0.1:58061';
        await login(page, pg, realm.users.find(item => item.username === 'sso-oil'));
        await page.goto(`${ch}/login/keycloak`);
        await page.waitForURL(location => location.origin === ch && !/login|oauth-authorized/.test(location.pathname));
        await dashboard(page, ch, 'energy-futures-close-v1-oil-clickhouse', true, 'SYN-OIL');
        await dashboard(page, pg, 'energy-futures-close-v1-oil', true, 'SYN-OIL');
        output.push({ shared_sign_in: 'PASS', profiles: ['postgres', 'clickhouse'] });
        await fs.writeFile(path.join(dest, 'oidc-receipt.json'), JSON.stringify(output, null, 2));
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
