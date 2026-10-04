import { useEffect, useState } from 'react'
import { chartUrl, getBenchmarkResults } from '../api'

const BUILDS = ['-Meco', '-Mbalanced', '-Mperf', 'clang-O0', 'clang-O2']
const CHART_TITLES = {
  'runtime_speedup.png': 'Native speedup of each object file',
  'edp_vs_runs.png': 'Lifecycle EDP versus run count',
  'break_even.png': 'Executions needed to repay compile energy',
  'compile_time.png': 'Compile time per build',
  'perfmode_ga.png': '-Mperf search convergence',
  'compile_stages.png': 'Compile time by stage',
}

function geomean(xs) {
  const v = xs.filter((x) => x > 0)
  return Math.exp(v.reduce((s, x) => s + Math.log(x), 0) / v.length)
}

export default function BenchmarkView() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    getBenchmarkResults().then(setData).catch((e) => setError(e.message))
  }, [])

  if (error) return <p className="muted">Could not load results: {error}. Run <code>benchmarks/run_benchmarks.py</code> and reload.</p>
  if (!data) return <p className="muted">Loading measured results...</p>
  if (!data.available) return <p className="muted">No benchmark run found. Run <code>benchmarks/run_benchmarks.py</code>.</p>

  const find = (k, c) => k.builds.find((b) => b.config === c)
  const allCorrect = data.kernels.every((k) => k.builds.every((b) => b.correct))
  return (
    <div className="bench">
      <p className="muted">
        {data.kernels.length} kernels on {data.machine.cpu}, {data.settings.reps} interleaved native
        trials each. Every build returned the expected value: <b>{allCorrect ? 'yes' : 'NO'}</b>.
        Energy source: <b>{data.machine.energy_source === 'rapl' ? 'RAPL joules' : 'time only (estimates)'}</b>.
      </p>
      <table className="gate">
        <thead>
          <tr><th>Build</th><th>Geomean speedup</th><th>Repays its compile cost</th></tr>
        </thead>
        <tbody>
          {BUILDS.map((c) => {
            const bs = data.kernels.map((k) => find(k, c))
            const pays = bs.filter((b) => b.break_even_runs != null && b.break_even_runs <= data.settings.n_runs).length
            return (
              <tr key={c}>
                <td className="mono">{c}</td>
                <td className="num">{geomean(bs.map((b) => b.speedup_vs_unopt)).toFixed(2)}x</td>
                <td className="num">{pays} of {bs.length} within {data.settings.n_runs.toLocaleString('en-US')} runs</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="charts">
        {data.charts.map((f) => (
          <figure key={f}>
            <img src={chartUrl(f)} alt={CHART_TITLES[f] ?? f} loading="lazy" />
            <figcaption>{CHART_TITLES[f] ?? f}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  )
}
