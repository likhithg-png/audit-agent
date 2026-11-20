const BASE = "/api"

async function json(res) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  listRequests: () => fetch(`${BASE}/requests`).then(json),
  getText: (id) => fetch(`${BASE}/requests/${id}/text`).then(json),
  startRun: (id) => fetch(`${BASE}/requests/${id}/run`, { method: "POST" }).then(json),

  listGuidelines: () => fetch(`${BASE}/guidelines`).then(json),
  listLenders: () => fetch(`${BASE}/lenders`).then(json),
  startCustomRun: (formData) => fetch(`${BASE}/custom/run`, { method: "POST", body: formData }).then(json),

  inboxStatus: () => fetch(`${BASE}/inbox/status`).then(json),
  inboxAuthUrl: () => fetch(`${BASE}/inbox/auth-url`).then(json),
  inboxMessages: (sync = false) => fetch(`${BASE}/inbox/messages?sync=${sync}`).then(json),
  startInboxRun: (messageId, formData) =>
    fetch(`${BASE}/inbox/messages/${messageId}/run`, { method: "POST", body: formData }).then(json),

  getAutoReply: () => fetch(`${BASE}/inbox/auto-reply`).then(json),
  setAutoReply: (enabled) => {
    const fd = new FormData()
    fd.set("enabled", enabled)
    return fetch(`${BASE}/inbox/auto-reply`, { method: "POST", body: fd }).then(json)
  },

  getStatus: (id) => fetch(`${BASE}/requests/${id}/status`).then(json),
  getResult: (id) => fetch(`${BASE}/requests/${id}/result`).then(json),
  getGaps: (id, { checkType, severity } = {}) => {
    const params = new URLSearchParams()
    if (checkType) params.set("check_type", checkType)
    if (severity) params.set("severity", severity)
    const qs = params.toString()
    return fetch(`${BASE}/requests/${id}/gaps${qs ? `?${qs}` : ""}`).then(json)
  },
  getAccountResults: (id) => fetch(`${BASE}/requests/${id}/account-results`).then(json),
  getVerdicts: (id, status) =>
    fetch(`${BASE}/requests/${id}/verdicts?status=${encodeURIComponent(status)}`).then(json),
  downloadUrl: (id, kind) => `${BASE}/requests/${id}/download/${kind}`,

  sendReply: (id, body, attach = []) => {
    const fd = new FormData()
    fd.set("body", body)
    if (attach.length) fd.set("attach", attach.join(","))
    return fetch(`${BASE}/requests/${id}/send-reply`, { method: "POST", body: fd }).then(json)
  },
}
