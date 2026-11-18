from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.contrib.auth.models import User

from .id_storage import EncryptedIDStorage


class Skill(models.Model):
    STATUS_CHOICES = [
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=100, default='General')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_started')
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def total_study_hours(self):
        total_minutes = self.study_logs.aggregate(models.Sum('duration_minutes'))['duration_minutes__sum'] or 0
        return round(total_minutes / 60, 1)

    @property
    def completion_percentage(self):
        milestones_count = self.milestones.count()
        if milestones_count == 0:
            return 0
        completed_count = self.milestones.filter(is_completed=True).count()
        return int((completed_count / milestones_count) * 100)

class Milestone(models.Model):
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    is_completed = models.BooleanField(default=False)
    target_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.skill.name})"

class StudyLog(models.Model):
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='study_logs')
    date = models.DateField(default=timezone.now)
    duration_minutes = models.PositiveIntegerField(help_text="Duration in minutes")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.duration_minutes}m on {self.skill.name} ({self.date})"


class Profile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('applicant', 'Applicant'),
        ('employer', 'Employer'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='applicant')
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=6, blank=True, null=True)
    
    # Candidate details for Talent Bench
    title = models.CharField(max_length=100, default='Software Engineer')
    status = models.CharField(max_length=50, default='Applied')
    skills = models.CharField(max_length=255, default='Python, SQL')
    location = models.CharField(max_length=100, default='Remote')
    training_progress_title = models.CharField(max_length=100, default='General Training')
    training_progress_percentage = models.PositiveIntegerField(default=0)
    experience_years = models.CharField(max_length=50, default='2 Years')
    is_profile_complete = models.BooleanField(default=False)
    birthdate = models.DateField(blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    civil_status = models.CharField(max_length=50, blank=True, null=True)
    soft_notes = models.TextField(blank=True, null=True)
    is_fresh_grad = models.BooleanField(default=False)
    account_status = models.CharField(max_length=20, default='Active')
    preferred_job = models.CharField(max_length=50, blank=True, null=True)
    profile_picture = models.FileField(upload_to='profile_pictures/', blank=True, null=True)

    # Professional & Portfolio Links (LinkedIn, GitHub, Personal Website)
    linkedin_url = models.URLField(max_length=300, blank=True, null=True)
    portfolio_url = models.URLField(max_length=300, blank=True, null=True)
    github_url = models.URLField(max_length=300, blank=True, null=True)
    other_links = models.CharField(max_length=500, blank=True, null=True)

    # Identification Document (Valid ID for personal data extraction & verification)
    id_document = models.FileField(upload_to='id_documents/', storage=EncryptedIDStorage(), blank=True, null=True)
    id_type = models.CharField(max_length=100, blank=True, null=True)
    id_number = models.CharField(max_length=100, blank=True, null=True)
    id_verified = models.BooleanField(default=False)


    @property
    def age(self):
        if self.birthdate:
            today = timezone.localdate()
            return today.year - self.birthdate.year - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        return None

    @property
    def average_company_rating(self):
        reviews = self.company_reviews.all()
        if not reviews.exists():
            return 0.0  # No fabricated baseline rating.
        return round(sum(r.rating for r in reviews) / reviews.count(), 1)

    @property
    def total_company_reviews_count(self):
        return self.company_reviews.count()

    @property
    def rating_full_stars(self):
        return range(int(self.average_company_rating))

    @property
    def rating_empty_stars(self):
        return range(max(0, 5 - int(self.average_company_rating)))


    # Employer company details
    company_name = models.CharField(max_length=200, blank=True, null=True)
    industry = models.CharField(max_length=100, blank=True, null=True)
    company_size = models.CharField(max_length=50, blank=True, null=True)
    website = models.CharField(max_length=200, blank=True, null=True)
    company_logo = models.FileField(upload_to='company_logos/', blank=True, null=True)
    contact_name = models.CharField(max_length=200, blank=True, null=True)
    contact_position = models.CharField(max_length=100, blank=True, null=True)
    contact_email = models.CharField(max_length=100, blank=True, null=True)
    employment_type_offered = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.role}"


class CentralizedSkill(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100, default='General')
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class Education(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='education')
    institution = models.CharField(max_length=200)
    degree = models.CharField(max_length=200)
    field_of_study = models.CharField(max_length=200)
    start_year = models.PositiveIntegerField()
    end_year = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.degree} at {self.institution}"

