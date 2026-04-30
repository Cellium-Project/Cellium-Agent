const API_BASE = '/api';

export const API = {
  // Chat
  stream: `${API_BASE}/chat/stream`,
  supplement: `${API_BASE}/chat/supplement`,
  chatStatus: (sessionId: string) => `${API_BASE}/chat/status?session_id=${sessionId}`,
  chatStop: `${API_BASE}/chat/stop`,

  // Sessions
  sessions: `${API_BASE}/sessions`,
  sessionHistory: (sessionId: string) => `${API_BASE}/sessions/${sessionId}/history`,
  sessionDetail: (sessionId: string) => `${API_BASE}/sessions/${sessionId}`,
  sessionCreate: `${API_BASE}/session/create`,
  sessionLast: `${API_BASE}/session/last`,
  sessionList: `${API_BASE}/session/list`,
  sessionDelete: (sessionId: string) => `${API_BASE}/session/${sessionId}`,
  sessionRename: (sessionId: string) => `${API_BASE}/session/${sessionId}/title`,
  sessionSaveMessage: (sessionId: string) => `${API_BASE}/session/${sessionId}/save-message`,

  // Config
  configStatus: `${API_BASE}/config/status`,
  configGet: `${API_BASE}/config`,
  configSection: (section: string) => `${API_BASE}/config/${section}`,
  configReload: `${API_BASE}/config/reload`,
  configReloadSection: (section: string) => `${API_BASE}/config/reload/${section}`,
  configUpdate: (section: string) => `${API_BASE}/config/${section}`,
  configAutoReload: `${API_BASE}/config/auto-reload`,
  configValidate: `${API_BASE}/config/validate`,
  modelSwitch: `${API_BASE}/config/model/switch`,
  modelReloadEngine: `${API_BASE}/config/model/reload-engine`,
  modelListLocal: `${API_BASE}/config/model/list-local`,
  modelList: `${API_BASE}/config/models`,
  modelSave: `${API_BASE}/config/models`,
  modelAdd: `${API_BASE}/config/model`,
  modelDelete: (name: string) => `${API_BASE}/config/model/${name}`,

  // Memory
  memories: `${API_BASE}/memories`,
  memorySummary: `${API_BASE}/memories/summary`,
  memoryDetail: (memoryId: string) => `${API_BASE}/memories/${memoryId}`,
  memoryForget: `${API_BASE}/memories/actions/forget`,
  memoryMerge: `${API_BASE}/memories/actions/merge`,
  memoryArchive: (entryId: string) => `${API_BASE}/memories/archive/${entryId}`,

  // Logs
  logs: `${API_BASE}/logs`,
  logsStats: `${API_BASE}/logs/stats`,
  logsErrors: `${API_BASE}/logs/errors`,
  logsStatus: `${API_BASE}/logs/status`,
  logsStatusHistory: `${API_BASE}/logs/status/history`,
  channelReload: `${API_BASE}/channels/reload`,
  channelStatus: `${API_BASE}/channels/status`,
  channelStart: `${API_BASE}/channels/start`,
  channelStop: `${API_BASE}/channels/stop`,

  // Skills
  skills: `${API_BASE}/skills`,
  skillDetail: (name: string) => `${API_BASE}/skills/${name}`,
  skillSearch: `${API_BASE}/skills/search`,
  skillInstall: `${API_BASE}/skills/install`,
  skillRefreshIndex: `${API_BASE}/skills/refresh-index`,

  // Genes
  genes: `${API_BASE}/genes`,
  geneStats: `${API_BASE}/genes/stats`,
  geneDetail: (geneId: string) => `${API_BASE}/genes/${geneId}`,
  geneEvolve: (geneId: string) => `${API_BASE}/genes/${geneId}/evolve`,
  geneDelete: (geneId: string) => `${API_BASE}/genes/${geneId}`,

  // Scheduler
  scheduler: `${API_BASE}/scheduler`,
  schedulerStats: `${API_BASE}/scheduler/stats`,
  schedulerDetail: (taskId: string) => `${API_BASE}/scheduler/${taskId}`,
  schedulerToggle: (taskId: string) => `${API_BASE}/scheduler/${taskId}/toggle`,

  health: `${API_BASE}/health`,
} as const;


// Helper functions
export async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function postJSON<T>(url: string, data: any): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function patchJSON<T>(url: string, data: any): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function putJSON<T>(url: string, data: any): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteJSON<T>(url: string): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'DELETE',
  });
}
