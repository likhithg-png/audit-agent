// The agent deliberately refuses to resolve relative wording ("last month") found
// INSIDE a request's text -- the audited window would then depend on when the run
// happened, which is wrong for a reproducible compliance record (see decompose.py).
// These quick-pick chips give the human the same shortcut safely: clicking one
// computes real ISO dates right now and fills the explicit fields, so "last month"
// still means something concrete and fixed, just chosen by a person instead of guessed
// by the model.
function iso(d) {
  // toISOString() converts to UTC first -- in any timezone ahead of UTC (e.g. IST,
  // UTC+5:30) that silently shifts local midnight back onto the previous day.
  // Format from the local getters instead so the date picker shows the day the
  // user actually meant.
  const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, "0"), day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function monthRange(year, monthIndex) {        // monthIndex: 0 = Jan
  return [iso(new Date(year, monthIndex, 1)), iso(new Date(year, monthIndex + 1, 0))]
}

function PRESETS() {
  const now = new Date()
  const y = now.getFullYear(), m = now.getMonth()
  const todayStr = iso(now)
  const thisMonth = monthRange(y, m)
  const lastMonthDate = new Date(y, m - 1, 1)
  const lastMonth = monthRange(lastMonthDate.getFullYear(), lastMonthDate.getMonth())
  const q = Math.floor(m / 3)
  const thisQuarter = [monthRange(y, q * 3)[0], monthRange(y, q * 3 + 2)[1]]
  const lastQStart = new Date(y, q * 3 - 3, 1)
  const lq = Math.floor(lastQStart.getMonth() / 3)
  const lastQuarter = [monthRange(lastQStart.getFullYear(), lq * 3)[0], monthRange(lastQStart.getFullYear(), lq * 3 + 2)[1]]
  return [
    { label: "Today", range: [todayStr, todayStr] },
    { label: "This month", range: thisMonth },
    { label: "Last month", range: lastMonth },
    { label: "This quarter", range: thisQuarter },
    { label: "Last quarter", range: lastQuarter },
  ]
}

export default function PeriodFields({ periodFrom, setPeriodFrom, periodTo, setPeriodTo }) {
  function apply([from, to]) {
    setPeriodFrom(from)
    setPeriodTo(to)
  }
  const active = (range) => periodFrom === range[0] && periodTo === range[1]

  return (
    <div className="row">
      <div className="filter-chips" style={{ flexBasis: "100%", marginBottom: 2 }}>
        {PRESETS().map((p) => (
          <button key={p.label} type="button" className={`filter-chip ${active(p.range) ? "active" : ""}`}
                  onClick={() => apply(p.range)}>
            {p.label}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Period from (optional)</span>
        <input type="date" value={periodFrom} onChange={(e) => setPeriodFrom(e.target.value)} />
      </label>
      <label className="field">
        <span>Period to (optional)</span>
        <input type="date" value={periodTo} onChange={(e) => setPeriodTo(e.target.value)} />
      </label>
      <p className="muted small" style={{ flexBasis: "100%", margin: 0 }}>
        Leave the dates blank and the agent reads the audit period from the request. Enter them (or pick a shortcut
        above) to override it.
      </p>
    </div>
  )
}
