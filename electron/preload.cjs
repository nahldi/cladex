const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cladexDesktop', {
  chooseDirectory: () => ipcRenderer.invoke('cladex:choose-directory'),
});
