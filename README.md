# RootCause.ai

> Gemini-powered, agentic forest health monitoring from drone video.

RootCause.ai ingests a drone MP4 (or satellite imagery) of a forest, detects every tree, and produces an **evidence-grounded per-tree diagnosis** — species, disease, action plan, confidence — with a full audit trail of how each diagnosis was reached.

Built end-to-end on Google Gemini 3.5 / 3.1 Flash, with a tool-using AI agent orchestrating ~19 atomic visual-QA tools served through an MCP server.

---

## What it does

1. **Upload** a drone MP4 — pick `crown` (top-down), `stem` (low-altitude trunk), or `satellite` mode
2. **Detect** — tiled Gemini detection across each sampled frame, with adaptive resolution from DJI SRT telemetry when present
3. **Triage** — a tier-1 cost gate (one cheap Gemini Flash call per non-healthy tree) classifies severity. Healthy and minor-stress trees fast-path with an informative diagnosis; severely compromised trees escalate to…
4. **Diagnose** — an MCP-driven agent loop calls visual-QA tools (`examine_crown`, `examine_stem`, `compare_to_reference`, etc.), each backed by a focused Gemini Flash-Lite prompt
5. **Synthesize** — a structured-output Gemini call produces the final `DiagnosisCore`: disease name, confidence, summary, action plan, plus the full chain of tool calls (the *evidence trace*) that produced it
6. **Map** — sick trees pin to a Google Maps satellite view via GPS projection from drone telemetry, so field crews know where to go

Every diagnosis is **honest about uncertainty**: confidence scores reflect what the evidence actually supports, and the evidence trace lets a human verify each conclusion.

---

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design doc — system overview, the cost-optimization layers that took per-video cost from ~$4 to ~$1, 19 documented design decisions with rationale, file layout, and phase history.

---

## Quick start

**Backend** (Python 3.12+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set GEMINI_API_KEY
cd ..
python -m backend.main
```

Backend serves at `http://localhost:8080`.

**Frontend** (Node 20+, new terminal):

```bash
npm install
npm run dev
```

Frontend at `http://localhost:5173`.

**External API keys** (all optional except Gemini):

- `GEMINI_API_KEY` — **required**. Get one at [aistudio.google.com](https://aistudio.google.com/)
- `PLANTNET_API_KEY` — optional. Improves species ID. Sign up at [my.plantnet.org](https://my.plantnet.org/)
- Google Maps Static API — optional. Used only for the satellite-mode map; vector tiles work without it

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, asyncio (no Celery / Redis — proof of concept) |
| AI | Google Gemini API |
| &nbsp;&nbsp;– Orchestrator + detection + triage | `gemini-3.5-flash` |
| &nbsp;&nbsp;– Atomic visual QA tools | `gemini-3.1-flash-lite` |
| Tool protocol | Model Context Protocol (MCP) over stdio, FastMCP server |
| Frontend | Vite 6, React 19, Zustand, react-google-maps/api |
| Video processing | ffmpeg (frame extraction, scale filter), Pillow (in-memory crops) |
| External APIs | Pl@ntNet (species ID), iNaturalist (reference images), Google Maps |

---

## Status

Proof-of-concept stage — single-user local development. No persistence, no auth, no live drone stream (offline MP4 only).

See [`ARCHITECTURE.md` §12](ARCHITECTURE.md#12-limitations-and-future-work) for the full list of limitations and the roadmap (next on deck: iNaturalist Computer Vision as a parallel species-ID source, Gemini context caching, drone-pose-aware crop selection).

---

## License

No license declared — this is a personal research project. If you want to use any of it, get in touch.
