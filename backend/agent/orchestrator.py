"""Agent orchestrator — Gemini driving the MCP toolset.

We deliberately do NOT use google-genai's `tools=[mcp_session]` convenience.
That path runs the SDK's MCP→Gemini schema converter (`_filter_to_supported_schema`),
which crashes on any JSON Schema with `additionalProperties: false` (a
boolean). FastMCP generates exactly those schemas, so the convenience
path silently fails and falls back to a no-tool answer.

Instead:
  1. We list the MCP tools ourselves.
  2. We hand-build `FunctionDeclaration`s using `parameters_json_schema`,
     the API-native JSON Schema parameter (Google's own log message
     recommends this).
  3. We run a manual function-calling loop, dispatching each `function_call`
     back to MCP and feeding the result into the next turn.
  4. The first turn uses `mode=ANY` with `allowed_function_names` to
     force the mandatory species-baseline lookups; subsequent turns use
     `AUTO` so the model can stop when it has enough evidence.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Optional

from google.genai import types

from ..config import CONFIG
from ..diagnosis.single_call import diagnose as _single_call_diagnose
from ..gemini_client import client
from ..mcp_client import mcp_client
from ..schemas import DiagnoseRequest, DiagnosisCore, DiagnosisResult, EvidenceStep

logger = logging.getLogger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────────
_SYSTEM_INSTRUCTION = """You are a senior arborist and forest pathologist diagnosing trees via a toolbox of diagnostic tools. Tool descriptions and signatures are available in your function declarations — use them.

NON-NEGOTIABLES:
1. Every diagnosis must be grounded in tool results. Image-only conclusions are not acceptable.
2. Image params (image_base64, target_image_base64) are auto-injected by the runtime. Never pass them; they are deliberately absent from your tool schemas.
3. Pick crown-side OR stem-side atomic tools based on perspective — not both.
4. Don't invent specific pathogen names without a tool result that supports them.

EFFICIENCY:
- **PREFER THE MEGA-TOOLS for atomic checks.** They run all the relevant atomic checks in parallel server-side and return one merged dict.
  - examine_crown(baseline=...) for top-down / oblique crown views — covers leaf color, canopy density, canopy gaps, dieback.
  - examine_stem(baseline=...) for low-altitude trunk views — covers bark texture, pitch tubes, galleries, cankers, mycelial fans, resin flow, frass.
  Use individual atomic tools ONLY for follow-up drill-down on a specific signal the mega-tool already surfaced.
- For reference grounding use compare_to_reference DIRECTLY with species (healthy) or disease (disease) — it fetches the refs internally. Do NOT call get_healthy_reference_images or get_disease_reference_images separately.
- Only run compare_to_reference after the mega-tool has flagged a real anomaly. Don't fish.
- compare_to_reference is HARD-CAPPED at ONE CALL per tree. A second call returns a "skipped" stub; do not waste turns on it. Pick the single comparison that best resolves the diagnosis (healthy template OR a specific disease candidate, not both).
- Never re-emit the same call twice in one turn.
- Stop after 2-4 tool calls. Quality > volume.

When you have enough evidence, emit a short plain-text summary. The system then asks you for the final structured diagnosis as JSON.
"""


_USER_PROMPT_AUTOSPEC = """A tree has been flagged with these initial observations:
- Status hint from detection pass: {status}
- Visible symptoms previously noted: {symptoms}
- View perspective: {mode_hint}

PERSPECTIVE GUIDANCE:
- "satellite" / "crown": top-down view. Use crown-side tools.
- "stem": low-altitude trunk close-up. Use stem-side tools.
- "unspecified": choose tools based on what you see.

Start by calling identify_species and lookup_species_baseline, then run focused visual tools. Begin tool calls now."""


_USER_PROMPT_HINTED = """A tree has been flagged with these initial observations:
- Status hint from detection pass: {status}
- Visible symptoms previously noted: {symptoms}
- View perspective: {mode_hint}
- USER-PROVIDED SPECIES: "{species_hint}"

