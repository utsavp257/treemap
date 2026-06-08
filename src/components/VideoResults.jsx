// src/components/VideoResults.jsx
//
// Grid view of tracked trees from a completed video scan. Each card
// shows the cropped tree image + initial detection signals + diagnosis
// headline. Click a card to open TreePanel with the full evidence trace.

import React from 'react'
import { useForestStore } from '../store/forestStore'

const STATUS_COLOR = {
  healthy: '#639922',
  monitor: '#EF9F27',
  treat:   '#D85A30',
  cut:     '#E24B4A',
}

const STATUS_BG = {
  healthy: '#EAF3DE',
  monitor: '#FAEEDA',
  treat:   '#FAECE7',
  cut:     '#FCEBEB',
}


const TreeCard = React.forwardRef(function TreeCard(
  { tree, selected, onClick }, ref
) {
  const status = tree.status ?? 'healthy'
  const color = STATUS_COLOR[status] || '#9ca3af'
  const bg = STATUS_BG[status] || '#f3f4f6'

  return (
    <div
      ref={ref}
      onClick={onClick}
      data-track-id={tree.track_id}
      style={{
        cursor: 'pointer',
        background: 'white',
        border: selected ? `2px solid ${color}` : '1px solid #e5e7eb',
        borderRadius: 8,
        overflow: 'hidden',
        display: 'flex', flexDirection: 'column',
        transition: 'transform 0.08s, box-shadow 0.08s',
        boxShadow: selected ? `0 4px 12px ${color}33` : 'none',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateY(-1px)' }}
      onMouseLeave={(e) => { e.currentTarget.style.transform = 'translateY(0)' }}
    >
      <div style={{ position: 'relative', background: '#f3f4f6', height: 130 }}>
        {tree.cropUrl ? (
          <img
            src={tree.cropUrl}
            alt={`tree ${tree.track_id}`}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={(e) => { e.currentTarget.style.display = 'none' }}
          />
        ) : (
          <div style={{
            width: '100%', height: '100%', display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            fontSize: 11, color: '#9ca3af',
          }}>no crop</div>
        )}
        <span style={{
          position: 'absolute', top: 6, left: 6,
          background: bg, color, fontSize: 10, fontWeight: 700,
          padding: '2px 7px', borderRadius: 4, textTransform: 'uppercase',
          letterSpacing: '0.4px',
        }}>{status}</span>
      </div>

      <div style={{ padding: 10 }}>
        <div style={{ fontSize: 11, color: '#9ca3af',
          fontFamily: 'monospace', marginBottom: 4 }}>
          {tree.track_id}
        </div>

        {tree.species ? (
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {tree.species}
          </div>
        ) : (
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#6b7280' }}>
            (no species)
          </div>
        )}

        {tree.disease && tree.disease !== 'None detected' ? (
          <div style={{ fontSize: 11, color: '#991b1b',
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
            overflow: 'hidden' }}>
            {tree.disease}
          </div>
        ) : tree.diagnosis === null ? (
          <div style={{ fontSize: 11, color: '#9ca3af', fontStyle: 'italic' }}>
            Diagnosis incomplete (quota or error)
          </div>
        ) : (
          <div style={{ fontSize: 11, color: '#6b7280' }}>
            {tree.disease || '—'}
          </div>
        )}

        {tree.evidenceTrace?.length > 0 && (
          <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 6 }}>
            {tree.evidenceTrace.length} evidence step{tree.evidenceTrace.length === 1 ? '' : 's'}
          </div>
        )}
      </div>
    </div>
  )
})


export function VideoResults() {
  const trees = useForestStore(s => s.trees)
  const selectedId = useForestStore(s => s.selectedTreeId)
  const selectTree = useForestStore(s => s.selectTree)
  const videoJob = useForestStore(s => s.videoJob)

  const containerRef = React.useRef(null)

  // When the map (or anything else) changes the selection, scroll the
  // corresponding card into view.
  React.useEffect(() => {
    if (!selectedId || !containerRef.current) return
    const el = containerRef.current.querySelector(
      `[data-track-id="${selectedId}"]`
    )
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [selectedId])

  if (!videoJob || trees.length === 0) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', fontSize: 14, color: '#9ca3af',
      }}>
        {videoJob ? 'No trees detected in this scan.' : 'No scan loaded.'}
      </div>
    )
  }

  // Sort: sick trees first by severity, then healthy.
  const sevRank = { cut: 0, treat: 1, monitor: 2, healthy: 3 }
  const sorted = [...trees].sort((a, b) =>
    (sevRank[a.status] ?? 9) - (sevRank[b.status] ?? 9)
  )

  return (
    <div ref={containerRef} style={{
      height: '100%', overflowY: 'auto', padding: '24px 28px',
    }}>
      <div style={{ marginBottom: 16, display: 'flex',
        alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>
          {videoJob.mode === 'stem' ? 'Stem' : 'Crown'} scan results
        </div>
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          {trees.length} tracked across {videoJob.frame_count} frames
          {videoJob.has_gps && ' · GPS-mapped'}
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: 12,
      }}>
        {sorted.map(tree => (
          <TreeCard
            key={tree.id}
            tree={tree}
            selected={tree.id === selectedId}
            onClick={() => selectTree(tree.id === selectedId ? null : tree.id)}
          />
        ))}
      </div>
    </div>
  )
}
