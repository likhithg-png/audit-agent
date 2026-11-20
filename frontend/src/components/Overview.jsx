import { api } from "../api"

// Headline numbers for the run + the one download (the full gap report).
export default function Overview({ requestId, summary, checks }) {
  const sum = (k) => summary.reduce((a, r) => a + (Number(r[k]) || 0), 0)
  const tiles = [
    { label: "Checks run", value: checks.length },
    { label: "Gaps found", value: sum("GAP"), cls: sum("GAP") ? "t-gap" : "t-ok" },
    { label: "Cannot determine", value: sum("CANNOT_DETERMINE"), cls: sum("CANNOT_DETERMINE") ? "t-warn" : "" },
    { label: "Evidenced", value: sum("EVIDENCED"), cls: "t-ok" },
    { label: "Not applicable", value: sum("NOT_APPLICABLE") },
  ]
  return (
    <section className="pane" id="r-result">
      <div className="pane-head">
        <div>
          <h2>Result</h2>
          <p className="pane-sub">Counts are account-check results ({requestId}).</p>
        </div>
        <a className="button primary" href={api.downloadUrl(requestId, "gap_report")} download>
          Download gap report (.xlsx)
        </a>
      </div>
      <div className="tiles">
        {tiles.map((t) => (
          <div key={t.label} className={`tile ${t.cls || ""}`}>
            <span className="tile-value">{t.value}</span>
            <span className="tile-label">{t.label}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
