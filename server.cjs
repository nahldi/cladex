const express = require('express');
const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs/promises');
const path = require('path');
const fsSync = require('fs');
const os = require('os');
const crypto = require('crypto');

const execFileAsync = promisify(execFile);
const app = express();
const API_HOST = process.env.API_HOST || '127.0.0.1';
const API_PORT = Number(process.env.API_PORT || 3001);
let serverInstance = null;
let remoteAccessTokenCache = null;
const MAX_CHANNEL_HISTORY_LIMIT = 500;

app.disable('x-powered-by');
app.use(express.json({ limit: '1mb' }));

function isLoopbackOrigin(origin) {
  if (!origin) {
    return true;
  }
  // "null" is the serialized form of an opaque origin (sandboxed iframe,
  // data: URI, certain file:// contexts). It cannot be distinguished from a
  // hostile cross-origin embedder, so refuse to treat it as local — the
  // /api access-token gate below still lets an authenticated caller through.
  if (origin === 'null') {
    return false;
  }
  try {
    const parsed = new URL(origin);
    if (parsed.protocol === 'file:') {
      return true;
    }
    return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function isLoopbackHost(host) {
  return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(String(host || '').trim());
}

function isSameOriginRequest(req, origin) {
  if (!origin) {
    return true;
  }
  try {
    const parsed = new URL(origin);
    return parsed.host === String(req.headers.host || '').trim();
  } catch {
    return false;
  }
}

function hasOwn(value, key) {
  return Object.prototype.hasOwnProperty.call(value || {}, key);
}

function parseIntegerField(body, key, defaultValue) {
  if (!hasOwn(body, key)) {
    return defaultValue;
  }
  const raw = body[key];
  if (typeof raw === 'number' && Number.isInteger(raw)) {
    return raw;
  }
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    if (/^\d+$/.test(trimmed)) {
      return Number(trimmed);
    }
  }
  return NaN;
}

function parseBooleanField(body, key, defaultValue) {
  if (!hasOwn(body, key)) {
    return { ok: true, value: defaultValue };
  }
  const raw = body[key];
  if (typeof raw === 'boolean') {
    return { ok: true, value: raw };
  }
  if (typeof raw === 'string') {
    const normalized = raw.trim().toLowerCase();
    if (normalized === 'true') {
      return { ok: true, value: true };
    }
    if (normalized === 'false') {
      return { ok: true, value: false };
    }
  }
  return { ok: false, error: `${key} must be a boolean` };
}

function parseChannelHistoryLimitField(body) {
  if (!hasOwn(body, 'channelHistoryLimit')) {
    return { ok: true, value: '' };
  }
  const raw = body.channelHistoryLimit;
  const text = raw === null || raw === undefined ? '' : String(raw).trim();
  if (!text) {
    return { ok: true, value: '' };
  }
  if (!/^\d+$/.test(text)) {
    return { ok: false, error: `channelHistoryLimit must be an integer between 0 and ${MAX_CHANNEL_HISTORY_LIMIT}` };
  }
  const value = Number(text);
  if (value > MAX_CHANNEL_HISTORY_LIMIT) {
    return { ok: false, error: `channelHistoryLimit must be an integer between 0 and ${MAX_CHANNEL_HISTORY_LIMIT}` };
  }
  return { ok: true, value: String(value) };
}

function workspacePathError(workspace) {
  const absoluteWorkspace = path.resolve(String(workspace || '').trim());
  try {
    const stat = fsSync.statSync(absoluteWorkspace);
    if (!stat.isDirectory()) {
      return `workspace does not exist or is not a directory: ${absoluteWorkspace}`;
    }
  } catch {
    return `workspace does not exist or is not a directory: ${absoluteWorkspace}`;
  }
  return '';
}

function csvValues(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function profileCreateAccessError({ relayType, channelId, allowDms, operatorIds, allowedUserIds }) {
  const hasChannel = csvValues(channelId).length > 0;
  const hasApprovedUser = [...csvValues(operatorIds), ...csvValues(allowedUserIds)].length > 0;
  if (allowDms && !hasApprovedUser) {
    return 'allowDms requires at least one approved user or operator id';
  }
  if (relayType === 'codex' && !hasChannel && !allowDms) {
    return 'channelId is required for Codex unless allowDms is true with an approved user';
  }
  if (relayType === 'codex' && !hasChannel && allowDms && !hasApprovedUser) {
    return 'channelId is required unless allowDms is true with an approved user';
  }
  if (relayType === 'claude' && !hasChannel && !hasApprovedUser) {
    return 'channelId or an approved user/operator id is required for Claude';
  }
  return '';
}

function isValidReviewId(value) {
  return /^review-\d{8}-\d{6}-[a-f0-9]{8}$/.test(String(value || '').trim());
}

function isValidFixRunId(value) {
  return /^fix-\d{8}-\d{6}-[a-f0-9]{8}$/.test(String(value || '').trim());
}

function rejectInvalidReviewId(res, extra = {}) {
  res.status(400).json({ ...extra, error: 'invalid review id' });
}

function rejectInvalidFixRunId(res, extra = {}) {
  res.status(400).json({ ...extra, error: 'invalid fix run id' });
}

app.use((req, res, next) => {
  const origin = String(req.headers.origin || '').trim();
  const isOpaqueOrigin = origin === 'null';
  const allowed = !isOpaqueOrigin && (isLoopbackOrigin(origin) || isSameOriginRequest(req, origin));
  const corsAllowed = allowed || isOpaqueOrigin;
  res.header('Vary', 'Origin');
  res.header('X-Content-Type-Options', 'nosniff');
  res.header('X-Frame-Options', 'DENY');
  res.header('Content-Security-Policy', "frame-ancestors 'none'");
  if (origin && corsAllowed) {
    res.header('Access-Control-Allow-Origin', origin);
    res.header('Access-Control-Allow-Headers', 'Content-Type, X-CLADEX-Access-Token');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS');
  }
  if (req.method === 'OPTIONS') {
    // Opaque/file-like browser origins are allowed to preflight, but the
    // follow-up /api request is still forced through X-CLADEX-Access-Token.
    if (origin && !corsAllowed) {
      res.status(403).end();
      return;
    }
    res.status(204).end();
    return;
  }
  // Non-opaque untrusted origins are rejected outright. Opaque origins fall
  // through to the /api access-token gate, which requires a valid token.
  if (origin && !allowed && !isOpaqueOrigin) {
    res.status(403).json({ error: 'Origin not allowed' });
    return;
  }
  next();
});

app.use('/api', (req, res, next) => {
  if (!requiresRemoteAccessToken(req) || hasValidRemoteAccessToken(req)) {
    next();
    return;
  }
  res.status(401).json({ error: 'CLADEX remote access token required', authRequired: true });
});

function resolveBackendDir() {
  const bundledBackend = path.join(process.resourcesPath || '', 'backend');
  if (bundledBackend && fsSync.existsSync(bundledBackend)) {
    return bundledBackend;
  }
  return path.join(__dirname, 'backend');
}

const BACKEND_DIR = resolveBackendDir();

function resolveFrontendDir() {
  const bundledDist = path.join(process.resourcesPath || '', 'dist');
  if (bundledDist && fsSync.existsSync(bundledDist)) {
    return bundledDist;
  }
  return path.join(__dirname, 'dist');
}

const FRONTEND_DIR = resolveFrontendDir();

function remoteTokenStatePath() {
  const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(localAppData, 'cladex', 'remote-access-token.json');
}

function getRemoteAccessToken() {
  if (remoteAccessTokenCache) {
    return remoteAccessTokenCache;
  }
  const envToken = String(process.env.CLADEX_REMOTE_ACCESS_TOKEN || '').trim();
  if (envToken) {
    remoteAccessTokenCache = envToken;
    return remoteAccessTokenCache;
  }
  const statePath = remoteTokenStatePath();
  try {
    if (fsSync.existsSync(statePath)) {
      const parsed = JSON.parse(fsSync.readFileSync(statePath, 'utf8'));
      const saved = String(parsed.token || '').trim();
      if (saved) {
        remoteAccessTokenCache = saved;
        return remoteAccessTokenCache;
      }
    }
  } catch {}
  const generated = crypto.randomBytes(18).toString('base64url');
  remoteAccessTokenCache = generated;
  try {
    fsSync.mkdirSync(path.dirname(statePath), { recursive: true });
    fsSync.writeFileSync(statePath, JSON.stringify({ token: generated }, null, 2) + '\n', 'utf8');
  } catch {}
  return remoteAccessTokenCache;
}

function requestHost(req) {
  const forwarded = String(req.headers['x-forwarded-host'] || '').trim();
  if (forwarded) {
    return forwarded.split(',')[0].trim().replace(/:\d+$/, '');
  }
  const raw = String(req.headers.host || '').trim();
  return raw.replace(/:\d+$/, '');
}

function requestBase(req) {
  const forwardedProto = String(req.headers['x-forwarded-proto'] || '').trim();
  const proto = (forwardedProto ? forwardedProto.split(',')[0].trim() : req.protocol || 'http') || 'http';
  const forwardedHost = String(req.headers['x-forwarded-host'] || '').trim();
  const host = (forwardedHost ? forwardedHost.split(',')[0].trim() : String(req.get('host') || '').trim())
    || `${API_HOST}:${API_PORT}`;
  return `${proto}://${host}`;
}

function socketRemoteAddress(req) {
  const raw = String(
    (req.socket && req.socket.remoteAddress) ||
    (req.connection && req.connection.remoteAddress) ||
    ''
  ).trim();
  if (!raw) {
    return '';
  }
  if (raw.toLowerCase().startsWith('::ffff:')) {
    return raw.slice(7);
  }
  if (raw.startsWith('[') && raw.endsWith(']')) {
    return raw.slice(1, -1);
  }
  return raw;
}

function hasForwardedHeaders(req) {
  // Any signal that the request transited a proxy. Once we see one, treat as
  // remote even if the socket peer is loopback, because a local reverse
  // proxy can be relaying an off-host caller.
  return Boolean(
    String(req.headers['x-forwarded-for'] || '').trim() ||
    String(req.headers['x-forwarded-host'] || '').trim() ||
    String(req.headers['x-forwarded-proto'] || '').trim() ||
    String(req.headers['forwarded'] || '').trim() ||
    String(req.headers['cf-connecting-ip'] || '').trim() ||
    String(req.headers['x-real-ip'] || '').trim() ||
    String(req.headers['true-client-ip'] || '').trim()
  );
}

function isLoopbackRequest(req) {
  const remoteAddr = socketRemoteAddress(req);
  if (!remoteAddr || !isLoopbackHost(remoteAddr)) {
    return false;
  }
  if (hasForwardedHeaders(req)) {
    return false;
  }
  // A loopback socket isn't enough on its own; a local tunnel/reverse proxy
  // can forward an external caller to 127.0.0.1 without populating any of
  // the X-Forwarded-* headers above. Require the Host header to also be
  // loopback so /api/runtime-info never returns the remote token to a
  // remote caller proxied through localhost.
  const rawHost = String(req.headers.host || '').trim();
  if (rawHost && !isLoopbackHost(rawHost.replace(/:\d+$/, ''))) {
    return false;
  }
  const origin = String(req.headers.origin || '').trim();
  if (origin && !isLoopbackOrigin(origin)) {
    return false;
  }
  return true;
}

function requiresRemoteAccessToken(req) {
  return !isLoopbackRequest(req);
}

function hasValidRemoteAccessToken(req) {
  const provided = String(req.headers['x-cladex-access-token'] || '').trim();
  return Boolean(provided) && provided === getRemoteAccessToken();
}

function resolvePythonLaunchers() {
  const launchers = [];
  const localAppData = process.env.LOCALAPPDATA || '';
  const directCandidates = [
    process.env.CLADEX_PYTHON || '',
    localAppData ? path.join(localAppData, 'discord-codex-relay', 'runtime', 'Scripts', 'python.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python313', 'python.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python312', 'python.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python311', 'python.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python310', 'python.exe') : '',
  ].filter(Boolean);

  for (const candidate of directCandidates) {
    if (fsSync.existsSync(candidate) && !launchers.includes(candidate)) {
      launchers.push(candidate);
    }
  }

  const pathLaunchers = process.platform === 'win32'
    ? ['python', 'python3', 'py']
    : ['python3', 'python'];
  for (const launcher of pathLaunchers) {
    if (!launchers.includes(launcher)) {
      launchers.push(launcher);
    }
  }
  return launchers;
}

function resolvePythonwLaunchers() {
  const launchers = [];
  const localAppData = process.env.LOCALAPPDATA || '';
  const directCandidates = [
    process.env.CLADEX_PYTHONW || '',
    localAppData ? path.join(localAppData, 'discord-codex-relay', 'runtime', 'Scripts', 'pythonw.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python313', 'pythonw.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python312', 'pythonw.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python311', 'pythonw.exe') : '',
    localAppData ? path.join(localAppData, 'Programs', 'Python', 'Python310', 'pythonw.exe') : '',
    'pyw.exe',
  ].filter(Boolean);
  for (const candidate of directCandidates) {
    if (!launchers.includes(candidate) && (candidate.toLowerCase().endsWith('.exe') ? fsSync.existsSync(candidate) : true)) {
      launchers.push(candidate);
    }
  }
  return launchers;
}

function managedRuntimePythonPath() {
  const localAppData = process.env.LOCALAPPDATA || '';
  if (!localAppData) {
    return '';
  }
  if (process.platform === 'win32') {
    return path.join(localAppData, 'discord-codex-relay', 'runtime', 'Scripts', 'python.exe');
  }
  return path.join(localAppData, 'discord-codex-relay', 'runtime', 'bin', 'python');
}

let backendBootstrapPromise = null;

function bootstrapBackendRuntime() {
  if (backendBootstrapPromise) {
    return backendBootstrapPromise;
  }
  backendBootstrapPromise = (async () => {
    const managed = managedRuntimePythonPath();
    if (managed && fsSync.existsSync(managed)) {
      return managed;
    }
    if (process.env.CLADEX_SKIP_BACKEND_BOOTSTRAP === '1') {
      return '';
    }
    const candidates = process.platform === 'win32'
      ? ['py', 'python', 'python3']
      : ['python3', 'python'];
    let lastError = '';
    for (const launcher of candidates) {
      try {
        await execFileAsync(
          launcher,
          ['-c', 'import sys; sys.path.insert(0, "."); from install_plugin import _ensure_runtime; _ensure_runtime()'],
          { cwd: BACKEND_DIR, windowsHide: true, timeout: 240000 }
        );
        return managed;
      } catch (err) {
        if (err && err.code === 'ENOENT') {
          continue;
        }
        lastError = err && (err.stderr || err.message) ? String(err.stderr || err.message) : String(err);
      }
    }
    if (lastError) {
      console.warn(`CLADEX backend runtime bootstrap failed: ${lastError}`);
    }
    return '';
  })();
  return backendBootstrapPromise;
}

async function runPython(args, cwd = BACKEND_DIR, extraEnv = {}) {
  await bootstrapBackendRuntime();
  const childEnv = { ...process.env, ...extraEnv };
  if (process.platform === 'win32') {
    const pythonwLaunchers = resolvePythonwLaunchers();
    for (const launcher of pythonwLaunchers) {
      try {
        const outputPath = path.join(os.tmpdir(), `cladex-api-${Date.now()}-${Math.random().toString(16).slice(2)}.json`);
        await execFileAsync(launcher, ['api_runner.py', '--output', outputPath, ...args], { cwd, windowsHide: true, env: childEnv });
        const raw = await fs.readFile(outputPath, 'utf-8');
        await fs.unlink(outputPath).catch(() => undefined);
        const payload = JSON.parse(raw || '{}');
        return {
          stdout: String(payload.stdout ?? ''),
          stderr: String(payload.stderr ?? ''),
          code: Number(payload.code ?? 1),
        };
      } catch (err) {
        if (err && err.code === 'ENOENT') {
          continue;
        }
      }
    }
  }

  const launchers = resolvePythonLaunchers();
  let lastError = '';

  for (const launcher of launchers) {
    try {
      const result = await execFileAsync(launcher, args, { cwd, windowsHide: true, env: childEnv });
      return { stdout: result.stdout ?? '', stderr: result.stderr ?? '', code: 0 };
    } catch (err) {
      if (err && err.code === 'ENOENT') {
        lastError = `${launcher} not found`;
        continue;
      }
      return {
        stdout: err?.stdout ?? '',
        stderr: err?.stderr ?? err?.message ?? String(err),
        code: typeof err?.code === 'number' ? err.code : 1,
      };
    }
  }

  return { stdout: '', stderr: lastError || 'No Python launcher found', code: 1 };
}

async function getProfiles() {
  const result = await runPython(['cladex.py', 'list', '--json']);
  if (result.code !== 0) {
    throw new Error(result.stderr || 'Failed to list profiles');
  }
  return JSON.parse(result.stdout || '[]');
}

async function findProfile(id, relayType) {
  const profiles = await getProfiles();
  return profiles.find((profile) => profile.id === id && (!relayType || profile.relayType === relayType));
}

async function runJson(args, cwd = BACKEND_DIR, extraEnv = {}) {
  const result = await runPython(args, cwd, extraEnv);
  if (result.code !== 0) {
    throw new Error(result.stderr || result.stdout || 'Backend command failed');
  }
  return JSON.parse(result.stdout || '{}');
}

function backendErrorMessage(err, fallback) {
  return String(err?.message || fallback || 'Backend command failed').trim() || fallback;
}

function backendErrorStatus(err) {
  const message = backendErrorMessage(err, '').toLowerCase();
  if (message.includes('invalid review id') || message.includes('invalid backup id') || message.includes('invalid fix run id')) {
    return 400;
  }
  if (message.includes('no review job found') || message.includes('no cladex backup found') || message.includes('no fix run found')) {
    return 404;
  }
  if (message.includes('workspace does not exist or is not a directory')) {
    return 400;
  }
  if (message.includes('overlaps protected cladex/runtime root')) {
    return 400;
  }
  if (message.includes('review job must be completed') || message.includes('fix agents must be between') || message.includes('self-fix requires explicit')) {
    return 400;
  }
  return 500;
}

function sendBackendError(res, err, fallback, extra = {}) {
  res.status(backendErrorStatus(err)).json({ ...extra, error: backendErrorMessage(err, fallback) });
}

function listDirectoryRoots() {
  if (process.platform === 'win32') {
    const roots = [];
    for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') {
      const drive = `${letter}:\\`;
      if (fsSync.existsSync(drive)) {
        roots.push({ name: drive, path: drive });
      }
    }
    return roots;
  }
  return [{ name: '/', path: '/' }];
}

function parseRemoteFilesystemRoots() {
  return String(process.env.CLADEX_REMOTE_FS_ROOTS || process.env.CLADEX_REMOTE_FS_ROOT || '')
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function comparablePath(value) {
  const resolved = path.resolve(String(value || ''));
  const normalized = process.platform === 'win32' ? resolved.toLowerCase() : resolved;
  return normalized.endsWith(path.sep) && normalized !== path.parse(normalized).root
    ? normalized.slice(0, -1)
    : normalized;
}

function isSubpathOrSame(candidate, root) {
  const target = comparablePath(candidate);
  const base = comparablePath(root);
  if (target === base) {
    return true;
  }
  const relative = path.relative(base, target);
  return Boolean(relative) && !relative.startsWith('..') && !path.isAbsolute(relative);
}

async function canonicalDirectoryPath(candidate) {
  const resolved = path.resolve(String(candidate || ''));
  const stats = await fs.stat(resolved);
  if (!stats.isDirectory()) {
    return '';
  }
  return fs.realpath(resolved).catch(() => resolved);
}

async function remoteFilesystemRoots() {
  const candidates = [...parseRemoteFilesystemRoots()];
  try {
    const profiles = await getProfiles();
    for (const profile of profiles) {
      for (const key of ['workspace', 'activeWorktree', 'codexHome', 'claudeConfigDir']) {
        const value = String(profile?.[key] || '').trim();
        if (value) {
          candidates.push(value);
        }
      }
    }
  } catch {}

  const roots = [];
  const seen = new Set();
  for (const candidate of candidates) {
    try {
      const realPath = await canonicalDirectoryPath(candidate);
      const comparable = comparablePath(realPath);
      if (realPath && !seen.has(comparable)) {
        seen.add(comparable);
        roots.push(realPath);
      }
    } catch {}
  }
  return roots.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

function rootLabel(root) {
  const parsed = path.parse(root);
  if (root === parsed.root) {
    return root;
  }
  return path.basename(root) || root;
}

async function directoryRootsForRequest(req) {
  if (isLoopbackRequest(req) || process.env.CLADEX_REMOTE_FS_UNRESTRICTED === '1') {
    return listDirectoryRoots();
  }
  const roots = await remoteFilesystemRoots();
  return roots.map((root) => ({ name: rootLabel(root), path: root }));
}

async function allowedDirectoryForRequest(req, requestedPath) {
  if (isLoopbackRequest(req) || process.env.CLADEX_REMOTE_FS_UNRESTRICTED === '1') {
    return canonicalDirectoryPath(requestedPath);
  }
  const roots = await remoteFilesystemRoots();
  const resolved = path.resolve(String(requestedPath || ''));
  if (!roots.some((root) => isSubpathOrSame(resolved, root))) {
    return '';
  }
  const target = await canonicalDirectoryPath(resolved);
  return roots.some((root) => isSubpathOrSame(target, root)) ? target : '';
}

app.get('/api/runtime-info', async (req, res) => {
  const payload = {
    apiBase: `${requestBase(req)}/api`,
    backendDir: BACKEND_DIR,
    frontendDir: FRONTEND_DIR,
    packaged: process.env.NODE_ENV === 'production' || !!process.resourcesPath,
    appVersion: process.env.npm_package_version || '2.5.1',
    remoteAccessProtected: true,
  };
  if (isLoopbackRequest(req)) {
    payload.remoteAccessToken = getRemoteAccessToken();
  }
  res.json(payload);
});

app.get('/api/fs/list', async (req, res) => {
  const raw = String(req.query.path || '').trim();
  if (!raw) {
    res.json({ currentPath: '', parentPath: '', directories: await directoryRootsForRequest(req) });
    return;
  }
  try {
    const target = await allowedDirectoryForRequest(req, raw);
    if (!target) {
      res.status(403).json({ error: 'Path is outside the configured remote filesystem roots' });
      return;
    }
    const stats = await fs.stat(target);
    if (!stats.isDirectory()) {
      res.status(400).json({ error: 'Path is not a directory' });
      return;
    }
    const entries = await fs.readdir(target, { withFileTypes: true });
    const directories = entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => ({ name: entry.name, path: path.join(target, entry.name) }))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
    const parentPath = path.dirname(target);
    let allowedParent = '';
    if (parentPath && parentPath !== target) {
      allowedParent = await allowedDirectoryForRequest(req, parentPath);
    }
    res.json({
      currentPath: target,
      parentPath: allowedParent || '',
      directories,
    });
  } catch (error) {
    res.status(500).json({ error: error?.message || 'Failed to list directories' });
  }
});

app.get('/api/profiles', async (_req, res) => {
  try {
    res.json(await getProfiles());
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load profiles' });
  }
});

app.get('/api/profiles/:id', async (req, res) => {
  const relayType = String(req.query.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ error: 'type must be claude or codex' });
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'show', req.params.id, '--type', relayType, '--json']));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load profile' });
  }
});