class WorkExperience(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='experience')
    company = models.CharField(max_length=200)
    position = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    start_date = models.CharField(max_length=50)
    end_date = models.CharField(max_length=50, default='Present')
    is_current = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.position} at {self.company}"

class Certification(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='certifications')
    name = models.CharField(max_length=200)
    issuing_organization = models.CharField(max_length=200)
    issue_date = models.DateField(blank=True, null=True)
    expiration_date = models.DateField(blank=True, null=True)
    is_tesda = models.BooleanField(default=False)
    certificate_file = models.FileField(upload_to='certificates/', blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.issuing_organization})"

class ApplicantSkill(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='applicant_skills')
    skill = models.ForeignKey(CentralizedSkill, on_delete=models.CASCADE)
    source = models.CharField(max_length=20, default='self')
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('profile', 'skill')

    def __str__(self):
        return f"{self.profile.user.username} - {self.skill.name}"

class JobVacancy(models.Model):
    title = models.CharField(max_length=200)
    employer = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='vacancies')
    location = models.CharField(max_length=100, default='Remote')
    description = models.TextField(blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)
    salary_range = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='Open')
    category = models.CharField(max_length=50, default='jobs')
    min_education = models.CharField(max_length=100, blank=True, null=True)
    required_certifications = models.CharField(max_length=255, blank=True, null=True)
    required_experience_years = models.PositiveIntegerField(default=0)
    slots = models.PositiveIntegerField(default=3)
    remaining_slots = models.PositiveIntegerField(default=3)

    @property
    def category_display(self):
        category_map = {
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
        return category_map.get(self.category, self.category)

    def auto_withdraw_applications(self, reason='Position closed by employer.'):
        """Automatically withdraw active applications when a vacancy is closed or archived."""
        active_statuses = ['Pending', 'Interviewing', 'Accepted — Awaiting Onboarding']
        active_referrals = self.referrals.filter(status__in=active_statuses).select_related('applicant__user', 'applicant')
        
        for ref in active_referrals:
            app_profile = ref.applicant
            ref.status = 'Withdrawn — Position Closed'
            ref.rejection_reason = reason
            ref.save()
            
            # Send in-app Notification to applicant
            try:
                company = self.employer.company_name or (self.employer.user.get_full_name() if self.employer.user else 'the employer')
                msg = f"The job posting for '{self.title}' at {company} has been closed. Your application has been automatically withdrawn from active consideration."
                from django.utils import timezone
                import datetime
                recent_time = timezone.now() - datetime.timedelta(seconds=5)
                already_exists = Notification.objects.filter(
                    user=app_profile.user,
                    message=msg,
                    created_at__gte=recent_time
                ).exists()
                if not already_exists:
                    Notification.objects.create(
                        user=app_profile.user,
                        message=msg,
                        notif_type='general'
                    )
            except Exception:
                pass
            
            # Reset applicant status to Active — Job Seeking if they have no other active applications
            try:
                has_other_active = Referral.objects.filter(
                    applicant=app_profile,
                    job_vacancy__status='Open',
                    status__in=active_statuses
                ).exclude(id=ref.id).exists()
                
                if not has_other_active and app_profile.status in ['Applied', 'Shortlisted', 'Interviewing']:
                    app_profile.status = 'Active — Job Seeking'
                    app_profile.save()
            except Exception:
                pass

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        # Callers that close a vacancy as a side effect (e.g. the last slot was
        # just filled) can set this transient flag to keep already-active
        # applications alive. Manually closing a posting does not set it.
        skip_auto_withdraw = getattr(self, '_skip_auto_withdraw', False)
        if not is_new:
            try:
                old_status = JobVacancy.objects.filter(pk=self.pk).values_list('status', flat=True).first()
            except Exception:
                pass

        super().save(*args, **kwargs)

        # If status transitioned to Closed or Archived, auto-withdraw active applications
        if not skip_auto_withdraw and self.status in ['Closed', 'Archived'] and (is_new or old_status != self.status):
            self.auto_withdraw_applications()

    def __str__(self):
        return self.title

class JobSkillRequirement(models.Model):
    job_vacancy = models.ForeignKey(JobVacancy, on_delete=models.CASCADE, related_name='requirements')
    skill = models.ForeignKey(CentralizedSkill, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('job_vacancy', 'skill')

    def __str__(self):
        return f"{self.job_vacancy.title} - {self.skill.name}"

class TrainingProgram(models.Model):
    title = models.CharField(max_length=200)
    provider = models.CharField(max_length=200, default='TESDA')
    skill_addressed = models.ForeignKey(CentralizedSkill, on_delete=models.SET_NULL, null=True, blank=True)
    skills_addressed = models.ManyToManyField(CentralizedSkill, blank=True, related_name='training_programs')
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=255, default='PESO Training Center', blank=True, null=True)
    duration = models.CharField(max_length=100, default='40 Hours')
    required_training_minutes = models.PositiveIntegerField(default=60, help_text="Minimum required training seat time in minutes before certificate is awarded")
    scheduled_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, default='Scheduled') # Scheduled, In Progress, Completed
    slots = models.PositiveIntegerField(default=30)
    remaining_slots = models.PositiveIntegerField(default=30)
    custom_curriculum = models.JSONField(default=dict, blank=True, null=True)

    @property
    def available_slots(self):
        if 'enrollments' in getattr(self, '_prefetched_objects_cache', {}):
            enrolled_count = len(self._prefetched_objects_cache['enrollments'])
        else:
            enrolled_count = self.enrollments.count()
        return max(0, self.slots - enrolled_count)

    @property
    def all_skills(self):
        m2m = list(self.skills_addressed.all())
        if m2m:
            return m2m
        if self.skill_addressed:
            return [self.skill_addressed]
        return []

    @property
    def skills_display(self):
        skills = self.all_skills
        if skills:
            return ", ".join(s.name for s in skills)
        return "None"

    @property
    def formatted_required_duration(self):
        mins = self.required_training_minutes or 60
        if mins >= 60:
            hrs = mins // 60
            rem_m = mins % 60
            if rem_m > 0:
                return f"{hrs}h {rem_m}m ({mins} mins)"
            return f"{hrs} Hour{'s' if hrs > 1 else ''} ({mins} mins)"
        return f"{mins} mins"

    def __str__(self):
        return self.title

