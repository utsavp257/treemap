// src/components/Dashboard.jsx
import React from 'react'
import { useForestStore } from '../store/forestStore'
import { ForestMap } from './ForestMap'
import { TreePanel } from './TreePanel'
import { ModePicker } from './ModePicker'
import { VideoUpload } from './VideoUpload'
import { VideoResults } from './VideoResults'
import { VideoMap } from './VideoMap'
import logo from '../assets/logo.png'

const TOP_BAR_BG = '#1e1e1e'

export function Dashboard() {
  const mode = useForestStore(s => s.mode)
  const videoJob = useForestStore(s => s.videoJob)

  const totalTrees      = useForestStore(s => s.totalTrees)
  const healthyCount    = useForestStore(s => s.healthyCount)
  const monitorCount    = useForestStore(s => s.monitorCount)
  const treatCount      = useForestStore(s => s.treatCount)
  const cutCount        = useForestStore(s => s.cutCount)
  const infectedCount   = useForestStore(s => s.infectedCount)
  const beetleCount     = useForestStore(s => s.beetleCount)
  const fungalCount     = useForestStore(s => s.fungalCount)
  const areaAffectedPct = useForestStore(s => s.areaAffectedPct)

  const [scanPulse, setScanPulse] = React.useState(false)
  React.useEffect(() => {
    const t = setInterval(() => setScanPulse(p => !p), 2000)
    return () => clearInterval(t)
  }, [])

  const isVideoMode = mode !== 'satellite'

  return (
    <div style={{
      position: 'fixed',
      top: 0, left: 0, right: 0, bottom: 0,
      display: 'flex', flexDirection: 'column',
      fontFamily: 'system-ui, sans-serif',
      overflow: 'hidden',
    }}>
      {/* ── Top bar ───────────────────────────────────────────────────── */}
      <div style={{
        height: 56, background: TOP_BAR_BG,
        display: 'flex', alignItems: 'center', gap: 16,
        padding: '0 16px', zIndex: 200, flexShrink: 0, width: '100%',
      }}>
        {/* Logo + wordmark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <img
            src={logo}
            alt="RootCause.ai"
            style={{ height: 28, width: 'auto', objectFit: 'contain' }}
          />
          <span style={{
            fontWeight: 800, fontSize: 14,
            color: '#ffffff', letterSpacing: '-0.3px',
          }}>RootCause.ai</span>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: scanPulse ? '#639922' : '#475569',
            transition: 'background 0.6s', display: 'inline-block',
          }} title="Live" />
        </div>

        {/* Mode picker */}
        <ModePicker />

        <div style={{ width: 1, height: 26, background: '#ffffff22' }} />

        {/* Stats row */}
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center',
          justifyContent: 'space-evenly', minWidth: 0, overflow: 'hidden', gap: 4,
        }}>
          <DashStat label="Detected"    value={totalTrees}    textColor="#ffffff" />
          <Divider />
          <DashStat label="Healthy"     value={healthyCount}  textColor="#639922" />
          <DashStat label="Monitor"     value={monitorCount}  textColor="#EF9F27" />
          <DashStat label="Treat"       value={treatCount}    textColor="#D85A30" />
          <DashStat label="Cut down"    value={cutCount}      textColor="#E24B4A" />
          <Divider />
          <DashStat label="Infected"    value={infectedCount} textColor="#E24B4A" />
          <DashStat label="Beetle"      value={beetleCount}   textColor="#A32D2D" />
          <DashStat label="Fungal"      value={fungalCount}   textColor="#EF9F27" />
          <Divider />
          <DashStat
            label="Area affected"
            value={`${areaAffectedPct.toFixed(1)}%`}
            textColor={
              areaAffectedPct > 20 ? '#E24B4A'
              : areaAffectedPct > 10 ? '#EF9F27' : '#639922'
            }
          />
        </div>

        {/* Right-side action: video mode shows job status / new-scan button */}
        {isVideoMode && videoJob && (
          <>
            <div style={{ width: 1, height: 26, background: '#ffffff22' }} />
            <VideoUpload />
          </>
        )}
      </div>

      {/* ── Main view ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden',
        background: '#f9fafb' }}>
        {!isVideoMode && <ForestMap />}
        {isVideoMode && !videoJob && <VideoUpload />}
        {isVideoMode && videoJob && (
          <div style={{
            display: 'flex', flexDirection: 'column',
            height: '100%', width: '100%',
          }}>
            <div style={{ flex: '1 1 60%', minHeight: 0, overflow: 'hidden' }}>
              <VideoResults />
            </div>
            <div style={{ flex: '1 1 40%', minHeight: 0,
              borderTop: '1px solid #e5e7eb' }}>
              <VideoMap />
            </div>
          </div>
        )}
        <TreePanel />
      </div>
    </div>
  )
}

function Divider() {
  return (
    <div style={{
      width: 1, height: 26,
      background: '#ffffff22',
      flexShrink: 0, margin: '0 2px',
    }} />
  )
}

function DashStat({ label, value, textColor }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', flexShrink: 0, padding: '0 4px',
    }}>
      <span style={{ fontSize: 15, fontWeight: 700, color: textColor, lineHeight: 1 }}>
        {value}
      </span>
      <span style={{ fontSize: 9, color: '#9ca3af', marginTop: 2, whiteSpace: 'nowrap' }}>
        {label}
      </span>
    </div>
  )
}