app.get('/api/status', async (_req, res) => {
  try {
    const result = await runPython(['cladex.py', 'status', '--json']);
    if (result.code !== 0) {
      throw new Error(result.stderr || 'Failed to load status');
    }
    res.json(JSON.parse(result.stdout || '{"running":[],"profiles":[]}'));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load status' });
  }
});

app.post('/api/profiles/:id/start', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  const result = await runPython(['cladex.py', 'start', id, '--type', relayType]);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to start relay' });
    return;
  }
  res.json({ success: true });
});

app.post('/api/profiles/:id/stop', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  const result = await runPython(['cladex.py', 'stop', id, '--type', relayType]);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to stop relay' });
    return;
  }
  res.json({ success: true });
});

app.post('/api/profiles/:id/restart', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  const result = await runPython(['cladex.py', 'restart', id, '--type', relayType]);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to restart relay' });
    return;
  }
  res.json({ success: true });
});

app.patch('/api/profiles/:id', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  const channelHistoryLimitInput = parseChannelHistoryLimitField(req.body);
  if (!channelHistoryLimitInput.ok) {
    res.status(400).json({ success: false, error: channelHistoryLimitInput.error });
    return;
  }
  if (Object.prototype.hasOwnProperty.call(req.body, 'workspace')) {
    const workspace = String(req.body?.workspace || '').trim();
    if (workspace) {
      const error = workspacePathError(workspace);
      if (error) {
        res.status(400).json({ success: false, error });
        return;
      }
    }
  }
  const args = ['cladex.py', 'update-profile', id, '--type', relayType, '--json'];
  const updateEnv = {};
  if (Object.prototype.hasOwnProperty.call(req.body, 'workspace')) args.push('--workspace', String(req.body?.workspace || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'discordToken')) {
    const tokenValue = String(req.body?.discordToken || '').trim();
    if (tokenValue) {
      args.push('--discord-bot-token-env', 'CLADEX_REGISTER_DISCORD_BOT_TOKEN');
      updateEnv.CLADEX_REGISTER_DISCORD_BOT_TOKEN = tokenValue;
    }
  }
  if (Object.prototype.hasOwnProperty.call(req.body, 'botName')) args.push('--bot-name', String(req.body?.botName || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'model')) args.push('--model', String(req.body?.model || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'codexHome')) args.push('--codex-home', String(req.body?.codexHome || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'claudeConfigDir')) args.push('--claude-config-dir', String(req.body?.claudeConfigDir || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'triggerMode')) args.push('--trigger-mode', String(req.body?.triggerMode || '').trim() || 'mention_or_dm');
  if (req.body?.allowDms === true) args.push('--allow-dms');
  if (req.body?.allowDms === false) args.push('--deny-dms');
  if (Object.prototype.hasOwnProperty.call(req.body, 'operatorIds')) args.push('--operator-ids', String(req.body?.operatorIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'allowedUserIds')) args.push('--allowed-user-ids', String(req.body?.allowedUserIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'allowedBotIds')) args.push('--allowed-bot-ids', String(req.body?.allowedBotIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'channelId')) args.push('--allowed-channel-id', String(req.body?.channelId || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'allowedChannelAuthorIds')) args.push('--allowed-channel-author-ids', String(req.body?.allowedChannelAuthorIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'channelNoMentionAuthorIds')) args.push('--channel-no-mention-author-ids', String(req.body?.channelNoMentionAuthorIds || '').trim());
  if (channelHistoryLimitInput.value) args.push('--channel-history-limit', channelHistoryLimitInput.value);
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupDmUserIds')) args.push('--startup-dm-user-ids', String(req.body?.startupDmUserIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupDmText')) args.push('--startup-dm-text', String(req.body?.startupDmText || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupChannelText')) args.push('--startup-channel-text', String(req.body?.startupChannelText || '').trim());
  try {
    res.json(await runJson(args, BACKEND_DIR, updateEnv));
  } catch (err) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to update profile' });
  }
});