class TrainingEnrollment(models.Model):
    STATUS_CHOICES = [
        ('Enrolled', 'Enrolled'),
        ('Attended', 'Attended & Completed'),
        ('No Show', 'No Show'),
    ]
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='training_enrollments')
    training_program = models.ForeignKey(TrainingProgram, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Enrolled')
    progress_percentage = models.PositiveIntegerField(default=0)
    completed_lessons = models.JSONField(default=list, blank=True)
    time_spent_seconds = models.PositiveIntegerField(default=0, help_text="Total active learning time spent in seconds")
    assessment_score = models.PositiveIntegerField(default=0)
    assessment_results = models.JSONField(default=dict, blank=True)
    certificate_code = models.CharField(max_length=100, blank=True, null=True)
    certificate_issued_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ('profile', 'training_program')

    @property
    def required_training_minutes(self):
        return self.training_program.required_training_minutes or 60

    @property
    def required_training_seconds(self):
        return self.required_training_minutes * 60

    @property
    def time_spent_minutes(self):
        return round(self.time_spent_seconds / 60.0, 1)

    @property
    def time_remaining_seconds(self):
        return max(0, self.required_training_seconds - self.time_spent_seconds)

    @property
    def time_remaining_minutes(self):
        return max(0, round(self.time_remaining_seconds / 60.0, 1))

    @property
    def is_time_requirement_met(self):
        if self.certificate_code or self.status == 'Attended':
            return True
        return self.time_spent_seconds >= self.required_training_seconds

    @property
    def time_progress_percentage(self):
        if self.certificate_code or self.status == 'Attended':
            return 100
        req = self.required_training_seconds
        if req <= 0:
            return 100
        return min(100, int((self.time_spent_seconds / req) * 100))

    @property
    def formatted_time_spent(self):
        mins = self.time_spent_seconds // 60
        secs = self.time_spent_seconds % 60
        if mins >= 60:
            hrs = mins // 60
            mins = mins % 60
            return f"{hrs}h {mins}m {secs}s"
        return f"{mins}m {secs}s"

    @property
    def formatted_required_time(self):
        mins = self.required_training_minutes
        if mins >= 60:
            hrs = mins // 60
            rem_m = mins % 60
            if rem_m > 0:
                return f"{hrs}h {rem_m}m ({mins} mins)"
            return f"{hrs} Hour{'s' if hrs > 1 else ''} ({mins} mins)"
        return f"{mins} mins"

    @property
    def formatted_time_remaining(self):
        rem_sec = self.time_remaining_seconds
        if rem_sec <= 0:
            return "0m 0s"
        mins = rem_sec // 60
        secs = rem_sec % 60
        if mins >= 60:
            hrs = mins // 60
            mins = mins % 60
            return f"{hrs}h {mins}m {secs}s"
        return f"{mins}m {secs}s"

    @property
    def can_claim_certificate(self):
        if self.certificate_code or self.status == 'Attended':
            return True
        assessment_passed = bool((self.assessment_results or {}).get('passed'))
        return self.progress_percentage >= 100 and self.is_time_requirement_met and assessment_passed

    def __str__(self):
        return f"{self.profile.user.username} enrolled in {self.training_program.title} ({self.status})"

class Referral(models.Model):
    ACTIVE_EMPLOYMENT_STATUSES = [
        'Confirmed — Onboarding',
        'Hired — Probationary',
        'Hired — Regular',
        'Regularly Employed',
        'Still Employed — Performing Well',
        'Still Employed — On Improvement Plan',
        'Probation Extended',
    ]
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Interviewing', 'Interviewing'),
        ('Accepted — Awaiting Onboarding', 'Accepted — Awaiting Onboarding'),
        ('Confirmed — Onboarding', 'Confirmed — Onboarding'),
        ('Declined', 'Declined'),
        ('Hired — Probationary', 'Hired — Probationary'),
        ('Hired — Regular', 'Hired — Regular'),
        ('No Show', 'No Show'),
        ('Closed — No Show', 'Closed — No Show'),
        ('Still Employed — Performing Well', 'Still Employed — Performing Well'),
        ('Still Employed — On Improvement Plan', 'Still Employed — On Improvement Plan'),
        ('Resigned Voluntarily', 'Resigned Voluntarily'),
        ('Terminated by Employer', 'Terminated by Employer'),
        ('No Response from Employer', 'No Response from Employer'),
        ('No Response from Applicant', 'No Response from Applicant'),
        ('Regularly Employed', 'Regularly Employed'),
        ('Probation Extended', 'Probation Extended'),
        ('Separated — End of Probation', 'Separated — End of Probation'),
        ('Not Hired', 'Not Hired'),
        ('No Response', 'No Response'),
        ('Withdrawn — Position Closed', 'Withdrawn — Position Closed'),
        ('Withdrawn', 'Withdrawn'),
    ]
    applicant = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='referrals')
    job_vacancy = models.ForeignKey(JobVacancy, on_delete=models.CASCADE, related_name='referrals')
    date_referred = models.DateField(default=timezone.now)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Pending')
    updated_at = models.DateTimeField(auto_now=True)
    contact_attempts = models.JSONField(default=list)
    rejection_reason = models.CharField(max_length=255, blank=True, null=True)

    # Multi-Stage Interview Process Tracker
    INTERVIEW_STAGE_CHOICES = [
        ('Initial Assessment', 'Initial Assessment'),
        ('Online or Face-to-Face (F2F) Assessment', 'Online or Face-to-Face (F2F) Assessment'),
        ('Hiring Manager / Final Interview', 'Hiring Manager / Final Interview'),
        ('Interview Completed', 'Interview Completed'),
    ]
    INTERVIEW_MODE_CHOICES = [
        ('Online Video Assessment', 'Online Video Assessment (Google Meet / Zoom)'),
        ('Face-to-Face (F2F)', 'Face-to-Face (F2F) On-Site Assessment'),
        ('Phone Call', 'Phone Screening / Call'),
    ]
    interview_stage = models.CharField(max_length=100, choices=INTERVIEW_STAGE_CHOICES, default='Initial Assessment', blank=True, null=True)
    interview_mode = models.CharField(max_length=100, choices=INTERVIEW_MODE_CHOICES, default='Online Video Assessment', blank=True, null=True)
    interview_stage_notes = models.TextField(blank=True, null=True)
    interview_stage_history = models.JSONField(default=list, blank=True)
    # Employer technical screening scorecard (criteria ratings + weighted total)
    screening_evaluation = models.JSONField(default=dict, blank=True)

    @property
    def current_interview_stage_number(self):
        if self.status == 'Pending':
            return 0
        if not self.interview_stage:
            return 1
        if 'Initial' in self.interview_stage:
            return 1
        elif 'Online' in self.interview_stage or 'F2F' in self.interview_stage or 'Face-to-Face' in self.interview_stage:
            return 2
        elif 'Final' in self.interview_stage or 'Hiring Manager' in self.interview_stage:
            return 3
        elif 'Completed' in self.interview_stage:
            return 4
        return 1

    # Stage 1: Onboarding Details
    accepted_position = models.CharField(max_length=200, blank=True, null=True)
    accepted_salary = models.CharField(max_length=100, blank=True, null=True)
    reporting_date = models.DateField(blank=True, null=True)
    work_location = models.CharField(max_length=255, blank=True, null=True)
    employment_type = models.CharField(max_length=100, blank=True, null=True) # Regular, Probationary, Project-Based, Contract, Part-Time
    probationary_period_months = models.PositiveIntegerField(default=6, blank=True, null=True)
    employer_remarks = models.TextField(blank=True, null=True)
    
    # Stage 2: Applicant Response
    decline_reason = models.CharField(max_length=100, blank=True, null=True)
    decline_remarks = models.TextField(blank=True, null=True)
    
    # Stage 2: Pre-employment Checklist Uploads
    nbi_clearance = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    medical_certificate = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    sss_number = models.CharField(max_length=50, blank=True, null=True)
    philhealth_number = models.CharField(max_length=50, blank=True, null=True)
    pagibig_number = models.CharField(max_length=50, blank=True, null=True)
    bir_tin = models.CharField(max_length=50, blank=True, null=True)
    birth_certificate = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    diploma_transcript = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    prev_employment_cert = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    tesda_cert = models.FileField(upload_to='pre_employment/', blank=True, null=True)
    
    # Stage 3: PESO Coordinator
    pre_employment_status = models.CharField(max_length=20, default='Incomplete')
    coordination_notes = models.TextField(blank=True, null=True)
    
    # Stage 4: Physical Report
    reported_for_work = models.CharField(max_length=20, default='Awaiting')
    actual_start_date = models.DateField(blank=True, null=True)
    
    # Stage 5: Mid-Probation Monitoring
    mid_probation_outcome = models.CharField(max_length=100, blank=True, null=True)
    mid_probation_notes = models.TextField(blank=True, null=True)
    mid_probation_checked_at = models.DateTimeField(blank=True, null=True)
    
    # Stage 6: Probation Completion
    probation_extension_end_date = models.DateField(blank=True, null=True)
    separation_reason = models.CharField(max_length=100, blank=True, null=True)
    
    # Stage 7: Evaluation Feedback
    # Employer eval of candidate
    eval_emp_quality = models.PositiveIntegerField(blank=True, null=True)
    eval_emp_skills_accurate = models.CharField(max_length=10, blank=True, null=True)
    eval_emp_certs_genuine = models.CharField(max_length=10, blank=True, null=True)
    eval_emp_future_referrals = models.CharField(max_length=10, blank=True, null=True)
    eval_emp_satisfaction = models.PositiveIntegerField(blank=True, null=True)
    eval_emp_notes = models.TextField(blank=True, null=True)
    
    # Applicant eval of job match
    eval_app_accurate_desc = models.CharField(max_length=10, blank=True, null=True)
    eval_app_terms_met = models.CharField(max_length=10, blank=True, null=True)
    eval_app_expectations = models.TextField(blank=True, null=True)
    eval_app_future_use = models.CharField(max_length=10, blank=True, null=True)
    eval_app_satisfaction = models.PositiveIntegerField(blank=True, null=True)

    def __str__(self):
        return f"{self.applicant.user.username} referred to {self.job_vacancy.title}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        old_attempts_count = 0
        if not is_new:
            try:
                db_values = Referral.objects.filter(pk=self.pk).values('status', 'contact_attempts').first()
                if db_values:
                    old_status = db_values.get('status')
                    old_attempts = db_values.get('contact_attempts') or []
                    if isinstance(old_attempts, list):
                        old_attempts_count = len(old_attempts)
            except Exception:
                pass
        
        super().save(*args, **kwargs)
        
        current_attempts = self.contact_attempts or []
        current_attempts_count = 0
        if isinstance(current_attempts, list):
            current_attempts_count = len(current_attempts)
            
        if is_new or (old_status is not None and old_status != self.status) or (current_attempts_count > old_attempts_count):
            try:
                from .models import Notification
                if is_new:
                    msg = f"Your application for '{self.job_vacancy.title}' has been submitted successfully."
                elif self.status == 'Withdrawn — Position Closed':
                    company = self.job_vacancy.employer.company_name or 'the employer'
                    msg = f"The job posting for '{self.job_vacancy.title}' at {company} has been closed. Your application has been automatically withdrawn from active consideration."
                elif old_status != self.status:
                    msg = f"Your application status for '{self.job_vacancy.title}' has been updated to '{self.status}'."
                else:
                    msg = f"A new update/contact attempt has been logged for your application to '{self.job_vacancy.title}'."
                
                from django.utils import timezone
                import datetime
                recent_time = timezone.now() - datetime.timedelta(seconds=5)
                already_exists = Notification.objects.filter(
                    user=self.applicant.user,
                    message=msg,
                    created_at__gte=recent_time
                ).exists()
                
                if not already_exists:
                    Notification.objects.create(
                        user=self.applicant.user,
                        message=msg,
                        notif_type='general'
                    )
            except Exception:
                pass

