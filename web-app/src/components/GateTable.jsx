import { joules } from '../lib/format'

export default function GateTable({ gating }) {
  const rows = gating?.decisions ?? []
  if (!rows.length) return <p className="muted">No pass decisions were recorded.</p>
  const ran = rows.filter((r) => r.run).length
  return (
    <div>
      <p className="muted">
        Budget <b>{gating.budget}</b> (lambda {gating.lambda}, mu {gating.mu}): a pass runs only when its
        predicted benefit exceeds lambda x its energy cost + mu x its time cost. {ran} of {rows.length} ran.
      </p>
      <table className="gate">
        <thead>
          <tr><th>Pass</th><th>Decision</th><th>Benefit</th><th>Threshold</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className={r.run ? 'ran' : 'skipped'}>
              <td className="mono">{r.name}</td>
              <td>{r.run ? 'run' : 'skip'}</td>
              <td className="num">{joules(r.benefit)}</td>
              <td className="num">{joules(r.threshold)}</td>
              <td className="reason">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