app.delete('/api/profiles/:id', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.query.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  const result = await runPython(['cladex.py', 'remove', id, '--type', relayType]);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to remove profile' });
    return;
  }
  res.json({ success: true });
});

app.get('/api/profiles/:id/logs', async (req, res) => {
  const { id } = req.params;
  const relayType = String(req.query.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ error: 'type must be claude or codex' });
    return;
  }

  const result = await runPython(['cladex.py', 'logs', id, '--type', relayType, '--lines', '100', '--json']);
  if (result.code === 0) {
    res.json(JSON.parse(result.stdout || '{"logs":[]}'));
    return;
  }

  try {
    const profile = await findProfile(id, relayType);
    if (!profile?.logPath) {
      throw new Error('No log path found');
    }
    const content = await fs.readFile(profile.logPath, 'utf-8');
    res.json({ logs: content.split(/\r?\n/).filter(Boolean).slice(-100) });
  } catch {
    res.status(500).json({ logs: [], error: result.stderr || result.stdout || 'Failed to read logs' });
  }
});

app.get('/api/profiles/:id/chat/history', async (req, res) => {
  const relayType = String(req.query.type || '').trim().toLowerCase();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ error: 'type must be claude or codex' });
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'chat-history', req.params.id, '--type', relayType, '--json']));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load local chat history' });
  }
});