class Notification(models.Model):
    NOTIF_TYPE_CHOICES = [
        ('general', 'General'),
        ('match_alert', 'Match Alert'),
        ('offer_sent', 'Offer Sent'),
        ('offer_response', 'Offer Response'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notif_type = models.CharField(max_length=30, choices=NOTIF_TYPE_CHOICES, default='general')
    # Optional FKs for rich notifications (match alerts, offers)
    applicant_profile = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True, related_name='received_notifications')
    vacancy = models.ForeignKey('JobVacancy', on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message[:30]}..."


class GapScoreLog(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='gap_logs')
    job_vacancy = models.ForeignKey(JobVacancy, on_delete=models.CASCADE, related_name='gap_logs')
    match_percentage = models.FloatField()
    gap_data = models.JSONField(default=list)
    logged_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.profile.user.username} matching {self.job_vacancy.title} ({self.match_percentage}%)"


class JobBookmark(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='bookmarked_jobs')
    job_vacancy = models.ForeignKey(JobVacancy, on_delete=models.CASCADE, related_name='bookmarks')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('profile', 'job_vacancy')

    def __str__(self):
        return f"{self.profile.user.username} bookmarked {self.job_vacancy.title}"


class ApplicantDocument(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to='applicant_documents/')
    file_name = models.CharField(max_length=255)
    file_size = models.CharField(max_length=50)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.profile.user.username} - {self.file_name}"


