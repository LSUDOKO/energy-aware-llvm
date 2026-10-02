import React, { useState } from 'react';
import { Code2, Zap, Play, Settings2, Activity, CheckCircle2, Box, Cpu } from 'lucide-react';
import './App.css';

function App() {
  const [code, setCode] = useState(`int main() {
    int a = 10;
    int b = 20;
    int c = a + b;
    int d = c * 2;
    if (d > 50) {
        return 1;
    } else {
        return 0;
    }
}`);
  
  const [mode, setMode] = useState('-Mbalanced');
  const [isCompiling, setIsCompiling] = useState(false);
  const [results, setResults] = useState(null);

  const handleCompile = async () => {
    setIsCompiling(true);
    try {
      const response = await fetch('http://localhost:5000/compile', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source_code: code,
          mode: mode
        }),
      });
      
      const data = await response.json();
      setResults(data);
    } catch (error) {
      console.error("Compilation error:", error);
      setResults({ success: false, error: "Failed to connect to backend compiler." });
    } finally {
      setIsCompiling(false);
    }
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>Energy-Aware Compiler</h1>
        <p>Integrated Semantic Analysis, IR Generation, and ML Optimization</p>
      </header>

      <div className="main-grid">
        {/* Editor Panel */}
        <div className="panel">
          <div className="panel-header">
            <h2><Code2 size={20} /> C Source Code</h2>
          </div>
          
          <div className="code-editor-container">
            <textarea
              className="code-textarea"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              spellCheck="false"
            />
          </div>
          
          <div className="controls">
            <div className="mode-selector">
              <button 
                className={`mode-btn ${mode === '-Meco' ? 'active' : ''}`}
                onClick={() => setMode('-Meco')}
              >
                <Zap size={20} />
                Eco Mode
                <span>Minimal Overhead</span>
              </button>
              <button 
                className={`mode-btn ${mode === '-Mbalanced' ? 'active' : ''}`}
                onClick={() => setMode('-Mbalanced')}
              >
                <Activity size={20} />
                Balanced
                <span>ML Pass Ranker</span>
              </button>
              <button 
                className={`mode-btn ${mode === '-Mperf' ? 'active' : ''}`}
                onClick={() => setMode('-Mperf')}
              >
                <Cpu size={20} />
                Performance
                <span>Max Output Speed</span>
              </button>
            </div>
            
            <button 
              className="compile-btn" 
              onClick={handleCompile}
              disabled={isCompiling}
            >
              {isCompiling ? (
                <><RefreshCw className="spinner" size={20} /> Compiling...</>
              ) : (
                <><Play size={20} /> Compile & Optimize</>
              )}
            </button>
          </div>
        </div>

        {/* Results Panel */}
        <div className="panel">
          <div className="panel-header">
            <h2><Settings2 size={20} /> Compilation Results</h2>
          </div>
          
          <div className="results-panel">
            {!results && !isCompiling && (
              <div className="empty-state">
                <Box size={48} />
                <p>Click "Compile & Optimize" to see results</p>
              </div>
            )}

            {isCompiling && (
              <div className="empty-state">
                <div className="spinner">
                  <Activity size={48} color="var(--primary)" />
                </div>
                <p style={{marginTop: '1rem'}}>Running compiler pipeline...</p>
              </div>
            )}

            {results && !isCompiling && (
              <>
                <div className="metrics-grid">
                  <div className="metric-card">
                    <span className="metric-label">AST + IR Emit Time</span>
                    <span className="metric-value">{results.ast_time_ms ? results.ast_time_ms.toFixed(2) : '0'} ms</span>
                  </div>
                  <div className="metric-card">
                    <span className="metric-label">ML Pass Ranker Time</span>
                    <span className="metric-value">{results.ml_time_ms ? results.ml_time_ms.toFixed(2) : '0'} ms</span>
                  </div>
                  <div className="metric-card">
                    <span className="metric-label">Predicted EDP Benefit</span>
                    <span className="metric-value highlight">{results.predicted_edp ? results.predicted_edp.toFixed(2) : '0'}x</span>
                  </div>
                  <div className="metric-card">
                    <span className="metric-label">Status</span>
                    <span className="metric-value" style={{color: results.success ? 'var(--accent-green)' : 'var(--danger)', display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '1.2rem'}}>
                      {results.success ? <CheckCircle2 size={20} /> : <Zap size={20} />}
                      {results.success ? 'Success' : 'Failed'}
                    </span>
                  </div>
                </div>

                {results.selected_passes && (
                  <div>
                    <h3 className="section-title">Selected Gated Passes</h3>
                    <div className="passes-container">
                      {results.selected_passes.map((pass, i) => (
                        <span key={i} className="pass-badge">{pass}</span>
                      ))}
                    </div>
                  </div>
                )}

                <div>
                  <h3 className="section-title">Compiler Logs</h3>
                  <div className="terminal-box">
                    {results.logs && results.logs.map((log, i) => (
                      <div key={i} className="terminal-line">{log}</div>
                    ))}
                    {results.error && (
                      <div className="terminal-line" style={{color: '#BF616A'}}>{results.error}</div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Missing icon import hack
import { RefreshCw } from 'lucide-react';

export default App;
