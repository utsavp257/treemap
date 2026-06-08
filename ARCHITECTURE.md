# RootCause.ai — System Architecture

> Agentic, evidence-grounded forest health diagnosis from satellite imagery + drone video, powered by Gemini and an MCP-hosted diagnostic toolset.

---

## 1. What this is

A forest-monitoring system that ingests either a satellite map view or drone footage (top-down crown surveys or low-altitude trunk passes) and produces a structured health diagnosis for **every tracked tree**:

- Species identification
- Disease / stress hypothesis with calibrated confidence
- A reference-grounded summary citing specific visual observations
- A concrete action plan
- A full **evidence trace** — every tool call the AI agent made and what it returned, so a human can audit *why* the AI concluded what it did

The defining quality: **no claim is unanchored**. Confidence numbers and disease names exist only when a tool result supports them. There is no random data generation, no hallucinated arborist content — when the model can't reach a conclusion, the system says so.

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React)                                │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │  Mode picker (Satellite / Video → Crown|Stem)                            │ │
│ │  Map view (Google Maps satellite tiles)                                  │ │
│ │  Video upload — species hint, same-species toggle, resolution            │ │
│ │  Tree grid w/ crops · Tree panel with EVIDENCE TRACE per tool call       │ │
│ │  GPS-pinned map of sick trees (Path A)                                   │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ HTTP (Vite proxies /detect, /diagnose,
                                  │       /scan-video, /health)
┌─────────────────────────────────▼────────────────────────────────────────────┐
│                        BACKEND (FastAPI, Python 3.12)                        │
│                                                                              │
│  ┌────────────────────┐   ┌───────────────────────────────────────────────┐  │
│  │ /detect (satellite)│   │ /scan-video (background job + polling)        │  │
│  │  → spatial bbox    │   │  ffmpeg → frame extract → tiled 2×2 detect    │  │
│  │  → pixel→lat/lng   │   │  → IoU tracker → crops on full-res frames     │  │
│  │  → DetectResponse  │   │  → video-level species ID + baseline pre-fetch│  │
│  └────────────────────┘   │  → batch tier-1 triage (parallel)             │  │
│                            │  → per-tree diagnose                          │  │
│  ┌────────────────────┐   └───────────────┬───────────────────────────────┘  │
│  │ /diagnose          │                   │                                 │
│  │  (single tree)     │                   ▼                                 │
│  └────────┬───────────┘     ┌───────────────────────────────┐                │
│           │                 │      AGENT ORCHESTRATOR        │                │
│           └────────────────►│   Gemini 3.5 Flash + MCP loop  │                │
│                             │   Manual function-calling      │                │
│                             │   Image injection at dispatch  │                │
│                             │   Dedupe + 3-tier synthesis    │                │
│                             └────────────────┬──────────────┘                │
└──────────────────────────────────────────────┼───────────────────────────────┘
                                               │ stdio JSON-RPC
┌──────────────────────────────────────────────▼───────────────────────────────┐
│              MCP SERVER (subprocess, FastMCP, stdio transport)               │
│                                                                              │
│  Reference data (cheap, cached):                                             │
│    identify_species · lookup_species_baseline · get_soil_suitability         │
│    get_healthy_reference_images · get_disease_reference_images               │
│                                                                              │
│  Atomic visual QA (each = 1 focused Gemini Flash-Lite call):                 │
│    crown: assess_leaf_color · assess_canopy_density · detect_canopy_gaps     │
│           · detect_dieback_pattern                                           │
│    stem:  assess_bark_texture · detect_pitch_tubes · detect_galleries        │
│           · detect_cankers · detect_mycelial_fans · detect_resin_flow        │
│           · detect_frass                                                     │
│                                                                              │
│  MEGA-tools (parallel server-side fan-out — preferred):                      │
│    examine_crown  → 4 crown-side checks in one shot                          │
│    examine_stem   → 7 stem-side checks in one shot                           │
│                                                                              │
│  Cross-cutting:                                                              │
│    compare_to_reference (auto-fetches refs by species/disease)               │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
              External APIs:
                Pl@ntNet (species ID)   ·   iNaturalist (reference imagery)
                Gemini API              ·   Google Maps Static API
