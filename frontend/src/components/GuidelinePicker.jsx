const SOURCE_LABEL = {
  RBI_VERIFIED: "RBI text",
  LENDER_POLICY: "Lender policy",
  UNVERIFIED: "Source not confirmed",
}

export default function GuidelinePicker({ guidelines, picked, setPicked, disabled }) {
  const cats = [...new Set(guidelines.map((g) => g.category))]
  const toggle = (id) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id])
  const setCat = (cat, on) => {
    const ids = guidelines.filter((g) => g.category === cat && g.testable === "Y").map((g) => g.guideline_id)
    setPicked(on ? [...new Set([...picked, ...ids])] : picked.filter((x) => !ids.includes(x)))
  }

  return (
    <div>
      {/* The "Guidelines to check (N selected)" heading lives on the parent <details> summary now. */}
      <p className="muted small" style={{ marginTop: 0 }}>
        Added to whatever the request itself asks for. A guideline the request already covers (same check and
        channel) is not run twice. With no request at all, these guidelines are the request.
      </p>
      {cats.map((cat) => {
        const items = guidelines.filter((g) => g.category === cat)
        const ok = items.filter((g) => g.testable === "Y")
        const all = ok.length > 0 && ok.every((g) => picked.includes(g.guideline_id))
        const n = items.filter((g) => picked.includes(g.guideline_id)).length
        return (
          <details key={cat} className="guide-group">
            <summary>
              <strong>{cat}</strong>{" "}
              <span className="muted" style={{ fontSize: "0.76rem" }}>
                {n ? `${n} selected` : `${ok.length} available`}
              </span>
              {ok.length > 0 && (
                <label style={{ marginLeft: 10 }} onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={all} disabled={disabled}
                         onChange={(e) => setCat(cat, e.target.checked)} /> all
                </label>
              )}
            </summary>
            {items.map((g) => (
              <label key={g.guideline_id} className="guide-item" style={{ opacity: g.testable === "Y" ? 1 : 0.55 }}>
                <input type="checkbox" disabled={disabled || g.testable !== "Y"}
                       checked={picked.includes(g.guideline_id)} onChange={() => toggle(g.guideline_id)} />
                <span>
                  {g.title}{" "}
                  <span className="muted" style={{ fontSize: "0.76rem" }}>
                    [{SOURCE_LABEL[g.source_type] || g.source_type}] {g.source_ref}
                    {g.testable !== "Y" && <> — not testable yet: needs {g.needs}</>}
                  </span>
                </span>
              </label>
            ))}
          </details>
        )
      })}
    </div>
  )
}
