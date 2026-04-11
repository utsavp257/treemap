// src/components/ForestMap.jsx
import React from 'react'
import { GoogleMap, Circle, HeatmapLayer, Polygon } from '@react-google-maps/api'
import { useForestStore } from '../store/forestStore'
import { discoverArea } from '../services/scoringService'

const MAP_CENTER = { lat: 36.022787, lng: -118.401180 }
const MAP_OPTIONS = {
  mapTypeId: 'satellite',
  disableDefaultUI: false,
  zoomControl: true,
  streetViewControl: false,
  tilt: 0,
}

const STATUS_COLORS = {
  healthy: '#639922',
  monitor: '#EF9F27',
  treat: '#D85A30',
  cut: '#E24B4A',
}

export function ForestMap() {
  const trees = useForestStore(s => s.trees)
  const selectTree = useForestStore(s => s.selectTree)
  const [zoom, setZoom] = React.useState(14)
  const mapRef = React.useRef(null)
  const scanTimeoutRef = React.useRef(null)
  const isScanningRef = React.useRef(false)

  const handleLoad = React.useCallback((map) => {
    mapRef.current = map

    map.addListener('zoom_changed', () => {
      setZoom(map.getZoom())
    })

    const triggerScan = () => {
      // Debounce — wait 1.5s after map stops moving before scanning
      clearTimeout(scanTimeoutRef.current)
      scanTimeoutRef.current = setTimeout(async () => {
        if (isScanningRef.current) return
        if (!mapRef.current) return

        const currentZoom = mapRef.current.getZoom()
        if (currentZoom < 15) return

        const center = mapRef.current.getCenter()
        const bounds = mapRef.current.getBounds()
        if (!bounds) return

        const viewportBounds = {
          north: bounds.getNorthEast().lat(),
          east:  bounds.getNorthEast().lng(),
          south: bounds.getSouthWest().lat(),
          west:  bounds.getSouthWest().lng(),
        }

        isScanningRef.current = true
        try {
          await discoverArea(center.lat(), center.lng(), viewportBounds, currentZoom)
        } finally {
          isScanningRef.current = false
        }
      }, 3000)
    }

    // Only scan when user stops panning/zooming — saves API quota
    map.addListener('idle', triggerScan)
  }, [])

  React.useEffect(() => {
    return () => {
      clearTimeout(scanTimeoutRef.current)
      if (mapRef.current && window.google) {
        window.google.maps.event.clearListeners(mapRef.current, 'idle')
      }
    }
  }, [])

  const showHeatmap = zoom < 16

  const heatmapData = React.useMemo(() => {
    if (!window.google) return []
    return trees.map(t => ({
      location: new window.google.maps.LatLng(t.lat, t.lng),
      weight: 1 - (t.healthScore ?? 100) / 100,
    }))
  }, [trees])

  const sickTrees = trees.filter(t => t.status === 'treat' || t.status === 'cut')

  const affectedBounds = React.useMemo(() => {
    if (sickTrees.length < 2) return null
    const lats = sickTrees.map(t => t.lat)
    const lngs = sickTrees.map(t => t.lng)
    const pad = 0.0002
    return [
      { lat: Math.min(...lats) - pad, lng: Math.min(...lngs) - pad },
      { lat: Math.max(...lats) + pad, lng: Math.min(...lngs) - pad },
      { lat: Math.max(...lats) + pad, lng: Math.max(...lngs) + pad },
      { lat: Math.min(...lats) - pad, lng: Math.max(...lngs) + pad },
    ]
  }, [sickTrees])

  return (
    <div id="forest-map-container" style={{ width: '100%', height: '100%' }}>
      <GoogleMap
        mapContainerStyle={{ width: '100%', height: '100%' }}
        center={MAP_CENTER}
        zoom={zoom}
        options={MAP_OPTIONS}
        onLoad={handleLoad}
      >
        {showHeatmap && heatmapData.length > 0 && (
          <HeatmapLayer
            data={heatmapData}
            options={{
              radius: 40,
              opacity: 0.7,
              gradient: [
                'rgba(0,255,0,0)',
                '#639922',
                '#EF9F27',
                '#D85A30',
                '#E24B4A',
              ],
            }}
          />
        )}

        {!showHeatmap && trees.map(tree => (
          <Circle
            key={tree.id}
            center={{ lat: tree.lat, lng: tree.lng }}
            radius={tree.crownRadiusM ?? 4}
            options={{
              fillColor: STATUS_COLORS[tree.status ?? 'healthy'],
              fillOpacity: 0.75,
              strokeColor: STATUS_COLORS[tree.status ?? 'healthy'],
              strokeWeight: 1.5,
              clickable: true,
            }}
            onClick={() => selectTree(tree.id)}
          />
        ))}

        {affectedBounds && (
          <Polygon
            paths={affectedBounds}
            options={{
              fillColor: '#E24B4A',
              fillOpacity: 0.08,
              strokeColor: '#E24B4A',
              strokeWeight: 1.5,
            }}
          />
        )}

        {!showHeatmap && sickTrees.map(tree => (
          <Circle
            key={`spread-${tree.id}`}
            center={{ lat: tree.lat, lng: tree.lng }}
            radius={tree.spreadRiskRadiusM ?? 15}
            options={{
              fillColor: '#E24B4A',
              fillOpacity: 0.06,
              strokeColor: '#E24B4A',
              strokeWeight: 1,
              clickable: false,
            }}
          />
        ))}
      </GoogleMap>
    </div>
  )
}