```

---

## 3. Operating modes

The app has **three independent input modes**, each with its own pipeline but converging on the same per-tree diagnosis flow.

| Mode | Input | Detection | Tracking | Per-tree diagnosis |
|---|---|---|---|---|
| **Satellite** | Map viewport at zoom ≥ 15 | Gemini spatial bbox on the 400×400 Static-Maps tile, pixel → lat/lng from tile bounds | None — each detection is unique | Lazy-fired on tree click (`/diagnose`) |
| **Video — Crown** | MP4 (top-down/oblique drone), optional DJI SRT sidecar | ffmpeg @ 0.5 fps → adaptive-resolution downsample → **tiled 2×2** Gemini spatial → IoU + centroid tracker | Stable per-video `track_id` | Tier-1 triage → optional full agent loop |
| **Video — Stem** | MP4 (low-altitude trunk pass) | Same flow, stem-specific detection prompt | Same tracker | Same tier-1 → agent path |

---

## 4. The per-tree diagnostic pipeline (the AI agent)

```
                           ┌──────────────────────────┐
                           │  tracked tree (crop)     │
                           │  + initial detection     │
                           │    status + symptoms     │
                           └────────────┬─────────────┘
                                        │
                                        ▼
                  ┌──────────────────────────────────────┐
                  │  status == "healthy"?                │
                  └─┬──────────────────────────────────┬─┘
                    │ yes                              │ no
                    ▼                                  ▼
        ┌─────────────────────┐         ┌────────────────────────────┐
        │ STOCK RESPONSE      │         │ TIER-1 TRIAGE              │
        │ "None detected"     │         │ 1 Gemini Flash-Lite call   │
        │ Cost: ~0 tokens     │         │ Calibrated severity 0-1    │
        └─────────────────────┘         │ Cost: ~1k tokens           │
                                        └────────────┬───────────────┘
                                                     │
                                ┌────────────────────┴─────────────────────┐
                                │ severity ≥ threshold (default 0.4)?      │
                                └─┬──────────────────────────────────────┬─┘
                                  │ no (most "monitor" trees)            │ yes (truly suspect)
                                  ▼                                      ▼
                ┌──────────────────────────────────┐    ┌────────────────────────────────┐
                │ FAST PATH                        │    │ FULL AGENT LOOP                │
                │ Tier-1 result IS the diagnosis   │    │ Gemini 3.5 Flash orchestrator  │
                │ - disease = "None detected"      │    │ + MCP toolbox                  │
                │   or "Minor variation…"          │    │ Phase A: tool-using exploration│
                │ - confidence calibrated by       │    │ Phase B: structured synthesis  │
                │   inverse severity               │    │   (3-tier robustness)          │
                │ - evidenceTrace: [tier1_triage]  │    │ evidenceTrace: tier1 + tools   │
                └──────────────────────────────────┘    └────────────────────────────────┘
```

### 4.1 Tier-1 triage (cost gate)

A single Gemini 3.5 Flash call gets:
- the crop image
- species + baseline pre-fetched at video level
- detection's initial status + symptoms (framed as *noisy hypothesis*, not fact)

Returns a strict Pydantic-validated `Tier1Triage`:

```python
{
  "confirmed_unhealthy": bool,
  "severity": float,                 # 0..1, calibrated against explicit anchors
  "primary_indicators": [...],       # short visible-sign labels
  "quick_assessment": "one sentence",
  "suggested_action": "one sentence"
}
```

If `severity < TIER1_SEVERITY_THRESHOLD` (default **0.80**) the result *becomes* the diagnosis. Otherwise the indicators are forwarded as enriched context to the full agent loop.

**Fast-path diagnoses are not minimal.** `diagnosis_from_tier1` reshapes the result into a real `DiagnosisCore` whose `disease` field surfaces the observed indicators per severity band:

| Severity band | Fast-path `disease` text | Default action |
|---|---|---|
| `0.00–0.20` | `"None detected"` | No action; tree appears healthy |
| `0.20–0.40` | `"Minor variation, within normal range"` | Continue routine monitoring |
| `0.40–0.60` | `"Mild crown stress (indicator1, indicator2)"` | Monitor on next scheduled survey |
| `0.60–0.80` | `"Moderate crown stress (indicator1, indicator2)"` | Schedule field inspection within 2 weeks |
| `≥ 0.80` | (escalated to full agent — no fast-path) | (deep diagnosis runs) |

This means borderline trees (0.6-0.8) get an *actionable* fast-path report — disease name, action, confidence, and indicators — without paying the ~$0.20 deep-diagnosis cost.

**Calibration discipline is critical.** The prompt:
1. Leads with the calibration anchors (`0.0-0.2 = false alarm`, etc.)
2. Includes a worked example showing "monitor + chlorosis but green canopy → 0.10, not 0.5"
3. Explicitly tells the model the upstream detector over-flags ~70% of trees
4. Frames the detection status as a hypothesis at the BOTTOM of the prompt, not as ground truth at the top
5. Quantifies the cost of false positives ($0.20 per wasted escalation) so the model has a calibration incentive

Earlier versions running Flash-Lite over-anchored on the detection priors — every non-healthy tree got severity ≥ 0.75 and the cost filter never fired. Switching to 3.5-flash + the restructured prompt restores honest calibration.

**Thinking mode is explicitly disabled** for tier-1 (`thinking_budget=0`). 3.5-flash defaults to internal reasoning that consumes the output token budget BEFORE writing the JSON, producing truncated half-written responses ~90% of the time at the original 512-token cap. With `thinking_budget=0` the model goes straight to structured output. This single config flag took tier-1 success from 2/16 to 15/15 on a representative dense video.

**Triage calls are batched in parallel** (semaphore-bounded `asyncio.gather`, default 8 concurrent) so a 50-tree video doesn't run 50 × 2s sequentially.

### 4.2 Full agent loop (when tier-1 escalates)

```
┌───────────────────────────────────────────────────────────────────┐
│                  AGENT LOOP (Phase A — exploration)               │
│                                                                   │
│   Initial context: image + species + baseline JSON + status       │
│                                                                   │
│   Turn 1 (default AUTO mode, baseline pre-injected):              │
│   ┌─ examine_crown(baseline=...)                                  │
│   │   └─ server-side asyncio.gather:                              │
│   │       • assess_leaf_color                                     │
│   │       • assess_canopy_density                                 │
│   │       • detect_canopy_gaps                                    │
│   │       • detect_dieback_pattern                                │
│   └─ Returns ONE merged dict — 4 sub-results, 1 orchestrator turn │
│                                                                   │
│   Turn 2 (optional, only on anomaly):                             │
│   └─ compare_to_reference(species="…", reference_type="healthy")  │
│       └─ Internally fetches iNat refs + multi-image comparison    │
│                                                                   │
│   Turn 3: short text summary                                      │
│                                                                   │
│   • Image stripped from context after turn 1 (cost saver)         │
│   • Identical function_calls deduped within a turn                │
└─────────────────────────────────┬─────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────┐
│              SYNTHESIS (Phase B — structured output)              │
│                                                                   │
│   3-tier robustness:                                              │
│     1. Primary: response_schema=DiagnosisCore                     │
│     2. Lenient: strip markdown fences, fill missing fields        │
│     3. Retry: explicit schema reminder + lower temperature        │
│     4. Fallback: build DiagnosisCore from evidence_trace alone    │
│                                                                   │
│   No diagnose call ever returns null — even on Gemini failure     │
│   we synthesize a labeled fallback from gathered tool outputs.    │
└─────────────────────────────────┬─────────────────────────────────┘
                                  │
                                  ▼
                       DiagnosisResult {
                         disease, diseaseConfidence,
                         summary, actionPlan, cutReason,
                         species, speciesConfidence,
                         evidenceTrace: [...]
                       }
