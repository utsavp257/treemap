// src/services/soilService.js

export async function fetchSoilData(lat, lng) {
  // Step 1: get the map unit key (mukey) for this coordinate
  const queryBody = {
    query: `SELECT mu.mukey, mu.musym, mu.muname
            FROM mapunit mu
            INNER JOIN SDA_Get_Mukey_from_intersection_with_WktWgs84('point(${lng} ${lat})') res
              ON mu.mukey = res.mukey`,
    format: "JSON"
  }

  try {
    const res = await fetch('https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `query=${encodeURIComponent(queryBody.query)}&format=JSON`
    })
    const data = await res.json()
    const mukey = data?.Table?.[0]?.[0]

    if (!mukey) return defaultSoil()

    // Step 2: get soil properties for that map unit
    const propQuery = `
      SELECT ch.ph1to1h2o_r, ch.texture, ch.drainagecl, ch.om_r
      FROM chorizon ch
      INNER JOIN component co ON ch.cokey = co.cokey
      INNER JOIN mapunit mu ON co.mukey = mu.mukey
      WHERE mu.mukey = '${mukey}'
      ORDER BY ch.hzdept_r ASC
    `
    const propRes = await fetch('https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `query=${encodeURIComponent(propQuery)}&format=JSON`
    })
    const propData = await propRes.json()
    const row = propData?.Table?.[0]

    if (!row) return defaultSoil()

    return {
      ph: parseFloat(row[0]) || 6.5,
      texture: row[1] || 'loam',
      moistureClass: row[2] || 'well drained',
      organicMatter: omClass(parseFloat(row[3]))
    }
  } catch {
    return defaultSoil()
  }
}

function defaultSoil() {
  return { ph: 6.5, texture: 'loam', moistureClass: 'well drained', organicMatter: 'medium' }
}

function omClass(om) {
  if (isNaN(om) || om === null) return 'medium'
  if (om < 2) return 'low'
  if (om < 5) return 'medium'
  return 'high'
}