app.post('/api/profiles/:id/chat', async (req, res) => {
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  const message = String(req.body?.message || '').trim();
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be claude or codex' });
    return;
  }
  if (!message) {
    res.status(400).json({ success: false, error: 'message is required' });
    return;
  }
  const args = ['cladex.py', 'chat', req.params.id, '--type', relayType, '--message', message, '--json'];
  if (req.body?.channelId) args.push('--channel-id', String(req.body.channelId).trim());
  if (req.body?.senderName) args.push('--sender-name', String(req.body.senderName).trim());
  if (req.body?.senderId) args.push('--sender-id', String(req.body.senderId).trim());
  try {
    res.json(await runJson(args));
  } catch (err) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to send local operator message' });
  }
});

app.post('/api/actions/stop-all', async (req, res) => {
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  const args = ['cladex.py', 'stop-all', '--json'];
  if (relayType === 'claude' || relayType === 'codex') {
    args.push('--type', relayType);
  }
  try {
    res.json(await runJson(args));
  } catch (err) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to stop relays' });
  }
});

app.post('/api/profiles', async (req, res) => {
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  const name = String(req.body?.name || '').trim();
  const workspace = String(req.body?.workspace || '').trim();
  const discordToken = String(req.body?.discordToken || '').trim();
  const channelId = String(req.body?.channelId || '').trim();
  const model = String(req.body?.model || '').trim();
  const codexHome = String(req.body?.codexHome || '').trim();
  const claudeConfigDir = String(req.body?.claudeConfigDir || '').trim();
  const triggerMode = String(req.body?.triggerMode || 'mention_or_dm').trim();
  const allowDmsInput = parseBooleanField(req.body, 'allowDms', false);
  const operatorIds = String(req.body?.operatorIds || '').trim();
  const allowedUserIds = String(req.body?.allowedUserIds || '').trim();
  const allowedBotIds = String(req.body?.allowedBotIds || '').trim();
  const allowedChannelAuthorIds = String(req.body?.allowedChannelAuthorIds || '').trim();
  const channelNoMentionAuthorIds = String(req.body?.channelNoMentionAuthorIds || '').trim();
  const channelHistoryLimitInput = parseChannelHistoryLimitField(req.body);
  const channelHistoryLimit = channelHistoryLimitInput.value || '';
  const startupDmUserIds = String(req.body?.startupDmUserIds || '').trim();
  const startupDmText = String(req.body?.startupDmText || '').trim();
  const startupChannelText = String(req.body?.startupChannelText || '').trim();

  if (!allowDmsInput.ok) {
    res.status(400).json({ success: false, error: allowDmsInput.error });
    return;
  }
  if (!channelHistoryLimitInput.ok) {
    res.status(400).json({ success: false, error: channelHistoryLimitInput.error });
    return;
  }
  const allowDms = allowDmsInput.value;

  if (!name || !workspace || !discordToken) {
    res.status(400).json({ success: false, error: 'name, workspace, and discordToken are required' });
    return;
  }
  const workspaceError = workspacePathError(workspace);
  if (workspaceError) {
    res.status(400).json({ success: false, error: workspaceError });
    return;
  }
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be Claude or Codex' });
    return;
  }
  const accessError = profileCreateAccessError({ relayType, channelId, allowDms, operatorIds, allowedUserIds });
  if (accessError) {
    res.status(400).json({ success: false, error: accessError });
    return;
  }

  const absoluteWorkspace = path.resolve(workspace);
  // Tokens flow through CLADEX_REGISTER_DISCORD_BOT_TOKEN so the secret never
  // appears in subprocess command lines (visible in tasklist/ps output).
  const tokenEnv = { CLADEX_REGISTER_DISCORD_BOT_TOKEN: discordToken };
  let result;
  if (relayType === 'codex') {
    const operatorAndUserIds = [operatorIds, allowedUserIds]
      .flatMap((value) => csvValues(value));
    const dedupedUserIds = Array.from(new Set(operatorAndUserIds));
    result = await runPython([
      'relayctl.py',
      'register',
      '--workspace',
      absoluteWorkspace,
      '--bot-name',
      name,
      '--trigger-mode',
      triggerMode,
      ...(channelId ? ['--allowed-channel-id', channelId] : []),
      ...(allowDms ? ['--allow-dms'] : []),
      ...(model ? ['--model', model] : []),
      ...(codexHome ? ['--codex-home', codexHome] : []),
      ...(channelHistoryLimit ? ['--channel-history-limit', channelHistoryLimit] : []),
      ...(startupDmUserIds ? ['--startup-dm-user-ids', startupDmUserIds] : []),
      ...(startupDmText ? ['--startup-dm-text', startupDmText] : []),
      ...(startupChannelText ? ['--startup-channel-text', startupChannelText] : []),
      ...(allowedChannelAuthorIds ? csvValues(allowedChannelAuthorIds).flatMap((id) => ['--allowed-channel-author-id', id]) : []),
      ...(channelNoMentionAuthorIds ? csvValues(channelNoMentionAuthorIds).flatMap((id) => ['--channel-no-mention-author-id', id]) : []),
      ...dedupedUserIds.flatMap((id) => ['--allowed-user-id', id]),
      ...(allowedBotIds ? ['--allowed-bot-ids', allowedBotIds] : []),
    ], BACKEND_DIR, tokenEnv);
  } else {
    result = await runPython([
      'claude_relay.py',
      'register',
      '--workspace',
      absoluteWorkspace,
      '--bot-name',
      name,
      '--trigger-mode',
      triggerMode,
      ...(channelId ? ['--allowed-channel-id', channelId] : []),
      ...(allowDms ? ['--allow-dms'] : []),
      ...(model ? ['--model', model] : []),
      ...(claudeConfigDir ? ['--claude-config-dir', claudeConfigDir] : []),
      ...(channelHistoryLimit ? ['--channel-history-limit', channelHistoryLimit] : []),
      ...(operatorIds ? ['--operator-ids', operatorIds] : []),
      ...(allowedUserIds ? ['--allowed-user-ids', allowedUserIds] : []),
      ...(allowedBotIds ? ['--allowed-bot-ids', allowedBotIds] : []),
    ], BACKEND_DIR, tokenEnv);
  }

  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to create profile' });
    return;
  }
  res.json({ success: true });
});

