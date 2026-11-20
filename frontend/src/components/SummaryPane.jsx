// The columns come straight off summary.csv, so the headers were raw engine codes
// ("check_id", "CANNOT_DETERMINE", "NOT_APPLICABLE"). Anything not listed falls back
// to the raw name with underscores softened, so a new status can never render as a
// SHOUTING_SNAKE_CASE header.
const COL_LABEL = {
  check_id: "Check",
  check: "What was checked",
  EVIDENCED: "Evidenced",
  GAP: "Gap",
  CANNOT_DETERMINE: "Cannot determine",
  NOT_APPLICABLE: "Not applicable",
}
const label = (c) => COL_LABEL[c] || c.replace(/_/g, " ").toLowerCase()

export default function SummaryPane({ summary }) {
  if (!summary.length) return null
  // Show the readable check name instead of the raw check_type code.
  const cols = Object.keys(summary[0]).filter((c) => c !== "check_type")
  cols.sort((a, b) => (a === "check" ? 0.5 : cols.indexOf(a)) - (b === "check" ? 0.5 : cols.indexOf(b)))
  return (
    <section className="pane" id="r-summary">
      <div className="pane-head"><div><h2><span className="step">2</span>Run summary</h2><p className="pane-sub">Accounts per check and outcome.</p></div></div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>{cols.map((c) => <th key={c}>{label(c)}</th>)}</tr>
          </thead>
          <tbody>
            {summary.map((row, i) => (
              <tr key={i}>{cols.map((c) => <td key={c}>{row[c]}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
