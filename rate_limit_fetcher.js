/**
 * Fetches accurate rate limit data from Anthropic API.
 * Uses OAuth token from Claude desktop app, auto-refreshes when expired.
 * Costs ~8 input tokens per call (minimal haiku request).
 *
 * Outputs JSON to stdout:
 * { pct_5h, reset_5h, pct_7d, reset_7d, ok: true }
 * or { ok: false, error: "..." }
 */

'use strict';
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e';
const TOKEN_FILE = path.join(os.homedir(), '.claude', 'proxy_oauth_token.json');
const SCOPES = 'user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload';

// Paths to Claude desktop app config (UWP package location)
const CLAUDE_PKG = path.join(os.homedir(), 'AppData', 'Local', 'Packages', 'Claude_pzs8sxrjxfjjc');
const DESKTOP_CONFIG = path.join(CLAUDE_PKG, 'LocalCache', 'Roaming', 'Claude', 'config.json');
const DESKTOP_LOCAL_STATE = path.join(CLAUDE_PKG, 'LocalCache', 'Roaming', 'Claude', 'Local State');

function decryptAesKey(encryptedKeyB64) {
  const buf = Buffer.from(encryptedKeyB64, 'base64').slice(5); // strip 'DPAPI'
  const bytes = Array.from(buf).join(',');
  const ps = `Add-Type -AssemblyName System.Security; $d=[System.Security.Cryptography.ProtectedData]::Unprotect([byte[]](${bytes}),$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser); ($d|ForEach-Object{$_.ToString('X2')})-join''`;
  return Buffer.from(execSync(`powershell -Command "${ps}"`, { encoding: 'utf8' }).trim(), 'hex');
}

function decryptChromeValue(encB64, aesKey) {
  const buf = Buffer.from(encB64, 'base64');
  const nonce = buf.slice(3, 15);
  const tag = buf.slice(-16);
  const ct = buf.slice(15, -16);
  const d = crypto.createDecipheriv('aes-256-gcm', aesKey, nonce);
  d.setAuthTag(tag);
  return Buffer.concat([d.update(ct), d.final()]).toString('utf8');
}

function loadTokensFromDesktop() {
  try {
    const localState = JSON.parse(fs.readFileSync(DESKTOP_LOCAL_STATE, 'utf8'));
    const config = JSON.parse(fs.readFileSync(DESKTOP_CONFIG, 'utf8'));
    const aesKey = decryptAesKey(localState.os_crypt.encrypted_key);
    const tokenCache = JSON.parse(decryptChromeValue(config['oauth:tokenCache'], aesKey));
    const now = Date.now();
    let tokens = null;
    for (const [key, val] of Object.entries(tokenCache)) {
      if (key.includes(CLIENT_ID) && key.includes('sessions:claude_code') && val.expiresAt > now) {
        tokens = { accessToken: val.token, refreshToken: val.refreshToken, expiresAt: val.expiresAt };
        break;
      }
    }
    if (!tokens) {
      for (const val of Object.values(tokenCache)) {
        if (val.expiresAt > now && val.refreshToken) {
          tokens = { accessToken: val.token, refreshToken: val.refreshToken, expiresAt: val.expiresAt };
          break;
        }
      }
    }
    if (tokens) saveTokens(tokens);  // cache for future use
    return tokens;
  } catch (e) {
    // Desktop app not running or config not found
  }
  return null;
}

function loadTokens() {
  // If cached file has a still-valid token, use it
  try {
    if (fs.existsSync(TOKEN_FILE)) {
      const data = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
      if (data.expiresAt > Date.now() + 60000) {
        return data;  // Still valid — use it
      }
    }
  } catch (e) {}
  // Cached token missing or expired — always re-read from desktop app first.
  // The desktop app may have silently refreshed its own token, making our
  // stored refresh token stale. Re-decrypting gets the current valid token.
  const fresh = loadTokensFromDesktop();
  if (fresh) return fresh;
  // Desktop not available — return expired cached token so caller can try refresh
  try {
    if (fs.existsSync(TOKEN_FILE)) {
      return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
    }
  } catch (e) {}
  return null;
}

function saveTokens(tokens) {
  try {
    fs.writeFileSync(TOKEN_FILE, JSON.stringify(tokens));
  } catch (e) {}
}

function post(hostname, path, body, headers) {
  return new Promise((resolve, reject) => {
    const bodyBuf = Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
    const req = https.request({ hostname, path, method: 'POST', headers: {
      'Content-Type': 'application/json',
      'Content-Length': bodyBuf.length,
      'User-Agent': 'claude-code/1.0.34',
      ...headers
    }}, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, body: d, headers: res.headers }));
    });
    req.on('error', reject);
    req.setTimeout(12000, () => req.destroy(new Error('timeout')));
    req.write(bodyBuf);
    req.end();
  });
}

async function refreshToken(refreshToken) {
  const r = await post('platform.claude.com', '/v1/oauth/token', {
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
    client_id: CLIENT_ID,
    scope: SCOPES,
  });
  if (r.status !== 200) throw new Error(`Refresh failed: ${r.status} ${r.body.slice(0, 200)}`);
  const data = JSON.parse(r.body);
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: Date.now() + data.expires_in * 1000,
  };
}

async function getValidToken() {
  let tokens = loadTokens();
  if (!tokens) throw new Error('No OAuth tokens found — Claude desktop app must be running and logged in');

  if (tokens.expiresAt <= Date.now() + 60000) {
    // Token expired. Try refresh, but the desktop app may have consumed the
    // refresh token already — if refresh fails, re-read from desktop app.
    if (tokens.refreshToken) {
      try {
        tokens = await refreshToken(tokens.refreshToken);
        saveTokens(tokens);
        return tokens.accessToken;
      } catch (e) {
        // Refresh token was invalid (likely consumed by the desktop app).
        // Try loading a fresh token directly from the desktop app config.
        const fresh = loadTokensFromDesktop();
        if (fresh && fresh.expiresAt > Date.now() + 60000) return fresh.accessToken;
        throw new Error('Token expired and refresh failed: ' + e.message +
          ' (Claude desktop app may need to be restarted to refresh its session)');
      }
    }
    throw new Error('Token expired and no refresh token available');
  }
  return tokens.accessToken;
}

async function fetchRateLimits() {
  const token = await getValidToken();
  const r = await post('api.anthropic.com', '/v1/messages',
    { model: 'claude-haiku-4-5-20251001', max_tokens: 1, messages: [{ role: 'user', content: '.' }] },
    { 'Authorization': `Bearer ${token}`, 'anthropic-version': '2023-06-01' }
  );
  if (r.status !== 200) throw new Error(`API call failed: ${r.status} ${r.body.slice(0, 200)}`);
  const h = r.headers;
  return {
    ok: true,
    pct_5h:   parseFloat(h['anthropic-ratelimit-unified-5h-utilization']  || '0'),
    reset_5h: parseInt(h['anthropic-ratelimit-unified-5h-reset']          || '0'),
    pct_7d:   parseFloat(h['anthropic-ratelimit-unified-7d-utilization']  || '0'),
    reset_7d: parseInt(h['anthropic-ratelimit-unified-7d-reset']          || '0'),
  };
}

(async () => {
  try {
    const result = await fetchRateLimits();
    console.log(JSON.stringify(result));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: e.message }));
    process.exit(1);
  }
})();
