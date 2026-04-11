// src/components/Dashboard.jsx
import React from 'react'
import { useForestStore } from '../store/forestStore'
import { ForestMap } from './ForestMap'
import { TreePanel } from './TreePanel'
import logo from '../assets/logo.png'

const TOP_BAR_BG = '#1e1e1e'
// Dark green:   '#1a2e1a'
// Forest dark:  '#0d1f0d'
// Charcoal:     '#1e1e1e'
// Off white:    '#f5f5f0'
// Slate:        '#1e2d2e'

export function Dashboard() {
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

  const isDark = ['#1a2e1a','#0d1f0d','#1e1e1e','#1e2d2e'].includes(TOP_BAR_BG.toLowerCase())
  const textColor    = isDark ? '#ffffff' : '#111111'
  const subColor     = isDark ? '#aaaaaa' : '#888888'
  const dividerColor = isDark ? '#ffffff22' : '#e5e5e5'
  const borderColor  = isDark ? 'transparent' : '#e5e5e5'

  return (
    <div style={{
      position: 'fixed',
      top: 0, left: 0, right: 0, bottom: 0,
      display: 'flex',
      flexDirection: 'column',
      fontFamily: 'system-ui, sans-serif',
      overflow: 'hidden',
    }}>
      {/* ── Top bar ── */}
      <div style={{
        height: 52,
        background: TOP_BAR_BG,
        borderBottom: `1px solid ${borderColor}`,
        display: 'flex',
        alignItems: 'center',
        padding: '0 12px',
        zIndex: 200,
        flexShrink: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        width: '100%',
      }}>

        {/* Logo + wordmark */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          flexShrink: 0, marginRight: 10,
        }}>
          <img
            src={logo}
            alt="RootCause.ai"
            style={{ height: 28, width: 'auto', objectFit: 'contain' }}
          />
          <span style={{
            fontWeight: 800, fontSize: 14,
            color: isDark ? '#ffffff' : '#2D5A27',
            letterSpacing: '-0.3px', whiteSpace: 'nowrap',
          }}>
            RootCause.ai
          </span>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: scanPulse ? '#639922' : '#bbb',
            transition: 'background 0.6s',
            display: 'inline-block', flexShrink: 0,
          }} title="Live scanning" />
        </div>

        <div style={{
          width: 1, height: 26,
          background: dividerColor,
          flexShrink: 0, marginRight: 8,
        }} />

        {/* Stats row — fills remaining width, no overflow */}
        <div style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-evenly',
          minWidth: 0,
          overflow: 'hidden',
        }}>
          <DashStat label="Detected"      value={totalTrees}    textColor={textColor} subColor={subColor} />
          <Divider color={dividerColor} />
          <DashStat label="Healthy"       value={healthyCount}  textColor="#639922"   subColor={subColor} />
          <DashStat label="Monitor"       value={monitorCount}  textColor="#EF9F27"   subColor={subColor} />
          <DashStat label="Treat"         value={treatCount}    textColor="#D85A30"   subColor={subColor} />
          <DashStat label="Cut down"      value={cutCount}      textColor="#E24B4A"   subColor={subColor} />
          <Divider color={dividerColor} />
          <DashStat label="Infected"      value={infectedCount} textColor="#E24B4A"   subColor={subColor} />
          <DashStat label="Beetle"        value={beetleCount}   textColor="#A32D2D"   subColor={subColor} />
          <DashStat label="Fungal"        value={fungalCount}   textColor="#EF9F27"   subColor={subColor} />
          <Divider color={dividerColor} />
          <DashStat
            label="Area affected"
            value={`${areaAffectedPct.toFixed(1)}%`}
            textColor={
              areaAffectedPct > 20 ? '#E24B4A' :
              areaAffectedPct > 10 ? '#EF9F27' : '#639922'
            }
            subColor={subColor}
          />
        </div>
      </div>

      {/* ── Map + panel ── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <ForestMap />
        <TreePanel />
      </div>
    </div>
  )
}

function Divider({ color }) {
  return (
    <div style={{
      width: 1, height: 26,
      background: color || '#e5e5e5',
      flexShrink: 0, margin: '0 2px',
    }} />
  )
}

function DashStat({ label, value, textColor, subColor }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', flexShrink: 0, padding: '0 3px',
    }}>
      <span style={{ fontSize: 15, fontWeight: 700, color: textColor, lineHeight: 1 }}>
        {value}
      </span>
      <span style={{ fontSize: 9, color: subColor, marginTop: 2, whiteSpace: 'nowrap' }}>
        {label}
      </span>
    </div>
  )
}