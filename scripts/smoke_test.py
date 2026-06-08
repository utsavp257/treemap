"""Smoke-test the backend end-to-end without the frontend.

Walks through:
  1. /health — confirms models, ffmpeg, MCP server, tool list.
  2. /detect — sends a small sample satellite image, prints detected trees.
  3. /diagnose — sends a single tree crop, prints the agent's full
                 evidence trace + final diagnosis.

Run with the backend already running on localhost:8080:

    .venv/bin/python -m backend.main &
    .venv/bin/python scripts/smoke_test.py path/to/sample.jpg
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8080"


def fail(msg: str) -> None:
    print(f"\033[31m✗ {msg}\033[0m")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"\033[32m✓ {msg}\033[0m")


def section(msg: str) -> None:
    print(f"\n\033[1m── {msg} ──\033[0m")


def main(image_path: str) -> None:
    image_path_p = Path(image_path)
    if not image_path_p.exists():
        fail(f"Sample image not found: {image_path}")

    img_b64 = base64.b64encode(image_path_p.read_bytes()).decode("ascii")
    client = httpx.Client(base_url=BASE, timeout=180.0)

    # ── /health ──────────────────────────────────────────────────────────
    section("/health")
    try:
        r = client.get("/health")
        r.raise_for_status()
    except Exception as e:
        fail(f"/health failed — is the backend running on {BASE}? {e}")
    h = r.json()
    print(json.dumps(h, indent=2))
    if not h.get("mcp_server", {}).get("alive"):
        print("\033[33m⚠  MCP server not alive — /diagnose will use single-call fallback.\033[0m")
    else:
        ok(f"MCP server up with {len(h['mcp_server']['tools'])} tools")
    if not h.get("ffmpeg_available"):
        print("\033[33m⚠  ffmpeg missing — /scan-video will return 503.\033[0m")
    if not h.get("plantnet_configured"):
        print("\033[33m⚠  PLANTNET_API_KEY not set — species ID falls back to Gemini-only.\033[0m")

    # ── /detect ──────────────────────────────────────────────────────────
    section("/detect")
    detect_payload = {
        "image_base64": img_b64,
        # Arbitrary "tile bounds" — these are only used for pixel→lat/lng math.
        # For an arbitrary local file we just supply a small bounding box around (0,0).
        "north": 0.001,
        "south": -0.001,
        "east": 0.001,
        "west": -0.001,
        "zoom": 18,
    }
    r = client.post("/detect", json=detect_payload)
    if r.status_code != 200:
        fail(f"/detect failed: {r.status_code} {r.text[:200]}")
    detect = r.json()
    ok(f"/detect returned {detect['count']} trees")
    for t in detect["trees"][:3]:
        print(f"  • {t['label']:20s} status={t['status']:8s} score={t['healthScore']:3d} conf={t['detectionConfidence']:.2f}")
    if detect["count"] > 3:
        print(f"  … ({detect['count'] - 3} more)")

    # ── /diagnose ────────────────────────────────────────────────────────
    section("/diagnose (agent loop, may take 20-60s)")
    diag_payload = {
        "image_base64": img_b64,
        "status": "monitor",
        "visual_symptoms": ["discoloration"],
        "mode_hint": "crown",
    }
    r = client.post("/diagnose", json=diag_payload)
    if r.status_code != 200:
        fail(f"/diagnose failed: {r.status_code} {r.text[:500]}")
    d = r.json()

    ok(f"diagnosis: {d['disease']} (confidence {d['diseaseConfidence']:.2f})")
    if d.get("species"):
        print(f"  species: {d['species']} (conf {d.get('speciesConfidence') or 0:.2f})")
    print(f"  summary:    {d['summary']}")
    print(f"  actionPlan: {d['actionPlan']}")
    if d.get("cutReason"):
        print(f"  cutReason:  {d['cutReason']}")
    print(f"\n  Evidence trace ({len(d['evidenceTrace'])} steps):")
    for step in d["evidenceTrace"]:
        out_summary = ", ".join(
            f"{k}={v}" for k, v in list(step["output"].items())[:3]
        )
        print(f"   → {step['tool']:32s}  {step['inputs_summary']}")
        print(f"     ← {out_summary[:140]}")

    print("\n\033[32m✓ smoke test complete\033[0m")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/smoke_test.py <path-to-jpg>")
        sys.exit(1)
    main(sys.argv[1])
