// src/services/scoringService.js
import { useForestStore } from '../store/forestStore'

const GMAPS_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

const DEDUP_DEG   = 0.00002
const COOLDOWN_MS = 200

let lastScanTime = 0

// ── Helpers ───────────────────────────────────────────────────────────────────
function isDuplicate(lat, lng, trees) {
  return trees.some(t =>
    Math.abs(t.lat - lat) < DEDUP_DEG &&
    Math.abs(t.lng - lng) < DEDUP_DEG
  )
}

function treeId(lat, lng) {
  const snap = v => Math.round(v / DEDUP_DEG) * DEDUP_DEG
  return `tree-${snap(lat).toFixed(6)}-${snap(lng).toFixed(6)}`
}

// ── Image fetching ────────────────────────────────────────────────────────────
async function fetchBase64(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Static Maps failed: ${res.status}`)
  const blob = await res.blob()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload  = () => resolve(reader.result.split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

async function fetchMapImage(lat, lng, zoom, size = '400x400') {
  const url = `https://maps.googleapis.com/maps/api/staticmap?center=${lat},${lng}&zoom=${zoom}&size=${size}&scale=1&maptype=satellite&key=${GMAPS_KEY}`
  return fetchBase64(url)
}

/** Tight crop centered on a single tree — used by the diagnose panel. */
export async function fetchTreeCrop(lat, lng) {
  const url = `https://maps.googleapis.com/maps/api/staticmap?center=${lat},${lng}&zoom=20&size=128x128&scale=2&maptype=satellite&key=${GMAPS_KEY}`
  return fetchBase64(url)
}

// ── Main scan ─────────────────────────────────────────────────────────────────
export async function discoverArea(centerLat, centerLng, viewportBounds, zoom) {
  if (zoom < 15) return

  const now = Date.now()
  if (now - lastScanTime < COOLDOWN_MS) return
  lastScanTime = now

  const { trees, addTrees } = useForestStore.getState()

  let imageBase64
  try {
    imageBase64 = await fetchMapImage(centerLat, centerLng, zoom)
  } catch (e) {
    console.error('[scan] image fetch failed:', e)
    return
  }

  try {
    const res = await fetch('/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_base64: imageBase64,
        north: viewportBounds.north,
        south: viewportBounds.south,
        east:  viewportBounds.east,
        west:  viewportBounds.west,
        zoom,
      })
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      console.error('[scan] /detect failed:', res.status, err)
      return
    }

    const data = await res.json()
    const newTrees = data.trees
      .filter(t => !isDuplicate(t.lat, t.lng, trees))
      .map(t => ({ ...t, id: treeId(t.lat, t.lng) }))

    if (newTrees.length > 0) {
      addTrees(newTrees)
      console.log(`[scan] +${newTrees.length} trees (${trees.length + newTrees.length} total)`)
    }
  } catch (e) {
    console.error('[scan] /detect threw:', e)
  }
}

export function initForest() {
  if (!GMAPS_KEY) console.warn('[scan] VITE_GOOGLE_MAPS_API_KEY not set')
}
