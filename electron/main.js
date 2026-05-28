/**
 * OPERA AI — Electron Main Process
 *
 * Runtime Shell for AI Execution Platform.
 * Manages Python agent process, system tray, capability sync, and token lifecycle.
 */
const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

// ── State ──
let mainWindow = null;
let tray = null;
let isQuitting = false;
let agentProcess = null;
let agentRestartTimer = null;
let healthCheckTimer = null;

const runtimeState = {
  token: null,
  email: null,
  plan: 'trial',
  gpuName: null,
  gpuAvailable: false,
  creditsDaily: 0,
  creditsUsed: 0,
  workstationLimit: 1,
  maxConcurrent: 1,
  engineOnline: false,
  agentOnline: false,
  backgroundAutomation: false,
  lastCapabilityFetch: null,
  capabilityPollTimer: null,
};
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 min
const AGENT_PORT = 5000;
const SERVER_URL = app.isPackaged
  ? `http://127.0.0.1:${AGENT_PORT}`
  : `http://127.0.0.1:${AGENT_PORT}`;

// ── Resource Paths ──
function getAgentDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'agent');
  }
  return path.join(__dirname, '..', 'agent');
}

// ── Icon ──
function createTrayIcon(status) {
  const size = 16;
  const canvas = Buffer.alloc(size * size * 4, 0);
  const color = status === 'online' ? [74, 222, 128, 255]
    : status === 'starting' ? [240, 180, 41, 255]
    : [239, 68, 68, 255]; // red for offline
  for (let y = 2; y < 14; y++) {
    for (let x = 2; x < 14; x++) {
      const idx = (y * size + x) * 4;
      const dx = x - 7.5, dy = y - 7.5;
      if (Math.abs(dx) + Math.abs(dy) <= 4.5) {
        canvas[idx] = color[0];
        canvas[idx+1] = color[1];
        canvas[idx+2] = color[2];
        canvas[idx+3] = color[3];
      }
    }
  }
  return nativeImage.createFromBuffer(canvas, { width: size, height: size });
}

function notify(title, body) {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show();
  }
}

// ═══════════════════════════════════════════
//  PYTHON AGENT PROCESS MANAGEMENT
// ═══════════════════════════════════════════

function getAgentPythonCmd() {
  // Use bundled python from venv if available, otherwise system python3
  const agentDir = getAgentDir();
  const venvPython = path.join(agentDir, 'venv', 'bin', 'python3');
  if (fs.existsSync(venvPython)) return venvPython;
  return 'python3';
}

function startAgent() {
  if (agentProcess) {
    console.log('[agent] Already running, PID:', agentProcess.pid);
    return;
  }

  const agentDir = getAgentDir();
  const serverScript = path.join(agentDir, 'server.py');
  const pythonCmd = getAgentPythonCmd();

  if (!fs.existsSync(serverScript)) {
    console.error('[agent] server.py not found at:', serverScript);
    notify('OPERA AI', 'Agent runtime not found. Reinstall may be required.');
    return;
  }

  runtimeState.agentOnline = false;
  updateTrayIcon('starting');
  updateTrayMenu();
  notify('OPERA AI', 'Starting local AI agent...');

  console.log(`[agent] Starting: ${pythonCmd} ${serverScript} --port ${AGENT_PORT}`);
  console.log(`[agent] CWD: ${agentDir}`);

  agentProcess = spawn(pythonCmd, [serverScript, '--port', String(AGENT_PORT)], {
    cwd: agentDir,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  agentProcess.stdout.on('data', (data) => {
    const text = data.toString().trim();
    console.log(`[agent:stdout] ${text}`);
    // Detect agent ready from log
    if (text.includes('Running on')) {
      runtimeState.agentOnline = true;
      updateTrayIcon('online');
      updateTrayMenu();
      notify('OPERA AI', 'AI execution engine online.');
      startHealthCheck();
    }
  });

  agentProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    if (text) console.log(`[agent:stderr] ${text}`);
  });

  agentProcess.on('error', (err) => {
    console.error('[agent] Failed to start:', err.message);
    runtimeState.agentOnline = false;
    updateTrayIcon('offline');
    updateTrayMenu();
    notify('OPERA AI', `Agent failed: ${err.message}`);
  });

  agentProcess.on('exit', (code, signal) => {
    console.log(`[agent] Exited: code=${code} signal=${signal}`);
    agentProcess = null;
    runtimeState.agentOnline = false;
    stopHealthCheck();
    updateTrayIcon('offline');
    updateTrayMenu();

    if (!isQuitting && code !== 0) {
      notify('OPERA AI', `Agent stopped (code ${code}). Restarting...`);
      scheduleAgentRestart();
    }
  });
}

