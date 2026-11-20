// Plain-English headline per tool call. The raw args (a JSON blob, often containing
// SQL) used to be stringified straight into this line -- the summary is what a
// compliance reviewer skims, so it stays prose. The query itself is still shown,
// as formatted SQL, once the row is expanded: hiding it entirely would defeat the
// point of keeping an investigation trail.
function describe(t) {
  const a = t.args || {}
  switch (t.tool) {
    case "list_guidelines":
      return "Looked up the guideline catalogue"
    case "lookup_policy":
      return `Looked up ${a.lender_id || "the lender"}'s policy rules`
    case "query_accounts": {
      const ids = a.account_ids || []
      if (!ids.length) return "Checked the account ids named in the request"
      if (ids.length === 1) return `Checked account ${ids[0]} exists`
      const shown = ids.slice(0, 3).join(", ") + (ids.length > 3 ? ` +${ids.length - 3} more` : "")
      return `Checked ${ids.length} account ids exist: ${shown}`
    }
    case "run_sql":
      return "Queried the audit data directly"
    case "ask_clarifying_question":
      return "Asked for clarification"
    case "_fell_back_to_one_shot":
      return "Investigation ran out of rounds — used the one-shot extractor instead"
    default:
      return t.tool
  }
}

export default function ChecksPane({ decomposed }) {
  const { checks, unsupported, agent_trace: trace } = decomposed
  return (
    <section className="pane" id="r-checks">
      <div className="pane-head"><div><h2><span className="step">1</span>Checks extracted</h2><p className="pane-sub">What the agent understood the request to be asking for.</p></div></div>

      {trace && trace.length > 0 && (
        <div className="callout" style={{ marginBottom: 14 }}>
          <h3 style={{ marginTop: 0 }}>Agent investigated before deciding ({trace.length} lookup{trace.length === 1 ? "" : "s"})</h3>
          {trace.map((t, i) => (
            <details key={i} style={{ marginBottom: 6 }}>
              <summary>{describe(t)}</summary>
              {t.tool === "run_sql" && t.args?.query && (
                <>
                  <h4 style={{ margin: "8px 0 4px" }}>Query it ran</h4>
                  <pre className="evidence sql" style={{ maxHeight: 140 }}>{t.args.query}</pre>
                </>
              )}
              {t.tool === "ask_clarifying_question" && t.args?.question && (
                <p className="quote" style={{ marginTop: 6 }}>{t.args.question}</p>
              )}
              {t.result_preview && (
                <>
                  <h4 style={{ margin: "8px 0 4px" }}>What came back</h4>
                  <pre className="evidence" style={{ maxHeight: 160 }}>{t.result_preview}</pre>
                </>
              )}
            </details>
          ))}
        </div>
      )}

      <div className="two-col">
        <div>
          <h3>Checks ({checks.length})</h3>
          {checks.map((c) => (
            <div className="check-card" key={c.check_id}>
              <strong>{c.check_id} -- {c.check_label || c.check_type}</strong>
              <p className="quote">&ldquo;{c.source_text}&rdquo;</p>
            </div>
          ))}
        </div>
        <div>
          <h3>Unsupported -- reported, not dropped ({unsupported.length})</h3>
          {unsupported.length === 0 ? (
            <p className="badge success">Every requirement in the request mapped to a known check.</p>
          ) : (
            unsupported.map((c, i) => (
              <p className="badge warning" key={i}>&ldquo;{c.source_text}&rdquo;</p>
            ))
          )}
        </div>
      </div>
    </section>
  )
}
