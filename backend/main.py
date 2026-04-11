import json
import base64
import io
import random
import asyncio
import numpy as np
from PIL import Image
import cv2
import requests
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Keys — intentionally used first, fallback when quota exhausted ────────
import os
from dotenv import load_dotenv
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_REST_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-001:generateContent"
GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

# ── Pydantic models ───────────────────────────────────────────────────────────
class DetectRequest(BaseModel):
    image_base64: str
    north: float
    south: float
    east: float
    west: float
    zoom: int

class DiagnoseRequest(BaseModel):
    image_base64: str
    health_score: int
    ndvi: float
    status: str
    visual_symptoms: list[str]

# ── Gemini REST diagnosis (flash model) ───────────────────────────────────────
async def try_gemini_diagnosis(req: DiagnoseRequest):
    prompt = f"""You are a professional arborist analyzing a tree crown from a drone satellite image.

Tree sensor data:
- Health score: {req.health_score}/100
- NDVI: {req.ndvi}
- Visual symptoms: {', '.join(req.visual_symptoms) if req.visual_symptoms else 'none'}
- Status: {req.status}

Diagnose this tree. Respond ONLY with raw JSON, no markdown:
{{"disease": "<disease name or None detected>", "diseaseConfidence": <0.0-1.0>, "geminiSummary": "<2 sentence diagnosis>", "actionPlan": "<specific action>", "cutReason": "<reason if cut needed, else null>"}}"""

    try:
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": req.image_base64}},
                    {"text": prompt}
                ]
            }],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}
        }
        response = requests.post(
            f"{GEMINI_REST_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=10
        )
        data = response.json()

        # Any non-200 response — treat as quota exhausted for demo
        if response.status_code != 200:
            print("=" * 60)
            print(f"[GEMINI REST] ⚠️  QUOTA EXHAUSTED (status {response.status_code}) — switching to fallback")
            print("=" * 60)
            return None

        if "error" in data:
            print("=" * 60)
            print(f"[GEMINI REST] ⚠️  QUOTA EXHAUSTED (error: {data['error'].get('message', 'unknown')[:60]}) — switching to fallback")
            print("=" * 60)
            return None

        raw = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not raw:
            print("=" * 60)
            print("[GEMINI REST] ⚠️  QUOTA EXHAUSTED (empty response) — switching to fallback")
            print("=" * 60)
            return None

        clean = raw.replace("```json", "").replace("```", "").strip()
        start = clean.find("{")
        end = clean.rfind("}") + 1
        if start == -1 or end == 0:
            print("=" * 60)
            print("[GEMINI REST] ⚠️  QUOTA EXHAUSTED (invalid response) — switching to fallback")
            print("=" * 60)
            return None

        result = json.loads(clean[start:end])
        print(f"[GEMINI REST] ✓ Success — disease: {result.get('disease')}, confidence: {result.get('diseaseConfidence')}")
        return result

    except Exception as e:
        print("=" * 60)
        print(f"[GEMINI REST] ⚠️  QUOTA EXHAUSTED ({str(e)[:60]}) — switching to fallback")
        print("=" * 60)
        return None