The user has identified this tree's species as "{species_hint}". Treat this as authoritative — do NOT call identify_species. Start with lookup_species_baseline(species="{species_hint}") and then proceed with the visual tools that fit the view perspective.

PERSPECTIVE GUIDANCE:
- "satellite" / "crown": top-down view. Use crown-side tools.
- "stem": low-altitude trunk close-up. Use stem-side tools.
- "unspecified": choose tools based on what you see.

Begin tool calls now."""


_USER_PROMPT_PRELOADED = """A tree has been flagged with these initial observations:
- Status hint from detection pass: {status}
- Visible symptoms previously noted: {symptoms}
- View perspective: {mode_hint}
- Species (already confirmed): "{species_hint}"
- Species baseline (already fetched — do NOT call lookup_species_baseline):

{baseline_json}

You already have the species and its trait baseline. Pass the baseline above into the mega-tool's `baseline` argument.

PERSPECTIVE GUIDANCE:
- "satellite" / "crown": top-down view → call examine_crown(baseline=<above>) in turn 1.
- "stem": low-altitude trunk close-up → call examine_stem(baseline=<above>) in turn 1.
- "unspecified": pick the mega-tool that matches what you see.

After the mega-tool returns, if anomalies are clearly flagged, call compare_to_reference(species=..., reference_type='healthy') in turn 2 for reference grounding. Then summarize. Aim for 2-3 tool calls total. Begin now."""


_SYNTHESIS_PROMPT = """Based on all the evidence gathered above, produce the final structured diagnosis.

Calibration rules:
- diseaseConfidence > 0.8 requires a reference-grounded match (compare_to_reference returned matches_reference_type=true for a disease reference set).
- diseaseConfidence between 0.5-0.8 means atomic tools flagged consistent symptoms but no reference match was done or matched.
- diseaseConfidence < 0.5 means inconclusive — say so.
- If no clear disease is visible, set disease to "None detected" with high confidence and recommend continued monitoring.
- The summary MUST cite at least one specific tool finding.
- cutReason is null unless removal is the recommended action.
- species/speciesConfidence come from the identify_species tool result (or null if you never called it)."""


# Tools the agent is forced to call on its first turn.
_MANDATORY_FIRST_TOOLS = ["identify_species", "lookup_species_baseline"]


# ── Helpers ──────────────────────────────────────────────────────────────
def _image_part(image_base64: str) -> types.Part:
    return types.Part.from_bytes(
        data=base64.b64decode(image_base64),
        mime_type="image/jpeg",
    )


def _strip_images_from_content(content: types.Content) -> types.Content:
    """Return a copy of a Content with all inline_data (image) Parts removed.

    Used to drop the original frame image from the orchestrator's context
    once it's no longer driving decisions — atomic tool results carry the
    evidence forward, and the image keeps re-billing if it sits in history.
    """
    if content is None or not content.parts:
        return content
    kept = [p for p in content.parts if getattr(p, "inline_data", None) is None]
    if len(kept) == len(content.parts):
        return content  # nothing to strip
    return types.Content(role=content.role, parts=kept)


def _format_baseline_for_prompt(baseline: dict[str, Any]) -> str:
    """Render the baseline dict as compact JSON for inclusion in a prompt.

    Strips bookkeeping fields the agent doesn't need (matched, source,
    confidence) and pretty-prints with 2-space indent for readability.
    Keeps token cost predictable — a typical baseline is ~250 tokens.
    """
    if not baseline:
        return "{}"
    keep_keys = {
        "scientific_name",
        "common_names",
        "group",
        "mature_crown_radius_m",
        "canopy_density_pct_healthy",
        "leaf_hue_range_hsv_deg",
        "bark_texture",
        "common_diseases",
        "preferred_soil_textures",
    }
    trimmed = {k: v for k, v in baseline.items() if k in keep_keys}
    return json.dumps(trimmed, indent=2, ensure_ascii=False)


def _summarize_args(args: dict[str, Any]) -> str:
    """Format tool args for the evidence trace — eliding image payloads."""
    pieces: list[str] = []
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 200:
            pieces.append(f"{k}=<image:{len(v)}b>")
        elif isinstance(v, list):
            if v and isinstance(v[0], str) and len(v[0]) > 200:
                pieces.append(f"{k}=[{len(v)} images]")
            else:
                pieces.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, dict):
            pieces.append(f"{k}={{...{len(v)} keys}}")
        else:
            pieces.append(f"{k}={v}")
    return ", ".join(pieces)


# ── MCP <-> Gemini bridge ────────────────────────────────────────────────

# Parameters the orchestrator injects automatically. The model never sees
# them in the function declarations and can't fabricate them.
_INJECTED_IMAGE_PARAMS = {"image_base64", "target_image_base64"}


def _strip_injected_params(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove auto-injected image params from a JSON Schema before exposing
    the tool to Gemini. Keeps everything else verbatim.
    """
    if not isinstance(schema, dict):
        return schema
    cloned = {k: v for k, v in schema.items()}
    props = cloned.get("properties")
    if isinstance(props, dict):
        cloned["properties"] = {
            k: v for k, v in props.items() if k not in _INJECTED_IMAGE_PARAMS
        }
    req = cloned.get("required")
    if isinstance(req, list):
        cloned["required"] = [r for r in req if r not in _INJECTED_IMAGE_PARAMS]
    return cloned


