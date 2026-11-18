"""Embedding-based skill similarity for machine-learning skill matching.

Skill names/descriptions are embedded with Gemini's embedding model and stored
in a local cache.  Non-exact matching then uses cosine similarity instead of
hand-written keyword groups.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

MODEL = "gemini-embedding-001"
CACHE_PATH = Path(settings.BASE_DIR) / "ml_models" / "skill_embeddings.json"
DEFAULT_THRESHOLD = 0.62
_CACHE = {"mtime": None, "data": None}


def _norm(value):
    return " ".join(str(value or "").lower().replace("&", " and ").split())


def _api_key():
    return getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")


def _embed_texts(texts, batch_size=10):
    key = _api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        payload = {
            "requests": [
                {"model": f"models/{MODEL}", "content": {"parts": [{"text": text}]}}
                for text in batch
            ]
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents?key={key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    data = json.load(response)
                vectors.extend(item["values"] for item in data.get("embeddings", []))
                if start + batch_size < len(texts):
                    time.sleep(1.5)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if attempt == 4:
                    raise RuntimeError(f"Embedding request failed: {exc}") from exc
                time.sleep(10 * (attempt + 1))
    return vectors


def build_embeddings():
    from .models import ApplicantSkill, CentralizedSkill, JobSkillRequirement

    used_ids = set(JobSkillRequirement.objects.values_list("skill_id", flat=True))
    used_ids |= set(ApplicantSkill.objects.values_list("skill_id", flat=True))
    skills = list(CentralizedSkill.objects.filter(id__in=used_ids).order_by("id"))
    texts = []
    names = []
    for skill in skills:
        text = " | ".join(filter(None, [skill.name, skill.category, skill.description or ""]))
        texts.append(text)
        names.append(_norm(skill.name))

    vectors = _embed_texts(texts)
    payload = {
        "model": MODEL,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(vectors),
        "vectors": dict(zip(names, vectors)),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    _CACHE["mtime"] = None
    _CACHE["data"] = None
    return {"count": len(vectors), "model": MODEL}


def _load():
    try:
        mtime = CACHE_PATH.stat().st_mtime
    except OSError:
        return None
    if _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _CACHE["data"] = data
    _CACHE["mtime"] = mtime
    return data


def _cosine(a, b):
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def best_similarity(required_name, applicant_skills, threshold=DEFAULT_THRESHOLD):
    """Return (similarity, applicant_skill_name) for the closest embedded skill."""
    data = _load()
    if not data:
        return None
    vectors = data.get("vectors") or {}
    required = vectors.get(_norm(required_name))
    if not required:
        return None
    best_score = 0.0
    best_name = ""
    for askill in applicant_skills:
        skill = getattr(askill, "skill", None)
        if not skill:
            continue
        candidate = vectors.get(_norm(skill.name))
        if not candidate:
            continue
        score = _cosine(required, candidate)
        if score > best_score:
            best_score, best_name = score, skill.name
    return best_score, best_name


def cache_status():
    data = _load()
    if not data:
        return {"available": False, "count": 0, "threshold": DEFAULT_THRESHOLD}
    return {
        "available": True,
        "count": data.get("count", 0),
        "model": data.get("model", MODEL),
        "built_at": data.get("built_at", ""),
        "threshold": DEFAULT_THRESHOLD,
    }
