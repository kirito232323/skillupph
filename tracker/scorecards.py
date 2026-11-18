"""Automated, explainable applicant score cards.

Each required job skill is worth up to 1 point:
  1.0  exact skill in the applicant's profile
  0.5  a transferable / closely related skill
  0.0  no evidence

The total is divided by the number of required skills and mapped to a fit
rating (>=80% strong, 60-79% moderate, <60% weak), matching the sample
Applicant Score Cards format.
"""

TRANSFERABLE_GROUPS = {
    'sql': ['postgresql', 'mysql', 'sqlite', 'sql server', 'oracle', 'sql'],
    'api_framework': ['django', 'flask', 'fastapi', 'express', 'laravel', 'spring', 'rest api', 'api integration'],
    'js_framework': ['react', 'vue', 'angular', 'next.js', 'svelte'],
    'testing': ['jest', 'vitest', 'pytest', 'cypress', 'playwright', 'phpunit', 'unittest', 'test automation'],
    'cloud': ['aws', 'azure', 'gcp', 'google cloud', 'cloud fundamentals'],
    'containers': ['docker', 'kubernetes', 'podman'],
    'version_control': ['git', 'github', 'gitlab', 'bitbucket', 'version control'],
    'agile': ['agile', 'scrum', 'kanban'],
    'auth': ['jwt', 'oauth'],
    'css': ['tailwind', 'bootstrap', 'css', 'sass'],
    'programming_language': ['javascript', 'typescript', 'python', 'java', 'php', 'c#', 'go', 'ruby'],
}

SOURCE_LABELS = {
    'Assessment': 'verified assessment',
    'assessment': 'verified assessment',
    'PESO Verified': 'PESO verified',
    'AI Resume Scan': 'résumé scan',
    'training': 'training completion',
    'Training Completion': 'training completion',
    'coordinator-verified': 'coordinator verified',
    'self': 'self-reported',
}


def _norm(value):
    return ' '.join(str(value or '').lower().replace('&', ' and ').split())


def _group_for(name):
    n = _norm(name)
    for group, keywords in TRANSFERABLE_GROUPS.items():
        if any(k in n for k in keywords):
            return group
    return None


def _is_exact(required, candidate):
    r, c = _norm(required), _norm(candidate)
    if not r or not c:
        return False
    if r == c:
        return True
    # Treat "JavaScript" and "JavaScript (ES6+)" as the same named skill.
    return (len(r) >= 4 and len(c) >= 4 and (r in c or c in r))


def _source_label(askill):
    return SOURCE_LABELS.get(askill.source, askill.source or 'self-reported')


def _work_evidence(profile, required_name, related_name=None, experiences=None):
    terms = [_norm(required_name)]
    if related_name:
        terms.append(_norm(related_name))
    terms = [t for t in terms if len(t) >= 3]
    for exp in (experiences if experiences is not None else profile.experience.all()):
        haystack = _norm(f"{exp.position or ''} {exp.company or ''} {exp.description or ''}")
        if any(term in haystack for term in terms):
            return f"Work history: {exp.position or 'role'} at {exp.company or 'company'}"
    return ''


def match_skill(required_name, applicant_skills):
    """Return the exact/transferable/none match for one required skill.

    The embedding model is available for experimentation, but calibration on
    the current catalog produced false positives (e.g. Django/PHP, React/Python),
    so production matching keeps the curated transferable groups.
    """
    exact = next(
        (a for a in applicant_skills if a.skill and _is_exact(required_name, a.skill.name)),
        None,
    )
    if exact:
        return {'match_type': 'Exact', 'points': 1.0, 'exact': exact, 'related': None}

    group = _group_for(required_name)
    if group:
        related = next(
            (a for a in applicant_skills if a.skill and _group_for(a.skill.name) == group),
            None,
        )
        if related:
            return {'match_type': 'Transferable', 'points': 0.5, 'exact': None, 'related': related}

    return {'match_type': 'None', 'points': 0.0, 'exact': None, 'related': None}


