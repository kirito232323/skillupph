# SKILLUP PH

**Skills-gap analysis and employment-lifecycle platform for a City PESO (Public Employment Service Office).**

A single Django application that connects three groups through one database and one workflow:

| Portal | Who it serves | What it does |
| --- | --- | --- |
| **Applicant** | Jobseekers, trainees | Profile wizard, resume upload, skills-gap analysis, training enrolment with videos and assessments, job search and applications, certificates |
| **Employer** | Companies posting vacancies | Job postings, applicant screening with scorecards, interview scheduling, offers, hiring tracker, gap analysis |
| **PESO Administrator** | City PESO staff | Applicant and employer monitoring, referral pipeline, training-program and curriculum management, employment tracking, analytics export |

The matching engine combines a curated skill taxonomy with a trained skill-match
model (`ml_models/`) and optional Gemini-powered assistance (resume scanning,
qualification fit, syllabus and training generation).

## Tech stack

- Django 5/6 + SQLite (or any `DATABASE_URL` database)
- WhiteNoise for static files, Gunicorn for serving
- Vanilla HTML/CSS/JS templates (no SPA build step)
- Google Gemini API for the AI helpers (optional)
- SMTP / webhook / Resend / Brevo for outbound email (optional)

## Repository layout

```
manage.py                Django entry point
requirements.txt         Python dependencies
skillup_project/         settings, root URLs, WSGI
tracker/                 the app: models, views, forms, emails, middleware,
                         AI services, 44 migrations, templates for all portals
ml_models/               serialized skill embeddings + match model
scripts/                 data seeding and maintenance scripts
deploy/                  systemd unit, nginx site, install scripts, env template
sample_data/             sample CSV imports, syllabi, document samples
sample_resumes/          sample resumes used by the resume-parsing demo
```

## Quick start

```bash
git clone https://github.com/kirito232323/skillupph.git
cd skillupph

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # then edit if you need AI or email
python manage.py migrate
python manage.py seed_demo_accounts   # optional: demo applicants/employers/PESO staff
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>. `seed_demo_accounts` creates the demo logins
listed below; the login screen links the three portals.

## Demo accounts

`python manage.py seed_demo_accounts` rebuilds these accounts. It is idempotent and
only touches `demo.*@skillup.ph` users, so real accounts and jobs are never affected.

| Role | Email | Password |
| --- | --- | --- |
| PESO Administrator | `demo.admin@skillup.ph` | `Demo@2026!` |
| Employer (verified) | `demo.employer@skillup.ph` | `Demo@2026!` |
| Applicant | `demo.applicant@skillup.ph` | `Demo@2026!` |
| Applicant (empty profile, for the wizard) | `demo.applicant.fresh@skillup.ph` | `Demo@2026!` |
| Employer (pending verification) | `demo.employer.pending@skillup.ph` | `Demo@2026!` |
| Applicant pool (4 applicants) | `demo.pool.bianca@skillup.ph`, `demo.pool.paolo@skillup.ph`, `demo.pool.rafael@skillup.ph`, `demo.pool.chloe@skillup.ph` | `Demo@2026!` |

These logins only exist after the seeding command runs. The password is placeholder
demo data for local walkthroughs — replace it before putting anything real online.

## Configuration

Every environment-specific value is read from environment variables, listed and
documented in [`.env.example`](.env.example). Nothing secret is stored in the
repository: the secret key has a throwaway development fallback, and the Gemini
key and SMTP password are blank by default, so AI and email features simply stay
off until you supply your own.

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Signing key — **required** in production |
| `DJANGO_DEBUG` | `True` locally, `False` on a server |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated hosts / origins |
| `DATABASE_URL` | Optional; defaults to local `db.sqlite3` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Enables the AI features |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Enables notification email |
| `ID_ENCRYPTION_KEY` | Optional key for encrypted ID-document storage |

## Management commands

```bash
python manage.py seed_demo_accounts        # rebuild the demo PESO/employer/applicant story
python manage.py build_skill_embeddings    # rebuild skill embeddings (needs GEMINI_API_KEY)
python manage.py train_skill_match_model   # retrain the skill-match model
```

## Deployment

`deploy/` contains a Gunicorn systemd unit, an nginx site, and install scripts
for an Ubuntu droplet. The application reads all secrets from
`deploy/env.production.example` → `/var/www/skillup/.env` (mode 600).

## Notes

- User uploads (`media/`) and the local database (`db.sqlite3`) are git-ignored;
  they are runtime state, not source.
- ID documents are encrypted at rest with a key derived from `DJANGO_SECRET_KEY`
  (or `ID_ENCRYPTION_KEY`). Changing that key makes previously stored scans
  unreadable, so set it once and keep it.

---

Built as a capstone project for a City PESO office. Issues and pull requests are
welcome.
