import { app, BrowserWindow, shell } from 'electron';
import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let mainWindow: BrowserWindow | null = null;
let apiServer: ChildProcess | null = null;

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

function startApiServer(): void {
  const serverPath = isDev
    ? path.join(__dirname, '..', 'server.ts')
    : path.join(process.resourcesPath, 'server.js');

  const command = isDev ? 'npx' : 'node';
  const args = isDev ? ['tsx', serverPath] : [serverPath];

  apiServer = spawn(command, args, {
    cwd: isDev ? path.join(__dirname, '..') : process.resourcesPath,
    stdio: 'pipe',
    shell: true,
  });

  apiServer.stdout?.on('data', (data) => {
    console.log(`API: ${data}`);
  });

  apiServer.stderr?.on('data', (data) => {
    console.error(`API Error: ${data}`);
  });

  apiServer.on('close', (code) => {
    console.log(`API server exited with code ${code}`);
  });
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    title: 'CLADEX',
    backgroundColor: '#050505',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 15, y: 15 },
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  });

  // Show window when ready
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  // Load the app
  if (isDev) {
    // Dev mode: load from Vite dev server
    const loadDevServer = async () => {
      // Try different ports in case 3000 is taken
      const ports = [3000, 3007, 3008, 3009, 3010];
      for (const port of ports) {
        try {
          await mainWindow?.loadURL(`http://localhost:${port}`);
          console.log(`Loaded from port ${port}`);
          return;
        } catch (e) {
          console.log(`Port ${port} not available, trying next...`);
        }
      }
      // Fallback: load built files
      mainWindow?.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
    };
    loadDevServer();
    mainWindow.webContents.openDevTools();
  } else {
    // Production: load built files
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  startApiServer();

  // Give API server a moment to start
  setTimeout(createWindow, 1000);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (apiServer) {
    apiServer.kill();
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (apiServer) {
    apiServer.kill();
  }
});