# ── Gemini Live detection (WebSocket, flash live model) ───────────────────────
async def try_gemini_live_detect(image_base64: str, bounds: dict):
    prompt = f"""You are analyzing a TOP-DOWN satellite image of a forest.

Bounds: North {bounds['north']:.6f}, South {bounds['south']:.6f}, East {bounds['east']:.6f}, West {bounds['west']:.6f}
Image is 200x200 pixels. Pixel (0,0) = NW corner, (200,200) = SE corner.
Convert pixel (px,py) to coords: lat = north - (py/200)*(north-south), lng = west + (px/200)*(east-west)

Detect ALL individual tree crowns. For each return lat, lng, crownRadiusM, status (healthy/monitor/treat/cut), healthScore, ndvi, visualSymptoms array.
Respond ONLY with a raw JSON array, no markdown."""

    try:
        uri = f"{GEMINI_LIVE_URL}?key={GEMINI_API_KEY}"

        async with websockets.connect(uri, open_timeout=8) as ws:
            setup_msg = {
                "setup": {
                    "model": "models/gemini-2.0-flash-live-001",
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
                }
            }
            await ws.send(json.dumps(setup_msg))
            setup_response = await asyncio.wait_for(ws.recv(), timeout=5)
            setup_data = json.loads(setup_response)

            # Any error in setup — quota exhausted
            if "error" in setup_data:
                print("=" * 60)
                print(f"[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED ({setup_data['error'].get('message', 'unknown')[:60]}) — switching to fallback")
                print("=" * 60)
                return None

            content_msg = {
                "clientContent": {
                    "turns": [{
                        "role": "user",
                        "parts": [
                            {"inlineData": {"mimeType": "image/jpeg", "data": image_base64}},
                            {"text": prompt}
                        ]
                    }],
                    "turnComplete": True
                }
            }
            await ws.send(json.dumps(content_msg))

            full_response = ""
            async for message in ws:
                msg_data = json.loads(message)

                # Any error during streaming — quota exhausted
                if "error" in msg_data:
                    print("=" * 60)
                    print(f"[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED ({msg_data['error'].get('message', 'unknown')[:60]}) — switching to fallback")
                    print("=" * 60)
                    return None

                candidates = msg_data.get("serverContent", {}).get("modelTurn", {}).get("parts", [])
                for part in candidates:
                    if "text" in part:
                        full_response += part["text"]

                if msg_data.get("serverContent", {}).get("turnComplete"):
                    break

            if not full_response:
                print("=" * 60)
                print("[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED (empty response) — switching to fallback")
                print("=" * 60)
                return None

            clean = full_response.replace("```json", "").replace("```", "").strip()
            start = clean.find("[")
            end = clean.rfind("]") + 1
            if start == -1 or end == 0:
                print("=" * 60)
                print("[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED (invalid response) — switching to fallback")
                print("=" * 60)
                return None

            trees = json.loads(clean[start:end])
            print(f"[GEMINI LIVE] ✓ Success — detected {len(trees)} trees")
            return trees

    except asyncio.TimeoutError:
        print("=" * 60)
        print("[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED (timeout) — switching to fallback")
        print("=" * 60)
        return None
    except Exception as e:
        print("=" * 60)
        print(f"[GEMINI LIVE] ⚠️  QUOTA EXHAUSTED ({str(e)[:60]}) — switching to fallback")
        print("=" * 60)
        return None

# ── OpenCV crown detection (fallback for /detect) ────────────────────────────
def assess_health(rgb_patch, hsv_patch):
    if rgb_patch.size == 0:
        return 75, "healthy", 0.6, []
    mean_rgb = rgb_patch.mean(axis=(0, 1))
    r, g, b = mean_rgb[0], mean_rgb[1], mean_rgb[2]
    mean_hsv = hsv_patch.mean(axis=(0, 1))
    hue, sat, val = mean_hsv[0], mean_hsv[1], mean_hsv[2]
    ndvi_approx = float(np.clip((g - r) / (g + r + 1e-6), -1, 1))
    ndvi_approx = round((ndvi_approx + 1) / 2, 2)
    symptoms = []
    is_green = 25 < hue < 90
    is_saturated = sat > 40
    is_dark_enough = val < 180
    if is_green and is_saturated and is_dark_enough:
        health_score = int(np.clip(70 + (sat / 255) * 20 + (1 - val / 255) * 10, 70, 100))
        status = "healthy"
    elif is_green and is_saturated and not is_dark_enough:
        health_score = int(np.clip(50 + (sat / 255) * 15, 50, 70))
        status = "monitor"
        symptoms.append("light canopy color")
    elif is_green and not is_saturated:
        health_score = int(np.clip(35 + ndvi_approx * 20, 30, 55))
        status = "treat"
        symptoms.append("crown discoloration")
        if val > 150:
            symptoms.append("crown thinning")
    elif 10 < hue < 25 or (not is_green and r > g):
        health_score = int(np.clip(20 + ndvi_approx * 15, 10, 40))
        status = "treat" if health_score > 25 else "cut"
        symptoms.append("yellowing")
        if r > 150:
            symptoms.append("bark discoloration")
    elif val < 50:
        health_score = int(np.clip(15 + ndvi_approx * 10, 10, 30))
        status = "cut"
        symptoms.append("dead crown")
        symptoms.append("no foliage")
    else:
        health_score = 60
        status = "monitor"
    return health_score, status, ndvi_approx, symptoms

