// Thin client for the Flask API (api.py). All numbers shown in the UI come
// from these responses; nothing is computed client-side except the lifecycle
// curve in lib/lifecycle.js, which mirrors energy/lifecycle.py.
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:5000'

async function request(path, options) {
  const response = await fetch(`${BASE}${path}`, options)
  let body = null
  try {
    body = await response.json()
  } catch {
    // non-JSON error page: fall through to the status text below
  }
  if (!response.ok && !(body && 'success' in body)) {
    throw new Error(body?.error ?? `${response.status} ${response.statusText}`)
  }
  return body
}

export const compile = (source_code, mode, n_runs) =>
  request('/compile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_code, mode, n_runs }),
  })

export const listBenchmarks = () => request('/benchmarks')
export const getBenchmark = (name) => request(`/benchmarks/${name}`)
export const getBenchmarkResults = () => request('/benchmark-results')
export const chartUrl = (file) => `${BASE}/reports/benchmarks/${file}`
