import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'skillup_project.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone
from tracker.models import Profile, JobVacancy, JobSkillRequirement, CentralizedSkill

def get_or_create_skill(name, category='General'):
    skill, _ = CentralizedSkill.objects.get_or_create(
        name=name,
        defaults={'category': category, 'description': f'Professional competency in {name}'}
    )
    return skill

def enrich_all_job_vacancies():
    print("Enriching all Job Vacancies with LinkedIn data...")

    # 1. Update/Clean all employer profiles with rich corporate backgrounds
    employer_profiles_info = {
        'apex_employer@test.com': {
            'company_name': 'Apex Global Technology Solutions',
            'industry': 'Information Technology & Cloud Software',
            'company_size': '1,000-5,000 employees',
            'website': 'https://www.apex-techglobal.com',
            'contact_name': 'Jane Doe',
            'contact_position': 'Director of Talent Acquisition',
            'contact_email': 'jane.doe@apex-techglobal.com',
            'phone_number': '+63 (02) 8876-5432',
            'location': 'BGC, Taguig City, Metro Manila',
            'soft_notes': (
                "Apex Global Technology Solutions is a premier digital engineering and enterprise technology consulting firm. "
                "We design, build, and deploy mission-critical cloud platforms, full-stack enterprise applications, and AI-driven "
                "workflow automation for Fortune 500 enterprises and hyper-growth scaleups across the Asia-Pacific region.\n\n"
                "Our culture values technical excellence, continuous learning, and inclusive collaboration. Recognized as a Top Employer "
                "in Southeast Asia, Apex provides an environment where innovators thrive, supported by comprehensive wellness programs, "
                "certification sponsorships, and accelerated career pathways."
            )
        },
        'prime_employer@test.com': {
            'company_name': 'Prime Global BPO & Enterprise Operations',
            'industry': 'Customer Experience, BPO & Shared Services',
            'company_size': '5,000-10,000 employees',
            'website': 'https://www.primeglobal-bpo.com',
            'contact_name': 'Carlos Mendoza',
            'contact_position': 'Head of Global Talent Acquisition',
            'contact_email': 'carlos.mendoza@primeglobal-bpo.com',
            'phone_number': '+63 (02) 8632-9000',
            'location': 'Ortigas Center, Pasig City / Cebu I.T. Park',
            'soft_notes': (
                "Prime Global BPO & Enterprise Operations is an award-winning business process management and customer experience "
                "solutions provider. We operate across 8 delivery hubs in the Philippines, delivering 24/7 customer engagement, "
                "technical support, back-office administration, and digital shared services to global market leaders.\n\n"
                "We are deeply committed to employee welfare, offering world-class training academies, structured career progression "
                "pathways, modern recreational facilities, and comprehensive healthcare packages for all team members."
            )
        },
        'nexus_employer@test.com': {
            'company_name': 'Nexus Engineering & Infrastructure Corp.',
            'industry': 'Civil Engineering, Infrastructure & Commercial Construction',
            'company_size': '1,000-5,000 employees',
            'website': 'https://www.nexusengineering-ph.com',
            'contact_name': 'Engr. Ronald David',
            'contact_position': 'VP of Human Capital & Site Operations',
            'contact_email': 'ronald.david@nexusengineering-ph.com',
            'phone_number': '+63 (02) 8812-3456',
            'location': 'Makati City & Regional Project Sites across Luzon',
            'soft_notes': (
                "Nexus Engineering & Infrastructure Corp. is an ISO 9001-certified general contractor and engineering leader in the Philippines. "
                "We specialize in mega-infrastructure development, high-rise commercial structures, power plant installations, and specialized "
                "Mechanical, Electrical, and Plumbing (MEP) systems.\n\n"
                "Safety, structural durability, and technological innovation are the cornerstones of our work. We provide competitive engineering "
                "compensation, field hazard allowances, professional development sponsorships, and world-class safety training."
            )
        },
        'healthcare_employer@test.com': {
            'company_name': 'St. Jude Medical Center & Hospital Network',
            'industry': 'Healthcare, Hospital & Clinical Services',
            'company_size': '1,000-5,000 employees',
            'website': 'https://www.stjudemedical-ph.org',
            'contact_name': 'Dr. Patricia Elena Santos',
            'contact_position': 'Chief Medical HR Officer',
            'contact_email': 'hr@stjudemedical-ph.org',
            'phone_number': '+63 (02) 8721-8000',
            'location': 'Quezon City, Metro Manila',
            'soft_notes': (
                "St. Jude Medical Center is a JCI-accredited tertiary hospital and academic medical institution dedicated to patient-centered "
                "clinical excellence, medical research, and compassionate healthcare delivery. Equipped with modern intensive care units, "
                "diagnostic imaging centers, and emergency surgical suites, our complex serves over 100,000 patients annually.\n\n"
                "We provide supportive nurse-to-patient staffing ratios, continuous clinical certifications, hazardous duty allowances, "
                "comprehensive medical coverage for staff families, and clear career ladders across all allied health specialties."
            )
        },
        'ofw_employer@test.com': {
            'company_name': 'Global Horizon International OFW Placement & Manpower',
            'industry': 'Overseas Employment & Global Manpower (POEA / DMW Licensed)',
            'company_size': '500-1,000 employees',
            'website': 'https://www.globalhorizon-ofw.com',
            'contact_name': 'Atty. Ferdinand Reyes',
            'contact_position': 'Managing Director & POEA Accredited Agent',
            'contact_email': 'ferdinand.reyes@globalhorizon-ofw.com',
            'phone_number': '+63 (02) 8524-1122',
            'location': 'Manila / Dubai / Doha / Singapore / UK / Canada / Japan',
            'soft_notes': (
                "Global Horizon International is a DMW (Department of Migrant Workers) and POEA-licensed overseas manpower recruitment agency "
                "with over 20 years of trusted international recruitment service. We connect verified Filipino healthcare workers, technical engineers, "
                "vocational trade specialists, and hospitality personnel with accredited overseas employers across the GCC, Europe, North America, and Asia.\n\n"
                "All overseas job vacancies are government-verified with direct employer employment contracts, paid airfare, comprehensive overseas "
                "medical insurance, accommodation, and pre-departure orientation (PDOS)."
            )
        },
        'primelogistics_employer@test.com': {
            'company_name': 'Prime Logistics & Retail Distribution Inc.',
            'industry': 'Logistics, Supply Chain & FMCG Retail',
            'company_size': '2,000-5,000 employees',
            'website': 'https://www.primelogistics-ph.com',
            'contact_name': 'Jennifer Dela Cruz',
            'contact_position': 'Head of Supply Chain Talent Acquisition',
            'contact_email': 'jennifer.delacruz@primelogistics-ph.com',
            'phone_number': '+63 (02) 8834-5500',
            'location': 'Laguna Technopark / Paranaque City / Davao City',
            'soft_notes': (
                "Prime Logistics & Retail Distribution Inc. is one of the largest multimodal freight forwarding, modern automated warehousing, "
                "and FMCG retail supply networks in the Philippines. Spanning nationwide logistics hubs, our cold-chain facilities and last-mile "
                "delivery fleet power critical retail and e-commerce supply chains across Luzon, Visayas, and Mindanao.\n\n"
                "We foster a safety-first, merit-based working environment with rapid career advancement, certified equipment training, "
                "and competitive performance rewards for all warehouse and supply chain professionals."
            )
        }
    }

    for email, data in employer_profiles_info.items():
        try:
            u = User.objects.get(username=email)
            Profile.objects.filter(user=u).update(**data, is_verified=True, role='employer')
        except User.DoesNotExist:
            pass

    # Ensure all remaining employer profiles also have clean backgrounds
    for emp in Profile.objects.filter(role='employer'):
        if not emp.soft_notes or len(emp.soft_notes) < 50:
            emp.soft_notes = (
                f"{emp.company_name or 'Our Company'} is a verified enterprise talent partner dedicated to creating meaningful career opportunities. "
                "We foster an environment of continuous learning, diversity, equity, and sustainable professional advancement across all our divisions."
            )
            emp.save()

    # 2. Enrich every Job Vacancy with full LinkedIn-level details if too short
    for vac in JobVacancy.objects.all():
        desc = vac.description or ''
        if len(desc) < 180 or 'About the Role' not in desc:
            title = vac.title
            cat = vac.category_display
            company = vac.employer.company_name or 'Our Enterprise'

            vac.description = (
                f"About the Role:\n"
                f"{company} is seeking an exceptional and driven professional for the role of {title}. "
                f"In this position within our {cat} division, you will collaborate with experienced industry leaders to deliver top-quality results, "
                f"optimize daily operational processes, and contribute directly to organizational excellence.\n\n"
                f"Key Responsibilities:\n"
                f"• Execute core operational workflows and technical deliverables according to established enterprise benchmarks.\n"
                f"• Collaborate proactively with cross-functional team members, leadership, and external stakeholders.\n"
                f"• Maintain high standards of quality assurance, safety compliance, and documentation accuracy.\n"
                f"• Identify opportunities for continuous process improvement and operational innovation.\n\n"
                f"Required Qualifications:\n"
                f"• Proven competency and hands-on experience relevant to {title}.\n"
                f"• Strong problem-solving abilities, communication skills, and professional work ethic.\n"
                f"• Relevant bachelor's degree, vocational TESDA certification, or equivalent practical track record.\n\n"
                f"What We Offer:\n"
                f"• Comprehensive HMO healthcare coverage with dependent benefits.\n"
                f"• 13th and 14th-month pay, performance bonuses, and paid leave incentives.\n"
                f"• Government-mandated statutory benefits (SSS, PhilHealth, Pag-IBIG) and career development allowances."
            )
            if not vac.min_education:
                vac.min_education = "Bachelor's Degree or Relevant Technical / Vocational Certificate"
            if not vac.salary_range or vac.salary_range == 'Competitive':
                vac.salary_range = "₱35k - ₱55k"
            vac.status = 'Open'
            vac.save()

    print("All vacancies and employer profiles successfully enriched!")

if __name__ == '__main__':
    enrich_all_job_vacancies()
