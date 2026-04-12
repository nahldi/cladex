import express from 'express';
import { execFile } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';
import fsSync from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const execFileAsync = promisify(execFile);
const app = express();
const API_HOST = process.env.API_HOST || '127.0.0.1';
const API_PORT = Number(process.env.API_PORT || 3001);
let serverInstance: import('http').Server | null = null;

app.use(express.json());
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  res.header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE');
  next();
});

function resolveBackendDir() {
  const bundledBackend = path.join(process.resourcesPath || '', 'backend');
  if (bundledBackend && fsSync.existsSync(bundledBackend)) {
    return bundledBackend;
  }
  return path.join(__dirname, 'backend');
}

const BACKEND_DIR = resolveBackendDir();

function resolvePythonLaunchers(): string[] {
  const launchers: string[] = [];
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

function resolvePythonwLaunchers(): string[] {
  const launchers: string[] = [];
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

type RelayType = 'claude' | 'codex';
type ProfileRecord = {
  id: string;
  name: string;
  displayName?: string;
  technicalName?: string;
  type: 'Claude' | 'Codex';
  relayType: RelayType;
  workspace: string;
  workspaceLabel?: string;
  status: 'Running' | 'Stopped';
  running: boolean;
  ready: boolean;
  provider: string;
  model: string;
  triggerMode: string;
  effort?: string;
  botName?: string;
  allowDms?: boolean;
  stateNamespace?: string;
  discordChannel: string;
  channelLabel?: string;
  activeChannel?: string;
  activeWorktree?: string;
  sessionId?: string;
  statusText?: string;
  state: 'idle' | 'working';
  logPath: string;
};

type RuntimeInfo = {
  apiBase: string;
  backendDir: string;
  packaged: boolean;
  appVersion: string;
};

async function runPython(args: string[], cwd = BACKEND_DIR): Promise<{ stdout: string; stderr: string; code: number }> {
  if (process.platform === 'win32') {
    const pythonwLaunchers = resolvePythonwLaunchers();
    for (const launcher of pythonwLaunchers) {
      try {
        const outputPath = path.join(os.tmpdir(), `cladex-api-${Date.now()}-${Math.random().toString(16).slice(2)}.json`);
        await execFileAsync(launcher, ['api_runner.py', '--output', outputPath, ...args], { cwd, windowsHide: true });
        const raw = await fs.readFile(outputPath, 'utf-8');
        await fs.unlink(outputPath).catch(() => undefined);
        const payload = JSON.parse(raw || '{}');
        return {
          stdout: String(payload.stdout ?? ''),
          stderr: String(payload.stderr ?? ''),
          code: Number(payload.code ?? 1),
        };
      } catch (err: any) {
        if (err?.code === 'ENOENT') {
          continue;
        }
      }
    }
  }

  const launchers = resolvePythonLaunchers();
  let lastError = '';

  for (const launcher of launchers) {
    try {
      const result = await execFileAsync(launcher, args, { cwd, windowsHide: true });
      return { stdout: result.stdout ?? '', stderr: result.stderr ?? '', code: 0 };
    } catch (err: any) {
      if (err?.code === 'ENOENT') {
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

async function getProfiles(): Promise<ProfileRecord[]> {
  const result = await runPython(['cladex.py', 'list', '--json']);
  if (result.code !== 0) {
    throw new Error(result.stderr || 'Failed to list profiles');
  }
  return JSON.parse(result.stdout || '[]');
}

async function findProfile(id: string, relayType?: string): Promise<ProfileRecord | undefined> {
  const profiles = await getProfiles();
  return profiles.find((profile) => profile.id === id && (!relayType || profile.relayType === relayType));
}

async function runJson(args: string[], cwd = BACKEND_DIR): Promise<any> {
  const result = await runPython(args, cwd);
  if (result.code !== 0) {
    throw new Error(result.stderr || result.stdout || 'Backend command failed');
  }
  return JSON.parse(result.stdout || '{}');
}

app.get('/api/runtime-info', async (_req, res) => {
  const payload: RuntimeInfo = {
    apiBase: `http://${API_HOST}:${API_PORT}`,
    backendDir: BACKEND_DIR,
    packaged: app.get('env') === 'production' || !!process.resourcesPath,
    appVersion: process.env.npm_package_version || '2.0.10',
  };
  res.json(payload);
});

app.get('/api/profiles', async (_req, res) => {
  try {
    res.json(await getProfiles());
  } catch (err: any) {
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
  } catch (err: any) {
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
  } catch (err: any) {
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
  const args = ['cladex.py', 'update-profile', id, '--type', relayType, '--json'];
  if (Object.prototype.hasOwnProperty.call(req.body, 'workspace')) args.push('--workspace', String(req.body?.workspace || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'discordToken')) args.push('--discord-bot-token', String(req.body?.discordToken || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'botName')) args.push('--bot-name', String(req.body?.botName || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'model')) args.push('--model', String(req.body?.model || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'triggerMode')) args.push('--trigger-mode', String(req.body?.triggerMode || '').trim() || 'mention_or_dm');
  if (req.body?.allowDms === true) args.push('--allow-dms');
  if (req.body?.allowDms === false) args.push('--deny-dms');
  if (Object.prototype.hasOwnProperty.call(req.body, 'operatorIds')) args.push('--operator-ids', String(req.body?.operatorIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'allowedUserIds')) args.push('--allowed-user-ids', String(req.body?.allowedUserIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'channelId')) args.push('--allowed-channel-id', String(req.body?.channelId || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'allowedChannelAuthorIds')) args.push('--allowed-channel-author-ids', String(req.body?.allowedChannelAuthorIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'channelNoMentionAuthorIds')) args.push('--channel-no-mention-author-ids', String(req.body?.channelNoMentionAuthorIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'channelHistoryLimit')) args.push('--channel-history-limit', String(req.body?.channelHistoryLimit || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupDmUserIds')) args.push('--startup-dm-user-ids', String(req.body?.startupDmUserIds || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupDmText')) args.push('--startup-dm-text', String(req.body?.startupDmText || '').trim());
  if (Object.prototype.hasOwnProperty.call(req.body, 'startupChannelText')) args.push('--startup-channel-text', String(req.body?.startupChannelText || '').trim());
  try {
    res.json(await runJson(args));
  } catch (err: any) {
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
    const profile = await findProfile(id, relayType as RelayType);
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
  } catch (err: any) {
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
  } catch (err: any) {
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
  } catch (err: any) {
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
  const triggerMode = String(req.body?.triggerMode || 'mention_or_dm').trim();
  const allowDms = Boolean(req.body?.allowDms);
  const operatorIds = String(req.body?.operatorIds || '').trim();
  const allowedUserIds = String(req.body?.allowedUserIds || '').trim();
  const allowedChannelAuthorIds = String(req.body?.allowedChannelAuthorIds || '').trim();
  const channelNoMentionAuthorIds = String(req.body?.channelNoMentionAuthorIds || '').trim();
  const channelHistoryLimit = String(req.body?.channelHistoryLimit || '').trim();
  const startupDmUserIds = String(req.body?.startupDmUserIds || '').trim();
  const startupDmText = String(req.body?.startupDmText || '').trim();
  const startupChannelText = String(req.body?.startupChannelText || '').trim();

  if (!name || !workspace || !discordToken || !channelId) {
    res.status(400).json({ success: false, error: 'name, workspace, discordToken, and channelId are required' });
    return;
  }
  if (relayType !== 'claude' && relayType !== 'codex') {
    res.status(400).json({ success: false, error: 'type must be Claude or Codex' });
    return;
  }

  const absoluteWorkspace = path.resolve(workspace);

  let result;
  if (relayType === 'codex') {
    result = await runPython([
      'relayctl.py',
      'register',
      '--workspace',
      absoluteWorkspace,
      '--discord-bot-token',
      discordToken,
      '--bot-name',
      name,
      '--allowed-channel-id',
      channelId,
      '--trigger-mode',
      triggerMode,
      ...(allowDms ? ['--allow-dms'] : []),
      ...(model ? ['--model', model] : []),
      ...(channelHistoryLimit ? ['--channel-history-limit', channelHistoryLimit] : []),
      ...(startupDmText ? ['--startup-dm-text', startupDmText] : []),
      ...(startupChannelText ? ['--startup-channel-text', startupChannelText] : []),
      ...(startupDmUserIds ? startupDmUserIds.split(',').map((id) => id.trim()).filter(Boolean).flatMap((id) => ['--allowed-user-id', id]) : []),
      ...(allowedChannelAuthorIds ? allowedChannelAuthorIds.split(',').map((id) => id.trim()).filter(Boolean).flatMap((id) => ['--allowed-channel-author-id', id]) : []),
      ...(channelNoMentionAuthorIds ? channelNoMentionAuthorIds.split(',').map((id) => id.trim()).filter(Boolean).flatMap((id) => ['--channel-no-mention-author-id', id]) : []),
      ...[operatorIds, allowedUserIds].flatMap((value) => value.split(',').map((id) => id.trim()).filter(Boolean)).flatMap((id) => ['--allowed-user-id', id]),
    ]);
  } else {
    result = await runPython(
      [
        'claude_relay.py',
        'register',
        '--discord-bot-token',
        discordToken,
        '--bot-name',
        name,
        '--allowed-channel-id',
        channelId,
        '--trigger-mode',
        triggerMode,
        ...(allowDms ? ['--allow-dms'] : []),
        ...(model ? ['--model', model] : []),
        ...(channelHistoryLimit ? ['--channel-history-limit', channelHistoryLimit] : []),
        ...(operatorIds ? ['--operator-ids', operatorIds] : []),
        ...(allowedUserIds ? ['--allowed-user-ids', allowedUserIds] : []),
      ],
      absoluteWorkspace,
    );
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
  } catch (err: any) {
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
  } catch (err: any) {
    res.status(500).json({ success: false, error: err?.message ?? 'Failed to start workgroup' });
  }
});

app.post('/api/projects/:name/stop', async (req, res) => {
  try {
    res.json(await runJson(['cladex.py', 'project', 'stop', req.params.name, '--json']));
  } catch (err: any) {
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

export function startServer(options: { host?: string; port?: number; quiet?: boolean } = {}) {
  const host = options.host || API_HOST;
  const port = Number(options.port || API_PORT);
  const quiet = Boolean(options.quiet);

  if (serverInstance) {
    return Promise.resolve(serverInstance);
  }

  return new Promise<import('http').Server>((resolve, reject) => {
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

export function stopServer() {
  if (!serverInstance) {
    return Promise.resolve();
  }
  const activeServer = serverInstance;
  serverInstance = null;
  return new Promise<void>((resolve, reject) => {
    activeServer.close((error?: Error | undefined) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  startServer().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