function stopAgent() {
  if (agentRestartTimer) {
    clearTimeout(agentRestartTimer);
    agentRestartTimer = null;
  }
  stopHealthCheck();
  if (agentProcess) {
    console.log('[agent] Stopping PID:', agentProcess.pid);
    agentProcess.kill('SIGTERM');
    // Force kill after 5 seconds
    setTimeout(() => {
      if (agentProcess) {
        try { agentProcess.kill('SIGKILL'); } catch (e) { /* already dead */ }
        agentProcess = null;
      }
    }, 5000);
  }
  runtimeState.agentOnline = false;
  updateTrayIcon('offline');
  updateTrayMenu();
}

function restartAgent() {
  notify('OPERA AI', 'Restarting AI agent...');
  stopAgent();
  // Wait for clean stop then restart
  setTimeout(startAgent, 2000);
}

function scheduleAgentRestart() {
  if (agentRestartTimer) clearTimeout(agentRestartTimer);
  const delay = 5000;
  notify('OPERA AI', `Restarting agent in ${delay/1000}s...`);
  agentRestartTimer = setTimeout(() => {
    agentRestartTimer = null;
    if (!agentProcess && !isQuitting) startAgent();
  }, delay);
}

// ── Health Check ──
function startHealthCheck() {
  stopHealthCheck();
  healthCheckTimer = setInterval(async () => {
    if (!runtimeState.agentOnline) return;
    try {
      const resp = await fetch(`http://127.0.0.1:${AGENT_PORT}/api/health`);
      if (!resp.ok) throw new Error('Health check failed');
    } catch (e) {
      console.log('[agent] Health check failed, agent may be down');
      runtimeState.agentOnline = false;
      updateTrayIcon('offline');
      updateTrayMenu();
      if (!isQuitting) scheduleAgentRestart();
    }
  }, 30000); // check every 30s
}
function stopHealthCheck() {
  if (healthCheckTimer) { clearInterval(healthCheckTimer); healthCheckTimer = null; }
}

function updateTrayIcon(status) {
  if (!tray) return;
  try { tray.setImage(createTrayIcon(status)); } catch (e) { /* ignore */ }
}

// ═══════════════════════════════════════════
//  WINDOW
// ═══════════════════════════════════════════

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100, height: 750, minWidth: 600, minHeight: 400,
    title: 'OPERA AI',
    icon: createTrayIcon('online'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadURL(`${SERVER_URL}/`);
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.on('close', (e) => {
    if (!isQuitting) { e.preventDefault(); mainWindow.hide(); }
  });
  mainWindow.webContents.on('page-title-updated', (e) => e.preventDefault());
}

// ═══════════════════════════════════════════
//  CAPABILITY SYNC
// ═══════════════════════════════════════════

async function fetchRuntimeStatus() {
  const tok = runtimeState.token;
  if (!tok) return;
  try {
    const resp = await fetch(`${SERVER_URL}/api/agent/capabilities`, {
      headers: { 'Authorization': `Bearer ${tok}` }
    });
    const data = await resp.json();
    if (data.ok && data.capabilities) {
      const c = data.capabilities;
      runtimeState.plan = c.plan;
      runtimeState.creditsDaily = c.execution_credits_daily || 0;
      runtimeState.workstationLimit = c.workstation_limit || 1;
      runtimeState.maxConcurrent = c.max_concurrent_tasks || 1;
      runtimeState.backgroundAutomation = c.background_execution || false;
      runtimeState.engineOnline = true;
      runtimeState.lastCapabilityFetch = new Date().toISOString();
      updateTrayMenu();
    } else if (data.error === 'authentication_required') {
      runtimeState.token = null;
      runtimeState.email = null;
      runtimeState.engineOnline = false;
      stopCapabilityPolling();
      notify('OPERA AI', 'Session expired. Please log in again.');
      updateTrayMenu();
    }
  } catch (e) {
    runtimeState.engineOnline = false;
    updateTrayMenu();
  }
}