def _image_params_in(schema: dict[str, Any]) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return set()
    return {name for name in props if name in _INJECTED_IMAGE_PARAMS}


async def _build_tool_surface() -> tuple[
    list[types.FunctionDeclaration],
    dict[str, set[str]],
]:
    """List live MCP tools and produce Gemini-facing declarations + a
    per-tool map of which image params we need to inject at dispatch time.
    """
    tools_result = await mcp_client.session.list_tools()
    declarations: list[types.FunctionDeclaration] = []
    image_params_by_tool: dict[str, set[str]] = {}

    for tool in tools_result.tools:
        original_schema = tool.inputSchema or {"type": "object", "properties": {}}
        image_params_by_tool[tool.name] = _image_params_in(original_schema)
        cleaned_schema = _strip_injected_params(original_schema)
        try:
            declarations.append(
                types.FunctionDeclaration(
                    name=tool.name,
                    description=(tool.description or "").strip(),
                    parameters_json_schema=cleaned_schema,
                )
            )
        except Exception as e:
            logger.warning("Skipping tool %s — declaration build failed: %s", tool.name, e)

    return declarations, image_params_by_tool


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Gemini may hand back args as a Struct, dict, or odd nested types."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return dict(raw)
    except Exception:
        return {}


async def _dispatch_to_mcp(
    name: str,
    args: dict[str, Any],
    *,
    user_image_base64: str,
    image_params: set[str],
) -> dict[str, Any]:
    """Call an MCP tool. Auto-injects the user's tree image into any
    image_base64 / target_image_base64 parameter the tool requires —
    these were stripped from the Gemini-visible declaration so the
    model can't pass garbage placeholders.
    """
    for param in image_params:
        args[param] = user_image_base64

    try:
        result = await mcp_client.session.call_tool(name, args)
    except Exception as e:
        logger.warning("MCP tool '%s' raised: %s", name, e)
        return {"error": f"{type(e).__name__}: {e}"}

    # CallToolResult.content is a list of text/image/other parts.
    # We extract the first text payload and JSON-decode if possible.
    for item in (getattr(result, "content", None) or []):
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    is_error = getattr(result, "isError", False)
    return {"error": "tool returned no text content", "is_error": is_error}


