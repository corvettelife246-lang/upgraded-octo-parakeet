import 'dotenv/config';
import { app, BrowserWindow, Menu, ipcMain, shell } from 'electron';
import path from 'path';
import { initializeDatabase } from './database';
import { startExpressServer, stopExpressServer } from './server';
import { loginUser, signToken } from './auth';
import {
  createLicenseCode,
  getAllLicenseCodes,
  checkLicenseCode,
  useLicenseCode,
  revokeLicenseCode,
} from './license';
import { analyzeMarket, analyzeRisk, generateSignal, healthCheck } from './ai';

const PORT = Number(process.env.PORT) || 3001;
let mainWindow: BrowserWindow | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0d1117',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
    title: 'Cipher-AI Trading System',
  });

  mainWindow.loadURL(`http://localhost:${PORT}`);

  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }

  // Open external links in system browser, not in Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: 'File',
      submenu: [{ label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() }],
    },
    {
      label: 'View',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => mainWindow?.reload() },
        { label: 'Toggle DevTools', accelerator: 'F12', click: () => mainWindow?.webContents.toggleDevTools() },
        { type: 'separator' },
        { label: 'Actual Size', accelerator: 'CmdOrCtrl+0', click: () => mainWindow?.webContents.setZoomLevel(0) },
        { label: 'Zoom In', accelerator: 'CmdOrCtrl+Plus', click: () => mainWindow?.webContents.setZoomLevel((mainWindow?.webContents.getZoomLevel() ?? 0) + 0.5) },
        { label: 'Zoom Out', accelerator: 'CmdOrCtrl+-', click: () => mainWindow?.webContents.setZoomLevel((mainWindow?.webContents.getZoomLevel() ?? 0) - 0.5) },
      ],
    },
    {
      label: 'Help',
      submenu: [
        { label: 'About Cipher-AI', click: () => {
          mainWindow?.webContents.executeJavaScript(
            `alert('Cipher-AI Trading System\\nVersion 1.0.0\\nFoundry Local AI Integration')`
          );
        }},
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// -- IPC bridge: auth --
ipcMain.handle('auth:login', async (_e, username: string, password: string) => {
  const user = loginUser(username, password);
  if (!user) return { success: false, error: 'Invalid credentials' };
  return { success: true, token: signToken(user), user };
});

// -- IPC bridge: licenses --
ipcMain.handle('license:generate', async (_e, expirationDays: number) => {
  return createLicenseCode(expirationDays);
});
ipcMain.handle('license:all', async () => {
  return { success: true, licenses: getAllLicenseCodes() };
});
ipcMain.handle('license:check', async (_e, code: string) => {
  return checkLicenseCode(code);
});
ipcMain.handle('license:redeem', async (_e, code: string, userId: number) => {
  return useLicenseCode(code, userId);
});
ipcMain.handle('license:revoke', async (_e, code: string) => {
  return revokeLicenseCode(code);
});

// -- IPC bridge: AI --
ipcMain.handle('ai:health', async () => healthCheck());
ipcMain.handle('ai:analyze', async (_e, symbol: string, priceData: unknown) =>
  analyzeMarket(symbol, priceData)
);
ipcMain.handle('ai:risk', async (_e, portfolio: unknown, trade: unknown) =>
  analyzeRisk(portfolio, trade)
);
ipcMain.handle('ai:signal', async (_e, symbol: string, indicators: unknown) =>
  generateSignal(symbol, indicators)
);

// -- App lifecycle --
app.whenReady().then(async () => {
  try {
    initializeDatabase();
    await startExpressServer(PORT);
    buildMenu();
    createWindow();
  } catch (err) {
    console.error('Startup failed:', err);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});

app.on('before-quit', async () => {
  await stopExpressServer();
});
