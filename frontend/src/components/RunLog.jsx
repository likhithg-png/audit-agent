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

export default function RunLog({ log, status, error, clarification }) {
  if (!log.length) return null
  const { stages, current, doneLine } = groupLog(log)
  const failed = status === "error" && !clarification

  return (
    <section className="pane run-log">
      <h3>
        {status === "running" && <span className="spinner" />}
        {status === "running" ? "Running..."
          : status === "done" ? "Done"
          : status === "error" ? (clarification ? "Needs more information" : "Failed")
          : ""}
      </h3>
      <ol className="stage-list">
        {stages.map((s, i) => {
          const state =
            failed && i === current ? "error"
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