```

---

## 5. The MCP server — every tool the agent can call

The MCP server is a **separate Python process** spawned by the FastAPI app's lifespan via stdio. The orchestrator talks to it through JSON-RPC. This isolates the tool surface from the API process and lets future agents (drone-flight planner, batch reporter, etc.) reuse the same toolkit without dragging in the web layer.

| Tool | What it does | Backed by | Typical cost |
|---|---|---|---|
| `identify_species` | Tree species ID from an image | Pl@ntNet (with Gemini tiebreak on close candidates) → Gemini fallback when Pl@ntNet absent | ~1 HTTP call + maybe 1 Flash call |
| `lookup_species_baseline` | Trait baseline (canopy %, hue, common diseases…) | Bundled JSON of curated species → Gemini Flash fallback for unknown species (cached in-process) | ~0 (cache hit) to ~1 Flash call |
| `get_soil_suitability` | Species × soil-texture score | Bundled matrix → Gemini fallback for unknown pairs | ~0 to ~1 Flash call |
| `get_healthy_reference_images` | URLs of healthy reference photos | iNaturalist research-grade observations | 1 HTTP call (cached) |
| `get_disease_reference_images` | URLs of disease photos | iNaturalist | 1 HTTP call (cached) |
| **`examine_crown`** ⭐ | All 4 crown atomic checks in parallel | `asyncio.gather` of sub-tools | 4 Flash-Lite calls fan-out |
| **`examine_stem`** ⭐ | All 7 stem atomic checks in parallel | `asyncio.gather` of sub-tools | 7 Flash-Lite calls fan-out |
| `assess_leaf_color` | Leaf hue, chlorosis, necrosis | 1 Flash-Lite call | ~$0.0003 |
| `assess_canopy_density` | % canopy, gaps, dieback presence | 1 Flash-Lite call | ~$0.0003 |
| `detect_canopy_gaps` | Gap count + spatial pattern | 1 Flash-Lite call | ~$0.0003 |
| `detect_dieback_pattern` | Dieback severity + spatial pattern | 1 Flash-Lite call | ~$0.0003 |
| `assess_bark_texture` | Bark descriptor + anomalies vs baseline | 1 Flash-Lite call | ~$0.0003 |
| `detect_pitch_tubes`/`galleries`/`cankers`/`mycelial_fans`/`resin_flow`/`frass` | Specific stem pathology detectors | 1 Flash-Lite call each | ~$0.0003 each |
| `compare_to_reference` | Multi-image reference grounding; **auto-fetches refs** by species/disease | iNat fetch + 1 multi-image Flash call | ~$0.001-0.002 |

⭐ = the **mega-tools**: agent's preferred path. They batch atomic calls server-side, dramatically reducing orchestrator turn count and the accumulating-context cost that dominates a multi-turn agent loop.

---

## 6. Cost & performance optimization layers

The system has been progressively tuned through several passes. Each layer compounds.

```
                Layer                              │  Per-video cost │  Why it helps
