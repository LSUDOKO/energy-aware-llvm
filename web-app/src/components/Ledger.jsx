import { useMemo, useState } from 'react'
import { breakEven, costsFrom, savingsPct } from '../lib/lifecycle'
import { energySourceLabel, joules, ms, pct, runsLabel } from '../lib/format'

const STAGES = [
  ['frontend', 'Front-end'],
  ['stage2', 'IR metrics'],
  ['search', 'Search'],
  ['passes', 'Passes'],
  ['codegen', 'Code generation'],
]
const MAX_EXP = 7 // slider covers 1 .. 10^7 executions

const runsAt = (exp) => Math.round(10 ** exp)

function Curve({ base, opt, exp }) {
  const W = 360, H = 120
  const pts = useMemo(() => {
    const xs = []
    for (let e = 0; e <= MAX_EXP; e += 0.1) xs.push([e, savingsPct(base, opt, 10 ** e)])
    return xs
  }, [base, opt])
  // clamp to +-100% so one huge loss at small run counts cannot flatten the curve
  const lo = -100, hi = 100
  const clamp = (v) => Math.max(lo, Math.min(hi, v))
  const x = (e) => (e / MAX_EXP) * W
  const y = (v) => H - ((clamp(v) - lo) / (hi - lo)) * H
  const d = pts.map(([e, v], i) => `${i ? 'L' : 'M'}${x(e).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const here = savingsPct(base, opt, 10 ** exp)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="curve" role="img"
         aria-label="EDP savings versus number of executions">
      <line x1="0" x2={W} y1={y(0)} y2={y(0)} className="curve-zero" />
      <path d={d} className="curve-line" />
      <line x1={x(exp)} x2={x(exp)} y1="0" y2={H} className="curve-cursor" />
      <circle cx={x(exp)} cy={y(here)} r="4.5" className={here >= 0 ? 'dot-gain' : 'dot-loss'} />
    </svg>
  )
}

export default function Ledger({ result, initialRuns }) {
  const costs = costsFrom(result)
  const [exp, setExp] = useState(Math.log10(initialRuns || 10000))
  if (!costs) {
    return <p className="muted">This build has no measured native runtime, so there is no lifecycle to chart.</p>
  }
  const { base, opt } = costs
  const n = runsAt(exp)
  const saving = savingsPct(base, opt, n)
  const be = breakEven(base, opt)
  const stageJ = result.energy.stage_j ?? {}
  const total = STAGES.reduce((s, [k]) => s + (stageJ[k] ?? 0), 0) || 1

  return (
    <div className="ledger">
      <section>
        <h3>What compiling cost</h3>
        <div className="stagebar" role="img" aria-label="compile energy by stage">
          {STAGES.map(([k, label]) => (stageJ[k] ?? 0) > 0 && (
            <span key={k} className={`seg seg-${k}`} style={{ flexGrow: stageJ[k] / total }}
                  title={`${label}: ${joules(stageJ[k])}`} />
          ))}
        </div>
        <ul className="stagekey">
          {STAGES.map(([k, label]) => (
            <li key={k}><i className={`seg-${k}`} />{label}<b>{joules(stageJ[k] ?? 0)}</b></li>
          ))}
        </ul>
        <p className="muted">
          {joules(opt.compileJ)} in {ms(opt.compileS)}, against {joules(base.compileJ)} for a
          build with no optional passes. Energy: {energySourceLabel(result.energy)}.
        </p>
      </section>

      <section>
        <h3>Is it worth it?</h3>
        <div className="runs-readout">
          <span className="runs-n">{n.toLocaleString('en-US')}</span>
          <span className="muted">times the program runs</span>
        </div>
        <input type="range" min="0" max={MAX_EXP} step="0.05" value={exp}
               aria-label="Number of executions (log scale)"
               onChange={(e) => setExp(Number(e.target.value))} />
        <Curve base={base} opt={opt} exp={exp} />
        <p className={`verdict ${saving >= 0 ? 'gain' : 'loss'}`}>
          Energy-delay product {saving >= 0 ? 'drops' : 'rises'} by {Math.abs(saving).toFixed(1)}%
          at this run count ({pct(saving)}).
        </p>
        <p className="muted">
          Each run takes {(opt.runS * 1e6).toFixed(2)} us instead of {(base.runS * 1e6).toFixed(2)} us
          and {joules(opt.runJ)} instead of {joules(base.runJ)}. The extra compile energy is repaid after{' '}
          <b>{runsLabel(be)}</b>.
        </p>
      </section>
    </div>
  )
}
