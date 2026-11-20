import { useState } from "react"
import { api } from "../api"
import LetterView from "./LetterView"

// Manual-only, always: nothing in this app sends the letter on its own. A human
// reviews it (as it will look in the email), can edit the text, then clicks Send
// and confirms -- with an extra tick if the validator flagged needs_human_review.
const ATTACHMENTS = [{ key: "gap_report", label: "Full gap report (gap_report.xlsx)" }]

export default function ReplyPane({ requestId, letter, replyTo, replied: repliedProp }) {
  const [draft, setDraft] = useState(letter.text)
  const [editing, setEditing] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(repliedProp)
  const [error, setError] = useState("")
  const [attach, setAttach] = useState(["gap_report"])

  if (!replyTo) return null

  const needsExtraConfirm = letter.needs_human_review
  const canSend = !sending && !sent && (!needsExtraConfirm || reviewed)
  const toggleAttach = (k) => setAttach((a) => (a.includes(k) ? a.filter((x) => x !== k) : [...a, k]))

  async function send() {
    const what = attach.length ? ` with ${attach.length} attachment(s)` : " with no attachments"
    if (!window.confirm(`Send this reply to ${replyTo}${what}? This cannot be undone from here.`)) return
    setSending(true)
    setError("")
    try {
      await api.sendReply(requestId, draft, attach)
      setSent(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="pane" id="r-letter">
      <div className="pane-head">
        <div>
          <h2><span className="step">4</span>Send reply</h2>
          <p className="pane-sub">To <strong>{replyTo}</strong> — a manual, one-time action.</p>
        </div>
        {!sent && (
          <button onClick={() => setEditing((v) => !v)} disabled={sending}>
            {editing ? "Preview" : "Edit text"}
          </button>
        )}
      </div>

      {sent ? (
        <p className="badge success">Reply sent to {replyTo}.</p>
      ) : (
        <>
          {letter.needs_human_review ? (
            <p className="badge warning">Needs human review: {letter.validation.reasons.join("; ")}</p>
          ) : (
            <p className="badge success">Validator passed — every claim cites the summary, no softening language, DRAFT disclaimer present.</p>
          )}
          {editing ? (
            <textarea className="paste-box" rows={16} value={draft} onChange={(e) => setDraft(e.target.value)} disabled={sending} />
          ) : (
            <LetterView text={draft} />
          )}

          <div className="field" style={{ marginTop: 16 }}>
            <span>Attach to the reply</span>
            {ATTACHMENTS.map((a) => (
              <label key={a.key} className="attach-opt">
                <input type="checkbox" checked={attach.includes(a.key)} onChange={() => toggleAttach(a.key)} disabled={sending} />
                <span>{a.label}</span>
              </label>
            ))}
          </div>

          {needsExtraConfirm && (
            <label className="attach-opt" style={{ marginBottom: 12 }}>
              <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
              <span>I've reviewed this draft (flagged for human review) and confirm it's accurate before sending.</span>
            </label>
          )}
          <button className="primary" onClick={send} disabled={!canSend}>
            {sending ? "Sending..." : `Send reply to ${replyTo}`}
          </button>
          {error && <p className="error" style={{ marginTop: 12 }}>{error}</p>}
        </>
      )}
    </section>
  )
}
