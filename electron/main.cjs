const { app, BrowserWindow, shell, ipcMain, dialog } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

let mainWindow = null;
let apiServer = null;
const API_PORT = Number(process.env.API_PORT || 3001);
const API_HOST = process.env.API_HOST || '127.0.0.1';
let activeApiPort = API_PORT;

const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
}

function logDesktop(message) {
  try {
    const logDir = app.getPath('userData');
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, 'desktop.log'), `[${new Date().toISOString()}] ${message}\n`, 'utf8');
  } catch {}
}

function getServerModulePath() {
  return app.isPackaged
    ? path.join(app.getAppPath(), 'server.cjs')
    : path.join(__dirname, '..', 'server.cjs');
}

async function startApiServer() {
  if (apiServer) {
    return apiServer;
  }
  const serverModulePath = getServerModulePath();
  logDesktop(`Starting API from ${serverModulePath}`);
  // eslint-disable-next-line global-require, import/no-dynamic-require
  const serverModule = require(serverModulePath);
  for (let port = API_PORT; port < API_PORT + 20; port += 1) {
    try {
      apiServer = await serverModule.startServer({ host: API_HOST, port, quiet: false });
      activeApiPort = port;
      logDesktop(`API started on ${API_HOST}:${activeApiPort}`);
      return apiServer;
    } catch (error) {
      if (error && error.code === 'EADDRINUSE') {
        const claDexRuntime = await readRuntimeInfo(port).catch(() => null);
        if (claDexRuntime) {
          activeApiPort = port;
          logDesktop(`API port ${port} already in use by CLADEX, reusing existing server`);
          await waitForApi(port);
          return null;
        }
        logDesktop(`API port ${port} already in use by a different service, trying next port`);
        continue;
      }
      logDesktop(`API start failed: ${error && error.stack ? error.stack : error}`);
      throw error;
    }
  }
  throw new Error(`No usable CLADEX API port found starting at ${API_PORT}`);
}

function readRuntimeInfo(port, timeoutMs = 1000) {
  return new Promise((resolve, reject) => {
    const request = http.get({ host: API_HOST, port, path: '/api/runtime-info', timeout: timeoutMs }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        try {
          const payload = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
          if (payload && payload.appVersion && Object.prototype.hasOwnProperty.call(payload, 'backendDir')) {
            resolve(payload);
            return;
          }
        } catch {}
        reject(new Error('Not a CLADEX runtime-info response'));
      });
    });
    request.on('error', reject);
    request.on('timeout', () => {
      request.destroy();
      reject(new Error('timeout'));
    });
  });
}

function waitForApi(port = activeApiPort, timeoutMs = 8000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      readRuntimeInfo(port, 1000).then(() => resolve()).catch(retry);
    };
    const retry = () => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error(`CLADEX API server did not become ready on ${API_HOST}:${port}`));
        return;
      }
      setTimeout(attempt, 250);
    };
    attempt();
  });
}

async function stopApiServer() {
  if (!apiServer) {
    return;
  }
  const serverModulePath = getServerModulePath();
  // eslint-disable-next-line global-require, import/no-dynamic-require
  const serverModule = require(serverModulePath);
  await serverModule.stopServer();
  apiServer = null;
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
      preload: path.join(__dirname, 'preload.cjs'),
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
  mainWindow.loadFile(indexPath, { query: { apiBase: `http://${API_HOST}:${activeApiPort}/api` } });

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

ipcMain.handle('cladex:choose-directory', async () => {
  const target = BrowserWindow.getFocusedWindow() || mainWindow;
  const result = await dialog.showOpenDialog(target || undefined, {
    title: 'Choose workspace folder',
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? '' : (result.filePaths[0] || '');
});

app.whenReady().then(async () => {
  try {
    await startApiServer();
    await waitForApi();
  } catch (error) {
    console.error(error);
    logDesktop(`Startup error: ${error && error.stack ? error.stack : error}`);
  }
  createWindow();

  app.on('second-instance', () => {
    if (!mainWindow) {
      return;
    }
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.focus();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  void stopApiServer();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  void stopApiServer();
});