function startCapabilityPolling() {
  stopCapabilityPolling();
  fetchRuntimeStatus();
  runtimeState.capabilityPollTimer = setInterval(fetchRuntimeStatus, POLL_INTERVAL_MS);
}
function stopCapabilityPolling() {
  if (runtimeState.capabilityPollTimer) {
    clearInterval(runtimeState.capabilityPollTimer);
    runtimeState.capabilityPollTimer = null;
  }
}

async function syncPlan() {
  const tok = runtimeState.token;
  if (!tok) { notify('OPERA AI', 'Please log in first.'); return; }
  try {
    const resp = await fetch(`${SERVER_URL}/api/auth/sync-plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${tok}` }
    });
    const data = await resp.json();
    if (data.ok) {
      notify('OPERA AI', `Plan synced: ${data.capabilities.plan}. Runtime updated.`);
      fetchRuntimeStatus();
    } else {
      notify('OPERA AI', 'Sync failed. Try again.');
    }
  } catch (e) {
    notify('OPERA AI', 'Cannot reach server.');
  }
}

// ═══════════════════════════════════════════
//  TRAY
// ═══════════════════════════════════════════

function updateTrayMenu() {
  if (!tray) return;
  const s = runtimeState;
  const agentStatus = s.agentOnline ? '🟢 Agent running' : (s.agentOnline === false && agentProcess ? '🟡 Agent starting' : '🔴 Agent offline');
  const engineLabel = s.engineOnline ? '🟢 API connected' : '🔴 API offline';
  const planLabel = s.plan.charAt(0).toUpperCase() + s.plan.slice(1);
  const gpuLabel = s.gpuAvailable ? `🎮 ${s.gpuName}` : '🎮 GPU: detecting...';

  const ctx = Menu.buildFromTemplate([
    { label: `⚡ OPERA AI — ${planLabel}`, enabled: false },
    { label: `${agentStatus} · ${engineLabel}`, enabled: false },
    { label: gpuLabel, enabled: false },
    { label: `🪙 ${s.creditsUsed} / ${s.creditsDaily} credits`, enabled: false },
    { type: 'separator' },
    { label: '📊 Open Dashboard', click: () => mainWindow ? mainWindow.show() : createWindow() },
    { label: '🔄 Sync Execution Plan', click: () => syncPlan() },
    { type: 'separator' },
    { label: '▶️ Start Agent', click: () => startAgent(), enabled: !s.agentOnline && !agentProcess },
    { label: '⏹ Stop Agent', click: () => stopAgent(), enabled: !!agentProcess },
    { label: '🔄 Restart Agent', click: () => restartAgent(), enabled: !!agentProcess },
    { type: 'separator' },
    { label: '🔄 Reload Interface', click: () => { if (mainWindow) mainWindow.webContents.reload(); } },
    { type: 'separator' },
    { label: '❌ Exit Runtime', click: () => { isQuitting = true; stopCapabilityPolling(); stopAgent(); app.quit(); } },
  ]);
  tray.setContextMenu(ctx);
}

function createTray() {
  tray = new Tray(createTrayIcon('starting'));
  tray.setToolTip('OPERA AI — Runtime');
  updateTrayMenu();
  tray.on('double-click', () => mainWindow ? mainWindow.show() : createWindow());
}

// ═══════════════════════════════════════════
//  IPC HANDLERS
// ═══════════════════════════════════════════

ipcMain.handle('get-platform', () => process.platform);
ipcMain.handle('minimize-window', () => { if (mainWindow) mainWindow.minimize(); });
ipcMain.handle('close-window', () => { if (mainWindow) mainWindow.hide(); });
ipcMain.handle('send-notification', (_, { title, body }) => notify(title, body));

