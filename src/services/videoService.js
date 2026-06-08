// src/services/videoService.js
//
// Talks to /scan-video on the backend. Two patterns:
//   - upload: POST a video (and optional SRT) → returns {job_id, status}
//   - poll:   GET status every N seconds until complete/failed.

export async function uploadVideo({
  file, mode, fps = 0.5, srt = null,
  speciesHint = null, maxEdgePx = null,
  sameSpecies = true,
}) {
  const form = new FormData()
  form.append('video', file)
  form.append('mode', mode)
  form.append('fps', String(fps))
  form.append('same_species', sameSpecies ? 'true' : 'false')
  if (srt) form.append('srt', srt)
  if (speciesHint && speciesHint.trim()) {
    form.append('species_hint', speciesHint.trim())
  }
  if (maxEdgePx && Number.isFinite(maxEdgePx)) {
    form.append('max_edge_px', String(maxEdgePx))
  }

  const res = await fetch('/scan-video', { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(`Upload failed (${res.status}): ${JSON.stringify(err)}`)
  }
  return res.json()
}

export async function fetchJobStatus(jobId) {
  const res = await fetch(`/scan-video/${jobId}`)
  if (!res.ok) throw new Error(`Status fetch failed (${res.status})`)
  return res.json()
}

export function treeCropUrl(jobId, trackId) {
  return `/scan-video/${jobId}/tree/${trackId}/image`
}

/** Poll job status until terminal state; calls onUpdate on every tick. */
export async function pollJobUntilDone(jobId, { onUpdate, intervalMs = 2500 } = {}) {
  while (true) {
    const job = await fetchJobStatus(jobId)
    if (onUpdate) onUpdate(job)
    if (job.status === 'complete' || job.status === 'failed') return job
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

export async function deleteJob(jobId) {
  await fetch(`/scan-video/${jobId}`, { method: 'DELETE' })
}
