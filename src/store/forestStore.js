// src/store/forestStore.js
import { create } from 'zustand'

function computeStats(trees) {
  const healthy = trees.filter(t => t.status === 'healthy').length
  const monitor = trees.filter(t => t.status === 'monitor').length
  const treat   = trees.filter(t => t.status === 'treat').length
  const cut     = trees.filter(t => t.status === 'cut').length
  const infected = treat + cut

  const beetle = trees.filter(t =>
    t.disease && /beetle|borer/i.test(t.disease)
  ).length

  const fungal = trees.filter(t =>
    t.disease && /fungal|rot|phytophthora|canker|cast|rust|heterobasidion|armillaria/i.test(t.disease)
  ).length

  const areaAffectedPct = trees.length > 0
    ? parseFloat(((infected / trees.length) * 100).toFixed(1))
    : 0

  return {
    totalTrees:    trees.length,
    healthyCount:  healthy,
    monitorCount:  monitor,
    treatCount:    treat,
    cutCount:      cut,
    infectedCount: infected,
    beetleCount:   beetle,
    fungalCount:   fungal,
    areaAffectedPct,
  }
}

export const useForestStore = create((set, get) => ({
  // ── Mode ──────────────────────────────────────────────────────────────
  mode: 'satellite',  // 'satellite' | 'video-crown' | 'video-stem'
  setMode: (mode) => set({
    mode,
    selectedTreeId: null,
    // wipe the tree list when switching modes — satellite and video are
    // independent universes, not cumulative.
    trees: [],
    ...computeStats([]),
    videoJob: null,
  }),

  // ── Trees ─────────────────────────────────────────────────────────────
  trees: [],
  totalTrees: 0,
  healthyCount: 0,
  monitorCount: 0,
  treatCount: 0,
  cutCount: 0,
  infectedCount: 0,
  beetleCount: 0,
  fungalCount: 0,
  areaAffectedPct: 0,
  selectedTreeId: null,
  isScanning: false,
  lastScanTime: null,

  setTrees: (trees) => set({ trees, ...computeStats(trees) }),

  addTrees: (newTrees) => set((state) => {
    const combined = [...state.trees, ...newTrees]
    return { trees: combined, ...computeStats(combined) }
  }),

  updateTree: (treeId, patch) => set((state) => {
    const trees = state.trees.map(t =>
      t.id === treeId ? { ...t, ...patch, lastScannedAt: new Date().toISOString() } : t
    )
    return { trees, ...computeStats(trees) }
  }),

  selectTree: (treeId) => set({ selectedTreeId: treeId }),
  setScanning: (val) => set({ isScanning: val }),
  setLastScanTime: (t) => set({ lastScanTime: t }),

  // ── Video job ─────────────────────────────────────────────────────────
  videoJob: null,
  setVideoJob: (job) => {
    if (!job) {
      set({ videoJob: null, trees: [], ...computeStats([]), selectedTreeId: null })
      return
    }
    // Map TrackedTree → frontend tree shape so existing components
    // (TreePanel, etc.) can render video results without branching.
    const trees = (job.trees || []).map((t) => ({
      id: t.track_id,
      track_id: t.track_id,
      lat: t.lat,
      lng: t.lng,
      crownRadiusM: 6,
      label: null,
      status: t.diagnosis?.disease && t.diagnosis.disease !== 'None detected'
        ? (t.initial_status === 'cut' ? 'cut' : t.initial_status === 'treat' ? 'treat' : 'monitor')
        : t.initial_status,
      healthScore: t.diagnosis
        ? (t.diagnosis.disease === 'None detected'
            ? 85
            : t.initial_status === 'cut' ? 18
            : t.initial_status === 'treat' ? 38
            : 62)
        : 62,
      detectionConfidence: t.detection_confidence,
      visualSymptoms: t.initial_symptoms,
      cropUrl: t.crop_url,
      framesSeen: t.frames_seen,
      bboxNormalized: t.bbox_normalized,
      ...(t.diagnosis || {}),
    }))
    set({ videoJob: job, trees, ...computeStats(trees) })
  },
}))
