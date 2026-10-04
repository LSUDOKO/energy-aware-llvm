// Compile once, run n times (mirrors energy/lifecycle.py).
//   E(n) = Ec + n*Er      T(n) = Tc + n*Tr      EDP(n) = E(n) * T(n)

export const edp = (cost, n) =>
  (cost.compileJ + n * cost.runJ) * (cost.compileS + n * cost.runS)

export const savingsPct = (base, opt, n) => {
  const b = edp(base, n)
  return b > 0 ? ((b - edp(opt, n)) / b) * 100 : 0
}

// Smallest n from which opt never uses more energy than base; null if never.
export function breakEven(base, opt) {
  const extra = opt.compileJ - base.compileJ
  const savedPerRun = base.runJ - opt.runJ
  if (savedPerRun < 0 || (savedPerRun === 0 && extra > 0)) return null
  if (extra <= 0) return 0
  return extra / savedPerRun
}

// Costs for the baseline and optimized build from a /compile response.
export function costsFrom(result) {
  const e = result?.energy
  if (!e || e.e_run_j == null || !result.runtime_us || !result.baseline_runtime_us) {
    return null
  }
  return {
    base: {
      compileJ: e.e_compile_baseline_j,
      compileS: e.compile_time_baseline_s,
      runJ: e.e_run_baseline_j,
      runS: result.baseline_runtime_us * 1e-6,
    },
    opt: {
      compileJ: e.e_compile_j,
      compileS: e.compile_time_s,
      runJ: e.e_run_j,
      runS: result.runtime_us * 1e-6,
    },
  }
}
