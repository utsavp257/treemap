// src/components/VideoMap.jsx
//
// Geo-pinned map view for video-mode results. Renders only when the
// uploaded SRT supplied GPS — bbox-centroid → lat/lng projection
// happens on the backend (nadir crown footage only).
//
// Bi-directional selection with the grid:
//   - click a circle → store.selectTree(id) → TreePanel opens + card highlights
//   - clicking a card elsewhere pans/zooms the map onto that tree

import React from 'react'
import { GoogleMap, Circle, HeatmapLayer } from '@react-google-maps/api'
import { useForestStore } from '../store/forestStore'

const STATUS_COLORS = {
  healthy: '#639922',
  monitor: '#EF9F27',
  treat:   '#D85A30',
  cut:     '#E24B4A',
}

const MAP_OPTIONS = {
  mapTypeId: 'satellite',
  disableDefaultUI: false,
  zoomControl: true,
  mapTypeControl: true,
  streetViewControl: false,
  fullscreenControl: false,
  tilt: 0,
}


export function VideoMap() {
  const videoJob   = useForestStore(s => s.videoJob)
  const trees      = useForestStore(s => s.trees)
  const selectedId = useForestStore(s => s.selectedTreeId)
  const selectTree = useForestStore(s => s.selectTree)

  const [showHeatmap, setShowHeatmap] = React.useState(false)
  const mapRef = React.useRef(null)

  // Only trees with real GPS show on the map.
  const geoTrees = React.useMemo(
    () => trees.filter(t => t.lat != null && t.lng != null),
    [trees]
  )

  // Initial center: mean of all geo-pinned trees (or fall back to 0,0).
  const initialCenter = React.useMemo(() => {
    if (geoTrees.length === 0) return { lat: 0, lng: 0 }
    const sum = geoTrees.reduce(
      (acc, t) => ({ lat: acc.lat + t.lat, lng: acc.lng + t.lng }),
      { lat: 0, lng: 0 }
    )
    return { lat: sum.lat / geoTrees.length, lng: sum.lng / geoTrees.length }
  }, [geoTrees])

  // Fit bounds to all pinned trees on first render of a new job.
  const handleLoad = React.useCallback((map) => {
    mapRef.current = map
    if (geoTrees.length < 2) return
    const bounds = new window.google.maps.LatLngBounds()
    geoTrees.forEach(t => bounds.extend({ lat: t.lat, lng: t.lng }))
    map.fitBounds(bounds, 80)
  }, [geoTrees])

  // Pan + zoom to the selected tree.
  React.useEffect(() => {
    if (!mapRef.current || !selectedId) return
    const t = geoTrees.find(t => t.id === selectedId)
    if (!t) return
    mapRef.current.panTo({ lat: t.lat, lng: t.lng })
    if (mapRef.current.getZoom() < 19) mapRef.current.setZoom(19)
  }, [selectedId, geoTrees])

  // Heatmap data shape: [{location, weight}, ...] — weight is (1 - health/100)
  // so sick trees pull the heat up.
  const heatmapData = React.useMemo(() => {
    if (!window.google) return []
    return geoTrees.map(t => ({
      location: new window.google.maps.LatLng(t.lat, t.lng),
      weight: 1 - (t.healthScore ?? 100) / 100,
    }))
  }, [geoTrees])

  // ── No-GPS placeholder ──────────────────────────────────────────────
  if (!videoJob || geoTrees.length === 0) {
    return (
      <div style={{
        height: '100%', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        background: '#f9fafb', borderTop: '1px solid #e5e7eb',
        flexDirection: 'column', gap: 6, padding: 24, textAlign: 'center',
      }}>
        <div style={{ fontSize: 13, color: '#374151', fontWeight: 600 }}>
          No GPS-pinned trees on this scan.
        </div>
        <div style={{ fontSize: 12, color: '#6b7280', maxWidth: 420 }}>
          Upload a DJI SRT sidecar with the video to project each tree's
          bbox onto real lat/lng. Crown-mode + nadir-pointing gimbal only.
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', position: 'relative', background: '#f9fafb' }}>
      <GoogleMap
        mapContainerStyle={{ width: '100%', height: '100%' }}
        center={initialCenter}
        zoom={18}
        options={MAP_OPTIONS}
        onLoad={handleLoad}
      >
        {showHeatmap && heatmapData.length > 0 && (
          <HeatmapLayer
            data={heatmapData}
            options={{
              radius: 36,
              opacity: 0.7,
              gradient: [
                'rgba(0,255,0,0)',
                '#639922', '#EF9F27', '#D85A30', '#E24B4A',
              ],
            }}
          />
        )}

        {geoTrees.map(tree => {
          const color = STATUS_COLORS[tree.status ?? 'healthy']
          const isSelected = tree.id === selectedId
          return (
            <Circle
              key={tree.id}
              center={{ lat: tree.lat, lng: tree.lng }}
              radius={tree.crownRadiusM ?? 5}
              options={{
                fillColor: color,
                fillOpacity: isSelected ? 0.95 : 0.7,
                strokeColor: isSelected ? '#ffffff' : color,
                strokeWeight: isSelected ? 3 : 1.5,
                clickable: true,
                zIndex: isSelected ? 1000 : 1,
              }}
              onClick={() => selectTree(isSelected ? null : tree.id)}
            />
          )
        })}
      </GoogleMap>

      {/* Top-left chip showing tree count + heatmap toggle */}
      <div style={{
        position: 'absolute', top: 12, left: 12,
        background: 'rgba(255,255,255,0.95)',
        borderRadius: 6, padding: '6px 10px',
        boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
        display: 'flex', alignItems: 'center', gap: 10,
        fontSize: 12, fontWeight: 600, color: '#1f2937',
      }}>
        <span>{geoTrees.length} GPS-pinned</span>
        <span style={{ color: '#d1d5db' }}>|</span>
        <label style={{
          display: 'flex', alignItems: 'center', gap: 4,
          cursor: 'pointer', userSelect: 'none', fontWeight: 500,
        }}>
          <input
            type="checkbox"
            checked={showHeatmap}
            onChange={(e) => setShowHeatmap(e.target.checked)}
            style={{ margin: 0 }}
          />
          Heatmap
        </label>
      </div>
    </div>
  )
}
