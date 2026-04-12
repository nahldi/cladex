const { app, BrowserWindow, shell, ipcMain, dialog } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

let mainWindow = null;
let apiServer = null;
const API_PORT = Number(process.env.API_PORT || 3001);
const API_HOST = process.env.API_HOST || '127.0.0.1';

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
  try {
    apiServer = await serverModule.startServer({ host: API_HOST, port: API_PORT, quiet: false });
    logDesktop(`API started on ${API_HOST}:${API_PORT}`);
  } catch (error) {
    if (error && error.code === 'EADDRINUSE') {
      logDesktop(`API port ${API_PORT} already in use, waiting for existing server`);
      await waitForApi();
      return null;
    }
    logDesktop(`API start failed: ${error && error.stack ? error.stack : error}`);
    throw error;
  }
  return apiServer;
}

function waitForApi(timeoutMs = 8000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get({ host: API_HOST, port: API_PORT, path: '/api/runtime-info', timeout: 1000 }, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode >= 200 && response.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
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
