"""Typed errors with honest surfacing.

The old code labelled every Gemini failure 'QUOTA EXHAUSTED' and silently
fell back to random data. These errors are explicit so callers can decide
how to surface them to the user.
"""

from fastapi import HTTPException


class GeminiError(Exception):
    """Base class for all Gemini-side problems."""


class GeminiQuotaExhausted(GeminiError):
    """The API returned a 429 / quota-related error."""


class GeminiInvalidResponse(GeminiError):
    """Gemini returned a 200 but the payload was unusable
    (empty, malformed, or didn't match the response schema)."""


class GeminiTransportError(GeminiError):
    """Network / SDK transport failure."""


def to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, GeminiQuotaExhausted):
        return HTTPException(status_code=429, detail={"code": "gemini_quota", "message": str(exc)})
    if isinstance(exc, GeminiInvalidResponse):
        return HTTPException(status_code=502, detail={"code": "gemini_bad_response", "message": str(exc)})
    if isinstance(exc, GeminiTransportError):
        return HTTPException(status_code=503, detail={"code": "gemini_transport", "message": str(exc)})
    return HTTPException(status_code=500, detail={"code": "internal", "message": str(exc)})
