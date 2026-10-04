import test from 'node:test'
import assert from 'node:assert/strict'
import { breakEven, costsFrom, edp, savingsPct } from './lifecycle.js'

const base = { compileJ: 1, compileS: 0.1, runJ: 0.01, runS: 0.001 }
const fast = { compileJ: 3, compileS: 0.3, runJ: 0.004, runS: 0.0004 }

test('edp multiplies total energy by total time', () => {
  assert.equal(edp(base, 100), (1 + 100 * 0.01) * (0.1 + 100 * 0.001))
})

test('break-even is the energy crossover', () => {
  const n = breakEven(base, fast)
  assert.ok(Math.abs(n - 2 / 0.006) < 1e-9)
})

test('never repaid when the run is not cheaper', () => {
  assert.equal(breakEven(base, { ...fast, runJ: 0.02 }), null)
})

test('zero when no costlier to build and run', () => {
  assert.equal(breakEven(base, { ...base, compileJ: 0.9, runJ: 0.009 }), 0)
})

test('savings sign flips as the run count grows', () => {
  assert.ok(savingsPct(base, fast, 1) < 0)
  assert.ok(savingsPct(base, fast, 1e6) > 50)
})

test('costsFrom needs a measured baseline and optimized run', () => {
  assert.equal(costsFrom({ energy: { e_run_j: null } }), null)
  const c = costsFrom({
    runtime_us: 20,
    baseline_runtime_us: 50,
    energy: { e_compile_j: 2, e_compile_baseline_j: 1, compile_time_s: 0.2,
      compile_time_baseline_s: 0.1, e_run_j: 0.002, e_run_baseline_j: 0.005 },
  })
  assert.ok(Math.abs(c.opt.runS - 20e-6) < 1e-15)
  assert.equal(c.base.compileJ, 1)
})
