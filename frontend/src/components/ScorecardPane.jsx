import LetterView from "./LetterView"

// Scorecard (only when ground truth exists for this request) + the drafted letter.
// The letter preview is skipped when there's a real reply_to -- ReplyPane ("Send
// reply") already shows that same text, editable, right below. Showing it twice
// (once read-only here, once editable there) was pure duplication for every
// Gmail-inbox run, which is the only path that ever has a reply_to.
export default function ScorecardPane({ score, letter, replyTo }) {
  return (
    <>
      {score && !score.error && (
        <section className="pane">
          <h2>Accuracy scorecard</h2>
          <div className="tiles">
            <div className="tile"><span className="tile-value">{(score.recall * 100).toFixed(1)}%</span><span className="tile-label">Gap recall (target ≥ 95%)</span></div>
            <div className="tile"><span className="tile-value">{(score.false_gap_rate * 100).toFixed(1)}%</span><span className="tile-label">False gap rate (target &lt; 5%)</span></div>
            <div className="tile"><span className="tile-value">{score.unsupported_claims}</span><span className="tile-label">Unevidenced claims (target 0)</span></div>
          </div>
        </section>
      )}
      {!replyTo && (
        <section className="pane" id="r-letter">
          <div className="pane-head">
            <div>
              <h2><span className="step">4</span>Draft response letter</h2>
              <p className="pane-sub">This is how the results table will look in the email.</p>
            </div>
          </div>
          {letter.needs_human_review ? (
            <p className="badge warning">Needs human review: {letter.validation.reasons.join("; ")}</p>
          ) : (
            <p className="badge success">Validator passed — every claim cites the summary, no softening language, DRAFT disclaimer present.</p>
          )}
          <LetterView text={letter.text} />
        </section>
      )}
    </>
  )
}
