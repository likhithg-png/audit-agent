import { useEffect, useState } from "react"
import { api } from "../api"
import PeriodFields from "./PeriodFields"
import AccountsField from "./AccountsField"
import GuidelinePicker from "./GuidelinePicker"

export default function UploadPane({ running, onStarted, onError }) {
  const [src, setSrc] = useState("paste")     // "paste" | "file" -- only one is ever submitted
  const [file, setFile] = useState(null)
  const [text, setText] = useState("")
  const [accountsFile, setAccountsFile] = useState(null)
  const [periodFrom, setPeriodFrom] = useState("")
  const [periodTo, setPeriodTo] = useState("")
  const [guidelines, setGuidelines] = useState([])
  const [picked, setPicked] = useState([])
  const [skipAgent, setSkipAgent] = useState(false)

  useEffect(() => {
    api.listGuidelines().then(setGuidelines).catch((e) => onError(e.message))
  }, [])

  const hasText = text.trim().length > 0
  // Request source: a file OR pasted text, never both.
  const hasRequest = !!file || hasText
  // With no request text, the guidelines are the whole request, and the dates must be entered.
  const canRun = (hasRequest || picked.length > 0) && !!periodFrom === !!periodTo &&
    (hasRequest || (!!periodFrom && !!accountsFile))

  // Switching source clears the other one, so `hasRequest` can never mean two things at once.
  function pickSrc(next) {
    setSrc(next)
    if (next === "paste") setFile(null)
    else setText("")
  }

  async function submit() {
    const fd = new FormData()
    if (accountsFile) fd.set("accounts_file", accountsFile)
    if (periodFrom) fd.set("period_from", periodFrom)
    if (periodTo) fd.set("period_to", periodTo)
    if (picked.length) fd.set("guideline_ids", picked.join(","))
    if (file) fd.set("file", file)
    else fd.set("text", text)
    if (skipAgent && hasRequest) fd.set("use_agent", "false")
    try {
      const { request_id } = await api.startCustomRun(fd)
      onStarted(request_id)
    } catch (e) {
      onError(e.message)
    }
  }

  return (
    <section className="pane">
      <div className="pane-head">
        <div>
          <h2>New audit request</h2>
          <p className="pane-sub">Give the agent a request, then run it.</p>
        </div>
      </div>

      <div className="seg" role="tablist">
        <button role="tab" aria-selected={src === "paste"} className={src === "paste" ? "active" : ""}
                onClick={() => pickSrc("paste")} disabled={running}>Paste text</button>
        <button role="tab" aria-selected={src === "file"} className={src === "file" ? "active" : ""}
                onClick={() => pickSrc("file")} disabled={running}>Upload file</button>
      </div>

      {src === "paste" ? (
        <textarea
          className="paste-box"
          placeholder="Paste the audit request text here..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={running}
          rows={8}
        />
      ) : (
        <>
          <input
            type="file"
            key={file ? "f" : "nof"}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={running}
          />
          <p className="muted small">PDF, Word (.docx/.doc), Excel, email (.eml), HTML, CSV or any text file.</p>
          {file && (
            <div className="filename">
              <strong>{file.name}</strong>
              <button className="link" onClick={() => setFile(null)} disabled={running}>Remove</button>
            </div>
          )}
        </>
      )}

      <p className="muted small" style={{ marginTop: 10 }}>
        Accounts come from the account list you attach, or from the account ids named in the request itself
        (e.g. ACC100001).
      </p>

      <details className="disclosure">
        <summary>Accounts &amp; period{accountsFile || periodFrom ? " — set" : " — optional"}</summary>
        <div className="disclosure-body">
          <AccountsField file={accountsFile} setFile={setAccountsFile} disabled={running} />
          <PeriodFields
            periodFrom={periodFrom} setPeriodFrom={setPeriodFrom} periodTo={periodTo} setPeriodTo={setPeriodTo} />
        </div>
      </details>

      <details className="disclosure">
        <summary>Guidelines to check{picked.length ? ` — ${picked.length} selected` : " — none selected"}</summary>
        <div className="disclosure-body">
          <GuidelinePicker guidelines={guidelines} picked={picked} setPicked={setPicked} disabled={running} />
        </div>
      </details>

      {hasRequest && (
        <details className="disclosure">
          <summary>Advanced{skipAgent ? " — one-shot" : ""}</summary>
          <div className="disclosure-body">
            <label className="attach-opt">
              <input type="checkbox" checked={skipAgent} onChange={(e) => setSkipAgent(e.target.checked)} disabled={running} />
              <span>
                Skip investigation (faster, one-shot) -- by default the agent can look up the guideline catalogue,
                the lender's real policy, or check account ids before committing to checks; check this to extract
                from the text in one pass instead.
              </span>
            </label>
          </div>
        </details>
      )}

      <button className="primary block" style={{ marginTop: 14 }} onClick={submit} disabled={!canRun || running}>
        {running ? "Running..." : "Run the agent"}
      </button>
    </section>
  )
}
