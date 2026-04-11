const { app, BrowserWindow, shell } = require('electron');
const { fork } = require('child_process');
const http = require('http');
const path = require('path');

let mainWindow = null;
let apiServer = null;
const API_PORT = Number(process.env.API_PORT || 3001);
const API_HOST = process.env.API_HOST || '127.0.0.1';

const isDev = !app.isPackaged;

function startApiServer() {
  const serverPath = app.isPackaged
    ? path.join(process.resourcesPath, 'app.asar.unpacked', 'server.cjs')
    : path.join(__dirname, '..', 'server.cjs');
  const serverCwd = app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, '..');

  apiServer = fork(serverPath, [], {
    cwd: serverCwd,
    silent: true,
    windowsHide: true,
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

function waitForApi(timeoutMs = 10000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(
        {
          host: API_HOST,
          port: API_PORT,
          path: '/api/runtime-info',
          timeout: 1200,
        },
        (response) => {
          response.resume();
          if (response.statusCode && response.statusCode >= 200 && response.statusCode < 500) {
            resolve();
            return;
          }
          retry();
        },
      );
      request.on('error', retry);
      request.on('timeout', () => {
        request.destroy();
        retry();
      });
    };

    const retry = () => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error(`CLADEX API server did not become ready on ${API_HOST}:${API_PORT}`));
        return;
      }
      setTimeout(attempt, 250);
    };

    attempt();
  });
}

function createWindow() {
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'assets', 'icon.png')
    : path.join(__dirname, '..', 'assets', 'icon.png');

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    title: 'CLADEX',
    icon: iconPath,
    backgroundColor: '#050505',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 15, y: 15 },
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
    autoHideMenuBar: true,
  });

  // Show window when ready
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  // Load built files (production mode)
  const indexPath = path.join(__dirname, '..', 'dist', 'index.html');
  console.log('Loading:', indexPath);
  mainWindow.loadFile(indexPath);

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  startApiServer();
  try {
    await waitForApi();
  } catch (error) {
    console.error(error);
  }
  createWindow();

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