────────────────────────────────────────────────── │ ─────────────── │ ──────────────────────────
 Bare agent loop (initial)                         │  $3.50–4.00     │  baseline
 + Skip-healthy (status == "healthy")              │  $2.50–3.00     │  ~70% of trees are healthy
 + Pre-inject species + baseline                   │  $1.80–2.50     │  kills 30% repeat tool calls
 + Dedupe identical function_calls within turn     │  $1.60–2.20     │  defends vs model misbehavior
 + Merge ref fetch into compare_to_reference       │  $1.50–2.10     │  -1 turn per sick tree
 + 3-tier synthesis robustness                     │  same           │  prevents wasted runs
 + Tier-1 triage (Flash-Lite, threshold 0.4)       │  $2.50–3.20     │  ⚠ Lite over-anchored; rubber-stamped
 + Tier-1 calibration fix (3.5-flash, thresh 0.55) │  $2.80–3.20     │  ⚠ Tier-1 truncated by thinking mode
 + Tier-1 thinking_budget=0                        │  $2.20–3.00     │  ✅ Tier-1 actually returns valid JSON
 + Tier-1 threshold 0.55 → 0.80                    │  $1.10–1.50     │  ✅ moderate-stress trees fast-path
 + compare_to_reference hard cap (1/tree)          │  $1.00–1.40     │  -1 multi-image call when model misfires
 + Mega-tools (examine_crown/stem)                 │  $0.80–1.20     │  -3 turns per escalated tree
────────────────────────────────────────────────── │ ─────────────── │ ──────────────────────────
 Current realistic range (dense Aleppo-pine video) │  $1.00–1.50     │
 Healthy or low-detection videos                   │  $0.20–0.50     │
 With species hint filled in                       │  ~10-15% lower  │  skip video-level Pl@ntNet call
```

**Honest note on the curve.** Two cost layers above are corrections to *previously claimed* savings that didn't hold up under measurement:
- `Tier-1 calibration fix (3.5-flash)` was projected to drop to $0.80-1.30 but the live measurement showed $2.80-3.20 because tier-1 was silently failing on ~90% of trees (MAX_TOKENS truncation from thinking mode).
- `thinking_budget=0` was the actual fix that made tier-1 functional, but tier-1 still escalated most trees because **the trees genuinely are sick at severity 0.65-0.85** — the threshold needed raising to fast-path the moderate cases.

After all layers, a representative dense video (57 trees tracked, 15 non-healthy, 12 visibly stressed at severity ≥0.65, 2 standing-dead) costs **~$1.20-1.40**. A sparse healthy video runs $0.20-0.40.

### 6.1 The shape of a saved dollar

The single biggest cost in a deep diagnostic loop is **orchestrator turn input** — every turn re-sends all prior turns + system prompt + initial context. Every saved turn = ~5-8k tokens of accumulating context not re-billed. Optimizations target turn count, not raw call count.

### 6.2 Image cost optimization (detection)

```
Detection input pipeline:

  ffmpeg extracts at FULL native resolution to disk
            │
            ▼
  In-memory downsample to adaptive width:
    Without SRT altitude: 1280px
    With altitude (h):    target = 2·h·tan(40°)·(80px/5m crown),
                          clamped to [768, 1920]
            │
            ▼
  Tile 2×2 with 10% overlap → each tile ≈ 1 Gemini tile (~258 tokens)
            │
            ▼
  Concurrent Gemini calls per tile via asyncio.gather
            │
            ▼
  NMS dedup at IoU > 0.4 across tile seams
