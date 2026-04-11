// src/services/geminiService.js

const GEMINI_KEY = import.meta.env.VITE_GEMINI_API_KEY
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-001:generateContent?key=${GEMINI_KEY}`

/**
 * Phase 3: Forest-level reasoning to get a prioritized action plan.
 */
export async function getForestActionPlan(sickTrees) {
  console.log(`[Gemini API] Using Key: ${GEMINI_KEY ? GEMINI_KEY.substring(0, 8) + '...' : 'undefined'}`)
  if (sickTrees.length === 0) return []

  const treeSummary = sickTrees.map(t =>
    `Tree ${t.id}: disease=${t.disease}, score=${t.healthScore}, spreadRisk=${t.spreadRiskScore}, neighbors=${t.neighborIds.length}`
  ).join('\n')

  const prompt = `
You are a forest manager AI. Here are all sick trees in the forest:

${treeSummary}

Respond ONLY with a valid JSON array (no markdown), ordered by urgency (highest first):
[
  {
    "treeId": "<id>",
    "priority": <1 = cut now, 2 = treat this week, 3 = monitor>,
    "urgencyReason": "<one sentence why>"
  }
]
`.trim()

  const body = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { temperature: 0.1, maxOutputTokens: 1024 }
  }

  try {
    const res = await fetch(GEMINI_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })

    const data = await res.json()
    const rawText = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "[]"
    const clean = rawText.replace(/```json|```/g, "").trim()
    return JSON.parse(clean)
  } catch {
    return []
  }
}
