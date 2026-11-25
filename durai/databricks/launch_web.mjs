import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { Readable } from 'node:stream';

const workspace = new URL(process.env.DATABRICKS_HOST);
if (workspace.protocol !== 'https:') throw new Error('Use an HTTPS workspace');
const target = process.env.DURAI_API_APP;
if (!/^[a-z0-9-]+$/.test(target ?? '')) throw new Error('The API app resource is missing');
let token;

async function authorization() {
  // Sample: Read an expired token. Request a new access token for the web app.
  if (!token || token.expires < Date.now() + 60000) {
    const identity = process.env.DATABRICKS_CLIENT_ID + ':' + process.env.DATABRICKS_CLIENT_SECRET;
    const response = await fetch(new URL('/oidc/v1/token', workspace), {
      method: 'POST',
      headers: { Authorization: 'Basic ' + Buffer.from(identity).toString('base64') },
      body: new URLSearchParams({ grant_type: 'client_credentials', scope: 'all-apis' }),
      redirect: 'error', signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error('Token request failed');
    const { access_token, expires_in } = await response.json();
    token = { value: access_token, expires: Date.now() + expires_in * 1000 };
  }
  return 'Bearer ' + token.value;
}

const lookup = await fetch(new URL('/api/2.0/apps/' + target, workspace), {
  headers: { Authorization: await authorization() }, redirect: 'error', signal: AbortSignal.timeout(8000),
});
if (!lookup.ok) throw new Error('Target app lookup failed');
const api = new URL((await lookup.json()).url);
if (api.protocol !== 'https:' || !api.hostname.endsWith('.databricksapps.com')) {
  throw new Error('Unexpected API app origin');
}

const proxy = createServer(async (request, response) => {
  // Sample: Receive GET /session. Forward it to the API with only the current cookie.
  try {
    const allowed = new URL(request.url ?? '/', 'http://127.0.0.1');
    const session = ['/session', '/session/sign-in', '/session/sign-out'].includes(allowed.pathname);
    if ((!session && !allowed.pathname.startsWith('/api/')) ||
        !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method)) {
      response.writeHead(404).end();
      return;
    }
    const headers = new Headers({ Authorization: await authorization() });
    for (const name of ['cookie', 'content-type', 'idempotency-key']) {
      if (request.headers[name]) headers.set(name, String(request.headers[name]));
    }
    const upstream = new URL(api);
    upstream.pathname = '/api' + allowed.pathname;
    upstream.search = allowed.search;
    const update = request.method !== 'GET';
    const result = await fetch(upstream, {
      method: request.method, headers, body: update ? request : undefined,
      duplex: update ? 'half' : undefined, redirect: 'error', signal: AbortSignal.timeout(8000),
    });
    response.statusCode = result.status;
    response.setHeader('Content-Type', result.headers.get('content-type') || 'application/json');
    response.setHeader('Cache-Control', 'no-store');
    if (result.headers.getSetCookie().length) response.setHeader('Set-Cookie', result.headers.getSetCookie());
    if (result.body) Readable.fromWeb(result.body).pipe(response);
    else response.end();
  } catch {
    response.writeHead(502, { 'Content-Type': 'application/json' }).end('{"error":{"code":"api_unavailable"}}');
  }
});
proxy.listen(0, '127.0.0.1');
await once(proxy, 'listening');
const next = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '-H', '0.0.0.0',
  '-p', process.env.DATABRICKS_APP_PORT], {
  stdio: 'inherit', env: { ...process.env, DURAI_SHELL_API_ORIGIN: 'http://127.0.0.1:' + proxy.address().port },
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => next.kill(signal));
const [status] = await once(next, 'exit');
proxy.close();
process.exitCode = status ?? 1;
