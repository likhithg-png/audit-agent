import { useEffect, useState } from "react"
import { api } from "../api"

const STATUS_CHIP = {
  NEW: { label: "New", cls: "chip-neutral" },
  PROCESSING: { label: "Processing...", cls: "chip-processing" },
  NEEDS_REVIEW: { label: "Needs review", cls: "chip-warning" },
  ERROR: { label: "Error", cls: "chip-error" },
  REPLIED: { label: "Replied", cls: "chip-success" },
}

function StatusChip({ status }) {
  const chip = STATUS_CHIP[status] || STATUS_CHIP.NEW
  return <span className={`chip ${chip.cls}`}>{chip.label}</span>
}

// `from` arrives as a raw header: '"Pannaga N (Work)" <pannaga.n@creditnirvana.ai>'.
// Showing it whole is what used to overflow the row -- split it so the name can lead
// and the address can be dropped to a title tooltip.
function parseFrom(raw = "") {
  const m = raw.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/)
  if (m) return { name: m[1].trim() || m[2], email: m[2] }
  return { name: raw.trim(), email: raw.trim() }
}

// "Fri, 20 Nov 2026 08:01:49 +0530" -> "20 Nov, 08:01" (or the year if it's not this year).
function shortDate(raw = "") {
  const d = new Date(raw)
  if (isNaN(d)) return raw
  const now = new Date()
  const sameYear = d.getFullYear() === now.getFullYear()
  const day = d.toLocaleDateString(undefined, { day: "numeric", month: "short" })
  return sameYear
    ? `${day}, ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`
    : `${day} ${d.getFullYear()}`
}

function Confidence({ value }) {
  if (value === null || value === undefined) return null
  const pct = Math.round(value * 100)
  return (
    <span className="conf" title={`${pct}% likely a genuine audit request`}>
      <span className="conf-bar">
        <span className={`conf-fill ${pct < 50 ? "low" : ""}`} style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  )
}