```

**Diagnosis decouples from detection resolution.** Per-tree atomic-tool crops are taken from the **full-resolution frame on disk** with 30% padding and floor at 256×256 — so the agent sees full detail even though detection ran on a tile-friendly downsample. This was the key fix to keep accuracy while cutting tokens.

### 6.3 Calibrated severity gate

Tier-1's prompt makes the calibration anchors explicit (`0.0-0.2 = false alarm`, `0.6-0.8 = clearly unhealthy`, etc.) and warns the model that false positives trigger expensive analysis. This biases toward honest "fine" calls instead of defensive over-flagging — which is the exact pathology that wasted budget before.

---

## 7. Key design decisions (and why)

### 7.1 MCP server, not in-process tools
**Decision:** The diagnostic tools live in a separate subprocess speaking MCP over stdio.

**Why:**
- Future agents (drone planner, batch reporter) can reuse the same toolset without coupling to the FastAPI app.
- A hung tool call can't take down the API.
- The protocol is well-defined and reusable by other LLM frameworks.

**Trade-off:** Inter-process JSON-RPC adds ~5-10ms latency per call. Negligible vs Gemini call latency.

### 7.2 Manual function-calling loop (not the SDK's `tools=[mcp_session]`)
**Decision:** We bypass google-genai's MCP convenience wrapper and write our own dispatch loop.

**Why:** The SDK's `_filter_to_supported_schema` chokes on JSON Schema with `"additionalProperties": false` (a bool where it expects a dict). FastMCP generates exactly those schemas. The convenience path silently produces a no-tools agent. We use `parameters_json_schema` directly on `FunctionDeclaration` instead.

### 7.3 Image injection at dispatch time
**Decision:** `image_base64`/`target_image_base64` parameters are **stripped from the tool schemas Gemini sees**. The orchestrator injects the real image at dispatch.

**Why:** Without this, the model hallucinates tiny placeholder base64 strings (we saw it identify a 1×1 transparent PNG as "English ivy"). Hiding the param from the model's schema makes the bug impossible.

### 7.4 Two-phase agent (exploration → synthesis)
**Decision:** Phase A uses tools freely with no response_schema. Phase B is a second Gemini call with no tools but `response_schema=DiagnosisCore`.

**Why:** Gemini doesn't reliably mix `tools=[...]` with `response_schema=...` in one call. Splitting keeps both behaviors at their best.

### 7.5 Tier-1 triage as a separate stage (not a tool)
**Decision:** Tier-1 is a pipeline-level pre-filter, not exposed as a tool the agent can call.

**Why:** It's a *gating* decision, not an evidence-gathering decision. Making it a tool would let the agent skip it — defeating the purpose. As a pipeline stage, it deterministically runs before the agent ever sees the tree.

### 7.6 Pre-inject species + baseline; drop the mandatory first tool
**Decision:** When video-level species ID succeeds, both the species name AND its trait baseline are injected directly into the agent's initial user prompt. The "force lookup_species_baseline on turn 1" requirement disappears.

**Why:** The agent was repeatedly calling `lookup_species_baseline` 4-5× per tree (parallel-call instruction misinterpretation). Injecting the result eliminates the temptation entirely.

### 7.7 Mega-tools collapse 4-7 atomic calls into 1 orchestrator turn
**Decision:** `examine_crown` and `examine_stem` run all relevant atomic tools server-side via `asyncio.gather`, return one merged dict.

**Why:** Atomic Gemini calls fire either way — but on the orchestrator side, going from 4-7 function_call/function_response pairs to 1 saves ~30-40% of accumulating-context tokens. Individual atomic tools remain registered as drill-down escape hatches.

### 7.8 Greedy NMS dedup in the dispatch loop
**Decision:** Within a single agent turn, identical `(name, args)` function calls execute exactly once; the result is replayed to every duplicate caller for protocol validity.

**Why:** Gemini 3.5 Flash occasionally batches the same call 4-5× in one response (misinterpreting "call independent tools in parallel"). Dispatch-level dedup makes this cost zero.

### 7.9 Tile-based detection over single-pass
**Decision:** Each video frame is split 2×2 with 10% overlap, detected concurrently, merged by NMS.

**Why:** Gemini's spatial detection has a practical per-image object cap (~20-30) regardless of `max_output_tokens`. Tiling gives each region of the scene its own enumeration budget — found 51 trees in a video where single-pass detection found 30.

### 7.10 3-tier synthesis robustness
**Decision:** Synthesis tries `response_schema` validation → lenient text parse → retry with schema reminder → fallback core built from evidence trace.

**Why:** A single bad JSON response previously wasted ~50k tokens (a whole agent loop's worth). Now no tree returns `diagnosis=null` if the agent gathered any evidence.

### 7.11 Bundled JSON baseline + Gemini fallback for unknown species
**Decision:** `lookup_species_baseline` and `get_soil_suitability` use a curated JSON file as a fast path, fall back to a Gemini Flash call for any species not in the file, and cache the result in-process.

**Why:** Scalable to any forest globally without manually curating thousands of species. Common species pay zero Gemini cost on lookup; rare species pay once and cache thereafter.

### 7.12 Adaptive detection resolution from drone telemetry
**Decision:** If the DJI SRT sidecar provides altitude, frame resolution is sized to give Gemini ~80 px per 5m crown (clamped 768-1920). Without SRT we default to 1280px.

**Why:** Gemini tokenizes images per 768×768 tile. There's a saturation point above which extra resolution buys zero detection accuracy but burns linear tokens. Matching resolution to scene scale puts every Gemini call near the sweet spot.

### 7.13 Frontend → polling-based video jobs
**Decision:** `/scan-video` returns a job_id immediately; processing runs as `asyncio.create_task`; the frontend polls `/scan-video/{job_id}` every 2s.

**Why:** Video processing takes 2-15 minutes. Synchronous HTTP would time out at the proxy. The job model also enables live progress updates (extracting → detecting → tracking → diagnosing → complete).

### 7.14 GPS-pinned map below results (Path A)
**Decision:** When SRT telemetry is present and the gimbal is approximately nadir, we project each tracked tree's bbox centroid to lat/lng using altitude + assumed 80° HFOV. Trees plot as colored circles on a Google Maps satellite view below the grid.

**Why:** Spatial layout matters operationally — ground crews dispatching to sick trees need a map, not a list. The projection isn't surveying-grade (~1-5m accuracy) but it's enough to navigate to the right tree.

### 7.15 Tier-1 calibration: 3.5-flash, not Flash-Lite
**Decision:** Tier-1 triage runs on `gemini-3.5-flash` by default, not Flash-Lite.

**Why:** Flash-Lite, despite explicit prompt anchoring, anchored too hard on the upstream detection's `monitor`/`treat`/`cut` status. Every non-healthy tree returned severity ≥ 0.75, defeating the entire cost-gate purpose. 3.5-flash follows calibration discipline reliably and weeds out ~50-70% of false-alarm escalations. The extra ~$0.025 spent on 13 triage calls saves $1.50+ in avoided full-agent runs.

**Trade-off:** ~5× more expensive per triage call (still tiny absolute cost). The threshold also raised from 0.4 to 0.55 to give the calibrated model room to identify borderline cases as borderline rather than escalating them.

### 7.16 Tier-1 prompt restructure (calibration-first)
**Decision:** The tier-1 prompt leads with calibration anchors + a worked example, and places the upstream detection verdict at the BOTTOM framed as "a noisy hypothesis to verify, not fact."

**Why:** When the detection status appears at the top of the prompt, the model reads it as ground truth and works backward to justify ≥ 0.5 severity. Burying it at the bottom AND explicitly stating "the detector over-flags ~70% of trees" AND including a worked false-alarm example breaks the anchoring. The prompt also quantifies the cost of false positives in dollar terms ("each false positive wastes $0.20") so the model has a calibration incentive baked in.

### 7.17 compare_to_reference hard cap (one per tree)
**Decision:** `compare_to_reference` is in a `_MAX_ONCE_PER_TREE` set in the dispatch loop. Second-or-later invocations within the same tree's agent loop short-circuit to a synthetic `{"skipped": true, "reason": "..."}` response — no Gemini call fires.

**Why:** The agent sometimes called `compare_to_reference` twice per tree (once with healthy refs, once with disease refs) — the priciest single tool in the toolkit (multi-image input). The cap forces the model to pick its single best comparison rather than fish with both. System prompt mirrors the cap.

**Trade-off:** Loses the (rare) case where two comparisons would genuinely help. In practice the agent's *first* comparison overwhelmingly answers the question; a second is usually fishing for confirmation rather than new signal.

### 7.18 Tier-1 thinking_budget=0 (the silent-failure fix)
**Decision:** The tier-1 Gemini call passes `thinking_budget=0` via `ThinkingConfig`, disabling 3.5-flash's internal reasoning phase.

**Why:** Gemini 3.5-flash defaults to a "thinking" mode where the model spends output tokens on internal reasoning BEFORE emitting the structured response. At the original `max_output_tokens=512`, thinking consumed the entire budget and JSON output was truncated to ~20-300 bytes. The structured-output parser rejected every call. Tier-1 was *silently* falling through to the full agent loop on ~90% of trees — paying the tier-1 cost AND the full-agent cost. Smoking gun: log line `Tier-1 batch complete: 2 trees triaged (of 16 non-healthy)`. After the fix: `15 trees triaged (of 15 non-healthy)`.

**Trade-off:** Loses the marginal accuracy benefit of in-model reasoning. For triage (one calibrated severity score from one image) the model doesn't need it — the calibration anchors and worked example in the prompt do the heavy lifting. The flag is per-call, so the synthesis step retains thinking by default.

### 7.19 Threshold 0.80, with informative fast-path text
**Decision:** `TIER1_SEVERITY_THRESHOLD` raised from 0.55 → 0.80. `diagnosis_from_tier1` was simultaneously upgraded to produce informative disease text per severity band rather than a single "Borderline — triage only" stub.

**Why:** Live measurement on a representative dense video showed that ~80% of escalated trees scored 0.65-0.79 — visibly stressed but not severely compromised. Deep-diagnosing every one of these costs ~$0.20/tree while adding marginal information over the tier-1 indicators that are already in hand. Raising the threshold halves escalations on dense videos without losing accuracy on the trees that *actually* need a deep look (0.8+). The fast-path text now surfaces the tier-1 `primary_indicators` directly in the `disease` field ("Moderate crown stress (chlorosis, canopy thinning)") so a fast-path diagnosis is still actionable.

**Trade-off:** Moderate-stress trees no longer get reference-image comparison or a confirmed pathology name. They get the indicators, a severity score, and a "schedule field inspection within 2 weeks" action. Crews dispatching to dying trees still get the full deep diagnosis. Set `TIER1_SEVERITY_THRESHOLD=0.55` to revert if a use case demands deep-diagnosing every borderline tree.

---

## 8. Technology stack

```
Language                  Python 3.12     +   JavaScript ES2022 / React 19
Frameworks/libs           FastAPI         +   Vite 6, react-google-maps/api
Background processing     asyncio (in-process tasks; no Celery/Redis)
AI                        Google Gemini API
  - Orchestrator:           gemini-3.5-flash (configurable via env)
  - Detection (tiles):      gemini-3.5-flash (configurable)
  - Atomic visual QA:       gemini-3.1-flash-lite (cheap, focused)
  - Tier-1 triage:          gemini-3.5-flash + thinking_budget=0 (calibration over speed)
