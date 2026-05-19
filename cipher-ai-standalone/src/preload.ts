import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  auth: {
    login: (username: string, password: string) =>
      ipcRenderer.invoke('auth:login', username, password),
  },
  license: {
    generate: (expirationDays: number) => ipcRenderer.invoke('license:generate', expirationDays),
    all: () => ipcRenderer.invoke('license:all'),
    check: (code: string) => ipcRenderer.invoke('license:check', code),
    redeem: (code: string, userId: number) => ipcRenderer.invoke('license:redeem', code, userId),
    revoke: (code: string) => ipcRenderer.invoke('license:revoke', code),
  },
  ai: {
    health: () => ipcRenderer.invoke('ai:health'),
    analyze: (symbol: string, priceData: unknown) =>
      ipcRenderer.invoke('ai:analyze', symbol, priceData),
    risk: (portfolio: unknown, trade: unknown) =>
      ipcRenderer.invoke('ai:risk', portfolio, trade),
    signal: (symbol: string, indicators: unknown) =>
      ipcRenderer.invoke('ai:signal', symbol, indicators),
  },
});