def build_automated_scorecard(profile, vacancy):
    from .skill_ml import points_for, transferable_weight

    requirements = list(vacancy.requirements.all())
    applicant_skills = list(profile.applicant_skills.all())
    experiences = list(profile.experience.all())

    rows = []
    exact_count = transferable_count = none_count = 0

    for req in requirements:
        required_name = req.skill.name if req.skill else 'Unnamed skill'
        match = match_skill(required_name, applicant_skills)
        match_type = match['match_type']
        points = points_for(match_type)

        if match_type == 'Exact':
            exact_count += 1
            exact = match['exact']
            evidence = _work_evidence(profile, required_name, experiences=experiences) or (
                f"Profile skill: {exact.skill.name} ({_source_label(exact)})"
            )
        elif match_type == 'Transferable':
            transferable_count += 1
            related = match['related']
            evidence = f"Transferable: has {related.skill.name} ({_source_label(related)})"
            work_note = _work_evidence(profile, required_name, related.skill.name, experiences=experiences)
            if work_note:
                evidence += f" — {work_note}"
        else:
            none_count += 1
            evidence = "No evidence in profile skills or work history"

        rows.append({
            'skill': required_name,
            'evidence': evidence,
            'match_type': match_type,
            'points': points,
            'points_display': f"{points:.1f}",
            'percent': int(round(points * 100)),
        })

    max_points = len(requirements)
    total_points = sum(row['points'] for row in rows)
    percentage = round((total_points / max_points) * 100) if max_points else 0

    if max_points == 0:
        fit_label, fit_tone = 'Not scored', 'slate'
    elif percentage >= 80:
        fit_label, fit_tone = 'Strong fit', 'emerald'
    elif percentage >= 60:
        fit_label, fit_tone = 'Moderate fit', 'blue'
    else:
        fit_label, fit_tone = 'Weak fit', 'rose'

    strengths = []
    exact_names = [r['skill'] for r in rows if r['match_type'] == 'Exact']
    transferable_names = [r['skill'] for r in rows if r['match_type'] == 'Transferable']
    missing_names = [r['skill'] for r in rows if r['match_type'] == 'None']

    if exact_names:
        strengths.append("Exact match on " + ", ".join(exact_names[:4]) + ("." if len(exact_names) <= 4 else ", among others."))
    if transferable_names:
        strengths.append("Transferable evidence for " + ", ".join(transferable_names[:3]) + ".")
    if profile.experience_years:
        strengths.append(f"Lists {profile.experience_years} of work experience.")
    if not strengths:
        strengths.append("Profile is missing evidence for the main required skills.")

    weaknesses = []
    for name in transferable_names[:3]:
        related = next((a.skill.name for a in applicant_skills if a.skill and _group_for(a.skill.name) == _group_for(name)), 'a related tool')
        weaknesses.append(f"{name}: only transferable evidence ({related}); may need ramp-up.")
    for name in missing_names[:4]:
        weaknesses.append(f"{name}: no evidence found in the profile.")
    if not weaknesses:
        weaknesses.append("No major skill gaps identified from the profile.")

    return {
        'rows': rows,
        'max_points': max_points,
        'total_points': total_points,
        'total_display': f"{total_points:.1f}",
        'percentage': percentage,
        'fit_label': fit_label,
        'fit_tone': fit_tone,
        'fit_is_strong': fit_tone == 'emerald',
        'fit_is_moderate': fit_tone == 'blue',
        'fit_is_weak': fit_tone == 'rose',
        'exact_count': exact_count,
        'transferable_count': transferable_count,
        'none_count': none_count,
        'transferable_weight': transferable_weight(),
        'transferable_percent': int(round(transferable_weight() * 100)),
        'strengths': strengths,
        'weaknesses': weaknesses,
    }
