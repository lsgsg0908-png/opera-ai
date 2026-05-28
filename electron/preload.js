/**
 * OPERA AI — Preload Script
 *
 * Safe IPC bridge (contextIsolation).
 * Exposes runtime API to renderer (web page).
 */
const { contextBridge, ipcRenderer } = require('electron');

let _capabilityPollTimer = null;
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

contextBridge.exposeInMainWorld('opera', {
  // ── Agent/Runtime Status ──
  getAgentStatus: () => ipcRenderer.invoke('get-agent-status'),
  restartAgent: () => ipcRenderer.invoke('restart-agent'),
  startAgent: () => ipcRenderer.invoke('start-agent'),
  stopAgent: () => ipcRenderer.invoke('stop-agent'),

  // ── Window Control ──
  minimize: () => ipcRenderer.invoke('minimize-window'),
  close: () => ipcRenderer.invoke('close-window'),

  // ── Notifications ──
  notify: (title, body) => ipcRenderer.invoke('send-notification', { title, body }),

  // ── Platform Info ──
  platform: process.platform,

  // ── Token Lifecycle ──
  // Call after login success to start runtime sync
  login: (token, email) => ipcRenderer.invoke('runtime-login', { token, email }),

  // Call on logout
  logout: () => ipcRenderer.invoke('runtime-logout'),

  // ── Plan Sync ──
  syncPlan: () => ipcRenderer.invoke('sync-plan'),

  // ── GPU Detection ──
  reportGpu: (gpuName) => ipcRenderer.invoke('gpu-detected', { gpuName }),

  // ── Capability Sync ──
  // Fetch capabilities from server directly (for renderer use)
  fetchCapabilities: async (token) => {
    const serverUrl = window.location.origin;
    const resp = await fetch(`${serverUrl}/api/agent/capabilities`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await resp.json();
    if (data.ok && data.capabilities) {
      ipcRenderer.invoke('capability-update', data.capabilities);
    }
    return data;
  },

  // Start renderer-side capability polling
  startCapabilityPolling: (token) => {
    if (_capabilityPollTimer) clearInterval(_capabilityPollTimer);
    _capabilityPollTimer = setInterval(async () => {
      const serverUrl = window.location.origin;
      try {
        const resp = await fetch(`${serverUrl}/api/agent/capabilities`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await resp.json();
        if (data.ok && data.capabilities) {
          ipcRenderer.invoke('capability-update', data.capabilities);
        }
      } catch (e) { /* silent */ }
    }, POLL_INTERVAL_MS);
  },

  stopCapabilityPolling: () => {
    if (_capabilityPollTimer) {
      clearInterval(_capabilityPollTimer);
      _capabilityPollTimer = null;
    }
  },

  // ── Capability Event Listener ──
  onCapabilityUpdate: (callback) => {
    ipcRenderer.on('capability-update', (_, capabilities) => callback(capabilities));
  },
});
