import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import { getDb } from './database';

export interface AuthUser {
  id: number;
  username: string;
  role: string;
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthUser;
    }
  }
}

function getSecret(): string {
  const s = process.env.JWT_SECRET;
  if (!s) throw new Error('JWT_SECRET env var is required');
  return s;
}

export function signToken(user: AuthUser): string {
  return jwt.sign({ id: user.id, username: user.username, role: user.role }, getSecret(), {
    expiresIn: '8h',
  });
}

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const header = req.headers.authorization;
  if (!header?.startsWith('Bearer ')) {
    res.status(401).json({ success: false, error: 'Missing or invalid Authorization header' });
    return;
  }
  try {
    const payload = jwt.verify(header.slice(7), getSecret()) as AuthUser;
    req.user = payload;
    next();
  } catch {
    res.status(401).json({ success: false, error: 'Invalid or expired token' });
  }
}

export function requireAdmin(req: Request, res: Response, next: NextFunction): void {
  requireAuth(req, res, () => {
    if (req.user?.role !== 'admin') {
      res.status(403).json({ success: false, error: 'Admin access required' });
      return;
    }
    next();
  });
}

export function loginUser(username: string, password: string): AuthUser | null {
  const bcrypt = require('bcrypt');
  const db = getDb();
  const row = db
    .prepare('SELECT id, username, password_hash, role FROM users WHERE username = ?')
    .get(username) as { id: number; username: string; password_hash: string; role: string } | undefined;
  if (!row) return null;
  const ok = bcrypt.compareSync(password, row.password_hash);
  return ok ? { id: row.id, username: row.username, role: row.role } : null;
}