class Interview(models.Model):
    employer = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='scheduled_interviews')
    title = models.CharField(max_length=200, blank=True, null=True)
    candidate = models.ForeignKey(Profile, on_delete=models.SET_NULL, blank=True, null=True, related_name='interviews')
    vacancy = models.ForeignKey(JobVacancy, on_delete=models.SET_NULL, blank=True, null=True)
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    interview_type = models.CharField(max_length=50, default='Video Call')  # Video Call, Phone Call, In-Person
    round_name = models.CharField(max_length=100, default='Screening')  # Screening, Technical Interview, Final Round, Hiring Sync
    meeting_link = models.URLField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'start_time']

    def __str__(self):
        if self.candidate:
            return f"Interview with {self.candidate.user.get_full_name()} for {self.vacancy.title if self.vacancy else 'Job'}"
        return self.title or f"Meeting on {self.date}"

    @property
    def is_past(self):
        import datetime
        from django.utils import timezone
        now = timezone.localtime()
        # combine date and time
        interview_datetime = timezone.make_aware(datetime.datetime.combine(self.date, self.start_time))
        return interview_datetime < now


class CompanyReview(models.Model):
    employer = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='company_reviews', limit_choices_to={'role': 'employer'})
    reviewer = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='submitted_company_reviews')
    rating = models.PositiveSmallIntegerField(default=5)  # 1 to 5
    review_title = models.CharField(max_length=255, blank=True)
    review_text = models.TextField()
    employment_status = models.CharField(max_length=100, default='Applicant / Candidate')  # Applicant, Current Employee, Former Employee, Intern
    pros = models.TextField(blank=True, null=True)
    cons = models.TextField(blank=True, null=True)
    is_anonymous = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Review ({self.rating}★) for {self.employer.company_name} by {self.reviewer.user.username}"

    @property
    def star_range(self):
        return range(self.rating)

    @property
    def empty_star_range(self):
        return range(max(0, 5 - self.rating))