Tool protocol             MCP (Model Context Protocol)
  - Server                  FastMCP, stdio transport
  - Client                  mcp Python SDK
External APIs             Pl@ntNet (species ID), iNaturalist (refs),
                          Google Maps Static API (satellite tiles)
Video processing          ffmpeg subprocess (frame extraction, scale filter)
Image                     Pillow (in-memory downsample, crops, upscale to floor)
Persistence               In-memory job state + tempdir per video
                          (no DB — proof of concept stage)
```

---

## 9. File layout

```
treemap/
├── backend/
│   ├── main.py                     FastAPI app: /detect /diagnose /scan-video /health
│   ├── config.py                   env-driven config (model IDs, cost knobs, tier-1)
│   ├── schemas.py                  Pydantic contracts (DiagnoseRequest, DiagnosisResult, VideoJob…)
│   ├── errors.py                   Typed Gemini errors (Quota / Invalid / Transport)
│   ├── gemini_client.py            google-genai wrapper, lenient JSON parsing, error mapping
│   ├── mcp_client.py               Persistent MCP stdio client, lifespan-managed
│   ├── agent/
│   │   ├── orchestrator.py         Manual function-calling loop, 3-tier synthesis
│   │   └── triage.py               Tier-1 single-call classifier
│   ├── detection/
│   │   └── satellite.py            Gemini spatial-bbox + pixel→lat/lng for satellite mode
│   ├── diagnosis/
│   │   └── single_call.py          Single-shot diagnosis fallback (when MCP unavailable)
│   ├── video/
│   │   ├── extract.py              ffmpeg, DJI SRT parsing, altitude-adaptive resolution
│   │   ├── detector.py             Per-frame + tiled Gemini detection
│   │   ├── tracker.py              IoU + centroid track manager
│   │   ├── projection.py           Nadir pixel → lat/lng for GPS-mapped results
│   │   └── pipeline.py             End-to-end: extract → detect → track → triage → diagnose
│   └── mcp_server/
│       ├── server.py               FastMCP entry; all tool registrations
│       ├── tools/
│       │   ├── species_id.py         Pl@ntNet + Gemini tiebreak
│       │   ├── baselines.py          Bundled JSON + Gemini fallback
│       │   ├── references.py         iNaturalist reference fetching (cached)
│       │   ├── visual_crown.py       4 crown-side atomic Gemini calls
│       │   ├── visual_stem.py        7 stem-side atomic Gemini calls
│       │   └── visual_compare.py     Multi-image reference comparison (auto-fetches)
│       └── data/
│           ├── species_baselines.json   31 curated species + generic anchor
│           └── soil_suitability.json    species × 8 soil-texture scoring matrix
│
├── src/                            React frontend
│   ├── App.jsx                     Google Maps script load + Dashboard mount
│   ├── components/
│   │   ├── Dashboard.jsx           Top bar (mode picker + stats) + main view router
│   │   ├── ModePicker.jsx          Satellite | Video → Crown/Stem segmented control
│   │   ├── ForestMap.jsx           Satellite-mode Google Maps + debounced /detect
│   │   ├── VideoUpload.jsx         MP4 + SRT + species hint + same-species toggle
│   │   ├── VideoResults.jsx        Grid of tree cards (crop + status + headline)
│   │   ├── VideoMap.jsx            GPS-pinned satellite map below the grid (Path A)
│   │   ├── TreePanel.jsx           Right-side drawer with evidence trace
│   │   └── EvidenceTrace.jsx       Collapsible per-tool-call audit trail
│   ├── services/
│   │   ├── scoringService.js       /detect orchestration for satellite mode
│   │   └── videoService.js         /scan-video upload + polling
│   └── store/
│       └── forestStore.js          Zustand store: mode, trees, selectedTreeId, videoJob
│
├── scripts/
│   ├── smoke_test.py               Hits /health → /detect → /diagnose on a sample image
│   └── scan_video.sh               Uploads MP4 + polls /scan-video until done
│
├── public/                         Static assets (demo MP4s, drone forest stills)
├── package.json                    onnxruntime-web purged; minimal frontend deps
├── vite.config.js                  Proxies /detect /diagnose /health /scan-video → :8080
└── ARCHITECTURE.md                 ← this file
```

---

## 10. Configuration (env vars)

All cost/quality knobs are env-driven so they can be tuned without code changes.

```
# Required
GEMINI_API_KEY=...