app.get('/api/projects', async (_req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'project', 'list', '--json']));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load workgroups' });
  }
});

app.post('/api/projects', async (req, res) => {
  const name = String(req.body?.name || '').trim();
  const members = Array.isArray(req.body?.members) ? req.body.members : [];
  if (!name || !members.length) {
    res.status(400).json({ success: false, error: 'name and members are required' });
    return;
  }
  const args = ['cladex.py', 'project', 'save', name];
  for (const member of members) {
    const relayType = String(member?.relayType || '').trim().toLowerCase();
    const id = String(member?.id || '').trim();
    if (relayType && id) {
      args.push('--member', `${relayType}:${id}`);
    }
  }
  const result = await runPython(args);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to save workgroup' });
    return;
  }
  res.json({ success: true });
});

app.post('/api/projects/:name/start', async (req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'project', 'start', req.params.name, '--json']));
  } catch (err) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to start workgroup' });
  }
});

app.post('/api/projects/:name/stop', async (req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'project', 'stop', req.params.name, '--json']));
  } catch (err) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to stop workgroup' });
  }
});

app.delete('/api/projects/:name', async (req, res) => {
  const result = await runPython(['cladex.py', 'project', 'remove', req.params.name]);
  if (result.code !== 0) {
    res.status(500).json({ success: false, error: result.stderr || result.stdout || 'Failed to remove workgroup' });
    return;
  }
  res.json({ success: true });
});