function MessageRow({ msg, running, onStarted, onView, onError }) {
  const [open, setOpen] = useState(false)
  const alreadyRan = ["NEEDS_REVIEW", "ERROR", "REPLIED"].includes(msg.status)
  // A row auto-started by the low confidence bar is already running in the background
  // (it has a request_id the moment mark_processing() sets it) -- clicking in should
  // jump straight to that live run log, not offer to start a second, redundant run
  // that would just 409 against the one already in progress.
  const isProcessing = msg.status === "PROCESSING"

  // No account list or period fields here -- a Gmail-sourced run always has the real
  // email to read both from (same fallback the auto-start path already uses); an
  // override UI was never needed for this entry point, only for upload/paste where
  // there's no email to fall back to.
  async function submit() {
    try {
      const { request_id } = await api.startInboxRun(msg.message_id, new FormData())
      onStarted(request_id)
    } catch (e) {
      onError(e.message)
    }
  }

  const { name, email } = parseFrom(msg.from)
  const subject = (msg.subject || "").trim()

  return (
    <div className={`inbox-row ${open ? "open" : ""}`}>
      <div className="inbox-row-main" onClick={() => setOpen((v) => !v)}>
        <div className="inbox-text">
          <span className={`inbox-subject ${subject ? "" : "none"}`} title={subject || undefined}>
            {subject || "(no subject)"}
          </span>
          <div className="inbox-meta">
            <span className="inbox-sender" title={email}>{name}</span>
            <span className="inbox-date">{shortDate(msg.date)}</span>
          </div>
          {msg.snippet && <p className="inbox-snippet">{msg.snippet}</p>}
        </div>
        <div className="inbox-side">
          <StatusChip status={msg.status} />
          {msg.has_pdf && <span className="badge-pill">PDF</span>}
          <Confidence value={msg.confidence} />
        </div>
      </div>
      {open && (
        <div className="inbox-row-run">
          {isProcessing ? (
            <button className="primary" onClick={() => onView(msg.request_id)}>View progress</button>
          ) : (
            <>
              {alreadyRan && msg.request_id && (
                <button onClick={() => onView(msg.request_id)}>
                  {msg.status === "ERROR" ? "View error log" : "View result"}
                </button>
              )}
              <button className="primary" onClick={submit} disabled={running}>
                {running ? "Running..." : alreadyRan ? "Run again" : "Run agent on this email"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

const FILTERS = [
  { key: "ALL", label: "All" },
  { key: "NEW", label: "New" },
  { key: "NEEDS_REVIEW", label: "Needs review" },
  { key: "REPLIED", label: "Replied" },
]

export default function InboxPane({ running, onStarted, onView, onError }) {
  const [status, setStatus] = useState(null)
  const [messages, setMessages] = useState(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState("ALL")
  const [autoReply, setAutoReply] = useState(null)

  // Opening the tab reads the saved list from the database -- no Gmail call.
  useEffect(() => {
    api.inboxStatus().then((st) => {
      setStatus(st)
      if (st.connected) {
        load(false)
        api.getAutoReply().then((r) => setAutoReply(r.enabled)).catch(() => {})
      }
    }).catch((e) => onError(e.message))
  }, [])

  async function connect() {
    try {
      const { url } = await api.inboxAuthUrl()
      window.location.href = url
    } catch (e) {
      onError(e.message)
    }
  }

  // Persisted server-side (audit.app_settings), not just local UI state -- it decides
  // real behavior in the background auto-start path, which has no browser tab open at all.
  async function toggleAutoReply() {
    const next = !autoReply
    setAutoReply(next)
    try {
      await api.setAutoReply(next)
    } catch (e) {
      setAutoReply(!next)
      onError(e.message)
    }
  }

  // sync=false: saved list only. sync=true: also fetch mail not seen before.
  async function load(sync) {
    setLoading(true)
    try {
      setMessages(await api.inboxMessages(sync))
    } catch (e) {
      onError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (!status) return <section className="pane"><p className="muted">Checking inbox connection...</p></section>

  if (!status.configured) {
    return (
      <section className="pane">
        <h2>Gmail inbox</h2>
        <p className="badge warning">
          Not configured -- set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the backend's .env (see README.md
          "Gmail inbox setup") to enable this.
        </p>
      </section>
    )
  }

  if (!status.connected) {
    return (
      <section className="pane">
        <h2>Gmail inbox</h2>
        <p className="muted">Connect a mailbox to pull in real audit-request emails.</p>
        <button className="primary" onClick={connect}>Connect Gmail</button>
      </section>
    )
  }

  const shown = (messages || []).filter((m) => filter === "ALL" || m.status === filter)

  return (
    <section className="pane">
      <div className="pane-head">
        <div>
          <h2>Gmail inbox</h2>
          <p className="pane-sub">
            Saved list -- only new mail is fetched when you check. Mail you sent is never listed. A new email that
            names account ids starts processing by itself.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <button className="primary" onClick={() => load(true)} disabled={loading}>
            {loading ? "Checking..." : "Check for new mail"}
          </button>
          {autoReply !== null && (
            <label className="attach-opt" title="When off, a clarifying question waits for you to review and send it yourself instead of going out on its own.">
              <input type="checkbox" checked={autoReply} onChange={toggleAutoReply} />
              <span>Auto-send clarifying questions</span>
            </label>
          )}
        </div>
      </div>

      <div className="inbox-toolbar">
        <div className="filter-chips">
          {FILTERS.map((f) => (
            <button key={f.key} className={`filter-chip ${filter === f.key ? "active" : ""}`} onClick={() => setFilter(f.key)}>
              {f.label}{messages ? ` (${f.key === "ALL" ? messages.length : messages.filter((m) => m.status === f.key).length})` : ""}
            </button>
          ))}
        </div>
      </div>

      {messages === null ? (
        <p className="muted">Loading saved list...</p>
      ) : shown.length === 0 ? (
        <p className="muted">
          {messages.length === 0 ? 'Nothing saved yet -- click "Check for new mail".' : "No emails in this view."}
        </p>
      ) : (
        <div className="inbox-list">
          {shown.map((m) => (
            <MessageRow key={m.message_id} msg={m} running={running} onStarted={onStarted} onView={onView} onError={onError} />
          ))}
        </div>
      )}
    </section>
  )
}