def _extract_function_calls(content: types.Content) -> list[types.FunctionCall]:
    out: list[types.FunctionCall] = []
    for part in (content.parts or []):
        fc = getattr(part, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            out.append(fc)
    return out


# Tools that may be called AT MOST ONCE per tree's agent loop. Second-or-later
# invocations get a synthetic "skipped" response so the model proceeds to
# synthesis instead of burning another multi-image Gemini call.
_MAX_ONCE_PER_TREE: set[str] = {"compare_to_reference"}


def _capped_skip_result(name: str) -> dict[str, Any]:
    """Synthetic response returned in place of a capped tool invocation."""
    return {
        "skipped": True,
        "reason": (
            f"{name} has already been invoked once for this tree. "
            "Use the prior result. Do not call this tool again — "
            "proceed to your text summary and the system will run synthesis."
        ),
    }


# ── Manual agent loop ────────────────────────────────────────────────────
async def _run_agent_loop(
    initial_contents: list[types.Content],
    declarations: list[types.FunctionDeclaration],
    *,
    user_image_base64: str,
    image_params_by_tool: dict[str, set[str]],
    mandatory_first_tools: list[str],
    max_calls: int,
) -> tuple[list[types.Content], list[EvidenceStep]]:
    """Manual function-calling loop. Returns the final contents list and
    the assembled evidence trace.
    """
    contents = list(initial_contents)
    evidence: list[EvidenceStep] = []
    calls_made = 0
    turn = 0
    # Names in _MAX_ONCE_PER_TREE that we've already dispatched at least
    # once. Tracked across turns so a second/third call returns a cheap
    # "skipped" stub instead of running another multi-image Gemini call.
    capped_already_called: set[str] = set()

    while calls_made < max_calls and turn < max_calls + 2:
        turn += 1

        if turn == 1 and mandatory_first_tools:
            # First turn — force a tool call to one of the mandatory tools.
            # Only applies when caller has specified that some tool MUST run
            # first (e.g. species lookup). With baseline pre-injected the
            # list is empty and we go straight to AUTO so the agent can
            # batch parallel atomic tools immediately.
            allowed = [
                d.name for d in declarations if d.name in mandatory_first_tools
            ] or [d.name for d in declarations[:1]]
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=allowed,
                ),
            )
        else:
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO"),
            )

        try:
            response = await client.aio.models.generate_content(
                model=CONFIG.orchestrator_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    tools=[types.Tool(function_declarations=declarations)],
                    tool_config=tool_config,
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
        except Exception:
            logger.exception("Agent turn %d failed.", turn)
            break

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            logger.warning("Agent turn %d returned no candidates.", turn)
            break

        model_content = candidates[0].content
        if model_content is None:
            logger.warning("Agent turn %d returned no content.", turn)
            break
        contents.append(model_content)

        fcs = _extract_function_calls(model_content)
        if not fcs:
            # Model emitted text only → exploration done.
            break

        # Cap on this turn — never exceed the global max.
        budget = max(0, max_calls - calls_made)
        fcs_to_dispatch = fcs[:budget]

        # ── Classify each fc + dedupe within this turn ────────────────
        # Two-stage plan:
        #   1. Mark calls that hit the once-per-tree cap → synthetic skip.
        #   2. Dedupe remaining (name, args) signatures so duplicates within
        #      a turn execute once. Replay one response per original caller.
        #
        # 3.5 Flash occasionally emits the same call 4-5x in one response
        # (mis-reading the parallel-tool instruction) — dedup defends.
        # Capping defends against multi-call compare_to_reference that
        # burns multi-image Gemini cost without new diagnostic signal.
        unique_specs: list[tuple[str, str, dict]] = []  # (name, sig, args)
        sig_to_idx: dict[tuple[str, str], int] = {}
        # plan[fc_idx] = (-1 → skip via cap) or (index into unique_specs).
        plan: list[int] = []
        capped_this_turn: list[str] = []

        for fc in fcs_to_dispatch:
            args = _coerce_args(fc.args)

            # Cap check FIRST: if this tool already ran once and is in the
            # max-once set, short-circuit to a synthetic skip response.
            if fc.name in _MAX_ONCE_PER_TREE and fc.name in capped_already_called:
                plan.append(-1)
                capped_this_turn.append(fc.name)
                continue

            sig = (fc.name, json.dumps(args, sort_keys=True, default=str))
            if sig not in sig_to_idx:
                sig_to_idx[sig] = len(unique_specs)
                unique_specs.append((fc.name, sig[1], args))
            plan.append(sig_to_idx[sig])

        if capped_this_turn:
            logger.info(
                "Capped %d call(s) at once-per-tree limit: %s",
                len(capped_this_turn),
                capped_this_turn,
            )
        dispatched_count = sum(1 for p in plan if p >= 0)
        if len(unique_specs) < dispatched_count:
            logger.info(
                "Deduped %d duplicate function calls this turn "
                "(%d unique of %d dispatched).",
                dispatched_count - len(unique_specs),
                len(unique_specs),
                dispatched_count,
            )

        async def _dispatch_unique(name: str, args: dict) -> dict:
            logger.info("Agent → %s(%s)", name, _summarize_args(args))
            return await _dispatch_to_mcp(
                name,
                args,
                user_image_base64=user_image_base64,
                image_params=image_params_by_tool.get(name, set()),
            )

        # All unique calls run concurrently. MCP's stdio transport
        # serializes JSON-RPC frames internally but the atomic Gemini
        # calls fired inside the MCP tools DO run concurrently — net
        # win on wall-clock when multiple distinct tools share a turn.
        unique_results = await asyncio.gather(
            *[_dispatch_unique(name, args) for (name, _sig, args) in unique_specs],
            return_exceptions=False,
        )

        # Mark all newly-dispatched cap-set tools as "already called" so
        # subsequent turns see the cap.
        for name, _sig, _args in unique_specs:
            if name in _MAX_ONCE_PER_TREE:
                capped_already_called.add(name)

        response_parts: list[types.Part] = []
        for fc_idx, fc in enumerate(fcs_to_dispatch):
            unique_idx = plan[fc_idx]

            if unique_idx == -1:
                # Capped — synthetic skip. No dispatch happened, no new
                # evidence to record, just satisfy the protocol with a
                # matching function_response so the model can continue.
                result = _capped_skip_result(fc.name)
                response_parts.append(
                    types.Part.from_function_response(name=fc.name, response=result)
                )
                calls_made += 1
                continue

            name, _sig, args = unique_specs[unique_idx]
            result = unique_results[unique_idx]

            # Evidence trace records each tool execution once (not per
            # duplicate caller) — duplicates aren't really new evidence.
            if fc_idx == plan.index(unique_idx):
                evidence.append(
                    EvidenceStep(
                        tool=name,
                        inputs_summary=_summarize_args(args),
                        output=result if isinstance(result, dict) else {"value": result},
                    )
                )
            response_parts.append(
                types.Part.from_function_response(name=name, response=result)
            )
            calls_made += 1

        if response_parts:
            contents.append(types.Content(role="user", parts=response_parts))

        # After turn 1, the original frame image has served its purpose:
        # the model has chosen its first tool call. Subsequent decisions
        # are driven by tool results, not by re-examining the source frame.
        # Stripping the image part avoids re-billing it on every turn.
        if turn == 1 and contents:
            contents[0] = _strip_images_from_content(contents[0])

    return contents, evidence


def _result_from_core(core: DiagnosisCore, trace: list[EvidenceStep]) -> DiagnosisResult:
    return DiagnosisResult(**core.model_dump(), evidenceTrace=trace)


# ── Synthesis robustness ────────────────────────────────────────────────

_SYNTHESIS_RETRY_NOTE = (
    "Your previous response did not match the required schema. Return ONLY "
    "a JSON object with EXACTLY these fields: \"disease\" (string), "
    "\"diseaseConfidence\" (number between 0 and 1), \"summary\" (string), "
    '"actionPlan" (string), and optionally "cutReason" (string or null), '
    '"species" (string or null), "speciesConfidence" (number 0-1 or null). '
    "No markdown, no preamble, just the JSON object."
)


def _strip_json_fences(raw: str) -> str:
    """Remove ```json fences and isolate the outermost JSON object."""
    if not raw:
        return raw
    s = raw.strip()
    if s.startswith("```"):
        s = s.lstrip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
        s = s.rstrip("`").strip()
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        return s[start : end + 1]
    return s


def _coerce_to_core(data: dict[str, Any]) -> Optional[DiagnosisCore]:
    """Fill obvious gaps and try to construct a DiagnosisCore.

    Returns None if even that lenient pass can't produce a valid model.
    """
    if not isinstance(data, dict):
        return None
    cleaned: dict[str, Any] = dict(data)
    # Defaults for required fields the model might have omitted.
    cleaned.setdefault("disease", "Inconclusive")
    cleaned.setdefault("diseaseConfidence", 0.0)
    cleaned.setdefault(
        "summary",
        "Synthesis response was malformed; please review the evidence trace.",
    )
    cleaned.setdefault(
        "actionPlan",
        "Review the evidence trace and rescan if needed.",
    )
    # Clamp confidence into valid range if it leaked outside.
    try:
        conf = float(cleaned.get("diseaseConfidence", 0.0) or 0.0)
        cleaned["diseaseConfidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        cleaned["diseaseConfidence"] = 0.0
    try:
        return DiagnosisCore.model_validate(cleaned)
    except Exception as e:
        logger.warning("Lenient DiagnosisCore coercion still failed: %s", e)
        return None


async def _synthesize_diagnosis(
    synthesis_contents: list[types.Content],
    evidence_trace: list[EvidenceStep],
) -> Optional[DiagnosisCore]:
    """Three-tier synthesis robustness:
       1. Primary call with response_schema validation.
       2. Lenient parse of response.text on schema miss.
       3. One retry at lower temperature + explicit schema reminder.
    Returns None if all three tiers fail; caller builds a fallback.
    """

    # ── Tier 1: primary structured call ──────────────────────────────
    synthesis = None
    try:
        synthesis = await client.aio.models.generate_content(
            model=CONFIG.orchestrator_model,
            contents=synthesis_contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DiagnosisCore,
                temperature=0.1,
                max_output_tokens=4096,
            ),
        )
        parsed = getattr(synthesis, "parsed", None)
        if isinstance(parsed, DiagnosisCore):
            return parsed
    except Exception:
        logger.exception("Synthesis tier-1 call raised.")

    # ── Tier 2: lenient parse of whatever text came back ─────────────
    if synthesis is not None:
        raw_text = getattr(synthesis, "text", None) or ""
        if raw_text:
            try:
                data = json.loads(_strip_json_fences(raw_text))
                core = _coerce_to_core(data) if isinstance(data, dict) else None
                if core is not None:
                    logger.info("Synthesis recovered via lenient parse.")
                    return core
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Tier-2 lenient parse failed: %s", e)

    # ── Tier 3: retry with explicit schema reminder ──────────────────
    logger.info("Synthesis retrying with schema reminder…")
    retry_contents = list(synthesis_contents) + [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=_SYNTHESIS_RETRY_NOTE)],
        )
    ]
    try:
        retry = await client.aio.models.generate_content(
            model=CONFIG.orchestrator_model,
            contents=retry_contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DiagnosisCore,
                temperature=0.05,
                max_output_tokens=2048,
            ),
        )
        parsed = getattr(retry, "parsed", None)
        if isinstance(parsed, DiagnosisCore):
            logger.info("Synthesis recovered on retry.")
            return parsed
        raw_text = getattr(retry, "text", None) or ""
        if raw_text:
            data = json.loads(_strip_json_fences(raw_text))
            core = _coerce_to_core(data) if isinstance(data, dict) else None
            if core is not None:
                logger.info("Synthesis retry recovered via lenient parse.")
                return core
    except Exception:
        logger.exception("Synthesis retry raised.")

    return None


