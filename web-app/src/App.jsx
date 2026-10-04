import { useEffect, useState } from 'react'
import { compile, getBenchmark, listBenchmarks } from './api'
import BenchmarkView from './components/BenchmarkView'
import GateTable from './components/GateTable'
import IRView from './components/IRView'
import Ledger from './components/Ledger'
import { ms } from './lib/format'
import './App.css'

const MODES = [
  { id: '-Meco', name: 'Eco', note: 'strictest budget: only passes that repay their cost' },
  { id: '-Mbalanced', name: 'Balanced', note: 'XGBoost ranker trained on measured runs' },
  { id: '-Mperf', name: 'Performance', note: 'genetic search, scored by measured EDP' },
]
const TABS = ['Ledger', 'Gate', 'IR', 'Log', 'Benchmarks']
const STARTER = `int main() {
    int a = 10;
    int b = 20;
    int c = a + b;
    int d = c * 2;
    if (d > 50) {
        return 1;
    } else {
        return 0;
    }
}`

export default function App() {
  const [code, setCode] = useState(STARTER)
  const [mode, setMode] = useState('-Mbalanced')
  const [runs, setRuns] = useState(10000)
  const [kernels, setKernels] = useState([])
  const [kernel, setKernel] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [tab, setTab] = useState('Ledger')

  useEffect(() => {
    listBenchmarks().then(setKernels).catch(() => setKernels([]))
  }, [])

  async function pickKernel(name) {
    setKernel(name)
    if (!name) return
    const k = await getBenchmark(name)
    setCode(k.source)
    setResult(null)
  }

  async function run() {
    setBusy(true)
    try {
      setResult(await compile(code, mode, runs))
    } catch (e) {
      setResult({ success: false, error: `Cannot reach the compiler API (${e.message}). Start it with: python api.py` })
    } finally {
      setBusy(false)
    }
  }

  const ok = result?.success
  return (
    <div className="shell">
      <header className="top">
        <h1>Energy-aware compiler lab</h1>
        <p>Compile a small C program, then see what the optimizer spent and what it bought back.</p>
      </header>

      <main className="grid">
        <section className="pane left" aria-label="Source and options">
          <div className="row">
            <label htmlFor="kernel">Example program</label>
            <select id="kernel" value={kernel} onChange={(e) => pickKernel(e.target.value)}>
              <option value="">Custom code</option>
              {kernels.map((k) => (
                <option key={k.name} value={k.name}>{k.name} - {k.description}</option>
              ))}
            </select>
          </div>
          <textarea className="editor" value={code} spellCheck="false" aria-label="C source"
                    onChange={(e) => { setCode(e.target.value); setKernel('') }} />
          <fieldset className="modes">
            <legend>Optimization mode</legend>
            {MODES.map((m) => (
              <label key={m.id} className={mode === m.id ? 'on' : ''}>
                <input type="radio" name="mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
                <b>{m.name}</b>
                <span>{m.note}</span>
              </label>
            ))}
          </fieldset>
          <div className="row">
            <label htmlFor="runs">Assumed executions</label>
            <input id="runs" type="number" min="1" value={runs}
                   onChange={(e) => setRuns(Math.max(1, Number(e.target.value) || 1))} />
          </div>
          <button className="go" onClick={run} disabled={busy}>
            {busy ? 'Compiling...' : 'Compile and measure'}
          </button>
        </section>

        <section className="pane right" aria-label="Results">
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t} role="tab" aria-selected={tab === t}
                      className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>
            ))}
          </nav>

          {tab === 'Benchmarks' && <BenchmarkView />}

          {tab !== 'Benchmarks' && !result && !busy && (
            <p className="muted empty">Choose a program and compile it. Nothing is simulated: the API runs the pipeline, executes the result natively and times it.</p>
          )}
          {tab !== 'Benchmarks' && busy && <p className="muted empty">Running the pipeline and timing the generated code...</p>}

          {tab !== 'Benchmarks' && result && !busy && (
            <>
              <div className={`status ${ok ? 'good' : 'bad'}`} role="status">
                {ok
                  ? <>Compiled and verified. <b>main()</b> returned <b>{result.return_value}</b>, identical before and after optimization.</>
                  : <>{result.error ?? 'Compilation failed verification.'}</>}
              </div>
              {ok && (
                <dl className="facts">
                  <div><dt>Front-end</dt><dd>{ms(result.ast_time_ms / 1000)}</dd></div>
                  <div><dt>Search</dt><dd>{ms(result.ml_time_ms / 1000)}</dd></div>
                  <div><dt>Passes run</dt><dd>{result.selected_passes.length}</dd></div>
                  <div><dt>Native run</dt><dd>{result.runtime_us?.toFixed(2)} us</dd></div>
                  <div><dt>Object</dt><dd>{result.object_bytes} bytes</dd></div>
                </dl>
              )}
              {ok && tab === 'Ledger' && <Ledger key={result.llvm_ir + mode} result={result} initialRuns={runs} />}
              {ok && tab === 'Gate' && <GateTable gating={result.gating} />}
              {ok && tab === 'IR' && <IRView before={result.llvm_ir} after={result.optimized_ir} instructions={result.instructions} />}
              {tab === 'Log' && (
                <pre className="log" tabIndex={0}>{[...(result.logs ?? []), result.error].filter(Boolean).join('\n')}</pre>
              )}
            </>
          )}
        </section>
      </main>
    </div>
  )
}
