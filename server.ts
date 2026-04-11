import express from 'express';
import { execFile } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';
import fsSync from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const execFileAsync = promisify(execFile);
const app = express();

app.use(express.json());
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  res.header('Access-Control-Allow-Methods', 'GET, POST, DELETE');
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

type RelayType = 'claude' | 'codex';
type ProfileRecord = {
  id: string;
  name: string;
  type: 'Claude' | 'Codex';
  relayType: RelayType;
  workspace: string;
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
  const launchers = process.platform === 'win32' ? ['py', 'python'] : ['python3', 'python'];
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

app.get('/api/runtime-info', async (_req, res) => {
  const payload: RuntimeInfo = {
    apiBase: `http://localhost:${Number(process.env.API_PORT || 3001)}`,
    backendDir: BACKEND_DIR,
    packaged: app.get('env') === 'production' || !!process.resourcesPath,
    appVersion: process.env.npm_package_version || '2.0.0',
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

const PORT = Number(process.env.API_PORT || 3001);
app.listen(PORT, () => {
  console.log(`CLADEX API server running on http://localhost:${PORT}`);
});
