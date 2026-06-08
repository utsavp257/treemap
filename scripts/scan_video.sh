#!/usr/bin/env bash
# Upload a drone MP4 + (optional) SRT sidecar to /scan-video and poll until done.
#
# Usage:
#   ./scripts/scan_video.sh <crown|stem> <path/to/video.mp4> [path/to/telemetry.srt]
#
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <crown|stem> <video.mp4> [telemetry.srt]"
  exit 1
fi

MODE=$1
VIDEO=$2
SRT=${3:-}
BASE=${BASE:-http://localhost:8080}

# Use the project venv's Python so we get 3.12 (system python3 on macOS is 3.9).
PY=${PY:-.venv/bin/python}
if [ ! -x "$PY" ]; then
  PY=python3
fi

curl_args=( -s -F "video=@${VIDEO}" -F "mode=${MODE}" -F "fps=0.5" )
if [ -n "$SRT" ]; then
  curl_args+=( -F "srt=@${SRT}" )
fi

echo "→ Uploading ${VIDEO} as ${MODE}…"
RESP=$(curl "${curl_args[@]}" "${BASE}/scan-video")
JOB_ID=$("$PY" -c "import sys, json; print(json.loads(sys.argv[1])['job_id'])" "$RESP")
echo "  job_id = ${JOB_ID}"
echo "→ Polling /scan-video/${JOB_ID} every 3s…"

while true; do
  STATE=$(curl -s "${BASE}/scan-video/${JOB_ID}")
  read STATUS PROG_PCT COUNT < <("$PY" -c "
import sys, json
d = json.loads(sys.argv[1])
print(d['status'], int(round(d['progress'] * 100)), d['tracked_tree_count'])
" "$STATE")
  printf '  [%-12s] %3s%%   trees=%s\n' "$STATUS" "$PROG_PCT" "$COUNT"
  if [ "$STATUS" = "complete" ] || [ "$STATUS" = "failed" ]; then
    echo "$STATE" | "$PY" -m json.tool
    break
  fi
  sleep 3
done
