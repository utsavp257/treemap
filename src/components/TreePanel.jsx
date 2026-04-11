// src/components/TreePanel.jsx
import React from 'react'
import { useForestStore } from '../store/forestStore'

const RISK_LABEL = {
  healthy: 'Healthy',
  monitor: 'Monitor',
  treat: 'Needs treatment',
  cut: 'Cut down'
}
const RISK_BG = {
  healthy: '#EAF3DE',
  monitor: '#FAEEDA',
  treat: '#FAECE7',
  cut: '#FCEBEB'
}
const RISK_TEXT = {
  healthy: '#27500A',
  monitor: '#633806',
  treat: '#712B13',
  cut: '#501313'
}

export function TreePanel() {
  // ── ALL hooks must be at the top, before any return ──
  const trees        = useForestStore(s => s.trees)
  const selectedId   = useForestStore(s => s.selectedTreeId)
  const selectTree   = useForestStore(s => s.selectTree)
  const updateTreeEnv = useForestStore(s => s.updateTreeEnv)

  const tree = trees.find(t => t.id === selectedId)

  React.useEffect(() => {
    if (!tree) return

    console.log('[Diagnosis] Running for tree:', tree.id, 'status:', tree.status)

    const runDiagnosis = async () => {
      try {
        const res = await fetch('/diagnose', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            image_base64: 'none',
            health_score: tree.healthScore ?? 75,
            ndvi: tree.ndvi ?? 0.5,
            status: tree.status ?? 'healthy',
            visual_symptoms: tree.visualSymptoms ?? [],
          })
        })
        if (!res.ok) {
          console.warn('[Diagnosis] Bad response:', res.status)
          return
        }
        const diagnosis = await res.json()
        console.log('[Diagnosis] Got result:', diagnosis)
        updateTreeEnv(tree.id, diagnosis)
      } catch (e) {
        console.warn('[Diagnosis] Failed:', e)
      }
    }

    runDiagnosis()
  }, [tree?.id]) // fires every time a different tree is selected

  // ── Early return AFTER all hooks ──
  if (!tree) return null

  const status = tree.status ?? 'healthy'
  const healthScore = tree.healthScore ?? 100
  const scoreColor =
    healthScore > 60 ? '#639922' :
    healthScore > 40 ? '#EF9F27' : '#E24B4A'

  const hasCanopyScan = tree.ndvi != null
  const hasStemScan = tree.diameterCm != null

  return (
    <div style={{
      position: 'absolute', right: 0, top: 0, width: 320, height: '100%',
      background: 'white', borderLeft: '1px solid #e5e5e5',
      overflowY: 'auto', padding: '20px', zIndex: 100,
      fontFamily: 'system-ui, sans-serif'
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 600 }}>{tree.id}</div>
        <button
          onClick={() => selectTree(null)}
          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18 }}
        >
          ×
        </button>
      </div>

      {/* Awaiting diagnosis banner — shows for ANY tree not yet diagnosed */}
      {tree.disease === undefined && (
        <div style={{
          background: '#f0f9ff', borderRadius: 8,
          padding: '8px 12px', marginBottom: 12,
          fontSize: 12, color: '#185FA5',
          display: 'flex', alignItems: 'center', gap: 6
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: '#185FA5', display: 'inline-block',
            animation: 'pulse 1s infinite'
          }} />
          Analyzing with AI...
        </div>
      )}

      {/* Score ring + status badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
        <div style={{
          width: 64, height: 64, borderRadius: '50%',
          border: `6px solid ${scoreColor}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18, fontWeight: 700, color: scoreColor, flexShrink: 0
        }}>
          {healthScore}
        </div>
        <div>
          <div style={{ fontSize: 13, color: '#666', marginBottom: 4 }}>Health score</div>
          <span style={{
            background: RISK_BG[status],
            color: RISK_TEXT[status],
            padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 600
          }}>
            {RISK_LABEL[status]}
          </span>
        </div>
      </div>

      {/* Scan perspective badges */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>Scan perspective</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Badge label="Canopy" active={hasCanopyScan} />
          <Badge label="Stem" active={hasStemScan} />
        </div>
      </div>

      {/* Stem diameter */}
      {hasStemScan && (
        <div style={{ background: '#f0f9ff', borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>Stem diameter (DBH)</div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>{tree.diameterCm} cm</div>
        </div>
      )}

      {/* Disease */}
      {tree.disease && (
        <div style={{ background: '#fafafa', borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
            <div style={{ fontSize: 11, color: '#888' }}>Detected condition</div>
            {tree.diseaseConfidence != null && (
              <span style={{ fontSize: 11, color: '#aaa' }}>
                {Math.round(tree.diseaseConfidence * 100)}% conf.
              </span>
            )}
          </div>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>{tree.disease}</div>
          {tree.geminiSummary && (
            <div style={{ fontSize: 12, color: '#666', lineHeight: 1.5 }}>{tree.geminiSummary}</div>
          )}
        </div>
      )}

      {/* Action plan */}
      {tree.actionPlan && (
        <div style={{ background: '#f0f9ff', borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>Action plan</div>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{tree.actionPlan}</div>
        </div>
      )}

      {/* Cut reason */}
      {status === 'cut' && tree.cutReason && (
        <div style={{
          background: '#FCEBEB', border: '1px solid #f09595',
          borderRadius: 8, padding: '12px 14px', marginBottom: 12
        }}>
          <div style={{ fontSize: 11, color: '#A32D2D', marginBottom: 4, fontWeight: 600 }}>
            Cut required
          </div>
          <div style={{ fontSize: 13, color: '#501313' }}>{tree.cutReason}</div>
        </div>
      )}

      {/* Visual symptoms */}
      {tree.visualSymptoms?.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>Visual symptoms</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {tree.visualSymptoms.map(s => (
              <span key={s} style={{
                background: '#f5f5f5', padding: '2px 8px',
                borderRadius: 999, fontSize: 12
              }}>
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Stats grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        <Stat label="Spread risk" value={`${Math.round((tree.spreadRiskScore ?? 0) * 100)}%`} />
        <Stat label="Infection zone" value={`${tree.spreadRiskRadiusM ?? 0}m`} />
        <Stat label="At-risk neighbors" value={tree.neighborIds?.length ?? 0} />
        <Stat label="NDVI" value={tree.ndvi != null ? tree.ndvi.toFixed(2) : '—'} />
      </div>

      {/* Environment */}
      <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>Environment</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        <Stat label="Soil pH" value={tree.soil?.ph ?? '—'} />
        <Stat label="Soil type" value={tree.soil?.texture ?? '—'} />
        <Stat label="Humidity" value={tree.weather?.humidity != null ? `${tree.weather.humidity}%` : '—'} />
        <Stat label="Temp" value={tree.weather?.tempC != null ? `${tree.weather.tempC}°C` : '—'} />
        <Stat label="Rain (7d)" value={tree.weather?.rainMm7d != null ? `${tree.weather.rainMm7d}mm` : '—'} />
        <Stat label="Sun (7d)" value={tree.weather?.sunHours7d != null ? `${tree.weather.sunHours7d}h` : '—'} />
      </div>

      {/* Score history */}
      {tree.scanHistory?.length > 1 && (
        <div>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>Score history</div>
          <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 40 }}>
            {tree.scanHistory.slice(-10).map((h, i) => (
              <div key={i} title={`Score: ${h.score}`} style={{
                flex: 1,
                background: h.score > 60 ? '#639922' : h.score > 40 ? '#EF9F27' : '#E24B4A',
                height: `${Math.max(10, h.score)}%`,
                borderRadius: 2, opacity: 0.85, minWidth: 4
              }} />
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
            <span style={{ fontSize: 10, color: '#ccc' }}>oldest</span>
            <span style={{ fontSize: 10, color: '#ccc' }}>latest</span>
          </div>
        </div>
      )}

      {/* GPS */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid #f0f0f0' }}>
        <div style={{ fontSize: 10, color: '#bbb', fontFamily: 'monospace' }}>
          {tree.lat?.toFixed(6)}, {tree.lng?.toFixed(6)}
        </div>
        {tree.lastScannedAt && (
          <div style={{ fontSize: 10, color: '#bbb', marginTop: 2 }}>
            Last scanned: {new Date(tree.lastScannedAt).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  )
}

function Badge({ label, active }) {
  return (
    <span style={{
      background: active ? '#EAF3DE' : '#f0f0f0',
      color: active ? '#27500A' : '#999',
      padding: '3px 10px', borderRadius: 4, fontSize: 11, fontWeight: 700,
      border: `1px solid ${active ? '#639922' : '#ddd'}`
    }}>
      {label} {active ? '✓' : '–'}
    </span>
  )
}

function Stat({ label, value }) {
  return (
    <div style={{ background: '#fafafa', borderRadius: 6, padding: '8px 10px' }}>
      <div style={{ fontSize: 11, color: '#888' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>{value}</div>
    </div>
  )
}