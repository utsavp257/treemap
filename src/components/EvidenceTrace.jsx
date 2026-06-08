// src/components/EvidenceTrace.jsx
//
// Renders the agent's audit trail: which tool was called, what arguments,
// and the key fields from each tool's result. Each step is collapsible.

import React from 'react'

// One-line "headline" plucked from each tool's output. Kept narrow so
// the trace is scannable without clicking through.
function summarizeOutput(tool, output) {
  if (!output || typeof output !== 'object') return ''

  switch (tool) {
    case 'identify_species':
      return `${output.species || 'unknown'}${output.common_name ? ` (${output.common_name})` : ''} — conf ${Number(output.confidence || 0).toFixed(2)}`

    case 'lookup_species_baseline':
      return output.matched
        ? `${output.scientific_name}: density ${output.canopy_density_pct_healthy?.[0]}–${output.canopy_density_pct_healthy?.[1]}%`
        : `unmatched — fell back to generic baseline`

    case 'get_soil_suitability':
      return `${output.rating} (${output.score})`

    case 'assess_leaf_color':
      return `${output.dominant_hue}, chlorosis ${output.chlorosis_score}, necrosis ${output.necrosis_score}`

    case 'assess_canopy_density':
      return `${output.estimated_density_pct}% density, ${output.visible_gap_count} gaps${output.dieback_present ? ', dieback' : ''}`

    case 'detect_canopy_gaps':
      return `${output.gap_count} gaps, ${output.gap_severity} ${output.pattern}`

    case 'detect_dieback_pattern':
      return output.dieback_present
        ? `${output.severity} ${output.pattern}`
        : 'no dieback'

    case 'assess_bark_texture':
      return `${output.observed_texture}${output.anomalies?.length ? ` — ${output.anomalies.join(', ')}` : ''}`

    case 'detect_pitch_tubes':
    case 'detect_galleries':
    case 'detect_cankers':
    case 'detect_mycelial_fans':
    case 'detect_resin_flow':
    case 'detect_frass':
      return output.present
        ? `present${output.severity ? ` (${output.severity})` : ''}${output.count_estimate != null ? `, ~${output.count_estimate}` : ''}`
        : 'not detected'

    case 'compare_to_reference':
      return `similarity ${output.overall_similarity}${output.matches_reference_type ? ', matches refs' : ''}`

    case 'get_healthy_reference_images':
    case 'get_disease_reference_images':
      return `${output.image_urls?.length || 0} reference photos`

    case 'tier1_triage':
      return `severity ${Number(output.severity || 0).toFixed(2)}${
        output.confirmed_unhealthy ? ' · confirmed' : ' · false alarm'
      }${
        output.primary_indicators?.length
          ? ' · ' + output.primary_indicators.slice(0, 3).join(', ')
          : ''
      }`

    case 'examine_crown': {
      // Combine sub-tool headlines into one scannable line.
      const parts = []
      const lc = output.leaf_color
      if (lc && !lc.error) {
        parts.push(`leaves ${lc.dominant_hue || '?'} (chl ${lc.chlorosis_score ?? '?'})`)
      }
      const cd = output.canopy_density
      if (cd && !cd.error) {
        parts.push(`density ${cd.estimated_density_pct}%${cd.dieback_present ? ' + dieback' : ''}`)
      }
      const cg = output.canopy_gaps
      if (cg && !cg.error) {
        parts.push(`${cg.gap_count} gaps (${cg.gap_severity})`)
      }
      const db = output.dieback
      if (db && !db.error && db.dieback_present) {
        parts.push(`dieback: ${db.severity}/${db.pattern}`)
      }
      return parts.join(' · ') || 'no crown signals'
    }

    case 'examine_stem': {
      const parts = []
      const bk = output.bark_texture
      if (bk && !bk.error && bk.anomalies?.length) {
        parts.push(`bark: ${bk.anomalies.slice(0, 2).join(', ')}`)
      }
      for (const [key, label] of [
        ['pitch_tubes', 'pitch tubes'],
        ['galleries', 'galleries'],
        ['cankers', 'cankers'],
        ['mycelial_fans', 'mycelial fans'],
        ['resin_flow', 'resin'],
        ['frass', 'frass'],
      ]) {
        const v = output[key]
        if (v && !v.error && v.present) {
          parts.push(`${label}${v.severity ? `(${v.severity})` : ''}`)
        }
      }
      return parts.join(' · ') || 'no stem signals'
    }

    default:
      return ''
  }
}

function ToolBadge({ tool }) {
  const color =
    tool === 'tier1_triage' ? '#0d9488'
    : tool.startsWith('examine_') ? '#7c3aed'
    : tool.startsWith('identify') || tool.startsWith('lookup') || tool.startsWith('get_') ? '#2563eb'
    : tool.startsWith('assess') ? '#0891b2'
    : tool.startsWith('detect') ? '#9333ea'
    : tool === 'compare_to_reference' ? '#db2777'
    : '#64748b'
  return (
    <span style={{
      background: `${color}15`, color, fontSize: 10, fontWeight: 700,
      padding: '2px 7px', borderRadius: 4, fontFamily: 'monospace',
      letterSpacing: '-0.2px', whiteSpace: 'nowrap',
    }}>{tool}</span>
  )
}

function EvidenceStep({ step, index }) {
  const [expanded, setExpanded] = React.useState(false)
  const headline = summarizeOutput(step.tool, step.output)

  return (
    <div style={{
      borderLeft: '2px solid #e5e7eb',
      paddingLeft: 10, marginLeft: 4, marginBottom: 6,
    }}>
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          cursor: 'pointer', userSelect: 'none', padding: '4px 0',
        }}
      >
        <span style={{ fontSize: 10, color: '#9ca3af', width: 14, textAlign: 'right' }}>
          {index + 1}
        </span>
        <ToolBadge tool={step.tool} />
        <span style={{ flex: 1, fontSize: 12, color: '#374151', minWidth: 0,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {headline}
        </span>
        <span style={{ fontSize: 10, color: '#9ca3af' }}>{expanded ? '−' : '+'}</span>
      </div>

      {expanded && (
        <div style={{ marginTop: 4, marginBottom: 6, paddingLeft: 22 }}>
          {step.inputs_summary && (
            <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>
              <span style={{ color: '#9ca3af' }}>args:</span> {step.inputs_summary}
            </div>
          )}
          <pre style={{
            margin: 0, padding: '6px 8px',
            background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 4,
            fontSize: 10, lineHeight: 1.4, color: '#1f2937',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            maxHeight: 240, overflow: 'auto',
          }}>
            {JSON.stringify(step.output, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

export function EvidenceTrace({ trace }) {
  if (!trace || trace.length === 0) return null
  return (
    <div>
      <div style={{ fontSize: 11, color: '#888', marginBottom: 8 }}>
        Evidence trace ({trace.length} {trace.length === 1 ? 'step' : 'steps'})
      </div>
      <div>
        {trace.map((step, i) => (
          <EvidenceStep key={i} step={step} index={i} />
        ))}
      </div>
    </div>
  )
}
