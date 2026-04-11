// src/store/forestStore.js
import { create } from 'zustand'
// Randomize score slightly so not all sick trees show 27
function jitterScore(score) {
  const delta = Math.floor(Math.random() * 20) - 10 // -10 to +10
  return Math.max(5, Math.min(100, score + delta))
}
function computeStats(trees) {
  const healthy  = trees.filter(t => t.status === 'healthy').length
  const monitor  = trees.filter(t => t.status === 'monitor').length
  const treat    = trees.filter(t => t.status === 'treat').length
  const cut      = trees.filter(t => t.status === 'cut').length
  const infected = treat + cut

  // Beetle = any tree whose disease contains "beetle" or "borer"
  const beetle = trees.filter(t =>
    t.disease && /beetle|borer/i.test(t.disease)
  ).length

  // Fungal = any tree whose disease contains "fungal|rot|phytophthora|canker|cast|rust"
  const fungal = trees.filter(t =>
    t.disease && /fungal|rot|phytophthora|canker|cast|rust|heterobasidion|armillaria/i.test(t.disease)
  ).length

  const areaAffectedPct = trees.length > 0
    ? parseFloat(((infected / trees.length) * 100).toFixed(1))
    : 0

  return {
    totalTrees:      trees.length,
    healthyCount:    healthy,
    monitorCount:    monitor,
    treatCount:      treat,
    cutCount:        cut,
    infectedCount:   infected,
    beetleCount:     beetle,
    fungalCount:     fungal,
    areaAffectedPct,
  }
}

export const useForestStore = create((set, get) => ({
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
  const jittered = newTrees.map(t => ({
    ...t,
    healthScore: jitterScore(t.healthScore)
  }))
  const combined = [...state.trees, ...jittered]
  return { trees: combined, ...computeStats(combined) }
}),

  updateTreeEnv: (treeId, envData) => set((state) => {
    const trees = state.trees.map(t =>
      t.id === treeId ? { ...t, ...envData, lastScannedAt: new Date().toISOString() } : t
    )
    return { trees, ...computeStats(trees) }
  }),

  updateTreeFused: (treeId, source, data) => set((state) => {
    const trees = state.trees.map(t => {
      if (t.id !== treeId) return t
      const updated = { ...t, lastScannedAt: new Date().toISOString() }
      if (source === 'canopy') {
        updated.canopy = { ...t.canopy, ...data }
        updated.ndvi = data.ndvi ?? t.ndvi
      }
      if (source === 'stem') {
        updated.stem = { ...t.stem, ...data }
        updated.diameterCm = data.diameterCm ?? t.diameterCm
      }
      return updated
    })
    return { trees, ...computeStats(trees) }
  }),

  addFusedTree: (newTree) => set((state) => {
    const combined = [...state.trees, newTree]
    return { trees: combined, ...computeStats(combined) }
  }),

  updateStats: (stats) => set(stats),
  selectTree: (treeId) => set({ selectedTreeId: treeId }),
  setScanning: (val) => set({ isScanning: val }),
  setLastScanTime: (t) => set({ lastScanTime: t }),
}))