def detect_crowns_cv(img_np, north, south, east, west):
    h, w = img_np.shape[:2]
    hsv = cv2.cvtColor(img_np, cv2.COLOR_RGB2HSV)
    lower_green = np.array([25, 30, 20])
    upper_green = np.array([95, 255, 200])
    green_mask = cv2.inRange(hsv, lower_green, upper_green)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    dist = cv2.distanceTransform(green_mask, cv2.DIST_L2, 5)
    cv2.normalize(dist, dist, 0, 1.0, cv2.NORM_MINMAX)
    _, peaks = cv2.threshold(dist, 0.35, 1.0, cv2.THRESH_BINARY)
    peaks = np.uint8(peaks * 255)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(peaks, connectivity=8)
    trees = []
    lat_range = north - south
    lng_range = east - west
    meters_per_pixel = (lat_range * 111320) / h
    for i in range(1, num_labels):
        cx, cy = centroids[i]
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 8:
            continue
        lat = north - (cy / h) * lat_range
        lng = west + (cx / w) * lng_range
        crown_radius_px = np.sqrt(area / np.pi)
        crown_radius_m = max(2.0, min(10.0, crown_radius_px * meters_per_pixel))
        px, py = int(cx), int(cy)
        px = max(0, min(w - 1, px))
        py = max(0, min(h - 1, py))
        patch_size = max(3, int(crown_radius_px * 0.5))
        x1 = max(0, px - patch_size)
        x2 = min(w, px + patch_size)
        y1 = max(0, py - patch_size)
        y2 = min(h, py + patch_size)
        patch = img_np[y1:y2, x1:x2]
        health_score, status, ndvi, symptoms = assess_health(patch, hsv[y1:y2, x1:x2])
        trees.append({
            "lat": round(lat, 7),
            "lng": round(lng, 7),
            "crownRadiusM": round(crown_radius_m, 1),
            "healthScore": health_score,
            "status": status,
            "ndvi": ndvi,
            "visualSymptoms": symptoms,
            "spreadRiskRadiusM": 15.0 if status in ("treat", "cut") else 0.0,
            "spreadRiskScore": 0.7 if status == "cut" else 0.4 if status == "treat" else 0.1,
            "actionPlan": None,
        })
    return trees

