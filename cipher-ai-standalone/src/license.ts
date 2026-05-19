import crypto from 'crypto';
import { getDb } from './database';

// 16 cryptographically random hex bytes → 32-char uppercase hex code
export function generateLicenseCode(): string {
  return crypto.randomBytes(8).toString('hex').toUpperCase();
}

export function validateCodeFormat(code: string): boolean {
  return /^[A-F0-9]{16}$/.test(code);
}

export function createLicenseCode(
  expirationDays: number = 30,
  createdBy?: number
): { success: boolean; code?: string; message: string } {
  const code = generateLicenseCode();
  const expiresAt = new Date();
  expiresAt.setDate(expiresAt.getDate() + expirationDays);

  try {
    getDb()
      .prepare(
        `INSERT INTO license_codes (code, created_by, expires_at, status) VALUES (?, ?, ?, 'active')`
      )
      .run(code, createdBy ?? null, expiresAt.toISOString());
    return { success: true, code, message: 'License code created' };
  } catch (e) {
    return { success: false, message: String(e) };
  }
}

export function getAllLicenseCodes(): unknown[] {
  return getDb()
    .prepare(`SELECT * FROM license_codes ORDER BY created_at DESC`)
    .all();
}

export function checkLicenseCode(code: string): {
  valid: boolean;
  message: string;
  expired: boolean;
} {
  if (!validateCodeFormat(code)) {
    return { valid: false, message: 'Invalid license code format', expired: false };
  }

  const row = getDb()
    .prepare(`SELECT * FROM license_codes WHERE code = ?`)
    .get(code) as
    | { status: string; expires_at: string | null; used_at: string | null }
    | undefined;

  if (!row) return { valid: false, message: 'License code not found', expired: false };
  if (row.expires_at && new Date(row.expires_at) < new Date())
    return { valid: false, message: 'License code has expired', expired: true };
  if (row.used_at)
    return { valid: false, message: 'License code already used', expired: false };
  if (row.status !== 'active')
    return { valid: false, message: `License code is ${row.status}`, expired: false };

  return { valid: true, message: 'License code is valid', expired: false };
}

export function useLicenseCode(
  code: string,
  userId?: number
): { success: boolean; message: string } {
  const check = checkLicenseCode(code);
  if (!check.valid) return { success: false, message: check.message };

  getDb()
    .prepare(
      `UPDATE license_codes SET used_at = CURRENT_TIMESTAMP, used_by = ?, status = 'used' WHERE code = ?`
    )
    .run(userId ?? null, code);

  return { success: true, message: 'License code redeemed successfully' };
}

export function revokeLicenseCode(code: string): { success: boolean; message: string } {
  const result = getDb()
    .prepare(`UPDATE license_codes SET status = 'revoked' WHERE code = ? AND status = 'active'`)
    .run(code);

  if (result.changes === 0)
    return { success: false, message: 'Code not found or not in active state' };
  return { success: true, message: 'License code revoked' };
}
