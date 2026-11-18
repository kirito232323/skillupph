"""Create a clean, presentation-ready set of SKILLUP demo accounts.

The command is intentionally idempotent: it removes the demo users (and all
records that cascade from them) and rebuilds a coherent story for a live
walkthrough.  It never touches non-demo accounts, jobs, or training programs.

Usage:
    python manage.py seed_demo_accounts
    python manage.py seed_demo_accounts --no-assets   # skip sample files
"""

from __future__ import annotations

import os
import secrets
import textwrap
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tracker.course_curriculum import get_program_curriculum
from tracker.models import (
    ApplicantDocument,
    ApplicantSkill,
    CentralizedSkill,
    Certification,
    CompanyReview,
    Education,
    Interview,
    JobSkillRequirement,
    JobVacancy,
    Notification,
    Profile,
    Referral,
    TrainingEnrollment,
    TrainingProgram,
    WorkExperience,
)
from tracker.views import calculate_total_experience_years


DEMO_PASSWORD = "Demo@2026!"

ADMIN_EMAIL = "demo.admin@skillup.ph"
EMPLOYER_EMAIL = "demo.employer@skillup.ph"
APPLICANT_EMAIL = "demo.applicant@skillup.ph"
FRESH_EMAIL = "demo.applicant.fresh@skillup.ph"
PENDING_EMPLOYER_EMAIL = "demo.employer.pending@skillup.ph"

POOL_EMAILS = [
    "demo.pool.bianca@skillup.ph",
    "demo.pool.paolo@skillup.ph",
    "demo.pool.rafael@skillup.ph",
    "demo.pool.chloe@skillup.ph",
]

DEMO_EMAILS = [
    ADMIN_EMAIL,
    EMPLOYER_EMAIL,
    APPLICANT_EMAIL,
    FRESH_EMAIL,
    PENDING_EMPLOYER_EMAIL,
    *POOL_EMAILS,
]

# ---------------------------------------------------------------------------
# Sample assets
# ---------------------------------------------------------------------------

ALEXIS_RESUME = [
    ("h1", "ALEXIS D. CRUZ"),
    ("sub", "Junior Full-Stack Web Developer"),
    ("p", "Bocaue, Bulacan  |  +63 917 555 0142  |  alexis.cruz@example.com"),
    ("p", "linkedin.com/in/alexiscruz  |  github.com/alexiscruz  |  alexiscruz.dev"),
    ("h2", "PROFESSIONAL SUMMARY"),
    ("p", "Full-stack developer with 4+ years of experience building responsive web applications and REST APIs. Skilled in Python, Django, React, and SQL, with a strong focus on clean architecture, automated testing, and measurable business outcomes."),
    ("h2", "CORE SKILLS"),
    ("p", "Python, Django, React, JavaScript, SQL, Git, REST API Development, HTML5, CSS3, Tailwind CSS, PostgreSQL, Problem Solving, Communication"),
    ("h2", "WORK EXPERIENCE"),
    ("h3", "Web Developer - CloudBridge Digital Solutions (June 2022 - Present)"),
    ("p", "Built and maintained 12+ Django and React modules for a logistics platform used by 8,000 monthly users. Designed REST endpoints, optimized PostgreSQL queries, and reduced page load time by 38%."),
    ("h3", "Junior Developer Intern - Municipal Government of Bocaue (January 2022 - May 2022)"),
    ("p", "Digitized 4,000 resident records, built an internal search tool, and documented data privacy handling for the municipal PESO office."),
    ("h2", "EDUCATION"),
    ("p", "BS Information Technology - Polytechnic University of the Philippines (2018 - 2022)"),
    ("h2", "CERTIFICATIONS"),
    ("p", "Computer Systems Servicing NC II - TESDA (2023)"),
    ("p", "Meta Front-End Developer Professional Certificate - Coursera (2024)"),
]

JAMIE_RESUME = [
    ("h1", "JAMIE L. NAVARRO"),
    ("sub", "Aspiring Web Developer | BS Information Technology Graduate"),
    ("p", "Bocaue, Bulacan  |  +63 918 555 0177  |  jamie.navarro@example.com"),
    ("p", "github.com/jamienavarro  |  jamienavarro.dev"),
    ("h2", "PROFESSIONAL SUMMARY"),
    ("p", "Recent BS Information Technology graduate seeking an entry-level web development role. Completed a 480-hour internship building responsive interfaces with HTML, CSS, JavaScript, and React."),
    ("h2", "CORE SKILLS"),
    ("p", "HTML5, CSS3, JavaScript, React, Tailwind CSS, Git, Python, SQL, Communication, Problem Solving"),
    ("h2", "WORK EXPERIENCE"),
    ("h3", "Front-End Developer Intern - Bocaue Digital Services (June 2025 - December 2025)"),
    ("p", "Converted 9 Figma screens into responsive React components, fixed accessibility issues, and wrote reusable CSS utilities."),
    ("h2", "EDUCATION"),
    ("p", "BS Information Technology - Bulacan State University (2021 - 2025)"),
    ("h2", "CERTIFICATIONS"),
    ("p", "Computer Systems Servicing NC II - TESDA (2025)"),
]

SAMPLE_JOBS_CSV = (
    "title,category,location,min_salary,max_salary,slots,required_experience_years,"
    "min_education,required_certifications,skills,description,remarks\n"
    "Frontend React Engineer,IT,\"BGC, Taguig / Hybrid\",35000,55000,2,1,"
    "\"College graduate\",\"\",\"React, TypeScript, Tailwind CSS, JavaScript, Git\","
    "\"Build accessible React interfaces and reusable design-system components.\","
    "\"Hybrid setup with equipment allowance.\"\n"
    "IT Support Specialist,IT,\"Bocaue, Bulacan / On-site\",20000,28000,3,0,"
    "\"Vocational / Tech Grad\",\"Computer Systems Servicing NC II\","
    "\"Networking, Windows, Technical Support, Customer Service Orientation\","
    "\"Provide first-line desktop, network, and application support to internal users.\","
    "\"TESDA NC II holders preferred; free certification renewal.\"\n"
)