ipcMain.handle('get-agent-status', () => ({
  online: runtimeState.agentOnline,
  engineOnline: runtimeState.engineOnline,
  plan: runtimeState.plan,
  gpuAvailable: runtimeState.gpuAvailable,
  gpuName: runtimeState.gpuName,
  token: runtimeState.token ? 'present' : null,
}));

ipcMain.handle('restart-agent', async () => {
  restartAgent();
  return { ok: true };
});

ipcMain.handle('start-agent', () => { startAgent(); return { ok: true }; });
ipcMain.handle('stop-agent', () => { stopAgent(); return { ok: true }; });

ipcMain.handle('runtime-login', (_, { token, email }) => {
  runtimeState.token = token;
  runtimeState.email = email;
  startCapabilityPolling();
  const store = getStore();
  store.set('opera_token', token);
  store.set('opera_email', email);
  notify('OPERA AI', `Runtime active. ${email}`);
  return { ok: true };
});

ipcMain.handle('runtime-logout', () => {
  stopCapabilityPolling();
  runtimeState.token = null;
  runtimeState.email = null;
  runtimeState.engineOnline = false;
  const store = getStore();
  store.delete('opera_token');
  store.delete('opera_email');
  updateTrayMenu();
  return { ok: true };
});

ipcMain.handle('sync-plan', async () => { await syncPlan(); return { ok: true }; });

ipcMain.handle('gpu-detected', (_, { gpuName }) => {
  runtimeState.gpuName = gpuName;
  runtimeState.gpuAvailable = !!gpuName;
  updateTrayMenu();
  return { ok: true };
});

ipcMain.handle('capability-update', (_, capabilities) => {
  if (capabilities) {
    Object.assign(runtimeState, {
      plan: capabilities.plan || runtimeState.plan,
      creditsDaily: capabilities.execution_credits_daily || runtimeState.creditsDaily,
      workstationLimit: capabilities.workstation_limit || runtimeState.workstationLimit,
      maxConcurrent: capabilities.max_concurrent_tasks || runtimeState.maxConcurrent,
      backgroundAutomation: capabilities.background_execution || false,
      engineOnline: true,
    });
    updateTrayMenu();
  }
  return { ok: true };
});

// ═══════════════════════════════════════════
//  APP LIFECYCLE
// ═══════════════════════════════════════════

// Shared electron-store instance
let _store = null;
function getStore() {
  if (!_store) _store = new (require('electron-store'))();
  return _store;
}

// ── Auto Launch (Windows/Mac) ──
function setupAutoLaunch() {
  try {
    app.setLoginItemSettings({
      openAtLogin: true,
      path: app.getPath('exe'),
    });
    console.log('[runtime] Auto-launch configured');
  } catch (e) {
    console.log('[runtime] Auto-launch not supported on this platform');
  }
}

// ── Port availability check ──
function checkAgentPort() {
  const net = require('net');
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(AGENT_PORT, '127.0.0.1', () => {
      server.close(() => resolve(true)); // port free
    });
    server.on('error', () => resolve(false)); // port in use
  });
}

app.whenReady().then(async () => {
  setupAutoLaunch();
  createTray();
  notify('OPERA AI', 'Initializing OPERA Runtime...');

  // Check if agent port is available
  const portFree = await checkAgentPort();
  if (!portFree) {
    notify('OPERA AI', 'Agent port 5000 in use. Using existing agent.');
    runtimeState.agentOnline = true;
    updateTrayIcon('online');
    updateTrayMenu();
    startHealthCheck();
  } else {
    startAgent();
  }

  // Restore token from electron-store
  const store = getStore();
  const savedToken = store.get('opera_token');
  const savedEmail = store.get('opera_email');

  if (savedToken) {
    runtimeState.token = savedToken;
    runtimeState.email = savedEmail;
    setTimeout(() => startCapabilityPolling(), 3000);
  }

  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && isQuitting) { stopAgent(); app.quit(); }
});
app.on('before-quit', () => {
  isQuitting = true;
  stopCapabilityPolling();
  stopAgent();
});
app.on('activate', () => { if (mainWindow) mainWindow.show(); else createWindow(); });