class SystemSetting(models.Model):
    MATCHING_SENSITIVITY_CHOICES = [
        ('strict', 'Strict (90%+ Match)'),
        ('balanced', 'Balanced (75%+ Match)'),
        ('lenient', 'Lenient (50%+ Match)'),
    ]

    REFRESH_RATE_CHOICES = [
        ('realtime', 'Real-time'),
        ('daily', 'Daily Batch'),
        ('weekly', 'Weekly Sync'),
    ]

    ai_job_matching = models.BooleanField(default=True)
    strict_skill_verification = models.BooleanField(default=False)
    matching_sensitivity = models.CharField(
        max_length=20,
        choices=MATCHING_SENSITIVITY_CHOICES,
        default='balanced'
    )
    refresh_rate = models.CharField(
        max_length=20,
        choices=REFRESH_RATE_CHOICES,
        default='daily'
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SystemSetting (Sensitivity: {self.matching_sensitivity}, AI: {self.ai_job_matching})"

    @classmethod
    def get_settings(cls):
        setting, _ = cls.objects.get_or_create(id=1)
        return setting

    @property
    def matching_threshold(self):
        if self.matching_sensitivity == 'strict':
            return 90
        elif self.matching_sensitivity == 'lenient':
            return 50
        return 75

    @property
    def sensitivity_display(self):
        mapping = dict(self.MATCHING_SENSITIVITY_CHOICES)
        return mapping.get(self.matching_sensitivity, 'Balanced (75%+ Match)')

    @property
    def refresh_rate_display(self):
        mapping = dict(self.REFRESH_RATE_CHOICES)
        return mapping.get(self.refresh_rate, 'Daily Batch')


class ConfigurationAudit(models.Model):
    admin_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    parameter_changed = models.CharField(max_length=100)
    old_value = models.CharField(max_length=100)
    new_value = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default='COMMITTED')
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.parameter_changed}: {self.old_value} -> {self.new_value} ({self.status})"


