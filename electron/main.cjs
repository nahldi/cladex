const { app, BrowserWindow, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

let mainWindow = null;
let apiServer = null;

const isDev = !app.isPackaged;

function startApiServer() {
  const serverPath = path.join(__dirname, '..', 'server.ts');

  apiServer = spawn('npx', ['tsx', serverPath], {
    cwd: path.join(__dirname, '..'),
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

function createWindow() {
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

app.whenReady().then(() => {
  startApiServer();

  // Give API server a moment to start
  setTimeout(createWindow, 1500);

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
