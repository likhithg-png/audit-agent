import { useEffect, useMemo, useState } from "react"
import { api } from "../api"

const VIEWS = [
  { key: "GAP", label: "Gaps" },
  { key: "CANNOT_DETERMINE", label: "Cannot determine" },
  { key: "NOT_APPLICABLE", label: "Not applicable" },
  { key: "EVIDENCED", label: "Evidenced" },
  { key: "", label: "All" },
]

const STATUS_LABEL = {
  GAP: "Gap",
  CANNOT_DETERMINE: "Cannot determine",
  NOT_APPLICABLE: "Not applicable",
  EVIDENCED: "Evidenced",
}

export default function GapsPane({ requestId }) {
  const [view, setView] = useState("GAP")
  const [all, setAll] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState("")
  const [channel, setChannel] = useState("")
  const [severity, setSeverity] = useState("")
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    setErr("")
    setSelected(null)
    api.getAccountResults(requestId)
      .then((r) => live && setAll(r))
      .catch((e) => live && setErr(e.message))
      .finally(() => live && setLoading(false))
    return () => { live = false }
  }, [requestId])

  useEffect(() => { setSelected(null) }, [view])

  const channels = useMemo(() => [...new Set(all.map((r) => r.channel_label).filter(Boolean))], [all])
  const severities = useMemo(() => [...new Set(all.map((r) => r.severity).filter(Boolean))].sort(), [all])
  const filtered = all.filter(
    (r) => (!view || r.status === view) && (!channel || r.channel_label === channel) &&
           (!severity || r.severity === severity)
  )
  const count = (k) => all.filter((r) => !k || r.status === k).length

  return (
    <section className="pane" id="r-gaps">
      <div className="pane-head"><div><h2><span className="step">3</span>Results by account</h2><p className="pane-sub">One row per account and check. Click a row for the records behind it.</p></div></div>
      <div className="row">
        <select value={view} onChange={(e) => setView(e.target.value)}>
          {VIEWS.map((v) => <option key={v.key} value={v.key}>{v.label} ({count(v.key)})</option>)}
        </select>
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option>
          {channels.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {severities.length > 0 && (
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option>
            {severities.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <span className="muted">{loading ? "Loading..." : `${filtered.length} of ${all.length} account-check results`}</span>
      </div>
      {err && <p className="error">{err}</p>}

      <div className="gap-table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>account_id</th><th>check</th><th>channel</th><th>result</th><th>what we found</th><th>severity</th></tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={`${r.account_id}-${r.check_id}`}
                  className={selected === r ? "selected" : ""} onClick={() => setSelected(r)}>
                <td>{r.account_id}</td>
                <td>{r.check_name}</td>
                <td>{r.channel_label || "-"}</td>
                <td><span className={`status status-${r.status.toLowerCase()}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                <td>{r.headline}</td>
                <td>{r.severity && <span className={`sev sev-${r.severity.toLowerCase()}`}>{r.severity}</span>}</td>
              </tr>
            ))}
            {!loading && !filtered.length && (
              <tr><td colSpan={6} className="muted">No results in this view.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h3>Details</h3>
      {selected ? <Detail r={selected} /> : <p className="muted">Click a row above to see exactly what was checked and why.</p>}
    </section>
  )
}

function Detail({ r }) {
  return (
    <div className="evidence">
      <p><strong>{r.account_id}</strong> · {r.check_name}{r.channel_label ? ` · ${r.channel_label}` : ""} · <span className={`status status-${r.status.toLowerCase()}`}>{STATUS_LABEL[r.status] || r.status}</span>
        {r.severity && <> · <span className={`sev sev-${r.severity.toLowerCase()}`}>{r.severity}</span></>}</p>
      <p>{r.summary}</p>
      {r.test && <p className="muted small"><em>Check:</em> {r.test}. <em>Period:</em> {r.period}.
        {r.rule_refs && <> <em>Policy:</em> {r.rule_refs}.</>}</p>}

      {r.consent?.length > 0 && (
        <>
          <h4>Consent records ({r.channel_label})</h4>
          <table className="data-table">
            <thead><tr><th>consent_id</th><th>status</th><th>source</th><th>captured</th><th>opted out</th></tr></thead>
            <tbody>
              {r.consent.map((c) => (
                <tr key={c.consent_id}><td>{c.consent_id}</td><td>{c.status}</td><td>{c.source}</td><td>{c.captured}</td><td>{c.opted_out}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {r.items?.length > 0 && (() => {
        const consentView = r.check_type === "CONSENT_BEFORE_CONTACT"
        const showTime = r.items.some((i) => i.time)
        return (
          <>
            <h4>{consentView ? `${r.channel_label} contacts in the period` : "Records tested"} ({r.items.length})</h4>
            <table className="data-table">
              <thead><tr>
                <th>record_id</th>{showTime && <th>time</th>}
                <th>{consentView ? "outcome" : "result"}</th><th>{consentView ? "consent result" : "detail"}</th>
              </tr></thead>
              <tbody>
                {r.items.map((i) => (
                  <tr key={i.contact_id}><td>{i.contact_id}</td>{showTime && <td>{i.time}</td>}
                    <td>{i.outcome}</td><td>{i.result}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )
      })()}
    </div>
  )
}
