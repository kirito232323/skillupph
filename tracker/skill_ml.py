"""Lightweight machine-learning layer for skill matching.

The model learns how much a *transferable* skill should count, using historical
referral outcomes (hired/employed vs not hired/declined/withdrawn). Exact
skills stay at 1.0; the learned transferable weight is constrained to 0.05-1.0.

The implementation is dependency-free logistic regression so it can run in the
existing virtualenv. Retrain with:

    .\\.venv\\Scripts\\python.exe manage.py train_skill_match_model
"""
import json
import math
from pathlib import Path

from django.conf import settings

MODEL_PATH = Path(settings.BASE_DIR) / "ml_models" / "skill_match_model.json"
DEFAULT_TRANSFERABLE_WEIGHT = 0.5
_CACHE = {"mtime": None, "model": None}


def _sigmoid(z):
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def load_model():
    try:
        mtime = MODEL_PATH.stat().st_mtime
    except OSError:
        return None
    if _CACHE["model"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["model"]
    try:
        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _CACHE["mtime"] = mtime
    _CACHE["model"] = model
    return model


def transferable_weight():
    model = load_model()
    if not model:
        return DEFAULT_TRANSFERABLE_WEIGHT
    try:
        samples = int(model.get("samples", 0))
        accuracy = float(model.get("accuracy", 0))
        learned = max(0.05, min(1.0, float(model.get("transferable_weight", DEFAULT_TRANSFERABLE_WEIGHT))))
    except (TypeError, ValueError):
        return DEFAULT_TRANSFERABLE_WEIGHT

    force_active = bool(getattr(settings, "SKILL_ML_FORCE_ACTIVE", False))
    if samples < 40 or accuracy < 0.65:
        if not force_active:
            return DEFAULT_TRANSFERABLE_WEIGHT
        # Forced live mode: blend the weak learned weight with the prior so
        # transferable matching does not collapse to near-zero.
        blend = max(0.0, min(1.0, float(getattr(settings, "SKILL_ML_PRIOR_BLEND", 0.5))))
        return round(((1.0 - blend) * learned) + (blend * DEFAULT_TRANSFERABLE_WEIGHT), 4)
    return learned


def model_status():
    model = load_model()
    if not model:
        return {
            'available': False,
            'active': False,
            'forced': False,
            'reason': 'No trained model yet.',
            'samples': 0,
            'accuracy': None,
            'transferable_weight': DEFAULT_TRANSFERABLE_WEIGHT,
        }
    samples = int(model.get('samples', 0) or 0)
    accuracy = model.get('accuracy')
    accuracy_ok = accuracy is not None and float(accuracy) >= 0.65
    force_active = bool(getattr(settings, 'SKILL_ML_FORCE_ACTIVE', False))
    active = (samples >= 40 and accuracy_ok) or force_active
    if samples >= 40 and accuracy_ok:
        reason = ''
    elif force_active:
        reason = 'Force-active with a low-sample prior blend; collect more labeled outcomes.'
    else:
        reason = 'Needs at least 40 labeled referrals and 65% accuracy.'
    return {
        'available': True,
        'active': active,
        'forced': force_active and not (samples >= 40 and accuracy_ok),
        'reason': reason,
        'samples': samples,
        'accuracy': round(float(accuracy), 3) if accuracy is not None else None,
        'transferable_weight': transferable_weight(),
        'trained_at': model.get('trained_at', ''),
        'algorithm': model.get('algorithm', 'logistic_regression'),
    }


def points_for(match_type):
    if match_type == "Exact":
        return 1.0
    if match_type == "Transferable":
        return transferable_weight()
    return 0.0


def _feature_row(profile, vacancy, match_skill):
    requirements = list(vacancy.requirements.all())
    if not requirements:
        return None
    applicant_skills = list(profile.applicant_skills.all())
    exact = transferable = 0
    for req in requirements:
        name = req.skill.name if req.skill else ""
        result = match_skill(name, applicant_skills)
        if result["match_type"] == "Exact":
            exact += 1
        elif result["match_type"] == "Transferable":
            transferable += 1
    total = len(requirements)
    return exact / total, transferable / total


def train_model(min_samples=12):
    from .models import Referral
    from .scorecards import match_skill

    positive_statuses = set(Referral.ACTIVE_EMPLOYMENT_STATUSES) | {
        "Hired", "Regularly Employed", "Still Employed — Performing Well",
        "Still Employed — On Improvement Plan",
    }
    negative_statuses = {
        "Not Hired", "Declined", "Closed — No Show", "No Response from Applicant",
        "No Response from Employer", "No Response", "No Show", "Withdrawn",
        "Withdrawn — Position Closed", "Separated — End of Probation",
    }

    rows = []
    for ref in Referral.objects.select_related("applicant", "job_vacancy").all():
        if ref.status in positive_statuses:
            label = 1
        elif ref.status in negative_statuses:
            label = 0
        else:
            continue
        features = _feature_row(ref.applicant, ref.job_vacancy, match_skill)
        if features is not None:
            rows.append((features[0], features[1], label))

    if len(rows) < min_samples or len({r[2] for r in rows}) < 2:
        return {
            "trained": False,
            "reason": f"Need at least {min_samples} labeled referrals with both outcomes.",
            "samples": len(rows),
        }

    positives = sum(1 for r in rows if r[2] == 1)
    negatives = len(rows) - positives
    pos_weight = len(rows) / (2.0 * positives)
    neg_weight = len(rows) / (2.0 * negatives)

    w = DEFAULT_TRANSFERABLE_WEIGHT
    b = 0.0
    lr = 0.15
    l2 = 0.02
    for _ in range(3000):
        grad_w = grad_b = 0.0
        for exact_ratio, transfer_ratio, label in rows:
            z = b + exact_ratio + w * transfer_ratio
            error = _sigmoid(z) - label
            weight = pos_weight if label == 1 else neg_weight
            grad_b += error * weight
            grad_w += error * weight * transfer_ratio
        n = len(rows)
        b -= lr * (grad_b / n)
        w -= lr * ((grad_w / n) + l2 * w)
        w = max(0.05, min(1.0, w))

    correct = 0
    for exact_ratio, transfer_ratio, label in rows:
        pred = 1 if _sigmoid(b + exact_ratio + w * transfer_ratio) >= 0.5 else 0
        correct += int(pred == label)
    accuracy = round(correct / len(rows), 3)

    model = {
        "transferable_weight": round(w, 4),
        "bias": round(b, 4),
        "samples": len(rows),
        "positives": positives,
        "negatives": negatives,
        "accuracy": accuracy,
        "exact_weight": 1.0,
        "algorithm": "logistic_regression",
        "trained_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, indent=2), encoding="utf-8")
    model["trained"] = True
    return model
