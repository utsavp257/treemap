"""Reference image lookups via iNaturalist.

iNaturalist's research-grade observations are the cleanest open
corpus of in-situ species photos. We use them for:

- get_healthy_reference_images(species, view, season): visual anchors
  the agent compares against to detect divergence.
- get_disease_reference_images(disease, species): visual templates for
  candidate pathology hypotheses.

No API key required. Results are cached briefly in-process to avoid
hammering the API during a single diagnostic loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

INAT_OBS_URL = "https://api.inaturalist.org/v1/observations"

# View hints map roughly to iNaturalist's "photo" defaults. We can't filter
# strictly by leaf-vs-bark on iNat, so the agent uses the returned images
# as soft anchors and discriminates them itself via the compare_to_reference
# tool added in Phase 3.
ViewHint = Literal["crown", "leaf", "stem", "any"]
Season = Literal["spring", "summer", "autumn", "winter", "any"]

_MONTHS = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "autumn": [9, 10, 11],
    "winter": [12, 1, 2],
}

_CACHE: dict[tuple, tuple[float, list[str]]] = {}
_CACHE_TTL = 1800.0  # 30 minutes


def _cache_get(key: tuple) -> list[str] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, urls = hit
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return urls


def _cache_set(key: tuple, urls: list[str]) -> None:
    _CACHE[key] = (time.time(), urls)


async def _inat_search(params: dict[str, Any], limit: int) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(INAT_OBS_URL, params=params)
        if resp.status_code != 200:
            logger.warning("iNaturalist returned %s: %s", resp.status_code, resp.text[:200])
            return []
        results = resp.json().get("results", [])
    except Exception as e:
        logger.warning("iNaturalist call failed: %s", e)
        return []

    urls: list[str] = []
    for obs in results:
        for photo in obs.get("photos", []):
            url = photo.get("url", "")
            if url:
                # Upgrade thumbnail URL to medium-res; iNat hosts size variants
                # at the same path with sizing keywords (square, medium, large).
                urls.append(url.replace("square", "medium"))
            if len(urls) >= limit:
                return urls
    return urls


async def get_healthy_reference_images(
    species: str,
    view: ViewHint = "any",
    season: Season = "any",
    limit: int = 6,
) -> dict[str, Any]:
    """Healthy reference imagery for the named species."""
    key = ("healthy", species.lower(), view, season, limit)
    cached = _cache_get(key)
    if cached is not None:
        return {"species": species, "view": view, "season": season, "image_urls": cached, "cached": True}

    params: dict[str, Any] = {
        "taxon_name": species,
        "quality_grade": "research",
        "photos": "true",
        "per_page": min(limit * 3, 30),  # over-fetch so we can skip dud entries
        "order": "desc",
        "order_by": "votes",
    }
    if season != "any" and season in _MONTHS:
        params["month"] = ",".join(str(m) for m in _MONTHS[season])

    urls = await _inat_search(params, limit)
    _cache_set(key, urls)
    return {"species": species, "view": view, "season": season, "image_urls": urls, "cached": False}


async def get_disease_reference_images(
    disease: str,
    species: str | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    """Reference imagery showing the named disease.

    iNaturalist hosts many observations tagged with pest/disease names in
    their description or annotations. We query by free-text against the
    disease name, narrowed by species if provided.
    """
    key = ("disease", disease.lower(), (species or "").lower(), limit)
    cached = _cache_get(key)
    if cached is not None:
        return {
            "disease": disease, "species": species,
            "image_urls": cached, "cached": True,
        }

    query = disease if not species else f"{disease} {species}"
    params: dict[str, Any] = {
        "q": query,
        "quality_grade": "research",
        "photos": "true",
        "per_page": min(limit * 3, 30),
        "order": "desc",
        "order_by": "votes",
    }
    if species:
        params["taxon_name"] = species

    urls = await _inat_search(params, limit)
    _cache_set(key, urls)
    return {
        "disease": disease, "species": species,
        "image_urls": urls, "cached": False,
    }
