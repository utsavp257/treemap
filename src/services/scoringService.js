// src/services/scoringService.js
import { useForestStore } from '../store/forestStore'

const BACKEND_URL = ''
const GEMINI_KEY  = import.meta.env.VITE_GEMINI_API_KEY
const GMAPS_KEY   = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

const DEDUP_DEG  = 0.00002
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

// ── Fetch satellite image ─────────────────────────────────────────────────────
async function fetchMapImage(lat, lng, zoom, size = '400x400') {
  const url = `https://maps.googleapis.com/maps/api/staticmap?center=${lat},${lng}&zoom=${zoom}&size=${size}&scale=1&maptype=satellite&key=${GMAPS_KEY}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Static Maps failed: ${res.status}`)
  const blob = await res.blob()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result.split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

// ── Gemini diagnosis — only called for sick trees, automatically ──────────────
async function diagnoseTree(tree) {
  if (tree.disease !== undefined) return null
  if (tree.status !== 'treat' && tree.status !== 'cut') return null

  try {
    // Fetch tight crop around this tree
    const size = '100x100'
    const url = `https://maps.googleapis.com/maps/api/staticmap?center=${tree.lat},${tree.lng}&zoom=19&size=${size}&scale=1&maptype=satellite&key=${GMAPS_KEY}`
    const res = await fetch(url)
    const blob = await res.blob()
    const imageBase64 = await new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result.split(',')[1])
      reader.readAsDataURL(blob)
    })

    // Call backend /diagnose — uses LLaVA vision model locally
    const diagRes = await fetch('/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_base64: imageBase64,
        health_score: tree.healthScore,
        ndvi: tree.ndvi,
        status: tree.status,
        visual_symptoms: tree.visualSymptoms ?? [],
      })
    })

    if (!diagRes.ok) throw new Error(`Diagnose error: ${diagRes.status}`)
    return await diagRes.json()

  } catch (e) {
    console.warn('[Diagnosis] Failed for tree', tree.id, e)
    return null
  }
}

// ── Main scan ─────────────────────────────────────────────────────────────────
export async function discoverArea(centerLat, centerLng, viewportBounds, zoom) {
  if (zoom < 15) {
    console.log('[ForestSight] Zoom too low:', zoom)
    return
  }

  const now = Date.now()
  if (now - lastScanTime < COOLDOWN_MS) {
    console.log(`[ForestSight] Cooldown — ${Math.round((COOLDOWN_MS - (now - lastScanTime)) / 1000)}s left`)
    return
  }
  lastScanTime = now

  console.log('[ForestSight] Scanning at zoom', zoom)

  const { trees, addTrees, updateTreeEnv } = useForestStore.getState()

  let imageBase64
  try {
    imageBase64 = await fetchMapImage(centerLat, centerLng, zoom)
    console.log(`[ForestSight] Image: ${(imageBase64.length / 1024).toFixed(1)} KB`)
  } catch (e) {
    console.error('[ForestSight] Image fetch failed:', e)
    return
  }

  let newTrees = []
  try {
    const res = await fetch(`${BACKEND_URL}/detect`, {
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

    if (!res.ok) throw new Error(`Backend error: ${res.status}`)
    const data = await res.json()
    console.log(`[ForestSight] Backend detected ${data.count} trees`)

    newTrees = data.trees
      .filter(t => !isDuplicate(t.lat, t.lng, trees))
      .map(t => ({
        ...t,
        id:          treeId(t.lat, t.lng),
        soil:        { ph: null },
        weather:     { humidity: null, rainMm7d: null, sunHours7d: null },
        scanHistory: [{ score: t.healthScore, timestamp: Date.now() }],
      }))

    if (newTrees.length > 0) {
      addTrees(newTrees)
      console.log(`[ForestSight] +${newTrees.length} trees (${trees.length + newTrees.length} total)`)
    }

  } catch (e) {
    console.error('[ForestSight] Detection failed:', e)
    return
  }

  // ── Auto-diagnose sick trees with Gemini, staggered to avoid rate limits ──
  
}

// ── Called by App.jsx on startup ──────────────────────────────────────────────
export function initForest() {
  console.log('[ForestSight] Ready — Gemini diagnosis on sick trees')
  if (!BACKEND_URL) console.error('[ForestSight] VITE_BACKEND_URL not set in .env')
  if (!GEMINI_KEY) console.warn('[ForestSight] VITE_GEMINI_API_KEY not set — diagnosis disabled')
}