app.get('/api/reviews', async (_req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'review', 'list', '--json']));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load review jobs' });
  }
});

app.get('/api/reviews/:id', async (req, res) => {
  if (!isValidReviewId(req.params.id)) {
    rejectInvalidReviewId(res);
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'review', 'show', req.params.id, '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to load review job');
  }
});

app.get('/api/reviews/:id/findings', async (req, res) => {
  if (!isValidReviewId(req.params.id)) {
    rejectInvalidReviewId(res);
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'review', 'findings', req.params.id]));
  } catch (err) {
    sendBackendError(res, err, 'Failed to load review findings');
  }
});

app.post('/api/reviews', async (req, res) => {
  const workspace = String(req.body?.workspace || '').trim();
  const provider = String(req.body?.provider || 'codex').trim().toLowerCase();
  const agents = parseIntegerField(req.body, 'agents', 4);
  const title = String(req.body?.title || '').trim();
  const accountHome = String(req.body?.accountHome || '').trim();
  const allowSelfReviewInput = parseBooleanField(req.body, 'allowSelfReview', false);
  const backupBeforeReviewInput = parseBooleanField(req.body, 'backupBeforeReview', true);
  if (!workspace) {
    res.status(400).json({ success: false, error: 'workspace is required' });
    return;
  }
  if (provider !== 'codex' && provider !== 'claude') {
    res.status(400).json({ success: false, error: 'provider must be codex or claude' });
    return;
  }
  if (!Number.isInteger(agents) || agents < 1 || agents > 50) {
    res.status(400).json({ success: false, error: 'agents must be between 1 and 50' });
    return;
  }
  if (!allowSelfReviewInput.ok) {
    res.status(400).json({ success: false, error: allowSelfReviewInput.error });
    return;
  }
  if (!backupBeforeReviewInput.ok) {
    res.status(400).json({ success: false, error: backupBeforeReviewInput.error });
    return;
  }
  const allowSelfReview = allowSelfReviewInput.value;
  const backupBeforeReview = backupBeforeReviewInput.value;
  const args = [
    'cladex.py',
    'review',
    'start',
    '--workspace',
    path.resolve(workspace),
    '--provider',
    provider,
    '--agents',
    String(agents),
    '--json',
  ];
  if (title) args.push('--title', title);
  if (accountHome) args.push('--account-home', accountHome);
  if (allowSelfReview) args.push('--allow-cladex-self-review');
  if (!backupBeforeReview) args.push('--no-backup');
  try {
    res.json(await runJson(args));
  } catch (err) {
    sendBackendError(res, err, 'Failed to start review job', { success: false });
  }
});

