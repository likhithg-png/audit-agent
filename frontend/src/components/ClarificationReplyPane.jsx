import { useState } from "react"
import { api } from "../api"

// When the agent asks a clarifying question instead of guessing (ask_clarifying_question),
// there is no letter/decomposed result to show -- but reply_meta was captured at the START
// of the Gmail run, before decompose even runs, so there's already somewhere to send the
// question TO. Unlike the final evidence-pack letter (always a manual, reviewed send), the
// backend auto-sends a clarifying question the moment the agent asks one (_auto_reply_with_
// clarification in api/main.py) -- it asserts nothing about the account, it only asks for
// missing scope/period/severity-scale, so a human doesn't have to click through every one.
// `alreadySent` reflects that: this UI then just confirms what went out, and only offers a
// manual send as a fallback for the rare case where the auto-send itself failed.
export default function ClarificationReplyPane({ requestId, replyTo, question, alreadySent }) {
  const [draft, setDraft] = useState(question)
  const [editing, setEditing] = useState(false)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(alreadySent)
  const [error, setError] = useState("")

  if (!replyTo) return null

  async function send() {
    if (!window.confirm(`Send this clarifying question to ${replyTo}? This cannot be undone from here.`)) return
    setSending(true)
    setError("")
    try {
      await api.sendReply(requestId, draft, [])
      setSent(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="pane">
      <div className="pane-head">
        <div>
          <h2>{sent ? "Clarifying question sent" : "Send this question to the sender"}</h2>
          <p className="pane-sub">To <strong>{replyTo}</strong></p>
        </div>
        {!sent && (
          <button onClick={() => setEditing((v) => !v)} disabled={sending}>
            {editing ? "Preview" : "Edit text"}
          </button>
        )}
      </div>

      {sent ? (
        <p className="badge success">Sent to {replyTo} automatically -- no action needed.</p>
      ) : (
        <>
          <p className="muted small" style={{ marginBottom: 10 }}>
            This hasn't been sent yet -- either auto-send is turned off, or it couldn't go out
            automatically. Review it and send when ready.
          </p>
          {editing ? (
            <textarea className="paste-box" rows={10} value={draft} onChange={(e) => setDraft(e.target.value)} disabled={sending} />
          ) : (
            <p style={{ whiteSpace: "pre-wrap" }}>{draft}</p>
          )}
          <button className="primary" onClick={send} disabled={sending} style={{ marginTop: 12 }}>
            {sending ? "Sending..." : `Send to ${replyTo}`}
          </button>
          {error && <p className="error" style={{ marginTop: 12 }}>{error}</p>}
        </>
      )}
    </section>
  )
}
