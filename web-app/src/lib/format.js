export const ms = (s) => `${(s * 1e3).toFixed(s * 1e3 < 10 ? 2 : 1)} ms`

export function joules(j) {
  if (j == null) return 'n/a'
  const a = Math.abs(j)
  if (a >= 1) return `${j.toFixed(2)} J`
  if (a >= 1e-3) return `${(j * 1e3).toFixed(2)} mJ`
  return `${(j * 1e6).toFixed(2)} uJ`
}

export const runsLabel = (n) => {
  if (n == null) return 'never'
  if (n === 0) return 'immediately'
  return `${Math.round(n).toLocaleString('en-US')} runs`
}

export const pct = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

export const energySourceLabel = (energy) =>
  energy?.measured ? 'RAPL package joules' : 'estimated: power x time (RAPL unreadable)'