app.get('/api/backups', async (_req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'backup', 'list', '--json']));
  } catch (err) {
    res.status(500).json({ error: err?.message ?? 'Failed to load source backups' });
  }
});

app.post('/api/backups', async (req, res) => {
  const workspace = String(req.body?.workspace || '').trim();
  const reason = String(req.body?.reason || 'manual').trim();
  if (!workspace) {
    res.status(400).json({ success: false, error: 'workspace is required' });
    return;
  }
  const args = ['cladex.py', 'backup', 'create', '--workspace', path.resolve(workspace), '--reason', reason, '--json'];
  try {
    res.json(await runJson(args));
  } catch (err) {
    sendBackendError(res, err, 'Failed to create source backup', { success: false });
  }
});

app.get('/api/fix-runs', async (_req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'fix', 'list', '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to load fix runs');
  }
});

app.get('/api/fix-runs/:id', async (req, res) => {
  if (!isValidFixRunId(req.params.id)) {
    rejectInvalidFixRunId(res);
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'fix', 'show', req.params.id, '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to load fix run');
  }
});

app.post('/api/reviews/:id/fix-plan', async (req, res) => {
  if (!isValidReviewId(req.params.id)) {
    rejectInvalidReviewId(res, { success: false });
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'review', 'fix-plan', req.params.id, '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to generate fix plan', { success: false });
  }
});

