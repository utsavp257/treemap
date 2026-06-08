// src/components/ModePicker.jsx
//
// Compact mode toggle. Satellite | Video → (Crown | Stem).
// Lives in the top bar; switching mode wipes the tree list.

import React from 'react'
import { useForestStore } from '../store/forestStore'

const SEG_BTN = (active) => ({
  background: active ? '#ffffff' : 'transparent',
  color: active ? '#111827' : '#cbd5e1',
  border: 'none',
  padding: '6px 14px',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  borderRadius: 6,
  transition: 'background 0.12s, color 0.12s',
})

export function ModePicker() {
  const mode = useForestStore(s => s.mode)
  const setMode = useForestStore(s => s.setMode)

  const isVideo = mode === 'video-crown' || mode === 'video-stem'
  const subtype = mode === 'video-stem' ? 'stem' : 'crown'

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 2,
        background: '#334155', borderRadius: 8, padding: 3,
      }}>
        <button onClick={() => setMode('satellite')} style={SEG_BTN(!isVideo)}>
          Satellite
        </button>
        <button
          onClick={() => setMode(`video-${subtype}`)}
          style={SEG_BTN(isVideo)}
        >
          Video
        </button>
      </div>

      {isVideo && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 2,
          background: '#334155', borderRadius: 8, padding: 3,
        }}>
          <button
            onClick={() => setMode('video-crown')}
            style={SEG_BTN(mode === 'video-crown')}
          >
            Crown
          </button>
          <button
            onClick={() => setMode('video-stem')}
            style={SEG_BTN(mode === 'video-stem')}
          >
            Stem
          </button>
        </div>
      )}
    </div>
  )
}