# Optional — better species ID
PLANTNET_API_KEY=...

# Model selection (env-swappable as new Gemini releases ship)
GEMINI_ORCHESTRATOR_MODEL=gemini-3.5-flash
GEMINI_DETECTION_MODEL=gemini-3.5-flash
GEMINI_FLASH_MODEL=gemini-3.1-flash-lite      # atomic tools (in mega-tools)
GEMINI_DIAGNOSIS_MODEL=gemini-3.5-flash       # single-call fallback when MCP down

# Cost / quality knobs
MAX_TOOL_CALLS=6                    # cap on agent's tool calls per tree
SKIP_HEALTHY_DIAGNOSIS=true         # detection-flagged-healthy trees → stock response
TIER1_ENABLED=true
TIER1_MODEL=gemini-3.5-flash        # Lite over-anchored on detection priors; 3.5 calibrates honestly
TIER1_SEVERITY_THRESHOLD=0.80       # below this → tier-1-only fast-path, above → escalate to deep agent
                                    # 0.80 default surfaces indicators for moderate stress (0.55-0.79)
                                    # without paying for deep diagnosis; set 0.55 to deep-diagnose every
                                    # confirmed-stressed tree (3-4× per-video cost)

# Server
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8080
```

---

## 11. Phase history — what was built when

| Phase | What landed |
|---|---|
| **1 — Foundation** | Stripped the original codebase of random-disease generators, OpenCV fallbacks, unused YOLOv8 ONNX, "QUOTA EXHAUSTED" mislabels. Scaffolded the agent orchestrator module structure and typed Gemini errors. |
| **2 — Reference toolset** | MCP server subprocess. Bundled 31-species baseline + soil matrix. Pl@ntNet + iNaturalist integrations. |
| **3 — Atomic visual QA** | 12 focused Gemini-Flash-backed tools (4 crown, 7 stem, 1 cross-cutting comparison). Each a tight prompt + Pydantic schema. |
| **4 — Orchestrator wiring** | Hand-rolled MCP→Gemini bridge to bypass SDK's broken schema converter. Two-phase agent (exploration + synthesis). Image injection at dispatch time. Evidence trace from `automatic_function_calling_history`. |
| **5 — Video pipeline** | ffmpeg frame extraction, DJI SRT parsing, IoU tracker, GPS projection for nadir crown footage, background job + polling endpoint. |
| **6 — Frontend rebuild** | Mode picker, video upload UI, evidence-trace renderer, GPS-pinned map (Path A), Tier-1/mega-tool summarizers. |
| **Optimization pass 1** | Pre-injected baseline · function-call dedup · merged reference fetch · 3-tier synthesis robustness · Tier-1 triage · parallel triage batching · mega-tools. |
| **Optimization pass 2 (calibration)** | Tier-1 calibration fix: 3.5-flash + restructured prompt (calibration anchors first, detection verdict last) + threshold 0.55. compare_to_reference hard cap to one call per tree. Result on dense 50-tree video: tier-1 rubber-stamping → ~50-70% honest fast-path rate; ~4× cost reduction. |
| **Optimization pass 3 (the silent-failure fix)** | Live measurement caught tier-1 truncating mid-JSON on ~90% of trees: 3.5-flash's default thinking mode was consuming the 512-token output budget BEFORE writing the structured response. Added `thinking_budget` parameter to `generate_structured`; tier-1 now uses `thinking_budget=0` + `max_output_tokens=2048`. Tier-1 success rate went 2/16 → 15/15 on the representative dense video. |
| **Optimization pass 4 (threshold + fast-path UX)** | With tier-1 actually functioning, raised `TIER1_SEVERITY_THRESHOLD` from 0.55 → 0.80 to fast-path the moderate-stress cases (0.65-0.79) that dominate dense-video diagnoses. Rewrote `diagnosis_from_tier1` to produce informative disease text per severity band — "Moderate crown stress (chlorosis, canopy thinning)" instead of "Borderline — triage only". Same dense video: $3.19 → ~$1.20-1.40 per video. |

---

## 12. Limitations and future work

**Known limitations (deliberate for proof-of-concept):**

- **No persistent storage.** Job state and tempdirs live in-memory and tempfile. Restart = forget.
- **No auth / multi-tenancy.** Single-user local dev.
- **Pl@ntNet hardcoded to `organs=leaf`.** Stem-mode videos feed Pl@ntNet bark/trunk imagery but ask it to match leaf photos. Confidence drops to 0.4-0.6 on stem footage. The Pl@ntNet API supports `organs=bark` (and `habit` for whole-tree views) — we just don't pass the mode-appropriate value. See §12 backlog for the fix.
- **Species ID skews to leaf close-ups.** Pl@ntNet's training corpus is heavily weighted toward hand-held leaf photos; on aerial drone canopy views the model is stretching past its training distribution. Gemini fallback is often more accurate on aerial imagery than Pl@ntNet itself.
- **Crown ↔ stem video aren't linked.** Each video produces an independent tree list — no fusion across angles for the same physical tree.
- **No live drone stream.** Offline MP4 only.

**Realistic next steps:**

| Lever | Effort | Where it helps |
|---|---|---|
| **iNaturalist Computer Vision API as parallel species source** + fix Pl@ntNet `organs` to track mode_hint | ~1 hour | Better aerial / bark / habit coverage than Pl@ntNet alone. Both APIs free. Take higher-confidence result; Gemini fallback unchanged. (Tracked as Task #28.) |
| Gemini explicit context caching | ~1 day (needs 32k-token cache prefix) | Cuts orchestrator-turn input ~50% on multi-tree videos |
| Persistent species baseline cache to disk | 1 hour | Cold-start cost for unknown species drops to zero across restarts |
| Drone-pose-aware crop selection (best frame per tree) | half day | Better atomic-tool inputs → fewer escalations from borderline triage |
| Crown↔stem fusion via SRT timeline alignment | 1-2 days | One diagnosis per physical tree combining both views |
| Backend job queue (Redis/Celery) | 1 day | Multi-user, multi-video parallelism beyond what asyncio gives |
| Persisted job results + history | 1 day | Re-open old diagnoses without re-running |

---

## 13. The system in one sentence

A scalable, evidence-grounded, MCP-driven agent that takes drone or satellite imagery of a forest and produces species-aware, baseline-anchored, action-oriented diagnoses for every tree it can detect — with cost gates at every layer so a 50-tree video runs for cents, not dollars.