SAMPLE_APPLICANTS_CSV = (
    "first_name,last_name,email,phone,birthdate,civil_status,degree,field_of_study,"
    "institution,end_year,company,position,description\n"
    "Liza,Morales,liza.morales@example.com,09175550190,1998-04-12,Single,"
    "BS Information Technology,Information Technology,Bulacan State University,2020,"
    "Metro Digital Hub,Junior Developer,Built internal dashboards with JavaScript and SQL.\n"
    "Noel,Bautista,noel.bautista@example.com,09175550191,1995-11-03,Married,"
    "BS Computer Engineering,Computer Engineering,Polytechnic University of the Philippines,2018,"
    "Northline Logistics,IT Support Assistant,Handled desktop support and network troubleshooting.\n"
)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path: Path, blocks) -> None:
    """Write a small, text-extractable A4 PDF without third-party libraries."""

    # Lay out each block: (text, font, size, gap_before)
    laid_out = []
    for kind, text in blocks:
        if kind == "h1":
            laid_out.append((text, "F2", 20, 0))
        elif kind == "sub":
            laid_out.append((text, "F2", 12, 4))
        elif kind == "h2":
            laid_out.append((text, "F2", 11, 14))
        elif kind == "h3":
            laid_out.append((text, "F2", 10, 8))
        else:
            laid_out.append((text, "F1", 9.5, 3))

    pages = []
    current = []
    y = 800.0
    for text, font, size, gap in laid_out:
        y -= gap
        if y < 55:
            pages.append(current)
            current = []
            y = 800.0
        current.append((text, font, size, y))
        y -= size + 3.5
    if current:
        pages.append(current)
    if not pages:
        pages = [[]]

    objects = {}
    page_ids = []
    next_id = 5
    for page_lines in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)

        stream_lines = []
        for text, font, size, y_pos in page_lines:
            safe = _pdf_escape(text.replace("\u2014", "-").replace("\u2013", "-"))
            stream_lines.append(
                f"BT /{font} {size} Tf 1 0 0 1 50 {y_pos:.2f} Tm ({safe}) Tj ET"
            )
        stream = "\n".join(stream_lines).encode("latin-1", "replace")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
    ).encode("ascii")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"

    max_id = max(objects)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for obj_id in range(1, max_id + 1):
        body = objects.get(obj_id, b"<< >>")
        offsets[obj_id] = len(out)
        out.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {max_id + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, max_id + 1):
        out.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode(
            "ascii"
        )
    )
    path.write_bytes(bytes(out))


def write_docx_resume(path: Path, blocks) -> None:
    try:
        from docx import Document
        from docx.shared import Pt
    except Exception:
        return

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for kind, text in blocks:
        if kind == "h1":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True
            run.font.size = Pt(20)
        elif kind == "sub":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True
            run.font.size = Pt(11.5)
        elif kind == "h2":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True
            run.font.size = Pt(10.5)
        elif kind == "h3":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True
        else:
            doc.add_paragraph(text)
    doc.save(str(path))


