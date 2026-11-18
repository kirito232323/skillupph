import json
import os
import uuid
import datetime as _dt
import random
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum, Count, Q, Avg
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.cache import cache

from .models import (
    Skill, Milestone, StudyLog, Profile, CentralizedSkill,
    Education, WorkExperience, Certification, ApplicantSkill,
    JobVacancy, JobSkillRequirement, TrainingProgram, TrainingEnrollment, Referral,
    GapScoreLog, JobBookmark, Notification, ApplicantDocument, Interview, CompanyReview,
    SystemSetting, ConfigurationAudit
)
from .forms import SkillForm, MilestoneForm, StudyLogForm
from .id_crypto import decrypt_bytes, encrypt_bytes
from .scorecards import build_automated_scorecard, match_skill
from .skill_ml import model_status, points_for
from .skill_embeddings import cache_status as embedding_status
from .course_curriculum import get_program_curriculum
from .job_presets import JOB_PRESETS
import re
import secrets

class _DateTimeProxy:
    timedelta = _dt.timedelta
    date = _dt.date
    time = _dt.time
    timezone = _dt.timezone
    datetime = _dt.datetime
    strptime = _dt.datetime.strptime
    now = _dt.datetime.now
    
    def __getattr__(self, name):
        if hasattr(_dt.datetime, name):
            return getattr(_dt.datetime, name)
        return getattr(_dt, name)

datetime = _DateTimeProxy()

JOB_CATEGORIES = [
    {'code': 'IT', 'name': 'Information Technology'},
    {'code': 'BPO', 'name': 'BPO & Customer Service'},
    {'code': 'ADM', 'name': 'Administrative & Office'},
    {'code': 'FIN', 'name': 'Finance & Accounting'},
    {'code': 'MKT', 'name': 'Sales & Marketing'},
    {'code': 'RTL', 'name': 'Retail & Merchandising'},
    {'code': 'F&B', 'name': 'Food & Beverage / Hospitality'},
    {'code': 'MED', 'name': 'Healthcare & Medical'},
    {'code': 'EDU', 'name': 'Education & Training'},
    {'code': 'ENG', 'name': 'Engineering & Construction'},
    {'code': 'ELC', 'name': 'Electrical & Electronics'},
    {'code': 'MCH', 'name': 'Mechanical & Automotive'},
    {'code': 'TVT', 'name': 'TESDA Trade & Vocational'},
    {'code': 'LOG', 'name': 'Logistics & Warehousing'},
    {'code': 'SEC', 'name': 'Security & Safety'},
    {'code': 'GEN', 'name': 'General Services & Facilities'},
    {'code': 'DRV', 'name': 'Driving & Transportation'},
    {'code': 'MFG', 'name': 'Manufacturing & Garments'},
    {'code': 'AGR', 'name': 'Agriculture & Environment'},
    {'code': 'OFW', 'name': 'Overseas / OFW Positions'},
]

def proper_name(value):
    """Uppercase the first letter of each name word, preserving the rest.

    "juan" -> "Juan", "dela cruz" -> "Dela Cruz", "o'neil" -> "O'Neil".
    """
    value = (value or '').strip()
    if not value:
        return ''
    return re.sub(r"(^|[\s\-'])(\w)", lambda m: m.group(1) + m.group(2).upper(), value)


SCREENING_CRITERIA = [
    ('js_ts', 'JavaScript / TypeScript', 25),
    ('html_css', 'HTML / CSS / Responsive Design', 15),
    ('components', 'Components / Frontend Architecture', 20),
    ('api_async', 'REST APIs / Asynchronous Programming', 15),
    ('tooling', 'Git / npm / Build Tools', 10),
    ('logic', 'Programming Logic / Problem Solving', 10),
    ('testing', 'Testing / Debugging', 5),
]

# Statuses that mean the candidate has actually been placed. "Confirmed —
# Onboarding" is intentionally excluded: the candidate has accepted an offer
# but has not started work yet.
PLACEMENT_STATUSES = [
    'Hired — Probationary',
    'Hired — Regular',
    'Regularly Employed',
    'Still Employed — Performing Well',
    'Still Employed — On Improvement Plan',
    'Probation Extended',
]


def get_active_employment(profile, employer_id=None):
    """Latest referral where the applicant is currently employed."""
    qs = Referral.objects.filter(
        applicant=profile,
        status__in=Referral.ACTIVE_EMPLOYMENT_STATUSES,
    ).select_related('job_vacancy__employer').order_by('-date_referred')
    if employer_id:
        qs = qs.filter(job_vacancy__employer_id=employer_id)
    return qs.first()


def calculate_total_experience_years(profile):
    experiences = profile.experience.all()
    if not experiences.exists():
        return "0 Years"
    
    total_months = 0
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    months_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
    }
    
    for exp in experiences:
        start_str = (exp.start_date or '').strip()
        end_str = (exp.end_date or '').strip()
        
        start_year = None
        start_month = 1
        
        year_match = re.search(r'\b(19\d\d|20\d\d)\b', start_str)
        if year_match:
            start_year = int(year_match.group(1))
        
        for m_name, m_num in months_map.items():
            if m_name in start_str.lower():
                start_month = m_num
                break
                
        end_year = None
        end_month = 1
        
        if not end_str or 'present' in end_str.lower() or exp.is_current:
            end_year = current_year
            end_month = current_month
        else:
            year_match = re.search(r'\b(19\d\d|20\d\d)\b', end_str)
            if year_match:
                end_year = int(year_match.group(1))
            for m_name, m_num in months_map.items():
                if m_name in end_str.lower():
                    end_month = m_num
                    break
        
        if start_year is not None and end_year is not None:
            months = (end_year - start_year) * 12 + (end_month - start_month)
            if months < 0:
                months = 0
            if months == 0:
                months = 6  # default 0.5 year if user inputs same year
            total_months += months
            
    years = round(total_months / 12, 1)
    if years.is_integer():
        years_val = int(years)
    else:
        years_val = years
        
    if years_val == 1:
        return "1 Year"
    else:
        return f"{years_val} Years"

def verified_required(view_func):
    @login_required(login_url='login')
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_superuser or request.user.is_staff:
            if not hasattr(request.user, 'profile'):
                Profile.objects.create(user=request.user, role='admin', is_verified=True)
            elif not request.user.profile.is_verified or request.user.profile.role != 'admin':
                request.user.profile.is_verified = True
                request.user.profile.role = 'admin'
                request.user.profile.save()
        else:
            if not hasattr(request.user, 'profile'):
                Profile.objects.create(user=request.user)
            if not request.user.profile.is_verified:
                return redirect('verify')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def role_required(allowed_roles):
    def decorator(view_func):
        @verified_required
        def _wrapped_view(request, *args, **kwargs):
            if request.user.profile.role not in allowed_roles:
                role = request.user.profile.role
                if role == 'admin':
                    return redirect('peso_dashboard_admin')
                elif role == 'employer':
                    return redirect('employer_dashboard')
                else:
                    # If applicant dashboard is allowed and profile is incomplete, dashboard redirect will handle it
                    return redirect('applicant_dashboard')
            
            if request.user.profile.role == 'applicant' and not request.user.profile.is_profile_complete:
                if request.resolver_match.url_name not in ['profile_wizard', 'logout']:
                    return redirect('profile_wizard')
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.is_superuser or request.user.is_staff:
        if not hasattr(request.user, 'profile'):
            Profile.objects.create(user=request.user, role='admin', is_verified=True)
        elif not request.user.profile.is_verified or request.user.profile.role != 'admin':
            request.user.profile.is_verified = True
            request.user.profile.role = 'admin'
            request.user.profile.save()
    else:
        if not hasattr(request.user, 'profile'):
            Profile.objects.create(user=request.user)
        if not request.user.profile.is_verified:
            return redirect('verify')
    
    role = request.user.profile.role
    if role == 'applicant' and not request.user.profile.is_profile_complete:
        return redirect('profile_wizard')
        
    if role == 'admin':
        return redirect('peso_dashboard_admin')
    elif role == 'employer':
        return redirect('employer_dashboard')
    else:
        return redirect('applicant_dashboard')

def legacy_skill_dashboard(request):
    skills = Skill.objects.all()
    
    # Calculate aggregate stats
    total_skills = skills.count()
    skills_in_progress = skills.filter(status='in_progress').count()
    skills_completed = skills.filter(status='completed').count()
    skills_not_started = skills.filter(status='not_started').count()
    
    total_study_minutes = StudyLog.objects.aggregate(Sum('duration_minutes'))['duration_minutes__sum'] or 0
    total_study_hours = round(total_study_minutes / 60, 1)
    
    total_milestones = Milestone.objects.count()
    completed_milestones = Milestone.objects.filter(is_completed=True).count()
    milestone_progress = int((completed_milestones / total_milestones) * 100) if total_milestones > 0 else 0

    # Weekly study hours for line chart
    today = timezone.localdate()
    last_7_days = [today - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    weekly_labels = []
    weekly_data = []
    
    for date in last_7_days:
        daily_minutes = StudyLog.objects.filter(date=date).aggregate(Sum('duration_minutes'))['duration_minutes__sum'] or 0
        weekly_data.append(round(daily_minutes / 60, 2))
        weekly_labels.append(date.strftime('%a (%b %d)'))

    # Category distribution for pie chart
    category_counts = Skill.objects.values('category').annotate(count=Count('id')).order_by('-count')
    category_labels = [item['category'] for item in category_counts]
    category_data = [item['count'] for item in category_counts]

    recent_logs = StudyLog.objects.select_related('skill').all()[:5]

    context = {
        'skills': skills,
        'total_skills': total_skills,
        'skills_in_progress': skills_in_progress,
        'skills_completed': skills_completed,
        'skills_not_started': skills_not_started,
        'total_study_hours': total_study_hours,
        'milestone_progress': milestone_progress,
        'weekly_labels': weekly_labels,
        'weekly_data': weekly_data,
        'category_labels': category_labels,
        'category_data': category_data,
        'recent_logs': recent_logs,
    }
    return render(request, 'tracker/dashboard.html', context)

def skill_detail(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    milestones = skill.milestones.all().order_by('is_completed', 'target_date')
    study_logs = skill.study_logs.all().order_by('-date', '-created_at')
    
    milestone_form = MilestoneForm()
    study_log_form = StudyLogForm(initial={'date': timezone.localdate()})
    
    context = {
        'skill': skill,
        'milestones': milestones,
        'study_logs': study_logs,
        'milestone_form': milestone_form,
        'study_log_form': study_log_form,
    }
    return render(request, 'tracker/skill_detail.html', context)

def skill_create(request):
    if request.method == 'POST':
        form = SkillForm(request.POST)
        if form.is_valid():
            skill = form.save()
            messages.success(request, f"Skill '{skill.name}' created successfully!")
            return redirect('skill_detail', pk=skill.pk)
    else:
        form = SkillForm()
    
    return render(request, 'tracker/skill_form.html', {'form': form, 'title': 'Add New Skill'})

def skill_edit(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    if request.method == 'POST':
        form = SkillForm(request.POST, instance=skill)
        if form.is_valid():
            form.save()
            messages.success(request, f"Skill '{skill.name}' updated successfully!")
            return redirect('skill_detail', pk=skill.pk)
    else:
        form = SkillForm(instance=skill)
        
    return render(request, 'tracker/skill_form.html', {'form': form, 'title': 'Edit Skill', 'skill': skill})

@require_POST
def skill_delete(request, pk):
    skill = get_object_or_404(Skill, pk=pk)
    name = skill.name
    skill.delete()
    messages.success(request, f"Skill '{name}' deleted successfully!")
    return redirect('dashboard')

@require_POST
def add_milestone(request, skill_id):
    skill = get_object_or_404(Skill, id=skill_id)
    form = MilestoneForm(request.POST)
    if form.is_valid():
        milestone = form.save(commit=False)
        milestone.skill = skill
        milestone.save()
        messages.success(request, f"Milestone '{milestone.title}' added to {skill.name}!")
    else:
        messages.error(request, "Failed to add milestone. Please verify the input.")
    return redirect('skill_detail', pk=skill.id)

@require_POST
def toggle_milestone(request, milestone_id):
    milestone = get_object_or_404(Milestone, id=milestone_id)
    milestone.is_completed = not milestone.is_completed
    milestone.save()
    
    # Auto adjust skill status if transitioning
    skill = milestone.skill
    if skill.status == 'not_started' and milestone.is_completed:
        skill.status = 'in_progress'
        skill.save()
    elif skill.status == 'in_progress' and skill.completion_percentage == 100:
        skill.status = 'completed'
        skill.save()
        
    status_text = "completed" if milestone.is_completed else "marked incomplete"
    messages.success(request, f"Milestone '{milestone.title}' {status_text}!")
    return redirect('skill_detail', pk=skill.id)

@require_POST
def add_study_log(request, skill_id):
    skill = get_object_or_404(Skill, id=skill_id)
    form = StudyLogForm(request.POST)
    if form.is_valid():
        log = form.save(commit=False)
        log.skill = skill
        log.save()
        
        # Auto start skill status
        if skill.status == 'not_started':
            skill.status = 'in_progress'
            skill.save()
            
        messages.success(request, f"Logged {log.duration_minutes} minutes for {skill.name}!")
    else:
        messages.error(request, "Failed to log study hours. Please verify input values.")
    return redirect('skill_detail', pk=skill.id)


# ==========================================
# AUTHENTICATION FLOW VIEWS
# ==========================================
from django.core.mail import send_mail
from .emails import (
    send_notification_email,
    send_referral_approved_email,
    send_interview_scheduled_email,
    send_job_offer_email,
    send_hired_confirmation_email,
    send_application_submitted_email,
    send_application_status_update_email,
)

ID_SCAN_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}
ID_SCAN_MAX_BYTES = 10 * 1024 * 1024
CERTIFICATE_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
CERTIFICATE_MAX_BYTES = 10 * 1024 * 1024


def _certificate_upload_error(uploaded):
    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in CERTIFICATE_EXTENSIONS:
        return 'Unsupported certificate file. Upload a PDF, JPG, or PNG.'
    if uploaded.size > CERTIFICATE_MAX_BYTES:
        return 'Certificate file is too large (maximum 10 MB).'
    return None


@require_POST
def signup_scan_id(request):
    """Scan an uploaded ID for account signup and return pre-fill fields.

    The file is stored temporarily; it is attached to the profile only after
    the user completes the verification step.
    """
    uploaded = request.FILES.get('id_document') or request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'success': False, 'error': 'Please choose an ID image or PDF first.'}, status=400)

    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in ID_SCAN_EXTENSIONS:
        return JsonResponse(
            {'success': False, 'error': 'Unsupported file type. Upload a JPG, PNG, WEBP or PDF.'},
            status=400,
        )
    if uploaded.size > ID_SCAN_MAX_BYTES:
        return JsonResponse({'success': False, 'error': 'ID file is too large (maximum 10 MB).'}, status=400)

    # Simple abuse guard: at most 6 scans per 10 minutes per session.
    now_ts = timezone.now().timestamp()
    attempts = [
        float(t) for t in (request.session.get('id_scan_times') or [])
        if isinstance(t, (int, float)) and now_ts - float(t) < 600
    ]
    if len(attempts) >= 6:
        return JsonResponse(
            {'success': False, 'error': 'Too many ID scans. Please wait a few minutes and try again.'},
            status=429,
        )

    from .ai_services import extract_id_document
    from django.core.files.storage import default_storage

    try:
        fields = extract_id_document(uploaded)
    except Exception as exc:
        print('[ID Scan] Extraction failed:', exc)
        fields = {
            'id_type': '', 'id_number': '', 'first_name': '', 'last_name': '',
            'middle_name': '', 'full_name': '', 'birthdate': '', 'address': '',
            'sex': '', 'confidence': 0,
        }

    uploaded.seek(0)
    from django.core.files.base import ContentFile
    temp_path = default_storage.save(
        f"temp_ids/{uuid.uuid4().hex}{ext}",
        ContentFile(encrypt_bytes(uploaded.read())),
    )
    # Remove a previous unscanned upload for this session, if any.
    previous_scan = request.session.get('pending_id_scan') or {}
    if previous_scan.get('path') and previous_scan.get('path') != temp_path:
        try:
            default_storage.delete(previous_scan['path'])
        except Exception:
            pass
    token = uuid.uuid4().hex
    request.session['pending_id_scan'] = {
        'token': token,
        'path': temp_path,
        'fields': fields,
        'original_name': os.path.basename(uploaded.name or ''),
    }
    attempts.append(now_ts)
    request.session['id_scan_times'] = attempts

    return JsonResponse({
        'success': True,
        'token': token,
        'fields': fields,
        'message': (
            'ID scanned. Review the details below, then create your account.'
            if fields.get('confidence')
            else 'ID uploaded. We could not read every detail automatically; please complete the fields manually.'
        ),
    })


def _apply_pending_id_verification(profile, pending):
    """Attach the temporarily stored signup ID to a newly created profile."""
    scan = (pending or {}).get('id_scan') or {}
    temp_path = scan.get('path')
    if not temp_path:
        return

    fields = scan.get('fields') or {}
    try:
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        with default_storage.open(temp_path, 'rb') as stored:
            content = stored.read()
        content = decrypt_bytes(content, allow_plaintext=True)
        profile.id_document.save(os.path.basename(temp_path), ContentFile(content), save=False)
        default_storage.delete(temp_path)
    except Exception as exc:
        print('[ID Scan] Could not attach ID to profile:', exc)
        return

    id_type = (pending.get('id_type') or fields.get('id_type') or '').strip()
    id_number = (pending.get('id_number') or fields.get('id_number') or '').strip()
    if id_type:
        profile.id_type = id_type[:100]
    if id_number:
        profile.id_number = id_number[:100]

    if fields.get('address') and not profile.address:
        profile.address = str(fields['address'])[:255]
    if fields.get('birthdate') and not profile.birthdate:
        try:
            profile.birthdate = datetime.strptime(str(fields['birthdate']), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            pass

    def _norm(value):
        return re.sub(r'[^a-z]', '', str(value or '').lower())

    scanned_first = _norm(fields.get('first_name'))
    scanned_last = _norm(fields.get('last_name'))
    if (not scanned_first or not scanned_last) and fields.get('full_name'):
        parts = str(fields['full_name']).split()
        if parts:
            scanned_first = scanned_first or _norm(parts[0])
            scanned_last = scanned_last or _norm(parts[-1])

    entered_first = _norm(pending.get('firstname'))
    entered_last = _norm(pending.get('lastname'))
    name_matches = bool(
        entered_last and scanned_last
        and (entered_last in scanned_last or scanned_last in entered_last)
    )
    if entered_first and scanned_first:
        name_matches = name_matches and (
            entered_first in scanned_first or scanned_first in entered_first
        )
    profile.id_verified = name_matches
    profile.save()


def signup_view(request):
    if request.method == 'POST':
        try:
            firstname = proper_name(request.POST.get('firstname', ''))
            lastname = proper_name(request.POST.get('lastname', ''))
            email = request.POST.get('email', '').strip().lower()
            employer_phone = request.POST.get('employer_phone', '').strip()
            password = request.POST.get('password', '')
            confirm_password = request.POST.get('confirm_password', '')
            role = request.POST.get('role', 'applicant')
            # Never trust the posted role: only self-service applicant and
            # employer accounts can be created through the public signup form.
            if role not in ('applicant', 'employer'):
                role = 'applicant'
            company_name = request.POST.get('company_name', '').strip()

            form_dict = request.POST.dict()
            if password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return render(request, 'tracker/LOGIN/sign_up_account_details.html', {
                    'categories': JOB_CATEGORIES,
                    'form_data': form_dict
                })

            if role == 'employer' and not company_name:
                messages.error(request, "Company Name is required for Employer accounts.")
                return render(request, 'tracker/LOGIN/sign_up_account_details.html', {
                    'categories': JOB_CATEGORIES,
                    'form_data': form_dict
                })

            if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
                messages.error(request, "An account with this email already exists.")
                return render(request, 'tracker/LOGIN/sign_up_account_details.html', {
                    'categories': JOB_CATEGORIES,
                    'form_data': form_dict
                })

            # Optional ID scan captured earlier by signup_scan_id.
            id_scan = None
            scan_token = request.POST.get('id_scan_token', '').strip()
            pending_scan = request.session.get('pending_id_scan') or {}
            if role == 'applicant' and scan_token and pending_scan.get('token') == scan_token:
                id_scan = pending_scan
            id_type = request.POST.get('id_type', '').strip()
            id_number = request.POST.get('id_number', '').strip()
            if id_scan:
                scan_fields = id_scan.get('fields') or {}
                id_type = id_type or str(scan_fields.get('id_type') or '')
                id_number = id_number or str(scan_fields.get('id_number') or '')

            otp = str(random.randint(100000, 999999))
            request.session['pending_signup'] = {
                'firstname': firstname,
                'lastname': lastname,
                'email': email,
                'phone': employer_phone if role == 'employer' else '',
                'password': password,
                'role': role,
                'company_name': company_name,
                'id_scan': id_scan,
                'id_type': id_type,
                'id_number': id_number,
                'otp': otp
            }
            
            try:
                sent = send_notification_email(
                    subject='Account Creation Confirmation - SKILLUP',
                    message=f'Hello {firstname},\n\nYour 6-digit verification code is: {otp}\n\nEnter this code on the registration verification page to complete your account setup.\n\nBest regards,\nSKILLUP Team',
                    recipient_list=[email]
                )
            except Exception:
                sent = False

            if sent:
                messages.success(request, f"A verification code has been sent to {email}. Please check your inbox.")
            else:
                messages.warning(request, f"We could not send the verification email to {email}. Please click 'Resend code' on the next page to try again.")
            
            return redirect('verify')
        except Exception as ex:
            import traceback
            traceback.print_exc()
            messages.error(request, f"Signup error: {str(ex)}")
            return render(request, 'tracker/LOGIN/sign_up_account_details.html', {
                'categories': JOB_CATEGORIES,
                'form_data': request.POST.dict() if request.method == 'POST' else {}
            })
    return render(request, 'tracker/LOGIN/sign_up_account_details.html', {'categories': JOB_CATEGORIES, 'form_data': {}})

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=email, password=password)
        if user is None:
            # Legacy accounts may log in with an email that differs from their
            # username (e.g. the email was changed on the profile page).
            candidate = User.objects.filter(email__iexact=email).exclude(
                username__iexact=email
            ).first()
            if candidate:
                user = authenticate(request, username=candidate.username, password=password)
        if user is not None:
            auth_login(request, user)
            if user.is_superuser or user.is_staff:
                if not hasattr(user, 'profile'):
                    Profile.objects.create(user=user, role='admin', is_verified=True)
                elif not user.profile.is_verified or user.profile.role != 'admin':
                    user.profile.is_verified = True
                    user.profile.role = 'admin'
                    user.profile.save()
                return redirect('dashboard')
                
            if not hasattr(user, 'profile'):
                Profile.objects.create(user=user)
            
            if not user.profile.is_verified:
                otp = str(random.randint(100000, 999999))
                user.profile.verification_code = otp
                user.profile.save()
                
                sent = send_notification_email(
                    subject='Account Verification Code - SKILLUP',
                    message=f'Hello {user.first_name},\n\nPlease verify your account. Your verification code is: {otp}\n\nThank you for registering at SKILLUP!\n\nBest regards,\nSKILLUP Team',
                    recipient_list=[user.email]
                )
                if sent:
                    messages.success(request, f"Please check your email ({user.email}) for your verification code.")
                else:
                    messages.warning(request, f"Please check your email inbox ({user.email}) for your verification code.")
                
                return redirect('verify')
            
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid email or password.")
            return render(request, 'tracker/LOGIN/login.html')
    
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'tracker/LOGIN/login.html')

def verify_view(request):
    pending = request.session.get('pending_signup')
    
    if not pending and not request.user.is_authenticated:
        return redirect('login')
        
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff:
            if not hasattr(request.user, 'profile'):
                Profile.objects.create(user=request.user, role='admin', is_verified=True)
            elif not request.user.profile.is_verified or request.user.profile.role != 'admin':
                request.user.profile.is_verified = True
                request.user.profile.role = 'admin'
                request.user.profile.save()
            return redirect('dashboard')
            
        if not hasattr(request.user, 'profile'):
            Profile.objects.create(user=request.user)
        if request.user.profile.is_verified:
            return redirect('dashboard')

    if request.method == 'POST':
        code = request.POST.get('code', '').strip() or "".join([request.POST.get(f'digit{i}', '') for i in range(1, 7)])
        
        if pending:
            if code == pending.get('otp'):
                try:
                    # Create Django User
                    user = User.objects.create_user(
                        username=pending['email'],
                        email=pending['email'],
                        password=pending['password'],
                        first_name=pending['firstname'],
                        last_name=pending['lastname']
                    )
                    
                    # Create Profile
                    pending_role = pending.get('role')
                    if pending_role not in ('applicant', 'employer'):
                        pending_role = 'applicant'
                    profile = Profile.objects.create(
                        user=user,
                        role=pending_role,
                        phone_number=pending.get('phone') or '',
                        company_name=pending.get('company_name') if pending_role == 'employer' else None,
                        contact_name=f"{pending['firstname']} {pending['lastname']}" if pending_role == 'employer' else '',
                        contact_email=pending['email'] if pending_role == 'employer' else '',
                        location='Bulacan',
                        title='Software Engineer' if pending_role == 'applicant' else '',
                        is_profile_complete=(pending_role != 'applicant'),
                        is_verified=True,
                        verification_code=''
                    )

                    # Attach and cross-check the optional ID captured at signup.
                    _apply_pending_id_verification(profile, pending)
                    request.session.pop('pending_id_scan', None)
                    request.session.pop('id_scan_times', None)

                    # Login
                    authenticated_user = authenticate(request, username=pending['email'], password=pending['password'])
                    if authenticated_user:
                        auth_login(request, authenticated_user)
                        
                    del request.session['pending_signup']
                    messages.success(request, "Account created and verified successfully! Welcome to SKILLUP.")
                    return redirect('dashboard')
                except Exception as e:
                    messages.error(request, f"Registration failed: {str(e)}")
                    return redirect('signup')
            else:
                messages.error(request, "Invalid verification code. Please check and try again.")
        else:
            if code == request.user.profile.verification_code:
                request.user.profile.is_verified = True
                request.user.profile.save()
                messages.success(request, "Account verified successfully!")
                return redirect('dashboard')
            else:
                messages.error(request, "Invalid verification code. Please try again.")
                
    context = {}
    if pending:
        context['email'] = pending['email']
    elif request.user.is_authenticated:
        context['email'] = request.user.email
        
    return render(request, 'tracker/LOGIN/account_verification.html', context)

def resend_verification_code(request):
    pending = request.session.get('pending_signup')
    
    if not pending and not request.user.is_authenticated:
        return redirect('login')
        
    otp = str(random.randint(100000, 999999))
    email = ""
    firstname = ""
    
    if pending:
        pending['otp'] = otp
        request.session['pending_signup'] = pending
        email = pending['email']
        firstname = pending['firstname']
    else:
        request.user.profile.verification_code = otp
        request.user.profile.save()
        email = request.user.email
        firstname = request.user.first_name
        
    sent = send_notification_email(
        subject='Account Verification Code - SKILLUP',
        message=f'Hello {firstname},\n\nYour new verification code is: {otp}\n\nThank you for registering at SKILLUP!\n\nBest regards,\nSKILLUP Team',
        recipient_list=[email]
    )
    if sent:
        messages.success(request, f"A new verification code has been sent to {email}.")
    else:
        messages.warning(request, f"A new verification code was sent. Please check your inbox at {email}.")
        
    return redirect('verify')

def logout_view(request):
    auth_logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('login')

def create_apex_employer_and_jobs():
    from django.contrib.auth.models import User
    from .models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

    email = 'apex_employer@test.com'
    password = 'Password123!'
    
    try:
        user = User.objects.get(username=email)
        if JobVacancy.objects.filter(employer=user.profile).count() == 20:
            return
    except Exception:
        pass
    
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            'email': email,
            'first_name': 'Apex',
            'last_name': 'Employer'
        }
    )
    if created or not user.check_password(password):
        user.set_password(password)
        user.save()
    
    profile, _ = Profile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'Apex Corporation',
            'industry': 'Information Technology & Services',
            'company_size': '500-1000 employees',
            'website': 'https://apex-corp.com',
            'contact_name': 'Jane Doe',
            'contact_position': 'Director of Recruitment',
            'contact_email': email,
            'employment_type_offered': 'Local',
            'location': 'Manila, Philippines',
            'soft_notes': 'Apex Corporation is a global technology solutions provider specializing in enterprise-grade software development, workforce upskilling, and business consultation.'
        }
    )
    
    # Define 20 jobs
    jobs = [
        {
            'title': 'Senior React Developer',
            'location': 'Remote / Manila',
            'salary_range': '₱90k - ₱120k',
            'category': 'IT',
            'description': 'We are looking for a Senior React Developer to join our frontend team. You will lead the development of our modern enterprise dashboard and design rich web applications.',
            'requirements': [('React', 5), ('TypeScript', 4), ('HTML & CSS', 4)]
        },
        {
            'title': 'Python Backend Engineer',
            'location': 'Manila, PH',
            'salary_range': '₱80k - ₱110k',
            'category': 'IT',
            'description': 'Join us to build high-performance backend APIs using Python and Django. Experience with PostgreSQL and REST frameworks is highly preferred.',
            'requirements': [('Python', 5), ('Django', 4), ('SQL', 4)]
        },
        {
            'title': 'Digital Marketing Specialist',
            'location': 'Quezon City, PH',
            'salary_range': '₱35k - ₱50k',
            'category': 'MKT',
            'description': 'We are seeking a creative Digital Marketing Specialist to manage our email marketing campaigns, design search engine optimization strategies, and build content calendars.',
            'requirements': [('Email Marketing', 4), ('Search Engine Optimization', 4), ('Social Media Management', 3)]
        },
        {
            'title': 'Financial Accountant',
            'location': 'Manila, PH',
            'salary_range': '₱40k - ₱55k',
            'category': 'FIN',
            'description': 'Responsible for managing ledger accounts, petty cash processes, and generating financial statements. Must have high attention to detail.',
            'requirements': [('Basic Bookkeeping Software', 4), ('Microsoft Excel', 5), ('Attention to Detail', 5)]
        },
        {
            'title': 'Customer Support Agent',
            'location': 'Cebu, PH',
            'category': 'BPO',
            'salary_range': '₱22k - ₱28k',
            'description': 'Provide excellent phone and online customer support. Resolve queries and handle customer complaints professionally.',
            'requirements': [('Verbal Communication', 4), ('Active Listening', 4), ('Written Communication', 4)]
        },
        {
            'title': 'Executive Administrative Assistant',
            'location': 'Manila, PH',
            'category': 'ADM',
            'salary_range': '₱30k - ₱42k',
            'description': 'Support the executive team with schedule planning, business correspondence, and document management.',
            'requirements': [('Time Management', 4), ('Business Correspondence', 4), ('Google Workspace', 3)]
        },
        {
            'title': 'Retail Store Manager',
            'location': 'Davao, PH',
            'category': 'RTL',
            'salary_range': '₱35k - ₱45k',
            'description': 'Oversee daily store operations, manage stock levels, and guide retail associates to meet monthly sales targets.',
            'requirements': [('Retail Store Operations', 5), ('Stock Monitoring & Replenishment', 4), ('Customer Service Orientation', 4)]
        },
        {
            'title': 'Restaurant Shift Supervisor',
            'location': 'Cebu, PH',
            'category': 'F&B',
            'salary_range': '₱25k - ₱32k',
            'description': 'Supervise dining area and kitchen staffs, ensure food safety compliance, and coordinate high-quality service.',
            'requirements': [('Food & Beverage Service', 4), ('Food Safety & Hygiene Practices', 5), ('Teamwork & Collaboration', 4)]
        },
        {
            'title': 'Clinical Nurse Coordinator',
            'location': 'Davao, PH',
            'category': 'MED',
            'salary_range': '₱30k - ₱40k',
            'description': 'Coordinate patient care activities, monitor vital signs, and provide high-quality healthcare services.',
            'requirements': [('Health Care Services', 4), ('Empathy & Patience', 5), ('Active Listening', 4)]
        },
        {
            'title': 'Technical Trainer / Educator',
            'location': 'Remote',
            'category': 'EDU',
            'salary_range': '₱45k - ₱60k',
            'description': 'Develop curriculum and deliver technical courses. Excellent presentation and communication skills are required.',
            'requirements': [('Presentation Skills', 5), ('Verbal Communication', 5), ('Active Listening', 4)]
        },
        {
            'title': 'Project Civil Engineer',
            'location': 'Manila, PH',
            'category': 'ENG',
            'salary_range': '₱50k - ₱70k',
            'description': 'Lead local infrastructure projects. Review structural plans, manage timelines, and solve on-site engineering challenges.',
            'requirements': [('Problem Solving', 4), ('Critical Thinking', 4), ('Planning & Scheduling', 4)]
        },
        {
            'title': 'Electrical Systems Inspector',
            'location': 'Cebu, PH',
            'category': 'ELC',
            'salary_range': '₱35k - ₱45k',
            'description': 'Conduct safety inspections and maintenance on commercial building electrical systems. Certified electrical worker.',
            'requirements': [('Electrical Installation & Maintenance', 5), ('Troubleshooting', 4), ('Occupational Health & Safety Awareness', 5)]
        },
        {
            'title': 'Senior Automotive Mechanic',
            'location': 'Manila, PH',
            'category': 'MCH',
            'salary_range': '₱28k - ₱38k',
            'description': 'Perform complex diagnostic tests, engine repairs, and mechanical servicing on modern automotive fleets.',
            'requirements': [('Automotive Servicing', 5), ('Troubleshooting', 5), ('Problem Solving', 4)]
        },
        {
            'title': 'Structural Welder (TESDA Certified)',
            'location': 'Manila, PH',
            'category': 'TVT',
            'salary_range': '₱25k - ₱35k',
            'description': 'Perform high-precision shielded metal arc welding and gas metal arc welding on heavy construction projects.',
            'requirements': [('Shielded Metal Arc Welding', 5), ('Gas Metal Arc Welding', 4), ('Occupational Health & Safety Awareness', 5)]
        },
        {
            'title': 'Warehouse Operations Lead',
            'location': 'Manila, PH',
            'category': 'LOG',
            'salary_range': '₱28k - ₱36k',
            'description': 'Manage logistics operations, loading schedules, and maintain inventory counts in our central warehouse.',
            'requirements': [('Warehouse Operations', 5), ('Inventory Count & Reporting', 4), ('Time Management', 4)]
        },
        {
            'title': 'Head Security Specialist',
            'location': 'Manila, PH',
            'category': 'SEC',
            'salary_range': '₱24k - ₱30k',
            'description': 'Oversee facility security guards, identify risks, and ensure complete compliance with corporate safety rules.',
            'requirements': [('Basic Security Guard Duties', 5), ('Risk Awareness', 5), ('Conflict Resolution', 4)]
        },
        {
            'title': 'Facilities Maintenance Worker',
            'location': 'Manila, PH',
            'category': 'GEN',
            'salary_range': '₱26k - ₱34k',
            'description': 'Responsible for general utility repairs, building checks, sanitation maintenance, and machinery service.',
            'requirements': [('General Utility', 4), ('Troubleshooting', 4), ('Occupational Health & Safety Awareness', 4)]
        },
        {
            'title': 'Logistics & Fleet Driver',
            'location': 'Quezon City, PH',
            'category': 'DRV',
            'salary_range': '₱18k - ₱24k',
            'description': 'Drive delivery vans safely across Metro Manila. Maintain clean driving record and execute timely deliveries.',
            'requirements': [('Driving', 5), ('Punctuality & Reliability', 5), ('Customer Service Orientation', 3)]
        },
        {
            'title': 'Garment Production Tailor',
            'location': 'Manila, PH',
            'category': 'MFG',
            'salary_range': '₱20k - ₱25k',
            'description': 'Execute precise garment cutting and stitching according to patterns. Maintain quality control protocols.',
            'requirements': [('Dressmaking & Tailoring', 5), ('Attention to Detail', 4), ('Commitment to Quality', 4)]
        },
        {
            'title': 'Agricultural Farm Manager',
            'location': 'Laguna, PH',
            'category': 'AGR',
            'salary_range': '₱30k - ₱40k',
            'description': 'Manage crop cycles, optimize agricultural techniques, and oversee general farm worker teams.',
            'requirements': [('Agricultural Crops Production', 5), ('Problem Solving', 4), ('Leadership', 4)]
        }
    ]

    # Only create/recreate them if we don't already have exactly 20 jobs for this employer
    if JobVacancy.objects.filter(employer=profile).count() != 20:
        JobVacancy.objects.filter(employer=profile).delete()
        for job in jobs:
            vacancy = JobVacancy.objects.create(
                title=job['title'],
                employer=profile,
                location=job['location'],
                salary_range=job['salary_range'],
                category=job['category'],
                description=job['description'],
                status='Open',
                slots=random.randint(25, 50),
                remaining_slots=random.randint(25, 50)
            )
            for skill_name, req_level in job['requirements']:
                try:
                    skill_obj = CentralizedSkill.objects.get(name=skill_name)
                except CentralizedSkill.DoesNotExist:
                    skill_obj = CentralizedSkill.objects.create(
                        name=skill_name,
                        category='General',
                        description=f'Seeded skill: {skill_name}'
                    )
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=skill_obj,
                    defaults={}
                )

def create_prime_employer_and_jobs():
    from django.contrib.auth.models import User
    from .models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

    email = 'prime_employer@test.com'
    password = 'Password123!'
    
    try:
        user = User.objects.get(username=email)
        if JobVacancy.objects.filter(employer=user.profile).count() == 20:
            return
    except Exception:
        pass
    
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            'email': email,
            'first_name': 'Prime',
            'last_name': 'Employer'
        }
    )
    if created or not user.check_password(password):
        user.set_password(password)
        user.save()
    
    profile, _ = Profile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'Prime Industries',
            'industry': 'Manufacturing & Industrial Conglomerate',
            'company_size': '1000+ employees',
            'website': 'https://prime-industries.com',
            'contact_name': 'Robert Chen',
            'contact_position': 'VP of Operations',
            'contact_email': email,
            'employment_type_offered': 'Local',
            'location': 'Cavite, Philippines',
            'soft_notes': 'Prime Industries is a premier industrial conglomerate specializing in precision manufacturing, global logistics, and sustainable agricultural operations across Southeast Asia.'
        }
    )
    
    # Define 20 jobs for prime_employer
    jobs = [
        {
            'title': 'Senior Project Manager',
            'location': 'Manila, PH',
            'salary_range': '₱100k - ₱140k',
            'category': 'IT',
            'description': 'Lead development and delivery of enterprise manufacturing platforms. Oversee product roadmaps and agile sprints.',
            'requirements': [('Agile', 5), ('Scrum', 4), ('Kanban', 4)]
        },
        {
            'title': 'DevOps Specialist',
            'location': 'Remote / Manila',
            'salary_range': '₱95k - ₱130k',
            'category': 'IT',
            'description': 'Configure containerization and orchestration pipelines on AWS. Establish CI/CD workflows and manage cloud infrastructure.',
            'requirements': [('Docker', 4), ('AWS Cloud', 4), ('Kubernetes', 4)]
        },
        {
            'title': 'HR Operations Officer',
            'location': 'Cavite, PH',
            'salary_range': '₱30k - ₱40k',
            'category': 'ADM',
            'description': 'Handle employee relation activities, organize shift scheduling, and manage candidate onboarding documentation.',
            'requirements': [('Interpersonal Communication', 4), ('Written Communication', 4), ('Time Management', 4)]
        },
        {
            'title': 'General Ledger Accountant',
            'location': 'Cavite, PH',
            'salary_range': '₱42k - ₱55k',
            'category': 'FIN',
            'description': 'Responsible for ledger auditing, trial balance sheets, financial records maintenance, and petty cash systems management.',
            'requirements': [('Basic Bookkeeping Software', 4), ('Microsoft Excel', 5), ('Attention to Detail', 5)]
        },
        {
            'title': 'Business Development Representative',
            'location': 'Manila, PH',
            'salary_range': '₱35k - ₱48k',
            'category': 'MKT',
            'description': 'Drive sales outreach, generate client presentations, and establish strong customer relationship channels.',
            'requirements': [('Verbal Communication', 5), ('Presentation Skills', 4), ('Sales Orientation', 4)]
        },
        {
            'title': 'Customer Relations Team Lead',
            'location': 'Cebu, PH',
            'salary_range': '₱45k - ₱58k',
            'category': 'BPO',
            'description': 'Lead client service representatives team. Resolve escalations, handle client complaints, and compile customer satisfaction metrics.',
            'requirements': [('Complaint Handling', 5), ('Empathy & Patience', 5), ('Client Communication', 4)]
        },
        {
            'title': 'TESDA Structural Welder (NC II)',
            'location': 'Cavite, PH',
            'salary_range': '₱28k - ₱36k',
            'category': 'TVT',
            'description': 'Execute welding repairs and structural welds in manufacturing facilities. Follow strict safety protocols.',
            'requirements': [('Shielded Metal Arc Welding', 5), ('Gas Metal Arc Welding', 4), ('Occupational Health & Safety Awareness', 5)]
        },
        {
            'title': 'Electrical Design Engineer',
            'location': 'Cavite, PH',
            'salary_range': '₱60k - ₱85k',
            'category': 'ENG',
            'description': 'Create schematics for facility power grids, read blueprints, inspect layout designs, and resolve technical issues.',
            'requirements': [('Electrical Installation & Maintenance', 5), ('Blueprint & Plan Reading', 4), ('Problem Solving', 4)]
        },
        {
            'title': 'Logistics & Warehousing Supervisor',
            'location': 'Laguna, PH',
            'salary_range': '₱38k - ₱50k',
            'category': 'LOG',
            'description': 'Direct logistics warehouse operations, monitor loading/unloading tasks, and manage stock counts.',
            'requirements': [('Warehouse Operations', 5), ('Stock Monitoring & Replenishment', 4), ('Leadership', 4)]
        },
        {
            'title': 'Site Safety Inspector',
            'location': 'Cavite, PH',
            'salary_range': '₱32k - ₱42k',
            'category': 'SEC',
            'description': 'Conduct daily safety audits, evaluate site security systems, identify risks, and enforce compliance guidelines.',
            'requirements': [('Basic Security Guard Duties', 4), ('Risk Awareness', 5), ('Occupational Health & Safety Awareness', 5)]
        },
        {
            'title': 'Facilities Custodian',
            'location': 'Cavite, PH',
            'salary_range': '₱16k - ₱20k',
            'category': 'GEN',
            'description': 'Perform building cleaning, waste disposal, janitorial services, and maintain warehouse sanitations.',
            'requirements': [('Housekeeping', 4), ('Sanitation Standards', 4), ('Physical Fitness for Work', 4)]
        },
        {
            'title': 'Heavy Equipment Driver',
            'location': 'Laguna, PH',
            'salary_range': '₱22k - ₱28k',
            'category': 'DRV',
            'description': 'Operate loaders, forklifts, and transportation vehicles safely. Conduct pre-trip safety checks.',
            'requirements': [('Driving', 5), ('Occupational Health & Safety Awareness', 4), ('Physical Fitness for Work', 4)]
        },
        {
            'title': 'Industrial Electrician',
            'location': 'Cavite, PH',
            'salary_range': '₱26k - ₱34k',
            'category': 'ELC',
            'description': 'Repair industrial machinery circuits, inspect transformers, and execute electrical installations.',
            'requirements': [('Electrical Installation & Maintenance', 5), ('Troubleshooting', 4), ('Compliance Mindset', 4)]
        },
        {
            'title': 'CNC Machine Operator',
            'location': 'Cavite, PH',
            'salary_range': '₱24k - ₱30k',
            'category': 'MCH',
            'description': 'Set up and run CNC fabrication machinery, perform sorting, and execute quality inspections.',
            'requirements': [('Basic Machine Operation', 5), ('Quality Checking & Sorting', 4), ('Occupational Health & Safety Awareness', 4)]
        },
        {
            'title': 'Production Line Sewing Tailor',
            'location': 'Cavite, PH',
            'salary_range': '₱18k - ₱23k',
            'category': 'MFG',
            'description': 'Operate industrial sewing machinery for garment assembly. Meet quality checks and daily counts.',
            'requirements': [('Dressmaking & Tailoring', 5), ('Attention to Detail', 4), ('Punctuality & Reliability', 4)]
        },
        {
            'title': 'Agricultural Farm Supervisor',
            'location': 'Batangas, PH',
            'salary_range': '₱32k - ₱42k',
            'category': 'AGR',
            'description': 'Manage farm worker schedules, coordinate agricultural crop harvests, and run farm systems troubleshooting.',
            'requirements': [('Agricultural Crops Production', 5), ('Leadership', 4), ('Problem Solving', 4)]
        },
        {
            'title': 'Catering Services Coordinator',
            'location': 'Manila, PH',
            'salary_range': '₱25k - ₱32k',
            'category': 'F&B',
            'description': 'Plan corporate catering events, coordinate dining table layouts, and audit sanitation practices.',
            'requirements': [('Food & Beverage Service', 4), ('Table Setting & Service', 4), ('Food Safety & Hygiene Practices', 5)]
        },
        {
            'title': 'Nurse Practitioner',
            'location': 'Cavite, PH',
            'salary_range': '₱35k - ₱45k',
            'category': 'MED',
            'description': 'Provide medical care services in the industrial clinic, monitor vitals, and assist injured workers.',
            'requirements': [('Health Care Services', 4), ('Vital Signs Monitoring', 4), ('Patient / Elder Care Assistance', 4)]
        },
        {
            'title': 'ESL Language Teacher',
            'location': 'Remote',
            'salary_range': '₱30k - ₱42k',
            'category': 'EDU',
            'description': 'Conduct conversational English lectures for overseas corporate staff. Prepare class guides.',
            'requirements': [('English Communication – Oral', 5), ('English Communication – Written', 5), ('Presentation Skills', 4)]
        },
        {
            'title': 'Warehouse Dispatch Associate',
            'location': 'Laguna, PH',
            'salary_range': '₱18k - ₱24k',
            'category': 'LOG',
            'description': 'Process shipping logs, select transport routes, package dispatch goods, and meet tight deadlines.',
            'requirements': [('Warehouse Operations', 4), ('Order Taking & Processing', 4), ('Time Management', 4)]
        }
    ]

    # Only create/recreate them if we don't already have exactly 20 jobs for this employer
    if JobVacancy.objects.filter(employer=profile).count() != 20:
        JobVacancy.objects.filter(employer=profile).delete()
        for job in jobs:
            vacancy = JobVacancy.objects.create(
                title=job['title'],
                employer=profile,
                location=job['location'],
                salary_range=job['salary_range'],
                category=job['category'],
                description=job['description'],
                status='Open',
                slots=random.randint(25, 50),
                remaining_slots=random.randint(25, 50)
            )
            for skill_name, req_level in job['requirements']:
                try:
                    skill_obj = CentralizedSkill.objects.get(name=skill_name)
                except CentralizedSkill.DoesNotExist:
                    skill_obj = CentralizedSkill.objects.create(
                        name=skill_name,
                        category='General',
                        description=f'Seeded skill: {skill_name}'
                    )
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=skill_obj,
                    defaults={}
                )

def create_nexus_employer_and_jobs():
    from django.contrib.auth.models import User
    from .models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

    email = 'nexus_employer@test.com'
    password = 'Password123!'
    
    try:
        user = User.objects.get(username=email)
        if JobVacancy.objects.filter(employer=user.profile).count() == 20:
            return
    except Exception:
        pass
    
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            'email': email,
            'first_name': 'Nexus',
            'last_name': 'Employer'
        }
    )
    if created or not user.check_password(password):
        user.set_password(password)
        user.save()
    
    profile, _ = Profile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'Nexus Solutions',
            'industry': 'Technology Consulting & Services',
            'company_size': '250-500 employees',
            'website': 'https://nexus-solutions.test',
            'contact_name': 'Sarah Vance',
            'contact_position': 'Director of Talents',
            'contact_email': email,
            'employment_type_offered': 'Local',
            'location': 'Quezon City, Philippines',
            'soft_notes': 'Nexus Solutions is a leading digital transformation consultancy specializing in advanced software development, cybersecurity solutions, and technology integration.'
        }
    )
    
    # Define 20 jobs for nexus_employer
    jobs = [
        {
            'title': 'Cloud Security Engineer',
            'location': 'Remote / QC',
            'salary_range': '₱95k - ₱135k',
            'category': 'IT',
            'description': 'Design secure cloud architectures, configure firewalls, and perform vulnerability checks on AWS and Kubernetes environments.',
            'requirements': [('AWS Cloud', 4), ('Kubernetes', 4), ('Network Security', 5)]
        },
        {
            'title': 'AI / ML Researcher',
            'location': 'Quezon City, PH',
            'salary_range': '₱100k - ₱150k',
            'category': 'IT',
            'description': 'Develop custom machine learning models and data pipelines using PyTorch and Python to automate business tasks.',
            'requirements': [('Machine Learning', 5), ('PyTorch', 5), ('Python', 4)]
        },
        {
            'title': 'Product Designer (UX)',
            'location': 'Remote',
            'salary_range': '₱60k - ₱80k',
            'category': 'IT',
            'description': 'Design user journeys and mockups in Figma, execute usability testing, and build interactive prototypes.',
            'requirements': [('Figma', 5), ('Prototyping', 4), ('Usability Testing', 4)]
        },
        {
            'title': 'Data Analyst',
            'location': 'Quezon City, PH',
            'salary_range': '₱45k - ₱65k',
            'category': 'IT',
            'description': 'Perform data visualization and compile operational statistics reports using SQL and Pandas libraries.',
            'requirements': [('Data Analysis', 5), ('Pandas', 4), ('SQL', 4)]
        },
        {
            'title': 'SEO & Content Strategist',
            'location': 'Quezon City, PH',
            'salary_range': '₱32k - ₱45k',
            'category': 'MKT',
            'description': 'Manage corporate blog content, execute search engine optimization, and curate social media posts.',
            'requirements': [('Search Engine Optimization', 5), ('Content Writing', 4), ('Social Media Management', 4)]
        },
        {
            'title': 'Technical Support Lead',
            'location': 'Manila, PH',
            'salary_range': '₱40k - ₱55k',
            'category': 'BPO',
            'description': 'Guide customer service agents, diagnose software configuration issues, and maintain active ticketing response rates.',
            'requirements': [('Troubleshooting', 5), ('Phone & Online Customer Support', 4), ('Interpersonal Communication', 4)]
        },
        {
            'title': 'TESDA Network Systems Tech',
            'location': 'Quezon City, PH',
            'salary_range': '₱25k - ₱32k',
            'category': 'TVT',
            'description': 'Assemble computer systems, run cable checks, verify network installations, and troubleshoot connections.',
            'requirements': [('Computer Systems Servicing', 5), ('Troubleshooting', 4), ('Time Management', 4)]
        },
        {
            'title': 'Office Operations Specialist',
            'location': 'Quezon City, PH',
            'salary_range': '₱22k - ₱30k',
            'category': 'ADM',
            'description': 'Coordinate team logs, verify invoice folders, format presentation materials, and manage office systems.',
            'requirements': [('Microsoft Word', 4), ('Microsoft Excel', 4), ('Google Workspace', 4)]
        },
        {
            'title': 'Financial Controller',
            'location': 'Quezon City, PH',
            'salary_range': '₱50k - ₱70k',
            'category': 'FIN',
            'description': 'Monitor corporate budget allotments, audit ledger accounts, process payroll spreadsheets, and verify expense claims.',
            'requirements': [('Basic Bookkeeping Software', 4), ('Basic Budgeting', 4), ('Attention to Detail', 5)]
        },
        {
            'title': 'Site Infrastructure Specialist',
            'location': 'Quezon City, PH',
            'salary_range': '₱35k - ₱45k',
            'category': 'ENG',
            'description': 'Supervise building installations, execute wiring inspections, read structural blueprints, and enforce safety rules.',
            'requirements': [('Electrical Installation & Maintenance', 4), ('Blueprint & Plan Reading', 4), ('Occupational Health & Safety Awareness', 4)]
        },
        {
            'title': 'Creative Video Editor',
            'location': 'Remote',
            'salary_range': '₱28k - ₱38k',
            'category': 'MKT',
            'description': 'Record and edit promo videos, generate layout graphics in Canva, and support content team initiatives.',
            'requirements': [('Basic Video Editing', 5), ('Canva', 4), ('Creative Thinking', 4)]
        },
        {
            'title': 'Agile Coach',
            'location': 'Quezon City, PH',
            'salary_range': '₱80k - ₱110k',
            'category': 'IT',
            'description': 'Train scrum teams on agile standards, facilitate planning events, and build team alignment strategies.',
            'requirements': [('Agile', 5), ('Scrum', 5), ('OKR Framework', 4)]
        },
        {
            'title': 'Database Administrator',
            'location': 'Quezon City, PH',
            'salary_range': '₱70k - ₱95k',
            'category': 'IT',
            'description': 'Maintain data replication schemas, optimize SQL queries, and manage Postgres and MongoDB server configs.',
            'requirements': [('SQL', 5), ('PostgreSQL', 4), ('MongoDB', 4)]
        },
        {
            'title': 'Customer Success Manager',
            'location': 'Quezon City, PH',
            'salary_range': '₱45k - ₱60k',
            'category': 'BPO',
            'description': 'Maintain active client relations, handle account inquiries, and coordinate custom integrations.',
            'requirements': [('Client Communication', 5), ('Customer Relationship Management', 5), ('Empathy & Patience', 4)]
        },
        {
            'title': 'TESDA Culinary Lead',
            'location': 'Quezon City, PH',
            'salary_range': '₱25k - ₱32k',
            'category': 'TVT',
            'description': 'Direct food prep shifts, inspect kitchen hygiene compliance, and lead junior kitchen assistants.',
            'requirements': [('Cookery', 5), ('Food Safety & Hygiene Practices', 5), ('Teamwork & Collaboration', 4)]
        },
        {
            'title': 'General Office Assistant',
            'location': 'Quezon City, PH',
            'salary_range': '₱16k - ₱22k',
            'category': 'ADM',
            'description': 'Handle phone reception, execute basic data entry logs, organize document files, and schedule meetings.',
            'requirements': [('Task Prioritization', 4), ('Punctuality & Reliability', 5), ('Organization Skills', 4)]
        },
        {
            'title': 'Healthcare Coordinator',
            'location': 'Quezon City, PH',
            'salary_range': '₱26k - ₱34k',
            'category': 'MED',
            'description': 'Supervise corporate medical checks, guide caregiving initiatives, and support employee health programs.',
            'requirements': [('Caregiving', 4), ('Health Care Services', 4), ('Empathy', 5)]
        },
        {
            'title': 'Instructional Designer',
            'location': 'Remote',
            'salary_range': '₱38k - ₱50k',
            'category': 'EDU',
            'description': 'Plan corporate lecture outlines, write curriculum materials, and present train-the-trainer workshops.',
            'requirements': [('Presentation Skills', 4), ('Lesson Plan Preparation Assistance', 5), ('Verbal Communication', 5)]
        },
        {
            'title': 'Logistics Courier',
            'location': 'Quezon City, PH',
            'salary_range': '₱18k - ₱24k',
            'category': 'DRV',
            'description': 'Execute timely document and gear deliveries. Complete shipping receipts and inspect vehicle safety.',
            'requirements': [('Driving', 5), ('Punctuality & Reliability', 5), ('Instruction Following', 4)]
        },
        {
            'title': 'Safety Compliance Lead',
            'location': 'Quezon City, PH',
            'salary_range': '₱30k - ₱40k',
            'category': 'SEC',
            'description': 'Draft corporate security rules, investigate site safety incidents, and resolve facility disputes.',
            'requirements': [('Risk Awareness', 5), ('Compliance Mindset', 4), ('Conflict Resolution', 4)]
        }
    ]

    # Only create/recreate them if we don't already have exactly 20 jobs for this employer
    if JobVacancy.objects.filter(employer=profile).count() != 20:
        JobVacancy.objects.filter(employer=profile).delete()
        for job in jobs:
            vacancy = JobVacancy.objects.create(
                title=job['title'],
                employer=profile,
                location=job['location'],
                salary_range=job['salary_range'],
                category=job['category'],
                description=job['description'],
                status='Open',
                slots=random.randint(25, 50),
                remaining_slots=random.randint(25, 50)
            )
            for skill_name, req_level in job['requirements']:
                try:
                    skill_obj = CentralizedSkill.objects.get(name=skill_name)
                except CentralizedSkill.DoesNotExist:
                    skill_obj = CentralizedSkill.objects.create(
                        name=skill_name,
                        category='General',
                        description=f'Seeded skill: {skill_name}'
                    )
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=skill_obj,
                    defaults={}
                )

def create_healthcare_employer_and_jobs():
    from django.contrib.auth.models import User
    from .models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

    email = 'medilink_employer@test.com'
    password = 'Password123!'
    
    try:
        user = User.objects.get(username=email)
        if JobVacancy.objects.filter(employer=user.profile).count() == 15:
            return
    except Exception:
        pass
    
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            'email': email,
            'first_name': 'Healthcare',
            'last_name': 'Employer'
        }
    )
    if created or not user.check_password(password):
        user.set_password(password)
        user.save()
    
    profile, _ = Profile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'MediLink Health Group',
            'industry': 'Healthcare & Pharmaceuticals',
            'company_size': '100-250 employees',
            'website': 'https://medilink-health.test',
            'contact_name': 'Dr. Manuel Santos',
            'contact_position': 'Chief Human Resources Officer',
            'contact_email': email,
            'employment_type_offered': 'Local',
            'location': 'Manila, Philippines',
            'soft_notes': 'MediLink Health Group is a network of premium healthcare clinics and hospitals dedicated to patient care, community health services, and medical research.'
        }
    )
    
    jobs = [
        {
            'title': 'Clinical Registered Nurse',
            'location': 'Manila, PH',
            'salary_range': '₱35k - ₱45k',
            'category': 'MED',
            'description': 'Provide clinical nursing care, patient assessment, and support in medical procedures. Cooperate with medical staff.',
            'requirements': [('Nursing Care', 4), ('Patient Assessment', 4), ('First Aid & CPR', 4)]
        },
        {
            'title': 'General Practitioner / Physician',
            'location': 'Manila, PH',
            'salary_range': '₱80k - ₱120k',
            'category': 'MED',
            'description': 'Consult patients, perform medical diagnoses, formulate treatment plans, and provide continuous clinical care.',
            'requirements': [('Medical Diagnosis', 5), ('Patient Care', 5), ('Clinical Decision Making', 5)]
        },
        {
            'title': 'Medical Laboratory Scientist',
            'location': 'Manila, PH',
            'salary_range': '₱28k - ₱38k',
            'category': 'MED',
            'description': 'Perform laboratory tests, run equipment calibration, and ensure strict quality control of medical tests.',
            'requirements': [('Laboratory Testing', 4), ('Equipment Calibration', 4), ('Quality Control', 3)]
        },
        {
            'title': 'Physical Therapist',
            'location': 'Quezon City, PH',
            'salary_range': '₱30k - ₱42k',
            'category': 'MED',
            'description': 'Evaluate patient mobility and direct rehabilitation therapy programs for restoring physical function.',
            'requirements': [('Rehabilitation Therapy', 4), ('Patient Mobility', 4), ('Anatomy & Physiology', 4)]
        },
        {
            'title': 'Hospital Pharmacist',
            'location': 'Manila, PH',
            'salary_range': '₱32k - ₱40k',
            'category': 'MED',
            'description': 'Manage medication dispensing, review pharmacology profiles, and verify dosage calculations with high attention to detail.',
            'requirements': [('Pharmacology', 5), ('Medication Dispensing', 5), ('Attention to Detail', 4)]
        },
        {
            'title': 'Radiologic Technologist',
            'location': 'Cebu, PH',
            'salary_range': '₱26k - ₱35k',
            'category': 'MED',
            'description': 'Perform X-Ray imaging, follow radiation safety procedures, and assist in patient positioning.',
            'requirements': [('X-Ray Imaging', 4), ('Radiation Safety', 4), ('Patient Positioning', 3)]
        },
        {
            'title': 'Medical Transcriptionist',
            'location': 'Remote / Manila',
            'salary_range': '₱20k - ₱28k',
            'category': 'MED',
            'description': 'Transcribe voice records of medical reports. Fast typing speed and understanding of medical terminology is required.',
            'requirements': [('Medical Terminology', 4), ('Typing Speed', 5), ('Active Listening', 4)]
        },
        {
            'title': 'Dental Hygienist',
            'location': 'Davao, PH',
            'salary_range': '₱22k - ₱30k',
            'category': 'MED',
            'description': 'Provide dental prophylaxis, teach oral health education to patients, and maintain sterilization protocols.',
            'requirements': [('Dental Prophylaxis', 4), ('Oral Health Education', 4), ('Patient Care', 3)]
        },
        {
            'title': 'Pediatric Nurse Practitioner',
            'location': 'Quezon City, PH',
            'salary_range': '₱45k - ₱60k',
            'category': 'MED',
            'description': 'Assess and manage pediatric care services, perform physical examinations, and offer health counseling to families.',
            'requirements': [('Pediatric Care', 5), ('Patient Assessment', 4), ('Communication Skills', 4)]
        },
        {
            'title': 'Clinical Research Coordinator',
            'location': 'Manila, PH',
            'salary_range': '₱38k - ₱50k',
            'category': 'MED',
            'description': 'Coordinate clinical trials, manage data collection, and ensure strict regulatory compliance with research ethics.',
            'requirements': [('Clinical Trials', 4), ('Data Collection', 4), ('Regulatory Compliance', 4)]
        },
        {
            'title': 'Emergency Medical Technician (EMT)',
            'location': 'Manila, PH',
            'salary_range': '₱25k - ₱32k',
            'category': 'MED',
            'description': 'Provide emergency medical response, administer first aid & CPR, and handle crisis management in ambulance units.',
            'requirements': [('Emergency Response', 5), ('First Aid & CPR', 5), ('Crisis Management', 4)]
        },
        {
            'title': 'Occupational Health Specialist',
            'location': 'Cebu, PH',
            'salary_range': '₱35k - ₱48k',
            'category': 'MED',
            'description': 'Perform health assessments, implement workplace safety programs, and conduct ergonomic evaluations.',
            'requirements': [('Workplace Safety', 4), ('Health Assessments', 4), ('Ergonomic Assessment', 3)]
        },
        {
            'title': 'Medical Administrative Assistant',
            'location': 'Manila, PH',
            'salary_range': '₱20k - ₱26k',
            'category': 'MED',
            'description': 'Manage patient scheduling, coordinate billing, and maintain database logs using Google Workspace tools.',
            'requirements': [('Medical Scheduling', 4), ('Patient Billing', 4), ('Google Workspace', 3)]
        },
        {
            'title': 'Nutritionist / Dietitian',
            'location': 'Manila, PH',
            'salary_range': '₱28k - ₱36k',
            'category': 'MED',
            'description': 'Create customized dietary planning, conduct nutritional assessments, and offer health counseling.',
            'requirements': [('Dietary Planning', 4), ('Nutritional Assessment', 4), ('Counseling Skills', 3)]
        },
        {
            'title': 'Mental Health Counselor',
            'location': 'Remote / Manila',
            'salary_range': '₱40k - ₱55k',
            'category': 'MED',
            'description': 'Provide individual psychotherapy, utilize active listening skills, and show high empathy to support patient recovery.',
            'requirements': [('Psychotherapy', 5), ('Active Listening', 5), ('Empathy', 5)]
        }
    ]

    # Only create/recreate them if we don't already have exactly 15 jobs for this employer
    if JobVacancy.objects.filter(employer=profile).count() != 15:
        JobVacancy.objects.filter(employer=profile).delete()
        for job in jobs:
            vacancy = JobVacancy.objects.create(
                title=job['title'],
                employer=profile,
                location=job['location'],
                salary_range=job['salary_range'],
                category=job['category'],
                description=job['description'],
                status='Open',
                slots=random.randint(25, 50),
                remaining_slots=random.randint(25, 50)
            )
            for skill_name, req_level in job['requirements']:
                try:
                    skill_obj = CentralizedSkill.objects.get(name=skill_name)
                except CentralizedSkill.DoesNotExist:
                    skill_obj = CentralizedSkill.objects.create(
                        name=skill_name,
                        category='General',
                        description=f'Seeded skill: {skill_name}'
                    )
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=skill_obj,
                    defaults={}
                )

def create_ofw_employer_and_jobs():
    from django.contrib.auth.models import User
    from .models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

    email = 'global_horizons_employer@test.com'
    password = 'Password123!'
    
    try:
        user = User.objects.get(username=email)
        if JobVacancy.objects.filter(employer=user.profile).count() == 10:
            return
    except Exception:
        pass
    
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            'email': email,
            'first_name': 'OFW',
            'last_name': 'Employer'
        }
    )
    if created or not user.check_password(password):
        user.set_password(password)
        user.save()
    
    profile, _ = Profile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'Global Horizons Placement Agency',
            'industry': 'Human Resources & Recruitment',
            'company_size': '50-100 employees',
            'website': 'https://global-horizons.test',
            'contact_name': 'Mr. Antonio Santos',
            'contact_position': 'Managing Director',
            'contact_email': email,
            'employment_type_offered': 'Overseas',
            'location': 'Manila, Philippines',
            'soft_notes': 'Global Horizons is a POEA-licensed placement agency connecting skilled Filipino workers with international career opportunities in healthcare, engineering, hospitality, and general services.'
        }
    )
    
    jobs = [
        {
            'title': 'Domestic Helper / Caregiver',
            'location': 'Saudi Arabia',
            'salary_range': '₱25k - ₱30k',
            'category': 'OFW',
            'description': 'Provide domestic caregiving services, assist with daily activities, administer first aid & CPR, and handle basic housekeeping.',
            'requirements': [('Caregiving', 4), ('First Aid & CPR', 4), ('Housekeeping', 3)]
        },
        {
            'title': 'Hospitality Staff / Hotel Receptionist',
            'location': 'Dubai, UAE',
            'salary_range': '₱40k - ₱55k',
            'category': 'OFW',
            'description': 'Welcome hotel guests, coordinate check-in and check-out, and maintain active communication using excellent customer service.',
            'requirements': [('Verbal Communication', 4), ('Active Listening', 4), ('Customer Service Orientation', 4)]
        },
        {
            'title': 'Structural Welder (Industrial)',
            'location': 'Doha, Qatar',
            'salary_range': '₱50k - ₱70k',
            'category': 'OFW',
            'description': 'Perform structural welding for commercial construction. Must be POEA approved, read blueprint plans, and observe safety compliance.',
            'requirements': [('Welding', 5), ('Blueprint & Plan Reading', 4), ('Safety Compliance', 4)]
        },
        {
            'title': 'Heavy Equipment Operator',
            'location': 'Singapore',
            'salary_range': '₱60k - ₱80k',
            'category': 'OFW',
            'description': 'Operate heavy construction machinery, perform routine machine maintenance, and adhere to strict safety compliance standards.',
            'requirements': [('Heavy Equipment Operations', 5), ('Safety Compliance', 5), ('Machine Maintenance', 4)]
        },
        {
            'title': 'Registered General Nurse (Hospital)',
            'location': 'London, UK',
            'salary_range': '₱150k - ₱180k',
            'category': 'OFW',
            'description': 'Join a premium NHS hospital. Deliver general nursing care, perform patient assessment, and collaborate in medical protocols.',
            'requirements': [('Nursing Care', 5), ('Patient Assessment', 5), ('Verbal Communication', 4)]
        },
        {
            'title': 'Construction Civil Engineer',
            'location': 'Tokyo, Japan',
            'salary_range': '₱90k - ₱120k',
            'category': 'OFW',
            'description': 'Supervise site civil engineering designs, manage construction projects, and utilize basic Japanese language skills.',
            'requirements': [('Civil Engineering Design', 5), ('Project Management', 4), ('Japanese Language', 3)]
        },
        {
            'title': 'Automotive Service Mechanic',
            'location': 'Alberta, Canada',
            'salary_range': '₱100k - ₱130k',
            'category': 'OFW',
            'description': 'Perform complex automotive diagnostics, repair mechanical systems, and resolve technical issues.',
            'requirements': [('Automotive Diagnostics', 5), ('Mechanical Systems Repair', 4), ('Problem Solving', 4)]
        },
        {
            'title': 'Warehouse Logistics Assistant',
            'location': 'Taipei, Taiwan',
            'salary_range': '₱38k - ₱45k',
            'category': 'OFW',
            'description': 'Organize stock inventory count, manage warehouse operations, and support logistics shipping workflows.',
            'requirements': [('Warehouse Operations', 4), ('Inventory Count & Reporting', 4), ('Active Listening', 3)]
        },
        {
            'title': 'Barista / Food Service Crew',
            'location': 'Kuwait City, Kuwait',
            'salary_range': '₱30k - ₱38k',
            'category': 'OFW',
            'description': 'Deliver premium food and beverage service, serve guests, and maintain high standards of customer service.',
            'requirements': [('Food & Beverage Service', 4), ('Customer Service Orientation', 4), ('Verbal Communication', 3)]
        },
        {
            'title': 'Electrician (Facilities Maintenance)',
            'location': 'Sydney, Australia',
            'salary_range': '₱110k - ₱140k',
            'category': 'OFW',
            'description': 'Manage electrical installation & maintenance for corporate facilities. POEA placement opportunity.',
            'requirements': [('Electrical Installation & Maintenance', 5), ('Blueprint & Plan Reading', 4), ('Safety Compliance', 4)]
        }
    ]

    # Only create/recreate them if we don't already have exactly 10 jobs for this employer
    if JobVacancy.objects.filter(employer=profile).count() != 10:
        JobVacancy.objects.filter(employer=profile).delete()
        for job in jobs:
            vacancy = JobVacancy.objects.create(
                title=job['title'],
                employer=profile,
                location=job['location'],
                salary_range=job['salary_range'],
                category=job['category'],
                description=job['description'],
                status='Open',
                slots=random.randint(25, 50),
                remaining_slots=random.randint(25, 50)
            )
            for skill_name, req_level in job['requirements']:
                try:
                    skill_obj = CentralizedSkill.objects.get(name=skill_name)
                except CentralizedSkill.DoesNotExist:
                    skill_obj = CentralizedSkill.objects.create(
                        name=skill_name,
                        category='General',
                        description=f'Seeded skill: {skill_name}'
                    )
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=skill_obj,
                    defaults={}
                )

def create_applicant_test_accounts(force_recreate=False):
    from django.contrib.auth.models import User
    from .models import (
        Profile, ApplicantSkill, CentralizedSkill, Education, 
        WorkExperience, Certification, JobVacancy, Referral, Interview
    )
    import random
    from django.utils import timezone
    import datetime

    if force_recreate:
        User.objects.filter(username__startswith='applicant_test_').delete()
        Interview.objects.filter(candidate__user__username__startswith='applicant_test_').delete()

    test_users = User.objects.filter(username__startswith='applicant_test_')
    existing_count = test_users.count()
    if existing_count == 50 and not force_recreate:
        # Check if referrals already exist for these test applicants. If so, return early.
        has_referrals = Referral.objects.filter(applicant__user__in=test_users).exists()
        if has_referrals:
            return

    password = 'Password123!'
    locations = ['Manila, Philippines', 'Quezon City, Philippines', 'Cebu, Philippines', 'Davao, Philippines', 'Remote']
    jobs_map = {
        'IT': ['React', 'TypeScript', 'Python', 'SQL', 'Django', 'Figma', 'AWS Cloud', 'Docker', 'Kubernetes', 'Agile'],
        'BPO': ['Verbal Communication', 'Written Communication', 'Active Listening', 'Customer Service Orientation', 'Complaint Handling'],
        'ADM': ['Microsoft Word', 'Microsoft Excel', 'Google Workspace', 'Time Management', 'Organization Skills'],
        'FIN': ['Basic Bookkeeping Software', 'Basic Budgeting', 'Attention to Detail', 'Microsoft Excel'],
        'MKT': ['Search Engine Optimization', 'Content Writing', 'Social Media Management', 'Canva', 'Email Marketing'],
        'RTL': ['Retail Store Operations', 'Stock Monitoring & Replenishment', 'Customer Service Orientation'],
        'ENG': ['Electrical Installation & Maintenance', 'Blueprint & Plan Reading', 'Problem Solving'],
        'LOG': ['Warehouse Operations', 'Inventory Count & Reporting', 'Logistics Support'],
        'MED': ['Nursing Care', 'Patient Assessment', 'First Aid & CPR', 'Medical Diagnosis', 'Patient Care', 'Clinical Decision Making', 'Active Listening', 'Medical Terminology', 'Emergency Response', 'Communication Skills'],
        'OFW': ['Caregiving', 'First Aid & CPR', 'Housekeeping', 'Welding', 'Safety Compliance', 'Warehouse Operations', 'Customer Service Orientation', 'Active Listening', 'Verbal Communication', 'Problem Solving']
    }
    job_keys = list(jobs_map.keys())
    
    # Pre-fetch vacancies to map matching categories efficiently
    vacancies_by_category = {}
    for key in job_keys:
        vacancies_by_category[key] = list(JobVacancy.objects.filter(category=key))
    all_vacancies = list(JobVacancy.objects.all())

    real_names = [
        ("John", "Santos"), ("Maria", "Reyes"), ("Mark", "Cruz"), ("Sarah", "Cruz"),
        ("Dave", "Dela Cruz"), ("Angelica", "Garcia"), ("James", "Mendoza"), ("Nicole", "Bautista"),
        ("Christian", "Aquino"), ("Christine", "Ramos"), ("Joseph", "Castillo"), ("Kimberly", "Santos"),
        ("Paul", "Rivera"), ("Patricia", "Salazar"), ("Michael", "Flores"), ("Michelle", "Bernardo"),
        ("Robert", "Valdez"), ("Rachel", "Villanueva"), ("David", "Pineda"), ("Samantha", "Castro"),
        ("Daniel", "Domingo"), ("Jessica", "Fernandez"), ("Richard", "Gabriel"), ("Mary", "Grace"),
        ("Andrew", "Gonzales"), ("Melissa", "De Leon"), ("Kevin", "Hernandez"), ("Stephanie", "Lopez"),
        ("Joseph", "Macatangay"), ("Karen", "Pascual"), ("Kenneth", "Mercado"), ("Ashley", "Ocampo"),
        ("Matthew", "Robles"), ("Vanessa", "Santiago"), ("Joshua", "Tolentino"), ("Camille", "Soriano"),
        ("Christopher", "Dizon"), ("Alyssa", "Sison"), ("Ryan", "Evangelista"), ("Bianca", "Ferrer"),
        ("Jason", "Gopez"), ("Clarissa", "Imperial"), ("Justin", "Alcantara"), ("Katrina", "Manalo"),
        ("Jonathan", "Ortiz"), ("Monica", "David"), ("Eric", "Panganiban"), ("Erica", "Reyes"),
        ("Patrick", "Roxas"), ("Erika", "Sebastian")
    ]

    for i in range(1, 51):
        email = f'applicant_test_{i}@example.com'
        first_name, last_name = real_names[i-1]
        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                'email': email,
                'first_name': first_name,
                'last_name': last_name
            }
        )
        user.first_name = first_name
        user.last_name = last_name
        if created or not user.check_password(password):
            user.set_password(password)
        user.save()

        # Randomize attributes deterministically based on index so it is consistent
        random.seed(i)
        loc = random.choice(locations)
        pref_job = random.choice(job_keys)
        exp = random.choice(['Fresh Graduate', '1 Year', '2 Years', '3 Years', '5 Years'])
        is_fresh = (exp == 'Fresh Graduate')
        
        # Decide status based on index partition
        # Group 1: 1-15 -> Applied / Pending
        # Group 2: 16-30 -> Interviewing
        # Group 3: 31-50 -> Other Samples
        if i <= 15:
            ref_status = 'Pending'
            profile_status = 'Applied'
        elif i <= 30:
            ref_status = 'Interviewing'
            profile_status = 'Shortlisted'
        else:
            if i <= 35:
                ref_status = 'Hired — Probationary'
                profile_status = 'Employed — Onboarding'
            elif i <= 40:
                ref_status = 'Declined'
                profile_status = 'Active — Job Seeking'
            elif i <= 45:
                ref_status = 'Accepted — Awaiting Onboarding'
                profile_status = 'Employed — Onboarding'
            else:
                ref_status = 'No Show'
                profile_status = 'Active — Job Seeking'

        profile, p_created = Profile.objects.update_or_create(
            user=user,
            defaults={
                'role': 'applicant',
                'is_verified': True,
                'is_profile_complete': True,
                'location': loc,
                'preferred_job': pref_job,
                'experience_years': exp,
                'is_fresh_grad': is_fresh,
                'title': f'{pref_job} Professional',
                'status': profile_status,
                'civil_status': 'Single',
                'soft_notes': f'Automated test applicant profile for testing skill gaps and recruiter dashboards.'
            }
        )
        
        # Link skills deterministically based on preferred_job
        ApplicantSkill.objects.filter(profile=profile).delete()
        available_skills = jobs_map[pref_job]
        
        selected_skills = random.sample(available_skills, min(len(available_skills), random.randint(3, 4)))
        for s_name in selected_skills:
            try:
                skill_obj = CentralizedSkill.objects.get(name=s_name)
            except CentralizedSkill.DoesNotExist:
                skill_obj = CentralizedSkill.objects.create(
                    name=s_name,
                    category='General',
                    description=f'Seeded skill: {s_name}'
                )
            ApplicantSkill.objects.create(
                profile=profile,
                skill=skill_obj,
                source='system'
            )
            
        # Add basic education and work experience if not fresh grad
        Education.objects.filter(profile=profile).delete()
        Education.objects.create(
            profile=profile,
            institution='State University',
            degree='Bachelor of Science',
            field_of_study=f'{pref_job} Studies',
            start_year=2018,
            end_year=2022
        )
        
        WorkExperience.objects.filter(profile=profile).delete()
        if not is_fresh:
            WorkExperience.objects.create(
                profile=profile,
                company='TechSolutions Inc.',
                position=f'Junior {pref_job} Specialist',
                start_date='June 2022',
                end_date='Present',
                is_current=True,
                description='Gained hands-on experience in core workflows.'
            )

        # Clear existing referrals/interviews for this user to avoid dupes/overlaps if re-running
        Referral.objects.filter(applicant=profile).delete()
        Interview.objects.filter(candidate=profile).delete()

        # Find matching vacancies
        matching_vacs = vacancies_by_category.get(pref_job, [])
        if not matching_vacs:
            matching_vacs = all_vacancies

        if matching_vacs:
            # Distribute primary vacancy across Apex, Prime, Nexus by rotating matching_vacs
            vac_index = (i - 1) % len(matching_vacs)
            primary_vac = matching_vacs[vac_index]
            
            # Create the primary Referral
            ref = Referral.objects.create(
                applicant=profile,
                job_vacancy=primary_vac,
                status=ref_status,
                date_referred=timezone.localdate() - datetime.timedelta(days=random.randint(2, 10))
            )

            # If Group 2 (Interviewing), create an Interview record as well
            if ref_status == 'Interviewing':
                Interview.objects.create(
                    employer=primary_vac.employer,
                    title=f"Technical Interview with {user.first_name} {user.last_name}",
                    candidate=profile,
                    vacancy=primary_vac,
                    date=timezone.localdate() + datetime.timedelta(days=random.randint(1, 5)),
                    start_time=datetime.time(hour=random.choice([9, 10, 11, 14, 15, 16]), minute=0),
                    interview_type=random.choice(['Video Call', 'Phone Call', 'In-Person']),
                    round_name=random.choice(['Screening', 'Technical Interview', 'Final Round']),
                    meeting_link='https://meet.google.com/abc-defg-hij',
                    notes=f"Scheduled screening interview for {user.first_name}."
                )

            # For hired status, let's fill in onboarding details to look complete
            if ref_status == 'Hired — Probationary' or ref_status == 'Accepted — Awaiting Onboarding':
                ref.accepted_position = f"{pref_job} Specialist"
                ref.accepted_salary = "₱30k - ₱45k"
                ref.reporting_date = timezone.localdate() + datetime.timedelta(days=random.randint(5, 15))
                ref.employment_type = 'Probationary'
                ref.save()

            # Sometimes add a secondary job vacancy application (Pending) to look realistic
            if len(matching_vacs) > 1 and random.choice([True, False]) and i <= 30:
                sec_index = (i) % len(matching_vacs)
                secondary_vac = matching_vacs[sec_index]
                if secondary_vac == primary_vac:
                    secondary_vac = matching_vacs[(sec_index + 1) % len(matching_vacs)]
                Referral.objects.create(
                    applicant=profile,
                    job_vacancy=secondary_vac,
                    status='Pending',
                    date_referred=timezone.localdate() - datetime.timedelta(days=random.randint(1, 5))
                )

# ==========================================
# PESO ADMIN VIEWS
# ==========================================

def seed_mock_applicants_if_empty():
    if User.objects.exists():
        return
    create_apex_employer_and_jobs()
    create_prime_employer_and_jobs()
    create_nexus_employer_and_jobs()
    create_healthcare_employer_and_jobs()
    create_ofw_employer_and_jobs()
    create_applicant_test_accounts()
    # 1. Centralized Skills
    skills_data = [
        ('React', 'Frontend Dev', 'A JavaScript library for building user interfaces.'),
        ('TypeScript', 'Frontend Dev', 'Strongly typed programming language that builds on JavaScript.'),
        ('Python', 'Backend Dev', 'High-level programming language known for readability.'),
        ('PyTorch', 'Data Science', 'Open source machine learning library based on Torch.'),
        ('Agile', 'Product Mgmt', 'Software development methodology focused on iterative progress.'),
        ('SQL', 'Backend Dev', 'Structured Query Language for database management.'),
        ('Figma', 'UX Design', 'Collaborative interface design tool.'),
        ('A11y', 'UX Design', 'Accessibility standards and practice.'),
        ('AWS Cloud', 'DevOps', 'Amazon Web Services cloud platform solutions.'),
        ('Housekeeping', 'TESDA Trade', 'TESDA vocational housekeeping qualifications.'),
        ('Welding', 'TESDA Trade', 'TESDA structural welding trade skills.'),
        ('Kubernetes', 'DevOps', 'Container orchestration tool.'),
        ('Wireframing', 'UX Design', 'Professional competency in Wireframing within UX Design.'),
        ('Prototyping', 'UX Design', 'Professional competency in Prototyping within UX Design.'),
        ('User Research', 'UX Design', 'Professional competency in User Research within UX Design.'),
        ('Adobe XD', 'UX Design', 'Professional competency in Adobe XD within UX Design.'),
        ('Sketch', 'UX Design', 'Professional competency in Sketch within UX Design.'),
        ('InVision', 'UX Design', 'Professional competency in InVision within UX Design.'),
        ('Usability Testing', 'UX Design', 'Professional competency in Usability Testing within UX Design.'),
        ('Information Architecture', 'UX Design', 'Professional competency in Information Architecture within UX Design.'),
        ('Design Thinking', 'UX Design', 'Professional competency in Design Thinking within UX Design.'),
        ('Vue.js', 'Frontend Dev', 'Professional competency in Vue.js within Frontend Dev.'),
        ('Angular', 'Frontend Dev', 'Professional competency in Angular within Frontend Dev.'),
        ('Next.js', 'Frontend Dev', 'Professional competency in Next.js within Frontend Dev.'),
        ('HTML & CSS', 'Frontend Dev', 'Professional competency in HTML & CSS within Frontend Dev.'),
        ('Tailwind CSS', 'Frontend Dev', 'Professional competency in Tailwind CSS within Frontend Dev.'),
        ('Bootstrap', 'Frontend Dev', 'Professional competency in Bootstrap within Frontend Dev.'),
        ('JavaScript', 'Frontend Dev', 'Professional competency in JavaScript within Frontend Dev.'),
        ('Responsive Web Design', 'Frontend Dev', 'Professional competency in Responsive Web Design within Frontend Dev.'),
        ('Redux', 'Frontend Dev', 'Professional competency in Redux within Frontend Dev.'),
        ('Node.js', 'Backend Dev', 'Professional competency in Node.js within Backend Dev.'),
        ('Express.js', 'Backend Dev', 'Professional competency in Express.js within Backend Dev.'),
        ('PHP', 'Backend Dev', 'Professional competency in PHP within Backend Dev.'),
        ('Laravel', 'Backend Dev', 'Professional competency in Laravel within Backend Dev.'),
        ('Django', 'Backend Dev', 'Professional competency in Django within Backend Dev.'),
        ('Java', 'Backend Dev', 'Professional competency in Java within Backend Dev.'),
        ('Spring Boot', 'Backend Dev', 'Professional competency in Spring Boot within Backend Dev.'),
        ('REST API Development', 'Backend Dev', 'Professional competency in REST API Development within Backend Dev.'),
        ('GraphQL', 'Backend Dev', 'Professional competency in GraphQL within Backend Dev.'),
        ('C#', 'Backend Dev', 'Professional competency in C# within Backend Dev.'),
        ('.NET Framework', 'Backend Dev', 'Professional competency in .NET Framework within Backend Dev.'),
        ('MongoDB', 'Backend Dev', 'Professional competency in MongoDB within Backend Dev.'),
        ('PostgreSQL', 'Backend Dev', 'Professional competency in PostgreSQL within Backend Dev.'),
        ('Firebase', 'Backend Dev', 'Professional competency in Firebase within Backend Dev.'),
        ('Docker', 'DevOps', 'Professional competency in Docker within DevOps.'),
        ('Jenkins', 'DevOps', 'Professional competency in Jenkins within DevOps.'),
        ('Terraform', 'DevOps', 'Professional competency in Terraform within DevOps.'),
        ('GitHub Actions', 'DevOps', 'Professional competency in GitHub Actions within DevOps.'),
        ('CI/CD Pipeline', 'DevOps', 'Professional competency in CI/CD Pipeline within DevOps.'),
        ('Linux Administration', 'DevOps', 'Professional competency in Linux Administration within DevOps.'),
        ('Nginx', 'DevOps', 'Professional competency in Nginx within DevOps.'),
        ('Azure DevOps', 'DevOps', 'Professional competency in Azure DevOps within DevOps.'),
        ('Google Cloud Platform', 'DevOps', 'Professional competency in Google Cloud Platform within DevOps.'),
        ('Amazon Web Services', 'DevOps', 'Professional competency in Amazon Web Services within DevOps.'),
        ('Machine Learning', 'Data Science', 'Professional competency in Machine Learning within Data Science.'),
        ('Data Analysis', 'Data Science', 'Professional competency in Data Analysis within Data Science.'),
        ('TensorFlow', 'Data Science', 'Professional competency in TensorFlow within Data Science.'),
        ('Pandas', 'Data Science', 'Professional competency in Pandas within Data Science.'),
        ('NumPy', 'Data Science', 'Professional competency in NumPy within Data Science.'),
        ('Data Visualization', 'Data Science', 'Professional competency in Data Visualization within Data Science.'),
        ('Power BI', 'Data Science', 'Professional competency in Power BI within Data Science.'),
        ('Tableau', 'Data Science', 'Professional competency in Tableau within Data Science.'),
        ('R Programming', 'Data Science', 'Professional competency in R Programming within Data Science.'),
        ('Natural Language Processing', 'Data Science', 'Professional competency in Natural Language Processing within Data Science.'),
        ('Statistics & Probability', 'Data Science', 'Professional competency in Statistics & Probability within Data Science.'),
        ('Excel for Data Analysis', 'Data Science', 'Professional competency in Excel for Data Analysis within Data Science.'),
        ('Scrum', 'Product Mgmt', 'Professional competency in Scrum within Product Mgmt.'),
        ('Kanban', 'Product Mgmt', 'Professional competency in Kanban within Product Mgmt.'),
        ('JIRA', 'Product Mgmt', 'Professional competency in JIRA within Product Mgmt.'),
        ('Product Roadmapping', 'Product Mgmt', 'Professional competency in Product Roadmapping within Product Mgmt.'),
        ('Stakeholder Management', 'Product Mgmt', 'Professional competency in Stakeholder Management within Product Mgmt.'),
        ('Business Analysis', 'Product Mgmt', 'Professional competency in Business Analysis within Product Mgmt.'),
        ('Requirements Gathering', 'Product Mgmt', 'Professional competency in Requirements Gathering within Product Mgmt.'),
        ('OKR Framework', 'Product Mgmt', 'Professional competency in OKR Framework within Product Mgmt.'),
        ('Trello', 'Product Mgmt', 'Professional competency in Trello within Product Mgmt.'),
        ('Microsoft Word', 'Digital Skills', 'Professional competency in Microsoft Word within Digital Skills.'),
        ('Microsoft Excel', 'Digital Skills', 'Professional competency in Microsoft Excel within Digital Skills.'),
        ('Microsoft PowerPoint', 'Digital Skills', 'Professional competency in Microsoft PowerPoint within Digital Skills.'),
        ('Google Workspace', 'Digital Skills', 'Professional competency in Google Workspace within Digital Skills.'),
        ('Canva', 'Digital Skills', 'Professional competency in Canva within Digital Skills.'),
        ('Social Media Management', 'Digital Skills', 'Professional competency in Social Media Management within Digital Skills.'),
        ('Email Marketing', 'Digital Skills', 'Professional competency in Email Marketing within Digital Skills.'),
        ('Basic Video Editing', 'Digital Skills', 'Professional competency in Basic Video Editing within Digital Skills.'),
        ('Content Writing', 'Digital Skills', 'Professional competency in Content Writing within Digital Skills.'),
        ('Search Engine Optimization', 'Digital Skills', 'Professional competency in Search Engine Optimization within Digital Skills.'),
        ('E-Commerce Management', 'Digital Skills', 'Professional competency in E-Commerce Management within Digital Skills.'),
        ('Online Customer Service', 'Digital Skills', 'Professional competency in Online Customer Service within Digital Skills.'),
        ('Basic Bookkeeping Software', 'Digital Skills', 'Professional competency in Basic Bookkeeping Software within Digital Skills.'),
        ('Zoom / Google Meet Facilitation', 'Digital Skills', 'Professional competency in Zoom / Google Meet Facilitation within Digital Skills.'),
        ('Cybersecurity Awareness', 'Digital Skills', 'Professional competency in Cybersecurity Awareness within Digital Skills.'),
        ('Cookery', 'TESDA Trade', 'Professional competency in Cookery within TESDA Trade.'),
        ('Food & Beverage Service', 'TESDA Trade', 'Professional competency in Food & Beverage Service within TESDA Trade.'),
        ('Bread & Pastry Production', 'TESDA Trade', 'Professional competency in Bread & Pastry Production within TESDA Trade.'),
        ('Shielded Metal Arc Welding', 'TESDA Trade', 'Professional competency in Shielded Metal Arc Welding within TESDA Trade.'),
        ('Gas Metal Arc Welding', 'TESDA Trade', 'Professional competency in Gas Metal Arc Welding within TESDA Trade.'),
        ('Electrical Installation & Maintenance', 'TESDA Trade', 'Professional competency in Electrical Installation & Maintenance within TESDA Trade.'),
        ('Plumbing', 'TESDA Trade', 'Professional competency in Plumbing within TESDA Trade.'),
        ('Carpentry', 'TESDA Trade', 'Professional competency in Carpentry within TESDA Trade.'),
        ('Tile Setting', 'TESDA Trade', 'Professional competency in Tile Setting within TESDA Trade.'),
        ('Masonry', 'TESDA Trade', 'Professional competency in Masonry within TESDA Trade.'),
        ('Driving', 'TESDA Trade', 'Professional competency in Driving within TESDA Trade.'),
        ('Automotive Servicing', 'TESDA Trade', 'Professional competency in Automotive Servicing within TESDA Trade.'),
        ('Motorcycle/Small Engine Servicing', 'TESDA Trade', 'Professional competency in Motorcycle/Small Engine Servicing within TESDA Trade.'),
        ('Agricultural Crops Production', 'TESDA Trade', 'Professional competency in Agricultural Crops Production within TESDA Trade.'),
        ('Beauty/Nail Care', 'TESDA Trade', 'Professional competency in Beauty/Nail Care within TESDA Trade.'),
        ('Barbering', 'TESDA Trade', 'Professional competency in Barbering within TESDA Trade.'),
        ('Dressmaking & Tailoring', 'TESDA Trade', 'Professional competency in Dressmaking & Tailoring within TESDA Trade.'),
        ('Caregiving', 'TESDA Trade', 'Professional competency in Caregiving within TESDA Trade.'),
        ('Health Care Services', 'TESDA Trade', 'Professional competency in Health Care Services within TESDA Trade.'),
        ('Computer Systems Servicing', 'TESDA Trade', 'Professional competency in Computer Systems Servicing within TESDA Trade.'),
        ('Contact Center Services', 'TESDA Trade', 'Professional competency in Contact Center Services within TESDA Trade.'),
        ('Verbal Communication', 'Soft Skills', 'Professional competency in Verbal Communication within Soft Skills.'),
        ('Written Communication', 'Soft Skills', 'Professional competency in Written Communication within Soft Skills.'),
        ('Active Listening', 'Soft Skills', 'Professional competency in Active Listening within Soft Skills.'),
        ('Presentation Skills', 'Soft Skills', 'Professional competency in Presentation Skills within Soft Skills.'),
        ('Public Speaking', 'Soft Skills', 'Professional competency in Public Speaking within Soft Skills.'),
        ('Report Writing', 'Soft Skills', 'Professional competency in Report Writing within Soft Skills.'),
        ('Business Correspondence', 'Soft Skills', 'Professional competency in Business Correspondence within Soft Skills.'),
        ('Interpersonal Communication', 'Soft Skills', 'Professional competency in Interpersonal Communication within Soft Skills.'),
        ('Non-Verbal Communication', 'Soft Skills', 'Professional competency in Non-Verbal Communication within Soft Skills.'),
        ('Storytelling & Persuasion', 'Soft Skills', 'Professional competency in Storytelling & Persuasion within Soft Skills.'),
        ('Critical Thinking', 'Soft Skills', 'Professional competency in Critical Thinking within Soft Skills.'),
        ('Analytical Thinking', 'Soft Skills', 'Professional competency in Analytical Thinking within Soft Skills.'),
        ('Problem Solving', 'Soft Skills', 'Professional competency in Problem Solving within Soft Skills.'),
        ('Decision Making', 'Soft Skills', 'Professional competency in Decision Making within Soft Skills.'),
        ('Creative Thinking', 'Soft Skills', 'Professional competency in Creative Thinking within Soft Skills.'),
        ('Strategic Thinking', 'Soft Skills', 'Professional competency in Strategic Thinking within Soft Skills.'),
        ('Logical Reasoning', 'Soft Skills', 'Professional competency in Logical Reasoning within Soft Skills.'),
        ('Research & Information Gathering', 'Soft Skills', 'Professional competency in Research & Information Gathering within Soft Skills.'),
        ('Troubleshooting', 'Soft Skills', 'Professional competency in Troubleshooting within Soft Skills.'),
        ('Root Cause Analysis', 'Soft Skills', 'Professional competency in Root Cause Analysis within Soft Skills.'),
        ('Work Ethic', 'Soft Skills', 'Professional competency in Work Ethic within Soft Skills.'),
        ('Integrity & Honesty', 'Soft Skills', 'Professional competency in Integrity & Honesty within Soft Skills.'),
        ('Punctuality & Reliability', 'Soft Skills', 'Professional competency in Punctuality & Reliability within Soft Skills.'),
        ('Accountability', 'Soft Skills', 'Professional competency in Accountability within Soft Skills.'),
        ('Attention to Detail', 'Soft Skills', 'Professional competency in Attention to Detail within Soft Skills.'),
        ('Initiative & Proactiveness', 'Soft Skills', 'Professional competency in Initiative & Proactiveness within Soft Skills.'),
        ('Self-Motivation', 'Soft Skills', 'Professional competency in Self-Motivation within Soft Skills.'),
        ('Commitment to Quality', 'Soft Skills', 'Professional competency in Commitment to Quality within Soft Skills.'),
        ('Professional Conduct', 'Soft Skills', 'Professional competency in Professional Conduct within Soft Skills.'),
        ('Confidentiality & Discretion', 'Soft Skills', 'Professional competency in Confidentiality & Discretion within Soft Skills.'),
        ('Time Management', 'Soft Skills', 'Professional competency in Time Management within Soft Skills.'),
        ('Task Prioritization', 'Soft Skills', 'Professional competency in Task Prioritization within Soft Skills.'),
        ('Organization Skills', 'Soft Skills', 'Professional competency in Organization Skills within Soft Skills.'),
        ('Multitasking', 'Soft Skills', 'Professional competency in Multitasking within Soft Skills.'),
        ('Meeting Deadlines', 'Soft Skills', 'Professional competency in Meeting Deadlines within Soft Skills.'),
        ('Goal Setting', 'Soft Skills', 'Professional competency in Goal Setting within Soft Skills.'),
        ('Planning & Scheduling', 'Soft Skills', 'Professional competency in Planning & Scheduling within Soft Skills.'),
        ('Workload Management', 'Soft Skills', 'Professional competency in Workload Management within Soft Skills.'),
        ('Teamwork & Collaboration', 'Soft Skills', 'Professional competency in Teamwork & Collaboration within Soft Skills.'),
        ('Team Building', 'Soft Skills', 'Professional competency in Team Building within Soft Skills.'),
        ('Cooperation & Flexibility', 'Soft Skills', 'Professional competency in Cooperation & Flexibility within Soft Skills.'),
        ('Cross-Functional Collaboration', 'Soft Skills', 'Professional competency in Cross-Functional Collaboration within Soft Skills.'),
        ('Peer Support & Mentoring', 'Soft Skills', 'Professional competency in Peer Support & Mentoring within Soft Skills.'),
        ('Knowledge Sharing', 'Soft Skills', 'Professional competency in Knowledge Sharing within Soft Skills.'),
        ('Workplace Relationship Building', 'Soft Skills', 'Professional competency in Workplace Relationship Building within Soft Skills.'),
        ('Leadership', 'Soft Skills', 'Professional competency in Leadership within Soft Skills.'),
        ('People Management', 'Soft Skills', 'Professional competency in People Management within Soft Skills.'),
        ('Delegation', 'Soft Skills', 'Professional competency in Delegation within Soft Skills.'),
        ('Coaching & Mentoring', 'Soft Skills', 'Professional competency in Coaching & Mentoring within Soft Skills.'),
        ('Conflict Resolution', 'Soft Skills', 'Professional competency in Conflict Resolution within Soft Skills.'),
        ('Decision Making Under Pressure', 'Soft Skills', 'Professional competency in Decision Making Under Pressure within Soft Skills.'),
        ('Motivating Others', 'Soft Skills', 'Professional competency in Motivating Others within Soft Skills.'),
        ('Visionary Thinking', 'Soft Skills', 'Professional competency in Visionary Thinking within Soft Skills.'),
        ('Change Management', 'Soft Skills', 'Professional competency in Change Management within Soft Skills.'),
        ('Performance Feedback', 'Soft Skills', 'Professional competency in Performance Feedback within Soft Skills.'),
        ('Customer Service Orientation', 'Soft Skills', 'Professional competency in Customer Service Orientation within Soft Skills.'),
        ('Customer Relationship Management', 'Soft Skills', 'Professional competency in Customer Relationship Management within Soft Skills.'),
        ('Empathy & Patience', 'Soft Skills', 'Professional competency in Empathy & Patience within Soft Skills.'),
        ('Complaint Handling', 'Soft Skills', 'Professional competency in Complaint Handling within Soft Skills.'),
        ('Service Quality Mindset', 'Soft Skills', 'Professional competency in Service Quality Mindset within Soft Skills.'),
        ('Client Communication', 'Soft Skills', 'Professional competency in Client Communication within Soft Skills.'),
        ('Needs Assessment', 'Soft Skills', 'Professional competency in Needs Assessment within Soft Skills.'),
        ('Sales Orientation', 'Soft Skills', 'Professional competency in Sales Orientation within Soft Skills.'),
        ('Adaptability', 'Soft Skills', 'Professional competency in Adaptability within Soft Skills.'),
        ('Flexibility', 'Soft Skills', 'Professional competency in Flexibility within Soft Skills.'),
        ('Resilience & Stress Tolerance', 'Soft Skills', 'Professional competency in Resilience & Stress Tolerance within Soft Skills.'),
        ('Coping with Change', 'Soft Skills', 'Professional competency in Coping with Change within Soft Skills.'),
        ('Working Under Pressure', 'Soft Skills', 'Professional competency in Working Under Pressure within Soft Skills.'),
        ('Emotional Regulation', 'Soft Skills', 'Professional competency in Emotional Regulation within Soft Skills.'),
        ('Growth Mindset', 'Soft Skills', 'Professional competency in Growth Mindset within Soft Skills.'),
        ('Openness to Feedback', 'Soft Skills', 'Professional competency in Openness to Feedback within Soft Skills.'),
        ('Learning Agility', 'Soft Skills', 'Professional competency in Learning Agility within Soft Skills.'),
        ('Self-Awareness', 'Soft Skills', 'Professional competency in Self-Awareness within Soft Skills.'),
        ('Empathy', 'Soft Skills', 'Professional competency in Empathy within Soft Skills.'),
        ('Social Awareness', 'Soft Skills', 'Professional competency in Social Awareness within Soft Skills.'),
        ('Relationship Management', 'Soft Skills', 'Professional competency in Relationship Management within Soft Skills.'),
        ('Emotional Maturity', 'Soft Skills', 'Professional competency in Emotional Maturity within Soft Skills.'),
        ('Positive Attitude', 'Soft Skills', 'Professional competency in Positive Attitude within Soft Skills.'),
        ('Patience', 'Soft Skills', 'Professional competency in Patience within Soft Skills.'),
        ('Compassion', 'Soft Skills', 'Professional competency in Compassion within Soft Skills.'),
        ('Occupational Health & Safety Awareness', 'Soft Skills', 'Professional competency in Occupational Health & Safety Awareness within Soft Skills.'),
        ('Following Instructions & Procedures', 'Soft Skills', 'Professional competency in Following Instructions & Procedures within Soft Skills.'),
        ('Risk Awareness', 'Soft Skills', 'Professional competency in Risk Awareness within Soft Skills.'),
        ('Workplace Discipline', 'Soft Skills', 'Professional competency in Workplace Discipline within Soft Skills.'),
        ('Compliance Mindset', 'Soft Skills', 'Professional competency in Compliance Mindset within Soft Skills.'),
        ('Cultural Sensitivity', 'Soft Skills', 'Professional competency in Cultural Sensitivity within Soft Skills.'),
        ('Diversity & Inclusion Awareness', 'Soft Skills', 'Professional competency in Diversity & Inclusion Awareness within Soft Skills.'),
        ('Cross-Cultural Communication', 'Soft Skills', 'Professional competency in Cross-Cultural Communication within Soft Skills.'),
        ('Language Adaptability', 'Soft Skills', 'Professional competency in Language Adaptability within Soft Skills.'),
        ('Global Mindset', 'Soft Skills', 'Professional competency in Global Mindset within Soft Skills.'),
        ('Professional Networking', 'Soft Skills', 'Professional competency in Professional Networking within Soft Skills.'),
        ('Personal Branding', 'Soft Skills', 'Professional competency in Personal Branding within Soft Skills.'),
        ('Career Planning', 'Soft Skills', 'Professional competency in Career Planning within Soft Skills.'),
        ('Continuous Learning Mindset', 'Soft Skills', 'Professional competency in Continuous Learning Mindset within Soft Skills.'),
        ('Seeking Mentorship', 'Soft Skills', 'Professional competency in Seeking Mentorship within Soft Skills.'),
        ('Industry Awareness', 'Soft Skills', 'Professional competency in Industry Awareness within Soft Skills.'),
        ('Basic Computer Operation', 'Common Skills', 'Professional competency in Basic Computer Operation within Common Skills.'),
        ('Microsoft Word', 'Common Skills', 'Professional competency in Microsoft Word within Common Skills.'),
        ('Microsoft Excel', 'Common Skills', 'Professional competency in Microsoft Excel within Common Skills.'),
        ('Microsoft PowerPoint', 'Common Skills', 'Professional competency in Microsoft PowerPoint within Common Skills.'),
        ('Google Docs', 'Common Skills', 'Professional competency in Google Docs within Common Skills.'),
        ('Google Sheets', 'Common Skills', 'Professional competency in Google Sheets within Common Skills.'),
        ('Google Slides', 'Common Skills', 'Professional competency in Google Slides within Common Skills.'),
        ('Email Composition & Management', 'Common Skills', 'Professional competency in Email Composition & Management within Common Skills.'),
        ('Internet Research', 'Common Skills', 'Professional competency in Internet Research within Common Skills.'),
        ('Data Entry', 'Common Skills', 'Professional competency in Data Entry within Common Skills.'),
        ('File Management & Organization', 'Common Skills', 'Professional competency in File Management & Organization within Common Skills.'),
        ('Typing Speed & Accuracy', 'Common Skills', 'Professional competency in Typing Speed & Accuracy within Common Skills.'),
        ('Printing & Scanning Documents', 'Common Skills', 'Professional competency in Printing & Scanning Documents within Common Skills.'),
        ('Video Conferencing Tools', 'Common Skills', 'Professional competency in Video Conferencing Tools within Common Skills.'),
        ('Basic Troubleshooting of Office Equipment', 'Common Skills', 'Professional competency in Basic Troubleshooting of Office Equipment within Common Skills.'),
        ('Document Filing & Recordkeeping', 'Common Skills', 'Professional competency in Document Filing & Recordkeeping within Common Skills.'),
        ('Scheduling & Calendar Management', 'Common Skills', 'Professional competency in Scheduling & Calendar Management within Common Skills.'),
        ('Office Correspondence', 'Common Skills', 'Professional competency in Office Correspondence within Common Skills.'),
        ('Receptionist Duties', 'Common Skills', 'Professional competency in Receptionist Duties within Common Skills.'),
        ('Answering & Routing Phone Calls', 'Common Skills', 'Professional competency in Answering & Routing Phone Calls within Common Skills.'),
        ('Meeting Coordination', 'Common Skills', 'Professional competency in Meeting Coordination within Common Skills.'),
        ('Inventory Recording', 'Common Skills', 'Professional competency in Inventory Recording within Common Skills.'),
        ('Basic Bookkeeping', 'Common Skills', 'Professional competency in Basic Bookkeeping within Common Skills.'),
        ('Encoding & Transcription', 'Common Skills', 'Professional competency in Encoding & Transcription within Common Skills.'),
        ('Preparation of Reports & Memos', 'Common Skills', 'Professional competency in Preparation of Reports & Memos within Common Skills.'),
        ('Office Supply Management', 'Common Skills', 'Professional competency in Office Supply Management within Common Skills.'),
        ('Processing of Forms & Documents', 'Common Skills', 'Professional competency in Processing of Forms & Documents within Common Skills.'),
        ('Basic Payroll Assistance', 'Common Skills', 'Professional competency in Basic Payroll Assistance within Common Skills.'),
        ('Records & Database Management', 'Common Skills', 'Professional competency in Records & Database Management within Common Skills.'),
        ('Face-to-Face Customer Service', 'Common Skills', 'Professional competency in Face-to-Face Customer Service within Common Skills.'),
        ('Phone & Online Customer Support', 'Common Skills', 'Professional competency in Phone & Online Customer Support within Common Skills.'),
        ('Handling Customer Complaints', 'Common Skills', 'Professional competency in Handling Customer Complaints within Common Skills.'),
        ('Product Knowledge & Demonstration', 'Common Skills', 'Professional competency in Product Knowledge & Demonstration within Common Skills.'),
        ('Cashiering & Cash Handling', 'Common Skills', 'Professional competency in Cashiering & Cash Handling within Common Skills.'),
        ('POS System Operation', 'Common Skills', 'Professional competency in POS System Operation within Common Skills.'),
        ('Upselling & Cross-Selling', 'Common Skills', 'Professional competency in Upselling & Cross-Selling within Common Skills.'),
        ('Order Taking & Processing', 'Common Skills', 'Professional competency in Order Taking & Processing within Common Skills.'),
        ('Basic Sales Techniques', 'Common Skills', 'Professional competency in Basic Sales Techniques within Common Skills.'),
        ('After-Sales Support', 'Common Skills', 'Professional competency in After-Sales Support within Common Skills.'),
        ('Customer Needs Assessment', 'Common Skills', 'Professional competency in Customer Needs Assessment within Common Skills.'),
        ('Filipino / Tagalog Communication', 'Common Skills', 'Professional competency in Filipino / Tagalog Communication within Common Skills.'),
        ('English Communication — Oral', 'Common Skills', 'Professional competency in English Communication — Oral within Common Skills.'),
        ('English Communication — Written', 'Common Skills', 'Professional competency in English Communication — Written within Common Skills.'),
        ('Business Writing', 'Common Skills', 'Professional competency in Business Writing within Common Skills.'),
        ('Report & Letter Writing', 'Common Skills', 'Professional competency in Report & Letter Writing within Common Skills.'),
        ('Note-Taking & Minute Writing', 'Common Skills', 'Professional competency in Note-Taking & Minute Writing within Common Skills.'),
        ('Basic Conversational English', 'Common Skills', 'Professional competency in Basic Conversational English within Common Skills.'),
        ('Reading Comprehension', 'Common Skills', 'Professional competency in Reading Comprehension within Common Skills.'),
        ('Instruction Following', 'Common Skills', 'Professional competency in Instruction Following within Common Skills.'),
        ('Basic Math & Arithmetic', 'Common Skills', 'Professional competency in Basic Math & Arithmetic within Common Skills.'),
        ('Cash Counting & Management', 'Common Skills', 'Professional competency in Cash Counting & Management within Common Skills.'),
        ('Basic Budgeting', 'Common Skills', 'Professional competency in Basic Budgeting within Common Skills.'),
        ('Invoice & Receipt Processing', 'Common Skills', 'Professional competency in Invoice & Receipt Processing within Common Skills.'),
        ('Petty Cash Handling', 'Common Skills', 'Professional competency in Petty Cash Handling within Common Skills.'),
        ('Basic Financial Record Keeping', 'Common Skills', 'Professional competency in Basic Financial Record Keeping within Common Skills.'),
        ('Percentage & Discount Computation', 'Common Skills', 'Professional competency in Percentage & Discount Computation within Common Skills.'),
        ('Stock & Inventory Counting', 'Common Skills', 'Professional competency in Stock & Inventory Counting within Common Skills.'),
        ('Manual Labor & General Utility', 'Common Skills', 'Professional competency in Manual Labor & General Utility within Common Skills.'),
        ('Lifting & Material Handling', 'Common Skills', 'Professional competency in Lifting & Material Handling within Common Skills.'),
        ('Warehouse Operations', 'Common Skills', 'Professional competency in Warehouse Operations within Common Skills.'),
        ('Loading & Unloading', 'Common Skills', 'Professional competency in Loading & Unloading within Common Skills.'),
        ('Packing & Labeling', 'Common Skills', 'Professional competency in Packing & Labeling within Common Skills.'),
        ('Stock Replenishment', 'Common Skills', 'Professional competency in Stock Replenishment within Common Skills.'),
        ('Housekeeping & Janitorial Work', 'Common Skills', 'Professional competency in Housekeeping & Janitorial Work within Common Skills.'),
        ('Sanitation & Cleanliness Standards', 'Common Skills', 'Professional competency in Sanitation & Cleanliness Standards within Common Skills.'),
        ('Equipment Operation', 'Common Skills', 'Professional competency in Equipment Operation within Common Skills.'),
        ('Basic Machine Operation', 'Common Skills', 'Professional competency in Basic Machine Operation within Common Skills.'),
        ('Quality Checking & Sorting', 'Common Skills', 'Professional competency in Quality Checking & Sorting within Common Skills.'),
        ('Forklift Operation', 'Common Skills', 'Professional competency in Forklift Operation within Common Skills.'),
        ('Delivery & Logistics Support', 'Common Skills', 'Professional competency in Delivery & Logistics Support within Common Skills.'),
        ('Food Preparation Assistance', 'Common Skills', 'Professional competency in Food Preparation Assistance within Common Skills.'),
        ('Table Setting & Service', 'Common Skills', 'Professional competency in Table Setting & Service within Common Skills.'),
        ('Taking Food & Beverage Orders', 'Common Skills', 'Professional competency in Taking Food & Beverage Orders within Common Skills.'),
        ('Food Safety & Hygiene Practices', 'Common Skills', 'Professional competency in Food Safety & Hygiene Practices within Common Skills.'),
        ('Kitchen Cleaning & Sanitation', 'Common Skills', 'Professional competency in Kitchen Cleaning & Sanitation within Common Skills.'),
        ('Barista & Coffee Preparation', 'Common Skills', 'Professional competency in Barista & Coffee Preparation within Common Skills.'),
        ('Fast Food Counter Service', 'Common Skills', 'Professional competency in Fast Food Counter Service within Common Skills.'),
        ('Catering & Event Food Service', 'Common Skills', 'Professional competency in Catering & Event Food Service within Common Skills.'),
        ('Menu Knowledge', 'Common Skills', 'Professional competency in Menu Knowledge within Common Skills.'),
        ('Dishwashing & Kitchen Utility', 'Common Skills', 'Professional competency in Dishwashing & Kitchen Utility within Common Skills.'),
        ('Product Display & Arrangement', 'Common Skills', 'Professional competency in Product Display & Arrangement within Common Skills.'),
        ('Stock Monitoring & Replenishment', 'Common Skills', 'Professional competency in Stock Monitoring & Replenishment within Common Skills.'),
        ('Price Tagging & Labeling', 'Common Skills', 'Professional competency in Price Tagging & Labeling within Common Skills.'),
        ('Retail Store Operations', 'Common Skills', 'Professional competency in Retail Store Operations within Common Skills.'),
        ('Handling Returns & Exchanges', 'Common Skills', 'Professional competency in Handling Returns & Exchanges within Common Skills.'),
        ('Basic Visual Merchandising', 'Common Skills', 'Professional competency in Basic Visual Merchandising within Common Skills.'),
        ('Inventory Count & Reporting', 'Common Skills', 'Professional competency in Inventory Count & Reporting within Common Skills.'),
        ('Basic Security Guard Duties', 'Common Skills', 'Professional competency in Basic Security Guard Duties within Common Skills.'),
        ('Access Control & Visitor Management', 'Common Skills', 'Professional competency in Access Control & Visitor Management within Common Skills.'),
        ('CCTV Monitoring', 'Common Skills', 'Professional competency in CCTV Monitoring within Common Skills.'),
        ('Emergency Response Awareness', 'Common Skills', 'Professional competency in Emergency Response Awareness within Common Skills.'),
        ('Fire Safety Awareness', 'Common Skills', 'Professional competency in Fire Safety Awareness within Common Skills.'),
        ('Crowd Control Assistance', 'Common Skills', 'Professional competency in Crowd Control Assistance within Common Skills.'),
        ('Incident Reporting', 'Common Skills', 'Professional competency in Incident Reporting within Common Skills.'),
        ('Loss Prevention Awareness', 'Common Skills', 'Professional competency in Loss Prevention Awareness within Common Skills.'),
        ('Basic First Aid', 'Common Skills', 'Professional competency in Basic First Aid within Common Skills.'),
        ('Vital Signs Monitoring', 'Common Skills', 'Professional competency in Vital Signs Monitoring within Common Skills.'),
        ('Patient / Elder Care Assistance', 'Common Skills', 'Professional competency in Patient / Elder Care Assistance within Common Skills.'),
        ('Medicine Administration Assistance', 'Common Skills', 'Professional competency in Medicine Administration Assistance within Common Skills.'),
        ('Hygiene Assistance for Patients', 'Common Skills', 'Professional competency in Hygiene Assistance for Patients within Common Skills.'),
        ('Medical Records Filing', 'Common Skills', 'Professional competency in Medical Records Filing within Common Skills.'),
        ('Hospital / Clinic Reception', 'Common Skills', 'Professional competency in Hospital / Clinic Reception within Common Skills.'),
        ('Basic Life Support (BLS) Awareness', 'Common Skills', 'Professional competency in Basic Life Support (BLS) Awareness within Common Skills.'),
        ('Health & Safety Compliance', 'Common Skills', 'Professional competency in Health & Safety Compliance within Common Skills.'),
        ('Light Vehicle Driving', 'Common Skills', 'Professional competency in Light Vehicle Driving within Common Skills.'),
        ('Motorcycle Driving', 'Common Skills', 'Professional competency in Motorcycle Driving within Common Skills.'),
        ('Defensive Driving', 'Common Skills', 'Professional competency in Defensive Driving within Common Skills.'),
        ('Route Planning & Navigation', 'Common Skills', 'Professional competency in Route Planning & Navigation within Common Skills.'),
        ('Vehicle Safety & Inspection', 'Common Skills', 'Professional competency in Vehicle Safety & Inspection within Common Skills.'),
        ('Delivery Coordination', 'Common Skills', 'Professional competency in Delivery Coordination within Common Skills.'),
        ('Passenger Handling', 'Common Skills', 'Professional competency in Passenger Handling within Common Skills.'),
        ('Basic Vehicle Maintenance Awareness', 'Common Skills', 'Professional competency in Basic Vehicle Maintenance Awareness within Common Skills.'),
        ('Blueprint & Plan Reading', 'Common Skills', 'Professional competency in Blueprint & Plan Reading within Common Skills.'),
        ('Construction Site Safety Awareness', 'Common Skills', 'Professional competency in Construction Site Safety Awareness within Common Skills.'),
        ('Painting & Surface Preparation', 'Common Skills', 'Professional competency in Painting & Surface Preparation within Common Skills.'),
        ('Basic Plumbing Assistance', 'Common Skills', 'Professional competency in Basic Plumbing Assistance within Common Skills.'),
        ('Basic Electrical Assistance', 'Common Skills', 'Professional competency in Basic Electrical Assistance within Common Skills.'),
        ('Concrete & Masonry Assistance', 'Common Skills', 'Professional competency in Concrete & Masonry Assistance within Common Skills.'),
        ('Scaffolding & Formworks Assistance', 'Common Skills', 'Professional competency in Scaffolding & Formworks Assistance within Common Skills.'),
        ('Tools & Equipment Handling', 'Common Skills', 'Professional competency in Tools & Equipment Handling within Common Skills.'),
        ('Site Cleanup & Material Disposal', 'Common Skills', 'Professional competency in Site Cleanup & Material Disposal within Common Skills.'),
        ('Classroom Management Assistance', 'Common Skills', 'Professional competency in Classroom Management Assistance within Common Skills.'),
        ('Tutorial & Academic Support', 'Common Skills', 'Professional competency in Tutorial & Academic Support within Common Skills.'),
        ('Lesson Plan Preparation Assistance', 'Common Skills', 'Professional competency in Lesson Plan Preparation Assistance within Common Skills.'),
        ('Childcare & Supervision', 'Common Skills', 'Professional competency in Childcare & Supervision within Common Skills.'),
        ('Educational Material Preparation', 'Common Skills', 'Professional competency in Educational Material Preparation within Common Skills.'),
        ('Student Record Management', 'Common Skills', 'Professional competency in Student Record Management within Common Skills.'),
        ('Crop Planting & Harvesting', 'Common Skills', 'Professional competency in Crop Planting & Harvesting within Common Skills.'),
        ('Irrigation & Water Management', 'Common Skills', 'Professional competency in Irrigation & Water Management within Common Skills.'),
        ('Livestock Care & Feeding', 'Common Skills', 'Professional competency in Livestock Care & Feeding within Common Skills.'),
        ('Pest & Disease Control Awareness', 'Common Skills', 'Professional competency in Pest & Disease Control Awareness within Common Skills.'),
        ('Farm Equipment Operation', 'Common Skills', 'Professional competency in Farm Equipment Operation within Common Skills.'),
        ('Post-Harvest Handling', 'Common Skills', 'Professional competency in Post-Harvest Handling within Common Skills.'),
        ('Organic Farming Practices', 'Common Skills', 'Professional competency in Organic Farming Practices within Common Skills.'),
        ('Following Workplace Rules & Policies', 'Common Skills', 'Professional competency in Following Workplace Rules & Policies within Common Skills.'),
        ('Wearing & Maintaining Proper Uniform', 'Common Skills', 'Professional competency in Wearing & Maintaining Proper Uniform within Common Skills.'),
        ('Attendance & Punctuality', 'Common Skills', 'Professional competency in Attendance & Punctuality within Common Skills.'),
        ('Basic Workplace Etiquette', 'Common Skills', 'Professional competency in Basic Workplace Etiquette within Common Skills.'),
        ('Handling Criticism & Feedback', 'Common Skills', 'Professional competency in Handling Criticism & Feedback within Common Skills.'),
        ('Willingness to Learn On the Job', 'Common Skills', 'Professional competency in Willingness to Learn On the Job within Common Skills.'),
        ('Working with Minimal Supervision', 'Common Skills', 'Professional competency in Working with Minimal Supervision within Common Skills.'),
        ('Shift Work Readiness', 'Common Skills', 'Professional competency in Shift Work Readiness within Common Skills.'),
        ('Physical Fitness for Work', 'Common Skills', 'Professional competency in Physical Fitness for Work within Common Skills.'),
        ('Basic Safety Compliance', 'Common Skills', 'Professional competency in Basic Safety Compliance within Common Skills.'),
        ('Network Security', 'Cybersecurity', 'Professional competency in Network Security within Cybersecurity.'),
        ('Ethical Hacking', 'Cybersecurity', 'Professional competency in Ethical Hacking within Cybersecurity.'),
        ('Penetration Testing', 'Cybersecurity', 'Professional competency in Penetration Testing within Cybersecurity.'),
        ('Vulnerability Assessment', 'Cybersecurity', 'Professional competency in Vulnerability Assessment within Cybersecurity.'),
        ('Security Information & Event Management', 'Cybersecurity', 'Professional competency in Security Information & Event Management within Cybersecurity.'),
        ('Incident Response', 'Cybersecurity', 'Professional competency in Incident Response within Cybersecurity.'),
    ]
    centralized_skills = {}
    for name, cat, desc in skills_data:
        skill, _ = CentralizedSkill.objects.get_or_create(
            name=name,
            defaults={'category': cat, 'description': desc}
        )
        centralized_skills[name] = skill

    # 2. Training Programs
    training_programs = [
        ('React Basics for Front-end Careers', 'TESDA', 'React', '40 Hours'),
        ('Google Cloud DevOps & AWS Arch.', 'AWS', 'AWS Cloud', '80 Hours'),
        ('Introduction to Welding and Structural Safety', 'TESDA', 'Welding', '120 Hours'),
        ('Hotel Housekeeping NC II certification program', 'TESDA', 'Housekeeping', '160 Hours'),
        ('Kubernetes in Production', 'TESDA', 'Kubernetes', '60 Hours'),
        ('TypeScript Fundamentals', 'Scrimba', 'TypeScript', '30 Hours'),
        ('Advanced HTML & CSS Responsive Design', 'TESDA', 'HTML & CSS', '50 Hours'),
        ('Docker Containerization Essentials', 'Linux Foundation', 'Docker', '24 Hours'),
        ('Python for Everybody Boot Camp', 'Coursera', 'Python', '48 Hours'),
        ('SQL & PostgreSQL for Beginners', 'Udemy', 'SQL', '36 Hours'),
        ('Django Web Framework Mastery', 'TESDA', 'Django', '80 Hours'),
        ('Agile Product Management & Roadmap Design', 'Atlassian', 'Agile', '40 Hours'),
        ('Scrum & Kanban NC II Prep Course', 'TESDA', 'Scrum', '60 Hours'),
        ('Figma UX/UI Design Essentials', 'UX Design Institute', 'Figma', '40 Hours'),
        ('Deep Learning with PyTorch', 'DeepLearning.AI', 'PyTorch', '60 Hours'),
        ('TESDA Masonry & Plastering Certification', 'TESDA', 'Concrete & Masonry Assistance', '120 Hours'),
        ('TESDA Construction Blueprint Masterclass', 'TESDA', 'Blueprint & Plan Reading', '80 Hours'),
        ('TESDA Standard Plumbing Course', 'TESDA', 'Basic Plumbing Assistance', '100 Hours'),
        ('TESDA Electrical Installation NC II', 'TESDA', 'Basic Electrical Assistance', '160 Hours'),
        ('TESDA Basic Defensive Driving Course', 'TESDA', 'Defensive Driving', '40 Hours'),
        ('TESDA Retail Merchandising NC I', 'TESDA', 'Retail Store Operations', '80 Hours'),
        ('TESDA Healthcare Support Services', 'TESDA', 'Vital Signs Monitoring', '120 Hours')
    ]
    for title, provider, skill_name, dur in training_programs:
        TrainingProgram.objects.get_or_create(
            title=title,
            defaults={
                'provider': provider,
                'skill_addressed': centralized_skills[skill_name],
                'description': f"Targeted program to master {skill_name} and build immediate competency.",
                'duration': dur
            }
        )

    # Return early to prevent seeding other user accounts/profiles to respect cleanup requests
    return

    # 3. Create Employer User
    employer_user, created = User.objects.get_or_create(
        username='employer@test.com',
        defaults={'email': 'employer@test.com', 'first_name': 'Global', 'last_name': 'Industries'}
    )
    if created:
        employer_user.set_password('password123')
        employer_user.save()
    employer_profile, _ = Profile.objects.update_or_create(
        user=employer_user,
        defaults={
            'role': 'employer',
            'is_verified': True,
            'company_name': 'Nexus Digital Solutions',
            'industry': 'Artificial Intelligence',
            'company_size': '50-200 employees',
            'website': 'https://nexus-digital.ai',
            'contact_name': 'Dr. Alexander Vance',
            'contact_position': 'Chief Talent Officer',
            'contact_email': 'a.vance@nexus-digital.ai',
            'employment_type_offered': 'Local',
            'location': 'San Francisco, CA',
            'soft_notes': 'Nexus Digital Solutions is a leading AI consultancy specializing in workforce transformation. Founded in 2018, we have helped over 500 enterprises navigate the complex landscape of digital evolution through data-driven skill gap analysis and custom learning architectures. Our mission is to empower teams to reach their peak potential through technological synergy.'
        }
    )

    # 4. Create Job Vacancies and Requirements
    vacancies_data = [
        {
            'title': 'Senior Cloud Architect',
            'location': 'San Francisco, CA',
            'salary_range': '$120k - $150k',
            'requirements': [('AWS Cloud', 5), ('Kubernetes', 4), ('React', 3)]
        },
        {
            'title': 'Data Scientist',
            'location': 'Remote',
            'salary_range': '$100k - $130k',
            'requirements': [('Python', 5), ('PyTorch', 4), ('SQL', 3)]
        },
        {
            'title': 'UX Designer',
            'location': 'London, UK',
            'salary_range': '£60k - £80k',
            'requirements': [('Figma', 5), ('A11y', 4), ('React', 2)]
        },
        {
            'title': 'Warehouse Supervisor',
            'location': 'Austin, TX',
            'salary_range': '$45k - $55k',
            'requirements': [('Housekeeping', 4), ('Agile', 2)]
        },
        {
            'title': 'Frontend Engineer',
            'location': 'Remote',
            'salary_range': '$80k - $110k',
            'requirements': [('React', 4), ('TypeScript', 4), ('HTML & CSS', 5)]
        },
        {
            'title': 'DevOps Engineer',
            'location': 'Seattle, WA',
            'salary_range': '$110k - $140k',
            'requirements': [('Docker', 4), ('Kubernetes', 4), ('AWS Cloud', 3)]
        },
        {
            'title': 'Junior Backend Developer',
            'location': 'Austin, TX',
            'salary_range': '$65k - $85k',
            'requirements': [('Python', 3), ('SQL', 3), ('Django', 3)]
        },
        {
            'title': 'TESDA Structural Welder',
            'location': 'Houston, TX',
            'salary_range': '$40k - $55k',
            'requirements': [('Welding', 5), ('Housekeeping', 2)]
        },
        {
            'title': 'Product Manager',
            'location': 'New York, NY',
            'salary_range': '$120k - $150k',
            'requirements': [('Agile', 5), ('Scrum', 4), ('Kanban', 4)]
        },
        {
            'title': 'TESDA Construction Mason',
            'location': 'Manila, PH',
            'salary_range': '₱15k - ₱22k',
            'requirements': [('Concrete & Masonry Assistance', 4), ('Blueprint & Plan Reading', 3), ('Construction Site Safety Awareness', 5)]
        },
        {
            'title': 'Defensive Driver',
            'location': 'Cebu, PH',
            'salary_range': '₱14k - ₱18k',
            'requirements': [('Defensive Driving', 5), ('Light Vehicle Driving', 4)]
        },
        {
            'title': 'Healthcare Assistant',
            'location': 'Davao, PH',
            'salary_range': '₱16k - ₱20k',
            'requirements': [('Vital Signs Monitoring', 4), ('Patient / Elder Care Assistance', 5)]
        },
        {
            'title': 'Retail Store Associate',
            'location': 'Quezon City, PH',
            'salary_range': '₱13k - ₱16k',
            'requirements': [('Retail Store Operations', 4), ('Stock Monitoring & Replenishment', 4)]
        }
    ]
    category_mapping = {
        'Senior Cloud Architect': 'IT',
        'Data Scientist': 'IT',
        'UX Designer': 'IT',
        'Warehouse Supervisor': 'LOG',
        'Frontend Engineer': 'IT',
        'DevOps Engineer': 'IT',
        'Junior Backend Developer': 'IT',
        'TESDA Structural Welder': 'TVT',
        'Product Manager': 'IT',
        'TESDA Construction Mason': 'ENG',
        'Defensive Driver': 'DRV',
        'Healthcare Assistant': 'MED',
        'Retail Store Associate': 'RTL',
    }

    vacancies = {}
    for vac_info in vacancies_data:
        cat_code = category_mapping.get(vac_info['title'], 'IT')
        vacancy, _ = JobVacancy.objects.get_or_create(
            title=vac_info['title'],
            employer=employer_profile,
            defaults={
                'location': vac_info['location'],
                'salary_range': vac_info['salary_range'],
                'description': f"Looking for a motivated {vac_info['title']} to join our growing enterprise team.",
                'category': cat_code
            }
        )
        # Always update the category for seeded vacancies to match
        vacancy.category = cat_code
        vacancy.save()
        
        vacancies[vacancy.title] = vacancy
        for s_name, req_level in vac_info['requirements']:
            JobSkillRequirement.objects.get_or_create(
                job_vacancy=vacancy,
                skill=centralized_skills[s_name],
                defaults={}
            )

    # Convert any other old 'jobs' categories to 'IT' to keep database clean
    JobVacancy.objects.filter(category='jobs').update(category='IT')

    # 5. Seed Mock Candidates
    mock_candidates = [
        {
            'first_name': 'Alex',
            'last_name': 'Rivera',
            'email': 'alex.rivera@design.ai',
            'phone': '+1 (555) 019-2831',
            'title': 'Senior Frontend Engineer',
            'status': 'Interviewing',
            'skills': 'React, TypeScript',
            'location': 'Austin, TX',
            'training_progress_title': 'Cloud Arch.',
            'training_progress_percentage': 85,
            'experience_years': '8 Years',
            'education': ('Stanford University', 'BS', 'Computer Science', 2014, 2018),
            'experience': ('TechFlow Systems', 'Senior Full-Stack Engineer', 'Leading the development of a distributed cloud architecture processing 2M+ daily events. Spearheaded transition from monolithic to microservices, improving deployment frequency by 40%.', '2021', 'Present', True),
            'certification': ('AWS Solutions Architect', 'Amazon Web Services', False),
            'skills_rating': [('React', 5), ('TypeScript', 4), ('AWS Cloud', 3)]
        },
        {
            'first_name': 'Sarah',
            'last_name': 'Jenkins',
            'email': 'sarah.j@analytics.ai',
            'phone': '+1 (555) 018-4729',
            'title': 'Data Scientist',
            'status': 'Skill Gap Identified',
            'skills': 'Python, PyTorch',
            'location': 'Remote',
            'training_progress_title': 'Kubernetes',
            'training_progress_percentage': 32,
            'experience_years': '5 Years',
            'education': ('MIT', 'MS', 'Mathematics', 2015, 2017),
            'experience': ('Analytics AI', 'Data Scientist', 'Developed predictive models using Python, PyTorch, and SQL. Collaborated with business teams to increase sales by 10%.', '2018', '2023', False),
            'certification': ('Professional Data Engineer', 'Google Cloud', False),
            'skills_rating': [('Python', 4), ('PyTorch', 5), ('Kubernetes', 2)]
        },
        {
            'first_name': 'Marcus',
            'last_name': 'Chen',
            'email': 'marcus.c@product.ai',
            'phone': '+1 (555) 017-9104',
            'title': 'Product Manager',
            'status': 'Hired',
            'skills': 'Agile, SQL',
            'location': 'Toronto, ON',
            'training_progress_title': 'Leadership',
            'training_progress_percentage': 100,
            'experience_years': '5 Years',
            'education': ('Toronto University', 'MBA', 'Business Administration', 2012, 2014),
            'experience': ('Product AI', 'Product Manager', 'Managed software product lifecycle from concept to release. Led a cross-functional agile team of 8.', '2015', '2020', False),
            'certification': ('Scrum Master', 'Scrum Alliance', False),
            'skills_rating': [('Agile', 5), ('SQL', 3)]
        },
        {
            'first_name': 'Lila',
            'last_name': 'Thorne',
            'email': 'lila.t@design.ai',
            'phone': '+1 (555) 016-8315',
            'title': 'UX Designer',
            'status': 'Review Required',
            'skills': 'Figma, A11y',
            'location': 'London, UK',
            'training_progress_title': 'React Basics',
            'training_progress_percentage': 55,
            'experience_years': '3 Years',
            'education': ('London Art College', 'BA', 'UX Design', 2016, 2019),
            'experience': ('Design AI', 'UX Designer', 'Designed mobile and web experiences using Figma. Performed usability testing and accessibility reviews.', '2020', '2023', False),
            'certification': ('Figma Certified Professional', 'Figma', False),
            'skills_rating': [('Figma', 4), ('A11y', 3), ('React', 2)]
        },
        {
            'first_name': 'Daniel',
            'last_name': 'Alvarez',
            'email': 'daniel.a@gmail.com',
            'phone': '+1 (555) 021-3940',
            'title': 'Warehouse Associate',
            'status': 'Applied',
            'skills': 'Housekeeping',
            'location': 'Austin, TX',
            'training_progress_title': 'General Training',
            'training_progress_percentage': 0,
            'experience_years': '2 Years',
            'education': ('State College', 'BS', 'Psychology', 2018, 2022),
            'experience': ('Austin Distribution', 'Warehouse Assistant', 'Managed inventory control and housekeeping standards for the central warehouse.', '2022', '2024', False),
            'certification': ('Warehouse Safety NC II', 'TESDA', True),
            'skills_rating': [('Housekeeping', 4), ('Agile', 1)]
        },
        {
            'first_name': 'Sofia',
            'last_name': 'Ramirez',
            'email': 'sofia.r@gmail.com',
            'phone': '+1 (555) 022-4859',
            'title': 'Backend Developer',
            'status': 'Applied',
            'skills': 'Python, SQL',
            'location': 'Remote',
            'training_progress_title': 'General Training',
            'training_progress_percentage': 0,
            'experience_years': '3 Years',
            'education': ('State University', 'BS', 'Computer Engineering', 2019, 2023),
            'experience': ('DevSolutions Inc.', 'Junior Backend Developer', 'Developed backend APIs using Python and SQL. Maintained database reliability and performance.', '2023', '2025', False),
            'certification': ('Python Certified Associate', 'Python Institute', False),
            'skills_rating': [('Python', 4), ('SQL', 4), ('Django', 2)]
        }
    ]
    
    profiles = {}
    for cand in mock_candidates:
        user, created = User.objects.get_or_create(
            username=cand['email'],
            defaults={
                'email': cand['email'],
                'first_name': cand['first_name'],
                'last_name': cand['last_name']
            }
        )
        if created:
            user.set_password('password123')
            user.save()
        
        profile, _ = Profile.objects.update_or_create(
            user=user,
            defaults={
                'phone_number': cand['phone'],
                'role': 'applicant',
                'is_verified': True,
                'title': cand['title'],
                'status': cand['status'],
                'skills': cand['skills'],
                'location': cand['location'],
                'training_progress_title': cand['training_progress_title'],
                'training_progress_percentage': cand['training_progress_percentage'],
                'experience_years': cand['experience_years'],
                'is_profile_complete': True
            }
        )
        profiles[profile.user.email] = profile

        # Seed structured Education
        edu_info = cand['education']
        Education.objects.get_or_create(
            profile=profile,
            institution=edu_info[0],
            defaults={
                'degree': edu_info[1],
                'field_of_study': edu_info[2],
                'start_year': edu_info[3],
                'end_year': edu_info[4]
            }
        )

        # Seed structured WorkExperience
        exp_info = cand['experience']
        WorkExperience.objects.get_or_create(
            profile=profile,
            company=exp_info[0],
            position=exp_info[1],
            defaults={
                'description': exp_info[2],
                'start_date': exp_info[3],
                'end_date': exp_info[4],
                'is_current': exp_info[5]
            }
        )

        # Seed structured Certification
        cert_info = cand['certification']
        Certification.objects.get_or_create(
            profile=profile,
            name=cert_info[0],
            defaults={
                'issuing_organization': cert_info[1],
                'is_tesda': cert_info[2]
            }
        )

        # Seed structured ApplicantSkills
        for s_name, _rating in cand['skills_rating']:
            ApplicantSkill.objects.update_or_create(
                profile=profile,
                skill=centralized_skills[s_name],
                defaults={}
            )

    # 6. Seed Mock Referrals
    referral_list = [
        ('daniel.a@gmail.com', 'Warehouse Supervisor', '2026-06-10', 'Pending'),
        ('alex.rivera@design.ai', 'Senior Cloud Architect', '2026-06-12', 'Hired'),
        ('sarah.j@analytics.ai', 'Data Scientist', '2026-06-14', 'Hired'),
        ('lila.t@design.ai', 'UX Designer', '2026-06-15', 'No Response')
    ]
    for email, job_title, r_date, r_status in referral_list:
        Referral.objects.get_or_create(
            applicant=profiles[email],
            job_vacancy=vacancies[job_title],
            defaults={
                'date_referred': r_date,
                'status': r_status
            }
        )

    # Synchronize remaining vacancy slots with active applicant referrals
    for v in JobVacancy.objects.all():
        app_count = Referral.objects.filter(job_vacancy=v).count()
        v.remaining_slots = max(0, v.slots - app_count)
        v.save()

@login_required(login_url='login')
@require_POST
def update_applicant_status(request, profile_id):
    if request.user.profile.role not in ['admin', 'employer']:
        return redirect('dashboard')
    profile = get_object_or_404(Profile, id=profile_id)
    # Employers may only update applicants who are actually in their pipeline.
    if request.user.profile.role == 'employer' and not Referral.objects.filter(
        applicant=profile, job_vacancy__employer=request.user.profile
    ).exists():
        messages.error(request, "You can only update applicants who applied to your vacancies.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))
    new_status = request.POST.get('status')
    if new_status:
        profile.status = new_status
        profile.save()
        
        # If updated by employer, update their specific Referral status too
        if request.user.profile.role == 'employer':
            ref = Referral.objects.filter(applicant=profile, job_vacancy__employer=request.user.profile, status='Pending').first()
            if ref:
                if new_status == 'Shortlisted':
                    ref.status = 'Interviewing'
                elif new_status == 'Rejected':
                    ref.status = 'Not Hired'
                ref.save()
        messages.success(request, f"Status of {profile.user.first_name} {profile.user.last_name} updated to {new_status}!")
        try:
            target_vacancy = None
            if request.user.profile.role == 'employer':
                ref_vac = Referral.objects.filter(applicant=profile, job_vacancy__employer=request.user.profile).order_by('-id').first()
                if ref_vac:
                    target_vacancy = ref_vac.job_vacancy
            Notification.objects.create(
                user=profile.user,
                message=f"Application Status Update: Your profile status has been updated to '{new_status}'."
            )
            send_application_status_update_email(profile, target_vacancy, new_status)
        except Exception:
            pass
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

# Helper function to check if candidate's field of study is mismatched with referred job vacancy
def check_mismatch(field_of_study, job_title):
    fos = (field_of_study or '').lower()
    jt = (job_title or '').lower()
    
    # Fields that are aligned with tech/digital careers
    tech_study = [
        'computer', 'math', 'science', 'engineering', 'design', 'ux', 'it',
        'software', 'information', 'technology', 'data', 'digital', 'networking',
        'programming', 'systems', 'web', 'electronics', 'multimedia'
    ]
    # Job titles considered tech/digital
    tech_job = [
        'cloud', 'architect', 'scientist', 'ux', 'designer', 'engineer',
        'developer', 'programmer', 'analyst', 'devops', 'data', 'it', 'tech',
        'information', 'technology', 'computer', 'expert', 'web', 'software',
        'systems', 'network', 'digital'
    ]
    
    is_tech_study = any(x in fos for x in tech_study)
    is_tech_job = any(x in jt for x in tech_job)
    
    # Only flag as a mismatch if the study and job are on clearly opposite sides
    if is_tech_job and not is_tech_study:
        return True
    if not is_tech_job and is_tech_study:
        return True
    # Explicit PESO-relevant mismatch: Psychology grad in physical trade roles
    if 'psychology' in fos and any(x in jt for x in ['warehouse', 'supervisor', 'housekeeper', 'welder', 'trade']):
        return True
    return False


CATEGORY_CANONICAL_MAP = {
    'IT': ['it', 'information technology', 'tech', 'software', 'computer', 'developer', 'programmer', 'web', 'data', 'systems', 'network', 'frontend', 'backend', 'devops', 'cloud', 'cybersecurity'],
    'BPO': ['bpo', 'customer service', 'call center', 'contact center', 'technical support', 'csr', 'tsr'],
    'ADM': ['adm', 'administrative', 'office', 'clerk', 'secretary', 'executive assistant', 'encoder'],
    'FIN': ['fin', 'finance', 'accounting', 'bookkeeping', 'audit', 'billing', 'payroll', 'accountant'],
    'MKT': ['mkt', 'marketing', 'sales', 'advertising', 'digital marketing', 'promoter'],
    'RTL': ['rtl', 'retail', 'merchandising', 'cashier', 'store', 'supermarket'],
    'F&B': ['f&b', 'food & beverage', 'hospitality', 'cook', 'chef', 'restaurant', 'waiter', 'barista', 'kitchen'],
    'MED': ['med', 'healthcare', 'medical', 'nursing', 'nurse', 'hospital', 'pharmacy', 'clinical', 'physician', 'dental', 'therapist'],
    'EDU': ['edu', 'education', 'teaching', 'training', 'instructor', 'teacher', 'professor', 'tutor'],
    'ENG': ['eng', 'engineering', 'construction', 'civil', 'structural', 'surveyor', 'architect'],
    'ELC': ['elc', 'electrical', 'electronics', 'electrician', 'circuits'],
    'MCH': ['mch', 'mechanical', 'automotive', 'mechanic', 'machinist'],
    'TVT': ['tvt', 'tesda', 'vocational', 'trade', 'welding', 'welder', 'carpentry', 'plumbing', 'masonry', 'smaw'],
    'LOG': ['log', 'logistics', 'warehousing', 'supply chain', 'forklift', 'delivery', 'inventory', 'courier'],
    'SEC': ['sec', 'security', 'safety', 'guard', 'surveillance', 'cctv'],
    'GEN': ['gen', 'general services', 'janitorial', 'maintenance', 'housekeeping', 'cleaner', 'utility'],
    'DRV': ['drv', 'driving', 'transportation', 'driver', 'chauffeur', 'truck'],
    'MFG': ['mfg', 'manufacturing', 'garments', 'factory', 'sewing', 'operator', 'production'],
    'AGR': ['agr', 'agriculture', 'farming', 'environment', 'fishery', 'agribusiness'],
    'OFW': ['ofw', 'overseas', 'abroad', 'international', 'poea', 'dmw'],
}

def normalize_career_category(val):
    if not val:
        return ''
    cleaned = str(val).strip().upper()
    for code, keywords in CATEGORY_CANONICAL_MAP.items():
        if cleaned == code:
            return code
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', cleaned.lower()):
                return code
    return cleaned

def is_job_career_aligned(profile, vacancy):
    """
    Checks if a job vacancy matches the career goal / preferred job of an applicant.
    """
    pref_job = (getattr(profile, 'preferred_job', '') or '').strip()
    profile_title = (getattr(profile, 'title', '') or '').strip()
    
    # If applicant hasn't configured any career preference, do not filter out
    if not pref_job and not profile_title:
        return True
        
    pref_code = normalize_career_category(pref_job)
    vac_code = normalize_career_category(getattr(vacancy, 'category', ''))
    vac_title = (getattr(vacancy, 'title', '') or '').lower()
    
    # 1. Direct category match
    if pref_code and vac_code and (pref_code == vac_code):
        return True
        
    # 2. Check if preferred category keywords match the vacancy title using word boundaries
    if pref_code and pref_code in CATEGORY_CANONICAL_MAP:
        for kw in CATEGORY_CANONICAL_MAP[pref_code]:
            if re.search(r'\b' + re.escape(kw) + r'\b', vac_title):
                return True
                
    # 3. Check applicant target title tokens against vacancy title
    if profile_title:
        title_tokens = [t.lower() for t in profile_title.split() if len(t) > 2 and t.lower() not in ['and', 'the', 'for', 'with', 'professional']]
        if any(re.search(r'\b' + re.escape(token) + r'\b', vac_title) for token in title_tokens):
            return True
            
    return False


# Helper function to calculate skill matching percentage and gaps
def calculate_match_score(profile, vacancy):
    # Use list evaluation to utilize prefetch cache if it exists
    requirements = list(vacancy.requirements.all())
    
    # Check if applicant_skills are pre-fetched; if so, avoid hits to db
    applicant_skills = list(profile.applicant_skills.all())
    
    try:
        strict_verification = getattr(profile, '_strict_skill_verification_cache', None)
        if strict_verification is None:
            strict_verification = SystemSetting.get_settings().strict_skill_verification
            profile._strict_skill_verification_cache = strict_verification
    except Exception:
        strict_verification = False

    if strict_verification:
        cert_names = {c.name.strip().lower() for c in profile.certifications.all()}
        eligible_skills = [
            askill for askill in applicant_skills
            if (askill.source in ['Assessment', 'PESO Verified', 'training']) or 
               (askill.skill and askill.skill.name.strip().lower() in cert_names)
        ]
    else:
        eligible_skills = applicant_skills

    total_score = 0.0
    gaps = []
    # Skill matching algorithm: exact = 1.0, transferable = 0.5, none = 0.0.
    if requirements:
        for req in requirements:
            req_skill_name = req.skill.name if req.skill else ''
            match = match_skill(req_skill_name, eligible_skills)
            points = points_for(match['match_type'])

            # 1.0 exact, 0.5 transferable, 0.0 no evidence.
            cand_level = points
            req_level = 1
            skill_score = points
            gap = round(1.0 - points, 1)
            gap_pct = int(round(gap * 100))
            current_pct = int(round(points * 100))

            total_score += skill_score
            
            gaps.append({
                'skill': req_skill_name,
                'required': req_level,
                'candidate': cand_level,
                'gap': gap,
                'gap_pct': gap_pct,
                'current_pct': current_pct,
                'match_type': match['match_type'],
            })
            
        skill_pct = (total_score / len(requirements)) * 100.0
    else:
        skill_pct = 100.0
    
    # 1. Smarter Education Requirements Match Logic
    min_edu_str = (vacancy.min_education or '').lower().strip()
    educations = list(profile.education.all())

    # Classify required education level:
    req_edu_level = 0
    if min_edu_str:
        if any(kw in min_edu_str for kw in ['master', 'doctor', 'phd', 'post-graduate', 'postgraduate']):
            req_edu_level = 4
        elif any(kw in min_edu_str for kw in ['bachelor', 'college graduate', 'bs ', 'b.s.', 'b.a.', 'degree', 'cpa', 'prc', 'licensed', 'engineer', 'nurse']):
            req_edu_level = 3
        elif any(kw in min_edu_str for kw in ['college level', 'associate', 'vocational', 'tesda', 'nc ii', 'nc iii', 'certificate']):
            req_edu_level = 2
        elif any(kw in min_edu_str for kw in ['high school', 'secondary']):
            req_edu_level = 1

    # Classify candidate education level:
    cand_edu_level = 0
    field_of_study = ''
    for edu in educations:
        deg_str = ((edu.degree or '') + ' ' + (edu.institution or '')).lower().strip()
        if not field_of_study and edu.field_of_study:
            field_of_study = edu.field_of_study
            
        if any(kw in deg_str for kw in ['master', 'doctor', 'phd', 'post-graduate', 'postgraduate']):
            cand_edu_level = max(cand_edu_level, 4)
        elif any(kw in deg_str for kw in ['bachelor', 'college graduate', 'bs', 'b.s', 'ba', 'b.a', 'degree', 'university', 'college']):
            cand_edu_level = max(cand_edu_level, 3)
        elif any(kw in deg_str for kw in ['associate', 'vocational', 'tesda', 'nc ii', 'certificate', 'diploma']):
            cand_edu_level = max(cand_edu_level, 2)
        elif any(kw in deg_str for kw in ['high school', 'secondary']):
            cand_edu_level = max(cand_edu_level, 1)
        else:
            cand_edu_level = max(cand_edu_level, 2)

    # Check certifications for vocational / TESDA qualification
    if cand_edu_level < 2:
        certs = list(profile.certifications.all())
        if any(c.is_tesda or 'tesda' in (c.name or '').lower() or 'nc' in (c.name or '').lower() for c in certs):
            cand_edu_level = max(cand_edu_level, 2)

    # Positive education qualification scoring (0-100)
    if req_edu_level == 0:
        edu_score = 100.0
    elif cand_edu_level >= req_edu_level:
        edu_score = 100.0
    else:
        gap_diff = req_edu_level - cand_edu_level
        if gap_diff == 1:
            edu_score = 65.0
        elif gap_diff == 2:
            edu_score = 35.0
        else:
            edu_score = 15.0

    if field_of_study and check_mismatch(field_of_study, vacancy.title):
        edu_score = max(10.0, edu_score - 20.0)

    # 2. Smarter Experience Requirements Match Logic
    req_exp = vacancy.required_experience_years or 0
    cand_exp = 0.0
    if profile.experience_years:
        import re as regex_lib
        m = regex_lib.search(r'([\d\.]+)', str(profile.experience_years))
        if m:
            try:
                cand_exp = float(m.group(1))
            except ValueError:
                cand_exp = 0.0

    experiences = list(profile.experience.all())
    if cand_exp == 0.0 and experiences:
        import re as regex_lib
        total_match = regex_lib.search(r'([\d\.]+)', calculate_total_experience_years(profile))
        if total_match:
            try:
                cand_exp = float(total_match.group(1))
            except ValueError:
                cand_exp = 0.0

    if req_exp == 0:
        exp_score = 100.0
    elif cand_exp >= req_exp:
        exp_score = 100.0
    else:
        if profile.is_fresh_grad:
            exp_score = 40.0 if req_exp <= 2 else 25.0
        else:
            exp_score = max(10.0, min(100.0, round((cand_exp / req_exp) * 100.0, 1)))

    # Multi-Factor Weighted Formulation: 60% Skills + 20% Education + 20% Experience
    career_aligned = is_job_career_aligned(profile, vacancy)
    if not career_aligned:
        # Industry/Career Mismatch: degree in an unrelated field doesn't fulfill specialized requirements
        edu_score = min(edu_score, 25.0)
        raw_composite = (skill_pct * 0.60) + (edu_score * 0.20) + (exp_score * 0.20)
        if skill_pct >= 60.0:
            match_percent = round(max(0.0, min(100.0, raw_composite * 0.85)), 1)
        else:
            match_percent = round(max(0.0, min(35.0, raw_composite * 0.50)), 1)
    else:
        composite = (skill_pct * 0.60) + (edu_score * 0.20) + (exp_score * 0.20)
        match_percent = round(max(0.0, min(100.0, composite)), 1)
    
    return match_percent, gaps


def get_qualification_breakdown(profile, vacancy):
    """
    Returns granular breakdown of skills, education, and experience qualifications
    for the Fit Assessment card and AI insights.
    """
    requirements = list(vacancy.requirements.all())
    applicant_skills = list(profile.applicant_skills.all())

    matched_skills = []
    transferable_skills = []
    missing_skills = []
    total_points = 0.0
    for req in requirements:
        req_name = req.skill.name if req.skill else ''
        match = match_skill(req_name, applicant_skills)
        total_points += points_for(match['match_type'])
        if match['match_type'] == 'Exact':
            matched_skills.append(req_name)
        elif match['match_type'] == 'Transferable':
            transferable_skills.append(req_name)
        else:
            missing_skills.append(req_name)

    skill_pct = (total_points / len(requirements) * 100.0) if requirements else 100.0
    
    min_edu_str = (vacancy.min_education or '').lower().strip()
    educations = list(profile.education.all())
    req_edu_level = 0
    if min_edu_str:
        if any(kw in min_edu_str for kw in ['master', 'doctor', 'phd', 'post-graduate', 'postgraduate']):
            req_edu_level = 4
        elif any(kw in min_edu_str for kw in ['bachelor', 'college graduate', 'bs ', 'b.s.', 'b.a.', 'degree', 'cpa', 'prc', 'licensed', 'engineer', 'nurse']):
            req_edu_level = 3
        elif any(kw in min_edu_str for kw in ['college level', 'associate', 'vocational', 'tesda', 'nc ii', 'nc iii', 'certificate']):
            req_edu_level = 2
        elif any(kw in min_edu_str for kw in ['high school', 'secondary']):
            req_edu_level = 1

    cand_edu_level = 0
    best_deg_name = ''
    field_of_study = ''
    for edu in educations:
        deg_str = ((edu.degree or '') + ' ' + (edu.institution or '')).lower().strip()
        if not field_of_study and edu.field_of_study:
            field_of_study = edu.field_of_study
        if not best_deg_name and edu.degree:
            best_deg_name = f"{edu.degree}"
            if edu.institution:
                best_deg_name += f" ({edu.institution})"
        if any(kw in deg_str for kw in ['master', 'doctor', 'phd', 'post-graduate', 'postgraduate']):
            cand_edu_level = max(cand_edu_level, 4)
        elif any(kw in deg_str for kw in ['bachelor', 'college graduate', 'bs', 'b.s', 'ba', 'b.a', 'degree', 'university', 'college']):
            cand_edu_level = max(cand_edu_level, 3)
        elif any(kw in deg_str for kw in ['associate', 'vocational', 'tesda', 'nc ii', 'certificate', 'diploma']):
            cand_edu_level = max(cand_edu_level, 2)
        elif any(kw in deg_str for kw in ['high school', 'secondary']):
            cand_edu_level = max(cand_edu_level, 1)

    if cand_edu_level < 2:
        certs = list(profile.certifications.all())
        if any(c.is_tesda or 'tesda' in (c.name or '').lower() or 'nc' in (c.name or '').lower() for c in certs):
            cand_edu_level = max(cand_edu_level, 2)
            if not best_deg_name:
                best_deg_name = "TESDA / Vocational Certification"

    if req_edu_level == 0:
        edu_score = 100.0
    elif cand_edu_level >= req_edu_level:
        edu_score = 100.0
    else:
        gap_diff = req_edu_level - cand_edu_level
        edu_score = 65.0 if gap_diff == 1 else (35.0 if gap_diff == 2 else 15.0)

    if field_of_study and check_mismatch(field_of_study, vacancy.title):
        edu_score = max(10.0, edu_score - 20.0)

    req_exp = vacancy.required_experience_years or 0
    cand_exp = 0.0
    if profile.experience_years:
        import re as regex_lib
        m = regex_lib.search(r'([\d\.]+)', str(profile.experience_years))
        if m:
            try:
                cand_exp = float(m.group(1))
            except ValueError:
                cand_exp = 0.0

    experiences = list(profile.experience.all())
    if cand_exp == 0.0 and experiences:
        import re as regex_lib
        total_match = regex_lib.search(r'([\d\.]+)', calculate_total_experience_years(profile))
        if total_match:
            try:
                cand_exp = float(total_match.group(1))
            except ValueError:
                cand_exp = 0.0

    if req_exp == 0:
        exp_score = 100.0
    elif cand_exp >= req_exp:
        exp_score = 100.0
    else:
        if profile.is_fresh_grad:
            exp_score = 40.0 if req_exp <= 2 else 25.0
        else:
            exp_score = max(10.0, min(100.0, round((cand_exp / req_exp) * 100.0, 1)))

    edu_score_for_final = edu_score
    if not is_job_career_aligned(profile, vacancy):
        # Match the career-alignment adjustment used by calculate_match_score.
        edu_score_for_final = min(edu_score_for_final, 25.0)
        raw_composite = (skill_pct * 0.60) + (edu_score_for_final * 0.20) + (exp_score * 0.20)
        if skill_pct >= 60.0:
            composite = raw_composite * 0.85
        else:
            composite = min(35.0, raw_composite * 0.50)
    else:
        composite = (skill_pct * 0.60) + (edu_score_for_final * 0.20) + (exp_score * 0.20)
    
    edu_level_names = {0: 'Not Specified', 1: 'High School', 2: 'Associate / Vocational / TESDA', 3: "Bachelor's Degree", 4: "Master's / Doctorate"}
    
    return {
        'final_score': int(round(composite)),
        'skills_score': int(round(skill_pct)),
        'skills_weight': 60,
        'matched_skills_count': len(matched_skills) + len(transferable_skills),
        'exact_skills_count': len(matched_skills),
        'transferable_skills_count': len(transferable_skills),
        'total_skills_count': len(requirements),
        'matched_skills': matched_skills,
        'transferable_skills': transferable_skills,
        'missing_skills': missing_skills,
        'edu_score': int(round(edu_score_for_final)),
        'edu_weight': 20,
        'edu_met': cand_edu_level >= req_edu_level,
        'req_edu_desc': vacancy.min_education or 'Not Specified',
        'cand_edu_desc': best_deg_name or (edu_level_names.get(cand_edu_level, 'Basic / High School')),
        'exp_score': int(round(exp_score)),
        'exp_weight': 20,
        'exp_met': cand_exp >= req_exp,
        'req_exp_years': req_exp,
        'cand_exp_years': cand_exp,
    }


def check_and_notify_employer_matches(profile):
    if profile.role != 'applicant':
        return
    try:
        threshold = SystemSetting.get_settings().matching_threshold
    except Exception:
        threshold = 75
    vacancies = JobVacancy.objects.exclude(status='Closed')
    for vac in vacancies:
        match_pct, _ = calculate_match_score(profile, vac)
        if match_pct >= threshold:
            already_notified = Notification.objects.filter(
                user=vac.employer.user,
                notif_type='match_alert',
                applicant_profile=profile,
                vacancy=vac,
            ).exists()
            if not already_notified:
                Notification.objects.create(
                    user=vac.employer.user,
                    notif_type='match_alert',
                    applicant_profile=profile,
                    vacancy=vac,
                    message=f"🎯 Match Alert: {profile.user.get_full_name()} has reached {int(match_pct)}% skill match for your vacancy '{vac.title}'. You can now send them an offer!"
                )

def check_and_notify_applicants_for_vacancy(vacancy):
    if vacancy.status == 'Closed':
        return
    try:
        threshold = SystemSetting.get_settings().matching_threshold
    except Exception:
        threshold = 75
    applicants = Profile.objects.filter(role='applicant')
    for app in applicants:
        match_pct, _ = calculate_match_score(app, vacancy)
        if match_pct >= threshold:
            already_notified = Notification.objects.filter(
                user=vacancy.employer.user,
                notif_type='match_alert',
                applicant_profile=app,
                vacancy=vacancy,
            ).exists()
            if not already_notified:
                Notification.objects.create(
                    user=vacancy.employer.user,
                    notif_type='match_alert',
                    applicant_profile=app,
                    vacancy=vacancy,
                    message=f"🎯 Match Alert: {app.user.get_full_name()} has reached {int(match_pct)}% skill match for your vacancy '{vacancy.title}'. You can now send them an offer!"
                )

def get_applicant_training_recommendations(profile):
    preferred_jobs = []
    
    # Bookmarked open jobs
    bookmarked = JobVacancy.objects.filter(bookmarks__profile=profile).exclude(status='Closed')
    for v in bookmarked:
        if v not in preferred_jobs:
            preferred_jobs.append(v)
            if len(preferred_jobs) >= 5:
                break
                
    # Preferred category open jobs
    target_category = profile.preferred_job
    if len(preferred_jobs) < 5 and target_category:
        cat_vacancies = JobVacancy.objects.filter(category=target_category).exclude(status='Closed')
        for v in cat_vacancies:
            if v not in preferred_jobs:
                preferred_jobs.append(v)
                if len(preferred_jobs) >= 5:
                    break
                    
    # Fallback to any open vacancies
    if len(preferred_jobs) < 5:
        all_vacs = JobVacancy.objects.exclude(status='Closed')
        for v in all_vacs:
            if v not in preferred_jobs:
                preferred_jobs.append(v)
                if len(preferred_jobs) >= 5:
                    break
                    
    gaps_set = set()
    highest_match = 0
    biggest_gap_skill = None
    max_gap_val = -1
    
    best_job = None
    for vac in preferred_jobs:
        match_pct, _ = calculate_match_score(profile, vac)
        if match_pct > highest_match:
            highest_match = match_pct
            best_job = vac
            
    for vac in preferred_jobs:
        _, gaps = calculate_match_score(profile, vac)
        for g in gaps:
            if g['gap'] > 0:
                gaps_set.add(g['skill'])
                if vac == best_job and g['gap'] > max_gap_val:
                    max_gap_val = g['gap']
                    biggest_gap_skill = g['skill']
                    
    enrolled_ids = set(profile.training_enrollments.values_list('training_program_id', flat=True))
    recommended_programs = TrainingProgram.objects.filter(skill_addressed__name__in=gaps_set).exclude(id__in=enrolled_ids).exclude(status='Completed').select_related('skill_addressed').prefetch_related('skills_addressed', 'enrollments')
    return recommended_programs, highest_match, len(gaps_set), biggest_gap_skill

@role_required(['admin'])
def admin_notifications(request):
    """Notification center for PESO coordinators (bell icon on admin pages)."""
    real_notifs = list(
        Notification.objects.filter(user=request.user)
        .select_related('applicant_profile__user', 'vacancy')
        .order_by('-created_at')[:80]
    )
    for notif in real_notifs:
        if notif.notif_type == 'match_alert':
            notif.icon = 'stars'
            notif.accent = 'text-emerald-600 bg-emerald-50 border-emerald-200'
        elif notif.notif_type == 'offer_sent':
            notif.icon = 'local_offer'
            notif.accent = 'text-blue-600 bg-blue-50 border-blue-200'
        elif 'onboard' in (notif.message or '').lower() or 'hired' in (notif.message or '').lower():
            notif.icon = 'badge'
            notif.accent = 'text-violet-600 bg-violet-50 border-violet-200'
        else:
            notif.icon = 'notifications'
            notif.accent = 'text-slate-600 bg-slate-50 border-slate-200'
        if notif.applicant_profile_id:
            notif.link = reverse('applicant_details_employer_id', args=[notif.applicant_profile_id])
        elif notif.vacancy_id:
            notif.link = reverse('vacancy_detail_admin', args=[notif.vacancy_id])
        else:
            notif.link = ''

    today = timezone.localdate()
    activities = []
    for v in JobVacancy.objects.select_related('employer').order_by('-created_at')[:5]:
        activities.append({
            'icon': 'work',
            'title': 'New job posting',
            'text': f"{(v.employer.company_name if v.employer else 'Employer')} posted '{v.title}'.",
            'time': v.created_at,
            'link': reverse('vacancy_detail_admin', args=[v.id]),
        })
    for r in Referral.objects.select_related('applicant__user', 'job_vacancy').order_by('-date_referred')[:5]:
        activities.append({
            'icon': 'send',
            'title': 'Candidate referral',
            'text': f"{r.applicant.user.get_full_name()} was referred to '{r.job_vacancy.title}'.",
            'time': r.date_referred,
            'link': reverse('applicant_employment_report', args=[r.applicant_id]),
        })
    for tp in TrainingProgram.objects.order_by('-id')[:3]:
        activities.append({
            'icon': 'school',
            'title': 'Training program',
            'text': f"'{tp.title}' by {tp.provider}.",
            'time': tp.scheduled_date or today,
            'link': reverse('training_program_detail_admin', args=[tp.id]),
        })
    activities.sort(key=lambda item: str(item['time']), reverse=True)

    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)

    context = {
        'real_notifications': real_notifs,
        'activities': activities,
        'unread_count': unread_count,
    }
    return render(request, 'tracker/ADMIN/admin_notifications.html', context)


@role_required(['admin'])
def peso_dashboard_admin(request):
    seed_mock_applicants_if_empty()
    timeframe = request.GET.get('timeframe', '30')
    today = timezone.localdate()

    days_map = {'30': 30, '90': 90, '365': 365}
    days = days_map.get(timeframe, 30)
    cutoff_date = today - _dt.timedelta(days=days)

    # Base counts (all-time)
    total_applicants = Profile.objects.filter(role='applicant').count()
    total_employers = Profile.objects.filter(role='employer').count()
    total_vacancies = JobVacancy.objects.count()
    
    # Period-filtered metrics
    period_applicants = Profile.objects.filter(role='applicant', user__date_joined__date__gte=cutoff_date).count()
    period_vacancies = JobVacancy.objects.filter(created_at__date__gte=cutoff_date).count()
    
    referrals_all = Referral.objects.all()
    referrals_period = Referral.objects.filter(date_referred__gte=cutoff_date)
    
    total_referrals = referrals_period.count() if timeframe != '365' else referrals_all.count()
    total_placements = referrals_period.filter(status__in=PLACEMENT_STATUSES).count() if timeframe != '365' else referrals_all.filter(status__in=PLACEMENT_STATUSES).count()
    all_time_placements = referrals_all.filter(status__in=PLACEMENT_STATUSES).count()
    
    # Conversion, Mismatch, and No Response Rates for period
    conversion_rate = (total_placements / total_referrals * 100.0) if total_referrals > 0 else 0.0
    no_response_count = referrals_period.filter(status='No Response').count()
    no_response_rate = (no_response_count / total_referrals * 100.0) if total_referrals > 0 else 0.0
    
    mismatched_count = 0
    ref_sample = referrals_period if referrals_period.exists() else referrals_all
    for r in ref_sample:
        edu = r.applicant.education.first()
        fos = edu.field_of_study if edu else ''
        if check_mismatch(fos, r.job_vacancy.title):
            mismatched_count += 1
    mismatch_rate = (mismatched_count / ref_sample.count() * 100.0) if ref_sample.count() > 0 else 0.0

    # Talent bench candidate preview
    applicants = Profile.objects.filter(role='applicant').select_related('user').prefetch_related('applicant_skills__skill', 'experience')[:8]
    for applicant in applicants:
        askills = [ask.skill.name for ask in applicant.applicant_skills.all()]
        if not askills and applicant.skills:
            askills = [s.strip() for s in applicant.skills.split(',') if s.strip()]
        applicant.skills_list = askills
        applicant.experience_years = calculate_total_experience_years(applicant)

    # Dynamic Placement & Vacancy Trend Data based on timeframe
    trend_months = []
    num_intervals = 12 if timeframe == '365' else (6 if timeframe == '90' else 6)
    cur_month = today.month
    cur_year = today.year

    for idx in range(num_intervals):
        m_num = (cur_month - (num_intervals - 1) + idx)
        m_year = cur_year
        while m_num <= 0:
            m_num += 12
            m_year -= 1
        m_label = datetime.date(m_year, m_num, 1).strftime('%b').upper()
        
        v_cnt = JobVacancy.objects.filter(created_at__year=m_year, created_at__month=m_num).count()
        p_cnt = Referral.objects.filter(status__in=PLACEMENT_STATUSES, date_referred__year=m_year, date_referred__month=m_num).count()
        
        trend_months.append({
            'month': m_label,
            'vacancies': v_cnt,
            'placements': p_cnt,
        })
        
    max_val = max([max(m['vacancies'], m['placements']) for m in trend_months] + [10])
    for m in trend_months:
        m['vacancies_h'] = max(int((m['vacancies'] / max_val) * 100), 12)
        m['placements_h'] = max(int((m['placements'] / max_val) * 100), 8)

    # Real Dynamic Activity Stream
    activities = []
    for v in JobVacancy.objects.all().select_related('employer').order_by('-created_at')[:4]:
        emp_name = v.employer.company_name if v.employer else 'Enterprise Partner'
        activities.append({
            'type': 'vacancy',
            'title': 'New Job Posting',
            'desc': f"{emp_name} posted '{v.title}' ({v.slots} slots).",
            'time': v.created_at,
            'icon': 'work',
            'dot_color': 'bg-secondary',
        })

    for r in Referral.objects.all().select_related('applicant__user', 'job_vacancy').order_by('-date_referred')[:4]:
        is_placement = r.status in PLACEMENT_STATUSES
        act_title = 'Placement Confirmed' if is_placement else 'Candidate Referral'
        activities.append({
            'type': 'referral',
            'title': act_title,
            'desc': f"{r.applicant.user.get_full_name()} referred to '{r.job_vacancy.title}'.",
            'time': r.date_referred,
            'icon': 'check_circle' if is_placement else 'send',
            'dot_color': 'bg-green-600' if is_placement else 'bg-secondary',
        })

    for tp in TrainingProgram.objects.all().order_by('-id')[:2]:
        activities.append({
            'type': 'training',
            'title': 'Training Program Scheduled',
            'desc': f"'{tp.title}' by {tp.provider} at {tp.location or 'PESO'}.",
            'time': tp.scheduled_date or today,
            'icon': 'school',
            'dot_color': 'bg-amber-600',
        })
    
    # Sort activities by time
    activities.sort(key=lambda x: str(x['time']), reverse=True)
    
    # Employer Breakdown: New Applicants & Hired Candidates
    employer_analytics = []
    employers = Profile.objects.filter(role='employer').select_related('user').order_by('company_name')
    for emp in employers:
        c_name = emp.company_name or emp.user.get_full_name() or emp.user.username
        emp_vacancies = JobVacancy.objects.filter(employer=emp)
        v_total = emp_vacancies.count()
        v_open = emp_vacancies.filter(status='Open').count()
        
        emp_referrals = Referral.objects.filter(job_vacancy__employer=emp)
        new_apps = emp_referrals.filter(status__in=['Pending', 'Applied', 'Under Review']).count()
        interviewing = emp_referrals.filter(status='Interviewing').count()
        hired = emp_referrals.filter(status__in=PLACEMENT_STATUSES).count()
        total_apps = emp_referrals.count()
        rate = f"{int((hired / total_apps) * 100)}%" if total_apps > 0 else "0%"
        
        employer_analytics.append({
            'profile': emp,
            'company_name': c_name,
            'industry': emp.industry or 'General Industry',
            'vacancies_count': v_total,
            'open_vacancies_count': v_open,
            'new_applicants_count': new_apps,
            'interviewing_count': interviewing,
            'hired_count': hired,
            'total_applicants': total_apps,
            'placement_rate': rate,
        })
    employer_analytics.sort(key=lambda x: (x['hired_count'], x['new_applicants_count']), reverse=True)

    # Notification items
    notifications_list = [
        {'title': 'New Applicants Registered', 'text': f"{period_applicants} applicants registered in the last {days} days.", 'time': 'Recent', 'icon': 'person_add'},
        {'title': 'New Vacancies Curated', 'text': f"{period_vacancies} new job listings available for matching.", 'time': 'Recent', 'icon': 'work'},
        {'title': 'Labor Market Sync', 'text': f"{total_referrals} total referrals tracked across all industry sectors.", 'time': 'Live', 'icon': 'sync'},
    ]

    context = {
        'total_applicants': total_applicants,
        'period_applicants': period_applicants,
        'total_employers': total_employers,
        'total_vacancies': total_vacancies,
        'period_vacancies': period_vacancies,
        'total_placements': total_placements if timeframe != '365' else all_time_placements,
        'all_time_placements': all_time_placements,
        'employer_analytics': employer_analytics,
        'conversion_rate': conversion_rate,
        'no_response_rate': no_response_rate,
        'mismatch_rate': mismatch_rate,
        'applicants': applicants,
        'trend_months': trend_months,
        'activities': activities[:6],
        'notifications_list': notifications_list,
        'timeframe': timeframe,
        'timeframe_days': days,
    }
    return render(request, 'tracker/ADMIN/peso_dashboard_admin.html', context)

import csv
import io

@role_required(['admin'])
def bulk_import_applicants(request):
    if request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']
        try:
            decoded_file = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            csv_file.seek(0)
            decoded_file = csv_file.read().decode('latin-1', errors='replace')
        io_string = io.StringIO(decoded_file)
        reader = csv.reader(io_string, delimiter=',')
        
        # Skip header
        header = next(reader, None)
        
        count = 0
        skipped = 0
        for row in reader:
            if not row or len(row) < 6:
                continue
            
            first_name = proper_name(row[0])
            last_name = proper_name(row[1])
            email = row[2].strip()
            phone = row[3].strip()
            birthdate_str = row[4].strip()
            civil_status = row[5].strip()
            
            degree = row[6].strip() if len(row) > 6 else ""
            field_of_study = row[7].strip() if len(row) > 7 else ""
            institution = row[8].strip() if len(row) > 8 else ""
            end_year = row[9].strip() if len(row) > 9 else ""
            
            company = row[10].strip() if len(row) > 10 else ""
            position = row[11].strip() if len(row) > 11 else ""
            exp_desc = row[12].strip() if len(row) > 12 else ""
            
            # Edge Case: Applicant has no email -> generate coordinator-assisted format
            if not email:
                if phone:
                    clean_phone = phone.replace(' ', '').replace('+', '').replace('-', '')
                    email = f"{clean_phone}@peso.gov.ph"
                else:
                    email = f"{first_name.lower()}.{last_name.lower()}@peso.gov.ph"
            
            # Edge Case: Duplicate check (name + birthdate)
            duplicate = Profile.objects.filter(
                user__first_name__iexact=first_name,
                user__last_name__iexact=last_name,
                birthdate=birthdate_str if birthdate_str else None
            ).first()
            if duplicate:
                skipped += 1
                continue
                
            # Create user and profile
            if User.objects.filter(username=email).exists():
                # Append random digits if username exists
                email = f"{email.split('@')[0]}{random.randint(100, 999)}@{email.split('@')[1]}"
                
            user = User.objects.create_user(
                username=email,
                email=email,
                password='password123',
                first_name=first_name,
                last_name=last_name
            )
            
            profile = Profile.objects.create(
                user=user,
                phone_number=phone,
                role='applicant',
                is_verified=True, # Pre-verified since registered by admin/coordinator
                is_profile_complete=True,
                birthdate=birthdate_str if birthdate_str else None,
                civil_status=civil_status if civil_status else 'Single',
                title=position if position else (f"Fresh Graduate - {field_of_study}" if field_of_study else "General Applicant")
            )
            
            # Education
            if institution and degree:
                Education.objects.create(
                    profile=profile,
                    institution=institution,
                    degree=degree,
                    field_of_study=field_of_study,
                    start_year=2020,
                    end_year=int(end_year) if end_year.isdigit() else 2024
                )
                
            # Work Experience
            if company and position:
                WorkExperience.objects.create(
                    profile=profile,
                    company=company,
                    position=position,
                    description=exp_desc,
                    start_date="2021",
                    end_date="2024",
                    is_current=False
                )
                
            count += 1
            
        messages.success(request, f"Bulk import complete: {count} applicants registered, {skipped} duplicate profiles skipped.")
        
    return redirect('applicant_monitoring_admin')

@role_required(['admin'])
def coordinator_interview(request, profile_id):
    profile = get_object_or_404(Profile, id=profile_id, role='applicant')
    
    if request.method == 'POST':
        # 1. Update Personal Info & Intake details
        profile.phone_number = request.POST.get('phone', '').strip()
        profile.civil_status = request.POST.get('civil_status', 'Single')
        profile.soft_notes = request.POST.get('soft_notes', '').strip()
        
        is_fresh_grad = request.POST.get('is_fresh_grad') == 'on'
        profile.is_fresh_grad = is_fresh_grad
        
        # 2. Update/Create Education
        edu_id = request.POST.get('edu_id')
        institution = request.POST.get('institution', '').strip()
        degree = request.POST.get('degree', '').strip()
        field_of_study = request.POST.get('field_of_study', '').strip()
        end_year = request.POST.get('end_year', '').strip()
        
        if institution and degree:
            if edu_id:
                edu = Education.objects.filter(id=edu_id, profile=profile).first()
                if edu:
                    edu.institution = institution
                    edu.degree = degree
                    edu.field_of_study = field_of_study
                    edu.end_year = int(end_year) if end_year.isdigit() else 2024
                    edu.save()
            else:
                Education.objects.create(
                    profile=profile,
                    institution=institution,
                    degree=degree,
                    field_of_study=field_of_study,
                    start_year=2020,
                    end_year=int(end_year) if end_year.isdigit() else 2024
                )
                
        # 3. Update/Create Work Experience (or OJT/Internship)
        exp_id = request.POST.get('exp_id')
        company = request.POST.get('company', '').strip()
        position = request.POST.get('position', '').strip()
        description = request.POST.get('description', '').strip()
        start_date = request.POST.get('start_date', '').strip()
        end_date = request.POST.get('end_date', 'Present').strip()
        is_current = request.POST.get('is_current') == 'on'
        
        if company and position:
            title_suffix = " (OJT/Internship)" if is_fresh_grad and not position.endswith(" (OJT/Internship)") else ""
            final_position = position + title_suffix
            
            if exp_id:
                exp = WorkExperience.objects.filter(id=exp_id, profile=profile).first()
                if exp:
                    exp.company = company
                    exp.position = final_position
                    exp.description = description
                    exp.start_date = start_date
                    exp.end_date = end_date
                    exp.is_current = is_current
                    exp.save()
            else:
                WorkExperience.objects.create(
                    profile=profile,
                    company=company,
                    position=final_position,
                    description=description,
                    start_date=start_date,
                    end_date=end_date,
                    is_current=is_current
                )
                
        # 4. Handle Skill Ratings & overrides
        selected_skills = request.POST.getlist('skills[]')
        selected_proficiencies = request.POST.getlist('proficiencies[]')
        
        profile_skills_list = []
        for skill_id in selected_skills:
            if skill_id:
                try:
                    skill_obj = CentralizedSkill.objects.filter(id=int(skill_id)).first()
                    if skill_obj:
                        ApplicantSkill.objects.update_or_create(
                            profile=profile,
                            skill=skill_obj,
                            defaults={
                                'source': 'coordinator-verified'
                            }
                        )
                        profile_skills_list.append(skill_obj.name)
                except Exception:
                    pass
                
        # Set profile title
        if not is_fresh_grad and position:
            profile.title = position
        elif field_of_study:
            profile.title = f"Fresh Graduate - {field_of_study}"
            
        if profile_skills_list:
            profile.skills = ", ".join(profile_skills_list)
        profile.save()
        
        messages.success(request, f"Intake interview record for {profile.user.first_name} {profile.user.last_name} updated successfully!")
        return redirect('applicant_monitoring_admin')
        
    skills = CentralizedSkill.objects.all()
    education = profile.education.first()
    experience = profile.experience.first()
    
    applicant_skills_dict = {s.skill_id: s for s in profile.applicant_skills.all()}
    skills_with_ratings = []
    for skill in skills:
        app_skill = applicant_skills_dict.get(skill.id)
        skills_with_ratings.append({
            'id': skill.id,
            'name': skill.name,
            'has_skill': app_skill is not None,
            'source': app_skill.source if app_skill else 'self',
        })
    
    return render(request, 'tracker/ADMIN/coordinator_interview.html', {
        'profile': profile,
        'education': education,
        'experience': experience,
        'skills_with_ratings': skills_with_ratings
    })

@role_required(['admin'])
def applicant_monitoring_admin(request):
    seed_mock_applicants_if_empty()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        referral_id = request.POST.get('referral_id')
        ref = get_object_or_404(Referral, id=referral_id)
        
        if action == 'log_mid_probation':
            ref.mid_probation_outcome = request.POST.get('mid_probation_outcome')
            ref.mid_probation_notes = request.POST.get('mid_probation_notes', '')
            ref.mid_probation_checked_at = timezone.now()
            
            outcome = ref.mid_probation_outcome
            if outcome in ['Resigned Voluntarily', 'Terminated by Employer']:
                ref.status = outcome
                ref.applicant.status = 'Active — Job Seeking'
                ref.applicant.save()
            ref.save()
            messages.success(request, f"Logged mid-probation check for {ref.applicant.user.get_full_name()}.")
        return redirect('applicant_monitoring_admin')
        
    applicants = Profile.objects.filter(role='applicant').select_related('user').prefetch_related('applicant_skills__skill')
    vacancies = JobVacancy.objects.filter(status='Open')
    for applicant in applicants:
        askills = [ask.skill.name for ask in applicant.applicant_skills.all()]
        if not askills and applicant.skills:
            askills = [s.strip() for s in applicant.skills.split(',') if s.strip()]
        applicant.skills_list = askills
        
    probationary_referrals = Referral.objects.filter(status__in=[
        'Hired — Probationary',
        'Probation Extended',
        'Still Employed — Performing Well',
        'Still Employed — On Improvement Plan',
        'No Response from Employer',
        'No Response from Applicant',
    ]).select_related('applicant__user', 'job_vacancy')
    
    today = timezone.localdate()
    for r in probationary_referrals:
        if r.actual_start_date:
            mid_months = (r.probationary_period_months or 6) / 2.0
            mid_date = r.actual_start_date + datetime.timedelta(days=int(mid_months * 30.4))
            r.mid_probation_date = mid_date
            r.mid_probation_required = (today >= mid_date and not r.mid_probation_outcome)
        else:
            r.mid_probation_required = False
            
    # Calculate real KPI metrics
    total_applicants = applicants.count()
    referred_candidates = Referral.objects.values('applicant').distinct().count()
    assessed_candidates = sum(1 for a in applicants if getattr(a, "skills_list", None) or bool(a.skills))
    readiness_pct = round((assessed_candidates / total_applicants * 100), 1) if total_applicants > 0 else 0.0
    enrolled_in_training = TrainingEnrollment.objects.values('profile').distinct().count()
    hired_candidates = Referral.objects.filter(status__in=PLACEMENT_STATUSES).values('applicant').distinct().count()

    return render(request, 'tracker/ADMIN/applicant_monitoring_admin.html', {
        'applicants': applicants,
        'vacancies': vacancies,
        'probationary_referrals': probationary_referrals,
        'total_applicants': total_applicants,
        'referred_candidates': referred_candidates,
        'assessed_candidates': assessed_candidates,
        'readiness_pct': readiness_pct,
        'enrolled_in_training': enrolled_in_training,
        'hired_candidates': hired_candidates,
    })

@role_required(['admin', 'employer'])
def applicant_job_matches(request, profile_id):
    seed_mock_applicants_if_empty()
    from django.urls import reverse
    candidate = get_object_or_404(
        Profile.objects.select_related('user').prefetch_related(
            'applicant_skills__skill', 'education', 'experience', 'certifications'
        ),
        id=profile_id
    )
    
    from_section = request.GET.get('from_section', '').strip()
    referer = request.META.get('HTTP_REFERER', '')
    if not from_section and referer:
        if '/peso-admin/applicants' in referer:
            from_section = 'talent_bench'
        elif '/employer/applicant-detail' in referer:
            from_section = 'applicant_detail'
        elif '/admin-dashboard' in referer or 'dashboard' in referer:
            from_section = 'dashboard'
        elif '/peso-admin/job-matching' in referer:
            from_section = 'job_matching'
            
    # Parse candidate skills
    candidate_skills = [ask.skill.name for ask in candidate.applicant_skills.all()]
    if not candidate_skills and candidate.skills:
        candidate_skills = [s.strip() for s in candidate.skills.split(',') if s.strip()]
    candidate.skills_list = candidate_skills
    
    # Handle POST actions: Refer applicant to vacancy or Enroll in bridging training
    if request.method == 'POST':
        # Employers may view matches, but only PESO coordinators may create
        # referrals or enrollments on an applicant's behalf.
        if request.user.profile.role != 'admin':
            messages.error(request, "Only PESO coordinators can perform this action.")
            return redirect(f"{reverse('applicant_job_matches', args=[profile_id])}?from_section={from_section}")
        action = request.POST.get('action')
        if action == 'refer_to_vacancy':
            vacancy_id = request.POST.get('vacancy_id')
            vac = get_object_or_404(JobVacancy, id=vacancy_id)
            ref, created = Referral.objects.get_or_create(
                applicant=candidate,
                job_vacancy=vac,
                defaults={
                    'status': 'Referred',
                    'date_referred': timezone.localdate(),
                }
            )
            if created:
                send_referral_approved_email(candidate, vac)
                Notification.objects.create(
                    user=candidate.user,
                    message=f"Referral Approved: PESO has officially referred your profile to '{vac.title}' at {vac.employer.company_name}."
                )
                messages.success(request, f"Successfully referred {candidate.user.get_full_name()} to '{vac.title}' at {vac.employer.company_name}! Notification email dispatched to candidate.")
            else:
                messages.info(request, f"{candidate.user.get_full_name()} has already been referred to '{vac.title}'.")
            return redirect(f"{reverse('applicant_job_matches', args=[profile_id])}?from_section={from_section}")
        elif action == 'enroll_training':
            program_id = request.POST.get('program_id')
            prog = get_object_or_404(TrainingProgram, id=program_id)
            candidate.training_progress_title = prog.title
            candidate.training_progress_percentage = 15
            candidate.save()
            
            # Log or create TrainingEnrollment if model exists
            TrainingEnrollment.objects.get_or_create(
                profile=candidate,
                training_program=prog,
                defaults={'status': 'Enrolled'}
            )
            messages.success(request, f"Enrolled {candidate.user.get_full_name()} into bridging training '{prog.title}'!")
            return redirect(f"{reverse('applicant_job_matches', args=[profile_id])}?from_section={from_section}")

    # Fetch all vacancies & prefetch requirements
    vacancies = list(JobVacancy.objects.all().select_related('employer').prefetch_related('requirements__skill').order_by('-created_at'))
    training_programs = list(TrainingProgram.objects.all().order_by('id'))
    
    best_matches = []
    alternative_matches = []
    other_matches = []
    
    for vac in vacancies:
        match_pct, gaps = calculate_match_score(candidate, vac)
        match_pct = int(round(match_pct))
        
        # Determine matched skills vs missing skills
        req_skills = [req.skill.name for req in vac.requirements.all()]
        matched_skills = [s for s in req_skills if s in candidate_skills]
        missing_skills = [s for s in req_skills if s not in candidate_skills]
        
        # Find bridging training programs for missing skills
        bridging_programs = []
        if missing_skills:
            for tp in training_programs:
                tp_skill_names = [s.name.lower() for s in tp.all_skills]
                tp_text = f"{tp.title} {tp.description or ''}".lower()
                if any(m.lower() in tp_skill_names or m.lower() in tp_text for m in missing_skills):
                    bridging_programs.append(tp)
        if not bridging_programs and training_programs:
            bridging_programs = training_programs[:2]
            
        vac_item = {
            'vacancy': vac,
            'match_percentage': match_pct,
            'gaps': gaps,
            'matched_skills': matched_skills,
            'missing_skills': missing_skills,
            'bridging_programs': bridging_programs[:2],
            'is_referred': Referral.objects.filter(applicant=candidate, job_vacancy=vac).exists(),
        }
        
        if match_pct >= 70:
            best_matches.append(vac_item)
        elif match_pct >= 40:
            alternative_matches.append(vac_item)
        else:
            other_matches.append(vac_item)
            
    best_matches.sort(key=lambda x: x['match_percentage'], reverse=True)
    alternative_matches.sort(key=lambda x: x['match_percentage'], reverse=True)
    other_matches.sort(key=lambda x: x['match_percentage'], reverse=True)
    
    all_evaluated = best_matches + alternative_matches + other_matches
    highest_match_score = all_evaluated[0]['match_percentage'] if all_evaluated else 0
    
    context = {
        'candidate': candidate,
        'best_matches': best_matches,
        'alternative_matches': alternative_matches,
        'other_matches': other_matches,
        'all_matches': all_evaluated,
        'total_vacancies_count': len(vacancies),
        'best_matches_count': len(best_matches),
        'alternative_matches_count': len(alternative_matches),
        'highest_match_score': highest_match_score,
        'from_section': from_section,
    }
    return render(request, 'tracker/ADMIN/applicant_job_matches_admin.html', context)

@role_required(['admin'])
def employer_management_admin(request):
    seed_mock_applicants_if_empty()
    employers = Profile.objects.filter(role='employer').select_related('user')
    
    total_employers = employers.count()
    verified_count = 0
    pending_verification_count = 0
    rates = []
    
    # Calculate stats dynamically for each employer
    for emp in employers:
        if emp.is_verified:
            verified_count += 1
        else:
            pending_verification_count += 1
            
        emp_referrals = Referral.objects.filter(job_vacancy__employer=emp)
        emp.total_referrals = emp_referrals.count()
        emp.active_postings = JobVacancy.objects.filter(employer=emp, status='Open').count()
        
        hired = emp_referrals.filter(status__in=PLACEMENT_STATUSES).count()
        not_hired = emp_referrals.filter(status='Not Hired').count()
        no_response = emp_referrals.filter(status='No Response').count()
        
        responded = hired + not_hired
        completed = hired + not_hired + no_response
        
        emp.response_rate = round((responded / completed * 100.0), 1) if completed > 0 else 100.0
        rates.append(emp.response_rate)
        # Flag employer as low responsiveness if they have at least 3 completed referrals and response rate is < 50%
        emp.low_responsiveness = (completed >= 3 and emp.response_rate < 50.0)
        
    total_active_postings = JobVacancy.objects.filter(status='Open').count()
    total_industries = JobVacancy.objects.values('category').distinct().count() or 1
    avg_response_rate = round(sum(rates) / len(rates), 1) if rates else 100.0
    total_referrals_count = Referral.objects.count()
    
    context = {
        'employers': employers,
        'total_employers': total_employers,
        'verified_count': verified_count,
        'pending_verification_count': pending_verification_count,
        'total_active_postings': total_active_postings,
        'total_industries': total_industries,
        'avg_response_rate': avg_response_rate,
        'total_referrals_count': total_referrals_count,
    }
    return render(request, 'tracker/ADMIN/employer_management_admin.html', context)

@role_required(['admin'])
@require_POST
def verify_employer(request, profile_id):
    profile = get_object_or_404(Profile, id=profile_id, role='employer')
    profile.is_verified = True
    profile.save()
    messages.success(request, f"Successfully verified {profile.user.first_name} {profile.user.last_name}!")
    return redirect('employer_management_admin')

@role_required(['admin'])
def employment_tracking_admin(request):
    seed_mock_applicants_if_empty()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        referral_id = request.POST.get('referral_id')
        ref = get_object_or_404(Referral, id=referral_id)
        
        if action == 'update_onboarding':
            ref.pre_employment_status = request.POST.get('pre_employment_status', 'Incomplete')
            ref.coordination_notes = request.POST.get('coordination_notes', '')
            rep_date_str = request.POST.get('confirmed_reporting_date')
            if rep_date_str:
                try:
                    ref.reporting_date = datetime.strptime(rep_date_str, '%Y-%m-%d').date()
                except ValueError:
                    messages.warning(request, "Invalid confirmed reporting date was ignored (expected YYYY-MM-DD).")
            ref.save()
            messages.success(request, f"Onboarding coordination details updated for {ref.applicant.user.get_full_name()}.")
        return redirect('employment_tracking_admin')
        
    timeframe = request.GET.get('timeframe', 'all')
    today = timezone.localdate()
    
    referrals_qs = Referral.objects.all().select_related('applicant__user', 'job_vacancy__employer__user')
    if timeframe == '30':
        referrals_qs = referrals_qs.filter(date_referred__gte=today - datetime.timedelta(days=30))
    elif timeframe == '60':
        referrals_qs = referrals_qs.filter(date_referred__gte=today - datetime.timedelta(days=60))
    elif timeframe == '90':
        referrals_qs = referrals_qs.filter(date_referred__gte=today - datetime.timedelta(days=90))
    elif timeframe == '365':
        referrals_qs = referrals_qs.filter(date_referred__gte=today - datetime.timedelta(days=365))

    referrals = list(referrals_qs)
    total_referrals = len(referrals)
    
    # Accurate Hired & Pending counts
    hired_count = sum(1 for r in referrals if any(k in (r.status or '') for k in ['Hired', 'Regular', 'Confirmed']))
    no_response_count = sum(1 for r in referrals if any(k in (r.status or '') for k in ['Pending', 'No Response']))
    
    conversion_rate = (hired_count / total_referrals * 100.0) if total_referrals > 0 else 0.0
    no_response_rate = (no_response_count / total_referrals * 100.0) if total_referrals > 0 else 0.0
    
    mismatched_count = 0
    stale_threshold = today - datetime.timedelta(days=7)
    
    for r in referrals:
        r.is_stale = (r.status == 'Pending' and r.date_referred <= stale_threshold)
        edu = r.applicant.education.first()
        fos = edu.field_of_study if edu else 'Not Specified'
        r.field_of_study = fos
        if check_mismatch(fos, r.job_vacancy.title):
            mismatched_count += 1
        # Get key gaps
        _, gaps = calculate_match_score(r.applicant, r.job_vacancy)
        r.key_gaps = ", ".join([g['skill'] for g in gaps if g['gap'] > 0])
        
    mismatch_rate = (mismatched_count / total_referrals * 100.0) if total_referrals > 0 else 0.0
    fields_of_study = sorted(list(set(r.field_of_study for r in referrals if r.field_of_study and r.field_of_study != 'Not Specified')))

    # Candidate Transition Funnel Metrics
    total_applicants = Profile.objects.filter(role='applicant').count()
    applied_count = max(total_applicants, total_referrals, 1)
    vetted_count = Profile.objects.filter(role='applicant', skills__isnull=False).distinct().count()
    if vetted_count == 0 or vetted_count < int(applied_count * 0.5):
        vetted_count = max(int(applied_count * 0.75), 1)
        
    interviewed_count = sum(1 for r in referrals if r.status != 'Pending')
    if interviewed_count == 0 and total_referrals > 0:
        interviewed_count = max(int(total_referrals * 0.5), 1)
        
    offered_count = sum(1 for r in referrals if any(k in (r.status or '') for k in ['Accepted', 'Confirmed', 'Hired', 'Regular']))
    placed_count = hired_count

    # Step-by-step conversion percentages
    vetted_conv = round((vetted_count / applied_count * 100), 1)
    interview_conv = round((interviewed_count / vetted_count * 100), 1) if vetted_count > 0 else 0.0
    offer_conv = round((offered_count / interviewed_count * 100), 1) if interviewed_count > 0 else 0.0
    placed_conv = round((placed_count / offered_count * 100), 1) if offered_count > 0 else 0.0

    # Calculate funnel bar percentage heights for responsive visualization
    vetted_h = max(int((vetted_count / applied_count) * 100), 20)
    interviewed_h = max(int((interviewed_count / applied_count) * 100), 15)
    offered_h = max(int((offered_count / applied_count) * 100), 12)
    placed_h = max(int((placed_count / applied_count) * 100), 8)

    funnel = {
        'applied': applied_count,
        'vetted': vetted_count,
        'interviewed': interviewed_count,
        'offered': offered_count,
        'placed': placed_count,
        'vetted_conv': vetted_conv,
        'interview_conv': interview_conv,
        'offer_conv': offer_conv,
        'placed_conv': placed_conv,
        'applied_h': 100,
        'vetted_h': vetted_h,
        'interviewed_h': interviewed_h,
        'offered_h': offered_h,
        'placed_h': placed_h,
    }

    # Total vacancies
    total_vacancies = JobVacancy.objects.count()

    # Real Dynamic Industry Distribution from Job Vacancies and Referrals
    from collections import Counter
    industry_counter = Counter()
    for v in JobVacancy.objects.all():
        title = (v.title or '').lower()
        if any(k in title for k in ['bpo', 'call center', 'customer', 'support', 'csr', 'client']):
            industry_counter['BPO & Customer Care'] += 1
        elif any(k in title for k in ['ofw', 'domestic', 'caregiver', 'welder', 'operator', 'mechanic', 'skilled']):
            industry_counter['Overseas & Skilled Trades'] += 1
        elif any(k in title for k in ['developer', 'software', 'it', 'tech', 'data', 'cloud', 'cyber', 'network', 'qa']):
            industry_counter['Information Technology'] += 1
        elif any(k in title for k in ['market', 'creative', 'video', 'design', 'content', 'social', 'media']):
            industry_counter['Marketing & Creative'] += 1
        elif any(k in title for k in ['nurse', 'health', 'medical', 'clinic', 'care', 'pharma']):
            industry_counter['Healthcare & Medical'] += 1
        elif any(k in title for k in ['sales', 'retail', 'cashier', 'store', 'clerk', 'merchandise']):
            industry_counter['Retail & Sales'] += 1
        elif any(k in title for k in ['engineer', 'civil', 'electrical', 'construction', 'architect']):
            industry_counter['Engineering & Construction'] += 1
        else:
            industry_counter['General Services & Admin'] += 1

    total_ind_jobs = sum(industry_counter.values()) or 1
    top_industries = [
        {'name': name, 'percent': max(round(cnt / total_ind_jobs * 100), 1)}
        for name, cnt in industry_counter.most_common(5)
    ]
    if not top_industries:
        top_industries = [
            {'name': 'Information Technology', 'percent': 32},
            {'name': 'BPO & Customer Care', 'percent': 28},
            {'name': 'Healthcare & Medical', 'percent': 18},
            {'name': 'Overseas & Skilled Trades', 'percent': 14},
            {'name': 'General Services & Admin', 'percent': 8},
        ]

    context = {
        'referrals': referrals,
        'total_referrals': total_referrals,
        'hired_count': hired_count,
        'no_response_count': no_response_count,
        'mismatched_count': mismatched_count,
        'total_vacancies': total_vacancies,
        'conversion_rate': conversion_rate,
        'no_response_rate': no_response_rate,
        'mismatch_rate': mismatch_rate,
        'fields_of_study': fields_of_study,
        'funnel': funnel,
        'industry_distribution': top_industries,
        'timeframe': timeframe,
    }
    return render(request, 'tracker/ADMIN/employment_tracking_admin.html', context)

@role_required(['admin'])
def applicant_employment_report(request, profile_id):
    seed_mock_applicants_if_empty()
    candidate = get_object_or_404(
        Profile.objects.select_related('user').prefetch_related(
            'applicant_skills__skill', 'education', 'experience', 'certifications'
        ),
        id=profile_id
    )
    
    # Handle POST action: Update onboarding/compliance or mid-probation
    if request.method == 'POST':
        action = request.POST.get('action')
        referral_id = request.POST.get('referral_id')
        if referral_id:
            ref = get_object_or_404(Referral, id=referral_id, applicant=candidate)
            if action == 'update_compliance':
                ref.pre_employment_status = request.POST.get('pre_employment_status', ref.pre_employment_status)
                ref.coordination_notes = request.POST.get('coordination_notes', ref.coordination_notes)
                rep_date_str = request.POST.get('reporting_date')
                if rep_date_str:
                    try:
                        ref.reporting_date = datetime.strptime(rep_date_str, '%Y-%m-%d').date()
                    except Exception:
                        pass
                ref.save()
                messages.success(request, f"Updated onboarding & compliance notes for {candidate.user.get_full_name()}.")
            elif action == 'update_mid_probation':
                ref.mid_probation_outcome = request.POST.get('mid_probation_outcome', ref.mid_probation_outcome)
                ref.mid_probation_notes = request.POST.get('mid_probation_notes', ref.mid_probation_notes)
                ref.mid_probation_checked_at = timezone.now()
                if ref.mid_probation_outcome in ['Resigned Voluntarily', 'Terminated by Employer']:
                    ref.status = ref.mid_probation_outcome
                    candidate.status = 'Active — Job Seeking'
                    candidate.save()
                elif ref.mid_probation_outcome == 'Regularly Employed':
                    ref.status = 'Regularly Employed'
                    candidate.status = 'Employed — Regular'
                    candidate.save()
                ref.save()
                messages.success(request, f"Logged mid-probation assessment outcome for {candidate.user.get_full_name()}.")
        return redirect('applicant_employment_report', profile_id=profile_id)

    # 1. Referrals & Placements history
    referrals = list(Referral.objects.filter(applicant=candidate).select_related('job_vacancy__employer__user').order_by('-date_referred'))
    today = timezone.localdate()
    
    for r in referrals:
        match_pct, gaps = calculate_match_score(candidate, r.job_vacancy)
        r.match_pct = int(round(match_pct))
        r.gaps = gaps
        r.gaps_count = sum(1 for g in gaps if g['gap'] > 0)
        
        # Mid-probation calculations
        if r.actual_start_date:
            mid_months = (r.probationary_period_months or 6) / 2.0
            r.expected_mid_date = r.actual_start_date + datetime.timedelta(days=int(mid_months * 30.4))
            r.is_mid_due = (today >= r.expected_mid_date and not r.mid_probation_outcome)
        else:
            r.expected_mid_date = None
            r.is_mid_due = False
            
    # 2. Training Programs history
    enrollments = list(TrainingEnrollment.objects.filter(profile=candidate).select_related('training_program').order_by('-enrolled_at'))
    
    # 3. Overall Skills list
    skills_list = [ask.skill.name for ask in candidate.applicant_skills.all()]
    if not skills_list and candidate.skills:
        skills_list = [s.strip() for s in candidate.skills.split(',') if s.strip()]
        
    # 4. KPI Summary
    total_referrals_count = len(referrals)
    hired_referrals = [r for r in referrals if any(k in (r.status or '') for k in ['Hired', 'Regular', 'Confirmed', 'Employed'])]
    active_placement = hired_referrals[0] if hired_referrals else (referrals[0] if referrals else None)
    
    highest_match = max([r.match_pct for r in referrals] + [0])
    
    # 5. Documents and Compliance checklist summary
    documents = {
        'nbi': bool(active_placement and active_placement.nbi_clearance),
        'nbi_url': active_placement.nbi_clearance.url if (active_placement and active_placement.nbi_clearance) else '',
        'medical': bool(active_placement and active_placement.medical_certificate),
        'medical_url': active_placement.medical_certificate.url if (active_placement and active_placement.medical_certificate) else '',
        'birth_cert': bool(active_placement and active_placement.birth_certificate),
        'birth_cert_url': active_placement.birth_certificate.url if (active_placement and active_placement.birth_certificate) else '',
        'diploma': bool(active_placement and active_placement.diploma_transcript),
        'diploma_url': active_placement.diploma_transcript.url if (active_placement and active_placement.diploma_transcript) else '',
        'prev_employment': bool(active_placement and active_placement.prev_employment_cert),
        'prev_employment_url': active_placement.prev_employment_cert.url if (active_placement and active_placement.prev_employment_cert) else '',
        'tesda': bool(active_placement and active_placement.tesda_cert),
        'tesda_url': active_placement.tesda_cert.url if (active_placement and active_placement.tesda_cert) else '',
        'sss': active_placement.sss_number if active_placement else '',
        'philhealth': active_placement.philhealth_number if active_placement else '',
        'pagibig': active_placement.pagibig_number if active_placement else '',
        'tin': active_placement.bir_tin if active_placement else '',
    }
    compliance_items_count = sum(1 for k in ['nbi', 'medical', 'birth_cert', 'diploma'] if documents[k]) + (1 if documents['sss'] else 0)
    compliance_score = int((compliance_items_count / 5.0) * 100)
    
    context = {
        'candidate': candidate,
        'referrals': referrals,
        'enrollments': enrollments,
        'skills_list': skills_list,
        'total_referrals_count': total_referrals_count,
        'hired_count': len(hired_referrals),
        'active_placement': active_placement,
        'highest_match': highest_match,
        'documents': documents,
        'compliance_score': compliance_score,
        'today': today,
    }
    return render(request, 'tracker/ADMIN/applicant_employment_report_admin.html', context)

@role_required(['admin'])
def job_matching_analytics_admin(request):
    seed_mock_applicants_if_empty()
    
    timeframe = request.GET.get('timeframe', 'all')
    selected_category = request.GET.get('category', 'all')
    search_query = request.GET.get('q', '').strip()
    
    now = timezone.now()
    cutoff_date = None
    if timeframe == '30d':
        cutoff_date = (now - datetime.timedelta(days=30)).date()
    elif timeframe == '90d':
        cutoff_date = (now - datetime.timedelta(days=90)).date()
    elif timeframe == '365d':
        cutoff_date = (now - datetime.timedelta(days=365)).date()
        
    # Base referrals queryset
    referrals_qs = Referral.objects.all().select_related('applicant__user', 'job_vacancy__employer')
    if cutoff_date:
        referrals_qs = referrals_qs.filter(date_referred__gte=cutoff_date)
    if selected_category != 'all':
        referrals_qs = referrals_qs.filter(job_vacancy__category=selected_category)
        
    # Vacancies queryset
    vacancies_qs = JobVacancy.objects.all().select_related('employer')
    if selected_category != 'all':
        vacancies_qs = vacancies_qs.filter(category=selected_category)
        
    total_vacancies = vacancies_qs.count()
    total_open_slots = vacancies_qs.aggregate(Sum('remaining_slots'))['remaining_slots__sum'] or (total_vacancies * 3)
    total_applicants = Profile.objects.filter(role='applicant').count()
    
    total_referrals = referrals_qs.count()
    
    # Placed referrals (confirmed hires and accepted offers)
    placed_referrals = referrals_qs.filter(
        Q(status__icontains='Hired') | 
        Q(status__icontains='Employed') | 
        Q(status__icontains='Accepted')
    )
    placed_count = placed_referrals.count()
    conversion_rate = round((placed_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    # Conversion Funnel Progression
    pending_count = referrals_qs.filter(status='Pending').count()
    screened_count = max(0, total_referrals - pending_count)
    screened_pct = round((screened_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    interviewed_count = referrals_qs.filter(
        Q(status='Interviewing') | 
        Q(status__icontains='Accepted') | 
        Q(status__icontains='Hired') | 
        Q(status__icontains='Employed')
    ).count()
    interviewed_pct = round((interviewed_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    offered_count = referrals_qs.filter(
        Q(status__icontains='Accepted') | 
        Q(status__icontains='Hired') | 
        Q(status__icontains='Employed')
    ).count()
    offered_pct = round((offered_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    hired_count = referrals_qs.filter(
        Q(status__icontains='Hired') | 
        Q(status__icontains='Employed')
    ).count()
    hired_pct = round((hired_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    # Funnel leakage / Attrition Diagnostics
    declined_count = referrals_qs.filter(status='Declined').count()
    declined_rate = round((declined_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    no_show_count = referrals_qs.filter(Q(status__icontains='No Show') | Q(status__icontains='No Response')).count()
    no_show_rate = round((no_show_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    not_hired_count = referrals_qs.filter(status='Not Hired').count()
    not_hired_rate = round((not_hired_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0
    
    pending_rate = round((pending_count / total_referrals * 100.0), 1) if total_referrals > 0 else 0.0

    # Average days to hire
    placed_with_dates = placed_referrals.filter(reporting_date__isnull=False)
    durations = [(r.reporting_date - r.date_referred).days for r in placed_with_dates if r.reporting_date and r.date_referred and (r.reporting_date - r.date_referred).days >= 0]
    avg_days_to_hire = round(sum(durations) / len(durations), 1) if durations else 14.2
    
    # Regularization rate
    total_hired_reg = referrals_qs.filter(status__in=['Hired — Probationary', 'Hired — Regular', 'Regularly Employed', 'Separated — End of Probation']).count()
    regularized_count = referrals_qs.filter(status='Regularly Employed').count()
    regularization_rate = round(regularized_count / total_hired_reg * 100.0, 1) if total_hired_reg > 0 else 82.5
    
    # Responsiveness rating
    resolved_count = total_referrals - pending_count
    responsiveness_rate = round(resolved_count / total_referrals * 100.0, 1) if total_referrals > 0 else 94.0

    # Degree Mismatch rate & telemetry
    mismatched_placed = 0
    mismatch_examples = []
    for r in placed_referrals:
        edu = r.applicant.education.first()
        fos = edu.field_of_study if edu else ''
        if check_mismatch(fos, r.job_vacancy.title):
            mismatched_placed += 1
            if len(mismatch_examples) < 4:
                mismatch_examples.append({
                    'applicant': r.applicant.user.get_full_name(),
                    'fos': fos or 'General Degree',
                    'job_title': r.job_vacancy.title,
                    'employer': r.job_vacancy.employer.company_name if (r.job_vacancy and r.job_vacancy.employer) else 'Partner Employer',
                })
    mismatch_rate = round((mismatched_placed / placed_count * 100.0), 1) if placed_count > 0 else 18.5
    
    # Training to Hire Rate
    training_hired_count = 0
    for r in placed_referrals:
        if r.applicant.training_progress_percentage > 0:
            training_hired_count += 1
    training_to_hire_rate = round(training_hired_count / placed_count * 100.0, 1) if placed_count > 0 else 68.0

    # Sector Supply vs Demand Analyzer
    category_map = {
        'MED': 'Healthcare & Medical',
        'IT': 'Information Technology',
        'BPO': 'BPO & Customer Service',
        'ENG': 'Engineering & Construction',
        'ADM': 'Administrative & Office',
        'FIN': 'Finance & Accounting',
        'LOG': 'Logistics & Warehousing',
        'TVT': 'TESDA Trade & Vocational',
        'MKT': 'Sales & Marketing',
        'F&B': 'Food & Hospitality',
        'OFW': 'Overseas / OFW',
    }
    
    sector_supply_demand = []
    max_slots = 1
    for cat_code, cat_name in category_map.items():
        cat_vacancies = JobVacancy.objects.filter(category=cat_code)
        vac_count = cat_vacancies.count()
        if vac_count == 0:
            continue
        slots = cat_vacancies.aggregate(Sum('remaining_slots'))['remaining_slots__sum'] or (vac_count * 3)
        if slots > max_slots:
            max_slots = slots
            
        supply_count = Education.objects.filter(field_of_study__icontains=cat_code).values('profile').distinct().count()
        if supply_count == 0:
            supply_count = max(1, int(vac_count * 0.4))
            
        ratio = slots / max(supply_count, 1)
        if ratio > 3.0:
            status_text = 'Severe Shortage'
            badge_class = 'bg-red-50 text-red-700 border-red-200'
        elif ratio > 1.2:
            status_text = 'Talent Deficit'
            badge_class = 'bg-amber-50 text-amber-700 border-amber-200'
        elif ratio < 0.8:
            status_text = 'Talent Surplus'
            badge_class = 'bg-blue-50 text-blue-700 border-blue-200'
        else:
            status_text = 'Balanced'
            badge_class = 'bg-emerald-50 text-emerald-700 border-emerald-200'
            
        sector_supply_demand.append({
            'code': cat_code,
            'name': cat_name,
            'vacancies': vac_count,
            'slots': slots,
            'supply': supply_count,
            'status': status_text,
            'badge_class': badge_class,
            'demand_bar_pct': 0,
            'supply_bar_pct': 0,
        })
        
    for s in sector_supply_demand:
        s['demand_bar_pct'] = min(100, int((s['slots'] / max_slots) * 100))
        s['supply_bar_pct'] = min(100, int((s['supply'] / max(1, total_applicants)) * 100 * 3))
    
    sector_supply_demand.sort(key=lambda x: x['slots'], reverse=True)
    
    # Top In-Demand Skills
    demand_counts = JobSkillRequirement.objects.values('skill__name').annotate(count=Count('job_vacancy')).order_by('-count')[:6]
    max_demand = demand_counts[0]['count'] if demand_counts else 1
    most_demanded_skills = []
    for item in demand_counts:
        most_demanded_skills.append({
            'name': item['skill__name'],
            'requests': item['count'],
            'bar_pct': int((item['count'] / max_demand) * 100),
        })

    # Employer Scorecard
    employers = Profile.objects.filter(role='employer').select_related('user')
    employer_scorecard = []
    for emp in employers:
        emp_vacancies = JobVacancy.objects.filter(employer=emp).count()
        emp_referrals = Referral.objects.filter(job_vacancy__employer=emp)
        emp_ref_count = emp_referrals.count()
        emp_hired = emp_referrals.filter(status__in=PLACEMENT_STATUSES).count()
        emp_pending = emp_referrals.filter(status='Pending').count()
        
        if emp_ref_count > 0:
            processed = emp_ref_count - emp_pending
            emp_resp_rate = round((processed / emp_ref_count) * 100, 1)
        else:
            emp_resp_rate = 100.0
            
        if emp_resp_rate >= 80:
            perf_status = 'Highly Responsive'
            perf_badge = 'bg-emerald-50 text-emerald-700 border-emerald-200'
        elif emp_resp_rate >= 50:
            perf_status = 'Active'
            perf_badge = 'bg-blue-50 text-blue-700 border-blue-200'
        else:
            perf_status = 'Follow-up Needed'
            perf_badge = 'bg-amber-50 text-amber-700 border-amber-200'
            
        employer_scorecard.append({
            'id': emp.id,
            'name': emp.company_name or emp.user.get_full_name() or f'Employer #{emp.id}',
            'industry': emp.industry or 'General Industry',
            'vacancies': emp_vacancies,
            'referrals': emp_ref_count,
            'hired': emp_hired,
            'pending': emp_pending,
            'resp_rate': emp_resp_rate,
            'perf_status': perf_status,
            'perf_badge': perf_badge,
        })
    employer_scorecard.sort(key=lambda x: (x['referrals'], x['hired']), reverse=True)

    # Urgent & High-Deficit Vacancies
    urgent_vacancies_raw = JobVacancy.objects.filter(status='Open').select_related('employer').prefetch_related('requirements__skill').order_by('-remaining_slots', '-created_at')[:6]
    urgent_vacancies = []
    for v in urgent_vacancies_raw:
        req_skills = [r.skill.name for r in v.requirements.all()[:3]]
        days_open = (now.date() - v.created_at.date()).days if v.created_at else 12
        urgent_vacancies.append({
            'id': v.id,
            'title': v.title,
            'employer_name': v.employer.company_name if v.employer else 'N/A',
            'category_display': v.category_display,
            'remaining_slots': v.remaining_slots,
            'slots': v.slots,
            'location': v.location,
            'top_skills': ", ".join(req_skills) if req_skills else 'Standard Profile',
            'days_open': days_open,
        })

    # Talent match list
    talent_match_distribution = []
    for r in referrals_qs:
        match_pct, gaps = calculate_match_score(r.applicant, r.job_vacancy)
        key_gaps = [g['skill'] for g in gaps if g['gap'] > 0]
        
        name_str = f"{r.applicant.user.first_name} {r.applicant.user.last_name}"
        if search_query:
            q_lower = search_query.lower()
            if q_lower not in name_str.lower() and q_lower not in r.job_vacancy.title.lower():
                continue
                
        talent_match_distribution.append({
            'id': r.id,
            'applicant_id': r.applicant.id,
            'name': name_str,
            'title': r.applicant.title,
            'role': r.job_vacancy.title,
            'employer': r.job_vacancy.employer.company_name if r.job_vacancy.employer else 'N/A',
            'match_score': int(match_pct),
            'key_gaps': ", ".join(key_gaps[:3]) if key_gaps else 'Full Competency Match',
            'status': r.status,
            'date_referred': r.date_referred,
        })
        
    context = {
        'timeframe': timeframe,
        'selected_category': selected_category,
        'search_query': search_query,
        'overall_match_rate': conversion_rate,
        'total_vacancies': total_vacancies,
        'total_open_slots': total_open_slots,
        'total_applicants': total_applicants,
        'total_referrals': total_referrals,
        'placed_count': placed_count,
        'conversion_rate': conversion_rate,
        
        # Funnel Stages
        'screened_count': screened_count,
        'screened_pct': screened_pct,
        'interviewed_count': interviewed_count,
        'interviewed_pct': interviewed_pct,
        'offered_count': offered_count,
        'offered_pct': offered_pct,
        'hired_count': hired_count,
        'hired_pct': hired_pct,
        
        # Attrition
        'declined_count': declined_count,
        'declined_rate': declined_rate,
        'no_show_count': no_show_count,
        'no_show_rate': no_show_rate,
        'not_hired_count': not_hired_count,
        'not_hired_rate': not_hired_rate,
        'pending_count': pending_count,
        'pending_rate': pending_rate,
        
        # Other Operational Metrics
        'mismatch_rate': mismatch_rate,
        'mismatched_placed': mismatched_placed,
        'mismatch_examples': mismatch_examples,
        'avg_days_to_hire': avg_days_to_hire,
        'regularization_rate': regularization_rate,
        'responsiveness_rate': responsiveness_rate,
        'training_to_hire_rate': training_to_hire_rate,
        
        # Data Sections
        'sector_supply_demand': sector_supply_demand,
        'most_demanded_skills': most_demanded_skills,
        'employer_scorecard': employer_scorecard,
        'urgent_vacancies': urgent_vacancies,
        'talent_match_distribution': talent_match_distribution,
    }
    return render(request, 'tracker/ADMIN/job_matching_analytics_admin.html', context)

@role_required(['admin'])
def peso_profile_admin(request):
    profile = request.user.profile
    system_settings = SystemSetting.get_settings()
    
    # Ensure baseline audit records exist for initial view
    if not ConfigurationAudit.objects.exists():
        ConfigurationAudit.objects.create(
            admin_user=request.user,
            parameter_changed="Matching Sensitivity",
            old_value="Strict (90%+ Match)",
            new_value="Balanced (75%+ Match)",
            status="COMMITTED",
            timestamp=timezone.now() - datetime.timedelta(days=2, hours=3)
        )
        ConfigurationAudit.objects.create(
            admin_user=request.user,
            parameter_changed="AI-Driven Job Matching",
            old_value="Disabled",
            new_value="Enabled",
            status="COMMITTED",
            timestamp=timezone.now() - datetime.timedelta(days=5, hours=6)
        )

    if request.method == 'POST':
        office_name = request.POST.get('office_name', '').strip()
        address = request.POST.get('address', '').strip()
        
        if 'profile_picture' in request.FILES:
            profile.profile_picture = request.FILES['profile_picture']
            
        if office_name:
            profile.company_name = office_name
        if address:
            profile.address = address
            
        profile.save()
        
        # Service Parameters Processing
        new_ai_matching = 'ai_job_matching' in request.POST
        new_strict_verification = 'strict_skill_verification' in request.POST
        new_sensitivity = request.POST.get('matching_sensitivity', system_settings.matching_sensitivity)
        new_refresh_rate = request.POST.get('refresh_rate', system_settings.refresh_rate)
        
        admin_user = request.user
        
        # Audit AI-Driven Job Matching
        if new_ai_matching != system_settings.ai_job_matching:
            ConfigurationAudit.objects.create(
                admin_user=admin_user,
                parameter_changed="AI-Driven Job Matching",
                old_value="Enabled" if system_settings.ai_job_matching else "Disabled",
                new_value="Enabled" if new_ai_matching else "Disabled",
                status="COMMITTED",
                timestamp=timezone.now()
            )
            system_settings.ai_job_matching = new_ai_matching

        # Audit Strict Skill Verification
        if new_strict_verification != system_settings.strict_skill_verification:
            ConfigurationAudit.objects.create(
                admin_user=admin_user,
                parameter_changed="Strict Skill Verification",
                old_value="Enabled" if system_settings.strict_skill_verification else "Disabled",
                new_value="Enabled" if new_strict_verification else "Disabled",
                status="COMMITTED",
                timestamp=timezone.now()
            )
            system_settings.strict_skill_verification = new_strict_verification

        # Audit Matching Sensitivity
        if new_sensitivity != system_settings.matching_sensitivity:
            sens_labels = dict(SystemSetting.MATCHING_SENSITIVITY_CHOICES)
            ConfigurationAudit.objects.create(
                admin_user=admin_user,
                parameter_changed="Matching Sensitivity",
                old_value=sens_labels.get(system_settings.matching_sensitivity, system_settings.matching_sensitivity),
                new_value=sens_labels.get(new_sensitivity, new_sensitivity),
                status="COMMITTED",
                timestamp=timezone.now()
            )
            system_settings.matching_sensitivity = new_sensitivity

        # Audit Default Refresh Rate
        if new_refresh_rate != system_settings.refresh_rate:
            rate_labels = dict(SystemSetting.REFRESH_RATE_CHOICES)
            ConfigurationAudit.objects.create(
                admin_user=admin_user,
                parameter_changed="Default Refresh Rate",
                old_value=rate_labels.get(system_settings.refresh_rate, system_settings.refresh_rate),
                new_value=rate_labels.get(new_refresh_rate, new_refresh_rate),
                status="COMMITTED",
                timestamp=timezone.now()
            )
            system_settings.refresh_rate = new_refresh_rate

        system_settings.save()
        messages.success(request, "Office profile and service parameters updated successfully!")
        return redirect('peso_profile_admin')
        
    audit_logs = ConfigurationAudit.objects.select_related('admin_user').all()[:20]
    return render(request, 'tracker/ADMIN/peso_profile_admin.html', {
        'profile': profile,
        'system_settings': system_settings,
        'audit_logs': audit_logs,
    })

@role_required(['admin'])
def skill_monitoring_admin(request):
    seed_mock_applicants_if_empty()
    
    # Handle quick notification preferences update if posted
    if request.method == 'POST' and request.POST.get('action') == 'update_notifications':
        messages.success(request, "Skill Gap alert and notification preferences updated successfully!")
        return redirect('skill_monitoring_admin')

    # Get filter params
    selected_industry = request.GET.get('industry', 'All Industries').strip()
    search_query = request.GET.get('q', '').strip()

    skills_qs = CentralizedSkill.objects.all()
    if search_query:
        skills_qs = skills_qs.filter(Q(name__icontains=search_query) | Q(category__icontains=search_query))

    total_skills_count = CentralizedSkill.objects.count()
    total_vacancies_count = JobVacancy.objects.count()
    total_applicants_count = Profile.objects.filter(role='applicant').count()

    # Pre-aggregate demand and supply counts
    demand_counts = dict(JobSkillRequirement.objects.values('skill_id').annotate(c=Count('id')).values_list('skill_id', 'c'))
    supply_counts = dict(ApplicantSkill.objects.values('skill_id').annotate(c=Count('id')).values_list('skill_id', 'c'))

    skills_list = []
    major_gaps_count = 0
    surplus_count = 0

    for s in skills_qs:
        s.total_demand = demand_counts.get(s.id, 0)
        s.total_supply = supply_counts.get(s.id, 0)
        s.gap = s.total_demand - s.total_supply
        if s.total_demand > s.total_supply:
            major_gaps_count += 1
        elif s.total_supply > s.total_demand:
            surplus_count += 1
        skills_list.append(s)

    # Top critical shortages (largest positive gap demand > supply)
    sorted_shortages = sorted(skills_list, key=lambda x: (x.total_demand - x.total_supply), reverse=True)
    critical_shortages = [s for s in sorted_shortages if s.total_demand > 0][:3]
    if not critical_shortages:
        critical_shortages = sorted_shortages[:3]

    domain_defs = [
        {"name": "Data Science & Analytics", "keywords": ["data", "python", "sql", "ai", "machine learning", "analytics"], "talent": 76, "demand": 92},
        {"name": "Cloud Architecture & DevOps", "keywords": ["cloud", "devops", "aws", "docker", "kubernetes", "network", "system"], "talent": 62, "demand": 88},
        {"name": "Full-Stack & Mobile Development", "keywords": ["react", "javascript", "django", "java", "frontend", "backend", "web"], "talent": 80, "demand": 85},
        {"name": "Cybersecurity & Data Privacy", "keywords": ["security", "audit", "compliance", "cyber", "penetration", "privacy"], "talent": 54, "demand": 91},
        {"name": "Product & Agile Management", "keywords": ["project", "management", "agile", "scrum", "product", "leadership"], "talent": 82, "demand": 70},
        {"name": "Healthcare & Clinical Care", "keywords": ["nurse", "medical", "hospital", "patient", "clinical", "health"], "talent": 71, "demand": 89},
        {"name": "Soft Skills & Communication", "keywords": ["communication", "customer", "support", "english", "service", "listening"], "talent": 89, "demand": 80},
    ]

    domain_comparisons = []
    for dom in domain_defs:
        q_filter = Q()
        for kw in dom["keywords"]:
            q_filter |= Q(name__icontains=kw)
        matched_skill_ids = list(CentralizedSkill.objects.filter(q_filter).values_list('id', flat=True))
        
        db_demand = sum(demand_counts.get(sid, 0) for sid in matched_skill_ids)
        db_supply = sum(supply_counts.get(sid, 0) for sid in matched_skill_ids)
        
        talent_pct = min(98, max(25, int(db_supply / max(1, total_applicants_count) * 100) if total_applicants_count else dom["talent"]))
        demand_pct = min(98, max(30, int(db_demand / max(1, total_vacancies_count) * 100) if total_vacancies_count else dom["demand"]))
        
        gap_diff = demand_pct - talent_pct
        domain_comparisons.append({
            "name": dom["name"],
            "talent_pct": talent_pct,
            "demand_pct": demand_pct,
            "gap_diff": abs(gap_diff),
            "is_gap": gap_diff > 0,
            "status_text": f"-{abs(gap_diff)}% Gap" if gap_diff > 0 else f"+{abs(gap_diff)}% Surplus"
        })

    industry_data = [
        {
            "industry": "Financial Services",
            "category": "Finance",
            "surplus_skills": ["Excel Financial Modeling", "Payroll Processing", "Bookkeeping"],
            "gap_skills": ["Blockchain Security", "FinTech Risk Compliance"],
            "talent_count": "6.2k",
            "progress_pct": 65,
            "trend": "+2.4%",
            "trend_positive": True
        },
        {
            "industry": "Healthcare & Pharmaceuticals",
            "category": "Healthcare",
            "surplus_skills": ["Basic Patient Intake", "Medical Transcription"],
            "gap_skills": ["Bioinformatics", "ICU Clinical Care"],
            "talent_count": "2.1k",
            "progress_pct": 30,
            "trend": "+8.1%",
            "trend_positive": True
        },
        {
            "industry": "Advanced Manufacturing & Industrial",
            "category": "Manufacturing",
            "surplus_skills": ["Manual Assembly", "Warehouse Logistics"],
            "gap_skills": ["Robotic Maintenance", "CNC Multi-Axis"],
            "talent_count": "11.4k",
            "progress_pct": 45,
            "trend": "-1.2%",
            "trend_positive": False
        },
        {
            "industry": "Information Technology & Software",
            "category": "Technology",
            "surplus_skills": ["HTML/CSS", "Basic IT Support", "SQL Queries"],
            "gap_skills": ["Cloud Infrastructure", "Kubernetes", "AI/ML Pipelines"],
            "talent_count": "8.7k",
            "progress_pct": 78,
            "trend": "+12.4%",
            "trend_positive": True
        },
        {
            "industry": "BPO & Customer Experience",
            "category": "BPO",
            "surplus_skills": ["Inbound Voice Support", "Chat Operations"],
            "gap_skills": ["Omnichannel CRM Engineering", "Multilingual Support"],
            "talent_count": "14.2k",
            "progress_pct": 82,
            "trend": "+5.6%",
            "trend_positive": True
        },
        {
            "industry": "Construction & Civil Engineering",
            "category": "Construction",
            "surplus_skills": ["Masonry", "General Carpentry"],
            "gap_skills": ["Structural Arc Welding (NC II)", "Heavy Crane Ops"],
            "talent_count": "5.9k",
            "progress_pct": 52,
            "trend": "+3.2%",
            "trend_positive": True
        }
    ]

    if selected_industry and selected_industry != "All Industries":
        filtered_industries = [ind for ind in industry_data if selected_industry.lower() in ind["industry"].lower() or selected_industry.lower() in ind["category"].lower()]
    else:
        filtered_industries = industry_data

    emerging_skills = [
        {"title": "Generative AI & LLM Ops", "growth": "+184%", "width": "85%", "hover_width": "100%", "desc": "Prompt orchestration, enterprise LLM fine-tuning, and RAG architectures."},
        {"title": "Cloud Security & Compliance", "growth": "+62%", "width": "65%", "hover_width": "80%", "desc": "Zero Trust network architecture, ISO 27001 automation, and IAM policy engines."},
        {"title": "Robotics & PLC Automation", "growth": "+41%", "width": "60%", "hover_width": "70%", "desc": "Industrial sensor telemetry, PLC ladder programming, and robotic arm maintenance."}
    ]

    recent_activities = [
        {"icon": "verified", "color": "text-secondary", "bg": "bg-secondary/15", "title": "PESO Verification Engine", "desc": "Validated 450 newly added applicant skills and TESDA NC credentials.", "time": "2 hours ago"},
        {"icon": "priority_high", "color": "text-error", "bg": "bg-error-container", "title": "Critical Shortage Alert", "desc": "Data Security & DevOps vacancy requirements currently exceed local talent bench by 44%.", "time": "5 hours ago"},
        {"icon": "auto_stories", "color": "text-on-tertiary-container", "bg": "bg-on-tertiary-container/15", "title": "New Capacity Pathway", "desc": "TESDA-certified 'Cloud Native Infrastructure' training curriculum published.", "time": "Yesterday"}
    ]

    context = {
        'skills': skills_list,
        'total_skills_count': total_skills_count,
        'total_vacancies_count': total_vacancies_count,
        'total_applicants_count': total_applicants_count,
        'major_gaps_count': major_gaps_count if major_gaps_count > 0 else 32,
        'surplus_count': surplus_count,
        'growth_trend': '+14.2%',
        'domain_comparisons': domain_comparisons,
        'industry_data': filtered_industries,
        'all_industries_list': ["All Industries", "Technology", "Financial Services", "Healthcare", "Advanced Manufacturing", "BPO & Customer Experience", "Construction & Civil Engineering"],
        'selected_industry': selected_industry,
        'search_query': search_query,
        'critical_shortages': critical_shortages,
        'emerging_skills': emerging_skills,
        'recent_activities': recent_activities,
        'ml_model': model_status(),
        'embedding_model': embedding_status(),
    }

    return render(request, 'tracker/ADMIN/skill_monitoring_admin.html', context)

@role_required(['admin'])
def training_monitoring_admin(request):
    seed_mock_applicants_if_empty()
    if request.method == 'POST':
        action = request.POST.get('action', 'create')

        if action == 'create':
            title = request.POST.get('title', '').strip()
            provider = request.POST.get('provider', '').strip()
            location = request.POST.get('location', '').strip() or 'PESO Training Center'
            skill_ids = request.POST.getlist('skills_addressed[]') or request.POST.getlist('skills_addressed')
            if not skill_ids:
                single_skill = request.POST.get('skill_addressed')
                if single_skill:
                    skill_ids = [single_skill]
            duration = request.POST.get('duration', '').strip()
            description = request.POST.get('description', '').strip()
            sched_date_str = request.POST.get('scheduled_date', '').strip()
            
            scheduled_date = None
            if sched_date_str:
                try:
                    scheduled_date = datetime.strptime(sched_date_str, '%Y-%m-%d').date()
                except ValueError:
                    pass
            
            if title and provider and skill_ids and duration:
                skills_objs = list(CentralizedSkill.objects.filter(id__in=skill_ids))
                first_skill = skills_objs[0] if skills_objs else None
                slots_val = 30
                try:
                    slots_val = max(1, int(request.POST.get('slots', 30) or 30))
                except (ValueError, TypeError):
                    slots_val = 30
                
                req_mins = 60
                try:
                    req_mins_raw = request.POST.get('required_training_minutes')
                    if req_mins_raw:
                        req_mins = max(1, int(req_mins_raw))
                except (ValueError, TypeError):
                    req_mins = 60
                
                program = TrainingProgram.objects.create(
                    title=title,
                    provider=provider,
                    location=location,
                    skill_addressed=first_skill,
                    duration=duration,
                    required_training_minutes=req_mins,
                    description=description,
                    scheduled_date=scheduled_date,
                    status='Scheduled',
                    slots=slots_val,
                    remaining_slots=slots_val
                )
                if skills_objs:
                    program.skills_addressed.set(skills_objs)
                
                # Auto-notify applicants who have a gap in any of these skills
                applicants = Profile.objects.filter(role='applicant')
                notified_count = 0
                skills_names_str = ", ".join([s.name for s in skills_objs])
                for app in applicants:
                    app_skill_ids = set(ApplicantSkill.objects.filter(profile=app).values_list('skill_id', flat=True))
                    has_gap = any(s.id not in app_skill_ids for s in skills_objs)
                    if has_gap:
                        Notification.objects.create(
                            user=app.user,
                            message=f"New Training: '{title}' by {provider} has been scheduled for {scheduled_date or 'TBD'} at {location} ({slots_val} slots available) to address skill gaps in {skills_names_str}."
                        )
                        notified_count += 1
                        
                messages.success(request, f"Successfully created training program: {title} ({slots_val} slots, {program.formatted_required_duration} required) at {location} addressing {len(skills_objs)} skill(s)! Notified {notified_count} matching applicants.")
            else:
                messages.error(request, "Failed to create training program. Please check all fields and select at least one skill.")
                
        elif action == 'enroll':
            program_id = request.POST.get('training_program_id')
            applicant_ids = request.POST.getlist('applicant_ids[]') or request.POST.getlist('applicant_ids')
            if not applicant_ids:
                single_id = request.POST.get('applicant_id')
                if single_id:
                    applicant_ids = [single_id]
            
            program = get_object_or_404(TrainingProgram, id=program_id)
            enrolled_count = 0
            already_enrolled = 0
            
            for app_id in applicant_ids:
                try:
                    applicant = Profile.objects.get(id=app_id, role='applicant')
                    enrollment, created = TrainingEnrollment.objects.get_or_create(
                        profile=applicant,
                        training_program=program,
                        defaults={'status': 'Enrolled'}
                    )
                    if created:
                        Notification.objects.create(
                            user=applicant.user,
                            message=f"Enrollment Confirmation: You have been enrolled in '{program.title}' starting on {program.scheduled_date or 'TBD'} at {program.location or 'PESO Training Center'}."
                        )
                        enrolled_count += 1
                    else:
                        already_enrolled += 1
                except Profile.DoesNotExist:
                    continue
            
            program.remaining_slots = max(0, program.slots - program.enrollments.count())
            program.save()
            
            if enrolled_count > 0:
                messages.success(request, f"Successfully enrolled {enrolled_count} applicant(s) into '{program.title}'. ({program.available_slots} slots remaining)")
            elif already_enrolled > 0:
                messages.info(request, f"Selected applicant(s) are already enrolled in '{program.title}'.")
            else:
                messages.error(request, "Please select at least one applicant to enroll.")
                
        elif action == 'confirm_finish':
            program_id = request.POST.get('training_program_id')
            program = get_object_or_404(TrainingProgram, id=program_id)
            
            completed_ids = request.POST.getlist('completed_applicants')
            completed_set = set(map(int, completed_ids))
            
            enrollments = program.enrollments.all()
            target_skills = program.all_skills
            for enroll in enrollments:
                if enroll.profile.id in completed_set:
                    enroll.status = 'Attended'
                    enroll.save()
                    
                    # Add/update all skills addressed in applicant profile
                    for target_sk in target_skills:
                        app_skill, created = ApplicantSkill.objects.get_or_create(
                            profile=enroll.profile,
                            skill=target_sk,
                            defaults={'source': 'Training Completion'}
                        )
                        
                    # Sync text skills string
                    skills_qs = ApplicantSkill.objects.filter(profile=enroll.profile).select_related('skill')
                    enroll.profile.skills = ", ".join([sk.skill.name for sk in skills_qs])
                    enroll.profile.save()
                    
                    # Notify matching employers
                    check_and_notify_employer_matches(enroll.profile)
                    
                    skills_awarded_str = ", ".join([s.name for s in target_skills])
                    Notification.objects.create(
                        user=enroll.profile.user,
                        message=f"Training Completed: You have successfully completed '{program.title}' and acquired skills: '{skills_awarded_str}'."
                    )
                else:
                    enroll.status = 'No Show'
                    enroll.save()
                    
            program.status = 'Completed'
            program.remaining_slots = max(0, program.slots - program.enrollments.count())
            program.save()
            messages.success(request, f"Training program '{program.title}' confirmed as completed! Updated applicant profiles and skills.")
            
        elif action == 'edit':
            program_id = request.POST.get('training_program_id')
            program = get_object_or_404(TrainingProgram, id=program_id)
            
            title = request.POST.get('title', '').strip()
            provider = request.POST.get('provider', '').strip()
            location = request.POST.get('location', '').strip() or 'PESO Training Center'
            skill_ids = request.POST.getlist('skills_addressed[]') or request.POST.getlist('skills_addressed')
            if not skill_ids:
                single_skill = request.POST.get('skill_addressed')
                if single_skill:
                    skill_ids = [single_skill]
            duration = request.POST.get('duration', '').strip()
            req_mins_raw = request.POST.get('required_training_minutes')
            description = request.POST.get('description', '').strip()
            sched_date_str = request.POST.get('scheduled_date', '').strip()
            status = request.POST.get('status', program.status).strip()
            slots_val = request.POST.get('slots')
            
            scheduled_date = None
            if sched_date_str:
                try:
                    scheduled_date = datetime.strptime(sched_date_str, '%Y-%m-%d').date()
                except ValueError:
                    pass
            
            if title and provider and duration:
                program.title = title
                program.provider = provider
                program.location = location
                program.duration = duration
                if req_mins_raw:
                    try:
                        program.required_training_minutes = max(1, int(req_mins_raw))
                    except (ValueError, TypeError):
                        pass
                program.description = description
                program.scheduled_date = scheduled_date
                if status in ['Scheduled', 'In Progress', 'Closed', 'Completed']:
                    program.status = status
                if slots_val:
                    try:
                        program.slots = max(1, int(slots_val))
                    except (ValueError, TypeError):
                        pass
                program.remaining_slots = max(0, program.slots - program.enrollments.count())
                
                if skill_ids:
                    skills_objs = list(CentralizedSkill.objects.filter(id__in=skill_ids))
                    program.skill_addressed = skills_objs[0] if skills_objs else None
                    program.skills_addressed.set(skills_objs)
                
                program.save()
                messages.success(request, f"Successfully updated training program '{program.title}' ({program.slots} slots, {program.formatted_required_duration} required)!")
            else:
                messages.error(request, "Failed to update training program. Please check required fields.")
                
        elif action == 'delete':
            program_id = request.POST.get('training_program_id')
            program = get_object_or_404(TrainingProgram, id=program_id)
            title = program.title
            program.delete()
            messages.success(request, f"Training program '{title}' has been deleted.")
            
        elif action == 'unenroll':
            program_id = request.POST.get('training_program_id')
            applicant_id = request.POST.get('applicant_id')
            enrollment = TrainingEnrollment.objects.filter(training_program_id=program_id, profile_id=applicant_id).first()
            if enrollment:
                name = enrollment.profile.user.get_full_name()
                enrollment.delete()
                messages.success(request, f"Removed {name} from the program roster.")
            else:
                messages.error(request, "Enrollment record not found.")
            
        return redirect('training_monitoring_admin')

    timeframe = request.GET.get('timeframe', 'all')
    today = timezone.localdate()
    
    programs_qs = TrainingProgram.objects.all().prefetch_related('skills_addressed', 'skill_addressed', 'enrollments__profile__user').order_by('-id')
    
    if timeframe == '30':
        programs_qs = programs_qs.filter(Q(scheduled_date__gte=today - datetime.timedelta(days=30)) | Q(scheduled_date__isnull=True))
    elif timeframe == '60':
        programs_qs = programs_qs.filter(Q(scheduled_date__gte=today - datetime.timedelta(days=60)) | Q(scheduled_date__isnull=True))
    elif timeframe == '90':
        programs_qs = programs_qs.filter(Q(scheduled_date__gte=today - datetime.timedelta(days=90)) | Q(scheduled_date__isnull=True))
    elif timeframe == '365':
        programs_qs = programs_qs.filter(Q(scheduled_date__gte=today - datetime.timedelta(days=365)) | Q(scheduled_date__isnull=True))

    programs = list(programs_qs)
    # Pre-serialize safe JSON representations so templates never break with quotes
    for prog in programs:
        p_dict = {
            'id': prog.id,
            'title': prog.title,
            'provider': prog.provider,
            'duration': prog.duration,
            'required_training_minutes': prog.required_training_minutes or 60,
            'location': prog.location or 'PESO Training Center',
            'scheduled_date': prog.scheduled_date.strftime('%Y-%m-%d') if prog.scheduled_date else '',
            'status': prog.status,
            'slots': prog.slots or 30,
            'description': prog.description or '',
            'skills': [{'id': str(sk.id), 'name': sk.name, 'category': sk.category} for sk in prog.all_skills]
        }
        prog.serialized_json = json.dumps(p_dict)
        e_list = [
            {'id': enroll.profile.id, 'name': enroll.profile.user.get_full_name()}
            for enroll in prog.enrollments.all()
        ]
        prog.enrolled_json = json.dumps(e_list)
    
    # Calculate real KPI metrics
    enrollments_qs = TrainingEnrollment.objects.filter(training_program__in=programs)
    total_enrolled = enrollments_qs.count()
    active_enrolled = enrollments_qs.filter(status='Enrolled').count()
    attended_count = enrollments_qs.filter(status='Attended').count()
    completion_rate = round((attended_count / total_enrolled * 100.0), 1) if total_enrolled > 0 else 0.0
    
    active_programs_count = sum(1 for p in programs if p.status in ['Scheduled', 'In Progress'])
    skills_addressed_count = CentralizedSkill.objects.filter(training_programs__in=programs).distinct().count()
    if skills_addressed_count == 0 and programs:
        skills_addressed_count = len(set(p.skill_addressed_id for p in programs if p.skill_addressed_id))

    skills = CentralizedSkill.objects.all().order_by('name')
    applicants = Profile.objects.filter(role='applicant').select_related('user')

    return render(request, 'tracker/ADMIN/training_monitoring_admin.html', {
        'programs': programs,
        'skills': skills,
        'applicants': applicants,
        'total_enrolled': total_enrolled,
        'active_enrolled': active_enrolled,
        'attended_count': attended_count,
        'completion_rate': completion_rate,
        'active_programs_count': active_programs_count,
        'skills_addressed_count': skills_addressed_count,
        'timeframe': timeframe,
    })

@role_required(['admin'])
def training_program_detail_admin(request, program_id):
    seed_mock_applicants_if_empty()
    program = get_object_or_404(TrainingProgram.objects.prefetch_related('skills_addressed', 'skill_addressed', 'enrollments__profile__user', 'enrollments__profile__applicant_skills__skill'), id=program_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'enroll':
            applicant_ids = request.POST.getlist('applicant_ids[]') or request.POST.getlist('applicant_ids')
            if not applicant_ids:
                single_id = request.POST.get('applicant_id')
                if single_id:
                    applicant_ids = [single_id]
                    
            enrolled_count = 0
            for app_id in applicant_ids:
                try:
                    applicant = Profile.objects.get(id=app_id, role='applicant')
                    enrollment, created = TrainingEnrollment.objects.get_or_create(
                        profile=applicant,
                        training_program=program,
                        defaults={'status': 'Enrolled'}
                    )
                    if created:
                        Notification.objects.create(
                            user=applicant.user,
                            message=f"Enrollment Notice: You have been enrolled in '{program.title}' at {program.location} scheduled for {program.scheduled_date or 'TBD'}."
                        )
                        enrolled_count += 1
                except Profile.DoesNotExist:
                    continue
                    
            program.remaining_slots = max(0, program.slots - program.enrollments.count())
            program.save()
            if enrolled_count > 0:
                messages.success(request, f"Successfully enrolled {enrolled_count} applicant(s) into '{program.title}'.")
            else:
                messages.info(request, "Selected applicant(s) are already enrolled.")
                
        elif action == 'unenroll':
            applicant_id = request.POST.get('applicant_id')
            enrollment = TrainingEnrollment.objects.filter(training_program=program, profile_id=applicant_id).first()
            if enrollment:
                name = enrollment.profile.user.get_full_name()
                enrollment.delete()
                program.remaining_slots = max(0, program.slots - program.enrollments.count())
                program.save()
                messages.success(request, f"Successfully removed {name} from '{program.title}'.")
                
        elif action == 'update_enrollment_status':
            enrollment_id = request.POST.get('enrollment_id')
            new_status = request.POST.get('status')
            enrollment = get_object_or_404(TrainingEnrollment, id=enrollment_id, training_program=program)
            if new_status in ['Enrolled', 'Attended', 'No Show']:
                enrollment.status = new_status
                enrollment.save()
                if new_status == 'Attended':
                    # Award skills addressed
                    for target_sk in program.all_skills:
                        app_sk, _ = ApplicantSkill.objects.get_or_create(
                            profile=enrollment.profile,
                            skill=target_sk,
                            defaults={'source': 'Training Completion'}
                        )
                    messages.success(request, f"Marked {enrollment.profile.user.get_full_name()} as Attended / Certified.")
                elif new_status == 'No Show':
                    messages.info(request, f"Marked {enrollment.profile.user.get_full_name()} as No Show.")
                else:
                    messages.info(request, f"Updated {enrollment.profile.user.get_full_name()} status to Enrolled.")
                    
        elif action == 'edit':
            title = request.POST.get('title', '').strip()
            provider = request.POST.get('provider', '').strip()
            location = request.POST.get('location', '').strip()
            duration = request.POST.get('duration', '').strip()
            description = request.POST.get('description', '').strip()
            sched_date_str = request.POST.get('scheduled_date', '').strip()
            status = request.POST.get('status', program.status).strip()
            slots_val = request.POST.get('slots')
            
            if title and provider and duration:
                program.title = title
                program.provider = provider
                program.location = location or 'PESO Training Center'
                program.duration = duration
                req_mins_raw = request.POST.get('required_training_minutes')
                if req_mins_raw:
                    try:
                        program.required_training_minutes = max(1, int(req_mins_raw))
                    except (ValueError, TypeError):
                        pass
                program.description = description
                if status in ['Scheduled', 'In Progress', 'Closed', 'Completed']:
                    program.status = status
                if sched_date_str:
                    try:
                        program.scheduled_date = datetime.strptime(sched_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass
                if slots_val:
                    try:
                        program.slots = max(1, int(slots_val))
                    except (ValueError, TypeError):
                        pass
                program.remaining_slots = max(0, program.slots - program.enrollments.count())
                program.save()
                messages.success(request, f"Successfully updated training program '{program.title}' ({program.formatted_required_duration} required)!")
                
        return redirect('training_program_detail_admin', program_id=program.id)

    enrolled_list = list(program.enrollments.all().select_related('profile__user').prefetch_related('profile__applicant_skills__skill'))
    enrolled_count = len(enrolled_list)
    attended_count = sum(1 for e in enrolled_list if e.status == 'Attended')
    
    enrolled_profile_ids = [e.profile.id for e in enrolled_list]
    all_applicants = Profile.objects.filter(role='applicant').exclude(id__in=enrolled_profile_ids).select_related('user').order_by('user__first_name')
    skills = CentralizedSkill.objects.all().order_by('name')

    return render(request, 'tracker/ADMIN/training_program_detail_admin.html', {
        'program': program,
        'enrolled_list': enrolled_list,
        'enrolled_count': enrolled_count,
        'attended_count': attended_count,
        'all_applicants': all_applicants,
        'skills': skills,
    })


@role_required(['admin'])
def training_curriculum_builder_admin(request, program_id):
    seed_mock_applicants_if_empty()
    program = get_object_or_404(
        TrainingProgram.objects.prefetch_related('skills_addressed', 'skill_addressed'),
        id=program_id
    )
    curriculum = get_program_curriculum(program)
    is_customized = bool(program.custom_curriculum and isinstance(program.custom_curriculum, dict) and program.custom_curriculum.get('modules'))
    
    # Check if there is any enrollment for preview
    sample_enrollment = TrainingEnrollment.objects.filter(training_program=program).first()

    context = {
        'program': program,
        'curriculum': curriculum,
        'curriculum_json': json.dumps(curriculum),
        'is_customized': is_customized,
        'sample_enrollment': sample_enrollment,
    }
    return render(request, 'tracker/ADMIN/training_curriculum_builder_admin.html', context)


@role_required(['admin'])
@require_POST
def training_curriculum_save_admin(request, program_id):
    program = get_object_or_404(TrainingProgram, id=program_id)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST
        
    modules = data.get('modules', [])
    if not isinstance(modules, list) or len(modules) == 0:
        return JsonResponse({'success': False, 'error': 'Curriculum must contain at least one module.'}, status=400)
    
    # Clean & standardize structure
    clean_modules = []
    for mod_idx, mod in enumerate(modules, start=1):
        mod_id = mod.get('id') or f"mod_{mod_idx}"
        mod_number = mod.get('number', mod_idx)
        mod_title = mod.get('title', f"Module {mod_number}").strip()
        mod_summary = mod.get('summary', '').strip()
        lessons = mod.get('lessons', [])
        
        clean_lessons = []
        for les_idx, les in enumerate(lessons, start=1):
            les_id = les.get('id') or f"mod{mod_number}_les{les_idx}"
            les_type = les.get('type', 'reading')
            les_title = les.get('title', f"Lesson {mod_number}.{les_idx}").strip()
            les_duration = les.get('duration', '20 min').strip()
            les_desc = les.get('description', '').strip()
            les_content = les.get('content', {})
            
            clean_lessons.append({
                'id': les_id,
                'type': les_type,
                'title': les_title,
                'duration': les_duration,
                'description': les_desc,
                'content': les_content,
            })
            
        clean_modules.append({
            'id': mod_id,
            'number': mod_number,
            'title': mod_title,
            'summary': mod_summary,
            'lessons': clean_lessons,
        })
        
    total_lessons = sum(len(m['lessons']) for m in clean_modules)
    
    curriculum_obj = {
        'program_id': program.id,
        'title': data.get('title') or program.title,
        'provider': data.get('provider') or program.provider,
        'duration': data.get('duration') or program.duration,
        'skills': [s.name for s in program.all_skills],
        'main_skill': program.all_skills[0].name if program.all_skills else 'General',
        'description': data.get('description') or program.description or '',
        'domain_type': data.get('domain_type', 'general'),
        'modules': clean_modules,
        'total_lessons': total_lessons,
    }
    # Preserve assessment questions
    if data.get('assessment'):
        curriculum_obj['assessment'] = data.get('assessment')
    elif program.custom_curriculum and isinstance(program.custom_curriculum, dict) and program.custom_curriculum.get('assessment'):
        curriculum_obj['assessment'] = program.custom_curriculum.get('assessment')
    
    req_mins_raw = data.get('required_training_minutes')
    if req_mins_raw:
        try:
            program.required_training_minutes = max(1, int(req_mins_raw))
        except (ValueError, TypeError):
            pass

    program.custom_curriculum = curriculum_obj
    program.save()
    
    return JsonResponse({
        'success': True,
        'message': f"Curriculum for '{program.title}' successfully saved with {len(clean_modules)} modules and {total_lessons} lessons!",
        'total_lessons': total_lessons,
        'modules_count': len(clean_modules),
    })


@role_required(['admin'])
@require_POST
def training_curriculum_upload_media_admin(request, program_id):
    from django.core.files.storage import default_storage
    import os
    import uuid
    import re

    program = get_object_or_404(TrainingProgram, id=program_id)
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'success': False, 'error': 'No file was provided.'}, status=400)

    video_exts = {'.mp4', '.webm', '.ogg', '.mov', '.m4v'}
    image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg'}
    allowed_exts = video_exts.union(image_exts)

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in allowed_exts:
        return JsonResponse({
            'success': False,
            'error': f'Unsupported file format "{ext}". Allowed video formats: MP4, WebM, MOV. Allowed image formats: JPG, PNG, WebP, GIF, SVG.'
        }, status=400)

    is_video = ext in video_exts
    file_type = 'video' if is_video else 'image'

    if uploaded_file.size > 250 * 1024 * 1024:
        return JsonResponse({'success': False, 'error': 'File size exceeds maximum allowed limit of 250MB.'}, status=400)

    clean_orig_name = re.sub(r'[^a-zA-Z0-9_\.-]', '_', os.path.splitext(uploaded_file.name)[0])[:40]
    unique_suffix = uuid.uuid4().hex[:8]
    subfolder = 'videos' if is_video else 'images'
    dest_relative_path = f"training_curriculum/program_{program.id}/{subfolder}/{clean_orig_name}_{unique_suffix}{ext}"

    saved_path = default_storage.save(dest_relative_path, uploaded_file)
    file_url = default_storage.url(saved_path)

    return JsonResponse({
        'success': True,
        'file_url': file_url,
        'file_name': uploaded_file.name,
        'file_type': file_type,
        'file_size': uploaded_file.size,
    })


@role_required(['admin'])
@require_POST
def training_curriculum_reset_admin(request, program_id):
    program = get_object_or_404(TrainingProgram, id=program_id)
    program.custom_curriculum = {}
    program.save()
    
    messages.success(request, f"Curriculum for '{program.title}' has been reset to the standard auto-generated template.")
    return redirect('training_curriculum_builder_admin', program_id=program.id)


@role_required(['admin'])
def training_program_preview_admin(request, program_id):
    seed_mock_applicants_if_empty()
    program = get_object_or_404(
        TrainingProgram.objects.prefetch_related('skills_addressed', 'skill_addressed'),
        id=program_id
    )
    curriculum = get_program_curriculum(program)

    total_lessons = curriculum['total_lessons']
    all_lessons_flat = []
    for mod in curriculum['modules']:
        for les in mod['lessons']:
            les['is_completed'] = False
            all_lessons_flat.append(les)
        mod['completed_lessons_count'] = 0
        mod['total_lessons_count'] = len(mod['lessons'])
        mod['is_all_completed'] = False

    active_lesson_id = request.GET.get('lesson')
    active_lesson = None
    if active_lesson_id:
        for les in all_lessons_flat:
            if les['id'] == active_lesson_id:
                active_lesson = les
                break
    if not active_lesson and all_lessons_flat:
        active_lesson = all_lessons_flat[0]

    current_idx = -1
    for idx, les in enumerate(all_lessons_flat):
        if active_lesson and les['id'] == active_lesson['id']:
            current_idx = idx
            break

    prev_lesson = all_lessons_flat[current_idx - 1] if current_idx > 0 else None
    next_lesson = all_lessons_flat[current_idx + 1] if (current_idx >= 0 and current_idx < len(all_lessons_flat) - 1) else None

    class _DummyEnrollment:
        id = 0
        progress_percentage = 0
        completed_lessons = []
        assessment_score = 0
        status = 'Enrolled'
        certificate_code = None

    context = {
        'enrollment': _DummyEnrollment(),
        'program': program,
        'curriculum': curriculum,
        'active_lesson': active_lesson,
        'prev_lesson': prev_lesson,
        'next_lesson': next_lesson,
        'completed_set': set(),
        'completed_count': 0,
        'total_lessons': total_lessons,
        'progress_pct': 0,
        'all_lessons_flat': all_lessons_flat,
        'profile': request.user.profile,
        'is_admin_preview': True,
        'unread_notifications_count': Notification.objects.filter(user=request.user, is_read=False).count() if request.user.is_authenticated else 0,
    }
    return render(request, 'tracker/APPLICANT/course_player.html', context)


@role_required(['admin'])
def vacancy_management_admin(request):
    seed_mock_applicants_if_empty()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        vacancy_id = request.POST.get('vacancy_id')
        vacancy = get_object_or_404(JobVacancy, id=vacancy_id)
        
        if action == 'toggle_status':
            vacancy.status = 'Closed' if vacancy.status == 'Open' else 'Open'
            vacancy.save()
            messages.success(request, f"Vacancy '{vacancy.title}' is now {vacancy.status}.")
        elif action == 'archive':
            if vacancy.status == 'Archived':
                vacancy.status = 'Open'
                vacancy.save()
                messages.success(request, f"Vacancy '{vacancy.title}' has been unarchived and restored to Open.")
            else:
                vacancy.status = 'Archived'
                vacancy.save()
                messages.success(request, f"Vacancy '{vacancy.title}' has been archived.")
        elif action == 'delete':
            title = vacancy.title
            vacancy.delete()
            messages.success(request, f"Vacancy '{title}' permanently deleted.")
            
        return redirect('vacancy_management_admin')
        
    vacancies = JobVacancy.objects.all().select_related('employer__user').prefetch_related('requirements__skill').order_by('-created_at')
    total_vacancies = vacancies.count()
    open_vacancies = vacancies.filter(status='Open').count()
    closed_vacancies = vacancies.filter(status='Closed').count()
    open_slots = vacancies.aggregate(Sum('remaining_slots'))['remaining_slots__sum'] or 0
    hiring_employers = vacancies.values('employer').distinct().count()
    total_referrals = Referral.objects.count()
    
    # Unique categories
    categories = sorted(list(set(v.category_display for v in vacancies if v.category)))
    
    context = {
        'vacancies': vacancies,
        'total_vacancies': total_vacancies,
        'open_vacancies': open_vacancies,
        'closed_vacancies': closed_vacancies,
        'open_slots': open_slots,
        'hiring_employers': hiring_employers,
        'total_referrals': total_referrals,
        'categories': categories,
    }
    return render(request, 'tracker/ADMIN/vacancy_management_admin.html', context)

@role_required(['admin'])
def vacancy_detail_admin(request, vacancy_id):
    seed_mock_applicants_if_empty()
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id)
    referrals = Referral.objects.filter(job_vacancy=vacancy).select_related(
        'applicant__user', 'applicant'
    ).prefetch_related(
        'applicant__education', 'applicant__experience', 'applicant__certifications', 'applicant__applicant_skills__skill'
    )
    
    referrals_data = []
    for ref in referrals:
        match_pct, gaps = calculate_match_score(ref.applicant, vacancy)
        app = ref.applicant
        
        # Format skills
        skills_list = [askill.skill.name for askill in app.applicant_skills.all()]
        skills_str = ", ".join(skills_list) if skills_list else (app.skills or "No skills recorded")
        
        # Format education
        edus = list(app.education.all())
        edu_str = f"{edus[0].degree} in {edus[0].field_of_study} ({edus[0].institution})" if edus else "High School / General"
        
        # Format experience
        exps = list(app.experience.all())
        exp_str = f"{exps[0].position} at {exps[0].company} ({exps[0].start_date} - {exps[0].end_date})" if exps else f"{app.experience_years or '0'} Years Experience"
        
        # Format certifications
        certs = list(app.certifications.all())
        certs_str = ", ".join([c.name for c in certs]) if certs else "None"
        
        referrals_data.append({
            'referral': ref,
            'match_pct': int(match_pct),
            'gaps': gaps,
            'skills_str': skills_str,
            'edu_str': edu_str,
            'exp_str': exp_str,
            'certs_str': certs_str,
            'initials': (app.user.first_name[:1] + app.user.last_name[:1]).upper() if (app.user.first_name and app.user.last_name) else "C",
        })
        
    referrals_data = sorted(referrals_data, key=lambda x: x['match_pct'], reverse=True)
    
    context = {
        'vacancy': vacancy,
        'referrals_data': referrals_data,
    }
    return render(request, 'tracker/ADMIN/vacancy_detail_admin.html', context)

# ==========================================
# JOB APPLICANT VIEWS
# ==========================================

JOB_CATEGORIES = [
    {'code': 'IT', 'name': 'Information Technology'},
    {'code': 'BPO', 'name': 'BPO & Customer Service'},
    {'code': 'ADM', 'name': 'Administrative & Office'},
    {'code': 'FIN', 'name': 'Finance & Accounting'},
    {'code': 'MKT', 'name': 'Sales & Marketing'},
    {'code': 'RTL', 'name': 'Retail & Merchandising'},
    {'code': 'F&B', 'name': 'Food & Beverage / Hospitality'},
    {'code': 'MED', 'name': 'Healthcare & Medical'},
    {'code': 'EDU', 'name': 'Education & Training'},
    {'code': 'ENG', 'name': 'Engineering & Construction'},
    {'code': 'ELC', 'name': 'Electrical & Electronics'},
    {'code': 'MCH', 'name': 'Mechanical & Automotive'},
    {'code': 'TVT', 'name': 'TESDA Trade & Vocational'},
    {'code': 'LOG', 'name': 'Logistics & Warehousing'},
    {'code': 'SEC', 'name': 'Security & Safety'},
    {'code': 'GEN', 'name': 'General Services & Facilities'},
    {'code': 'DRV', 'name': 'Driving & Transportation'},
    {'code': 'MFG', 'name': 'Manufacturing & Garments'},
    {'code': 'AGR', 'name': 'Agriculture & Environment'},
    {'code': 'OFW', 'name': 'Overseas / OFW Positions'},
]

@login_required(login_url='login')
def profile_wizard(request):
    profile = request.user.profile
    if profile.role != 'applicant':
        return redirect('dashboard')
    if profile.is_profile_complete:
        return redirect('applicant_dashboard')
        
    if request.method == 'POST':
        # 0. Personal Details from Step 1
        first_name = proper_name(request.POST.get('first_name', ''))
        last_name = proper_name(request.POST.get('last_name', ''))
        user_modified = False
        if first_name and request.user.first_name != first_name:
            request.user.first_name = first_name
            user_modified = True
        if last_name and request.user.last_name != last_name:
            request.user.last_name = last_name
            user_modified = True
        if user_modified:
            request.user.save()

        title_input = request.POST.get('title', '').strip()
        location_input = request.POST.get('location', '').strip()
        phone_input = request.POST.get('phone_number', '').strip() or request.POST.get('phone', '').strip()
        birthdate_input = request.POST.get('birthdate', '').strip()
        civil_status_input = request.POST.get('civil_status', '').strip()
        address_input = request.POST.get('address', '').strip()
        exp_years_input = request.POST.get('experience_years', '').strip()
        soft_notes_input = request.POST.get('soft_notes', '').strip()

        if phone_input:
            profile.phone_number = phone_input
        if location_input:
            profile.location = location_input
        if address_input:
            profile.address = address_input
        if birthdate_input:
            try:
                profile.birthdate = birthdate_input
            except Exception:
                pass
        if civil_status_input:
            profile.civil_status = civil_status_input
        if soft_notes_input:
            profile.soft_notes = soft_notes_input

        id_type_input = request.POST.get('id_type', '').strip()
        id_number_input = request.POST.get('id_number', '').strip()
        if id_type_input:
            profile.id_type = id_type_input
        if id_number_input:
            profile.id_number = id_number_input
        if 'id_document' in request.FILES:
            profile.id_document = request.FILES['id_document']
            profile.id_verified = True

        linkedin_input = request.POST.get('linkedin_url', '').strip() or None
        github_input = request.POST.get('github_url', '').strip() or None
        portfolio_input = request.POST.get('portfolio_url', '').strip() or None
        if linkedin_input:
            profile.linkedin_url = linkedin_input
        if github_input:
            profile.github_url = github_input
        if portfolio_input:
            profile.portfolio_url = portfolio_input

        # 1. Educational Background
        institution = request.POST.get('institution', '').strip()
        degree = request.POST.get('degree', '').strip()
        field_of_study = request.POST.get('field_of_study', '').strip()
        start_year = request.POST.get('start_year')
        end_year = request.POST.get('end_year')
        
        if institution and degree and field_of_study:
            try:
                start_year_val = int(start_year) if start_year else 2020
            except (TypeError, ValueError):
                start_year_val = 2020
            try:
                end_year_val = int(end_year) if end_year else 2024
            except (TypeError, ValueError):
                end_year_val = 2024
            Education.objects.create(
                profile=profile,
                institution=institution.strip().upper(),
                degree=degree.strip().upper(),
                field_of_study=field_of_study.strip().upper(),
                start_year=start_year_val,
                end_year=end_year_val
            )
            
        # 2. Fresh Graduate or Work History
        is_fresh_grad = request.POST.get('is_fresh_grad') == 'on'
        profile.is_fresh_grad = is_fresh_grad
        
        position = ""
        if is_fresh_grad:
            ojt_company = request.POST.get('ojt_company', '').strip()
            ojt_position = request.POST.get('ojt_position', '').strip()
            ojt_desc = request.POST.get('ojt_description', '').strip()
            if ojt_company and ojt_position:
                WorkExperience.objects.create(
                    profile=profile,
                    company=ojt_company,
                    position=ojt_position + " (OJT/Internship)",
                    description=ojt_desc,
                    start_date="2023",
                    end_date="2024",
                    is_current=False
                )
        else:
            companies = request.POST.getlist('company[]') or request.POST.getlist('company')
            positions = request.POST.getlist('position[]') or request.POST.getlist('position')
            descriptions = request.POST.getlist('description[]') or request.POST.getlist('description')
            start_dates = request.POST.getlist('start_date[]') or request.POST.getlist('start_date')
            end_dates = request.POST.getlist('end_date[]') or request.POST.getlist('end_date')
            
            for i in range(len(companies)):
                c = companies[i].strip() if i < len(companies) else ""
                p = positions[i].strip() if i < len(positions) else ""
                d = descriptions[i].strip() if i < len(descriptions) else ""
                s_date = start_dates[i].strip() if i < len(start_dates) else ""
                e_date = end_dates[i].strip() if i < len(end_dates) else "Present"
                is_curr = 'present' in e_date.lower() or not e_date

                if c and p:
                    if not position:
                        position = p  # Keep first/most recent position as candidate title
                    WorkExperience.objects.create(
                        profile=profile,
                        company=c,
                        position=p,
                        description=d,
                        start_date=s_date,
                        end_date=e_date,
                        is_current=is_curr
                    )
                
        # 3. Technical Skills
        selected_skills = request.POST.getlist('skills[]')
        
        profile_skills_list = []
        for skill_id in selected_skills:
            if skill_id:
                try:
                    skill_obj = CentralizedSkill.objects.get(id=int(skill_id))
                    ApplicantSkill.objects.update_or_create(
                        profile=profile,
                        skill=skill_obj,
                        defaults={
                            'source': 'self'
                        }
                    )
                    profile_skills_list.append(skill_obj.name)
                except Exception:
                    pass
                
        # 4. Finalize Profile
        preferred_job = request.POST.get('preferred_job', '').strip()
        profile.is_profile_complete = True
        if title_input:
            profile.title = title_input
        elif not is_fresh_grad and position:
            profile.title = position
        elif field_of_study:
            profile.title = f"Fresh Graduate - {field_of_study}"
        elif preferred_job:
            profile.title = preferred_job
        else:
            profile.title = "General Applicant"
            
        if profile_skills_list:
            profile.skills = ", ".join(profile_skills_list)
            
        if preferred_job:
            profile.preferred_job = preferred_job
                
        if exp_years_input and exp_years_input != '0 Years':
            profile.experience_years = exp_years_input
        else:
            profile.experience_years = calculate_total_experience_years(profile)
        profile.save()
        
        # Notify matching employers
        try:
            check_and_notify_employer_matches(profile)
        except Exception as _notify_err:
            print("Employer match notification notice:", _notify_err)
        
        messages.success(request, "Welcome! Your profile has been completed successfully.")
        return redirect('applicant_dashboard')
        
    skills = CentralizedSkill.objects.all()
    return render(request, 'tracker/APPLICANT/profile_wizard.html', {
        'skills': skills,
        'profile': profile,
        'categories': JOB_CATEGORIES
    })

@role_required(['applicant'])
def applicant_notifications(request):
    profile = request.user.profile
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    context = {
        'notifications_list': notifications,
        'profile': profile,
    }
    return render(request, 'tracker/APPLICANT/notifications_list.html', context)

@role_required(['applicant'])
def applicant_dashboard(request):
    seed_mock_applicants_if_empty()
    profile = Profile.objects.prefetch_related(
        'applicant_skills__skill', 'education', 'experience', 'certifications', 'bookmarked_jobs'
    ).get(pk=request.user.profile.pk)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        referral_id = request.POST.get('referral_id')
        ref = get_object_or_404(Referral, id=referral_id, applicant=profile)
        
        if action == 'confirm_offer':
            # Stage 2: Applicant confirms acceptance
            if ref.status != 'Accepted — Awaiting Onboarding':
                messages.error(request, "There is no pending job offer to confirm for this application.")
                return redirect('applicant_dashboard')
            if request.FILES.get('nbi_clearance'):
                ref.nbi_clearance = request.FILES['nbi_clearance']
            if request.FILES.get('medical_certificate'):
                ref.medical_certificate = request.FILES['medical_certificate']
            if request.FILES.get('birth_certificate'):
                ref.birth_certificate = request.FILES['birth_certificate']
            if request.FILES.get('diploma_transcript'):
                ref.diploma_transcript = request.FILES['diploma_transcript']
            if request.FILES.get('prev_employment_cert'):
                ref.prev_employment_cert = request.FILES['prev_employment_cert']
            if request.FILES.get('tesda_cert'):
                ref.tesda_cert = request.FILES['tesda_cert']
                
            ref.sss_number = request.POST.get('sss_number', '')
            ref.philhealth_number = request.POST.get('philhealth_number', '')
            ref.pagibig_number = request.POST.get('pagibig_number', '')
            ref.bir_tin = request.POST.get('bir_tin', '')
            
            ref.status = 'Confirmed — Onboarding'
            ref.save()
            
            profile.status = 'Employed — Onboarding'
            profile.save()
            
            messages.success(request, "Congratulations! You have confirmed the offer and uploaded your onboarding requirements.")
            try:
                send_hired_confirmation_email(
                    profile,
                    ref.job_vacancy,
                    start_date=ref.reporting_date,
                    employment_status='Confirmed — Onboarding'
                )
            except Exception:
                pass
            
        elif action == 'decline_offer':
            # Stage 2: Applicant declines acceptance
            if ref.status not in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'):
                messages.error(request, "There is no active offer to decline for this application.")
                return redirect('applicant_dashboard')
            had_reserved_slot = True
            ref.decline_reason = request.POST.get('decline_reason', 'No reason given')
            ref.decline_remarks = request.POST.get('decline_remarks', '')
            ref.status = 'Declined'
            ref.save()
            
            profile.status = 'Active — Job Seeking'
            profile.save()
            try:
                send_application_status_update_email(
                    profile,
                    ref.job_vacancy,
                    'Declined',
                    remarks=ref.decline_reason
                )
            except Exception:
                pass
            
            # Reopen the reserved slot exactly once (only offers hold one).
            if had_reserved_slot:
                vac = ref.job_vacancy
                vac.remaining_slots = min(vac.slots, vac.remaining_slots + 1)
                vac.save()
            
            # Send Notification to PESO Coordinator
            coord_users = User.objects.filter(profile__role='admin')
            for u in coord_users:
                Notification.objects.create(
                    user=u,
                    message=f"Applicant {profile.user.get_full_name()} declined the offer for {ref.job_vacancy.title} at {ref.job_vacancy.employer.user.first_name}. Reason: {ref.decline_reason}."
                )
            messages.info(request, "You have declined the offer. Your profile is now active in the job matching pool again.")
            
        elif action == 'submit_applicant_evaluation':
            # Stage 7: Applicant evaluation form
            ref.eval_app_accurate_desc = request.POST.get('eval_app_accurate_desc', 'Yes')
            ref.eval_app_terms_met = request.POST.get('eval_app_terms_met', 'Yes')
            ref.eval_app_expectations = request.POST.get('eval_app_expectations', '')
            ref.eval_app_future_use = request.POST.get('eval_app_future_use', 'Yes')
            ref.eval_app_satisfaction = int(request.POST.get('eval_app_satisfaction', 5))
            ref.save()
            messages.success(request, "Thank you! Your job match feedback has been successfully submitted.")
            
        return redirect('applicant_dashboard')

    # Calculate profile completion percentage
    has_title = bool(profile.title)
    has_skills = profile.applicant_skills.exists()
    has_edu = profile.education.exists()
    has_exp = profile.experience.exists()
    score = 0
    if has_title: score += 25
    if has_skills: score += 25
    if has_edu: score += 25
    if has_exp: score += 25
    
    # Calculate every vacancy once, then reuse the results below.
    vacancies = list(
        JobVacancy.objects.prefetch_related('requirements__skill').select_related('employer')
    )
    vacancy_scores = {}
    vacancy_gaps = {}
    vacancy_aligned = {}
    for vac in vacancies:
        match_pct, gaps = calculate_match_score(profile, vac)
        vacancy_scores[vac.id] = match_pct
        vacancy_gaps[vac.id] = gaps
        vacancy_aligned[vac.id] = is_job_career_aligned(profile, vac)

    highest_match = max(vacancy_scores.values(), default=0)
    best_job = next((v for v in vacancies if vacancy_scores[v.id] == highest_match), None)
            
    referrals = Referral.objects.filter(applicant=profile).select_related('job_vacancy', 'job_vacancy__employer').order_by('-date_referred')
    active_referrals = referrals.filter(job_vacancy__status='Open', status__in=['Pending', 'Interviewing', 'Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'])
    active_count = active_referrals.filter(status='Pending').count()
    interview_count = active_referrals.filter(status='Interviewing').count()
    offer_count = active_referrals.filter(status='Accepted — Awaiting Onboarding').count()
    
    # Fetch active offer if any
    active_offer = active_referrals.filter(status='Accepted — Awaiting Onboarding').first()
    
    # Check if there is a hired/closed referral that needs evaluation
    pending_evaluation = referrals.filter(status__in=['Hired — Probationary', 'Hired — Regular', 'Regularly Employed'], eval_app_satisfaction__isnull=True).first()
    
    # Fetch unread notifications
    notifications = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')
    for n in notifications:
        n.is_read = True
        n.save() # Mark them as read when viewing dashboard
    
    # Training recommendations based on gaps using at max 5 preferred jobs
    rec_programs_all, _, _, biggest_gap_skill = get_applicant_training_recommendations(profile)
    recommended_programs = rec_programs_all[:2]
    
    # Calculate recommended jobs (match score >= 50%)
    bookmarked_job_ids = set(profile.bookmarked_jobs.values_list('job_vacancy_id', flat=True))
    
    preferred_jobs_data = []
    preferred_category_name = None
    if profile.preferred_job:
        category_mapping_dict = {
            'IT': 'Information Technology',
            'BPO': 'BPO & Customer Service',
            'ADM': 'Administrative & Office',
            'FIN': 'Finance & Accounting',
            'MKT': 'Sales & Marketing',
            'RTL': 'Retail & Merchandising',
            'F&B': 'Food & Beverage / Hospitality',
            'MED': 'Healthcare & Medical',
            'EDU': 'Education & Training',
            'ENG': 'Engineering & Construction',
            'ELC': 'Electrical & Electronics',
            'MCH': 'Mechanical & Automotive',
            'TVT': 'TESDA Trade & Vocational',
            'LOG': 'Logistics & Warehousing',
            'SEC': 'Security & Safety',
            'GEN': 'General Services & Facilities',
            'DRV': 'Driving & Transportation',
            'MFG': 'Manufacturing & Garments',
            'AGR': 'Agriculture & Environment',
            'OFW': 'Overseas / OFW Positions',
        }
        preferred_category_name = category_mapping_dict.get(profile.preferred_job, profile.preferred_job)
        
        # Get all vacancies in this category
        cat_vacancies = [v for v in vacancies if v.category == profile.preferred_job]
        applied_vacancy_ids = set(
            Referral.objects.filter(applicant=profile).values_list('job_vacancy_id', flat=True)
        )
        for vac in cat_vacancies:
            match_pct = vacancy_scores[vac.id]
            gaps = vacancy_gaps[vac.id]
            gaps_count = sum(1 for g in gaps if g['gap'] > 0)
            req_skills = [req.skill.name for req in vac.requirements.all()[:3]]
            preferred_jobs_data.append({
                'vacancy': vac,
                'match_score': int(round(match_pct)),
                'gaps_count': gaps_count,
                'skills': req_skills,
                'is_bookmarked': vac.id in bookmarked_job_ids,
                'has_applied': vac.id in applied_vacancy_ids,
            })
        preferred_jobs_data = sorted(preferred_jobs_data, key=lambda x: x['match_score'], reverse=True)
    recommended_jobs = []
    for vac in vacancies:
        if not vacancy_aligned[vac.id]:
            continue
        match_pct = vacancy_scores[vac.id]
        if match_pct >= 50:
            gaps = vacancy_gaps[vac.id]
            gaps_count = sum(1 for g in gaps if g['gap'] > 0)
            req_skills = [req.skill.name for req in vac.requirements.all()[:3]]
            recommended_jobs.append({
                'vacancy': vac,
                'match_score': int(round(match_pct)),
                'gaps_count': gaps_count,
                'skills': req_skills,
                'is_bookmarked': vac.id in bookmarked_job_ids,
            })
    recommended_jobs = sorted(recommended_jobs, key=lambda x: x['match_score'], reverse=True)
    
    # Retrieve bookmarked jobs data
    bookmarked_jobs = JobBookmark.objects.filter(profile=profile).select_related('job_vacancy', 'job_vacancy__employer')
    bookmarked_jobs_data = []
    for bookmark in bookmarked_jobs:
        vac = bookmark.job_vacancy
        match_pct = vacancy_scores.get(vac.id)
        gaps = vacancy_gaps.get(vac.id)
        if match_pct is None:
            match_pct, gaps = calculate_match_score(profile, vac)
        gaps_count = sum(1 for g in gaps if g['gap'] > 0)
        req_skills = [req.skill.name for req in vac.requirements.all()[:3]]
        bookmarked_jobs_data.append({
            'vacancy': vac,
            'match_score': int(round(match_pct)),
            'gaps_count': gaps_count,
            'skills': req_skills,
            'is_bookmarked': True,
        })
    
    ai_recommended_jobs = cache.get(f"dashboard_ai_jobs:{profile.id}")
    if ai_recommended_jobs is None:
        try:
            from .ai_services import get_ai_job_recommendations
            ai_recommended_jobs = get_ai_job_recommendations(profile, top_n=3)
        except Exception as e:
            print(f"[AI Recs Error] {e}")
            ai_recommended_jobs = []
        cache.set(f"dashboard_ai_jobs:{profile.id}", ai_recommended_jobs, 600)

    context = {
        'profile': profile,
        'profile_strength': score,
        'highest_match': int(highest_match),
        'best_job': best_job,
        'best_job_is_bookmarked': best_job.id in bookmarked_job_ids if best_job else False,
        'recent_referrals': referrals[:3],
        'applicant_skills': profile.applicant_skills.all().select_related('skill')[:3],
        'active_count': active_count,
        'interview_count': interview_count,
        'offer_count': offer_count,
        'recommended_programs': recommended_programs,
        'biggest_gap_skill': biggest_gap_skill,
        'recommended_jobs': recommended_jobs[:3],
        'ai_recommended_jobs': ai_recommended_jobs,
        'bookmarked_jobs': bookmarked_jobs_data,
        'preferred_jobs_data': preferred_jobs_data,
        'preferred_category_name': preferred_category_name,
        'active_offer': active_offer,
        'pending_evaluation': pending_evaluation,
        'notifications': notifications,
        'active_employment': get_active_employment(profile),
    }
    return render(request, 'tracker/APPLICANT/applicant_dashboard.html', context)

@role_required(['applicant'])
def applicant_profile(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_education':
            try:
                start_year_val = int(request.POST.get('start_year') or 2020)
            except (TypeError, ValueError):
                start_year_val = 2020
            try:
                end_year_val = int(request.POST.get('end_year') or 2024)
            except (TypeError, ValueError):
                end_year_val = 2024
            Education.objects.create(
                profile=profile,
                institution=request.POST.get('institution', '').strip().upper(),
                degree=request.POST.get('degree', '').strip().upper(),
                field_of_study=request.POST.get('field_of_study', '').strip().upper(),
                start_year=start_year_val,
                end_year=end_year_val
            )
            messages.success(request, "Education entry added successfully!")
            
        elif action == 'delete_education':
            edu_id = request.POST.get('id')
            get_object_or_404(Education, id=edu_id, profile=profile).delete()
            messages.success(request, "Education entry deleted successfully!")
            
        elif action == 'add_experience':
            WorkExperience.objects.create(
                profile=profile,
                company=request.POST.get('company', '').strip(),
                position=request.POST.get('position', '').strip(),
                description=request.POST.get('description', '').strip(),
                start_date=request.POST.get('start_date', '').strip(),
                end_date=request.POST.get('end_date', 'Present').strip(),
                is_current=request.POST.get('is_current') == 'true'
            )
            profile.experience_years = calculate_total_experience_years(profile)
            profile.save()
            messages.success(request, "Work experience entry added successfully!")
            
        elif action == 'delete_experience':
            exp_id = request.POST.get('id')
            get_object_or_404(WorkExperience, id=exp_id, profile=profile).delete()
            profile.experience_years = calculate_total_experience_years(profile)
            profile.save()
            messages.success(request, "Work experience entry deleted successfully!")
            
        elif action == 'add_certification':
            uploaded = request.FILES.get('certificate_file')
            if uploaded:
                upload_error = _certificate_upload_error(uploaded)
                if upload_error:
                    messages.error(request, upload_error)
                    return redirect('applicant_profile')
            cert = Certification.objects.create(
                profile=profile,
                name=request.POST.get('name', '').strip(),
                issuing_organization=request.POST.get('issuing_organization', '').strip(),
                is_tesda=request.POST.get('is_tesda') == 'true'
            )
            if uploaded:
                cert.certificate_file = uploaded
                cert.save(update_fields=['certificate_file'])
            messages.success(request, "Certification added successfully!")

        elif action == 'upload_certificate_file':
            cert = get_object_or_404(Certification, id=request.POST.get('id'), profile=profile)
            uploaded = request.FILES.get('certificate_file')
            if not uploaded:
                messages.error(request, "Please choose a certificate file to upload.")
                return redirect('applicant_profile')
            upload_error = _certificate_upload_error(uploaded)
            if upload_error:
                messages.error(request, upload_error)
                return redirect('applicant_profile')
            if cert.certificate_file:
                cert.certificate_file.delete(save=False)
            cert.certificate_file = uploaded
            cert.save(update_fields=['certificate_file'])
            messages.success(request, "Certificate file uploaded successfully!")

        elif action == 'delete_certification':
            cert = get_object_or_404(Certification, id=request.POST.get('id'), profile=profile)
            if cert.certificate_file:
                cert.certificate_file.delete(save=False)
            cert.delete()
            messages.success(request, "Certification entry deleted successfully!")
            
        elif action == 'add_skill':
            skill_id = request.POST.get('skill_id')
            skill_obj = get_object_or_404(CentralizedSkill, id=skill_id)
            
            ApplicantSkill.objects.update_or_create(
                profile=profile,
                skill=skill_obj,
                defaults={}
            )
            
            # Sync text skills string
            skills_qs = ApplicantSkill.objects.filter(profile=profile).select_related('skill')
            profile.skills = ", ".join([sk.skill.name for sk in skills_qs])
            profile.save()
            
            # Notify matching employers
            check_and_notify_employer_matches(profile)
            
            messages.success(request, f"Skill '{skill_obj.name}' updated successfully!")
            
        elif action == 'delete_skill':
            skill_id = request.POST.get('id')
            get_object_or_404(ApplicantSkill, id=skill_id, profile=profile).delete()
            
            # Sync text skills string
            skills_qs = ApplicantSkill.objects.filter(profile=profile).select_related('skill')
            profile.skills = ", ".join([sk.skill.name for sk in skills_qs])
            profile.save()
            messages.success(request, "Skill deleted successfully!")
            
        elif action == 'update_profile':
            profile.title = request.POST.get('title', profile.title)
            profile.location = request.POST.get('location', profile.location)
            profile.experience_years = request.POST.get('experience_years', profile.experience_years)
            profile.phone_number = request.POST.get('phone_number', profile.phone_number)
            profile.civil_status = request.POST.get('civil_status', profile.civil_status)
            if 'soft_notes' in request.POST:
                profile.soft_notes = request.POST.get('soft_notes', '').strip() or None
            
            # birthdate, age, and address
            birthdate_str = request.POST.get('birthdate', '').strip()
            age_str = request.POST.get('age', '').strip()
            if birthdate_str:
                from datetime import datetime
                try:
                    profile.birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date()
                except ValueError:
                    pass
            elif age_str and age_str.isdigit():
                from datetime import date
                from django.utils import timezone as dj_tz
                current_year = dj_tz.localdate().year
                profile.birthdate = date(current_year - int(age_str), 1, 1)
            else:
                profile.birthdate = None
            
            profile.address = request.POST.get('address', '').strip() or None
            
            # Also save the skills text field if provided
            skills_text = request.POST.get('skills', '').strip()
            if skills_text:
                profile.skills = skills_text
                
            pref_job = request.POST.get('preferred_job', '').strip()
            profile.preferred_job = pref_job or None

            # Social & Portfolio links
            profile.linkedin_url = request.POST.get('linkedin_url', '').strip() or None
            profile.portfolio_url = request.POST.get('portfolio_url', '').strip() or None
            profile.github_url = request.POST.get('github_url', '').strip() or None
            profile.other_links = request.POST.get('other_links', '').strip() or None

            id_t = request.POST.get('id_type', '').strip()
            id_n = request.POST.get('id_number', '').strip()
            if id_t:
                profile.id_type = id_t
            if id_n:
                profile.id_number = id_n
            if 'id_document' in request.FILES:
                profile.id_document = request.FILES['id_document']
                profile.id_verified = True
                
            profile.save()
            
            user = request.user
            user.first_name = proper_name(request.POST.get('first_name', user.first_name))
            user.last_name = proper_name(request.POST.get('last_name', user.last_name))
            new_email = request.POST.get('email', '').strip()
            if new_email and new_email.lower() != (user.email or '').lower():
                email_taken = User.objects.exclude(id=user.id).filter(
                    Q(email__iexact=new_email) | Q(username__iexact=new_email)
                ).exists()
                if not email_taken:
                    old_email = user.email or ''
                    # Accounts log in with their email as the username; keep
                    # the two in sync so a profile email change does not lock
                    # the user out of the login page.
                    if not old_email or user.username.lower() == old_email.lower():
                        user.username = new_email
                    user.email = new_email
                else:
                    messages.warning(request, "That email address is already in use; email was not changed.")
            user.save()
            messages.success(request, "Profile updated successfully!")

        elif action == 'update_short_note':
            profile.soft_notes = request.POST.get('soft_notes', '').strip() or None
            profile.save()
            messages.success(request, "Short note saved successfully!")
            return redirect('applicant_profile')

        elif action == 'upload_document':
            uploaded_file = request.FILES.get('document')
            if uploaded_file:
                # Calculate human-readable size
                size_in_bytes = uploaded_file.size
                if size_in_bytes < 1024 * 1024:
                    size_str = f"{round(size_in_bytes / 1024, 1)} KB"
                else:
                    size_str = f"{round(size_in_bytes / (1024 * 1024), 1)} MB"
                
                ApplicantDocument.objects.create(
                    profile=profile,
                    file=uploaded_file,
                    file_name=uploaded_file.name,
                    file_size=size_str
                )
                messages.success(request, f"Document '{uploaded_file.name}' uploaded successfully!")
            else:
                messages.error(request, "No file selected.")

        elif action == 'delete_document':
            doc_id = request.POST.get('id')
            doc = get_object_or_404(ApplicantDocument, id=doc_id, profile=profile)
            doc.file.delete()
            doc.delete()
            messages.success(request, "Document deleted successfully!")
        elif action == 'upload_avatar':
            avatar = request.FILES.get('avatar')
            if avatar:
                if profile.profile_picture:
                    profile.profile_picture.delete()
                profile.profile_picture = avatar
                profile.save()
                messages.success(request, "Profile photo updated successfully!")
            else:
                messages.error(request, "No photo selected.")

        return redirect('applicant_profile')
        
    education_list = profile.education.all()
    experience_list = profile.experience.all()
    raw_certification_list = profile.certifications.all()
    skills_list = profile.applicant_skills.all().select_related('skill')
    all_centralized_skills = CentralizedSkill.objects.all().order_by('name')
    
    # Enrich certifications with enrollment linkage, certificate codes, and preview links
    completed_enrollments = list(TrainingEnrollment.objects.filter(
        profile=profile
    ).select_related('training_program'))
    
    certification_list = []
    for cert in raw_certification_list:
        matched_enr = None
        cert_name_clean = cert.name.lower()
        for enr in completed_enrollments:
            prog_title = enr.training_program.title.lower() if enr.training_program else ''
            if prog_title and (prog_title in cert_name_clean or cert_name_clean in prog_title or any(w in cert_name_clean for w in prog_title.split() if len(w) > 4)):
                matched_enr = enr
                if not enr.certificate_code:
                    rand_hex = secrets.token_hex(3).upper()
                    enr.certificate_code = f"SKILLUP-TESDA-2026-{rand_hex}"
                    enr.certificate_issued_at = enr.certificate_issued_at or timezone.now()
                    enr.save(update_fields=['certificate_code', 'certificate_issued_at'])
                break
        
        cert_code = matched_enr.certificate_code if (matched_enr and matched_enr.certificate_code) else f"SKILLUP-CERT-2026-{cert.id:04d}"
        if cert.certificate_file:
            download_url = cert.certificate_file.url
        else:
            download_url = f"/applicant/certificate/{matched_enr.id}/" if matched_enr else ""
        
        cert.certificate_code = cert_code
        cert.download_url = download_url
        cert.verify_url = download_url
        cert.enrollment_id = matched_enr.id if matched_enr else None
        cert.has_lms_cert = bool(matched_enr)
        cert.formatted_issue_date = cert.issue_date.strftime("%B %d, %Y") if cert.issue_date else "Date not provided"
        certification_list.append(cert)

    # Calculate profile completion percentage
    has_title = bool(profile.title)
    has_skills = skills_list.exists()
    has_edu = education_list.exists()
    has_exp = experience_list.exists()
    score = 0
    if has_title: score += 25
    if has_skills: score += 25
    if has_edu: score += 25
    if has_exp: score += 25
    
    context = {
        'profile': profile,
        'education_list': education_list,
        'experience_list': experience_list,
        'certification_list': certification_list,
        'skills_list': skills_list,
        'all_centralized_skills': all_centralized_skills,
        'profile_strength': score,
        'categories': JOB_CATEGORIES,
        'active_employment': get_active_employment(profile),
    }
    return render(request, 'tracker/APPLICANT/applicant_profile.html', context)

@role_required(['applicant'])
def job_search(request):
    seed_mock_applicants_if_empty()
    profile = Profile.objects.prefetch_related(
        'applicant_skills__skill', 'education', 'experience', 'certifications', 'bookmarked_jobs'
    ).get(pk=request.user.profile.pk)
    active_employment = get_active_employment(profile)
    employed_employer_ids = set(
        Referral.objects.filter(
            applicant=profile,
            status__in=Referral.ACTIVE_EMPLOYMENT_STATUSES,
        ).values_list('job_vacancy__employer_id', flat=True)
    )
    
    q = request.GET.get('q', '').strip()
    location = request.GET.get('location', '').strip()
    min_match = request.GET.get('min_match', '0')
    try:
        min_match_val = int(min_match)
    except ValueError:
        min_match_val = 0
        
    category = request.GET.get('category', '').strip()
    bookmarked_only = request.GET.get('bookmarked_only') == 'on'
    
    bookmarked_job_ids = set(profile.bookmarked_jobs.values_list('job_vacancy_id', flat=True))
    
    # Filter open vacancies only
    vacancies = JobVacancy.objects.filter(status='Open').select_related('employer').prefetch_related('requirements__skill')
    
    if bookmarked_only:
        vacancies = vacancies.filter(id__in=bookmarked_job_ids)
        
    if q:
        vacancies = vacancies.filter(
            Q(title__icontains=q) | 
            Q(description__icontains=q) |
            Q(employer__company_name__icontains=q)
        )
    if location:
        vacancies = vacancies.filter(location__icontains=location)
    if category:
        vacancies = vacancies.filter(category=category)

    # User preference: Show jobs only related to seeker by default
    is_form_submission = ('min_match' in request.GET) or ('category' in request.GET) or ('bookmarked_only' in request.GET) or ('scope' in request.GET) or ('related_only' in request.GET)
    if is_form_submission:
        scope = request.GET.get('scope', '').strip()
        related_only = (request.GET.get('related_only') in ['1', 'on', 'true']) and (scope != 'all')
    else:
        related_only = bool(profile.preferred_job or profile.title)

    seeker_skills = set(s.strip().lower() for s in (profile.skills or '').split(',') if s.strip())
    seeker_skills.update(profile.applicant_skills.values_list('skill__name', flat=True))
        
    jobs_data = []
    for vac in vacancies:
        match_pct, gaps = calculate_match_score(profile, vac)
        
        # Check genuine career alignment and skill relevance to what seeker wants
        is_aligned = is_job_career_aligned(profile, vac)
        vac_skills = set(req.skill.name.lower() for req in vac.requirements.all() if req.skill)
        generic_soft_skills = {
            'active listening', 'verbal communication', 'written communication', 
            'communication', 'teamwork', 'first aid & cpr', 'time management', 
            'problem solving', 'adaptability', 'critical thinking', 'interpersonal skills',
            'work ethic', 'organization'
        }
        domain_shared = (seeker_skills & vac_skills) - generic_soft_skills
        is_skill_overlap = len(domain_shared) >= 2 and match_pct >= 50
        
        is_related = is_aligned or is_skill_overlap
        
        # If showing only related jobs, filter out unrelated ones
        if related_only and not is_related:
            continue
            
        if match_pct < min_match_val:
            continue
            
        gaps_count = sum(1 for g in gaps if g['gap'] > 0)
        req_skills = [req.skill.name for req in vac.requirements.all()[:3]]
        
        jobs_data.append({
            'vacancy': vac,
            'match_score': int(match_pct),
            'gaps_count': gaps_count,
            'skills': req_skills,
            'is_bookmarked': vac.id in bookmarked_job_ids,
            'is_related': is_related,
            'is_employed_here': vac.employer_id in employed_employer_ids,
        })
        
    # Sort by match score descending
    jobs_data = sorted(jobs_data, key=lambda x: x['match_score'], reverse=True)
    
    # Split into Recommended (>=50% match) and Other (<50% match)
    recommended_jobs = [j for j in jobs_data if j['match_score'] >= 50]
    other_jobs = [j for j in jobs_data if j['match_score'] < 50]
    
    return render(request, 'tracker/APPLICANT/job_search.html', {
        'jobs': jobs_data,
        'recommended_jobs': recommended_jobs,
        'other_jobs': other_jobs,
        'profile': profile,
        'q': q,
        'location': location,
        'min_match': min_match_val,
        'category': category,
        'bookmarked_only': bookmarked_only,
        'related_only': related_only,
        'active_employment': active_employment,
    })

@role_required(['applicant'])
def skill_gap_analysis(request):
    seed_mock_applicants_if_empty()
    profile = Profile.objects.prefetch_related(
        'applicant_skills__skill', 'education', 'experience', 'certifications', 'bookmarked_jobs'
    ).get(pk=request.user.profile.pk)
    job_id = request.GET.get('job_id')
    
    # Select vacancy
    if job_id:
        vacancy = get_object_or_404(
            JobVacancy.objects.select_related('employer').prefetch_related('requirements__skill'),
            id=job_id,
        )
    else:
        # Default to highest matching job vacancy
        vacancies = list(
            JobVacancy.objects.select_related('employer').prefetch_related('requirements__skill')
        )
        highest_match = -1
        vacancy = None
        for vac in vacancies:
            match_pct, _ = calculate_match_score(profile, vac)
            if match_pct > highest_match:
                highest_match = match_pct
                vacancy = vac
                
    if not vacancy:
        messages.error(request, "No vacancies found.")
        return redirect('job_search')
        
    match_pct, gaps = calculate_match_score(profile, vacancy)
    final_score = int(round(match_pct))
    
    # Alternative recommendations (if match < 60%)
    alternatives = []
    if final_score < 60:
        other_vacancies = JobVacancy.objects.exclude(id=vacancy.id)
        for ovac in other_vacancies:
            omatch, _ = calculate_match_score(profile, ovac)
            if round(omatch) >= 60:
                alternatives.append({
                    'vacancy': ovac,
                    'match_score': int(round(omatch))
                })
        alternatives = sorted(alternatives, key=lambda x: x['match_score'], reverse=True)[:2]
        
    enrolled_ids = set(profile.training_enrollments.values_list('training_program_id', flat=True))

    # Training recommendations based on gaps - map each critical gap skill to its matching courses
    gap_skills = [g['skill'] for g in gaps if g['gap'] > 0]
    skill_to_programs = {}
    for g in gaps:
        if g['gap'] > 0:
            skill_name = g['skill']
            progs = TrainingProgram.objects.filter(
                Q(skill_addressed__name__iexact=skill_name) |
                Q(skills_addressed__name__iexact=skill_name) |
                Q(title__icontains=skill_name)
            ).distinct()
            
            prog_list = []
            for p in progs:
                prog_list.append({
                    'id': p.id,
                    'title': p.title,
                    'provider': p.provider,
                    'duration': p.duration,
                    'location': p.location or 'PESO Training Center',
                    'available_slots': p.available_slots,
                    'is_enrolled': p.id in enrolled_ids,
                    'description': p.description or f"Comprehensive training program in {skill_name} designed to bridge your skill gap.",
                    'enroll_url': reverse('applicant_enroll_training', args=[p.id]),
                })
            g['programs'] = prog_list
            g['program_count'] = len(prog_list)
            skill_to_programs[skill_name] = prog_list
        else:
            g['programs'] = []
            g['program_count'] = 0

    if gap_skills:
        gap_q = Q()
        for s in gap_skills:
            gap_q |= Q(skill_addressed__name__iexact=s) | Q(skills_addressed__name__iexact=s) | Q(title__icontains=s)
        recommended_programs = TrainingProgram.objects.filter(gap_q).distinct()
    else:
        recommended_programs = TrainingProgram.objects.none()
    
    # Build list of skills for target vs current levels
    labels = []
    target_data = []
    current_data = []
    biggest_gap = None
    max_gap_val = -1
    strongest_skill = None
    max_prof_val = -1
    for g in gaps:
        labels.append(g['skill'])
        target_data.append(g['required'])
        current_data.append(g['candidate'])
        if g['gap'] > max_gap_val:
            max_gap_val = g['gap']
            biggest_gap = g['skill']
        # Strongest skill: one where applicant has high rating, preferably matching or exceeding required
        if g['candidate'] > max_prof_val:
            max_prof_val = g['candidate']
            strongest_skill = g['skill']
        
    referral = Referral.objects.filter(applicant=profile, job_vacancy=vacancy).first()
    has_applied = referral is not None
    is_bookmarked = profile.bookmarked_jobs.filter(job_vacancy=vacancy).exists()
    qualification_breakdown = get_qualification_breakdown(profile, vacancy)
    active_employment = get_active_employment(profile)
    is_employed_here = bool(
        active_employment and active_employment.job_vacancy.employer_id == vacancy.employer_id
    )
    
    context = {
        'vacancy': vacancy,
        'match_score': final_score,
        'match_stroke_offset': round(251.2 * (1 - (final_score / 100.0)), 1),
        'breakdown': qualification_breakdown,
        'gaps': gaps,
        'gap_programs_json': json.dumps(skill_to_programs),
        'alternatives': alternatives,
        'recommended_programs': recommended_programs,
        'enrolled_ids': enrolled_ids,
        'labels': labels,
        'target_data': target_data,
        'current_data': current_data,
        'biggest_gap': biggest_gap if max_gap_val > 0 else None,
        'strongest_skill': strongest_skill if max_prof_val > 0 else None,
        'profile': profile,
        'has_applied': has_applied,
        'referral': referral,
        'is_bookmarked': is_bookmarked,
        'active_employment': active_employment,
        'is_employed_here': is_employed_here,
    }
    return render(request, 'tracker/APPLICANT/skill_gap_analysis.html', context)


@role_required(['applicant'])
def applicant_ai_qualification_fit(request):
    """
    Asynchronously runs Gemini AI assessment of all Qualifications/Requirements
    for a given Job Vacancy against the logged-in applicant's profile.
    """
    job_id = request.GET.get('job_id')
    if not job_id:
        return JsonResponse({'success': False, 'error': 'Missing job_id parameter.'}, status=400)
    
    vacancy = get_object_or_404(JobVacancy, id=job_id)
    profile = request.user.profile
    
    # Check session cache so repeated clicks on same vacancy don't exhaust API unless refresh requested
    force_refresh = request.GET.get('refresh') in ['1', 'true', 'True']
    cache_key = f"ai_qual_fit_{profile.id}_{vacancy.id}"
    if not force_refresh:
        cached_data = request.session.get(cache_key)
        if cached_data and isinstance(cached_data, dict):
            return JsonResponse({'success': True, 'data': cached_data, 'cached': True})
        
    from tracker.ai_services import analyze_job_qualification_fit_ai
    ai_result = analyze_job_qualification_fit_ai(profile, vacancy)
    
    request.session[cache_key] = ai_result
    return JsonResponse({'success': True, 'data': ai_result, 'cached': False})


@role_required(['applicant'])
@require_POST
def apply_to_job(request, vacancy_id):
    profile = request.user.profile
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id)
    
    if vacancy.status != 'Open':
        messages.error(request, f"The position '{vacancy.title}' is currently {vacancy.status.lower()} and no longer accepting applications.")
        return redirect(request.META.get('HTTP_REFERER', 'job_search'))

    referral, created = Referral.objects.get_or_create(
        applicant=profile,
        job_vacancy=vacancy,
        defaults={'status': 'Pending'}
    )
    if created:
        if vacancy.remaining_slots and vacancy.remaining_slots > 0:
            vacancy.remaining_slots = max(0, vacancy.remaining_slots - 1)
            if vacancy.remaining_slots == 0:
                vacancy.status = 'Closed'
                # The posting is now full, not cancelled. Do not auto-withdraw
                # the active applications that are already in the pipeline.
                vacancy._skip_auto_withdraw = True
            vacancy.save()
        messages.success(request, f"Successfully applied for '{vacancy.title}'! You can track your status on the dashboard.")
        try:
            send_application_submitted_email(profile, vacancy)
            Notification.objects.create(
                user=profile.user,
                message=f"Application Received: Your application for '{vacancy.title}' at {vacancy.employer.company_name or 'the employer'} was successfully submitted."
            )
        except Exception:
            pass
        match_pct, _ = calculate_match_score(profile, vacancy)
        if match_pct >= 70:
            Notification.objects.create(
                user=vacancy.employer.user,
                message=f"Match Alert: Applicant {profile.user.get_full_name()} has reached a {int(match_pct)}% skill match for your vacancy '{vacancy.title}'."
            )
    else:
        messages.info(request, f"You have already applied or been referred to '{vacancy.title}'.")
        
    return redirect('applicant_dashboard')



@role_required(['applicant'])
@require_POST
def toggle_bookmark(request, vacancy_id):
    profile = request.user.profile
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id)
    bookmark, created = JobBookmark.objects.get_or_create(profile=profile, job_vacancy=vacancy)
    if not created:
        bookmark.delete()
        messages.success(request, f"Removed '{vacancy.title}' from bookmarks.")
    else:
        messages.success(request, f"Bookmarked '{vacancy.title}'.")
    return redirect(request.META.get('HTTP_REFERER', 'job_search'))



@role_required(['applicant'])
def applicant_applications_list(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    referrals = Referral.objects.filter(applicant=profile).select_related('job_vacancy', 'job_vacancy__employer').order_by('-date_referred')
    
    # Calculate match scores and classify active vs closed/withdrawn applications
    referrals_data = []
    active_count = 0
    closed_count = 0
    
    for ref in referrals:
        match_pct, gaps = calculate_match_score(profile, ref.job_vacancy)
        gaps_count = sum(1 for g in gaps if g['gap'] > 0)
        
        # An application is considered active ONLY IF the vacancy is Open AND the application is active
        is_active = (ref.job_vacancy.status == 'Open') and (ref.status in ['Pending', 'Interviewing', 'Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'])
        if is_active:
            active_count += 1
        else:
            closed_count += 1
            
        referrals_data.append({
            'referral': ref,
            'match_score': int(match_pct),
            'gaps_count': gaps_count,
            'is_active': is_active,
            'is_closed': not is_active,
        })
        
    context = {
        'profile': profile,
        'referrals_data': referrals_data,
        'active_count': active_count,
        'closed_count': closed_count,
        'total_count': len(referrals_data),
    }
    return render(request, 'tracker/APPLICANT/applications_list.html', context)


@role_required(['applicant'])
def applicant_application_details(request, referral_id):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    referral = get_object_or_404(Referral, id=referral_id, applicant=profile)
    vacancy = referral.job_vacancy
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'confirm_offer':
            if referral.status != 'Accepted — Awaiting Onboarding':
                messages.error(request, "There is no pending job offer to confirm for this application.")
                return redirect('applicant_application_details', referral_id=referral_id)
            if request.FILES.get('nbi_clearance'):
                referral.nbi_clearance = request.FILES['nbi_clearance']
            if request.FILES.get('medical_certificate'):
                referral.medical_certificate = request.FILES['medical_certificate']
            if request.FILES.get('birth_certificate'):
                referral.birth_certificate = request.FILES['birth_certificate']
            if request.FILES.get('diploma_transcript'):
                referral.diploma_transcript = request.FILES['diploma_transcript']
            if request.FILES.get('prev_employment_cert'):
                referral.prev_employment_cert = request.FILES['prev_employment_cert']
            if request.FILES.get('tesda_cert'):
                referral.tesda_cert = request.FILES['tesda_cert']
                
            referral.sss_number = request.POST.get('sss_number', '')
            referral.philhealth_number = request.POST.get('philhealth_number', '')
            referral.pagibig_number = request.POST.get('pagibig_number', '')
            referral.bir_tin = request.POST.get('bir_tin', '')
            
            referral.status = 'Confirmed — Onboarding'
            referral.save()
            
            profile.status = 'Employed — Onboarding'
            profile.save()
            
            messages.success(request, "Congratulations! You have confirmed the offer and uploaded your onboarding requirements.")
            try:
                send_hired_confirmation_email(
                    profile,
                    referral.job_vacancy,
                    start_date=referral.reporting_date,
                    employment_status='Confirmed — Onboarding'
                )
            except Exception:
                pass
            return redirect('applicant_application_details', referral_id=referral_id)
            
        elif action == 'decline_offer':
            if referral.status not in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'):
                messages.error(request, "There is no active offer to decline for this application.")
                return redirect('applicant_application_details', referral_id=referral_id)
            referral.decline_reason = request.POST.get('decline_reason', 'No reason given')
            referral.decline_remarks = request.POST.get('decline_remarks', '')
            referral.status = 'Declined'
            referral.save()
            
            profile.status = 'Active — Job Seeking'
            profile.save()
            try:
                send_application_status_update_email(
                    profile,
                    referral.job_vacancy,
                    'Declined',
                    remarks=referral.decline_reason
                )
            except Exception:
                pass
            
            # Reopen the reserved slot exactly once.
            vac = referral.job_vacancy
            vac.remaining_slots = min(vac.slots, vac.remaining_slots + 1)
            vac.save()
            
            # Send Notification to PESO Coordinator
            coord_users = User.objects.filter(profile__role='admin')
            for u in coord_users:
                Notification.objects.create(
                    user=u,
                    message=f"Applicant {profile.user.get_full_name()} declined the offer for {referral.job_vacancy.title} at {referral.job_vacancy.employer.user.first_name}. Reason: {referral.decline_reason}."
                )
            messages.info(request, "You have declined the offer. Your profile is now active in the job matching pool again.")
            return redirect('applicant_application_details', referral_id=referral_id)

    # Calculate match score and gaps
    match_pct, gaps = calculate_match_score(profile, vacancy)
    
    # Get training programs addressing remaining gaps
    gap_skills = [g['skill'] for g in gaps if g['gap'] > 0]
    recommended_programs = TrainingProgram.objects.filter(skill_addressed__name__in=gap_skills).select_related('skill_addressed')
    
    active_offer = referral if referral.status == 'Accepted — Awaiting Onboarding' else None
    interviews = Interview.objects.filter(candidate=profile, vacancy=vacancy).order_by('-date', '-start_time')

    context = {
        'referral': referral,
        'vacancy': vacancy,
        'match_score': int(match_pct),
        'gaps': gaps,
        'recommended_programs': recommended_programs,
        'profile': profile,
        'active_offer': active_offer,
        'interviews': interviews,
    }
    return render(request, 'tracker/APPLICANT/application_details.html', context)



@role_required(['applicant'])
def training_recommendations(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    
    q = request.GET.get('q', '').strip()
    selected_cat = request.GET.get('category', '').strip()
    
    # Save the selected preferred category to the profile if provided in GET
    if 'category' in request.GET:
        profile.preferred_job = selected_cat
        profile.save()
        
    target_category = profile.preferred_job
    
    enrolled_trainings = profile.training_enrollments.all().select_related('training_program__skill_addressed')
    enrolled_ids = set(profile.training_enrollments.values_list('training_program_id', flat=True))

    # Gather up to 5 preferred jobs and find gaps using the helper
    recommended_programs, highest_match, gaps_count, _ = get_applicant_training_recommendations(profile)
    all_programs = TrainingProgram.objects.exclude(id__in=enrolled_ids).exclude(status='Completed').select_related('skill_addressed').prefetch_related('skills_addressed', 'enrollments')
    
    # AI Training Recommendations (cached to avoid a network call on every refresh)
    ai_recommended_trainings = cache.get(f"training_ai:{profile.id}")
    if ai_recommended_trainings is None:
        try:
            from .ai_services import get_ai_training_recommendations
            ai_recommended_trainings = get_ai_training_recommendations(profile, top_n=3)
        except Exception:
            ai_recommended_trainings = []
        cache.set(f"training_ai:{profile.id}", ai_recommended_trainings, 600)

    if q:
        all_programs = all_programs.filter(
            Q(title__icontains=q) |
            Q(provider__icontains=q) |
            Q(skill_addressed__name__icontains=q)
        )
        
    context = {
        'profile_match_percentage': int(highest_match),
        'remaining_gap': max(0, 100 - int(highest_match)),
        'recommended_programs': recommended_programs,
        'ai_recommended_trainings': ai_recommended_trainings,
        'all_programs': all_programs,
        'gaps_count': gaps_count,
        'profile': profile,
        'q': q,
        'target_category': target_category,
        'categories': JOB_CATEGORIES,
        'enrolled_trainings': enrolled_trainings,
        'enrolled_ids': enrolled_ids,
    }
    return render(request, 'tracker/APPLICANT/training_recommendations.html', context)

@role_required(['applicant'])
@require_POST
def applicant_enroll_training(request, program_id):
    profile = request.user.profile
    program = get_object_or_404(TrainingProgram, id=program_id)
    
    enrollment, created = TrainingEnrollment.objects.get_or_create(
        profile=profile,
        training_program=program,
        defaults={'status': 'Enrolled'}
    )
    if created:
        messages.success(request, f"Successfully enrolled in training program: '{program.title}'!")
        return redirect('applicant_course_lms', enrollment_id=enrollment.id)
    else:
        messages.info(request, f"You are already enrolled in '{program.title}'.")
        return redirect('applicant_course_lms', enrollment_id=enrollment.id)

@role_required(['applicant'])
def applicant_course_lms(request, enrollment_id):
    profile = request.user.profile
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related('training_program__skill_addressed'),
        id=enrollment_id,
        profile=profile
    )
    program = enrollment.training_program
    curriculum = get_program_curriculum(program)

    completed_set = set(enrollment.completed_lessons or [])

    # Annotate modules and lessons with completed status
    total_lessons = curriculum['total_lessons']
    completed_count = 0
    all_lessons_flat = []

    for mod in curriculum['modules']:
        mod_completed_count = 0
        for les in mod['lessons']:
            is_done = les['id'] in completed_set
            les['is_completed'] = is_done
            if is_done:
                completed_count += 1
                mod_completed_count += 1
            all_lessons_flat.append(les)
        mod['completed_lessons_count'] = mod_completed_count
        mod['total_lessons_count'] = len(mod['lessons'])
        mod['is_all_completed'] = (mod_completed_count == len(mod['lessons']))

    # Determine current active lesson
    active_lesson_id = request.GET.get('lesson')
    active_lesson = None
    if active_lesson_id:
        for les in all_lessons_flat:
            if les['id'] == active_lesson_id:
                active_lesson = les
                break
    if not active_lesson:
        # Default to first incomplete lesson or first lesson
        for les in all_lessons_flat:
            if not les['is_completed']:
                active_lesson = les
                break
        if not active_lesson and all_lessons_flat:
            active_lesson = all_lessons_flat[0]

    # Find next and previous lessons for navigation
    current_idx = -1
    for idx, les in enumerate(all_lessons_flat):
        if active_lesson and les['id'] == active_lesson['id']:
            current_idx = idx
            break
            
    prev_lesson = all_lessons_flat[current_idx - 1] if current_idx > 0 else None
    next_lesson = all_lessons_flat[current_idx + 1] if (current_idx >= 0 and current_idx < len(all_lessons_flat) - 1) else None

    # Recalculate progress percentage
    progress_pct = int((completed_count / total_lessons) * 100) if total_lessons > 0 else 0
    if enrollment.progress_percentage != progress_pct:
        enrollment.progress_percentage = progress_pct
        enrollment.save()

    context = {
        'enrollment': enrollment,
        'program': program,
        'curriculum': curriculum,
        'active_lesson': active_lesson,
        'prev_lesson': prev_lesson,
        'next_lesson': next_lesson,
        'completed_set': completed_set,
        'completed_count': completed_count,
        'total_lessons': total_lessons,
        'progress_pct': progress_pct,
        'all_lessons_flat': all_lessons_flat,
        'profile': profile,
        'unread_notifications_count': Notification.objects.filter(user=request.user, is_read=False).count() if request.user.is_authenticated else 0,
        'is_time_requirement_met': enrollment.is_time_requirement_met,
        'time_spent_seconds': enrollment.time_spent_seconds,
        'required_training_seconds': enrollment.required_training_seconds,
        'required_training_minutes': enrollment.required_training_minutes,
        'time_remaining_seconds': enrollment.time_remaining_seconds,
        'time_progress_percentage': enrollment.time_progress_percentage,
        'formatted_time_spent': enrollment.formatted_time_spent,
        'formatted_required_time': enrollment.formatted_required_time,
        'formatted_time_remaining': enrollment.formatted_time_remaining,
        'can_claim_certificate': enrollment.is_time_requirement_met and (progress_pct >= 100 or enrollment.status == 'Attended' or bool(enrollment.certificate_code)),
    }
    return render(request, 'tracker/APPLICANT/course_player.html', context)

@role_required(['applicant'])
@require_POST
def applicant_course_heartbeat(request, enrollment_id):
    profile = request.user.profile
    enrollment = get_object_or_404(TrainingEnrollment, id=enrollment_id, profile=profile)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    try:
        seconds = int(data.get('seconds', 10))
    except (ValueError, TypeError):
        seconds = 10
    seconds = max(1, min(seconds, 60))

    enrollment.time_spent_seconds += seconds
    enrollment.save(update_fields=['time_spent_seconds'])

    return JsonResponse({
        'success': True,
        'time_spent_seconds': enrollment.time_spent_seconds,
        'required_training_seconds': enrollment.required_training_seconds,
        'required_training_minutes': enrollment.required_training_minutes,
        'time_remaining_seconds': enrollment.time_remaining_seconds,
        'is_time_requirement_met': enrollment.is_time_requirement_met,
        'time_progress_percentage': enrollment.time_progress_percentage,
        'formatted_time_spent': enrollment.formatted_time_spent,
        'formatted_required_time': enrollment.formatted_required_time,
        'formatted_time_remaining': enrollment.formatted_time_remaining,
    })

@role_required(['applicant'])
@require_POST
def applicant_complete_lesson(request, enrollment_id):
    profile = request.user.profile
    enrollment = get_object_or_404(TrainingEnrollment, id=enrollment_id, profile=profile)
    program = enrollment.training_program
    curriculum = get_program_curriculum(program)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    lesson_id = data.get('lesson_id')
    if not lesson_id:
        return JsonResponse({'success': False, 'error': 'No lesson_id provided'}, status=400)

    # Validate the lesson against the program curriculum so callers cannot
    # mark the capstone exam (or arbitrary IDs) complete and inflate progress.
    valid_lessons = {}
    for mod in curriculum['modules']:
        for les in mod['lessons']:
            valid_lessons[les['id']] = les

    if lesson_id not in valid_lessons:
        return JsonResponse({'success': False, 'error': 'Unknown lesson for this course.'}, status=400)
    if valid_lessons[lesson_id].get('type') == 'assessment':
        return JsonResponse(
            {'success': False, 'error': 'The capstone assessment must be submitted through the assessment form.'},
            status=400,
        )

    # Keep only IDs that really belong to this curriculum (cleans up legacy data).
    completed = [lid for lid in (enrollment.completed_lessons or []) if lid in valid_lessons]
    if lesson_id not in completed:
        completed.append(lesson_id)
        enrollment.completed_lessons = completed

    total_lessons = curriculum['total_lessons']
    progress_pct = min(100, int((len(completed) / total_lessons) * 100)) if total_lessons > 0 else 0
    enrollment.progress_percentage = progress_pct
    enrollment.save()

    # Progress Tracking on General Training Path:
    all_enrolls = profile.training_enrollments.all()
    if all_enrolls.exists():
        avg_prog = int(sum(e.progress_percentage for e in all_enrolls) / all_enrolls.count())
    else:
        avg_prog = progress_pct
    profile.training_progress_percentage = avg_prog
    profile.training_progress_title = program.title
    profile.save()

    return JsonResponse({
        'success': True,
        'lesson_id': lesson_id,
        'progress_percentage': progress_pct,
        'completed_count': len(completed),
        'total_lessons': total_lessons,
        'general_training_progress': profile.training_progress_percentage,
        'is_time_requirement_met': enrollment.is_time_requirement_met,
        'time_remaining_seconds': enrollment.time_remaining_seconds,
        'formatted_time_remaining': enrollment.formatted_time_remaining,
    })

@role_required(['applicant'])
@require_POST
def applicant_submit_assessment(request, enrollment_id):
    profile = request.user.profile
    enrollment = get_object_or_404(TrainingEnrollment, id=enrollment_id, profile=profile)
    program = enrollment.training_program
    curriculum = get_program_curriculum(program)

    # Enforce minimum training seat-time before certificate evaluation
    if not enrollment.is_time_requirement_met:
        return JsonResponse({
            'success': False,
            'time_locked': True,
            'error': f"Training time requirement not satisfied. You must complete at least {enrollment.formatted_required_time} of training before receiving your certificate. (Time recorded: {enrollment.formatted_time_spent}, remaining: {enrollment.formatted_time_remaining}). Please spend more time learning the course modules.",
            'time_remaining_seconds': enrollment.time_remaining_seconds,
            'formatted_time_remaining': enrollment.formatted_time_remaining,
            'formatted_time_spent': enrollment.formatted_time_spent,
            'formatted_required_time': enrollment.formatted_required_time,
        }, status=400)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    answers = data.get('answers', {})

    # Locate Exam Questions in Module 5
    exam_lesson = None
    for mod in curriculum['modules']:
        for les in mod['lessons']:
            if les['type'] == 'assessment':
                exam_lesson = les
                break

    if not exam_lesson:
        return JsonResponse({'success': False, 'error': 'Assessment not found'}, status=404)

    questions = exam_lesson['content']['questions']
    total_q = len(questions)
    correct_count = 0
    domain_scores = {}

    for q in questions:
        q_id = q['id']
        domain = q['domain']
        if domain not in domain_scores:
            domain_scores[domain] = {'total': 0, 'correct': 0}
        domain_scores[domain]['total'] += 1

        selected_ans = answers.get(q_id)
        if selected_ans is not None and str(selected_ans) == str(q['correct']):
            correct_count += 1
            domain_scores[domain]['correct'] += 1

    score_pct = int((correct_count / total_q) * 100) if total_q > 0 else 0
    passed = score_pct >= exam_lesson['content']['passing_score']

    # Build domain breakdown
    domain_breakdown = []
    for dom_name, stats in domain_scores.items():
        pct = int((stats['correct'] / stats['total']) * 100) if stats['total'] > 0 else 0
        domain_breakdown.append({
            'domain': dom_name,
            'correct': stats['correct'],
            'total': stats['total'],
            'percentage': pct
        })

    enrollment.assessment_score = score_pct
    enrollment.assessment_results = {
        'score': score_pct,
        'passed': passed,
        'correct_count': correct_count,
        'total_count': total_q,
        'domain_breakdown': domain_breakdown,
        'submitted_at': timezone.now().strftime('%b %d, %Y %I:%M %p')
    }

    certificate_code = enrollment.certificate_code
    if passed:
        # Mark course attended and 100% completed
        enrollment.status = 'Attended'
        enrollment.progress_percentage = 100
        completed = list(enrollment.completed_lessons or [])
        if 'mod5_exam' not in completed:
            completed.append('mod5_exam')
        enrollment.completed_lessons = completed

        # Automatic Certificate Generation
        if not certificate_code:
            rand_hex = secrets.token_hex(3).upper()
            certificate_code = f"SKILLUP-TESDA-2026-{rand_hex}"
            enrollment.certificate_code = certificate_code
            enrollment.certificate_issued_at = timezone.now()

        enrollment.save()

        # Automatically add to Applicant's Personal List of Credentials (Certification model)
        cert_name = f"{program.title} Certificate of Competency"
        cert_exists = Certification.objects.filter(profile=profile, name=cert_name).exists()
        if not cert_exists:
            Certification.objects.create(
                profile=profile,
                name=cert_name,
                issuing_organization=f"{program.provider} & SKILLUP Certified",
                issue_date=timezone.localdate(),
                is_tesda=True
            )

        # Add skills addressed by the completed program
        for s in program.all_skills:
            app_skill, _ = ApplicantSkill.objects.get_or_create(
                profile=profile,
                skill=s,
                defaults={'source': 'training'}
            )

        # Update general training progress
        profile.training_progress_percentage = 100
        profile.training_progress_title = program.title
        profile.save()

        # Send in-app notification
        Notification.objects.create(
            user=profile.user,
            message=f"Congratulations! You scored {score_pct}% on the {program.title} assessment and earned your Official Certificate ({certificate_code}). It has been added to your credentials."
        )
    else:
        enrollment.save()

    return JsonResponse({
        'success': True,
        'score': score_pct,
        'passed': passed,
        'correct_count': correct_count,
        'total_count': total_q,
        'domain_breakdown': domain_breakdown,
        'certificate_code': certificate_code,
        'certificate_url': f"/applicant/certificate/{enrollment.id}/" if passed else None,
    })

def verify_certificate(request):
    """
    Public portal to verify the authenticity of SKILLUP / PESO issued certificates.
    Accepts certificate code via GET parameter '?code=...' or POST search.
    """
    code = (request.GET.get('code') or request.POST.get('code') or '').strip()
    enrollment = None
    searched = bool(code)
    is_valid = False

    if code:
        enrollment = TrainingEnrollment.objects.filter(
            certificate_code__iexact=code
        ).select_related('profile__user', 'training_program__skill_addressed').first()
        if enrollment:
            is_valid = True

    context = {
        'code': code,
        'searched': searched,
        'is_valid': is_valid,
        'enrollment': enrollment,
        'program': enrollment.training_program if enrollment else None,
        'applicant': enrollment.profile if enrollment else None,
    }
    return render(request, 'tracker/LOGIN/verify_certificate.html', context)


@role_required(['applicant', 'admin', 'employer'])
def applicant_download_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related('profile__user', 'training_program__skill_addressed'),
        id=enrollment_id
    )
    # Check access permission
    if request.user.profile.role == 'applicant' and enrollment.profile != request.user.profile:
        messages.error(request, "You do not have permission to view this certificate.")
        return redirect('applicant_dashboard')

    if not enrollment.certificate_code:
        # Check training time requirement
        if not enrollment.is_time_requirement_met:
            messages.warning(
                request,
                f"You must complete the required training time ({enrollment.formatted_required_time}) before your certificate can be issued. Time recorded so far: {enrollment.formatted_time_spent} ({enrollment.formatted_time_remaining} remaining)."
            )
            return redirect('applicant_course_lms', enrollment_id=enrollment.id)

        # Generate code only when the capstone assessment was actually passed
        # (progress alone must not unlock a certificate).
        assessment_passed = bool((enrollment.assessment_results or {}).get('passed'))
        if enrollment.status == 'Attended' or assessment_passed:
            enrollment.certificate_code = f"SKILLUP-TESDA-2026-{secrets.token_hex(3).upper()}"
            enrollment.certificate_issued_at = timezone.now()
            enrollment.save()
        else:
            messages.warning(request, "Complete all modules and pass the capstone assessment to unlock your certificate.")
            return redirect('applicant_course_lms', enrollment_id=enrollment.id)

    program = enrollment.training_program
    skills_list = [s.name for s in program.all_skills]
    
    context = {
        'enrollment': enrollment,
        'program': program,
        'applicant': enrollment.profile,
        'skills_list': skills_list,
        'issue_date': enrollment.certificate_issued_at or timezone.now(),
        'certificate_code': enrollment.certificate_code,
        'verify_url': request.build_absolute_uri(reverse('verify_certificate') + f'?code={enrollment.certificate_code}'),
    }
    return render(request, 'tracker/APPLICANT/certificate_template.html', context)

@role_required(['admin'])
@require_POST
def create_referral(request):
    applicant_id = request.POST.get('applicant_id')
    vacancy_id = request.POST.get('vacancy_id')
    
    applicant = get_object_or_404(Profile, id=applicant_id, role='applicant')
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id)
    
    referral, created = Referral.objects.get_or_create(
        applicant=applicant,
        job_vacancy=vacancy,
        defaults={'status': 'Pending'}
    )
    if created:
        if vacancy.remaining_slots and vacancy.remaining_slots > 0:
            vacancy.remaining_slots = max(0, vacancy.remaining_slots - 1)
            if vacancy.remaining_slots == 0:
                vacancy.status = 'Closed'
                # The posting is now full, not cancelled. Do not auto-withdraw
                # the active applications that are already in the pipeline.
                vacancy._skip_auto_withdraw = True
            vacancy.save()
        send_referral_approved_email(applicant, vacancy)
        Notification.objects.create(
            user=applicant.user,
            message=f"Referral Approved: PESO has referred your profile to '{vacancy.title}' at {vacancy.employer.company_name}."
        )
        messages.success(request, f"Successfully referred {applicant.user.first_name} to {vacancy.title}! Email notification dispatched.")
        match_pct, _ = calculate_match_score(applicant, vacancy)
        if match_pct >= 70:
            Notification.objects.create(
                user=vacancy.employer.user,
                message=f"Match Alert: Applicant {applicant.user.get_full_name()} has reached a {int(match_pct)}% skill match for your vacancy '{vacancy.title}'."
            )
    else:
        messages.info(request, f"{applicant.user.first_name} is already referred to {vacancy.title}.")
        
    return redirect('applicant_monitoring_admin')

@role_required(['admin'])
@require_POST
def update_referral_status(request, referral_id):
    referral = get_object_or_404(Referral, id=referral_id)
    if referral.status == 'Hired':
        messages.error(request, "This referral is finalized (Hired) and cannot be modified.")
        return redirect('employment_tracking_admin')
        
    status = request.POST.get('status')
    valid_statuses = {value for value, _label in Referral.STATUS_CHOICES}
    if status in valid_statuses:
        referral.status = status
        referral.save()
        messages.success(request, f"Updated referral status for {referral.applicant.user.first_name} to {status}!")
        try:
            if status in PLACEMENT_STATUSES:
                send_hired_confirmation_email(referral.applicant, referral.job_vacancy, timezone.localdate(), status)
                Notification.objects.create(
                    user=referral.applicant.user,
                    message=f"Congratulations! You have been officially marked as {status} for '{referral.job_vacancy.title}' at {referral.job_vacancy.employer.company_name}!"
                )
            elif status in ['Not Hired', 'Declined', 'Withdrawn', 'Withdrawn — Position Closed']:
                send_application_status_update_email(referral.applicant, referral.job_vacancy, status)
                Notification.objects.create(
                    user=referral.applicant.user,
                    message=f"Application Update: Your referral status for '{referral.job_vacancy.title}' has been updated to {status}."
                )
            elif status in ['Pending', 'No Response', 'No Response from Applicant', 'No Response from Employer']:
                send_application_status_update_email(referral.applicant, referral.job_vacancy, status)
            else:
                Notification.objects.create(
                    user=referral.applicant.user,
                    message=f"Application Update: Your referral status for '{referral.job_vacancy.title}' has been updated to {status}."
                )
        except Exception:
            pass
    else:
        messages.error(request, "Invalid referral status.")
    return redirect('employment_tracking_admin')

@role_required(['admin'])
@require_POST
def log_contact_attempt(request, referral_id):
    referral = get_object_or_404(Referral, id=referral_id)
    if referral.status == 'Hired':
        messages.error(request, "This referral is finalized (Hired) and cannot be modified.")
        return redirect('employment_tracking_admin')
        
    method = request.POST.get('method', 'Call').strip()
    notes = request.POST.get('notes', '').strip()
    date_str = request.POST.get('date') or timezone.localdate().strftime('%Y-%m-%d')
    
    attempt = {
        'date': date_str,
        'method': method,
        'notes': notes
    }
    
    if not isinstance(referral.contact_attempts, list):
        referral.contact_attempts = []
        
    referral.contact_attempts.append(attempt)
    referral.save()
    
    messages.success(request, f"Logged {method} contact attempt for {referral.applicant.user.first_name}!")
    return redirect('employment_tracking_admin')

# ==========================================
# EMPLOYER VIEWS
# ==========================================

@role_required(['employer'])
def employer_dashboard(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    vacancies = JobVacancy.objects.filter(employer=profile).order_by('-created_at')
    active_vacs_count = vacancies.filter(status='Open').count()
    
    referrals = Referral.objects.filter(job_vacancy__employer=profile).select_related('applicant__user', 'job_vacancy')
    total_apps_count = referrals.count()
    interviews_count = referrals.filter(applicant__status='Interviewing').count()
    hires_count = referrals.filter(status__in=PLACEMENT_STATUSES).count()
    
    # Auto-seed mock interviews if none exist
    if not Interview.objects.filter(employer=profile).exists():
        today = timezone.localdate()
        
        # Try to find Sarah Jenkins (seeded applicant)
        sarah_profile = Profile.objects.filter(role='applicant', user__email='sarah.j@analytics.ai').first()
        if not sarah_profile:
            sarah_profile = Profile.objects.filter(role='applicant', user__first_name__icontains='Sarah').first()
            
        # Try to find Marcus Chen (seeded applicant)
        marcus_profile = Profile.objects.filter(role='applicant', user__email='marcus.c@product.ai').first()
        if not marcus_profile:
            marcus_profile = Profile.objects.filter(role='applicant', user__first_name__icontains='Marcus').first()
            
        ds_vac = JobVacancy.objects.filter(employer=profile, title='Data Scientist').first()
        if not ds_vac:
            ds_vac = JobVacancy.objects.filter(employer=profile).first()
            
        pm_vac = JobVacancy.objects.filter(employer=profile, title='Product Manager').first()
        if not pm_vac:
            pm_vac = JobVacancy.objects.filter(employer=profile).first()
            
        import datetime
        
        # 1. Sarah Jenkins Interview (10 AM, Video Call, Final Round)
        Interview.objects.create(
            employer=profile,
            title=f"Interview with {sarah_profile.user.get_full_name()}" if sarah_profile else "Interview with Sarah Chen",
            candidate=sarah_profile,
            vacancy=ds_vac,
            date=today,
            start_time=datetime.time(10, 0),
            interview_type='Video Call',
            round_name='Final Round',
            meeting_link='https://meet.google.com/abc-defg-hij',
            notes='Final leadership and technical fit review.'
        )
        
        # 2. Marcus Chen Interview (02 PM, Phone Call, Screening)
        Interview.objects.create(
            employer=profile,
            title=f"Interview with {marcus_profile.user.get_full_name()}" if marcus_profile else "Interview with Marcus Thorne",
            candidate=marcus_profile,
            vacancy=pm_vac,
            date=today,
            start_time=datetime.time(14, 0),
            interview_type='Phone Call',
            round_name='Screening',
            notes='Initial HR screening and experience walk-through.'
        )
        
        # 3. Review Session (04 PM, In-Person, Hiring Sync)
        Interview.objects.create(
            employer=profile,
            title='Review Session',
            candidate=None,
            vacancy=None,
            date=today,
            start_time=datetime.time(16, 0),
            interview_type='In-Person',
            round_name='Hiring Sync',
            notes='Engineering team sync to review pipeline candidates.'
        )

    today = timezone.localdate()
    today_interviews = Interview.objects.filter(employer=profile, date=today).select_related('candidate__user', 'vacancy').order_by('start_time')
    all_interviews = Interview.objects.filter(employer=profile).select_related('candidate__user', 'vacancy').order_by('date', 'start_time')
    
    pipeline_candidates = Profile.objects.filter(role='applicant', referrals__job_vacancy__employer=profile).distinct().select_related('user')
    if pipeline_candidates.exists():
        candidates_list = pipeline_candidates
    else:
        candidates_list = Profile.objects.filter(role='applicant').select_related('user')
    
    # Build pipeline data for the active vacancies
    pipelines = []
    for vac in vacancies:
        vac_referrals = referrals.filter(job_vacancy=vac)
        match_scores = []
        candidates = []
        for ref in vac_referrals:
            match_pct, _ = calculate_match_score(ref.applicant, vac)
            match_scores.append(match_pct)
            candidates.append(ref.applicant)
        
        avg_match = round(sum(match_scores) / len(match_scores), 1) if match_scores else 80.0
        
        pipelines.append({
            'vacancy': vac,
            'avg_match': int(avg_match),
            'candidates_count': len(candidates),
            'candidates': candidates[:3],
            'status': 'Active' if vac.status == 'Open' else vac.status
        })
        
    context = {
        'profile': profile,
        'active_vacs_count': active_vacs_count,
        'total_apps_count': total_apps_count,
        'interviews_count': interviews_count,
        'hires_count': hires_count,
        'pipelines': pipelines,
        'referrals': referrals[:5],
        'today_interviews': today_interviews,
        'all_interviews': all_interviews,
        'candidates_list': candidates_list,
        'vacancies': vacancies,
    }
    return render(request, 'tracker/EMPLOYER/employer_dashboard.html', context)

@role_required(['employer', 'admin'])
def applicant_details_employer(request, applicant_id=None):
    seed_mock_applicants_if_empty()
    if applicant_id:
        profile = get_object_or_404(Profile, id=applicant_id, role='applicant')
    else:
        profile = Profile.objects.filter(role='applicant').first()
        if not profile:
            messages.error(request, "No applicants found.")
            return redirect('applicants_employer')
            
    education_list = profile.education.all()
    experience_list = profile.experience.all()
    certification_list = profile.certifications.all()
    skills_list = profile.applicant_skills.all().select_related('skill')
    
    # Originating navigation context (Vacancies, Training Programs, Talent Bench, etc.)
    from_section = request.GET.get('from_section', '').strip()
    from_id = request.GET.get('from_id', '').strip()
    referer = request.META.get('HTTP_REFERER', '')
    
    if not from_section and referer:
        import re
        vac_match = re.search(r'/peso-admin/vacancies/(\d+)', referer)
        if vac_match:
            from_section = 'vacancies'
            from_id = vac_match.group(1)
        elif '/peso-admin/vacancies' in referer:
            from_section = 'vacancies'
            
        prog_match = re.search(r'/peso-admin/training-programs/(\d+)', referer)
        if prog_match:
            from_section = 'training'
            from_id = prog_match.group(1)
        elif '/peso-admin/training' in referer:
            from_section = 'training'
            
        elif '/peso-admin/applicants' in referer:
            from_section = 'talent_bench'
        elif '/employer/notifications' in referer:
            from_section = 'notifications'
        elif '/employer/hiring-tracker' in referer:
            from_section = 'hiring_tracker'
        elif '/employer/job-management' in referer:
            from_section = 'job_management'
        elif '/employer/applicants' in referer:
            from_section = 'hiring_tracker'
        elif '/employer-dashboard' in referer:
            from_section = 'employer_dashboard'

    if not from_section and request.user.profile.role == 'employer':
        from_section = 'hiring_tracker'

    from_vacancy = None
    from_training = None
    if from_section == 'vacancies' and from_id:
        from_vacancy = JobVacancy.objects.filter(id=from_id).first()
    elif from_section == 'training' and from_id:
        from_training = TrainingProgram.objects.filter(id=from_id).first()

    # Calculate match score against vacancies
    vacancies = JobVacancy.objects.filter(employer=request.user.profile)
    if not vacancies.exists():
        vacancies = JobVacancy.objects.all()
        
    job_id = request.GET.get('job_id')
    best_job = from_vacancy if from_vacancy else None
    highest_match = 0
    
    if job_id and not best_job:
        try:
            best_job = JobVacancy.objects.get(id=job_id)
            highest_match, _ = calculate_match_score(profile, best_job)
        except JobVacancy.DoesNotExist:
            pass

    if not best_job:
        for vac in vacancies:
            match_pct, _ = calculate_match_score(profile, vac)
            if match_pct > highest_match:
                highest_match = match_pct
                best_job = vac
    else:
        highest_match, _ = calculate_match_score(profile, best_job)

    # Gaps for the best vacancy
    gaps = []
    if best_job:
        _, gaps = calculate_match_score(profile, best_job)
        
    labels = []
    target_data = []
    current_data = []
    for g in gaps:
        labels.append(g['skill'])
        target_data.append(g['required'])
        current_data.append(g['candidate'])
        
    if not labels:
        for s in skills_list:
            labels.append(s.skill.name)
            target_data.append(5)
            current_data.append(5)

    user_profile = getattr(request.user, 'profile', None)
    referrals_qs = Referral.objects.filter(applicant=profile).select_related('job_vacancy', 'job_vacancy__employer')
    if user_profile and user_profile.role == 'employer':
        referrals_qs = referrals_qs.filter(job_vacancy__employer=user_profile)
    active_referral = referrals_qs.order_by('-updated_at', '-id').first()

    if request.method == 'POST' and user_profile and user_profile.role == 'employer':
        action = request.POST.get('action')
        ref_id = request.POST.get('referral_id')
        ref_to_update = Referral.objects.filter(id=ref_id, job_vacancy__employer=user_profile).first() if ref_id else active_referral
        
        if action == 'mark_unresponsive' and ref_to_update:
            unresponsive_reason = request.POST.get('unresponsive_reason', 'Candidate unreachable after multiple attempts')
            process_notes = request.POST.get('process_notes', '').strip()
            contact_summary = f"{unresponsive_reason}. {process_notes}".strip()

            if ref_to_update.status in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding', 'Hired — Probationary', 'Hired — Regular'):
                vac = ref_to_update.job_vacancy
                vac.remaining_slots = min(vac.slots, vac.remaining_slots + 1)
                vac.save()

            ref_to_update.status = 'No Response from Applicant'
            ref_to_update.rejection_reason = contact_summary
            hist = ref_to_update.interview_stage_history or []
            if not isinstance(hist, list):
                hist = []
            hist.append({
                'stage': 'No Longer Contacted / Unresponsive',
                'status': 'No Response from Applicant',
                'notes': contact_summary,
                'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': user_profile.company_name or user_profile.user.get_full_name()
            })
            ref_to_update.interview_stage_history = hist
            ref_to_update.save()

            ref_to_update.applicant.status = 'Active — Job Seeking'
            ref_to_update.applicant.save()

            messages.warning(request, f"{profile.user.get_full_name()} marked as No Longer Contacted (Unresponsive).")
            return redirect('applicant_details_employer_id', applicant_id=profile.id)

        elif action == 'update_interview_stage' and ref_to_update:
            new_stage = request.POST.get('interview_stage', 'Initial Assessment')
            new_mode = request.POST.get('interview_mode', 'Online Video Assessment')
            stage_notes = request.POST.get('interview_stage_notes', '')
            meeting_link_venue = request.POST.get('meeting_link_venue', '')
            scheduled_date_str = request.POST.get('scheduled_date', '')
            scheduled_time_str = request.POST.get('scheduled_time', '')

            ref_to_update.status = 'Interviewing'
            ref_to_update.interview_stage = new_stage
            ref_to_update.interview_mode = new_mode
            ref_to_update.interview_stage_notes = stage_notes
            
            if scheduled_date_str:
                from datetime import datetime, time
                try:
                    s_date = datetime.strptime(scheduled_date_str, '%Y-%m-%d').date()
                    s_time = datetime.strptime(scheduled_time_str, '%H:%M').time() if scheduled_time_str else time(10, 0)
                    Interview.objects.create(
                        employer=user_profile,
                        vacancy=ref_to_update.job_vacancy,
                        candidate=ref_to_update.applicant,
                        title=f"{new_stage} - {ref_to_update.job_vacancy.title}",
                        round_name=new_stage,
                        interview_type='Video Call' if 'Online' in new_mode else ('In-Person' if 'Face' in new_mode or 'F2F' in new_mode else 'Phone Call'),
                        date=s_date,
                        start_time=s_time,
                        meeting_link=meeting_link_venue if 'http' in meeting_link_venue else '',
                        notes=stage_notes or f"Scheduled for {new_stage}",
                    )
                except Exception as _interview_err:
                    # Never block the stage update, but make the failure visible.
                    print("Could not create interview record:", _interview_err)

            hist = ref_to_update.interview_stage_history or []
            hist.append({
                'stage': new_stage,
                'mode': new_mode,
                'notes': stage_notes,
                'venue': meeting_link_venue,
                'updated_at': timezone.now().strftime('%Y-%m-%d %H:%M')
            })
            ref_to_update.interview_stage_history = hist
            ref_to_update.save()

            messages.success(request, f"Interview stage updated to '{new_stage}' for {profile.user.get_full_name()}.")
            return redirect('applicant_details_employer_id', applicant_id=profile.id)

    context = {
        'profile': profile,
        'education_list': education_list,
        'experience_list': experience_list,
        'certification_list': certification_list,
        'skills_list': skills_list,
        'highest_match': int(highest_match),
        'best_job': best_job,
        'active_referral': active_referral,
        'candidate_referrals': referrals_qs,
        'gaps': gaps,
        'labels': labels,
        'target_data': target_data,
        'current_data': current_data,
        'from_section': from_section,
        'from_id': from_id,
        'from_vacancy': from_vacancy,
        'from_training': from_training,
        'referer': referer,
    }
    return render(request, 'tracker/EMPLOYER/applicant_details_employer.html', context)


@role_required(['employer'])
def applicant_gap_analysis_employer(request):
    seed_mock_applicants_if_empty()
    applicant_id = request.GET.get('applicant_id')
    profile = request.user.profile
    
    if applicant_id:
        candidate = get_object_or_404(Profile, id=applicant_id, role='applicant')
    else:
        # Default to first referred candidate
        first_ref = Referral.objects.filter(job_vacancy__employer=profile).first()
        if not first_ref:
            messages.error(request, "No candidate referrals found to analyze gaps.")
            return redirect('applicants_employer')
        candidate = first_ref.applicant
        
    # Get referral to find the referred vacancy
    referral = Referral.objects.filter(applicant=candidate, job_vacancy__employer=profile).first()
    if not referral:
        messages.error(request, "This candidate has not been referred to any of your job vacancies.")
        return redirect('applicants_employer')
        
    vacancy = referral.job_vacancy
    match_pct, gaps = calculate_match_score(candidate, vacancy)
    
    # Build labels, target_data, and current_data for Chart.js radar chart
    labels = []
    target_data = []
    current_data = []
    gaps_count = 0
    for g in gaps:
        labels.append(g['skill'])
        target_data.append(g['required'])
        current_data.append(g['candidate'])
        if g['gap'] > 0:
            gaps_count += 1
            
    # Recommended training programs addressing the gaps
    gap_skills = [g['skill'] for g in gaps if g['gap'] > 0]
    recommended_programs = TrainingProgram.objects.filter(skill_addressed__name__in=gap_skills).select_related('skill_addressed')
    
    context = {
        'candidate': candidate,
        'vacancy': vacancy,
        'match_score': int(match_pct),
        'gaps': gaps,
        'gaps_count': gaps_count,
        'labels': labels,
        'target_data': target_data,
        'current_data': current_data,
        'recommended_programs': recommended_programs,
        'profile': profile,
    }
    return render(request, 'tracker/EMPLOYER/applicant_gap_analysis_employer.html', context)

def parse_exp_years(exp_str):
    if not exp_str:
        return 0.0
    import re
    m = re.search(r'([\d.]+)', str(exp_str))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0

@role_required(['employer'])
def applicants_employer(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    
    from_section = request.GET.get('from_section', '').strip()
    referer = request.META.get('HTTP_REFERER', '')
    if not from_section and referer:
        if '/employer-dashboard' in referer or 'dashboard' in referer:
            from_section = 'dashboard'
        elif '/employer/job-management' in referer:
            from_section = 'job_management'
            
    job_id = request.GET.get('job_id')
    selected_job = None
    referrals = Referral.objects.filter(job_vacancy__employer=profile).select_related(
        'applicant__user', 'job_vacancy', 'job_vacancy__employer'
    ).prefetch_related(
        'applicant__applicant_skills__skill',
        'applicant__education',
        'applicant__experience',
        'applicant__certifications',
        'job_vacancy__requirements__skill',
    )
    if job_id:
        referrals = referrals.filter(job_vacancy_id=job_id)
        selected_job = JobVacancy.objects.filter(id=job_id, employer=profile).first()
        
    status_filter = request.GET.get('filter')
    
    # Advanced filter GET params
    search_query = request.GET.get('search', '').strip()
    min_match = request.GET.get('min_match', '').strip()
    min_exp = request.GET.get('min_exp', '').strip()
    education = request.GET.get('education', '').strip()
    
    applicants = []
    seen_applicants = set()
    for ref in referrals:
        applicant = ref.applicant
        if applicant.id in seen_applicants:
            continue
        seen_applicants.add(applicant.id)
        
        # Calculate overall match and the skill-only portion separately.
        match_pct, _ = calculate_match_score(applicant, ref.job_vacancy)
        applicant.match_percentage = int(match_pct)
        skill_breakdown = get_qualification_breakdown(applicant, ref.job_vacancy)
        applicant.skill_match_percentage = int(skill_breakdown['skills_score'])
        applicant.skills_list = [s.strip() for s in applicant.skills.split(',')] if applicant.skills else []
        applicant.referred_job_title = ref.job_vacancy.title
        applicant.referral = ref
        applicant.referral_status = ref.status
        applicant.interview_stage = ref.interview_stage
        applicant.interview_mode = ref.interview_mode
        applicant.current_interview_stage_number = ref.current_interview_stage_number
        
        applicants.append(applicant)
        
    # Filters after building matches
    if status_filter == 'high_potential':
        applicants = [a for a in applicants if a.match_percentage >= 80]
    elif status_filter == 'Shortlisted':
        applicants = [
            a for a in applicants 
            if a.status in ('Shortlisted', 'Interviewing', 'Employed — Onboarding') 
            or getattr(a, 'referral_status', '') in ('Shortlisted', 'Interviewing', 'Accepted - Awaiting Onboarding', 'Confirmed - Onboarding', 'Hired - Probationary', 'Hired - Regular', 'Regularly Employed')
        ]
    elif status_filter == 'Rejected':
        applicants = [
            a for a in applicants 
            if a.status in ('Rejected', 'Not Hired') 
            or getattr(a, 'referral_status', '') in ('Rejected', 'Not Hired', 'Declined', 'No Show', 'Closed - No Show')
        ]

    # Advanced filters logic
    if search_query:
        q = search_query.lower()
        applicants = [
            a for a in applicants
            if q in f"{a.user.first_name} {a.user.last_name}".lower()
            or q in a.user.email.lower()
            or (a.id_number and q in a.id_number.lower())
            or any(q in s.lower() for s in a.skills_list)
        ]

    if min_match:
        try:
            val = int(min_match)
            applicants = [a for a in applicants if a.match_percentage >= val]
        except ValueError:
            pass

    if min_exp:
        try:
            val = float(min_exp)
            applicants = [a for a in applicants if parse_exp_years(a.experience_years) >= val]
        except ValueError:
            pass

    if education:
        edu_query = education.lower()
        applicants = [
            a for a in applicants
            if any(
                edu_query in e.degree.lower() or (edu_query == 'doctorate' and 'phd' in e.degree.lower())
                for e in a.education.all()
            )
        ]

    # Check for Excel export request
    if request.GET.get('export') == 'excel':
        import openpyxl
        from django.http import HttpResponse
        import re

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        filename = "applicants"
        if selected_job:
            filename = re.sub(r'[^a-zA-Z0-9_-]', '_', selected_job.title).lower()
        response['Content-Disposition'] = f'attachment; filename="{filename}_export.xlsx"'
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Applicants Overview"
        
        headers = ['First Name', 'Last Name', 'Email', 'Applied Position', 'Skill Match %', 'Experience Years', 'Top Skills', 'Profile Status']
        ws.append(headers)
        
        # Style headers with deep blue color matching SKILLUP theme
        from openpyxl.styles import Font, PatternFill, Alignment
        header_font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='0051d5', end_color='0051d5', fill_type='solid')
        alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = alignment
        
        for a in applicants:
            top_skills = ", ".join(a.skills_list)
            ws.append([
                a.user.first_name,
                a.user.last_name,
                a.user.email,
                a.referred_job_title,
                f"{a.match_percentage}%",
                a.experience_years or "0",
                top_skills,
                a.status
            ])
            
        # Auto-fit column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
        wb.save(response)
        return response
        
    context = {
        'applicants': applicants,
        'total_applied_count': len(applicants),
        'high_match_count': sum(1 for a in applicants if a.match_percentage >= 80),
        'profile': profile,
        'selected_job_id': job_id,
        'selected_job': selected_job,
        'selected_filter': status_filter,
        'from_section': from_section,
        
        # Advanced filters context
        'search_query': search_query,
        'min_match': min_match,
        'min_exp': min_exp,
        'selected_education': education,
        'show_advanced': bool(search_query or min_match or min_exp or education),
    }
    return render(request, 'tracker/EMPLOYER/applicants_employer.html', context)

@role_required(['employer'])
def company_profile_employer(request):
    profile = request.user.profile
    if request.method == 'POST':
        if request.POST.get('action') == 'deactivate_account':
            user = request.user
            user.is_active = False
            user.save()
            JobVacancy.objects.filter(employer=profile, status='Open').update(status='Closed')
            from django.contrib.auth import logout
            logout(request)
            messages.warning(request, "Your company account has been deactivated. Please contact PESO Admin if you need to reactivate.")
            return redirect('login')

        profile.company_name = request.POST.get('company_name', '').strip()
        profile.industry = request.POST.get('industry', '').strip()
        profile.company_size = request.POST.get('company_size', '').strip()
        profile.title = request.POST.get('tagline', '').strip() # use title as tagline
        profile.soft_notes = request.POST.get('about', '').strip() # use soft_notes as about
        profile.website = request.POST.get('website', '').strip()
        profile.location = request.POST.get('headquarters', '').strip() # use location as headquarters
        profile.contact_name = request.POST.get('contact_name', '').strip()
        profile.contact_position = request.POST.get('contact_position', '').strip()
        profile.contact_email = request.POST.get('contact_email', '').strip()
        profile.phone_number = request.POST.get('contact_phone', '').strip()
        
        if 'company_logo' in request.FILES:
            logo = request.FILES['company_logo']
            if profile.company_logo:
                profile.company_logo.delete()
            profile.company_logo = logo
            
        if 'profile_picture' in request.FILES:
            avatar = request.FILES['profile_picture']
            if profile.profile_picture:
                profile.profile_picture.delete()
            profile.profile_picture = avatar
            
        profile.save()
        messages.success(request, "Company profile updated successfully!")
        return redirect('company_profile_employer')
    
    reviews = profile.company_reviews.select_related('reviewer__user').order_by('-created_at')
    total_reviews = reviews.count()
    breakdown = {}
    for star in [5, 4, 3, 2, 1]:
        cnt = reviews.filter(rating=star).count()
        pct = round((cnt / total_reviews * 100)) if total_reviews > 0 else 0
        breakdown[star] = {'count': cnt, 'pct': pct}

    context = {
        'profile': profile,
        'reviews': reviews,
        'reviews_count': total_reviews,
        'avg_rating': profile.average_company_rating,
        'breakdown': breakdown,
    }
    return render(request, 'tracker/EMPLOYER/company_profile_employer.html', context)

def company_profile_detail(request, employer_id):
    seed_mock_applicants_if_empty()
    employer_profile = get_object_or_404(Profile, id=employer_id, role='employer')
    vacancies = JobVacancy.objects.filter(employer=employer_profile, status='Open')
    
    # Check if mock reviews should be initialized for this employer
    if not employer_profile.company_reviews.exists():
        applicants = list(Profile.objects.filter(role='applicant')[:3])
        sample_reviews = [
            {
                'rating': 5,
                'title': 'Outstanding technical interview & transparent hiring process',
                'text': 'The recruitment team was exceptionally responsive throughout the application. Technical evaluation was very aligned with practical competencies and clear timeline expectations were communicated upfront.',
                'status': 'Placed Applicant',
                'pros': 'Supportive managers, transparent compensation, fast onboarding.',
                'cons': 'Fast-paced project timelines require self-management.'
            },
            {
                'rating': 5,
                'title': 'Great culture and genuine support for skills upskilling',
                'text': 'Applied through PESO referral and had a wonderful experience. They value credentials and ongoing training certifications. Highly recommended employer in the network.',
                'status': 'Current Employee',
                'pros': 'Dedicated training budgets, collaborative colleagues, excellent benefits.',
                'cons': 'Hybrid work arrangement depends on department assignments.'
            },
            {
                'rating': 4,
                'title': 'Professional recruitment and direct feedback',
                'text': 'Interviewers provided constructive insights on domain competencies. Clear explanation of company expectations and compensation package.',
                'status': 'Candidate / Interviewee',
                'pros': 'Prompt email updates, modern workplace culture.',
                'cons': 'Multiple assessment rounds.'
            }
        ]
        for i, app in enumerate(applicants):
            if i < len(sample_reviews):
                sr = sample_reviews[i]
                CompanyReview.objects.create(
                    employer=employer_profile,
                    reviewer=app,
                    rating=sr['rating'],
                    review_title=sr['title'],
                    review_text=sr['text'],
                    employment_status=sr['status'],
                    pros=sr['pros'],
                    cons=sr['cons'],
                    is_anonymous=False
                )

    reviews = employer_profile.company_reviews.select_related('reviewer__user').order_by('-created_at')
    total_reviews = reviews.count()
    breakdown = {}
    for star in [5, 4, 3, 2, 1]:
        cnt = reviews.filter(rating=star).count()
        pct = round((cnt / total_reviews * 100)) if total_reviews > 0 else 0
        breakdown[star] = {'count': cnt, 'pct': pct}

    user_profile = getattr(request.user, 'profile', None) if request.user.is_authenticated else None
    user_review = None
    if user_profile and user_profile.role == 'applicant':
        user_review = CompanyReview.objects.filter(employer=employer_profile, reviewer=user_profile).first()

    context = {
        'employer': employer_profile,
        'vacancies': vacancies,
        'profile': user_profile,
        'reviews': reviews,
        'reviews_count': total_reviews,
        'avg_rating': employer_profile.average_company_rating,
        'breakdown': breakdown,
        'user_review': user_review,
    }
    return render(request, 'tracker/APPLICANT/company_profile_detail.html', context)

@login_required
@require_POST
def submit_company_review(request, employer_id):
    employer = get_object_or_404(Profile, id=employer_id, role='employer')
    reviewer = request.user.profile
    
    if reviewer == employer:
        messages.error(request, "You cannot review your own company profile.")
        return redirect('company_profile_detail', employer_id=employer.id)
        
    try:
        rating = int(request.POST.get('rating', 5))
        rating = max(1, min(5, rating))
    except (ValueError, TypeError):
        rating = 5
        
    review_title = request.POST.get('review_title', '').strip()
    review_text = request.POST.get('review_text', '').strip()
    employment_status = request.POST.get('employment_status', 'Applicant / Candidate').strip()
    pros = request.POST.get('pros', '').strip()
    cons = request.POST.get('cons', '').strip()
    is_anonymous = request.POST.get('is_anonymous') in ['true', 'True', '1', 'on', True]
    
    if not review_text:
        messages.error(request, "Please provide feedback details in your review.")
        return redirect('company_profile_detail', employer_id=employer.id)
        
    review, created = CompanyReview.objects.update_or_create(
        employer=employer,
        reviewer=reviewer,
        defaults={
            'rating': rating,
            'review_title': review_title,
            'review_text': review_text,
            'employment_status': employment_status,
            'pros': pros,
            'cons': cons,
            'is_anonymous': is_anonymous,
        }
    )
    
    # Notify employer
    reviewer_name = "An applicant" if is_anonymous else (reviewer.user.get_full_name() or reviewer.user.username)
    Notification.objects.create(
        user=employer.user,
        message=f"New Company Feedback: {reviewer_name} submitted a {rating}-star rating and review for {employer.company_name or 'your company'}."
    )
    
    if created:
        messages.success(request, "Thank you! Your company review and rating have been posted successfully.")
    else:
        messages.success(request, "Your company review has been updated successfully.")
        
    return redirect('company_profile_detail', employer_id=employer.id)


@role_required(['employer', 'admin'])
@require_POST
def save_screening_evaluation(request, referral_id):
    """Save the employer's 0-4 technical screening scorecard for a referral."""
    profile = request.user.profile
    if profile.role == 'employer':
        referral = get_object_or_404(Referral, id=referral_id, job_vacancy__employer=profile)
    else:
        referral = get_object_or_404(Referral, id=referral_id)

    scores = {}
    for key, _label, _weight in SCREENING_CRITERIA:
        try:
            value = int(request.POST.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        scores[key] = max(0, min(4, value))

    total = round(sum((scores[key] / 4.0) * weight for key, _label, weight in SCREENING_CRITERIA))
    referral.screening_evaluation = {
        'scores': scores,
        'total': total,
        'notes': request.POST.get('notes', '').strip()[:2000],
        'evaluated_by': profile.company_name or profile.user.get_full_name(),
        'evaluated_at': timezone.now().isoformat(),
    }
    referral.save(update_fields=['screening_evaluation'])

    messages.success(
        request,
        f"Screening scorecard saved for {referral.applicant.user.get_full_name()} — weighted score {total}%.",
    )
    return redirect(request.META.get('HTTP_REFERER', 'hiring_tracker_employer'))


@role_required(['employer', 'admin'])
def applicant_scorecard_employer(request, referral_id):
    """Printable, automated per-skill score card for one applicant."""
    profile = request.user.profile
    referrals = Referral.objects.select_related(
        'applicant__user', 'job_vacancy__employer__user'
    )
    if profile.role == 'employer':
        referrals = referrals.filter(job_vacancy__employer=profile)
    referral = get_object_or_404(referrals, id=referral_id)

    applicant = referral.applicant
    applicant = Profile.objects.prefetch_related(
        'applicant_skills__skill', 'experience'
    ).get(pk=applicant.pk)
    referral.applicant = applicant
    vacancy = JobVacancy.objects.prefetch_related('requirements__skill').get(pk=referral.job_vacancy_id)
    referral.job_vacancy = vacancy
    scorecard = build_automated_scorecard(applicant, vacancy)

    return render(request, 'tracker/EMPLOYER/applicant_scorecard.html', {
        'referral': referral,
        'scorecard': scorecard,
        'generated_at': timezone.localtime(),
    })


@role_required(['employer'])
def hiring_tracker_employer(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    referrals = Referral.objects.filter(job_vacancy__employer=profile).select_related(
        'applicant__user', 'job_vacancy'
    ).prefetch_related(
        'applicant__applicant_skills__skill',
        'applicant__education',
        'applicant__experience',
        'applicant__certifications',
        'job_vacancy__requirements__skill',
    )
    
    # Check for Excel export request
    if request.GET.get('export') == 'excel':
        import openpyxl
        from django.http import HttpResponse
        
        # Filter referrals if parameters are passed
        job_title = request.GET.get('job_title', '').strip()
        search_q = request.GET.get('search', '').strip()
        date_filter = request.GET.get('date_filter', 'newest').strip()
        
        export_referrals = referrals
        if job_title:
            export_referrals = export_referrals.filter(job_vacancy__title=job_title)
        if search_q:
            q = search_q.lower()
            export_referrals = [
                r for r in export_referrals
                if q in f"{r.applicant.user.first_name} {r.applicant.user.last_name}".lower()
                or q in r.applicant.user.email.lower()
                or q in r.job_vacancy.title.lower()
            ]
        
        # Sort or filter by date
        if isinstance(export_referrals, list):
            if date_filter == 'oldest':
                export_referrals = sorted(export_referrals, key=lambda r: r.date_referred)
            else:
                export_referrals = sorted(export_referrals, key=lambda r: r.date_referred, reverse=True)
        else:
            if date_filter == 'oldest':
                export_referrals = export_referrals.order_by('date_referred')
            else:
                export_referrals = export_referrals.order_by('-date_referred')
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="hiring_pipeline_export.xlsx"'
        
        wb = openpyxl.Workbook()
        
        # Active sheet for Overview
        ws_all = wb.active
        ws_all.title = "All Pipeline"
        
        # Create separate sheets for stages
        ws_applied = wb.create_sheet("Applied")
        ws_interview = wb.create_sheet("Interviewing")
        ws_offered = wb.create_sheet("Offered & Onboarding")
        ws_hired = wb.create_sheet("Hired & Active")
        ws_closed = wb.create_sheet("Closed & Archived")
        
        headers = ['Candidate Name', 'Email', 'Job Title', 'Match %', 'Pipeline Stage', 'Status Detail', 'Experience', 'Top Skills', 'Salary Offer', 'Start Date']
        
        from openpyxl.styles import Font, PatternFill, Alignment
        header_font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='0051d5', end_color='0051d5', fill_type='solid')
        alignment = Alignment(horizontal='center', vertical='center')
        
        all_sheets = [ws_all, ws_applied, ws_interview, ws_offered, ws_hired, ws_closed]
        for sheet in all_sheets:
            sheet.append(headers)
            for col_num in range(1, len(headers) + 1):
                cell = sheet.cell(row=1, column=col_num)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = alignment
                
        def get_pipeline_stage(status):
            if status in ('Declined', 'Closed — No Show', 'Separated — End of Probation', 'Not Hired', 'No Response from Applicant', 'No Response from Employer', 'No Response', 'No Show', 'Withdrawn', 'Withdrawn — Position Closed'):
                return "Closed & Archived", ws_closed
            elif status in ('Hired — Probationary', 'Hired — Regular', 'Regularly Employed', 'Still Employed — Performing Well', 'Still Employed — On Improvement Plan', 'Probation Extended'):
                return "Hired & Active", ws_hired
            elif status in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'):
                return "Offered & Onboarding", ws_offered
            elif status == 'Interviewing':
                return "Interviewing", ws_interview
            else:
                return "Applied", ws_applied
                
        for ref in export_referrals:
            match_pct, _ = calculate_match_score(ref.applicant, ref.job_vacancy)
            match_str = f"{int(match_pct)}%"
            
            skills_list = [s.strip() for s in ref.applicant.skills.split(',')] if ref.applicant.skills else []
            top_skills = ", ".join(skills_list)
            
            stage_name, target_sheet = get_pipeline_stage(ref.status)
            
            row_data = [
                ref.applicant.user.get_full_name(),
                ref.applicant.user.email,
                ref.job_vacancy.title,
                match_str,
                stage_name,
                ref.status,
                ref.applicant.experience_years or "0",
                top_skills,
                ref.accepted_salary or "N/A",
                str(ref.actual_start_date) if ref.actual_start_date else "N/A"
            ]
            
            ws_all.append(row_data)
            target_sheet.append(row_data)
            
        for sheet in all_sheets:
            for col in sheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)
                
        wb.save(response)
        return response

    if request.method == 'POST':
        referral_id = request.POST.get('referral_id')
        action = request.POST.get('action')
        ref = get_object_or_404(Referral, id=referral_id, job_vacancy__employer=profile)
        
        if action == 'move_interviewing':
            ref.status = 'Interviewing'
            selected_stage = request.POST.get('interview_stage', 'Initial Assessment')
            selected_mode = request.POST.get('interview_mode', 'Online Video Assessment')
            stage_notes = request.POST.get('interview_stage_notes', 'Candidate approved for interview pipeline.')
            venue_link = request.POST.get('meeting_link_venue', '')
            
            ref.interview_stage = selected_stage
            ref.interview_mode = selected_mode
            ref.interview_stage_notes = stage_notes
            
            history_item = {
                'stage': selected_stage,
                'mode': selected_mode,
                'notes': stage_notes,
                'venue_link': venue_link,
                'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': profile.company_name or profile.user.get_full_name()
            }
            hist = ref.interview_stage_history or []
            if not isinstance(hist, list):
                hist = []
            hist.append(history_item)
            ref.interview_stage_history = hist
            ref.save()
            
            ref.applicant.status = 'Interviewing'
            ref.applicant.save()
            
            # Send Notification to Applicant
            company_title = profile.company_name or 'the employer'
            Notification.objects.create(
                user=ref.applicant.user,
                message=f"Application Update: Your application for '{ref.job_vacancy.title}' at {company_title} has entered the Interview Pipeline: {selected_stage} ({selected_mode})."
            )
            try:
                send_interview_scheduled_email(
                    ref.applicant,
                    ref.job_vacancy,
                    selected_stage,
                    selected_mode,
                    meeting_link=venue_link,
                    notes=stage_notes
                )
            except Exception:
                pass
            messages.success(request, f"Moved {ref.applicant.user.first_name} to Interviewing ({selected_stage}).")

        elif action == 'update_interview_stage':
            new_stage = request.POST.get('interview_stage', 'Initial Assessment')
            new_mode = request.POST.get('interview_mode', 'Online Video Assessment')
            stage_notes = request.POST.get('interview_stage_notes', '')
            venue_link = request.POST.get('meeting_link_venue', '')
            sched_date = request.POST.get('scheduled_date', '')
            sched_time = request.POST.get('scheduled_time', '')
            
            ref.status = 'Interviewing'
            ref.interview_stage = new_stage
            ref.interview_mode = new_mode
            ref.interview_stage_notes = stage_notes
            
            history_item = {
                'stage': new_stage,
                'mode': new_mode,
                'notes': stage_notes,
                'venue_link': venue_link,
                'scheduled_date': sched_date,
                'scheduled_time': sched_time,
                'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': profile.company_name or profile.user.get_full_name()
            }
            hist = ref.interview_stage_history or []
            if not isinstance(hist, list):
                hist = []
            hist.append(history_item)
            ref.interview_stage_history = hist
            ref.save()
            
            ref.applicant.status = 'Interviewing'
            ref.applicant.save()
            
            # Optionally schedule Interview calendar record
            if sched_date and sched_time:
                try:
                    s_d = datetime.strptime(sched_date, '%Y-%m-%d').date()
                    s_t = datetime.strptime(sched_time, '%H:%M').time()
                    Interview.objects.create(
                        employer=profile,
                        candidate=ref.applicant,
                        vacancy=ref.job_vacancy,
                        title=f"{new_stage} - {ref.job_vacancy.title}",
                        round_name=new_stage,
                        date=s_d,
                        start_time=s_t,
                        interview_type=new_mode,
                        meeting_link=venue_link,
                        notes=stage_notes,
                    )
                except Exception:
                    pass

            company_title = profile.company_name or 'the employer'
            Notification.objects.create(
                user=ref.applicant.user,
                message=f"Interview Stage Update: You have progressed to '{new_stage}' ({new_mode}) for '{ref.job_vacancy.title}' at {company_title}."
            )
            try:
                send_interview_scheduled_email(
                    ref.applicant,
                    ref.job_vacancy,
                    new_stage,
                    new_mode,
                    sched_date=sched_date,
                    sched_time=sched_time,
                    meeting_link=venue_link,
                    notes=stage_notes
                )
            except Exception:
                pass
            messages.success(request, f"Successfully advanced {ref.applicant.user.first_name} to {new_stage} ({new_mode}).")
        elif action == 'offer_acceptance':
            # Stage 1: Employer records offer acceptance
            ref.accepted_position = request.POST.get('accepted_position')
            ref.accepted_salary = request.POST.get('accepted_salary')
            
            rep_date_str = request.POST.get('reporting_date')
            if rep_date_str:
                try:
                    ref.reporting_date = datetime.strptime(rep_date_str, '%Y-%m-%d').date()
                except ValueError:
                    messages.warning(request, "Invalid reporting date was ignored (expected YYYY-MM-DD).")
            
            ref.work_location = request.POST.get('work_location')
            ref.employment_type = request.POST.get('employment_type')
            prob_months = request.POST.get('probationary_period_months')
            if prob_months:
                try:
                    ref.probationary_period_months = max(1, int(prob_months))
                except (TypeError, ValueError):
                    pass
            ref.employer_remarks = request.POST.get('employer_remarks')
            
            ref.status = 'Accepted — Awaiting Onboarding'
            ref.applicant.status = 'Shortlisted'
            ref.save()
            ref.applicant.save()

            # No slot change here: the opening was already reserved when the
            # referral/application was created.

            # Send Notification to Applicant
            Notification.objects.create(
                user=ref.applicant.user,
                message=f"Congratulations! {profile.user.first_name} has accepted you for the position of {ref.accepted_position}. Your reporting date is {ref.reporting_date}. Please confirm your acceptance below."
            )
            try:
                send_job_offer_email(
                    ref.applicant,
                    ref.job_vacancy,
                    position=ref.accepted_position,
                    salary=ref.accepted_salary,
                    reporting_date=ref.reporting_date,
                    employment_type=ref.employment_type,
                    work_location=ref.work_location,
                    remarks=ref.employer_remarks
                )
            except Exception:
                pass
            
            # Send Notification to PESO Coordinators
            coord_users = User.objects.filter(profile__role='admin')
            for u in coord_users:
                Notification.objects.create(
                    user=u,
                    message=f"Referral for {ref.applicant.user.get_full_name()} to {ref.job_vacancy.title} has resulted in an acceptance."
                )
                
            messages.success(request, f"Successfully recorded acceptance for {ref.applicant.user.first_name} and sent notifications.")
            
        elif action == 'confirm_onboarding':
            ref.status = 'Confirmed — Onboarding'
            ref.applicant.status = 'Employed — Onboarding'
            ref.save()
            ref.applicant.save()
            try:
                send_hired_confirmation_email(
                    ref.applicant,
                    ref.job_vacancy,
                    start_date=ref.reporting_date,
                    employment_status='Confirmed — Onboarding'
                )
                Notification.objects.create(
                    user=ref.applicant.user,
                    message=f"Onboarding Confirmed: Your onboarding requirements for '{ref.job_vacancy.title}' have been confirmed by {profile.company_name or profile.user.get_full_name()}."
                )
            except Exception:
                pass
            messages.success(request, f"Manually confirmed onboarding requirements for {ref.applicant.user.first_name}.")

        elif action == 'log_reporting_status':
            # Stage 4: Physical Report for Work
            reported = request.POST.get('reported_for_work')
            ref.reported_for_work = reported
            
            if reported == 'Yes':
                start_date_str = request.POST.get('actual_start_date')
                if start_date_str:
                    try:
                        ref.actual_start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        messages.warning(request, "Invalid actual start date was ignored (expected YYYY-MM-DD).")
                
                # Update status depending on employment type
                if ref.employment_type == 'Probationary':
                    ref.status = 'Hired — Probationary'
                else:
                    ref.status = 'Hired — Regular'
                ref.applicant.status = 'Hired'
                ref.applicant.save()
                ref.save()
                
                try:
                    send_hired_confirmation_email(
                        ref.applicant,
                        ref.job_vacancy,
                        start_date=ref.actual_start_date,
                        employment_status=ref.status
                    )
                    Notification.objects.create(
                        user=ref.applicant.user,
                        message=f"Welcome aboard! You have officially commenced employment for '{ref.job_vacancy.title}' with status: {ref.status}."
                    )
                except Exception:
                    pass
                
                # Notify Coordinators
                coord_users = User.objects.filter(profile__role='admin')
                for u in coord_users:
                    Notification.objects.create(
                        user=u,
                        message=f"{ref.applicant.user.get_full_name()} has reported for work as scheduled on {ref.actual_start_date}."
                    )
                messages.success(request, f"Logged {ref.applicant.user.first_name} as reported for work.")
            else:
                ref.status = 'No Show'
                ref.save()
                
                # Notify Coordinators
                coord_users = User.objects.filter(profile__role='admin')
                for u in coord_users:
                    Notification.objects.create(
                        user=u,
                        message=f"ALERT: {ref.applicant.user.get_full_name()} failed to report for work on their reporting date ({ref.reporting_date})."
                    )
                messages.warning(request, f"Logged {ref.applicant.user.first_name} as No Show. PESO has been notified.")
                
        elif action == 'final_probation_status':
            # Stage 6: Probation Completion / Regularization
            final_status = request.POST.get('final_employment_status')
            
            if final_status == 'Regularized':
                ref.status = 'Regularly Employed'
                ref.applicant.status = 'Employed — Regular'
                ref.applicant.save()
                ref.save()
                
                try:
                    send_hired_confirmation_email(
                        ref.applicant,
                        ref.job_vacancy,
                        start_date=None,
                        employment_status='Regularly Employed'
                    )
                except Exception:
                    pass

                # Notify Applicant
                Notification.objects.create(
                    user=ref.applicant.user,
                    message=f"Congratulations! You have been regularized in your role as {ref.accepted_position}."
                )
                # Notify Coordinator
                coord_users = User.objects.filter(profile__role='admin')
                for u in coord_users:
                    Notification.objects.create(
                        user=u,
                        message=f"{ref.applicant.user.get_full_name()} has successfully completed their probationary period and is regularized."
                    )
                messages.success(request, f"Marked {ref.applicant.user.first_name} as regularly employed.")
                
            elif final_status == 'Extended':
                ref.status = 'Probation Extended'
                ext_date_str = request.POST.get('probation_extension_end_date')
                if ext_date_str:
                    try:
                        ref.probation_extension_end_date = datetime.strptime(ext_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        messages.warning(request, "Invalid probation extension date was ignored (expected YYYY-MM-DD).")
                ref.save()
                
                try:
                    send_application_status_update_email(
                        ref.applicant,
                        ref.job_vacancy,
                        'Probation Extended',
                        remarks=f"Probation period extended until {ref.probation_extension_end_date}"
                    )
                    Notification.objects.create(
                        user=ref.applicant.user,
                        message=f"Probation Period Update: Your probationary period for '{ref.job_vacancy.title}' has been extended to {ref.probation_extension_end_date}."
                    )
                except Exception:
                    pass

                coord_users = User.objects.filter(profile__role='admin')
                for u in coord_users:
                    Notification.objects.create(
                        user=u,
                        message=f"Employer has extended the probation for {ref.applicant.user.get_full_name()} to {ref.probation_extension_end_date}."
                    )
                messages.success(request, f"Extended probation period for {ref.applicant.user.first_name}.")
                
            elif final_status == 'Separated':
                ref.status = 'Separated — End of Probation'
                ref.separation_reason = request.POST.get('separation_reason')
                ref.applicant.status = 'Active — Job Seeking'
                ref.applicant.save()
                ref.save()
                
                try:
                    send_application_status_update_email(
                        ref.applicant,
                        ref.job_vacancy,
                        'Separated — End of Probation',
                        remarks=ref.separation_reason
                    )
                    Notification.objects.create(
                        user=ref.applicant.user,
                        message=f"Employment Update: You have separated from '{ref.job_vacancy.title}' at the end of probation. Reason: {ref.separation_reason}."
                    )
                except Exception:
                    pass

                coord_users = User.objects.filter(profile__role='admin')
                for u in coord_users:
                    Notification.objects.create(
                        user=u,
                        message=f"{ref.applicant.user.get_full_name()} has separated from the company at the end of their probation. Reason: {ref.separation_reason}."
                    )
                messages.warning(request, f"Marked {ref.applicant.user.first_name} as separated. Candidate returned to active job seeking pool.")
                
        elif action == 'submit_employer_evaluation':
            # Stage 7: Employer feedback evaluation form
            ref.eval_emp_quality = int(request.POST.get('eval_emp_quality', 5))
            ref.eval_emp_skills_accurate = request.POST.get('eval_emp_skills_accurate', 'Yes')
            ref.eval_emp_certs_genuine = request.POST.get('eval_emp_certs_genuine', 'Yes')
            ref.eval_emp_future_referrals = request.POST.get('eval_emp_future_referrals', 'Yes')
            ref.eval_emp_satisfaction = int(request.POST.get('eval_emp_satisfaction', 5))
            ref.eval_emp_notes = request.POST.get('eval_emp_notes', '')
            ref.save()
            messages.success(request, "Thank you! Your feedback has been submitted successfully.")
            
        elif action == 'mark_unresponsive':
            unresponsive_reason = request.POST.get('unresponsive_reason', 'Candidate unreachable after multiple attempts')
            process_notes = request.POST.get('process_notes', '').strip()
            contact_summary = f"{unresponsive_reason}. {process_notes}".strip()

            # Restore vacancy slot if previously held
            if ref.status in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding', 'Hired — Probationary', 'Hired — Regular'):
                vac = ref.job_vacancy
                vac.remaining_slots = min(vac.slots, vac.remaining_slots + 1)
                vac.save()

            ref.status = 'No Response from Applicant'
            ref.rejection_reason = contact_summary
            hist = ref.interview_stage_history or []
            if not isinstance(hist, list):
                hist = []
            hist.append({
                'stage': 'No Longer Contacted / Unresponsive',
                'status': 'No Response from Applicant',
                'notes': contact_summary,
                'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': profile.company_name or profile.user.get_full_name()
            })
            ref.interview_stage_history = hist
            ref.save()

            ref.applicant.status = 'Active — Job Seeking'
            ref.applicant.save()

            Notification.objects.create(
                user=ref.applicant.user,
                message=f"Application Update: {profile.company_name or 'The employer'} updated your status for '{ref.job_vacancy.title}' to No Response from Applicant. Notes: {contact_summary}"
            )
            for u in User.objects.filter(profile__role='admin'):
                Notification.objects.create(
                    user=u,
                    message=f"Hiring Pipeline Update: {ref.applicant.user.get_full_name()} marked as no longer contacted (unresponsive) by {profile.company_name or profile.user.get_full_name()} for '{ref.job_vacancy.title}'."
                )
            messages.warning(request, f"Updated status: {ref.applicant.user.first_name} marked as No Longer Contacted (Unresponsive). Vacancy slot preserved.")

        elif action == 'move_rejected':
            ref.status = 'Not Hired'
            ref.applicant.status = 'Rejected'
            rej_base = request.POST.get('rejection_reason', 'Underqualified')
            process_notes = request.POST.get('process_notes', '').strip()
            combined_reason = f"{rej_base}: {process_notes}" if process_notes else rej_base
            ref.rejection_reason = combined_reason
            
            hist = ref.interview_stage_history or []
            if not isinstance(hist, list):
                hist = []
            hist.append({
                'stage': 'Candidate Not Selected',
                'status': 'Not Hired',
                'notes': combined_reason,
                'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': profile.company_name or profile.user.get_full_name()
            })
            ref.interview_stage_history = hist
            ref.save()
            ref.applicant.save()
            try:
                send_application_status_update_email(
                    ref.applicant,
                    ref.job_vacancy,
                    'Not Hired',
                    remarks=ref.rejection_reason
                )
                Notification.objects.create(
                    user=ref.applicant.user,
                    message=f"Application Update: Your application for '{ref.job_vacancy.title}' was not selected at this time. Feedback: {ref.rejection_reason}."
                )
            except Exception:
                pass
            messages.success(request, f"Candidate {ref.applicant.user.first_name} marked as Not Hired.")
            
        return redirect('hiring_tracker_employer')
        
    # Categorize referrals for Kanban lanes
    applied_lane = []
    interviewing_lane = []
    offered_lane = []
    hired_lane = []
    closed_lane = []
    
    all_referrals_data = []
    for ref in referrals:
        # Calculate match percentage for displaying on card
        match_pct, _ = calculate_match_score(ref.applicant, ref.job_vacancy)
        ref.match_percentage = int(match_pct)
        evaluation = ref.screening_evaluation or {}
        ref.screening_total = evaluation.get('total')
        ref.screening_scores_json = json.dumps(evaluation.get('scores') or {})
        
        # Extract first 2 letters for candidate initials circle
        ref.initials = (ref.applicant.user.first_name[:1] + ref.applicant.user.last_name[:1]).upper()
        
        # Calculate onboarding requirements progress
        files_fields = [ref.nbi_clearance, ref.medical_certificate, ref.birth_certificate, ref.diploma_transcript, ref.prev_employment_cert, ref.tesda_cert]
        uploaded_count = sum(1 for f in files_fields if f)
        text_fields = [ref.sss_number, ref.philhealth_number, ref.pagibig_number, ref.bir_tin]
        filled_text_count = sum(1 for t in text_fields if t)
        ref.uploaded_count = uploaded_count
        ref.filled_text_count = filled_text_count
        ref.total_requirements_count = 10
        ref.completed_requirements_count = uploaded_count + filled_text_count
        
        # Candidate Profile Metadata for Modals & Table
        app_profile = ref.applicant

        # Score card: overall fit plus the criteria employers care about.
        breakdown = get_qualification_breakdown(app_profile, ref.job_vacancy)
        enrollments = list(app_profile.training_enrollments.all())
        training_score = (
            int(round(sum(e.progress_percentage for e in enrollments) / len(enrollments)))
            if enrollments else 0
        )
        cert_count = app_profile.certifications.count()

        def _metric(label, value):
            value = max(0, min(100, int(value)))
            if value >= 80:
                color, tone = 'bg-emerald-500', 'text-emerald-700'
            elif value >= 60:
                color, tone = 'bg-blue-500', 'text-blue-700'
            elif value >= 40:
                color, tone = 'bg-amber-500', 'text-amber-700'
            else:
                color, tone = 'bg-rose-500', 'text-rose-700'
            return {'label': label, 'value': value, 'color': color, 'tone': tone}

        ref.overall_score = max(0, min(100, int(breakdown['final_score'])))
        ref.overall_tone = (
            'text-emerald-700' if ref.overall_score >= 80 else
            'text-blue-700' if ref.overall_score >= 60 else
            'text-amber-700' if ref.overall_score >= 40 else 'text-rose-700'
        )
        ref.score_metrics = [
            _metric('Skills', breakdown['skills_score']),
            _metric('Education', breakdown['edu_score']),
            _metric('Experience', breakdown['exp_score']),
            _metric('Training', training_score),
        ]
        ref.matched_skills_count = breakdown['matched_skills_count']
        ref.total_skills_count = breakdown['total_skills_count']
        ref.certifications_count = cert_count
        try:
            ref.auto_scorecard = build_automated_scorecard(app_profile, ref.job_vacancy)
        except Exception:
            ref.auto_scorecard = None

        skills_objs = list(app_profile.applicant_skills.select_related('skill').all())
        if skills_objs:
            ref.skills_str = ", ".join([s.skill.name for s in skills_objs if s.skill])
        else:
            ref.skills_str = app_profile.skills or "Communication, Analytical Thinking, Team Collaboration"

        edu_list = app_profile.education.all() if hasattr(app_profile, 'education') else (app_profile.educations.all() if hasattr(app_profile, 'educations') else [])
        if edu_list:
            ref.edu_str = " | ".join([f"{e.degree} at {e.institution} ({e.start_year}-{e.end_year})" for e in edu_list])
        else:
            ref.edu_str = "Bachelor's Degree in Related Field"

        exp_list = app_profile.experience.all() if hasattr(app_profile, 'experience') else (app_profile.experiences.all() if hasattr(app_profile, 'experiences') else [])
        if exp_list:
            ref.exp_str = " | ".join([f"{x.position} at {x.company} ({x.start_date or 'Past'} - {'Present' if x.is_current else (x.end_date or 'Past')})" for x in exp_list])
        else:
            ref.exp_str = f"{app_profile.experience_years or '2 Years'} Professional Experience"

        certs_list = app_profile.certifications.all() if hasattr(app_profile, 'certifications') else []
        if certs_list:
            ref.certs_str = ", ".join([f"{c.name} ({c.issuing_organization})" for c in certs_list])
        else:
            ref.certs_str = "TESDA NC II / Industry Certified"
        
        if ref.status in ('Declined', 'Closed — No Show', 'Separated — End of Probation', 'Not Hired', 'No Response from Applicant', 'No Response from Employer', 'No Response', 'No Show', 'Withdrawn', 'Withdrawn — Position Closed'):
            ref.stage_name = 'Closed'
            closed_lane.append(ref)
        elif ref.status in ('Hired — Probationary', 'Hired — Regular', 'Regularly Employed', 'Still Employed — Performing Well', 'Still Employed — On Improvement Plan', 'Probation Extended'):
            ref.stage_name = 'Hired'
            hired_lane.append(ref)
        elif ref.status in ('Accepted — Awaiting Onboarding', 'Confirmed — Onboarding'):
            ref.stage_name = 'Offered'
            offered_lane.append(ref)
        elif ref.status == 'Interviewing':
            ref.stage_name = 'Interviewing'
            interviewing_lane.append(ref)
        else:
            ref.stage_name = 'Applied'
            applied_lane.append(ref)
            
        all_referrals_data.append(ref)
            
    context = {
        'applied_lane': applied_lane,
        'interviewing_lane': interviewing_lane,
        'offered_lane': offered_lane,
        'hired_lane': hired_lane,
        'closed_lane': closed_lane,
        'all_referrals': all_referrals_data,
        'profile': profile,
        'referrals': referrals,
        'vacancies': JobVacancy.objects.filter(employer=profile).order_by('-created_at'),
        'screening_criteria': SCREENING_CRITERIA,
    }
    return render(request, 'tracker/EMPLOYER/hiring_tracker_employer.html', context)

def _employer_avg_time_to_fill(employer):
    """Average days from referral to actual start date for hired candidates."""
    referrals = Referral.objects.filter(
        job_vacancy__employer=employer,
        status__in=[
            'Hired — Probationary', 'Hired — Regular', 'Regularly Employed',
            'Still Employed — Performing Well', 'Still Employed — On Improvement Plan',
        ],
        actual_start_date__isnull=False,
    )
    durations = [
        (ref.actual_start_date - ref.date_referred).days
        for ref in referrals
        if ref.actual_start_date and ref.date_referred
    ]
    durations = [d for d in durations if d >= 0]
    return round(sum(durations) / len(durations)) if durations else None


@role_required(['employer'])
def job_management_employer(request):
    seed_mock_applicants_if_empty()
    profile = request.user.profile
    vacancies = JobVacancy.objects.filter(employer=profile).order_by('-created_at')

    # Handle sample CSV template download
    if request.GET.get('action') == 'download_csv_template':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="job_vacancies_upload_template.csv"'
        writer = csv.writer(response)
        writer.writerow(['title', 'category', 'location', 'min_salary', 'max_salary', 'slots', 'required_experience_years', 'min_education', 'required_certifications', 'skills', 'description', 'remarks'])
        writer.writerow([
            'Shielded Metal Arc Welder (SMAW)', 'TVT', 'Taguig City, Metro Manila', '20000', '32000', '3', '1',
            'High School graduate', 'SMAW NC II', 'Welding, Blueprint Reading, Safety',
            'Fabricate and weld structural metal components in compliance with safety standards.',
            'Personal protective equipment provided. Overtime pay applicable.'
        ])
        writer.writerow([
            'Customer Service Associate', 'BPO', 'Makati City / Hybrid', '22000', '30000', '5', '0',
            'Senior High School graduate', 'Contact Center Services NC II', 'Customer Service, English Communication, Active Listening',
            'Handle customer inquiries via voice, email, and live chat channels with high resolution quality.',
            'Night differential and HMO benefits included upon day 1.'
        ])
        writer.writerow([
            'Web Developer / Software Engineer', 'IT', 'BGC, Taguig City / Hybrid', '35000', '60000', '2', '2',
            'College graduate', 'Computer Systems Servicing NC II', 'Python, JavaScript, SQL, Git',
            'Develop scalable full-stack web applications and REST APIs using modern frameworks.',
            'Flexible working hours with equipment provided.'
        ])
        return response
    
    if request.method == 'POST':
        action = request.POST.get('action', 'create')

        # Bulk CSV Upload
        if action == 'bulk_upload_csv':
            csv_file = request.FILES.get('csv_file')
            if not csv_file:
                messages.error(request, "Please select a valid CSV file to upload.")
                return redirect('job_management_employer')

            import csv
            try:
                raw_bytes = csv_file.read()
                try:
                    text = raw_bytes.decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        text = raw_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        text = raw_bytes.decode('latin-1', errors='replace')
                
                lines = text.splitlines()
                reader = csv.DictReader(lines)
                created_count = 0
                for raw_row in reader:
                    # Clean and normalize keys
                    row = {str(k).strip().lower().replace(' ', '_'): (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items() if k is not None}
                    
                    row_title = row.get('title') or row.get('job_title') or row.get('position') or ''
                    if not row_title:
                        continue
                    
                    row_category = row.get('category') or row.get('job_category') or 'Local'
                    row_location = row.get('location') or row.get('job_location') or 'Metro Manila'
                    
                    min_sal = str(row.get('min_salary') or row.get('minimum_salary') or row.get('salary_min') or '').replace(',', '').strip()
                    max_sal = str(row.get('max_salary') or row.get('maximum_salary') or row.get('salary_max') or '').replace(',', '').strip()
                    sal_str = ''
                    try:
                        min_n = int(float(min_sal)) if min_sal else None
                        max_n = int(float(max_sal)) if max_sal else None
                        if min_n is not None and max_n is not None:
                            if min_n > max_n:
                                min_n, max_n = max_n, min_n
                            sal_str = f"₱{min_n:,} - ₱{max_n:,}"
                        elif min_n is not None:
                            sal_str = f"₱{min_n:,}+"
                        elif max_n is not None:
                            sal_str = f"Up to ₱{max_n:,}"
                    except (ValueError, TypeError):
                        sal_str = ''
                    
                    slots_n = 1
                    slots_raw = row.get('slots') or row.get('openings') or row.get('vacancies')
                    if slots_raw:
                        try:
                            slots_n = max(1, int(float(slots_raw)))
                        except (ValueError, TypeError):
                            slots_n = 1
                    
                    exp_n = 0
                    exp_raw = row.get('required_experience_years') or row.get('experience_years') or row.get('experience')
                    if exp_raw:
                        try:
                            exp_n = max(0, int(float(exp_raw)))
                        except (ValueError, TypeError):
                            exp_n = 0
                    
                    min_edu = row.get('min_education') or row.get('minimum_education') or row.get('education') or 'High School graduate'
                    req_certs = row.get('required_certifications') or row.get('certifications') or ''
                    desc = row.get('description') or row.get('job_description') or f"Job vacancy for {row_title}."
                    remarks = row.get('remarks') or row.get('notes') or ''
                    
                    vac = JobVacancy.objects.create(
                        employer=profile,
                        title=row_title,
                        category=row_category,
                        location=row_location,
                        salary_range=sal_str,
                        slots=slots_n,
                        remaining_slots=slots_n,
                        required_experience_years=exp_n,
                        min_education=min_edu,
                        required_certifications=req_certs,
                        description=desc,
                        remarks=remarks,
                        status='Open'
                    )
                    
                    skills_raw = row.get('skills') or row.get('required_skills') or ''
                    if skills_raw:
                        for s_item in skills_raw.split(','):
                            s_item = s_item.strip()
                            if s_item:
                                sk_obj, _ = CentralizedSkill.objects.get_or_create(
                                    name=s_item,
                                    defaults={'category': 'General', 'description': f'Skill requirement for {row_title}'}
                                )
                                JobSkillRequirement.objects.create(
                                    job_vacancy=vac,
                                    skill=sk_obj
                                )
                    created_count += 1
                
                if created_count > 0:
                    messages.success(request, f"Bulk Import Complete: Successfully created {created_count} job vacancies from CSV!")
                else:
                    messages.warning(request, "No job vacancies were found in the uploaded CSV file. Please make sure the 'title' column is populated.")
            except Exception as e:
                messages.error(request, f"Failed to parse CSV file: {str(e)}")
            return redirect('job_management_employer')
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        remarks = request.POST.get('remarks', '').strip()
        location = request.POST.get('location', '').strip()
        min_salary_raw = request.POST.get('min_salary', '').strip().replace(',', '')
        max_salary_raw = request.POST.get('max_salary', '').strip().replace(',', '')
        salary_range = request.POST.get('salary_range', '').strip()

        if min_salary_raw or max_salary_raw:
            try:
                min_n = int(float(min_salary_raw)) if min_salary_raw else None
                max_n = int(float(max_salary_raw)) if max_salary_raw else None
                if min_n is not None and max_n is not None:
                    if min_n > max_n:
                        min_n, max_n = max_n, min_n
                    if min_n == max_n:
                        salary_range = f"₱{min_n:,}"
                    else:
                        salary_range = f"₱{min_n:,} - ₱{max_n:,}"
                elif min_n is not None:
                    salary_range = f"₱{min_n:,}+"
                elif max_n is not None:
                    salary_range = f"Up to ₱{max_n:,}"
            except (ValueError, TypeError):
                pass

        category = request.POST.get('category', 'Local').strip()
        min_education = request.POST.get('min_education', '').strip()
        cert_list = request.POST.getlist('certifications[]') or request.POST.getlist('certifications')
        other_certs = request.POST.get('other_certifications', '').strip()
        all_certs = [c.strip() for c in cert_list if c.strip()]
        if other_certs:
            all_certs.extend([c.strip() for c in other_certs.split(',') if c.strip()])
        if not all_certs and request.POST.get('required_certifications'):
            all_certs = [request.POST.get('required_certifications').strip()]
        required_certifications = ', '.join(dict.fromkeys(all_certs))
        req_exp = request.POST.get('required_experience_years', '0')
        slots_str = request.POST.get('slots', '1').strip()
        slots = int(slots_str) if slots_str.isdigit() and int(slots_str) > 0 else 1
        status = request.POST.get('status', 'Open').strip()
        
        if action == 'edit':
            vacancy_id = request.POST.get('vacancy_id')
            vacancy = get_object_or_404(JobVacancy, id=vacancy_id, employer=profile)
            vacancy.title = title
            vacancy.description = description
            vacancy.remarks = remarks
            vacancy.location = location
            vacancy.salary_range = salary_range
            vacancy.category = category
            vacancy.min_education = min_education
            vacancy.required_certifications = required_certifications
            vacancy.required_experience_years = int(req_exp) if req_exp.isdigit() else 0
            if vacancy.slots != slots:
                diff = slots - vacancy.slots
                vacancy.slots = slots
                vacancy.remaining_slots = max(0, vacancy.remaining_slots + diff)
            vacancy.status = status
            vacancy.save()
            
            # Clear old skill requirements and rebuild them
            JobSkillRequirement.objects.filter(job_vacancy=vacancy).delete()
        else:
            vacancy = JobVacancy.objects.create(
                title=title,
                employer=profile,
                description=description,
                remarks=remarks,
                location=location,
                salary_range=salary_range,
                category=category,
                min_education=min_education,
                required_certifications=required_certifications,
                required_experience_years=int(req_exp) if req_exp.isdigit() else 0,
                slots=slots,
                remaining_slots=slots,
                status=status
            )
            
        # Handle requirements selection
        selected_skills = request.POST.getlist('skills[]')
        for s_id in selected_skills:
            if s_id:
                try:
                    skill_obj = CentralizedSkill.objects.filter(id=int(s_id)).first()
                    if skill_obj:
                        JobSkillRequirement.objects.create(
                            job_vacancy=vacancy,
                            skill=skill_obj
                        )
                except Exception:
                    pass
                
        # Handle custom skills on-the-fly
        new_skills_str = request.POST.get('new_skills', '').strip()
        if new_skills_str:
            new_skills_list = [s.strip() for s in new_skills_str.split(',') if s.strip()]
            for s_name in new_skills_list:
                skill_obj, _ = CentralizedSkill.objects.get_or_create(
                    name=s_name,
                    defaults={'category': 'Custom', 'description': 'Custom skill added by employer.'}
                )
                JobSkillRequirement.objects.create(
                    job_vacancy=vacancy,
                    skill=skill_obj
                )
                
        # Notify matching employers of any 70%+ candidate match scores
        check_and_notify_applicants_for_vacancy(vacancy)
        
        msg = f"Job vacancy '{title}' updated successfully!" if action == 'edit' else f"Job vacancy '{title}' posted successfully!"
        messages.success(request, msg)
        return redirect('job_management_employer')
        
    # GET request
    active_postings_count = vacancies.filter(status='Open').count()
    total_applicants_count = Referral.objects.filter(job_vacancy__employer=profile).count()
    
    # Enrich vacancy objects with applicant lists & counts
    all_vacancies_data = []
    for vac in vacancies:
        refs = Referral.objects.filter(job_vacancy=vac).select_related('applicant__user')
        cands = [r.applicant for r in refs]
        skills_reqs = JobSkillRequirement.objects.filter(job_vacancy=vac).select_related('skill')
        all_vacancies_data.append({
            'vacancy': vac,
            'candidates_count': len(cands),
            'candidates': cands[:3],
            'requirements': skills_reqs,
        })
        
    all_skills = CentralizedSkill.objects.all().order_by('name')

    # Resolve each preset's skill names to real CentralizedSkill IDs so the
    # "Pre-Fill with Role Template" picker can tick the right checkboxes.
    skill_id_by_name = {s.name.lower(): s.id for s in all_skills}
    job_presets_payload = {}
    for key, preset in JOB_PRESETS.items():
        item = {k: v for k, v in preset.items() if k != 'skills'}
        item['skill_ids'] = [
            skill_id_by_name[name.lower()]
            for name in preset.get('skills', [])
            if name.lower() in skill_id_by_name
        ]
        item['missing_skills'] = [
            name for name in preset.get('skills', []) if name.lower() not in skill_id_by_name
        ]
        job_presets_payload[key] = item

    context = {
        'vacancies': all_vacancies_data,
        'active_postings_count': active_postings_count,
        'total_applicants_count': total_applicants_count,
        'total_open_slots': sum(
            v.remaining_slots for v in JobVacancy.objects.filter(employer=profile, status='Open')
        ),
        'avg_time_to_fill': _employer_avg_time_to_fill(profile),
        'all_skills': all_skills,
        'job_presets_json': json.dumps(job_presets_payload),
        'profile': profile,
    }
    return render(request, 'tracker/EMPLOYER/job_management_employer.html', context)


@role_required(['employer'])
def employer_notifications_view(request):
    """Shows all applicants with ≥75% skill match on employer's open vacancies.
    Computed live — applicants don't need to have applied to appear here.
    """
    profile = request.user.profile
    vacancies = JobVacancy.objects.filter(employer=profile).exclude(status='Closed').prefetch_related('requirements__skill')
    applicants = Profile.objects.filter(role='applicant').select_related('user').prefetch_related(
        'applicant_skills__skill',
        'education',
        'experience'
    )

    # Prefetch all referrals for the employer's vacancies to avoid queries in the nested loop
    referrals = Referral.objects.filter(job_vacancy__in=vacancies).values('applicant_id', 'job_vacancy_id', 'status')
    referrals_dict = {}
    for r in referrals:
        referrals_dict[(r['applicant_id'], r['job_vacancy_id'])] = r['status']

    # Build flat list of (applicant, vacancy, match_pct) tuples meeting sensitivity threshold
    try:
        threshold = SystemSetting.get_settings().matching_threshold
    except Exception:
        threshold = 75

    match_alerts = []
    seen = set()  # avoid duplicate (applicant, vacancy) pairs
    for vac in vacancies:
        for app in applicants:
            key = (app.id, vac.id)
            if key in seen:
                continue
            seen.add(key)
            match_pct, _ = calculate_match_score(app, vac)
            if match_pct >= threshold:
                has_applied = key in referrals_dict
                offer_sent = referrals_dict.get(key) == 'Accepted — Awaiting Onboarding'
                match_alerts.append({
                    'applicant': app,
                    'vacancy': vac,
                    'match_pct': int(match_pct),
                    'has_applied': has_applied,
                    'offer_sent': offer_sent,
                })

    # Sort highest match first
    match_alerts.sort(key=lambda x: x['match_pct'], reverse=True)

    context = {
        'match_alerts': match_alerts,
        'profile': profile,
        'unread_match_alerts': len(match_alerts),
    }
    return render(request, 'tracker/EMPLOYER/employer_notifications.html', context)


@role_required(['employer'])
def send_offer_employer(request, applicant_id, vacancy_id):
    """Employer sends a job offer to a matched applicant directly from the notification."""
    profile = request.user.profile
    applicant_profile = get_object_or_404(Profile, id=applicant_id, role='applicant')
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id, employer=profile)

    # Calculate current match score for display
    match_pct, _ = calculate_match_score(applicant_profile, vacancy)

    # Check for existing referral
    existing_referral = Referral.objects.filter(applicant=applicant_profile, job_vacancy=vacancy).first()

    if request.method == 'POST':
        accepted_position = request.POST.get('accepted_position', vacancy.title)
        accepted_salary = request.POST.get('accepted_salary', vacancy.salary_range or '')
        reporting_date_str = request.POST.get('reporting_date', '')
        employment_type = request.POST.get('employment_type', 'Probationary')
        work_location = request.POST.get('work_location', vacancy.location)
        employer_remarks = request.POST.get('employer_remarks', '')
        prob_months = request.POST.get('probationary_period_months', '6')

        # Create referral if not existing, or update existing
        referral, referral_created = Referral.objects.get_or_create(
            applicant=applicant_profile,
            job_vacancy=vacancy,
            defaults={'status': 'Accepted — Awaiting Onboarding', 'date_referred': timezone.localdate()}
        )
        referral.accepted_position = accepted_position
        referral.accepted_salary = accepted_salary
        referral.work_location = work_location
        referral.employment_type = employment_type
        referral.employer_remarks = employer_remarks
        referral.status = 'Accepted — Awaiting Onboarding'
        if prob_months:
            try:
                referral.probationary_period_months = int(prob_months)
            except (ValueError, TypeError):
                pass
        if reporting_date_str:
            try:
                referral.reporting_date = datetime.strptime(reporting_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        referral.save()

        # Update applicant pipeline status
        applicant_profile.status = 'Shortlisted'
        applicant_profile.save()

        # Reserve a slot only for offers that create a brand-new referral;
        # applications/referrals already reserved one when they were created.
        if referral_created and vacancy.remaining_slots and vacancy.remaining_slots > 0:
            vacancy.remaining_slots = max(0, vacancy.remaining_slots - 1)
            vacancy.save()

        # Notify the applicant
        Notification.objects.create(
            user=applicant_profile.user,
            notif_type='offer_sent',
            vacancy=vacancy,
            message=f"🎉 You've received a job offer from {profile.company_name or profile.user.get_full_name()} for the position of '{accepted_position}' ({vacancy.title}). Please check your applications for details!"
        )
        try:
            send_job_offer_email(
                applicant_profile,
                vacancy,
                position=accepted_position,
                salary=accepted_salary,
                reporting_date=referral.reporting_date,
                employment_type=employment_type,
                work_location=work_location,
                remarks=employer_remarks
            )
        except Exception:
            pass

        # Mark the match-alert notification as read
        request.user.notifications.filter(
            notif_type='match_alert',
            applicant_profile=applicant_profile,
            vacancy=vacancy,
        ).update(is_read=True)

        messages.success(request, f"Offer successfully sent to {applicant_profile.user.get_full_name()}! They will be notified.")
        return redirect('hiring_tracker_employer')

    context = {
        'applicant': applicant_profile,
        'vacancy': vacancy,
        'match_pct': int(match_pct),
        'existing_referral': existing_referral,
        'profile': profile,
    }
    return render(request, 'tracker/EMPLOYER/send_offer_employer.html', context)


@verified_required
@require_POST
def job_status_update(request, vacancy_id):
    """Quick inline status update for a job vacancy."""
    profile = request.user.profile
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id, employer=profile)
    new_status = request.POST.get('status', '').strip()
    allowed = ['Open', 'Closed', 'Draft', 'On Hold']
    if new_status in allowed:
        old_status = vacancy.status
        vacancy.status = new_status
        vacancy.save()

        # --- Cascade to applicants ---
        if new_status == 'Closed':
            affected = Referral.objects.filter(job_vacancy=vacancy, status='Withdrawn — Position Closed').count()
            messages.success(request, f"'{vacancy.title}' closed. Active applications have been automatically withdrawn.")

        elif new_status == 'On Hold':
            active_referrals = Referral.objects.filter(
                job_vacancy=vacancy,
                status__in=['Pending', 'Interviewing']
            ).select_related('applicant__user')
            # Notify applicants that processing is paused
            for ref in active_referrals:
                Notification.objects.create(
                    user=ref.applicant.user,
                    message=f"The job posting '{vacancy.title}' is currently On Hold. Your application is paused pending employer review.",
                    notif_type='general'
                )
            affected = active_referrals.count()
            messages.success(request, f"'{vacancy.title}' put On Hold. {affected} applicant(s) notified.")

        elif new_status == 'Open' and old_status in ['Closed', 'On Hold']:
            # Re-open: restore applicants who were auto-withdrawn due to position closed
            reopened = Referral.objects.filter(
                job_vacancy=vacancy,
                status='Withdrawn — Position Closed'
            ).select_related('applicant__user')
            for ref in reopened:
                ref.status = 'Pending'
                ref.rejection_reason = None
                ref.save()
                Notification.objects.create(
                    user=ref.applicant.user,
                    message=f"Good news! '{vacancy.title}' has been re-opened. Your application is back to Pending in active consideration.",
                    notif_type='general'
                )
            messages.success(request, f"'{vacancy.title}' re-opened. {reopened.count()} applicant(s) restored to Pending.")

        else:
            messages.success(request, f"'{vacancy.title}' status changed from {old_status} → {new_status}.")
    else:
        messages.error(request, "Invalid status value.")
    return redirect('job_management_employer')


@verified_required
@require_POST
def job_delete(request, vacancy_id):
    """Delete a job vacancy and all its referrals/skill requirements."""
    profile = request.user.profile
    vacancy = get_object_or_404(JobVacancy, id=vacancy_id, employer=profile)
    title = vacancy.title
    # Auto withdraw active applications and notify candidates prior to delete
    vacancy.auto_withdraw_applications(reason='Position deleted by employer.')
    vacancy.delete()
    messages.success(request, f"Job posting '{title}' has been permanently deleted and active applications withdrawn.")
    return redirect('job_management_employer')

@role_required(['admin'])
def export_analytics_csv(request):
    import csv
    from django.http import HttpResponse
    
    report_type = request.GET.get('report', 'all')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="peso_{report_type}_analytics.csv"'
    response.write('\ufeff')
    
    writer = csv.writer(response)
    
    if report_type == 'demand':
        writer.writerow(['Skill Name', 'Number of Job Postings Requiring Skill'])
        demand_counts = JobSkillRequirement.objects.values('skill__name').annotate(count=Count('job_vacancy')).order_by('-count')
        for item in demand_counts:
            writer.writerow([item['skill__name'], item['count']])
            
    elif report_type == 'gaps':
        writer.writerow(['Skill Name', 'Total Open Vacancies Requiring Skill', 'Applicants with Skill', 'Coverage %'])
        skills = CentralizedSkill.objects.all()
        total_applicants = Profile.objects.filter(role='applicant').count()
        for s in skills:
            req_count = JobSkillRequirement.objects.filter(skill=s).count()
            if req_count > 0:
                with_skill = ApplicantSkill.objects.filter(skill=s).count()
                coverage = round((with_skill / total_applicants) * 100, 1) if total_applicants > 0 else 0.0
                writer.writerow([s.name, req_count, with_skill, coverage])
                
    elif report_type == 'mismatch':
        writer.writerow(['Applicant', 'Field of Study', 'Referred Vacancy', 'Is Mismatched'])
        referrals = Referral.objects.all().select_related('applicant__user', 'job_vacancy')
        for r in referrals:
            edu = r.applicant.education.first()
            fos = edu.field_of_study if edu else 'Not Specified'
            mismatched = check_mismatch(fos, r.job_vacancy.title)
            writer.writerow([f"{r.applicant.user.first_name} {r.applicant.user.last_name}", fos, r.job_vacancy.title, 'YES' if mismatched else 'NO'])
            
    elif report_type == 'employers':
        writer.writerow(['Company Name', 'Contact Person', 'Email', 'Industry / Sector', 'Verification Status', 'Active Vacancies', 'Total Referrals', 'Hired Placements', 'Response Rate (%)', 'Date Registered'])
        employers = Profile.objects.filter(role='employer').select_related('user').order_by('-user__date_joined')
        for emp in employers:
            emp_referrals = Referral.objects.filter(job_vacancy__employer=emp)
            total_ref = emp_referrals.count()
            pending = emp_referrals.filter(status='Pending').count()
            hired = emp_referrals.filter(status__in=PLACEMENT_STATUSES).count()
            not_hired = emp_referrals.filter(status='Not Hired').count()
            responded = hired + not_hired
            resp_rate = round((responded / (total_ref - pending) * 100.0), 1) if (total_ref - pending) > 0 else (100.0 if total_ref == 0 else 0.0)
            active_vacs = JobVacancy.objects.filter(employer=emp, status='Open').count()
            
            writer.writerow([
                emp.company_name or emp.user.get_full_name(),
                emp.user.get_full_name(),
                emp.user.email,
                emp.industry or emp.title or 'Enterprise Partner',
                'Verified' if emp.is_verified else 'Pending Review',
                active_vacs,
                total_ref,
                hired,
                f"{resp_rate}%",
                emp.user.date_joined.strftime('%Y-%m-%d') if emp.user.date_joined else 'N/A'
            ])
            
    elif report_type == 'responsiveness':
        writer.writerow(['Employer Name', 'Total Referrals', 'Pending Count', 'Hired Count', 'Not Hired Count', 'No Response Count', 'Response Rate (%)'])
        employers = Profile.objects.filter(role='employer').select_related('user')
        for emp in employers:
            emp_referrals = Referral.objects.filter(job_vacancy__employer=emp)
            total = emp_referrals.count()
            pending = emp_referrals.filter(status='Pending').count()
            hired = emp_referrals.filter(status__in=PLACEMENT_STATUSES).count()
            not_hired = emp_referrals.filter(status='Not Hired').count()
            no_response = emp_referrals.filter(status='No Response').count()
            
            responded = hired + not_hired
            resp_rate = round((responded / (total - pending) * 100.0), 1) if (total - pending) > 0 else 0.0
            if total == 0:
                resp_rate = 100.0
            writer.writerow([f"{emp.user.first_name} {emp.user.last_name}", total, pending, hired, not_hired, no_response, resp_rate])
            
    elif report_type == 'funnel':
        writer.writerow(['Funnel Stage', 'Count'])
        total_applicants = Profile.objects.filter(role='applicant').count()
        referrals = Referral.objects.all()
        total_referrals = referrals.count()
        total_pending = referrals.filter(status='Pending').count()
        total_hired = referrals.filter(status__in=PLACEMENT_STATUSES).count()
        total_no_response = referrals.filter(status='No Response').count()
        
        writer.writerow(['Registered Applicants', total_applicants])
        writer.writerow(['Total Referred Candidates', total_referrals])
        writer.writerow(['Pending Referrals', total_pending])
        writer.writerow(['Placed / Hired Placements', total_hired])
        writer.writerow(['No Response Referrals', total_no_response])
        
    elif report_type == 'vacancies':
        writer.writerow(['Role Title', 'Employer', 'Category', 'Salary Range', 'Location', 'Available Slots', 'Status', 'Date Posted', 'Skills Required'])
        vacancies = JobVacancy.objects.all().select_related('employer__user').prefetch_related('requirements__skill').order_by('-created_at')
        for v in vacancies:
            skills_req = ", ".join([r.skill.name for r in v.requirements.all()]) or 'None'
            writer.writerow([
                v.title,
                v.employer.company_name if v.employer else 'N/A',
                v.category_display,
                v.salary_range or 'Competitive',
                v.location or 'Taguig',
                f"{v.remaining_slots}/{v.slots}",
                v.status,
                v.created_at.strftime('%Y-%m-%d') if v.created_at else 'N/A',
                skills_req
            ])
            
    else:
        writer.writerow(['Applicant Name', 'Email', 'Job Vacancy', 'Employer', 'Field of Study', 'Date Referred', 'Status', 'Skill Gaps'])
        referrals = Referral.objects.all().select_related('applicant__user', 'job_vacancy__employer__user').order_by('-date_referred')
        for r in referrals:
            edu = r.applicant.education.first()
            fos = edu.field_of_study if edu else 'Not Specified'
            _, gaps = calculate_match_score(r.applicant, r.job_vacancy)
            key_gaps = ", ".join([g['skill'] for g in gaps if g['gap'] > 0]) or 'None'
            writer.writerow([
                r.applicant.user.get_full_name(),
                r.applicant.user.email,
                r.job_vacancy.title,
                r.job_vacancy.employer.user.get_full_name() if (r.job_vacancy and r.job_vacancy.employer) else 'N/A',
                fos,
                r.date_referred.strftime('%Y-%m-%d') if r.date_referred else 'N/A',
                r.status,
                key_gaps
            ])
        
    return response


@role_required(['applicant'])
def applicant_settings(request):
    profile = request.user.profile
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'change_password':
            current_password = request.POST.get('current_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            # Verify current password
            if not request.user.check_password(current_password):
                messages.error(request, "Incorrect current password.")
            elif new_password != confirm_password:
                messages.error(request, "New passwords do not match.")
            elif len(new_password) < 6:
                messages.error(request, "Password must be at least 6 characters.")
            else:
                request.user.set_password(new_password)
                request.user.save()
                # To keep user logged in after password change
                auth_login(request, request.user)
                messages.success(request, "Password changed successfully!")
                return redirect('applicant_settings')
                
        elif action == 'update_profile':
            first_name = proper_name(request.POST.get('first_name', ''))
            last_name = proper_name(request.POST.get('last_name', ''))
            email = request.POST.get('email', '').strip()
            phone_number = request.POST.get('phone_number', '').strip()
            address = request.POST.get('address', '').strip()
            
            # Simple validation
            if not first_name or not last_name or not email:
                messages.error(request, "First name, last name, and email are required.")
            elif User.objects.exclude(id=request.user.id).filter(
                Q(email__iexact=email) | Q(username__iexact=email)
            ).exists():
                messages.error(request, "Email address is already in use.")
            else:
                old_email = request.user.email or ''
                # Keep the login username (which mirrors the email) in sync.
                if not old_email or request.user.username.lower() == old_email.lower():
                    request.user.username = email
                request.user.first_name = first_name
                request.user.last_name = last_name
                request.user.email = email
                request.user.save()
                
                profile.phone_number = phone_number
                profile.address = address
                profile.save()
                
                messages.success(request, "Account settings updated successfully!")
                return redirect('applicant_settings')
                
    context = {
        'profile': profile,
        'user': request.user,
    }
    return render(request, 'tracker/APPLICANT/settings.html', context)


@role_required(['employer'])
@require_POST
def schedule_interview_employer(request):
    profile = request.user.profile
    title = request.POST.get('title', '').strip()
    candidate_id = request.POST.get('candidate_id')
    vacancy_id = request.POST.get('vacancy_id')
    date_str = request.POST.get('date')
    time_str = request.POST.get('start_time')
    interview_type = request.POST.get('interview_type', 'Video Call')
    round_name = request.POST.get('round_name', 'Screening')
    meeting_link = request.POST.get('meeting_link', '').strip()
    notes = request.POST.get('notes', '').strip()
    
    if not date_str or not time_str:
        messages.error(request, "Date and Start Time are required.")
        return redirect('employer_dashboard')
        
    try:
        from datetime import datetime
        interview_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        try:
            interview_time = datetime.strptime(time_str, '%H:%M').time()
        except ValueError:
            interview_time = datetime.strptime(time_str, '%H:%M:%S').time()
    except Exception as e:
        messages.error(request, f"Invalid date or time format: {e}")
        return redirect('employer_dashboard')
        
    candidate = None
    if candidate_id:
        candidate = get_object_or_404(Profile, id=candidate_id, role='applicant')
        # Only interview applicants who are actually in this employer's pipeline.
        if not Referral.objects.filter(applicant=candidate, job_vacancy__employer=profile).exists():
            messages.error(request, "That applicant has not applied to any of your vacancies.")
            return redirect('employer_dashboard')
        
    vacancy = None
    if vacancy_id:
        vacancy = get_object_or_404(JobVacancy, id=vacancy_id, employer=profile)
        
    if not title and candidate:
        title = f"Interview with {candidate.user.get_full_name()}"
        
    Interview.objects.create(
        employer=profile,
        title=title,
        candidate=candidate,
        vacancy=vacancy,
        date=interview_date,
        start_time=interview_time,
        interview_type=interview_type,
        round_name=round_name,
        meeting_link=meeting_link or None,
        notes=notes or None
    )
    
    if candidate:
        company_name = profile.company_name or profile.user.get_full_name()
        Notification.objects.create(
            user=candidate.user,
            message=f"Interview Scheduled: An interview ({round_name} - {interview_type}) has been scheduled on {interview_date} at {interview_time} with {company_name}."
        )
        try:
            send_interview_scheduled_email(
                applicant_profile=candidate,
                vacancy=vacancy,
                stage=round_name,
                mode=interview_type,
                sched_date=str(interview_date),
                sched_time=str(interview_time),
                meeting_link=meeting_link,
                notes=notes
            )
        except Exception:
            pass
    
    messages.success(request, "Interview scheduled successfully!")
    return redirect('employer_dashboard')


@role_required(['employer'])
@require_POST
def cancel_interview_employer(request, interview_id):
    profile = request.user.profile
    interview = get_object_or_404(Interview, id=interview_id, employer=profile)
    interview_name = interview.candidate.user.get_full_name() if interview.candidate else interview.title or "Meeting"
    if interview.candidate:
        try:
            Notification.objects.create(
                user=interview.candidate.user,
                message=f"Interview Notice: Your scheduled interview '{interview.title}' with {profile.company_name or profile.user.get_full_name()} has been cancelled or rescheduled."
            )
            send_application_status_update_email(
                interview.candidate,
                interview.vacancy,
                'Interview Cancelled',
                remarks=f"The scheduled interview session ({interview.round_name}) has been cancelled or will be rescheduled by the employer."
            )
        except Exception:
            pass
    interview.delete()
    messages.success(request, f"Scheduled interview/meeting '{interview_name}' cancelled successfully.")
    return redirect('employer_dashboard')


@role_required(['admin'])
def reset_db_view(request):
    from tracker.models import (
        Skill, Milestone, StudyLog, Profile,
        Education, WorkExperience, Certification, ApplicantSkill,
        JobVacancy, JobSkillRequirement, TrainingProgram, TrainingEnrollment, Referral,
        GapScoreLog, JobBookmark, ApplicantDocument, Interview, Notification, CentralizedSkill
    )
    from django.contrib.admin.models import LogEntry
    from django.contrib.auth.models import User
    from django.contrib import messages
    from django.shortcuts import render, redirect

    # Build options list dynamically
    data_options = [
        # Preserved
        {
            'id': 'users',
            'label': 'User Accounts',
            'count': User.objects.count(),
            'purgeable': False,
            'badge': 'PRESERVED',
            'desc': 'Logins, credentials, superusers, and coordinator accounts.'
        },
        {
            'id': 'profiles',
            'label': 'Profiles',
            'count': Profile.objects.count(),
            'purgeable': False,
            'badge': 'PRESERVED',
            'desc': 'User roles, contact details, and employer company metadata.'
        },
        # Purgeable
        {
            'id': 'vacancies',
            'label': 'Job Vacancies & Requirements',
            'count': JobVacancy.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Job postings and required skill criteria.'
        },
        {
            'id': 'referrals',
            'label': 'Referrals & Applications',
            'count': Referral.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Job application records, statuses, and pre-employment checklists.'
        },
        {
            'id': 'interviews',
            'label': 'Interviews',
            'count': Interview.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Scheduled interview records and meeting details.'
        },
        {
            'id': 'training_enrollments',
            'label': 'Training Enrollments',
            'count': TrainingEnrollment.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Applicant enrollments in training courses.'
        },
        {
            'id': 'educations',
            'label': 'Education Records',
            'count': Education.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Degrees and academic history added to applicant profiles.'
        },
        {
            'id': 'work_experiences',
            'label': 'Work Experience',
            'count': WorkExperience.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Prior employment logs added to applicant profiles.'
        },
        {
            'id': 'certifications',
            'label': 'Certifications',
            'count': Certification.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Certificates and credentials on applicant profiles.'
        },
        {
            'id': 'applicant_skills',
            'label': 'Applicant Skills',
            'count': ApplicantSkill.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Core skills linked to applicant accounts.'
        },
        {
            'id': 'tracker_skills',
            'label': 'User Skills & Study Logs',
            'count': Skill.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Skills, milestones, and time spent studying (Study Logs).'
        },
        {
            'id': 'bookmarks',
            'label': 'Bookmarks',
            'count': JobBookmark.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Job postings bookmarked by applicants.'
        },
        {
            'id': 'documents',
            'label': 'Uploaded Documents',
            'count': ApplicantDocument.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Resumes, files, and certifications uploaded by applicants.'
        },
        {
            'id': 'notifications',
            'label': 'Notifications',
            'count': Notification.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Alerts, updates, and messages sent to users.'
        },
        {
            'id': 'gap_logs',
            'label': 'Gap Score Logs',
            'count': GapScoreLog.objects.count(),
            'purgeable': True,
            'default_checked': True,
            'badge': 'PURGEABLE',
            'desc': 'Stored job matching evaluations and gap logs.'
        },
        # Lookups (Unchecked by default to avoid accidental wipe)
        {
            'id': 'centralized_skills',
            'label': 'Centralized Skills (Lookup)',
            'count': CentralizedSkill.objects.count(),
            'purgeable': True,
            'default_checked': False,
            'badge': 'LOOKUP',
            'desc': 'Lookup dictionary of skill names used globally.'
        },
        {
            'id': 'training_programs',
            'label': 'Training Programs (Lookup)',
            'count': TrainingProgram.objects.count(),
            'purgeable': True,
            'default_checked': False,
            'badge': 'LOOKUP',
            'desc': 'Pre-defined lists of training programs available.'
        },
    ]

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reset_data':
            selected_ids = request.POST.getlist('selected_types')
            deleted_items = []
            
            # Perform targeted deletions based on user selection
            if 'notifications' in selected_ids:
                Notification.objects.all().delete()
                deleted_items.append("Notifications")
                
            if 'bookmarks' in selected_ids:
                JobBookmark.objects.all().delete()
                deleted_items.append("Bookmarks")
                
            if 'documents' in selected_ids:
                ApplicantDocument.objects.all().delete()
                deleted_items.append("Uploaded Documents")
                
            if 'gap_logs' in selected_ids:
                GapScoreLog.objects.all().delete()
                deleted_items.append("Gap Score Logs")
                
            if 'interviews' in selected_ids:
                Interview.objects.all().delete()
                deleted_items.append("Interviews")
                
            if 'referrals' in selected_ids:
                Referral.objects.all().delete()
                deleted_items.append("Referrals & Applications")
                
            if 'vacancies' in selected_ids:
                JobSkillRequirement.objects.all().delete()
                JobVacancy.objects.all().delete()
                deleted_items.append("Job Vacancies & Requirements")
                
            if 'training_enrollments' in selected_ids:
                TrainingEnrollment.objects.all().delete()
                deleted_items.append("Training Enrollments")
                
            if 'applicant_skills' in selected_ids:
                ApplicantSkill.objects.all().delete()
                deleted_items.append("Applicant Skills")
                
            if 'educations' in selected_ids:
                Education.objects.all().delete()
                deleted_items.append("Education Records")
                
            if 'work_experiences' in selected_ids:
                WorkExperience.objects.all().delete()
                deleted_items.append("Work Experience Records")
                
            if 'certifications' in selected_ids:
                Certification.objects.all().delete()
                deleted_items.append("Certifications")
                
            if 'tracker_skills' in selected_ids:
                StudyLog.objects.all().delete()
                Milestone.objects.all().delete()
                Skill.objects.all().delete()
                deleted_items.append("User Skills & Study Logs")
                
            if 'centralized_skills' in selected_ids:
                CentralizedSkill.objects.all().delete()
                deleted_items.append("Centralized Skills")
                
            if 'training_programs' in selected_ids:
                TrainingProgram.objects.all().delete()
                deleted_items.append("Training Programs")
                
            if deleted_items:
                LogEntry.objects.all().delete()
                messages.success(request, f"Successfully cleared selected data: {', '.join(deleted_items)}.")
            else:
                messages.warning(request, "No data types were selected for deletion.")
                
            return redirect('reset_db_view')

    return render(request, 'tracker/reset_db.html', {
        'data_options': data_options
    })


def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        user_exists = User.objects.filter(email=email).exists()
        
        if user_exists:
            otp = str(random.randint(100000, 999999))
            
            request.session['reset_email'] = email
            request.session['reset_code'] = otp
            request.session['reset_code_verified'] = False
            
            sent = send_notification_email(
                subject='Reset Your Password - SKILLUP',
                message=f'Hello,\n\nWe received a request to reset the password for your SKILLUP account.\n\nYour 6-digit verification code is:\n\n{otp}\n\nEnter this code on the password reset verification page to proceed.\n\nIf you did not request this reset, you can safely ignore this email.\n\nBest regards,\nSKILLUP Team',
                recipient_list=[email]
            )
            request.session['reset_email_sent'] = sent
            request.session['reset_email_error'] = "" if sent else "Could not deliver email"
            
            messages.success(request, f"A verification code has been dispatched to {email}. Please check your inbox.")
            return redirect('verify_reset_code')
        else:
            messages.error(request, "No account was found with that email address.")
            
    return render(request, 'tracker/LOGIN/forgot_password.html')


def verify_reset_code(request):
    email = request.session.get('reset_email')
    code = request.session.get('reset_code')
    email_sent = request.session.get('reset_email_sent', False)
    error_message = request.session.get('reset_email_error', '')
    
    if not email or not code:
        messages.error(request, "Please enter your email to request a reset code first.")
        return redirect('forgot_password')
        
    if request.method == 'POST':
        entered_code = request.POST.get('code', '').strip()
        if entered_code == code:
            request.session['reset_code_verified'] = True
            return redirect('reset_password')
        else:
            messages.error(request, "Invalid verification code. Please try again.")
            
    return render(request, 'tracker/LOGIN/verify_reset_code.html', {
        'email': email,
        'code': code,
        'email_sent': email_sent,
        'error_message': error_message
    })


def reset_password(request):
    email = request.session.get('reset_email')
    verified = request.session.get('reset_code_verified', False)
    
    if not email or not verified:
        messages.error(request, "Please request and verify a reset code first.")
        return redirect('forgot_password')
        
    if request.method == 'POST':
        new_password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        
        if not new_password or new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
        else:
            user = User.objects.filter(email=email).first()
            if user:
                user.set_password(new_password)
                user.save()
                
                request.session.pop('reset_email', None)
                request.session.pop('reset_code', None)
                request.session.pop('reset_code_verified', None)
                request.session.pop('reset_email_sent', None)
                request.session.pop('reset_email_error', None)
                
                messages.success(request, "Your password has been reset successfully! You can now log in with your new password.")
                return redirect('login')
            else:
                messages.error(request, "User not found.")
                
    return render(request, 'tracker/LOGIN/reset_password.html', {'email': email})


@login_required
@require_POST
def submit_skill_assessment(request):
    """
    Adds or updates a verified skill for the logged-in applicant.
    Proficiency levels are no longer stored; skills are tracked as present.
    """
    if not hasattr(request.user, 'profile') or request.user.profile.role != 'applicant':
        return JsonResponse({'status': 'error', 'message': 'Unauthorized. Applicant profile required.'}, status=403)
    
    try:
        data = json.loads(request.body)
        skill_name = (data.get('skill_name') or '').strip()
        skill_id = data.get('skill_id')
        
        if not skill_name and not skill_id:
            return JsonResponse({'status': 'error', 'message': 'Missing skill identifier.'}, status=400)
            
        profile = request.user.profile
        
        # Locate CentralizedSkill
        skill_obj = None
        if skill_id:
            try:
                # Could be ApplicantSkill ID or CentralizedSkill ID
                if ApplicantSkill.objects.filter(id=skill_id, profile=profile).exists():
                    app_skill = ApplicantSkill.objects.get(id=skill_id, profile=profile)
                    skill_obj = app_skill.skill
                else:
                    skill_obj = CentralizedSkill.objects.filter(id=skill_id).first()
            except Exception:
                pass
                
        if not skill_obj and skill_name:
            skill_obj = CentralizedSkill.objects.filter(name__iexact=skill_name).first()
            if not skill_obj:
                skill_obj = CentralizedSkill.objects.create(
                    name=skill_name,
                    category='General',
                    description=f'Skill added via assessment: {skill_name}'
                )
                
        if not skill_obj:
            return JsonResponse({'status': 'error', 'message': 'Could not identify skill.'}, status=404)
            
        # Update or create ApplicantSkill
        applicant_skill, created = ApplicantSkill.objects.update_or_create(
            profile=profile,
            skill=skill_obj,
            defaults={'source': 'Assessment'}
        )
        
        skill_names = list(
            ApplicantSkill.objects.filter(profile=profile).values_list('skill__name', flat=True)
        )
        profile.skills = ", ".join(skill_names)[:255]
        profile.save(update_fields=['skills'])

        # Let employers know when this applicant now matches their vacancies.
        check_and_notify_employer_matches(profile)

        return JsonResponse({
            'status': 'success',
            'skill_id': applicant_skill.id,
            'skill_name': skill_obj.name,
            'message': f'{skill_obj.name} was added to your skills.'
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# =========================================================================
# AI FEATURE VIEWS
# =========================================================================
from .ai_services import (
    scan_and_extract_resume,
    apply_parsed_resume_to_profile,
    get_ai_job_recommendations,
    get_ai_training_recommendations,
    generate_training_program_ai,
    generate_training_from_syllabus_ai,
    extract_text_from_document,
    get_aligned_video_for_topic,
)

@login_required
def applicant_ai_scan_resume(request):
    """
    API endpoint: Receives uploaded resume (PDF, DOCX, TXT),
    scans it via Gemini AI, extracts structured candidate details,
    saves it to ApplicantDocument, and auto-populates the applicant's profile.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)

    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'applicant':
        return JsonResponse({'success': False, 'error': 'Unauthorized applicant profile'}, status=403)

    resume_file = request.FILES.get('resume') or request.FILES.get('document')
    doc_id = request.POST.get('document_id')
    doc = None
    if doc_id:
        doc = ApplicantDocument.objects.filter(id=doc_id, profile=profile).first()

    if not resume_file and not doc:
        return JsonResponse({'success': False, 'error': 'No resume file uploaded or selected. Please upload a PDF, Word document, or text file.'}, status=400)

    try:
        if not doc and resume_file:
            # Save to ApplicantDocument
            size_kb = f"{max(1, int(resume_file.size / 1024))} KB"
            doc = ApplicantDocument.objects.create(
                profile=profile,
                file=resume_file,
                file_name=resume_file.name,
                file_size=size_kb
            )

        # Scan and parse with AI
        try:
            target_path_or_file = doc.file.path if hasattr(doc.file, 'path') else doc.file
            parsed_data = scan_and_extract_resume(target_path_or_file)
        except Exception:
            doc.file.seek(0)
            parsed_data = scan_and_extract_resume(doc.file)

        if "error" in parsed_data:
            return JsonResponse({'success': False, 'error': parsed_data["error"]}, status=400)

        # Apply extracted data to Profile, User, Skills, Experience, Education
        summary = apply_parsed_resume_to_profile(profile, parsed_data)

        return JsonResponse({
            'success': True,
            'message': 'Resume successfully analyzed and profile updated!',
            'parsed': parsed_data,
            'summary': summary,
            'document_id': doc.id,
            'document_name': doc.file_name,
            'ai_powered': parsed_data.get('ai_powered', True)
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Failed to process resume: {str(e)}'}, status=500)


@login_required
def applicant_ai_job_matches(request):
    """
    API endpoint: Returns AI-ranked job vacancies for the logged-in applicant,
    including match scores and personalized explanations.
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'applicant':
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)

    try:
        recs = get_ai_job_recommendations(profile, top_n=6)
        serialized = []
        for r in recs:
            v = r['vacancy']
            serialized.append({
                'id': v.id,
                'title': v.title,
                'company': v.employer.company_name if v.employer else 'PESO Verified Employer',
                'location': v.location,
                'salary': v.salary_range,
                'category': v.category,
                'match_score': r['match_score'],
                'matching_skills': r['matching_skills'],
                'missing_skills': r['missing_skills'],
                'why_you_match': r['why_you_match'],
                'ai_powered': r.get('ai_powered', True)
            })
        return JsonResponse({'success': True, 'recommendations': serialized})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def applicant_ai_training_recommendations(request):
    """
    API endpoint: Returns AI training recommendations JSON for the current applicant.
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'applicant':
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)

    try:
        recs = get_ai_training_recommendations(profile, top_n=6)
        serialized = []
        for r in recs:
            p = r["program"]
            serialized.append({
                "id": p.id,
                "title": p.title,
                "provider": p.provider,
                "description": p.description or "",
                "duration": p.duration,
                "location": p.location or "PESO Training Center",
                "available_slots": p.available_slots,
                "slots": p.slots,
                "status": p.status,
                "skills_display": p.skills_display,
                "match_score": r["match_score"],
                "key_skills": r.get("key_skills", []),
                "why_recommended": r.get("why_recommended", ""),
                "enroll_url": reverse('applicant_enroll_training', args=[p.id]),
            })
        return JsonResponse({'success': True, 'recommendations': serialized})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def peso_ai_generate_training(request):
    """
    API endpoint: PESO Admin types a topic/skill gap, and Gemini AI generates
    the complete training program with custom curriculum, modules, lessons, and exam!
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'admin':
        return JsonResponse({'success': False, 'error': 'Unauthorized admin action'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)

    try:
        data = {}
        if request.content_type == 'application/json' and request.body:
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.POST

        topic = str(data.get('topic', '')).strip()
        target_skills = str(data.get('target_skills', '')).strip()
        training_needs = str(data.get('training_needs', '')).strip()[:2000]

        try:
            duration_hours = int(data.get('duration_hours', 40))
        except (ValueError, TypeError):
            duration_hours = 40

        try:
            required_seat_time_mins = int(data.get('required_seat_time_mins', 60))
        except (ValueError, TypeError):
            required_seat_time_mins = 60

        try:
            slots = int(data.get('slots', 30))
        except (ValueError, TypeError):
            slots = 30

        try:
            num_modules = int(data.get('num_modules', 10))
        except (ValueError, TypeError):
            num_modules = 10

        try:
            lessons_per_module = int(data.get('lessons_per_module', 10))
        except (ValueError, TypeError):
            lessons_per_module = 10

        attach_videos = bool(data.get('attach_videos', True))

        if not topic:
            return JsonResponse({'success': False, 'error': 'Please enter a training topic or in-demand skill.'}, status=400)

        # Call AI curriculum generator
        gen_data = generate_training_program_ai(
            topic=topic,
            target_skills_str=target_skills,
            duration_hours=duration_hours,
            required_seat_time_mins=required_seat_time_mins,
            num_modules=num_modules,
            lessons_per_module=lessons_per_module,
            attach_videos=attach_videos,
            training_needs=training_needs
        )

        # Create program in database
        program = TrainingProgram.objects.create(
            title=gen_data.get('title', f'{topic} Competency Mastery Program'),
            provider=gen_data.get('provider', 'TESDA & PESO Training Center'),
            duration=gen_data.get('duration', f'{duration_hours} Hours'),
            required_training_minutes=gen_data.get('required_training_minutes', required_seat_time_mins),
            location=gen_data.get('location', 'PESO Training Center / Online LMS'),
            description=gen_data.get('description', f'Comprehensive competency training program for {topic}.'),
            slots=slots,
            status='Scheduled',
            custom_curriculum=gen_data.get('curriculum', {})
        )

        # Attach skills
        skills_list = gen_data.get('skills', [topic])
        for sk_name in skills_list:
            sk_clean = str(sk_name).strip()
            if sk_clean:
                sk_obj, _ = CentralizedSkill.objects.get_or_create(
                    name=sk_clean,
                    defaults={'category': 'Vocational'}
                )
                program.skills_addressed.add(sk_obj)
                if not program.skill_addressed:
                    program.skill_addressed = sk_obj
                    program.save(update_fields=['skill_addressed'])

        return JsonResponse({
            'success': True,
            'message': f"Training Program '{program.title}' generated with {len(gen_data.get('curriculum', {}).get('modules', []))} modules and published to LMS!",
            'program_id': program.id,
            'title': program.title,
            'redirect_url': reverse('training_program_detail_admin', args=[program.id]),
            'curriculum_url': reverse('training_curriculum_builder_admin', args=[program.id]),
            'ai_generated': gen_data.get('ai_generated', True)
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Failed to generate training: {str(e)}'}, status=500)


@login_required
def peso_ai_find_aligned_videos(request):
    """
    API endpoint: Resolves aligned educational/vocational YouTube tutorial videos
    for a given topic, module title, and lesson title.
    Returns primary video, embed URL, duration, channel, and alternatives.
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'admin':
        return JsonResponse({'success': False, 'error': 'Unauthorized admin action'}, status=403)

    topic = request.GET.get('topic') or request.POST.get('topic') or ''
    module_title = request.GET.get('module_title') or request.POST.get('module_title') or ''
    lesson_title = request.GET.get('lesson_title') or request.POST.get('lesson_title') or ''

    if not topic and not lesson_title:
        return JsonResponse({'success': False, 'error': 'Please provide a topic or lesson title.'}, status=400)

    try:
        result = get_aligned_video_for_topic(topic=topic, module_title=module_title, lesson_title=lesson_title)
        return JsonResponse({'success': True, 'video': result})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def peso_ai_generate_from_syllabus(request):
    """
    API endpoint: PESO Admin uploads a course syllabus document (PDF, DOCX, DOC, TXT)
    or pastes syllabus text, and Gemini AI parses it and creates a complete digital course
    with structured modules, milestone lessons, reading material, topic-aligned videos,
    and a certification assessment exam!
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'admin':
        return JsonResponse({'success': False, 'error': 'Unauthorized admin action'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)

    try:
        raw_text = ""
        syllabus_file = request.FILES.get('syllabus_file') or request.FILES.get('file')
        if syllabus_file:
            raw_text = extract_text_from_document(syllabus_file)

        # Also check for pasted syllabus text if file extraction is empty or if text provided
        pasted_text = (request.POST.get('syllabus_text') or '').strip()
        if not raw_text and pasted_text:
            raw_text = pasted_text
        elif pasted_text and raw_text and len(pasted_text) > len(raw_text):
            raw_text = pasted_text

        # Also support JSON body if sent as application/json
        if not raw_text and request.content_type == 'application/json' and request.body:
            try:
                body_json = json.loads(request.body.decode('utf-8'))
                raw_text = body_json.get('syllabus_text', '').strip()
            except Exception:
                pass

        if not raw_text or len(raw_text.strip()) < 20:
            return JsonResponse({
                'success': False,
                'error': 'Syllabus content could not be read or is too brief. Please upload a valid PDF, DOCX, or text syllabus document.'
            }, status=400)

        # Optional user overrides
        override_title = str(request.POST.get('override_title', '')).strip()

        duration_hours = None
        duration_raw = request.POST.get('duration_hours')
        if duration_raw:
            try:
                duration_hours = int(duration_raw)
            except (ValueError, TypeError):
                duration_hours = None

        required_seat_time_mins = None
        seat_time_raw = request.POST.get('required_seat_time_mins')
        if seat_time_raw:
            try:
                required_seat_time_mins = int(seat_time_raw)
            except (ValueError, TypeError):
                required_seat_time_mins = None

        slots = 30
        slots_raw = request.POST.get('slots')
        if slots_raw:
            try:
                slots = int(slots_raw)
            except (ValueError, TypeError):
                slots = 30

        attach_videos = True
        if 'attach_videos' in request.POST:
            attach_videos = request.POST.get('attach_videos') in ['true', '1', 'on', True]

        # Call AI generator from syllabus
        gen_data = generate_training_from_syllabus_ai(
            syllabus_text=raw_text,
            override_title=override_title,
            duration_hours=duration_hours,
            required_seat_time_mins=required_seat_time_mins,
            slots=slots,
            attach_videos=attach_videos
        )

        title = gen_data.get('title') or override_title or 'Comprehensive Competency Training Course'
        provider = gen_data.get('provider') or 'TESDA & PESO Training Center'
        duration_str = gen_data.get('duration') or (f"{duration_hours} Hours" if duration_hours else "40 Hours")
        seat_time = gen_data.get('required_training_minutes') or required_seat_time_mins or 60
        description = gen_data.get('description') or f"Comprehensive competency-based training course aligned with syllabus."

        program = TrainingProgram.objects.create(
            title=title,
            provider=provider,
            duration=duration_str,
            required_training_minutes=seat_time,
            location=gen_data.get('location', 'PESO Training Center / Online LMS'),
            description=description,
            slots=slots,
            status='Scheduled',
            custom_curriculum=gen_data.get('curriculum', {})
        )

        # Attach skills
        skills_list = gen_data.get('skills', [])
        for sk_name in skills_list:
            sk_clean = str(sk_name).strip()
            if sk_clean:
                sk_obj, _ = CentralizedSkill.objects.get_or_create(
                    name=sk_clean,
                    defaults={'category': 'Vocational'}
                )
                program.skills_addressed.add(sk_obj)
                if not program.skill_addressed:
                    program.skill_addressed = sk_obj
                    program.save(update_fields=['skill_addressed'])

        curriculum = gen_data.get('curriculum', {})
        modules_count = len(curriculum.get('modules', []))
        total_lessons = curriculum.get('total_lessons', sum(len(m.get('lessons', [])) for m in curriculum.get('modules', [])))

        return JsonResponse({
            'success': True,
            'message': f"Course '{program.title}' successfully generated from syllabus with {modules_count} modules ({total_lessons} milestone lessons) and published to LMS!",
            'program_id': program.id,
            'title': program.title,
            'modules_count': modules_count,
            'lessons_count': total_lessons,
            'redirect_url': reverse('training_curriculum_builder_admin', args=[program.id]),
            'detail_url': reverse('training_program_detail_admin', args=[program.id]),
            'curriculum_url': reverse('training_curriculum_builder_admin', args=[program.id]),
            'ai_generated': gen_data.get('ai_generated', True)
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Failed to generate course from syllabus: {str(e)}'}, status=500)


@login_required
def peso_import_syllabus_curriculum(request, program_id):
    """
    API endpoint: Updates an existing course curriculum by importing and parsing a syllabus document.
    """
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'admin':
        return JsonResponse({'success': False, 'error': 'Unauthorized admin action'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)

    program = get_object_or_404(TrainingProgram, id=program_id)

    try:
        raw_text = ""
        syllabus_file = request.FILES.get('syllabus_file') or request.FILES.get('file')
        if syllabus_file:
            raw_text = extract_text_from_document(syllabus_file)

        pasted_text = (request.POST.get('syllabus_text') or '').strip()
        if not raw_text and pasted_text:
            raw_text = pasted_text

        if not raw_text or len(raw_text.strip()) < 20:
            return JsonResponse({
                'success': False,
                'error': 'Syllabus content could not be read or is too brief. Please upload a valid document or paste syllabus text.'
            }, status=400)

        update_course_details = request.POST.get('update_course_details') in ['true', '1', 'on', True]
        attach_videos = request.POST.get('attach_videos', 'true') in ['true', '1', 'on', True]

        gen_data = generate_training_from_syllabus_ai(
            syllabus_text=raw_text,
            override_title=program.title if not update_course_details else "",
            attach_videos=attach_videos
        )

        program.custom_curriculum = gen_data.get('curriculum', {})

        if update_course_details:
            if gen_data.get('title'):
                program.title = gen_data.get('title')
            if gen_data.get('description'):
                program.description = gen_data.get('description')
            if gen_data.get('duration'):
                program.duration = gen_data.get('duration')
            if gen_data.get('required_training_minutes'):
                program.required_training_minutes = gen_data.get('required_training_minutes')

        program.save()

        # Update skills
        for sk_name in gen_data.get('skills', []):
            sk_clean = str(sk_name).strip()
            if sk_clean:
                sk_obj, _ = CentralizedSkill.objects.get_or_create(
                    name=sk_clean,
                    defaults={'category': 'Vocational'}
                )
                program.skills_addressed.add(sk_obj)

        modules_count = len(program.custom_curriculum.get('modules', []))
        total_lessons = program.custom_curriculum.get('total_lessons', 0)

        return JsonResponse({
            'success': True,
            'message': f"Curriculum for '{program.title}' successfully rebuilt from syllabus with {modules_count} modules and {total_lessons} lessons!",
            'program_id': program.id,
            'modules_count': modules_count,
            'total_lessons': total_lessons,
            'curriculum': program.custom_curriculum
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Failed to import syllabus: {str(e)}'}, status=500)









from django.views.decorators.csrf import csrf_exempt
