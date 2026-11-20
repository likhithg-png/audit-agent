import { useEffect, useState } from "react"
import { api } from "../api"

// The backend only ever emits a flat list of plain-English lines (api/main.py's
// _append_log calls). This groups them into the pipeline's 4 known macro-stages
// client-side -- by matching the literal headline text each stage starts with --
// so loading reads as a checklist with real progress instead of a scrolling list
// that looks identical whether 1 line or 10 have printed.
const STAGES = [
  { key: "decompose", label: "Understand the request", starts: "Decomposing the request..." },
  { key: "run", label: "Test the sample", starts: "Running checks against the sample..." },
  { key: "pack", label: "Build the evidence pack", starts: "Building the evidence pack..." },
  { key: "letter", label: "Draft the response letter", starts: "Drafting the response letter..." },
]

function groupLog(log) {
  const stages = STAGES.map((s) => ({ ...s, items: [] }))
  let current = -1
  let doneLine = null
  for (const raw of log) {
    const line = raw.trim()
    const indented = raw.startsWith("  ")
    if (!indented) {
      if (line === "Done") { doneLine = line; continue }
      if (line.startsWith("Error:")) continue // surfaced separately via the `error` prop
      const next = STAGES.findIndex((s, i) => i > current && s.starts === line)
      if (next !== -1) { current = next; continue }
    }
    if (current >= 0) stages[current].items.push(line)
  }
  return { stages, current, doneLine }
}

function StageIcon({ state }) {
  if (state === "done") return <span className="stage-icon stage-icon-done">✓</span>
  if (state === "active") return <span className="spinner" />
  if (state === "error") return <span className="stage-icon stage-icon-error">!</span>
  return <span className="stage-icon stage-icon-pending" />
}

function fmt(sec) {
  const t = Math.max(0, Math.floor(sec))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), r = t % 60
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(r).padStart(2, "0")
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export default function RunLog({ run, requestId, onStopped }) {
  const { log, status, error, clarification, started_at, finished_at, clock_offset = 0 } = run
  const [, tick] = useState(0)
  const [stopping, setStopping] = useState(false)

  // Re-render every second while running so the timer counts up live.
  useEffect(() => {
    if (status !== "running") return
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [status])

  if (!log.length) return null
  const { stages, current, doneLine } = groupLog(log)
  const failed = status === "error" && !clarification
  const stopped = status === "cancelled"

  // Start of the run until it stops (or now, while it is still going).
  const elapsed = started_at
    ? (finished_at ?? (Date.now() / 1000 + clock_offset)) - started_at
    : null

  async function stop() {
    setStopping(true)
    try {
      onStopped(await api.cancelRun(requestId))
    } catch (e) {
      setStopping(false)
    }
  }

  return (
    <section className="pane run-log">
      <h3 className="run-head">
        {status === "running" && <span className="spinner" />}
        <span>
          {status === "running" ? "Running..."
            : status === "done" ? "Done"
            : stopped ? "Stopped"
            : status === "error" ? (clarification ? "Needs more information" : "Failed")
            : ""}
        </span>
        {elapsed !== null && <span className="run-timer" title="Time since the run started">{fmt(elapsed)}</span>}
        {status === "running" && requestId && (
          <button className="stop-btn" onClick={stop} disabled={stopping}>
            {stopping ? "Stopping..." : "Stop"}
          </button>
        )}
      </h3>
      <ol className="stage-list">
        {stages.map((s, i) => {
          const state =
            (failed || stopped) && i === current ? "error"
            : i < current || doneLine || status === "done" ? "done"
            : i === current ? "active"
            : "pending"
          return (
            <li key={s.key} className={`stage stage-${state}`}>
              <div className="stage-head">
                <StageIcon state={state} />
                <span className="stage-label">{s.label}</span>
              </div>
              {s.items.length > 0 && (
                <ul className="stage-items">
                  {s.items.map((item, j) => <li key={j}>{item}</li>)}
                </ul>
              )}
            </li>
          )
        })}
      </ol>
      {/* A clarifying question is the agent choosing to ask rather than guess -- not a
          crash, so it isn't styled as one. The generic error line is suppressed here
          because it would just repeat the same paragraph again, in red. */}
      {clarification ? (
        <div className="badge warning" style={{ marginTop: 10 }}>
          <strong>The agent stopped to ask:</strong>
          <p style={{ margin: "6px 0 0" }}>{clarification}</p>
          <p className="muted small" style={{ marginTop: 8 }}>
            Add the missing detail to the request and run it again.
          </p>
        </div>
      ) : (
        error && <p className="error" style={{ marginTop: 10 }}>Error: {error}</p>
      )}
    </section>
  )
}
