// src/components/VideoUpload.jsx
//
// Two-state component:
//   1. Idle: file inputs + "Start scan" button.
//   2. Running: progress strip + per-stage status; auto-polls until
//      job reaches complete or failed.
//
// On completion, the resulting VideoJob is dropped into the store so
// VideoResults can render the tracked trees.

import React from 'react'
import { useForestStore } from '../store/forestStore'
import { uploadVideo, pollJobUntilDone } from '../services/videoService'

const STAGE_LABELS = {
  queued:     'Queued',
  extracting: 'Extracting frames',
  detecting:  'Detecting trees per frame',
  tracking:   'Tracking across frames',
  diagnosing: 'Agent diagnosing sick trees',
  complete:   'Complete',
  failed:     'Failed',
}

export function VideoUpload() {
  const mode = useForestStore(s => s.mode)
  const videoJob = useForestStore(s => s.videoJob)
  const setVideoJob = useForestStore(s => s.setVideoJob)

  const subtype = mode === 'video-stem' ? 'stem' : 'crown'

  const [file, setFile] = React.useState(null)
  const [srt, setSrt] = React.useState(null)
  const [fps, setFps] = React.useState(0.5)
  const [speciesHint, setSpeciesHint] = React.useState('')
  const [sameSpecies, setSameSpecies] = React.useState(true)
  // 'auto' → backend picks from SRT altitude or default.
  // Otherwise a fixed width in px applied unconditionally.
  const [resolution, setResolution] = React.useState('auto')
  const [error, setError] = React.useState(null)
  const [polling, setPolling] = React.useState(false)
  const [liveStatus, setLiveStatus] = React.useState(null)

  const start = async () => {
    if (!file) { setError('Pick an MP4 first.'); return }
    setError(null)
    setPolling(true)
    setLiveStatus(null)
    try {
      const maxEdgePx = resolution === 'auto' ? null : parseInt(resolution, 10)
      const { job_id } = await uploadVideo({
        file, mode: subtype, fps, srt, speciesHint, maxEdgePx, sameSpecies,
      })
      const final = await pollJobUntilDone(job_id, {
        onUpdate: (job) => setLiveStatus(job),
        intervalMs: 2000,
      })
      setVideoJob(final)
      if (final.status === 'failed') setError(final.error || 'Job failed.')
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setPolling(false)
    }
  }

  // ── Already showing results — small "new scan" button ────────────────
  if (videoJob && !polling) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12, color: '#9ca3af' }}>
          {videoJob.status === 'complete'
            ? `${videoJob.tracked_tree_count} trees from ${videoJob.frame_count} frames`
            : `job ${videoJob.status}`}
          {videoJob.frame_max_edge_px && (
            <span style={{ marginLeft: 6, color: '#6b7280' }}>
              · {videoJob.frame_max_edge_px}px frames
            </span>
          )}
        </span>
        <button
          onClick={() => setVideoJob(null)}
          style={{
            fontSize: 12, fontWeight: 600, padding: '5px 12px',
            background: '#1f2937', color: 'white',
            border: 'none', borderRadius: 6, cursor: 'pointer',
          }}
        >New scan</button>
      </div>
    )
  }

  // ── In-flight job ─────────────────────────────────────────────────────
  if (polling) {
    const pct = Math.round((liveStatus?.progress || 0) * 100)
    const stage = liveStatus?.status || 'queued'
    return (
      <div style={{ width: '100%', maxWidth: 600, margin: '40px auto', padding: 24 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
          {STAGE_LABELS[stage] || stage}…
        </div>
        <div style={{
          height: 8, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden',
        }}>
          <div style={{
            width: `${pct}%`, height: '100%',
            background: '#639922', transition: 'width 0.3s',
          }} />
        </div>
        <div style={{ fontSize: 12, color: '#6b7280', marginTop: 6,
          display: 'flex', justifyContent: 'space-between' }}>
          <span>{pct}%</span>
          {liveStatus?.tracked_tree_count > 0 && (
            <span>{liveStatus.tracked_tree_count} trees tracked</span>
          )}
        </div>
      </div>
    )
  }

  // ── Idle: upload form ─────────────────────────────────────────────────
  return (
    <div style={{
      width: '100%', maxWidth: 560, margin: '60px auto', padding: 32,
      background: 'white', borderRadius: 12,
      boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
      border: '1px solid #e5e7eb',
    }}>
      <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 6 }}>
        Upload {subtype === 'crown' ? 'crown' : 'stem'} drone video
      </div>
      <div style={{ fontSize: 13, color: '#6b7280', marginBottom: 24 }}>
        {subtype === 'crown'
          ? 'Top-down or oblique aerial footage. Each tree is tracked across frames, sick ones get a full agent diagnosis.'
          : 'Low-altitude horizontal footage with trunks visible. The agent runs bark/trunk-focused tools per tracked tree.'}
      </div>

      <div style={{ marginBottom: 14 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
          color: '#374151', marginBottom: 6 }}>
          Video (MP4)
        </label>
        <input
          type="file" accept="video/mp4,video/*"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          style={{ fontSize: 13 }}
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
          color: '#374151', marginBottom: 6 }}>
          DJI SRT telemetry (optional, for GPS-mapped results)
        </label>
        <input
          type="file" accept=".srt"
          onChange={(e) => setSrt(e.target.files?.[0] || null)}
          style={{ fontSize: 13 }}
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
          color: '#374151', marginBottom: 6 }}>
          Tree species (optional)
        </label>
        <input
          type="text"
          value={speciesHint}
          onChange={(e) => setSpeciesHint(e.target.value)}
          placeholder='e.g. "Pinus halepensis" or "Aleppo pine"'
          style={{
            width: '100%', padding: '8px 10px',
            fontSize: 13, color: '#111827',
            border: '1px solid #d1d5db', borderRadius: 6,
            boxSizing: 'border-box',
          }}
        />
        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>
          If you know the dominant species in this footage, supply it here.
          The agent will skip its species-ID step and use this directly —
          faster, cheaper, and avoids misclassification on look-alike species.
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <label style={{
          display: 'flex', alignItems: 'flex-start', gap: 8,
          fontSize: 12, color: '#374151', cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={sameSpecies}
            onChange={(e) => setSameSpecies(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span>
            <strong style={{ fontWeight: 600 }}>
              All trees in this video are the same species
            </strong>
            <span style={{ display: 'block', color: '#9ca3af',
              fontWeight: 400, marginTop: 2, fontSize: 11 }}>
              Default. When on, species ID runs ONCE per video and is
              reused for every tree (faster, more consistent). Uncheck
              for mixed-species stands.
            </span>
          </span>
        </label>
      </div>

      <div style={{ marginBottom: 14 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
          color: '#374151', marginBottom: 6 }}>
          Detection resolution
        </label>
        <select
          value={resolution}
          onChange={(e) => setResolution(e.target.value)}
          style={{
            width: '100%', padding: '8px 10px',
            fontSize: 13, color: '#111827',
            border: '1px solid #d1d5db', borderRadius: 6,
            boxSizing: 'border-box', background: 'white',
          }}
        >
          <option value="auto">Auto (altitude-adaptive — recommended)</option>
          <option value="768">768 px · 1 tile · cheapest</option>
          <option value="1024">1024 px · ~2 tiles · balanced</option>
          <option value="1280">1280 px · ~3 tiles · default</option>
          <option value="1920">1920 px · ~6 tiles · highest detail</option>
        </select>
        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>
          Auto uses SRT altitude to pick the smallest resolution that still
          gives Gemini ~50 px of detail per crown. Higher resolutions cost
          more tokens per call (Gemini bills in 768×768 tiles).
        </div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600,
          color: '#374151', marginBottom: 6 }}>
          Sampling rate: {fps} fps
        </label>
        <input
          type="range" min={0.2} max={2} step={0.1}
          value={fps} onChange={(e) => setFps(parseFloat(e.target.value))}
          style={{ width: '100%' }}
        />
        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>
          Lower fps → cheaper, faster. Higher fps → more reliable tracking, more Gemini calls.
        </div>
      </div>

      {error && (
        <div style={{
          background: '#fef2f2', color: '#991b1b',
          padding: '10px 12px', borderRadius: 6,
          fontSize: 12, marginBottom: 14,
          maxHeight: 100, overflow: 'auto', wordBreak: 'break-word',
        }}>{error}</div>
      )}

      <button
        onClick={start}
        disabled={!file}
        style={{
          width: '100%', padding: '10px 14px',
          background: file ? '#639922' : '#9ca3af',
          color: 'white', border: 'none', borderRadius: 6,
          fontSize: 14, fontWeight: 600,
          cursor: file ? 'pointer' : 'not-allowed',
        }}
      >Start scan</button>
    </div>
  )
}
