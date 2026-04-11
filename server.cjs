const express = require('express');
const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs/promises');
const path = require('path');

const execFileAsync = promisify(execFile);
const app = express();

app.use(express.json());
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  res.header('Access-Control-Allow-Methods', 'GET, POST, DELETE');
  next();
});

const BACKEND_DIR = path.join(__dirname, 'backend');

async function runPython(args, cwd = BACKEND_DIR) {
  const launchers = process.platform === 'win32' ? ['py', 'python'] : ['python3', 'python'];
  let lastError = '';

  for (const launcher of launchers) {
    try {
      const result = await execFileAsync(launcher, args, { cwd, windowsHide: true });
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

app.get('/api/profiles', async (_req, res) => {
  try {
    res.json(await getProfiles());
  } catch (err) {
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

app.post('/api/profiles', async (req, res) => {
  const relayType = String(req.body?.type || '').trim().toLowerCase();
  const name = String(req.body?.name || '').trim();
  const workspace = String(req.body?.workspace || '').trim();
  const discordToken = String(req.body?.discordToken || '').trim();
  const channelId = String(req.body?.channelId || '').trim();

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
