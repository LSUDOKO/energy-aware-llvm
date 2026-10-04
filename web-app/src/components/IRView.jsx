export default function IRView({ before, after, instructions }) {
  if (!before) return <p className="muted">No IR was produced.</p>
  return (
    <div className="irview">
      {instructions && (
        <p className="muted">
          {instructions.before} instructions before the pass pipeline, {instructions.after} after
          {instructions.before > 0 && instructions.after < instructions.before
            ? ` (${(((instructions.before - instructions.after) / instructions.before) * 100).toFixed(0)}% fewer)`
            : ''}.
        </p>
      )}
      <div className="ir-cols">
        <figure>
          <figcaption>Front-end output</figcaption>
          <pre tabIndex={0}>{before}</pre>
        </figure>
        <figure>
          <figcaption>After scheduled passes</figcaption>
          <pre tabIndex={0}>{after}</pre>
        </figure>
      </div>
    </div>
  )
}
