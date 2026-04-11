import express from 'express';
import { exec, spawn } from 'child_process';
import { promisify } from 'util';
import path from 'path';

const execAsync = promisify(exec);
const app = express();
app.use(express.json());

// CORS for dev
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  res.header('Access-Control-Allow-Methods', 'GET, POST, DELETE');
  next();
});

const BACKEND_DIR = path.join(__dirname, 'backend');

// Helper to run Python commands
async function runPython(args: string[]): Promise<{ stdout: string; stderr: string }> {
  const cmd = `py ${args.map(a => `"${a}"`).join(' ')}`;
  try {
    return await execAsync(cmd, { cwd: BACKEND_DIR });
  } catch (err: any) {
    return { stdout: err.stdout || '', stderr: err.stderr || err.message };
  }
}

// List all profiles (both Claude and Codex)
app.get('/api/profiles', async (req, res) => {
  try {
    const { stdout } = await runPython(['cladex.py', 'list', '--json']);
    const profiles = JSON.parse(stdout || '[]');
    res.json(profiles);
  } catch (err) {
    // Fallback: return mock data structure
    res.json([]);
  }
});

// Get status of all relays
app.get('/api/status', async (req, res) => {
  try {
    const { stdout } = await runPython(['cladex.py', 'status', '--json']);
    res.json(JSON.parse(stdout || '{}'));
  } catch (err) {
    res.json({ running: [] });
  }
});

// Start a relay
app.post('/api/profiles/:id/start', async (req, res) => {
  const { id } = req.params;
  const { type } = req.body; // 'claude' or 'codex'

  try {
    if (type === 'claude') {
      await runPython(['claude_relay.py', 'run', '--profile', id]);
    } else {
      await runPython(['relayctl.py', 'run', '--profile', id]);
    }
    res.json({ success: true, message: `Started ${type} relay ${id}` });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Stop a relay
app.post('/api/profiles/:id/stop', async (req, res) => {
  const { id } = req.params;
  const { type } = req.body;

  try {
    if (type === 'claude') {
      await runPython(['claude_relay.py', 'stop', '--profile', id]);
    } else {
      await runPython(['relayctl.py', 'stop', '--profile', id]);
    }
    res.json({ success: true, message: `Stopped ${type} relay ${id}` });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Get logs for a profile
app.get('/api/profiles/:id/logs', async (req, res) => {
  const { id } = req.params;
  const { type } = req.query;

  try {
    const cmd = type === 'claude' ? 'claude_relay.py' : 'relayctl.py';
    const { stdout } = await runPython([cmd, 'logs', '--profile', id, '--tail', '100']);
    res.json({ logs: stdout.split('\n') });
  } catch (err) {
    res.json({ logs: [] });
  }
});

// Create a new profile
app.post('/api/profiles', async (req, res) => {
  const { name, type, workspace, discordToken, channelId } = req.body;

  try {
    const cmd = type === 'Claude' ? 'claude_relay.py' : 'relayctl.py';
    await runPython([
      cmd, 'register',
      '--name', name,
      '--workspace', workspace,
      '--discord-bot-token', discordToken,
      '--allowed-channel-id', channelId
    ]);
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Delete a profile
app.delete('/api/profiles/:id', async (req, res) => {
  const { id } = req.params;
  const { type } = req.query;

  try {
    const cmd = type === 'claude' ? 'claude_relay.py' : 'relayctl.py';
    await runPython([cmd, 'reset', '--profile', id]);
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

const PORT = process.env.API_PORT || 3001;
app.listen(PORT, () => {
  console.log(`CLADEX API server running on http://localhost:${PORT}`);
});
