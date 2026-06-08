// src/components/TreePanel.jsx
//
// Right-side panel that opens when a tree is selected. Same component
// for both satellite and video modes:
//
// - Satellite: when a tree is clicked we lazily POST /diagnose with a
//   tight crop. Result includes a full evidenceTrace from the agent.
// - Video: the diagnosis is already attached to each tree by the
//   /scan-video pipeline (full evidence trace included).
//
// No more dead UI (NDVI, soil pH, weather, etc.) — only fields that
// the real backend actually populates.

import React from 'react'
import { useForestStore } from '../store/forestStore'
import { fetchTreeCrop } from '../services/scoringService'
import { EvidenceTrace } from './EvidenceTrace'

const RISK_LABEL = {
  healthy: 'Healthy',
  monitor: 'Monitor',
  treat:   'Needs treatment',
  cut:     'Cut down',
}
const RISK_BG = {
  healthy: '#EAF3DE',
  monitor: '#FAEEDA',
  treat:   '#FAECE7',
  cut:     '#FCEBEB',
}
const RISK_TEXT = {
  healthy: '#27500A',
  monitor: '#633806',
  treat:   '#712B13',
  cut:     '#501313',
}


export function TreePanel() {
  const trees      = useForestStore(s => s.trees)
  const selectedId = useForestStore(s => s.selectedTreeId)
  const selectTree = useForestStore(s => s.selectTree)
  const updateTree = useForestStore(s => s.updateTree)
  const mode       = useForestStore(s => s.mode)
  const videoJob   = useForestStore(s => s.videoJob)

  const tree = trees.find(t => t.id === selectedId)

  const isVideoMode = mode !== 'satellite'

  // ── Satellite-only: lazy-diagnose on tree click ─────────────────────
  React.useEffect(() => {
    if (!tree || isVideoMode) return
    if (tree.disease !== undefined) return  // already diagnosed
    let cancelled = false

    const runDiagnosis = async () => {
      try {
        const imageBase64 = await fetchTreeCrop(tree.lat, tree.lng)
        if (cancelled) return

        const res = await fetch('/diagnose', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            image_base64: imageBase64,
            status: tree.status ?? 'healthy',
            visual_symptoms: tree.visualSymptoms ?? [],
            mode_hint: 'satellite',
          }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          console.warn('[diagnose] failed:', res.status, err)
          return
        }
        const diagnosis = await res.json()
        if (cancelled) return
        updateTree(tree.id, diagnosis)
      } catch (e) {
        console.warn('[diagnose] threw:', e)
      }
    }

    runDiagnosis()
    return () => { cancelled = true }
  }, [tree?.id, isVideoMode])

  if (!tree) return null

  const status = tree.status ?? 'healthy'
  const healthScore = tree.healthScore ?? 100
  const scoreColor =
    healthScore > 60 ? '#639922' :
    healthScore > 40 ? '#EF9F27' : '#E24B4A'

  const diagnosing = tree.disease === undefined

  // Build crop image URL — only video mode has one.
  const cropUrl = tree.cropUrl
    ? (videoJob?.job_id ? `/scan-video/${videoJob.job_id}/tree/${tree.track_id}/image` : null)
    : null

  return (
    <div style={{
      position: 'absolute', right: 0, top: 0, width: 360, height: '100%',
      background: 'white', borderLeft: '1px solid #e5e5e5',
      overflowY: 'auto', padding: '20px', zIndex: 100,
      fontFamily: 'system-ui, sans-serif',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between',
        alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace' }}>
            {isVideoMode ? `track ${tree.track_id}` : tree.id}
          </div>
          {tree.species && (
            <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
              {tree.species}
              {tree.speciesConfidence != null && (
                <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 400, marginLeft: 6 }}>
                  conf {(tree.speciesConfidence * 100).toFixed(0)}%
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={() => selectTree(null)}
          style={{ border: 'none', background: 'none', cursor: 'pointer',
            fontSize: 20, color: '#9ca3af', padding: 0, lineHeight: 1 }}
        >×</button>
      </div>

      {/* Crop thumbnail — video mode */}
      {cropUrl && (
        <img
          src={cropUrl}
          alt="tree crop"
          style={{
            width: '100%', height: 160, objectFit: 'cover',
            borderRadius: 6, marginBottom: 12,
            background: '#f3f4f6',
          }}
          onError={(e) => { e.currentTarget.style.display = 'none' }}
        />
      )}

      {/* Awaiting diagnosis */}
      {diagnosing && (
        <div style={{
          background: '#eff6ff', borderRadius: 8,
          padding: '8px 12px', marginBottom: 12,
          fontSize: 12, color: '#1e40af',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: '#1e40af', display: 'inline-block',
            animation: 'pulse 1s infinite',
          }} />
          Running agent diagnosis…
        </div>
      )}

      {/* Score ring + status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 18 }}>
        <div style={{
          width: 64, height: 64, borderRadius: '50%',
          border: `6px solid ${scoreColor}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18, fontWeight: 700, color: scoreColor, flexShrink: 0,
        }}>{healthScore}</div>
        <div>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>Health score</div>
          <span style={{
            background: RISK_BG[status], color: RISK_TEXT[status],
            padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 600,
          }}>
            {RISK_LABEL[status]}
          </span>
        </div>
      </div>

      {/* Disease */}
      {tree.disease && (
        <div style={{
          background: '#fafafa', borderRadius: 8,
          padding: '12px 14px', marginBottom: 12,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', marginBottom: 4 }}>
            <div style={{ fontSize: 11, color: '#888' }}>Detected condition</div>
            {tree.diseaseConfidence != null && (
              <span style={{ fontSize: 11, color: '#9ca3af' }}>
                {Math.round(tree.diseaseConfidence * 100)}% confidence
              </span>
            )}
          </div>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>{tree.disease}</div>
          {tree.summary && (
            <div style={{ fontSize: 12, color: '#4b5563', lineHeight: 1.5 }}>
              {tree.summary}
            </div>
          )}
        </div>
      )}

      {/* Action plan */}
      {tree.actionPlan && (
        <div style={{ background: '#eff6ff', borderRadius: 8,
          padding: '12px 14px', marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 4 }}>Action plan</div>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{tree.actionPlan}</div>
        </div>
      )}

      {/* Cut reason */}
      {status === 'cut' && tree.cutReason && (
        <div style={{
          background: '#FCEBEB', border: '1px solid #f09595',
          borderRadius: 8, padding: '12px 14px', marginBottom: 12,
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
          <div style={{ fontSize: 11, color: '#888', marginBottom: 6 }}>Visible symptoms</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {tree.visualSymptoms.map(s => (
              <span key={s} style={{
                background: '#f3f4f6', padding: '2px 8px',
                borderRadius: 999, fontSize: 12,
              }}>{s}</span>
            ))}
          </div>
        </div>
      )}

      {/* Evidence trace from the agent */}
      {tree.evidenceTrace?.length > 0 && (
        <div style={{ marginTop: 16, paddingTop: 12,
          borderTop: '1px solid #f0f0f0' }}>
          <EvidenceTrace trace={tree.evidenceTrace} />
        </div>
      )}

      {/* Location footer */}
      <div style={{ marginTop: 16, paddingTop: 12,
        borderTop: '1px solid #f0f0f0', fontSize: 10, color: '#9ca3af',
        fontFamily: 'monospace' }}>
        {tree.lat != null && tree.lng != null
          ? `${tree.lat.toFixed(6)}, ${tree.lng.toFixed(6)}`
          : tree.bboxNormalized
            ? `bbox ${tree.bboxNormalized.join(', ')} (pixel-space)`
            : 'location unknown'}
        {tree.framesSeen != null && (
          <span style={{ marginLeft: 8 }}>
            · seen in {tree.framesSeen} {tree.framesSeen === 1 ? 'frame' : 'frames'}
          </span>
        )}
        {tree.detectionConfidence != null && (
          <span style={{ marginLeft: 8 }}>
            · det conf {(tree.detectionConfidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
    </div>
  )
}