def _fallback_core_from_evidence(
    evidence_trace: list[EvidenceStep],
    req: DiagnoseRequest,
) -> DiagnosisCore:
    """Last-resort DiagnosisCore built from tool outputs without Gemini.

    Used only when synthesis fails twice. Keeps the user's per-tree
    investment from going to waste — they still get an actionable result
    grounded in real tool findings.
    """
    summary_bits: list[str] = []
    severity_score = 0.0
    dieback = False
    chlorosis = 0.0
    canopy_density: Optional[int] = None

    for step in evidence_trace:
        out = step.output or {}
        if not isinstance(out, dict):
            continue
        if step.tool == "assess_canopy_density":
            d = out.get("estimated_density_pct")
            if isinstance(d, (int, float)):
                canopy_density = int(d)
                if not out.get("healthy_match", True):
                    severity_score = max(severity_score, 1 - (d / 100.0))
                if out.get("dieback_present"):
                    dieback = True
                    severity_score = max(severity_score, 0.6)
            note = out.get("notes")
            if note:
                summary_bits.append(f"canopy density {note}")
        elif step.tool == "assess_leaf_color":
            chl = out.get("chlorosis_score") or 0.0
            try:
                chlorosis = max(chlorosis, float(chl))
            except (TypeError, ValueError):
                pass
            if chlorosis > 0.4:
                severity_score = max(severity_score, chlorosis)
            note = out.get("notes")
            if note:
                summary_bits.append(f"leaf color: {note}")
        elif step.tool == "detect_dieback_pattern":
            if out.get("dieback_present"):
                dieback = True
                sev = out.get("severity") or ""
                severity_score = max(
                    severity_score,
                    {"mild": 0.4, "moderate": 0.6, "severe": 0.85}.get(sev, 0.5),
                )

    disease = "Inconclusive — synthesis fallback"
    if severity_score > 0.6 or dieback:
        disease = "Visible stress / decline (auto-summarized)"
    elif severity_score > 0.3:
        disease = "Mild stress (auto-summarized)"
    elif severity_score == 0.0 and not summary_bits:
        disease = "None detected"

    summary = (
        "Synthesis call could not produce a valid structured response; "
        "this diagnosis was assembled directly from tool outputs. "
        + (" ".join(summary_bits)[:400] if summary_bits else "No anomalies were flagged.")
    )

    action_plan = (
        "Manual review recommended. Re-scan or perform ground inspection "
        "to confirm before any treatment."
    )

    return DiagnosisCore(
        disease=disease,
        diseaseConfidence=round(min(0.6, severity_score), 2),
        summary=summary,
        actionPlan=action_plan,
        cutReason=None,
        species=(req.species_hint or None),
        speciesConfidence=(0.7 if req.species_hint else None),
    )


