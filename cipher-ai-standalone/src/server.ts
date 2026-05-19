import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import path from 'path';
import bcrypt from 'bcrypt';
import http from 'http';
import { getDb } from './database';
import { requireAuth, requireAdmin, signToken, loginUser } from './auth';
import {
  createLicenseCode,
  getAllLicenseCodes,
  checkLicenseCode,
  useLicenseCode,
  revokeLicenseCode,
} from './license';
import { analyzeMarket, analyzeRisk, generateSignal, healthCheck } from './ai';

const app = express();
let httpServer: http.Server | null = null;

// -- Middleware --
app.use(cors({ origin: ['http://localhost', 'http://127.0.0.1', /^http:\/\/localhost:\d+$/] }));
app.use(express.json({ limit: '1mb' }));
app.use(express.static(path.join(__dirname, '../public')));

// -- Auth --
app.post('/api/auth/login', (req: Request, res: Response) => {
  const { username, password } = req.body ?? {};
  if (!username || !password) { res.status(400).json({ success: false, error: 'username and password required' }); return; }
  const user = loginUser(username, password);
  if (!user) { res.status(401).json({ success: false, error: 'Invalid credentials' }); return; }
  res.json({ success: true, token: signToken(user), user: { id: user.id, username: user.username, role: user.role } });
});

// -- Health --
app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', ts: new Date().toISOString() });
});

// -- Licenses --
app.post('/api/license/generate', requireAdmin, (req: Request, res: Response) => {
  const { expirationDays } = req.body ?? {};
  res.json(createLicenseCode(expirationDays ?? 30, req.user!.id));
});

app.get('/api/license/all', requireAdmin, (_req, res) => {
  res.json({ success: true, licenses: getAllLicenseCodes() });
});

app.post('/api/license/validate', requireAuth, (req: Request, res: Response) => {
  const { code } = req.body ?? {};
  if (!code) { res.status(400).json({ success: false, error: 'code required' }); return; }
  res.json(checkLicenseCode(code));
});

app.post('/api/license/redeem', requireAuth, (req: Request, res: Response) => {
  const { code } = req.body ?? {};
  if (!code) { res.status(400).json({ success: false, error: 'code required' }); return; }
  res.json(useLicenseCode(code, req.user!.id));
});

app.post('/api/license/revoke', requireAdmin, (req: Request, res: Response) => {
  const { code } = req.body ?? {};
  if (!code) { res.status(400).json({ success: false, error: 'code required' }); return; }
  res.json(revokeLicenseCode(code));
});

// -- Users --
app.get('/api/users', requireAdmin, (_req, res) => {
  const users = getDb().prepare('SELECT id, username, email, role, created_at FROM users').all();
  res.json({ success: true, users });
});

app.post('/api/users', requireAdmin, async (req: Request, res: Response) => {
  const { username, email, password, role } = req.body ?? {};
  if (!username || !email || !password) { res.status(400).json({ success: false, error: 'username, email and password required' }); return; }
  const userRole = ['user', 'admin'].includes(role) ? role : 'user';
  try {
    const hash = await bcrypt.hash(password, 12);
    getDb().prepare(`INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)`).run(username, email, hash, userRole);
    res.json({ success: true, message: 'User created' });
  } catch (e: unknown) {
    res.status(400).json({ success: false, error: e instanceof Error ? e.message : String(e) });
  }
});

// -- Downloads --
app.post('/api/downloads', requireAuth, (req: Request, res: Response) => {
  const { installerType } = req.body ?? {};
  getDb().prepare(`INSERT INTO downloads (user_id, installer_type) VALUES (?, ?)`).run(req.user!.id, installerType ?? 'unknown');
  res.json({ success: true, message: 'Download recorded' });
});

// -- AI routes --
app.get('/api/ai/health', requireAuth, async (_req, res) => {
  res.json(await healthCheck());
});

app.post('/api/ai/analyze', requireAuth, async (req: Request, res: Response) => {
  const { symbol, priceData } = req.body ?? {};
  if (!symbol || !priceData) { res.status(400).json({ success: false, error: 'symbol and priceData required' }); return; }
  try { res.json({ success: true, analysis: await analyzeMarket(symbol, priceData) }); }
  catch (e) { res.status(502).json({ success: false, error: String(e) }); }
});

app.post('/api/ai/risk', requireAuth, async (req: Request, res: Response) => {
  const { portfolio, trade } = req.body ?? {};
  if (!portfolio || !trade) { res.status(400).json({ success: false, error: 'portfolio and trade required' }); return; }
  try { res.json({ success: true, assessment: await analyzeRisk(portfolio, trade) }); }
  catch (e) { res.status(502).json({ success: false, error: String(e) }); }
});

app.post('/api/ai/signal', requireAuth, async (req: Request, res: Response) => {
  const { symbol, indicators } = req.body ?? {};
  if (!symbol || !indicators) { res.status(400).json({ success: false, error: 'symbol and indicators required' }); return; }
  try { res.json({ success: true, signal: await generateSignal(symbol, indicators) }); }
  catch (e) { res.status(502).json({ success: false, error: String(e) }); }
});

// -- SPA fallback --
app.get(/^(?!\/api).*/, (_req, res) => {
  res.sendFile(path.join(__dirname, '../public/index.html'));
});

// -- Error handler --
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error(err);
  res.status(500).json({ success: false, error: 'Internal server error' });
});

export function startExpressServer(port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    httpServer = app.listen(port, '127.0.0.1', () => {
      console.log(`Express server listening on http://localhost:${port}`);
      resolve();
    });
    httpServer.on('error', reject);
  });
}

export function stopExpressServer(): Promise<void> {
  return new Promise((resolve) => {
    httpServer?.close(() => resolve());
    if (!httpServer) resolve();
  });
}

// Allow running standalone (server-only mode for WSL2 without display)
if (require.main === module) {
  require('dotenv/config');
  const { initializeDatabase } = require('./database');
  const PORT = Number(process.env.PORT) || 3001;
  initializeDatabase();
  startExpressServer(PORT).then(() => {
    console.log(`\nCipher-AI server-only mode`);
    console.log(`Open in browser: http://localhost:${PORT}`);
    console.log(`Foundry Local: http://${process.env.FOUNDRY_HOST || 'localhost'}:${process.env.FOUNDRY_PORT || '5273'}/v1\n`);
  });
}