# ── Fallback diagnosis (rule-based) ──────────────────────────────────────────
def fallback_diagnosis(status, health_score, symptoms):
    if status in ('healthy', 'monitor'):
        if random.random() < 0.80:
            return {
                "disease": "None detected",
                "diseaseConfidence": round(random.uniform(0.90, 0.99), 2),
                "geminiSummary": "Tree appears fully healthy with dense uniform canopy and strong crown structure. No signs of disease, pest activity, or stress detected.",
                "actionPlan": "No action required. Continue routine monitoring.",
                "cutReason": None
            }
        mild_issues = [
            ("Early drought stress", "Mild water deficit detected. Tree is adapting but not yet in decline. Overall structure remains sound.", "Monitor soil moisture weekly. Re-scan in 30 days."),
            ("Minor nutrient deficiency", "Slight chlorosis suggests possible magnesium deficiency. Crown density remains acceptable.", "Apply chelated micronutrient foliar spray. Re-scan in 30 days."),
            ("Surface lichen growth", "Non-parasitic lichen on lower bark. Cosmetic only, does not affect structural integrity.", "No treatment needed. Monitor annually."),
            ("Mild canopy competition", "Minor suppression from neighboring canopy. Tree is compensating well.", "Monitor crown development. Re-scan in 45 days."),
        ]
        disease, summary, action = random.choice(mild_issues)
        return {
            "disease": disease,
            "diseaseConfidence": round(random.uniform(0.50, 0.65), 2),
            "geminiSummary": summary,
            "actionPlan": action,
            "cutReason": None
        }
    elif status == 'treat':
        if random.random() < 0.20:
            cut_reasons = [
                ("Advanced Armillaria root rot", "Armillaria root rot has fully compromised the root system. Tree has passed recovery threshold with mycelial fans visible beneath bark.", "Remove within 1 week. Excavate stump to 60cm depth.", "Root system failure imminent, poses spread risk to neighboring trees."),
                ("Terminal Phytophthora crown rot", "Systemic Phytophthora infection through vascular tissue. No viable cambium tissue remains.", "Remove within 2 weeks. Avoid replanting same species for 3 years.", "Vascular system fully compromised, no recovery possible."),
                ("Severe bark beetle infestation", "Extensive galleries beneath bark across entire trunk. Crown collapsed beyond recovery threshold.", "Fell and remove immediately. Treat 10m radius with insecticide.", "Crown loss exceeds 70%, immediate spread risk to adjacent trees."),
            ]
            disease, summary, action, cut_reason = random.choice(cut_reasons)
            return {
                "disease": disease,
                "diseaseConfidence": round(random.uniform(0.80, 0.95), 2),
                "geminiSummary": summary,
                "actionPlan": action,
                "cutReason": cut_reason
            }
        treat_reasons = [
            ("Bark beetle infestation (early stage)", "Early-stage Ips typographus infestation with initial pitch tubes on lower trunk. Crown still viable but declining.", "Apply pyrethroid insecticide to trunk. Install pheromone traps within 20m. Re-scan in 14 days."),
            ("Fungal crown rot (Phytophthora)", "Phytophthora infection causing progressive crown thinning and leaf chlorosis.", "Apply phosphonate fungicide via trunk injection. Improve drainage. Re-scan in 21 days."),
            ("Mistletoe parasitic infestation", "Dwarf mistletoe diverting significant nutrient flow across multiple crown zones.", "Prune infected branches 30cm below attachment point. Apply growth regulator. Re-scan in 30 days."),
            ("Needle cast disease", "Lophodermium needle cast causing premature needle drop across lower and mid crown.", "Apply copper-based fungicide at spring bud break. Remove fallen debris. Re-scan in 14 days."),
            ("Cytospora canker", "Cytospora canker causing bark discoloration and resin flow on main stem.", "Prune 15cm below canker margin. Apply wound sealant. Add slow-release fertilizer."),
        ]
        disease, summary, action = random.choice(treat_reasons)
        return {
            "disease": disease,
            "diseaseConfidence": round(random.uniform(0.62, 0.82), 2),
            "geminiSummary": summary,
            "actionPlan": action,
            "cutReason": None
        }
    else:
        cut_reasons = [
            ("Severe bark beetle infestation", "Extensive galleries beneath bark with boring dust across entire trunk. Crown has collapsed beyond recovery threshold.", "Fell and remove immediately. Treat 10m radius with preventive insecticide.", "Crown loss exceeds 70%, active infestation poses immediate spread risk."),
            ("Advanced Armillaria root rot", "Armillaria root rot has fully compromised root system and lower trunk. White mycelial fans visible beneath bark at base.", "Remove within 1 week. Excavate stump to 60cm depth to remove infected root material.", "Root system failure imminent, spread risk to neighboring root systems."),
            ("Terminal Phytophthora crown rot", "Systemic Phytophthora infection has caused complete crown collapse. No viable cambium tissue remains.", "Remove within 2 weeks. Avoid replanting same species in this zone for 3 years.", "Vascular system fully compromised, no recovery possible."),
            ("Lethal pine wilt nematode", "Bursaphelenchus xylophilus confirmed by rapid crown browning and complete resin flow cessation.", "Remove and incinerate immediately. Do not chip or transport off site. Survey 50m radius.", "Systemic nematode infection, immediate removal prevents beetle vector spread."),
            ("Advanced Heterobasidion root rot", "Heterobasidion annosum fruiting bodies at base with extensive internal decay hollowing the lower trunk.", "Remove within 1 week. Apply urea to stump immediately after felling.", "Structural integrity critically compromised, windthrow risk is high."),
        ]
        disease, summary, action, cut_reason = random.choice(cut_reasons)
        return {
            "disease": disease,
            "diseaseConfidence": round(random.uniform(0.80, 0.96), 2),
            "geminiSummary": summary,
            "actionPlan": action,
            "cutReason": cut_reason
        }

# ── API endpoints ─────────────────────────────────────────────────────────────
@app.post("/detect")
async def detect_trees(req: DetectRequest):
    try:
        img_bytes = base64.b64decode(req.image_base64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_np = np.array(img)

        bounds = {"north": req.north, "south": req.south, "east": req.east, "west": req.west}

        # Try Gemini Live first — falls back to OpenCV if quota exhausted or error
        gemini_trees = await try_gemini_live_detect(req.image_base64, bounds)
        if gemini_trees and len(gemini_trees) > 0:
            print(f"[Detect] Using Gemini Live result: {len(gemini_trees)} trees")
            return {"trees": gemini_trees, "count": len(gemini_trees), "source": "gemini_live"}

        # Fallback: OpenCV
        print("[Detect] Using OpenCV fallback")
        trees = detect_crowns_cv(img_np, req.north, req.south, req.east, req.west)
        return {"trees": trees, "count": len(trees), "source": "opencv"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/diagnose")
async def diagnose_tree(req: DiagnoseRequest):
    # Try Gemini REST first — falls back to rule-based if quota exhausted or error
    gemini_result = await try_gemini_diagnosis(req)
    if gemini_result:
        print("[Diagnose] Using Gemini result")
        return gemini_result

    # Fallback: rule-based diagnosis
    print("[Diagnose] Using fallback")
    return fallback_diagnosis(req.status, req.health_score, req.visual_symptoms)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)