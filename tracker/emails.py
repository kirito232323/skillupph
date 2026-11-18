import os
import json
import urllib.request
import urllib.error
from django.conf import settings
from django.core.mail import send_mail

DUMMY_EMAIL_DOMAINS = ("@example.com", "@test.com", "@sample.com", "@localhost", "@invalid")


def is_dummy_email(email):
    """Returns True if the email is a seeded test/dummy address."""
    if not email or not isinstance(email, str):
        return True
    clean_email = email.strip().lower()
    return any(clean_email.endswith(d) for d in DUMMY_EMAIL_DOMAINS)


def send_notification_email(subject, message, recipient_list):
    """
    Sends an email using:
    1. Google Apps Script / Gmail Webhook (sends directly from your Gmail via HTTPS Port 443)
    2. Resend HTTPS API (Port 443)
    3. Brevo HTTPS API (Port 443)
    4. Django standard SMTP (Local / Unblocked hosts)
    """
    # Filter out dummy/sample test addresses (e.g. @example.com) to prevent mailer-daemon bounces
    real_recipients = [r for r in recipient_list if not is_dummy_email(r)]
    if not real_recipients:
        print(f"[TEST EMAIL SIMULATED] Skipped outbound SMTP for sample accounts: {recipient_list}")
        return True
    recipient_list = real_recipients

    gmail_webhook_url = os.environ.get('GMAIL_WEBHOOK_URL')
    resend_api_key = os.environ.get('RESEND_API_KEY')
    brevo_api_key = os.environ.get('BREVO_API_KEY')
    
    # 1. Try Gmail Webhook (Native Gmail via HTTPS Port 443 - zero block on Render)
    if gmail_webhook_url:
        try:
            for recipient in recipient_list:
                payload = {
                    "to": recipient,
                    "subject": subject,
                    "body": message
                }
                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    gmail_webhook_url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "SkillupApp/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=8) as response:
                    print(f"Gmail Webhook sent to {recipient}, status: {response.status}")
            return True
        except Exception as e:
            print("Gmail Webhook API error:", e)

    # 2. Try Resend HTTPS API (Port 443)
    if resend_api_key:
        try:
            url = "https://api.resend.com/emails"
            payload = {
                "from": "SKILLUP <onboarding@resend.dev>",
                "to": recipient_list,
                "subject": subject,
                "text": message
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {resend_api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "SkillupApp/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in (200, 201):
                    print("Resend API email sent successfully to", recipient_list)
                    return True
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode('utf-8', errors='ignore')
                print(f"Resend API HTTPError {e.code}: {err_body}")
            except Exception:
                print("Resend API Email error:", e)
        except Exception as e:
            print("Resend API Email error:", e)

    # 3. Try Brevo HTTPS API (Port 443)
    if brevo_api_key:
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            payload = {
                "sender": {"name": "SKILLUP", "email": getattr(settings, 'DEFAULT_FROM_EMAIL', 'integratedskillsgapskillup@gmail.com')},
                "to": [{"email": r} for r in recipient_list],
                "subject": subject,
                "textContent": message
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "api-key": brevo_api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "SkillupApp/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in (200, 201):
                    print("Brevo API email sent successfully to", recipient_list)
                    return True
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode('utf-8', errors='ignore')
                print(f"Brevo API HTTPError {e.code}: {err_body}")
            except Exception:
                print("Brevo API Email error:", e)
        except Exception as e:
            print("Brevo API Email error:", e)

    # 4. Standard SMTP send_mail (Works in local dev, and on paid/unblocked hosts)
    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
            fail_silently=False
        )
        return sent_count > 0
    except Exception as e:
        print("SMTP send_mail error:", e)
        return False


# ==========================================
# APPLICANT LIFECYCLE & PROCESS EMAIL ALERTS
# ==========================================

def _get_applicant_info(applicant_profile):
    """Safely extracts applicant's name and email."""
    if not applicant_profile or not hasattr(applicant_profile, 'user'):
        return "Applicant", None
    user = applicant_profile.user
    name = user.get_full_name() or user.first_name or "Applicant"
    email = user.email
    return name, email


def _get_company_title(vacancy):
    """Safely extracts company name from vacancy."""
    if not vacancy:
        return "the employer"
    if hasattr(vacancy, 'employer') and vacancy.employer:
        return vacancy.employer.company_name or vacancy.employer.user.get_full_name() or "the employer"
    return "the employer"


def send_referral_approved_email(applicant_profile, vacancy, remarks=None):
    """Sent when PESO Admin approves and refers candidate to a vacancy."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    job_title = vacancy.title if vacancy else "Job Opportunity"
    location = getattr(vacancy, 'location', 'PESO Partner Location')

    subject = f"PESO Referral Approved: Endorsed for {job_title} at {company}"
    message = f"""Dear {name},

Great news from the Public Employment Service Office (PESO)!

Your profile and verified qualifications have been officially APPROVED for referral to {company}.

Job Opportunity Details:
• Position: {job_title}
• Company: {company}
• Work Location: {location}
{f'• Coordinator Remarks: {remarks}' if remarks else ''}

What happens next?
1. Your verified profile, skills, and credentials have been forwarded directly to {company}.
2. The hiring team is now reviewing your application and will contact you regarding next steps or interview schedules.
3. You can monitor your application status in real-time under "My Applications" on your SKILLUP Dashboard.

We wish you the best as your application moves forward!

Warm regards,
Public Employment Service Office (PESO) & SKILLUP Team
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending referral approval email to {email}: {e}")
        return False


def send_interview_scheduled_email(applicant_profile, vacancy, stage, mode, sched_date=None, sched_time=None, meeting_link=None, notes=None):
    """Sent when an interview or assessment stage is scheduled or advanced by employer."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    job_title = vacancy.title if vacancy else "Job Position"

    date_str = sched_date.strftime('%B %d, %Y') if hasattr(sched_date, 'strftime') else (str(sched_date) if sched_date else 'To Be Announced')
    time_str = sched_time.strftime('%I:%M %p') if hasattr(sched_time, 'strftime') else (str(sched_time) if sched_time else 'To Be Announced')

    subject = f"Interview Scheduled: {stage} for {job_title} at {company}"
    message = f"""Dear {name},

Congratulations! You have progressed to an interview stage for the position of {job_title} with {company}.

Interview Schedule Details:
• Stage / Round: {stage}
• Interview Mode: {mode}
• Scheduled Date: {date_str}
• Scheduled Time: {time_str}
{f'• Meeting Link / Venue Address: {meeting_link}' if meeting_link else ''}
{f'• Special Instructions: {notes}' if notes else ''}

Preparation Checklist:
1. Please be available or on-site 5-10 minutes prior to the scheduled start time.
2. If this is an online assessment, ensure your camera, microphone, and stable connection are set up.
3. Review the competencies and requirements listed on your SKILLUP application page.

Best of luck with your interview!

Sincerely,
{company} Hiring Team
via SKILLUP Workforce Platform
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending interview email to {email}: {e}")
        return False


def send_job_offer_email(applicant_profile, vacancy, position=None, salary=None, reporting_date=None, employment_type=None, work_location=None, remarks=None):
    """Sent when employer extends a job offer or records offer acceptance."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    pos = position or (vacancy.title if vacancy else "Position")
    loc = work_location or (vacancy.location if vacancy else "Company Office")
    emp_type = employment_type or "Probationary / Regular"
    rep_date_str = reporting_date.strftime('%B %d, %Y') if hasattr(reporting_date, 'strftime') else (str(reporting_date) if reporting_date else 'As mutually agreed')

    subject = f"Congratulations! Official Job Offer for {pos} at {company}"
    message = f"""Dear {name},

Congratulations! We are delighted to inform you that {company} has extended an official Job Offer to you for the position of {pos}!

Offer Summary:
• Position: {pos}
• Offered Compensation: {salary or 'As stipulated in contract'}
• Employment Type: {emp_type}
• Work Location: {loc}
• Expected Start Date: {rep_date_str}
{f'• Employer Remarks: {remarks}' if remarks else ''}

Next Action Required:
Please log in to your SKILLUP account immediately to review your offer details, confirm your acceptance, and upload your pre-employment onboarding documents (NBI Clearance, Medical, etc.):
👉 Go to My Applications: View and confirm onboarding requirements.

Congratulations on this milestone in your professional career!

Warm regards,
{company} & The SKILLUP Placement Team
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending job offer email to {email}: {e}")
        return False


def send_hired_confirmation_email(applicant_profile, vacancy, start_date=None, employment_status=None):
    """Sent when candidate is confirmed for onboarding or reports for work (Hired)."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    job_title = vacancy.title if vacancy else "Position"
    start_str = start_date.strftime('%B %d, %Y') if hasattr(start_date, 'strftime') else (str(start_date) if start_date else 'Confirmed')
    status_str = employment_status or "Hired"

    subject = f"Welcome Aboard! Employment Confirmed for {job_title} at {company}"
    message = f"""Dear {name},

Congratulations! Your employment and onboarding for {job_title} with {company} have been officially confirmed.

Employment Summary:
• Position: {job_title}
• Employer: {company}
• Status: {status_str}
• Effective Start Date: {start_str}

PESO and the SKILLUP team are proud of your achievement. Thank you for utilizing our public employment and workforce platform. We wish you continued growth, productivity, and success in your new career!

Sincerely,
PESO Employment Tracking & {company}
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending hired email to {email}: {e}")
        return False


def send_application_submitted_email(applicant_profile, vacancy):
    """Sent when an applicant applies for a job vacancy."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    job_title = vacancy.title if vacancy else "Job Position"

    subject = f"Application Received: {job_title} at {company}"
    message = f"""Dear {name},

Your application for {job_title} at {company} has been successfully submitted via SKILLUP.

Application Summary:
• Job Title: {job_title}
• Company: {company}
• Date Submitted: Today

What happens next?
The employer and PESO coordinators have received your profile and credentials. You will receive email notifications as soon as your application progresses through screening, referral, and interviews.

You can track your application status at any time under "My Applications" on your SKILLUP Dashboard.

Best regards,
SKILLUP Placement Team
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending application submitted email to {email}: {e}")
        return False


def send_application_status_update_email(applicant_profile, vacancy, status_name, remarks=None):
    """Sent when an application status is updated (Shortlisted, Review, Declined, etc.)."""
    name, email = _get_applicant_info(applicant_profile)
    if not email:
        return False
    company = _get_company_title(vacancy)
    job_title = vacancy.title if vacancy else "Position"

    status_lower = str(status_name).lower()
    is_declined = any(k in status_lower for k in ["decline", "reject", "not hired", "disqualified", "failed", "unsuccessful"])
    is_shortlisted = any(k in status_lower for k in ["shortlist", "interview", "review", "screen", "endorsed", "selected"])
    is_hired = "hire" in status_lower

    if is_declined:
        subject = f"Application Update: {job_title} at {company}"
        status_body = f"""While you were not selected for this specific vacancy, your job search journey continues:
1. PESO continues to automatically match your profile with other active vacancies in the city.
2. You can take free certified bridging courses on SKILLUP to boost your competencies:
   👉 Check Learning Recommendations on your dashboard.

We encourage you to explore other matching job vacancies on SKILLUP."""
    elif is_hired:
        subject = f"Congratulations! Employment Update for {job_title} at {company}"
        status_body = f"""Congratulations! Your application has been updated to: {status_name}.

The employer and PESO coordinators will coordinate with you regarding the formal onboarding procedure and reporting dates.
You can review your updated application details under "My Applications" on your SKILLUP Dashboard."""
    elif is_shortlisted:
        subject = f"Good News: Application Status Updated to {status_name} for {job_title} at {company}"
        status_body = f"""Great news! Your qualifications have been reviewed, and your application has advanced to: {status_name}.

What happens next?
1. The hiring team and PESO coordinators are processing your credentials for the next evaluation stage.
2. Please keep your contact information updated and check your inbox and SKILLUP Dashboard for upcoming interview or assessment notifications.
3. You can review your application progress anytime under "My Applications" on your SKILLUP Dashboard."""
    else:
        subject = f"Application Status Update: {job_title} at {company}"
        status_body = f"""Your application status has been updated to: {status_name}.

You can review your full application details and updates under "My Applications" on your SKILLUP Dashboard."""

    message = f"""Dear {name},

Thank you for your interest and the time you invested in applying for {job_title} with {company}.

After review, your application status has been updated to: {status_name}.
{f'• Feedback / Remarks: {remarks}' if remarks else ''}

{status_body}

Sincerely,
PESO Placement Office & SKILLUP Team
"""
    try:
        return send_notification_email(subject, message, [email])
    except Exception as e:
        print(f"Error sending status update email to {email}: {e}")
        return False