# ── Public entry point ──────────────────────────────────────────────────
async def diagnose_tree(req: DiagnoseRequest) -> DiagnosisResult:
    """Run the full agentic diagnosis loop for one tree."""

    # Skip the agent loop entirely for trees the detection pass already
    # marked healthy. Saves the biggest line item on a healthy forest.
    if CONFIG.skip_healthy_diagnosis and req.status == "healthy":
        logger.info("Skipping agent loop for healthy tree (cost saver).")
        return DiagnosisResult(
            disease="None detected",
            diseaseConfidence=0.85,
            summary="Detection pass flagged this tree as healthy; deep diagnosis skipped to conserve cost. Re-scan if condition changes.",
            actionPlan="No action required, continue routine monitoring.",
            cutReason=None,
            species=None,
            speciesConfidence=None,
            evidenceTrace=[],
        )

    if not mcp_client.is_alive or mcp_client.session is None:
        logger.warning("MCP client not alive; falling back to single-call diagnosis.")
        core = _single_call_diagnose(req)
        return _result_from_core(core, trace=[])

    # Build the function declarations from the live MCP tool list.
    try:
        declarations, image_params_by_tool = await _build_tool_surface()
    except Exception:
        logger.exception("Failed to list MCP tools; falling back to single-call.")
        core = _single_call_diagnose(req)
        return _result_from_core(core, trace=[])

    if not declarations:
        logger.warning("No tool declarations available; falling back.")
        core = _single_call_diagnose(req)
        return _result_from_core(core, trace=[])

    symptoms_str = ", ".join(req.visual_symptoms) if req.visual_symptoms else "none"
    hint = (req.species_hint or "").strip()
    baseline = req.species_baseline

    if hint and baseline:
        # Best case: we know the species AND have the baseline already.
        # No mandatory tool calls — straight to atomic visual checks.
        baseline_json = _format_baseline_for_prompt(baseline)
        user_prompt = _USER_PROMPT_PRELOADED.format(
            status=req.status,
            symptoms=symptoms_str,
            mode_hint=req.mode_hint or "unspecified",
            species_hint=hint,
            baseline_json=baseline_json,
        )
        mandatory_first: list[str] = []
    elif hint:
        # Species known but no baseline — agent still needs to look it up.
        user_prompt = _USER_PROMPT_HINTED.format(
            status=req.status,
            symptoms=symptoms_str,
            mode_hint=req.mode_hint or "unspecified",
            species_hint=hint,
        )
        mandatory_first = ["lookup_species_baseline"]
    else:
        # No species hint — full mandatory ladder.
        user_prompt = _USER_PROMPT_AUTOSPEC.format(
            status=req.status,
            symptoms=symptoms_str,
            mode_hint=req.mode_hint or "unspecified",
        )
        mandatory_first = _MANDATORY_FIRST_TOOLS

    initial_contents = [
        types.Content(
            role="user",
            parts=[_image_part(req.image_base64), types.Part.from_text(text=user_prompt)],
        )
    ]

    # ── Phase A: the manual agent loop ──────────────────────────────────
    final_contents, evidence_trace = await _run_agent_loop(
        initial_contents,
        declarations,
        user_image_base64=req.image_base64,
        image_params_by_tool=image_params_by_tool,
        mandatory_first_tools=mandatory_first,
        max_calls=CONFIG.max_tool_calls,
    )
    logger.info(
        "Agent gathered %d evidence steps via tools: %s",
        len(evidence_trace),
        [s.tool for s in evidence_trace],
    )

    if not evidence_trace:
        logger.warning("Agent produced zero tool calls; falling back to single-call.")
        core = _single_call_diagnose(req)
        return _result_from_core(core, trace=[])

    # ── Phase B: structured synthesis (with retry + fallback) ──────────
    # Strip any remaining inline_data (image) parts before synthesis.
    # All needed evidence is in the tool-result history.
    synthesis_contents = [_strip_images_from_content(c) for c in final_contents]
    synthesis_contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=_SYNTHESIS_PROMPT)])
    )

    core = await _synthesize_diagnosis(synthesis_contents, evidence_trace)
    if core is None:
        # Phase A produced valid evidence but the synthesis model
        # couldn't return a clean structured response even on retry.
        # Build a minimal diagnosis directly from the evidence — never
        # waste a 50k-token agent loop because of a JSON hiccup.
        logger.warning("Synthesis failed twice; assembling fallback from evidence.")
        core = _fallback_core_from_evidence(evidence_trace, req)

    # If the user supplied a species hint, make sure it survives synthesis
    # even if the model returned null for the species field.
    if hint and not core.species:
        core = core.model_copy(update={
            "species": hint,
            "speciesConfidence": max(core.speciesConfidence or 0.0, 0.95),
        })

    return _result_from_core(core, trace=evidence_trace)