@receiver([post_save, post_delete], sender=WorkExperience)
def sync_profile_experience_years(sender, instance, **kwargs):
    """Keep Profile.experience_years in sync with the work-history entries."""
    try:
        profile = instance.profile
    except Profile.DoesNotExist:
        return
    from .views import calculate_total_experience_years

    years = calculate_total_experience_years(profile)
    Profile.objects.filter(pk=profile.pk).update(experience_years=years)


@receiver([post_save, post_delete], sender=Referral)
def sync_applicant_employment_status(sender, instance, **kwargs):
    """Keep the applicant's status aligned with their active employment record."""
    try:
        applicant = instance.applicant
    except Profile.DoesNotExist:
        return

    active = list(
        Referral.objects.filter(
            applicant=applicant,
            status__in=Referral.ACTIVE_EMPLOYMENT_STATUSES,
        ).values_list('status', flat=True)
    )
    if not active:
        if (applicant.status or '').startswith(('Hired', 'Employed')):
            Profile.objects.filter(pk=applicant.pk).update(status='Active — Job Seeking')
        return

    if any(s in ('Regularly Employed', 'Still Employed — Performing Well', 'Still Employed — On Improvement Plan') for s in active):
        new_status = 'Employed — Regular'
    elif any(s in ('Hired — Probationary', 'Hired — Regular') for s in active):
        new_status = 'Hired'
    else:
        new_status = 'Employed — Onboarding'

    if applicant.status != new_status:
        Profile.objects.filter(pk=applicant.pk).update(status=new_status)