def write_txt_resume(path: Path, blocks) -> None:
    lines = []
    for kind, text in blocks:
        if kind == "h1":
            lines.append(text.upper())
            lines.append("=" * len(text))
        elif kind == "h2":
            lines.append("")
            lines.append(text.upper())
        else:
            lines.append(text)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_sample_assets(base_dir: Path) -> None:
    resume_dir = base_dir / "sample_resumes"
    data_dir = base_dir / "sample_data"
    _ensure_dir(resume_dir)
    _ensure_dir(data_dir)

    for name, blocks in [
        ("Alexis_Cruz_Resume", ALEXIS_RESUME),
        ("Jamie_Navarro_Resume", JAMIE_RESUME),
    ]:
        write_txt_resume(resume_dir / f"{name}.txt", blocks)
        write_docx_resume(resume_dir / f"{name}.docx", blocks)
        write_simple_pdf(resume_dir / f"{name}.pdf", blocks)

    (data_dir / "bulk_job_postings.csv").write_text(SAMPLE_JOBS_CSV, encoding="utf-8")
    (data_dir / "bulk_applicants.csv").write_text(SAMPLE_APPLICANTS_CSV, encoding="utf-8")

    # Sample pre-employment documents for the offer-onboarding scene.
    write_simple_pdf(
        data_dir / "NBI_Clearance_Sample.pdf",
        [
            ("h1", "NATIONAL BUREAU OF INVESTIGATION"),
            ("sub", "CLEARANCE CERTIFICATE - SAMPLE / DEMO ONLY"),
            ("p", "Clearance No.: NBI-DEMO-2026-00142"),
            ("p", "Name: ALEXIS D. CRUZ"),
            ("p", "Date of Birth: 14 August 1999"),
            ("p", "Address: Bocaue, Bulacan"),
            ("p", "Purpose: Local Employment"),
            ("p", "Status: NO DEROGATORY RECORD FOUND"),
            ("p", "Issued: 05 November 2026  |  Valid Until: 05 May 2027"),
            ("h2", "NOTICE"),
            ("p", "This document is a fictional sample generated for the SKILLUP demonstration. It is not a real NBI clearance and must not be used for any official purpose."),
        ],
    )
    write_simple_pdf(
        data_dir / "Medical_Certificate_Sample.pdf",
        [
            ("h1", "MEDICAL CERTIFICATE"),
            ("sub", "FIT TO WORK - SAMPLE / DEMO ONLY"),
            ("p", "Certificate No.: MED-DEMO-2026-0088"),
            ("p", "Name: ALEXIS D. CRUZ"),
            ("p", "Examination Date: 06 November 2026"),
            ("p", "Findings: Physically fit to perform office and software development work."),
            ("p", "Physician: Dr. Helena R. Bautista, MD"),
            ("p", "License No.: 1234567 (sample)"),
            ("h2", "NOTICE"),
            ("p", "This document is a fictional sample generated for the SKILLUP demonstration. It is not a real medical certificate and must not be used for any official purpose."),
        ],
    )
    write_simple_pdf(
        data_dir / "Sample_Syllabus_Web_Development.pdf",
        [
            ("h1", "COURSE SYLLABUS - FULL-STACK WEB DEVELOPMENT"),
            ("sub", "PESO / TESDA Training Center - Sample Syllabus for AI Course Generation"),
            ("p", "Duration: 40 hours  |  Delivery: Hybrid  |  Prerequisite: Basic computer literacy"),
            ("h2", "COURSE DESCRIPTION"),
            ("p", "This course prepares learners for entry-level full-stack web development using Python, Django, React, and PostgreSQL. Learners build, test, and deploy a capstone web application."),
            ("h2", "LEARNING OUTCOMES"),
            ("p", "1. Build responsive user interfaces with HTML, CSS, JavaScript, and React."),
            ("p", "2. Design and implement REST APIs with Django and Django REST Framework."),
            ("p", "3. Model relational data and write safe SQL queries in PostgreSQL."),
            ("p", "4. Use Git for version control and collaborative development."),
            ("p", "5. Apply basic security, testing, and deployment practices."),
            ("h2", "MODULE OUTLINE"),
            ("p", "Module 1: Web foundations and semantic HTML."),
            ("p", "Module 2: CSS, responsive layout, and Tailwind CSS."),
            ("p", "Module 3: JavaScript fundamentals and DOM manipulation."),
            ("p", "Module 4: React components, state, and hooks."),
            ("p", "Module 5: Python fundamentals and data structures."),
            ("p", "Module 6: Django models, views, templates, and forms."),
            ("p", "Module 7: REST API design and authentication."),
            ("p", "Module 8: PostgreSQL, SQL, and data integrity."),
            ("p", "Module 9: Git, testing, and debugging."),
            ("p", "Module 10: Capstone project and deployment."),
            ("h2", "ASSESSMENT"),
            ("p", "40% module checkpoints, 20% practical exercises, 40% capstone project and oral defense."),
            ("h2", "NOTICE"),
            ("p", "This is a fictional sample syllabus created for the SKILLUP demonstration."),
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def skill(name: str, category: str = "General") -> CentralizedSkill:
    obj, _ = CentralizedSkill.objects.get_or_create(
        name=name, defaults={"category": category}
    )
    return obj


def set_file(field, filename: str, source: Path) -> None:
    """Replace a FileField value with a deterministic filename."""
    if not source.exists():
        return
    try:
        target = Path(field.storage.path(filename))
        if target.exists():
            target.unlink()
    except Exception:
        pass
    with source.open("rb") as handle:
        field.save(filename, ContentFile(handle.read()), save=True)


def cleanup_demo_data() -> None:
    users = User.objects.filter(email__in=DEMO_EMAILS)
    for user in users:
        profile = getattr(user, "profile", None)
        if not profile:
            continue
        for document in profile.documents.all():
            try:
                document.file.delete(save=False)
            except Exception:
                pass
        for field in ("id_document", "company_logo", "profile_picture"):
            value = getattr(profile, field)
            if value:
                try:
                    value.delete(save=False)
                except Exception:
                    pass
    users.delete()


def make_user(email: str, first: str, last: str, role: str, **profile_fields):
    user = User.objects.create_user(
        username=email, email=email, password=DEMO_PASSWORD
    )
    user.first_name = first
    user.last_name = last
    user.save()
    profile = Profile.objects.create(
        user=user,
        role=role,
        is_verified=profile_fields.pop("is_verified", True),
        is_profile_complete=profile_fields.pop("is_profile_complete", True),
        account_status=profile_fields.pop("account_status", "Active"),
        **profile_fields,
    )
    return user, profile


def add_skill(profile: Profile, name: str, source: str = "self") -> None:
    ApplicantSkill.objects.get_or_create(
        profile=profile, skill=skill(name), defaults={"source": source}
    )


def sync_skill_text(profile: Profile) -> None:
    names = list(
        profile.applicant_skills.select_related("skill").values_list(
            "skill__name", flat=True
        )
    )
    profile.skills = ", ".join(names)[:255]
    profile.save(update_fields=["skills"])


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Seed/refresh the SKILLUP demo accounts, jobs, applications and LMS state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-assets",
            action="store_true",
            help="Do not regenerate sample resume/CSV files.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        if not options["no_assets"]:
            ensure_sample_assets(base_dir)

        cleanup_demo_data()

        admin = self._seed_admin()
        employer, pending_employer = self._seed_employers(base_dir)
        applicant = self._seed_applicant(base_dir)
        self._seed_fresh_applicant()
        pool = self._seed_pool()

        jobs = self._seed_jobs(employer)
        self._seed_referrals(applicant, pool, jobs)
        self._seed_interviews(employer, applicant, pool, jobs)
        self._seed_reviews(employer, pool)
        self._seed_training(applicant)
        self._seed_notifications(admin, employer, applicant, pending_employer, jobs)
        self._refresh_slots(jobs)

        self._print_summary(applicant, jobs)

    # -- accounts ---------------------------------------------------------

    def _seed_admin(self):
        user, profile = make_user(
            ADMIN_EMAIL,
            "Maria",
            "Santos",
            "admin",
            phone_number="+63 917 555 0101",
            title="PESO Employment Coordinator",
            location="Bocaue, Bulacan",
            address="Municipal Hall, Bocaue, Bulacan",
            civil_status="Married",
            preferred_job="ADM",
            soft_notes=(
                "PESO Bocaue coordinator for employment facilitation, employer "
                "accreditation, and DOLE labor-market reporting."
            ),
        )
        return profile

    def _seed_employers(self, base_dir: Path):
        employer, profile = make_user(
            EMPLOYER_EMAIL,
            "Daniel",
            "Reyes",
            "employer",
            phone_number="+63 917 555 0120",
            title="Talent Acquisition Manager",
            location="BGC, Taguig City",
            address="18th Floor, One Bonifacio High Street, BGC, Taguig",
            preferred_job="IT",
            company_name="NovaTech Solutions Inc.",
            industry="Information Technology / Software Development",
            company_size="51-200 employees",
            website="https://www.novatech-demo.ph",
            contact_name="Daniel Reyes",
            contact_position="Talent Acquisition Manager",
            contact_email="daniel.reyes@novatech-demo.ph",
            employment_type_offered="Full-Time / Hybrid",
            soft_notes=(
                "Product engineering partner for Philippine logistics and "
                "financial technology companies."
            ),
        )
        set_file(
            profile.company_logo,
            "novatech_logo.png",
            base_dir / "media" / "company_logos" / "novatech_logo.png",
        )

        pending, pending_profile = make_user(
            PENDING_EMPLOYER_EMAIL,
            "Grace",
            "Villanueva",
            "employer",
            phone_number="+63 917 555 0131",
            title="Operations Director",
            location="Bocaue, Bulacan",
            company_name="BlueWave Logistics Corp.",
            industry="Logistics & Warehousing",
            company_size="201-500 employees",
            website="https://www.bluewave-demo.ph",
            contact_name="Grace Villanueva",
            contact_position="Operations Director",
            contact_email="grace.villanueva@bluewave-demo.ph",
            employment_type_offered="Full-Time",
            is_verified=False,
            soft_notes=(
                "New partner awaiting PESO accreditation. Submitted SEC "
                "registration and BIR 2303 for review."
            ),
        )
        set_file(
            pending_profile.company_logo,
            "bluewave_logo.png",
            base_dir / "media" / "company_logos" / "bluewave_logo.png",
        )
        return profile, pending_profile

    def _seed_applicant(self, base_dir: Path):
        user, profile = make_user(
            APPLICANT_EMAIL,
            "Alexis",
            "Cruz",
            "applicant",
            phone_number="+63 917 555 0142",
            title="Junior Full-Stack Web Developer",
            status="Active — Job Seeking",
            preferred_job="IT",
            location="Bocaue, Bulacan",
            address="Blk 7 Lot 12, Poblacion, Bocaue, Bulacan",
            birthdate=date(1999, 8, 14),
            civil_status="Single",
            is_fresh_grad=False,
            soft_notes=(
                "Full-stack developer focused on Django and React. Looking for "
                "a product team where I can own features end to end and grow "
                "into a senior engineer role."
            ),
            linkedin_url="https://www.linkedin.com/in/alexiscruz-demo",
            github_url="https://github.com/alexiscruz-demo",
            portfolio_url="https://alexiscruz.dev",
            id_type="Philippine National ID (PhilSys)",
            id_number="1234-5678-9012-3456",
            id_verified=True,
            training_progress_title="Full-Stack Web Development Bootcamp",
            training_progress_percentage=95,
        )

        # Profile photo + encrypted government ID at rest.
        set_file(
            profile.profile_picture,
            "alexis_cruz.png",
            base_dir / "media" / "profile_pictures" / "alexis_cruz.png",
        )
        set_file(
            profile.id_document,
            "alexis_cruz_philsys.png",
            base_dir / "sample_resumes" / "sample_philsys_id.png",
        )

        # Education
        Education.objects.create(
            profile=profile,
            institution="POLYTECHNIC UNIVERSITY OF THE PHILIPPINES",
            degree="BS INFORMATION TECHNOLOGY",
            field_of_study="INFORMATION TECHNOLOGY",
            start_year=2018,
            end_year=2022,
        )

        # Work history (also drives Profile.experience_years through the signal)
        WorkExperience.objects.create(
            profile=profile,
            company="CloudBridge Digital Solutions",
            position="Web Developer",
            description=(
                "Built Django and React modules, designed REST endpoints, "
                "optimized PostgreSQL queries, and cut page load time by 38%."
            ),
            start_date="Jun 2022",
            end_date="Present",
            is_current=True,
        )
        WorkExperience.objects.create(
            profile=profile,
            company="Municipal Government of Bocaue",
            position="Junior Developer Intern",
            description=(
                "Digitized 4,000 resident records and built an internal search "
                "tool for the municipal PESO office."
            ),
            start_date="Jan 2022",
            end_date="May 2022",
            is_current=False,
        )

        # Verified + self-reported skills
        for name, source in [
            ("Python", "Assessment"),
            ("Django", "Assessment"),
            ("React", "Assessment"),
            ("JavaScript", "self"),
            ("SQL", "self"),
            ("Git", "self"),
            ("REST API Development", "self"),
            ("HTML5", "self"),
            ("CSS3", "self"),
            ("Tailwind CSS", "self"),
            ("PostgreSQL", "self"),
            ("Problem Solving", "PESO Verified"),
            ("Communication", "PESO Verified"),
        ]:
            add_skill(profile, name, source)

        Certification.objects.create(
            profile=profile,
            name="Computer Systems Servicing NC II",
            issuing_organization="TESDA",
            issue_date=date(2023, 6, 15),
            is_tesda=True,
        )
        Certification.objects.create(
            profile=profile,
            name="Meta Front-End Developer Professional Certificate",
            issuing_organization="Coursera / Meta",
            issue_date=date(2024, 3, 10),
            is_tesda=False,
        )

        profile.experience_years = calculate_total_experience_years(profile)
        sync_skill_text(profile)

        # Resume already on file, ready for the "Scan with AI" demo.
        resume_path = base_dir / "sample_resumes" / "Alexis_Cruz_Resume.pdf"
        if resume_path.exists():
            document = ApplicantDocument(
                profile=profile,
                file_name="Alexis_Cruz_Resume.pdf",
                file_size=f"{max(1, resume_path.stat().st_size // 1024)} KB",
            )
            try:
                prior = Path(
                    document.file.storage.path("applicant_documents/Alexis_Cruz_Resume.pdf")
                )
                if prior.exists():
                    prior.unlink()
            except Exception:
                pass
            with resume_path.open("rb") as handle:
                document.file.save(
                    "Alexis_Cruz_Resume.pdf", ContentFile(handle.read()), save=True
                )

        return profile

    def _seed_fresh_applicant(self):
        user, profile = make_user(
            FRESH_EMAIL,
            "Jamie",
            "Navarro",
            "applicant",
            phone_number="+63 918 555 0177",
            title="Aspiring Web Developer",
            status="Active — Job Seeking",
            preferred_job="IT",
            location="Bocaue, Bulacan",
            address="Bocaue, Bulacan",
            birthdate=date(2003, 2, 21),
            civil_status="Single",
            is_fresh_grad=True,
            soft_notes=(
                "Fresh BSIT graduate. Upload a resume on the Profile page and "
                "let the AI scanner populate skills, education and experience."
            ),
        )
        profile.experience_years = "0 Years"
        profile.save(update_fields=["experience_years"])
        return profile

    def _seed_pool(self):
        pool = {}

        user, profile = make_user(
            POOL_EMAILS[0],
            "Bianca",
            "Lim",
            "applicant",
            phone_number="+63 917 555 0155",
            title="Frontend Developer",
            status="Interviewing",
            preferred_job="IT",
            location="Makati City",
            address="Makati City",
            birthdate=date(1997, 5, 9),
            civil_status="Single",
            soft_notes="React and TypeScript specialist with design-system experience.",
            training_progress_title="Front-End Development with React & Tailwind CSS",
            training_progress_percentage=100,
        )
        Education.objects.create(
            profile=profile,
            institution="DE LA SALLE UNIVERSITY",
            degree="BS COMPUTER SCIENCE",
            field_of_study="COMPUTER SCIENCE",
            start_year=2014,
            end_year=2018,
        )
        WorkExperience.objects.create(
            profile=profile,
            company="PixelWorks Studio",
            position="Frontend Developer",
            description="Built React design systems and accessible component libraries.",
            start_date="Feb 2021",
            end_date="Present",
            is_current=True,
        )
        for name in [
            "React",
            "TypeScript",
            "JavaScript",
            "Tailwind CSS",
            "HTML5",
            "CSS3",
            "Git",
            "Communication",
        ]:
            add_skill(profile, name, "Assessment" if name in ("React", "TypeScript") else "self")
        profile.experience_years = calculate_total_experience_years(profile)
        sync_skill_text(profile)
        pool["bianca"] = profile

        user, profile = make_user(
            POOL_EMAILS[1],
            "Paolo",
            "Garcia",
            "applicant",
            phone_number="+63 917 555 0166",
            title="Backend Developer",
            status="Applied",
            preferred_job="IT",
            location="Quezon City",
            address="Quezon City",
            birthdate=date(1996, 12, 2),
            civil_status="Single",
            soft_notes="Django and PostgreSQL developer focused on API reliability.",
        )
        Education.objects.create(
            profile=profile,
            institution="UNIVERSITY OF THE PHILIPPINES",
            degree="BS COMPUTER SCIENCE",
            field_of_study="COMPUTER SCIENCE",
            start_year=2013,
            end_year=2017,
        )
        WorkExperience.objects.create(
            profile=profile,
            company="SwiftPay Fintech",
            position="Backend Developer",
            description="Maintained Django APIs and PostgreSQL reporting pipelines.",
            start_date="Aug 2020",
            end_date="Present",
            is_current=True,
        )
        for name in [
            "Python",
            "Django",
            "PostgreSQL",
            "REST API Development",
            "Docker",
            "Git",
            "Problem Solving",
        ]:
            add_skill(profile, name, "Assessment" if name == "Python" else "self")
        profile.experience_years = calculate_total_experience_years(profile)
        sync_skill_text(profile)
        pool["paolo"] = profile

        user, profile = make_user(
            POOL_EMAILS[2],
            "Rafael",
            "Mendoza",
            "applicant",
            phone_number="+63 917 555 0188",
            title="IT Support Specialist",
            status="Applied",
            preferred_job="IT",
            location="Bocaue, Bulacan",
            address="Bocaue, Bulacan",
            birthdate=date(1998, 3, 30),
            civil_status="Single",
            soft_notes="Desktop and network support with strong customer service.",
        )
        Education.objects.create(
            profile=profile,
            institution="BULACAN STATE UNIVERSITY",
            degree="BS INFORMATION TECHNOLOGY",
            field_of_study="INFORMATION TECHNOLOGY",
            start_year=2016,
            end_year=2020,
        )
        WorkExperience.objects.create(
            profile=profile,
            company="EastGate Retail Group",
            position="IT Support Assistant",
            description="Resolved 40+ desktop and network tickets per week.",
            start_date="Jul 2020",
            end_date="Present",
            is_current=True,
        )
        for name in [
            "Networking",
            "Windows",
            "Technical Support",
            "Customer Service Orientation",
            "Communication",
        ]:
            add_skill(profile, name, "self")
        profile.experience_years = calculate_total_experience_years(profile)
        sync_skill_text(profile)
        pool["rafael"] = profile

        user, profile = make_user(
            POOL_EMAILS[3],
            "Chloe",
            "Ramos",
            "applicant",
            phone_number="+63 917 555 0199",
            title="Junior Web Developer",
            status="Hired",
            preferred_job="IT",
            location="Valenzuela City",
            address="Valenzuela City",
            birthdate=date(2000, 7, 18),
            civil_status="Single",
            soft_notes="Junior developer hired through the SKILLUP referral pipeline.",
            training_progress_title="Full-Stack Web Development Bootcamp",
            training_progress_percentage=100,
        )
        Education.objects.create(
            profile=profile,
            institution="VALENZUELA CITY POLYTECHNIC COLLEGE",
            degree="BS INFORMATION TECHNOLOGY",
            field_of_study="INFORMATION TECHNOLOGY",
            start_year=2018,
            end_year=2022,
        )
        WorkExperience.objects.create(
            profile=profile,
            company="NovaTech Solutions Inc.",
            position="Junior Web Developer",
            description="Onboarding under the six-month probationary program.",
            start_date="Oct 2025",
            end_date="Present",
            is_current=True,
        )
        for name in ["JavaScript", "React", "HTML5", "CSS3", "Git", "Communication"]:
            add_skill(profile, name, "self")
        profile.experience_years = calculate_total_experience_years(profile)
        sync_skill_text(profile)
        pool["chloe"] = profile

        return pool

    # -- jobs -------------------------------------------------------------

    def _seed_jobs(self, employer: Profile):
        jobs = {}

        jobs["fullstack"] = JobVacancy.objects.create(
            employer=employer,
            title="Junior Full-Stack Web Developer",
            category="IT",
            location="BGC, Taguig City / Hybrid",
            description=(
                "Join the NovaTech product engineering team and ship features "
                "across a Django + React stack. You will build REST endpoints, "
                "implement responsive interfaces, write tests, and work with "
                "designers and QA in two-week sprints."
            ),
            remarks=(
                "Hybrid setup (2 days on-site), equipment allowance, HMO on "
                "day one, and a structured 90-day onboarding plan."
            ),
            salary_range="₱35,000 - ₱50,000",
            status="Open",
            min_education="College graduate",
            required_certifications="Computer Systems Servicing NC II",
            required_experience_years=2,
            slots=5,
            remaining_slots=5,
        )
        for name in [
            "Python",
            "Django",
            "React",
            "JavaScript",
            "SQL",
            "Git",
        ]:
            JobSkillRequirement.objects.create(
                job_vacancy=jobs["fullstack"], skill=skill(name)
            )

        jobs["cloud"] = JobVacancy.objects.create(
            employer=employer,
            title="Full-Stack Cloud Engineer",
            category="IT",
            location="BGC, Taguig City / Hybrid",
            description=(
                "Own services from Django API to container deployment. The role "
                "combines backend feature work with Docker-based delivery on AWS, "
                "including observability and cost-aware scaling."
            ),
            remarks="Cloud certification sponsorship after regularization.",
            salary_range="₱45,000 - ₱60,000",
            status="Open",
            min_education="College graduate",
            required_certifications="",
            required_experience_years=3,
            slots=3,
            remaining_slots=3,
        )
        for name in [
            "Python",
            "Django",
            "Docker",
            "AWS",
            "REST API Development",
        ]:
            JobSkillRequirement.objects.create(
                job_vacancy=jobs["cloud"], skill=skill(name)
            )

        jobs["frontend"] = JobVacancy.objects.create(
            employer=employer,
            title="Frontend React Engineer",
            category="IT",
            location="BGC, Taguig City / Hybrid",
            description=(
                "Own the component library and candidate-facing interfaces for "
                "NovaTech's logistics products. Strong React and TypeScript "
                "fundamentals are required; accessibility and design-system "
                "experience are a plus."
            ),
            remarks="Hybrid setup, design-system budget, and conference allowance.",
            salary_range="₱38,000 - ₱55,000",
            status="Open",
            min_education="College graduate",
            required_certifications="",
            required_experience_years=2,
            slots=3,
            remaining_slots=3,
        )
        for name in [
            "React",
            "TypeScript",
            "Tailwind CSS",
            "JavaScript",
            "Git",
        ]:
            JobSkillRequirement.objects.create(
                job_vacancy=jobs["frontend"], skill=skill(name)
            )

        jobs["support"] = JobVacancy.objects.create(
            employer=employer,
            title="IT Support Specialist",
            category="IT",
            location="Bocaue, Bulacan / On-site",
            description=(
                "Provide first-line support for desktops, network connectivity, "
                "and business applications. Document fixes, maintain asset "
                "records, and escalate recurring issues to engineering."
            ),
            remarks="TESDA NC II holders preferred; certification renewal supported.",
            salary_range="₱20,000 - ₱28,000",
            status="Open",
            min_education="Vocational / Tech Grad",
            required_certifications="Computer Systems Servicing NC II",
            required_experience_years=1,
            slots=4,
            remaining_slots=4,
        )
        for name in [
            "Networking",
            "Windows",
            "Technical Support",
            "Customer Service Orientation",
        ]:
            JobSkillRequirement.objects.create(
                job_vacancy=jobs["support"], skill=skill(name)
            )

        # Deliberately left without a referral so the live walkthrough can
        # demonstrate a real 1-click application from the Job Board.
        jobs["apply"] = JobVacancy.objects.create(
            employer=employer,
            title="Junior Python Developer",
            category="IT",
            location="BGC, Taguig City / Hybrid",
            description=(
                "Build internal automation and reporting services with Python "
                "and Django. This is the recommended live-application target for "
                "the demo applicant because all required skills are an exact match."
            ),
            remarks="Mentorship program and study leave for certification exams.",
            salary_range="₱32,000 - ₱42,000",
            status="Open",
            min_education="College graduate",
            required_certifications="",
            required_experience_years=2,
            slots=2,
            remaining_slots=2,
        )
        for name in [
            "Python",
            "Django",
            "SQL",
            "Git",
            "REST API Development",
        ]:
            JobSkillRequirement.objects.create(
                job_vacancy=jobs["apply"], skill=skill(name)
            )

        return jobs

    # -- pipeline ---------------------------------------------------------

    def _seed_referrals(self, applicant: Profile, pool, jobs):
        today = timezone.localdate()

        def create(profile, job, status, days_ago, **extra):
            return Referral.objects.create(
                applicant=profile,
                job_vacancy=job,
                date_referred=today - timedelta(days=days_ago),
                status=status,
                **extra,
            )

        # Demo applicant: one active interview, one offer, one fresh application.
        create(
            applicant,
            jobs["fullstack"],
            "Interviewing",
            12,
            interview_stage="Online or Face-to-Face (F2F) Assessment",
            interview_mode="Online Video Assessment",
            interview_stage_notes=(
                "Passed resume screening. Technical panel scheduled; prepare "
                "a walkthrough of the CloudBridge logistics dashboard."
            ),
            screening_evaluation={
                "scores": {
                    "js_ts": 4,
                    "html_css": 4,
                    "components": 4,
                    "api_async": 3,
                    "tooling": 4,
                    "logic": 4,
                    "testing": 3,
                },
                "total": 89,
                "notes": "Strong Django/React evidence; confirm testing depth in panel.",
                "evaluated_by": "NovaTech Solutions Inc.",
                "evaluated_at": timezone.now().isoformat(),
            },
        )
        create(
            applicant,
            jobs["cloud"],
            "Accepted — Awaiting Onboarding",
            9,
            accepted_position="Full-Stack Cloud Engineer",
            accepted_salary="₱52,000",
            reporting_date=today + timedelta(days=10),
            work_location="BGC, Taguig City / Hybrid",
            employment_type="Probationary",
            probationary_period_months=6,
            employer_remarks=(
                "Offer extended after final panel. Please upload NBI clearance, "
                "medical certificate, and government numbers before onboarding."
            ),
        )
        create(
            applicant,
            jobs["support"],
            "Pending",
            3,
            interview_stage="Initial Assessment",
        )

        # Pool candidates spread across lanes.
        create(
            pool["bianca"],
            jobs["frontend"],
            "Interviewing",
            10,
            interview_stage="Hiring Manager / Final Interview",
            interview_mode="Face-to-Face (F2F)",
            interview_stage_notes="Final culture and architecture fit with the VP of Engineering.",
            screening_evaluation={
                "scores": {
                    "js_ts": 4,
                    "html_css": 4,
                    "components": 3,
                    "api_async": 3,
                    "tooling": 4,
                    "logic": 3,
                    "testing": 2,
                },
                "total": 86,
                "notes": "Excellent TypeScript depth; frontend architecture strong.",
                "evaluated_by": "NovaTech Solutions Inc.",
                "evaluated_at": timezone.now().isoformat(),
            },
        )
        create(
            pool["paolo"],
            jobs["cloud"],
            "Pending",
            4,
            interview_stage="Initial Assessment",
        )
        create(
            pool["rafael"],
            jobs["support"],
            "Pending",
            2,
            interview_stage="Initial Assessment",
        )
        create(
            pool["chloe"],
            jobs["fullstack"],
            "Hired — Probationary",
            45,
            accepted_position="Junior Web Developer",
            accepted_salary="₱38,000",
            reporting_date=today - timedelta(days=30),
            actual_start_date=today - timedelta(days=30),
            work_location="BGC, Taguig City / Hybrid",
            employment_type="Probationary",
            probationary_period_months=6,
            reported_for_work="Reported",
            pre_employment_status="Complete",
            employer_remarks="Onboarding complete; 30-day check scheduled.",
        )

    def _seed_interviews(self, employer, applicant, pool, jobs):
        tomorrow = timezone.localdate() + timedelta(days=1)
        Interview.objects.create(
            employer=employer,
            title=f"Technical Panel - {applicant.user.get_full_name()}",
            candidate=applicant,
            vacancy=jobs["fullstack"],
            date=tomorrow,
            start_time=time(10, 0),
            interview_type="Video Call",
            round_name="Technical Interview",
            meeting_link="https://meet.google.com/skillup-demo-panel",
            notes="Live coding: Django REST endpoint + React component walkthrough.",
        )
        Interview.objects.create(
            employer=employer,
            title=f"Final Interview - {pool['bianca'].user.get_full_name()}",
            candidate=pool["bianca"],
            vacancy=jobs["frontend"],
            date=tomorrow,
            start_time=time(14, 0),
            interview_type="In-Person",
            round_name="Final Round",
            meeting_link="",
            notes="On-site final interview with the VP of Engineering.",
        )

    def _seed_reviews(self, employer, pool):
        CompanyReview.objects.create(
            employer=employer,
            reviewer=pool["chloe"],
            rating=5,
            review_title="Smooth, transparent onboarding",
            review_text=(
                "The interview stages were clear and the recruiter shared "
                "timelines after every round. My first 30 days were structured "
                "and the team had a real onboarding plan."
            ),
            employment_status="Current Employee",
            pros="Clear process, responsive recruiter, structured onboarding.",
            cons="Hybrid schedule requires advance planning.",
            is_anonymous=False,
        )
        CompanyReview.objects.create(
            employer=employer,
            reviewer=pool["bianca"],
            rating=4,
            review_title="Technical panel was fair and realistic",
            review_text=(
                "The live coding exercise matched the actual work. Feedback came "
                "within two days and the offer details were explained clearly."
            ),
            employment_status="Interviewee",
            pros="Realistic technical assessment and fast feedback.",
            cons="The final round took slightly longer than the posted timeline.",
            is_anonymous=True,
        )

    # -- LMS --------------------------------------------------------------

    def _seed_training(self, applicant: Profile):
        bootcamp = (
            TrainingProgram.objects.filter(id=7).first()
            or TrainingProgram.objects.filter(
                title__icontains="Full-Stack Web Development"
            ).first()
        )
        database = (
            TrainingProgram.objects.filter(id=13).first()
            or TrainingProgram.objects.filter(
                title__icontains="Database Management"
            ).first()
        )

        # The demo may edit these two courses live (for example, uploading a
        # lesson image). Reset their custom curriculum so the next seed run
        # restores the canonical default module/lesson structure.
        for program in (bootcamp, database):
            if program and program.custom_curriculum:
                program.custom_curriculum = {}
                program.save(update_fields=["custom_curriculum"])

        if bootcamp:
            curriculum = get_program_curriculum(bootcamp)
            lesson_ids = [
                lesson["id"]
                for module in curriculum["modules"]
                for lesson in module["lessons"]
            ]
            exam_ids = [
                lesson["id"]
                for module in curriculum["modules"]
                for lesson in module["lessons"]
                if lesson.get("type") == "assessment"
            ]
            completed = [lid for lid in lesson_ids if lid not in exam_ids]
            enrollment, _ = TrainingEnrollment.objects.update_or_create(
                profile=applicant,
                training_program=bootcamp,
                defaults={
                    "status": "Enrolled",
                    "progress_percentage": int(
                        round(len(completed) / len(lesson_ids) * 100)
                    )
                    if lesson_ids
                    else 90,
                    "completed_lessons": completed,
                    "time_spent_seconds": 3900,
                    "assessment_score": 0,
                    "assessment_results": {},
                    "certificate_code": None,
                    "certificate_issued_at": None,
                },
            )

        if database:
            curriculum = get_program_curriculum(database)
            all_ids = [
                lesson["id"]
                for module in curriculum["modules"]
                for lesson in module["lessons"]
            ]
            code = "SKILLUP-TESDA-2026-D3M001"
            while TrainingEnrollment.objects.filter(certificate_code=code).exclude(
                profile=applicant
            ).exists():
                code = f"SKILLUP-TESDA-2026-{secrets.token_hex(3).upper()}"
            enrollment, _ = TrainingEnrollment.objects.update_or_create(
                profile=applicant,
                training_program=database,
                defaults={
                    "status": "Attended",
                    "progress_percentage": 100,
                    "completed_lessons": all_ids,
                    "time_spent_seconds": 4200,
                    "assessment_score": 88,
                    "assessment_results": {
                        "score": 88,
                        "passed": True,
                        "correct_count": 7,
                        "total_count": 8,
                        "domain_breakdown": [],
                        "submitted_at": (
                            timezone.now() - timedelta(days=25)
                        ).strftime("%b %d, %Y %I:%M %p"),
                    },
                    "certificate_code": code,
                    "certificate_issued_at": timezone.now() - timedelta(days=25),
                },
            )
            Certification.objects.get_or_create(
                profile=applicant,
                name=f"{database.title} Certificate of Competency",
                defaults={
                    "issuing_organization": f"{database.provider} & SKILLUP Certified",
                    "issue_date": timezone.localdate() - timedelta(days=25),
                    "is_tesda": True,
                },
            )
            for program_skill in database.all_skills:
                ApplicantSkill.objects.get_or_create(
                    profile=applicant,
                    skill=program_skill,
                    defaults={"source": "training"},
                )

        sync_skill_text(applicant)
        enrollments = applicant.training_enrollments.all()
        if enrollments.exists():
            applicant.training_progress_percentage = int(
                sum(e.progress_percentage for e in enrollments) / enrollments.count()
            )
            applicant.training_progress_title = "Full-Stack Web Development Bootcamp"
            applicant.save(
                update_fields=[
                    "training_progress_percentage",
                    "training_progress_title",
                    "skills",
                ]
            )

    # -- notifications & slots -------------------------------------------

    def _seed_notifications(self, admin, employer, applicant, pending_employer, jobs):
        Notification.objects.filter(user__email__in=DEMO_EMAILS).delete()

        Notification.objects.create(
            user=applicant.user,
            message=(
                "Job offer received: Full-Stack Cloud Engineer at NovaTech "
                "Solutions Inc. Review the offer and upload your pre-employment "
                "requirements from My Applications."
            ),
            notif_type="offer_sent",
            applicant_profile=applicant,
            vacancy=jobs["cloud"],
        )
        Notification.objects.create(
            user=applicant.user,
            message=(
                "Interview scheduled tomorrow at 10:00 AM for Junior Full-Stack "
                "Web Developer. The meeting link is in My Applications."
            ),
            notif_type="general",
            applicant_profile=applicant,
            vacancy=jobs["fullstack"],
        )
        Notification.objects.create(
            user=applicant.user,
            message=(
                "Your Database Management & Advanced SQL Querying certificate "
                "is now verifiable through the public certificate portal."
            ),
            notif_type="general",
            applicant_profile=applicant,
        )

        Notification.objects.create(
            user=employer.user,
            message=(
                "Match Alert: Alexis Cruz is a 100% skill match for Junior "
                "Full-Stack Web Developer."
            ),
            notif_type="match_alert",
            applicant_profile=applicant,
            vacancy=jobs["fullstack"],
        )
        Notification.objects.create(
            user=employer.user,
            message=(
                "Reminder: 2 interviews scheduled tomorrow (10:00 AM technical "
                "panel and 2:00 PM final round)."
            ),
            notif_type="general",
            vacancy=jobs["fullstack"],
        )

        Notification.objects.create(
            user=admin.user,
            message=(
                "Employer accreditation pending: BlueWave Logistics Corp. "
                "submitted registration documents for PESO review."
            ),
            notif_type="general",
        )
        Notification.objects.create(
            user=admin.user,
            message=(
                "Training completion posted: Alexis Cruz finished Database "
                "Management & Advanced SQL Querying with 88%."
            ),
            notif_type="general",
            applicant_profile=applicant,
        )

    def _refresh_slots(self, jobs):
        active_statuses = [
            "Pending",
            "Interviewing",
            "Accepted — Awaiting Onboarding",
            "Confirmed — Onboarding",
        ]
        for job in jobs.values():
            active = job.referrals.filter(status__in=active_statuses).count()
            job.remaining_slots = max(0, job.slots - active)
            job.save(update_fields=["remaining_slots"])

    # -- output -----------------------------------------------------------

    def _print_summary(self, applicant: Profile, jobs):
        base = "http://127.0.0.1:8000"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("SKILLUP demo accounts are ready"))
        self.stdout.write("=" * 68)
        self.stdout.write(f"Password for every demo account: {DEMO_PASSWORD}")
        self.stdout.write("")
        self.stdout.write("ADMIN      demo.admin@skillup.ph")
        self.stdout.write(f"           {base}/admin-dashboard/")
        self.stdout.write("EMPLOYER   demo.employer@skillup.ph")
        self.stdout.write(f"           {base}/employer-dashboard/")
        self.stdout.write("APPLICANT  demo.applicant@skillup.ph   (fully prepared)")
        self.stdout.write(f"           {base}/applicant-dashboard/")
        self.stdout.write("APPLICANT  demo.applicant.fresh@skillup.ph   (blank slate)")
        self.stdout.write(f"           {base}/applicant/profile/")
        self.stdout.write("")
        self.stdout.write("Demo jobs:")
        for job in jobs.values():
            self.stdout.write(
                f"  #{job.id}  {job.title}  ({job.remaining_slots}/{job.slots} slots open)"
            )
        bootcamp = applicant.training_enrollments.filter(
            training_program__title__icontains="Full-Stack"
        ).first()
        if bootcamp:
            self.stdout.write(
                f"LMS enrollment #{bootcamp.id}: {bootcamp.progress_percentage}% "
                f"progress, {bootcamp.time_spent_seconds // 60} min seat time"
            )
        cert = applicant.training_enrollments.exclude(certificate_code__isnull=True).first()
        if cert:
            self.stdout.write(f"Certificate code: {cert.certificate_code}")
        self.stdout.write("=" * 68)

        # Write current IDs/URLs so the presenter never relies on stale links.
        try:
            links_path = Path(settings.BASE_DIR) / "docs" / "DEMO_LINKS.txt"
            lines = [
                "SKILLUP DEMO LINKS (generated by seed_demo_accounts)",
                f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M')} UTC",
                "",
                "LOGIN",
                f"  {base}/login/",
                f"  Admin:     demo.admin@skillup.ph / {DEMO_PASSWORD}",
                f"  Employer:  demo.employer@skillup.ph / {DEMO_PASSWORD}",
                f"  Applicant: demo.applicant@skillup.ph / {DEMO_PASSWORD}",
                f"  Blank:     demo.applicant.fresh@skillup.ph / {DEMO_PASSWORD}",
                "",
                "DASHBOARDS",
                f"  Admin:     {base}/admin-dashboard/",
                f"  Employer:  {base}/employer-dashboard/",
                f"  Applicant: {base}/applicant-dashboard/",
                f"  Profile:   {base}/applicant/profile/",
                f"  Job Board: {base}/applicant/job-search/",
                f"  Learning:  {base}/applicant/training/",
                f"  Pipeline:  {base}/employer/hiring-tracker/",
                "",
                "DEMO VACANCIES",
            ]
            for job in jobs.values():
                lines.append(
                    f"  #{job.id}  {job.title}  ({job.remaining_slots}/{job.slots} slots)  "
                    f"{base}/applicant/skill-gap/?job_id={job.id}"
                )
            if bootcamp:
                lines.extend(
                    [
                        "",
                        "LMS",
                        f"  Enrollment #{bootcamp.id}: {bootcamp.progress_percentage}% progress, "
                        f"{bootcamp.time_spent_seconds // 60} min seat time",
                        f"  Course: {base}/applicant/course/{bootcamp.id}/",
                    ]
                )
            if cert:
                lines.extend(
                    [
                        "",
                        "CERTIFICATE",
                        f"  Code: {cert.certificate_code}",
                        f"  Verify: {base}/verify-certificate/?code={cert.certificate_code}",
                    ]
                )
            lines.extend(
                [
                    "",
                    "RESET",
                    "  .\\.venv\\Scripts\\python.exe manage.py seed_demo_accounts",
                    "",
                ]
            )
            links_path.parent.mkdir(parents=True, exist_ok=True)
            links_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass
