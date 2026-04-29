// HTTP smoke for server.cjs. Starts the Express app on an ephemeral loopback
// port and exercises CORS, security headers, the access-token gate, and the
// validation paths of representative privileged endpoints. Endpoints are
// chosen so request handlers reject before any Python backend command is
// invoked — the smoke must not require a live runtime.

const assert = require('node:assert/strict');
const http = require('node:http');

process.env.CLADEX_REMOTE_ACCESS_TOKEN = process.env.CLADEX_REMOTE_ACCESS_TOKEN
  || `smoke-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
process.env.CLADEX_SKIP_BACKEND_BOOTSTRAP = '1';
process.env.API_PORT = process.env.API_PORT || '34567';

const {
  csvValues,
  profileCreateAccessError,
  startServer,
  stopServer,
} = require('../server.cjs');

assert.deepEqual(csvValues('111, 222,,333 '), ['111', '222', '333']);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '',
    allowDms: false,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  'channelId is required for Codex unless allowDms is true with an approved user',
);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '',
    allowDms: true,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  '',
);

assert.equal(
  profileCreateAccessError({
    relayType: 'claude',
    channelId: '',
    allowDms: false,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  '',
);

assert.equal(
  profileCreateAccessError({
    relayType: 'claude',
    channelId: '',
    allowDms: true,
    operatorIds: '',
    allowedUserIds: '',
  }),
  'allowDms requires at least one approved user or operator id',
);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '123',
    allowDms: false,
    operatorIds: '',
    allowedUserIds: '',
  }),
  '',
);

function request(port, options) {
  const { method = 'GET', path = '/', headers = {}, body } = options;
  const finalHeaders = { ...headers };
  if (body !== undefined && finalHeaders['Content-Length'] === undefined) {
    finalHeaders['Content-Length'] = Buffer.byteLength(body);
  }
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port, method, path, headers: finalHeaders },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let parsed = null;
          try { parsed = text ? JSON.parse(text) : null; } catch {}
          resolve({ status: res.statusCode, headers: res.headers, body: text, json: parsed });
        });
      },
    );
    req.on('error', reject);
    if (body !== undefined) {
      req.write(body);
    }
    req.end();
  });
}

async function main() {
  const server = await startServer({ host: '127.0.0.1', port: 0, quiet: true });
  const port = server.address().port;
  assert.notEqual(port, Number(process.env.API_PORT));
  try {
    // Loopback request with no Origin header behaves like a desktop renderer
    // bootstrapping: it gets the runtime info and the remote access token.
    const local = await request(port, { path: '/api/runtime-info' });
    assert.equal(local.status, 200);
    assert.equal(local.headers['x-content-type-options'], 'nosniff');
    assert.equal(local.headers['x-frame-options'], 'DENY');
    assert.match(local.headers['content-security-policy'] || '', /frame-ancestors 'none'/);
    assert.equal(local.json.remoteAccessProtected, true);
    assert.equal(typeof local.json.remoteAccessToken, 'string');
    assert.ok(local.json.remoteAccessToken.length > 0);
    const token = local.json.remoteAccessToken;

    // Origin: null is no longer treated as trusted loopback. Without a token
    // the request is rejected by the /api access-token gate, even though CORS
    // preflight is allowed so authenticated file/Electron renderers can work.
    const opaqueNoToken = await request(port, {
      path: '/api/runtime-info',
      headers: { Origin: 'null' },
    });
    assert.equal(opaqueNoToken.status, 401);
    assert.equal(opaqueNoToken.json.authRequired, true);
    assert.equal(opaqueNoToken.headers['access-control-allow-origin'], 'null');

    // Opaque origin with a valid token authenticates, but the runtime-info
    // payload still withholds the remote access token because the request is
    // not from a trusted loopback origin.
    const opaqueWithToken = await request(port, {
      path: '/api/runtime-info',
      headers: { Origin: 'null', 'X-CLADEX-Access-Token': token },
    });
    assert.equal(opaqueWithToken.status, 200);
    assert.equal(opaqueWithToken.json.remoteAccessProtected, true);
    assert.equal(opaqueWithToken.json.remoteAccessToken, undefined);
    assert.equal(opaqueWithToken.headers['access-control-allow-origin'], 'null');

    // Untrusted non-opaque Origin is rejected by the CORS middleware.
    const evil = await request(port, {
      path: '/api/runtime-info',
      headers: { Origin: 'https://attacker.example' },
    });
    assert.equal(evil.status, 403);
    assert.equal(evil.json.error, 'Origin not allowed');

    // Preflight from an opaque origin is allowed. The actual /api call still
    // needs X-CLADEX-Access-Token, so tokenless browser probes stop at 401.
    const preflight = await request(port, {
      method: 'OPTIONS',
      path: '/api/profiles',
      headers: {
        Origin: 'null',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'X-CLADEX-Access-Token, Content-Type',
      },
    });
    assert.equal(preflight.status, 204);
    assert.equal(preflight.headers['access-control-allow-origin'], 'null');

    // Preflight from a trusted loopback origin succeeds and reflects the
    // origin and access-token header.
    const okPreflight = await request(port, {
      method: 'OPTIONS',
      path: '/api/profiles',
      headers: {
        Origin: 'http://127.0.0.1:3000',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'X-CLADEX-Access-Token, Content-Type',
      },
    });
    assert.equal(okPreflight.status, 204);
    assert.equal(okPreflight.headers['access-control-allow-origin'], 'http://127.0.0.1:3000');
    assert.match(okPreflight.headers['access-control-allow-headers'] || '', /X-CLADEX-Access-Token/);

    // Privileged route validation: relay-type checks happen before any
    // backend command, so we can confirm wiring without spawning Python.
    const badStart = await request(port, {
      method: 'POST',
      path: '/api/profiles/test-id/start',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'invalid' }),
    });
    assert.equal(badStart.status, 400);
    assert.match(badStart.json.error, /type must be claude or codex/);

    const badDelete = await request(port, {
      method: 'DELETE',
      path: '/api/profiles/test-id?type=invalid',
    });
    assert.equal(badDelete.status, 400);

    // Review/fix-run id validation rejects malformed identifiers before the
    // backend is consulted.
    const badReviewId = await request(port, { path: '/api/reviews/not-a-review-id' });
    assert.equal(badReviewId.status, 400);
    assert.match(badReviewId.json.error, /invalid review id/);

    const badFindings = await request(port, { path: '/api/reviews/not-a-review-id/findings' });
    assert.equal(badFindings.status, 400);

    const badFixRunId = await request(port, { path: '/api/fix-runs/not-a-fix-id' });
    assert.equal(badFixRunId.status, 400);
    assert.match(badFixRunId.json.error, /invalid fix run id/);

    const badFixPlan = await request(port, {
      method: 'POST',
      path: '/api/reviews/not-a-review-id/fix-plan',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    assert.equal(badFixPlan.status, 400);

    const badFixStart = await request(port, {
      method: 'POST',
      path: '/api/reviews/not-a-review-id/fix',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    assert.equal(badFixStart.status, 400);

    // Profile creation: missing required fields rejected before backend run.
    const badProfile = await request(port, {
      method: 'POST',
      path: '/api/profiles',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    assert.equal(badProfile.status, 400);

    // Review start: missing workspace rejected before backend run.
    const badReview = await request(port, {
      method: 'POST',
      path: '/api/reviews',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    assert.equal(badReview.status, 400);
    assert.match(badReview.json.error, /workspace is required/);

    // Review Scout: missing workspace rejected before backend run.
    const badReviewAnalyze = await request(port, {
      method: 'POST',
      path: '/api/reviews/analyze',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    assert.equal(badReviewAnalyze.status, 400);
    assert.match(badReviewAnalyze.json.error, /workspace is required/);

    // Token gate denies opaque-origin access to the privileged listing route.
    const opaqueProfilesNoToken = await request(port, {
      path: '/api/profiles',
      headers: { Origin: 'null' },
    });
    assert.equal(opaqueProfilesNoToken.status, 401);
    assert.equal(opaqueProfilesNoToken.json.authRequired, true);

    // Unknown /api routes return JSON 404 rather than the SPA fallback.
    const notFound = await request(port, { path: '/api/does-not-exist' });
    assert.equal(notFound.status, 404);
    assert.equal(notFound.json.error, 'Not found');

    console.log('server smoke passed');
  } finally {
    await stopServer();
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