app.post('/api/reviews/:id/fix', async (req, res) => {
  if (!isValidReviewId(req.params.id)) {
    rejectInvalidReviewId(res, { success: false });
    return;
  }
  const allowSelfFixInput = parseBooleanField(req.body, 'allowSelfFix', false);
  if (!allowSelfFixInput.ok) {
    res.status(400).json({ success: false, error: allowSelfFixInput.error });
    return;
  }
  const args = ['cladex.py', 'fix', 'start', '--review', req.params.id, '--json'];
  if (allowSelfFixInput.value) {
    args.push('--allow-cladex-self-fix');
  }
  try {
    res.json(await runJson(args));
  } catch (err) {
    sendBackendError(res, err, 'Failed to start fix run', { success: false });
  }
});

app.post('/api/reviews/:id/cancel', async (req, res) => {
  if (!isValidReviewId(req.params.id)) {
    rejectInvalidReviewId(res, { success: false });
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'review', 'cancel', req.params.id, '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to cancel review job', { success: false });
  }
});

app.post('/api/fix-runs/:id/cancel', async (req, res) => {
  if (!isValidFixRunId(req.params.id)) {
    rejectInvalidFixRunId(res, { success: false });
    return;
  }
  try {
    res.json(await runJson(['cladex.py', 'fix', 'cancel', req.params.id, '--json']));
  } catch (err) {
    sendBackendError(res, err, 'Failed to cancel fix run', { success: false });
  }
});

app.use('/api', (_req, res) => {
  res.status(404).json({ error: 'Not found' });
});

app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(500).json({ error: 'Internal server error' });
});

if (fsSync.existsSync(FRONTEND_DIR)) {
  app.use(express.static(FRONTEND_DIR, { index: 'index.html' }));
  app.get('/{*splat}', (req, res, next) => {
    if (req.path.startsWith('/api/')) {
      next();
      return;
    }
    res.sendFile(path.join(FRONTEND_DIR, 'index.html'));
  });
}

function startServer(options = {}) {
  const host = options.host || API_HOST;
  const port = Number(options.port === undefined ? API_PORT : options.port);
  const quiet = Boolean(options.quiet);
  const allowRemoteApi = process.env.CLADEX_ALLOW_REMOTE_API === '1';

  if (serverInstance) {
    return Promise.resolve(serverInstance);
  }

  if (!allowRemoteApi && !isLoopbackHost(host)) {
    return Promise.reject(new Error(`Refusing to bind CLADEX API to non-loopback host ${host}. Set CLADEX_ALLOW_REMOTE_API=1 to override.`));
  }

  return new Promise((resolve, reject) => {
    const server = app.listen(port, host, () => {
      serverInstance = server;
      if (!quiet) {
        console.log(`CLADEX API server running on http://${host}:${port}`);
      }
      resolve(server);
    });
    server.on('error', (error) => {
      reject(error);
    });
  });
}

function stopServer() {
  if (!serverInstance) {
    return Promise.resolve();
  }
  const activeServer = serverInstance;
  serverInstance = null;
  return new Promise((resolve, reject) => {
    activeServer.close((error) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

module.exports = {
  app,
  BACKEND_DIR,
  API_HOST,
  API_PORT,
  csvValues,
  profileCreateAccessError,
  startServer,
  stopServer,
};

if (require.main === module) {
  startServer().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
