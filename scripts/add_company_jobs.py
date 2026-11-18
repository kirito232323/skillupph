"""Top up every demo employer to 20 open job vacancies.

Local employers get Philippine locations; the two OFW placement agencies get
overseas locations. Run with:

    .\\.venv\\Scripts\\python.exe scripts\\add_company_jobs.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "skillup_project.settings")

import django

django.setup()

from tracker.models import CentralizedSkill, JobSkillRequirement, JobVacancy, Profile  # noqa: E402

TARGET_PER_EMPLOYER = 20

LOCAL_LOCATIONS = [
    "BGC, Taguig City", "Makati City", "Quezon City", "Pasig City", "Mandaluyong City",
    "Alabang, Muntinlupa", "Cebu City", "Davao City", "Clark, Pampanga", "Laguna",
    "Cavite", "Batangas", "Iloilo City", "Bacolod City", "Baguio City",
]

OFW_LOCATIONS = [
    "Riyadh, Saudi Arabia", "Jeddah, Saudi Arabia", "Dubai, UAE", "Abu Dhabi, UAE",
    "Doha, Qatar", "Kuwait City, Kuwait", "Singapore", "Hong Kong",
    "Tokyo, Japan", "Taipei, Taiwan", "Toronto, Canada", "Sydney, Australia",
    "Milan, Italy", "London, United Kingdom", "Auckland, New Zealand",
]

OFW_CITY_BY_COUNTRY = {
    "Saudi Arabia": "Riyadh, Saudi Arabia",
    "UAE": "Dubai, UAE",
    "Qatar": "Doha, Qatar",
    "Kuwait": "Kuwait City, Kuwait",
    "Singapore": "Singapore",
    "Hong Kong": "Hong Kong",
    "Japan": "Tokyo, Japan",
    "Taiwan": "Taipei, Taiwan",
    "Canada": "Toronto, Canada",
    "Australia": "Sydney, Australia",
    "New Zealand": "Auckland, New Zealand",
    "United Kingdom": "London, United Kingdom",
    "Italy": "Milan, Italy",
}


def ofw_location(title, locations, index):
    if " - " in title:
        country = title.rsplit(" - ", 1)[1].strip()
        if country in OFW_CITY_BY_COUNTRY:
            return OFW_CITY_BY_COUNTRY[country]
    return locations[index % len(locations)]

# company name -> (category, local|ofw, locations, [(title, skills, min_education, experience)])
COMPANIES = {
    "Apex Global Technology Solutions": {
        "mode": "local",
        "locations": ["BGC, Taguig City", "Makati City", "Quezon City", "Pasig City", "Cebu City", "Davao City", "Clark, Pampanga", "Laguna"],
        "roles": [
            ("Senior React & Frontend Developer", ["React", "TypeScript", "JavaScript"], "Bachelor's Degree in Computer Science or related field", 3),
            ("Backend Python Developer", ["Python", "Django", "REST API Development"], "Bachelor's Degree in Computer Science or related field", 2),
            ("Full-Stack Developer", ["React", "Node.js", "PostgreSQL"], "Bachelor's Degree in Computer Science or related field", 2),
            ("DevOps Engineer", ["Docker", "Kubernetes", "CI/CD"], "Bachelor's Degree in Computer Science or related field", 3),
            ("Data Analyst", ["SQL", "Python", "Data Analysis"], "Bachelor's Degree in Statistics or related field", 1),
            ("QA Automation Engineer", ["Playwright", "JavaScript", "Test Automation"], "Bachelor's Degree in Computer Science or related field", 2),
            ("IT Support Specialist", ["Technical Support", "Windows", "Networking"], "Bachelor's Degree in IT or related field", 1),
            ("Cloud Engineer", ["AWS", "Azure", "Cloud Fundamentals"], "Bachelor's Degree in Computer Science or related field", 2),
            ("Mobile Developer", ["React Native", "JavaScript", "Mobile Development"], "Bachelor's Degree in Computer Science or related field", 2),
            ("Cybersecurity Analyst", ["Cybersecurity Awareness", "Network Security", "Risk Assessment"], "Bachelor's Degree in Cybersecurity or related field", 2),
            ("UI/UX Designer", ["Figma", "UI Design", "User Research"], "Bachelor's Degree in Design or related field", 2),
            ("Business Analyst", ["Requirements Gathering", "Agile Methodology", "Data Analysis"], "Bachelor's Degree in Business or IT", 2),
            ("Database Administrator", ["SQL", "PostgreSQL", "Database Fundamentals"], "Bachelor's Degree in Computer Science or related field", 2),
            ("Systems Administrator", ["Linux", "Networking", "Technical Support"], "Bachelor's Degree in IT or related field", 2),
            ("AI / Machine Learning Engineer", ["Python", "Machine Learning", "Data Analysis"], "Bachelor's Degree in Computer Science or related field", 3),
            ("Technical Writer", ["Technical Documentation", "API Documentation"], "Bachelor's Degree in Communications or IT", 1),
            ("Scrum Master", ["Agile Methodology", "Project Coordination", "Scrum"], "Bachelor's Degree in IT or Business", 3),
            ("Network Engineer", ["Networking", "Cisco", "Network Security"], "Bachelor's Degree in IT or related field", 2),
            ("API Integration Specialist", ["API Integration", "REST API Development", "JSON"], "Bachelor's Degree in Computer Science or related field", 2),
            ("Solutions Architect", ["System Integration", "Cloud Fundamentals", "Architecture"], "Bachelor's Degree in Computer Science or related field", 5),
        ],
    },
    "Prime Global BPO & Enterprise Operations": {
        "mode": "local",
        "locations": ["Quezon City", "Makati City", "Taguig City", "Pasig City", "Cebu City", "Davao City", "Iloilo City", "Bacolod City", "Pampanga"],
        "roles": [
            ("Customer Service Representative", ["Customer Service", "Communication", "Active Listening"], "High School Graduate / College Level", 0),
            ("Technical Support Representative", ["Technical Support", "Customer Service", "Troubleshooting"], "High School Graduate / College Level", 1),
            ("Team Leader", ["Leadership", "Customer Service", "Coaching"], "Bachelor's Degree in any field", 3),
            ("Quality Analyst", ["Quality Assurance", "Data Analysis", "Communication"], "Bachelor's Degree in any field", 2),
            ("Workforce Analyst", ["Data Analysis", "Forecasting", "Excel"], "Bachelor's Degree in Business or Math", 1),
            ("Recruitment Specialist", ["Recruitment", "Communication", "HR"], "Bachelor's Degree in Psychology or HR", 1),
            ("HR Generalist", ["HR", "Employee Relations", "Communication"], "Bachelor's Degree in HR or Psychology", 2),
            ("Payroll Specialist", ["Payroll", "Accounting", "Excel"], "Bachelor's Degree in Accounting or Finance", 2),
            ("Accounts Receivable Specialist", ["Accounting", "Accounts Receivable", "Excel"], "Bachelor's Degree in Accounting or Finance", 1),
            ("Collections Specialist", ["Collections", "Negotiation", "Communication"], "High School Graduate / College Level", 1),
            ("Sales Agent", ["Sales", "Negotiation", "Customer Service"], "High School Graduate / College Level", 0),
            ("Retention Specialist", ["Customer Service", "Negotiation", "Communication"], "High School Graduate / College Level", 1),
            ("Chat Support Associate", ["Customer Service", "Written Communication", "Typing"], "High School Graduate / College Level", 0),
            ("Email Support Associate", ["Written Communication", "Customer Service", "Email Management"], "High School Graduate / College Level", 0),
            ("Social Media Manager", ["Social Media", "Content Creation", "Marketing"], "Bachelor's Degree in Marketing or Communications", 2),
            ("Operations Manager", ["Operations Management", "Leadership", "Process Improvement"], "Bachelor's Degree in Business", 4),
            ("Training Specialist", ["Training & Facilitation", "Communication", "Coaching"], "Bachelor's Degree in any field", 2),
            ("Data Entry Associate", ["Data Entry", "Typing", "Attention to Detail"], "High School Graduate / College Level", 0),
            ("Back Office Associate", ["Back Office", "Data Entry", "Excel"], "High School Graduate / College Level", 0),
            ("Compliance Analyst", ["Compliance Documentation", "Risk Assessment", "Data Analysis"], "Bachelor's Degree in Business or Law", 2),
        ],
    },
    "Nexus Engineering & Infrastructure Corp.": {
        "mode": "local",
        "locations": ["Bulacan", "Pampanga", "Cavite", "Laguna", "Batangas", "Metro Manila", "Cebu City", "Davao City", "Subic, Zambales", "Clark, Pampanga"],
        "roles": [
            ("Civil Engineer", ["Civil Engineering", "AutoCAD", "Project Coordination"], "Bachelor's Degree in Civil Engineering", 2),
            ("Site Engineer", ["Construction Management", "AutoCAD", "Project Coordination"], "Bachelor's Degree in Civil Engineering", 2),
            ("Structural Engineer", ["Structural Engineering", "AutoCAD", "Analysis"], "Bachelor's Degree in Civil Engineering", 3),
            ("Electrical Engineer", ["Electrical Engineering", "AutoCAD", "Project Coordination"], "Bachelor's Degree in Electrical Engineering", 2),
            ("Mechanical Engineer", ["Mechanical Engineering", "AutoCAD", "Maintenance"], "Bachelor's Degree in Mechanical Engineering", 2),
            ("Project Engineer", ["Project Coordination", "Construction Management", "Planning"], "Bachelor's Degree in Engineering", 3),
            ("Safety Officer", ["Occupational Safety", "Risk Assessment", "Compliance Documentation"], "Bachelor's Degree / Safety Training", 2),
            ("Quantity Surveyor", ["Cost Estimation", "Construction Management", "Excel"], "Bachelor's Degree in Civil Engineering", 2),
            ("CAD Drafter", ["AutoCAD", "Drafting", "Technical Documentation"], "Vocational / Associate Degree", 1),
            ("Construction Supervisor", ["Construction Management", "Leadership", "Safety"], "Bachelor's Degree in Engineering", 3),
            ("HVAC Engineer", ["HVAC", "Mechanical Engineering", "Maintenance"], "Bachelor's Degree in Mechanical Engineering", 2),
            ("Sanitary Engineer", ["Sanitary Engineering", "AutoCAD", "Project Coordination"], "Bachelor's Degree in Sanitary Engineering", 2),
            ("Geodetic Engineer", ["Surveying", "Geomatics", "AutoCAD"], "Bachelor's Degree in Geodetic Engineering", 2),
            ("Planning Engineer", ["Project Planning", "Primavera", "Data Analysis"], "Bachelor's Degree in Engineering", 3),
            ("QA/QC Engineer", ["Quality Assurance", "Inspection", "Technical Documentation"], "Bachelor's Degree in Engineering", 2),
            ("Materials Engineer", ["Materials Testing", "Quality Assurance", "Analysis"], "Bachelor's Degree in Engineering", 2),
            ("Estimator", ["Cost Estimation", "AutoCAD", "Excel"], "Bachelor's Degree in Engineering", 2),
            ("BIM Modeler", ["BIM", "Revit", "AutoCAD"], "Bachelor's Degree in Engineering / Architecture", 2),
            ("Facilities Engineer", ["Facilities Management", "Maintenance", "Project Coordination"], "Bachelor's Degree in Engineering", 2),
            ("Environmental Engineer", ["Environmental Engineering", "Compliance Documentation", "Risk Assessment"], "Bachelor's Degree in Environmental Engineering", 2),
        ],
    },
    "MediLink Health Group": {
        "mode": "local",
        "locations": ["Manila", "Quezon City", "Makati City", "Pasig City", "Cebu City", "Davao City", "Iloilo City", "Baguio City", "Angeles, Pampanga", "Naga City"],
        "roles": [
            ("Staff Nurse", ["Nursing", "Patient Care", "Communication"], "Bachelor's Degree in Nursing", 1),
            ("Nursing Assistant", ["Patient Care", "Nursing", "Communication"], "Vocational / Caregiving Certificate", 0),
            ("Caregiver", ["Caregiving", "Patient Care", "First Aid & CPR"], "Vocational / Caregiving Certificate", 0),
            ("Medical Technologist", ["Medical Technology", "Laboratory Testing", "Quality Assurance"], "Bachelor's Degree in Medical Technology", 1),
            ("Pharmacist", ["Pharmacy", "Medication Management", "Customer Service"], "Bachelor's Degree in Pharmacy", 1),
            ("Physical Therapist", ["Physical Therapy", "Patient Care", "Rehabilitation"], "Bachelor's Degree in Physical Therapy", 2),
            ("Radiologic Technologist", ["Radiology", "Medical Imaging", "Patient Care"], "Bachelor's Degree in Radiologic Technology", 1),
            ("Respiratory Therapist", ["Respiratory Therapy", "Patient Care", "Critical Thinking"], "Bachelor's Degree in Respiratory Therapy", 2),
            ("Clinic Administrator", ["Healthcare Administration", "Operations Management", "Excel"], "Bachelor's Degree in Healthcare Management", 2),
            ("Medical Coder", ["Medical Coding", "ICD-10", "Attention to Detail"], "Bachelor's Degree in Health Information Management", 1),
            ("Patient Care Coordinator", ["Patient Care", "Coordination", "Communication"], "Bachelor's Degree in any healthcare field", 1),
            ("Laboratory Technician", ["Laboratory Testing", "Quality Assurance", "Data Entry"], "Bachelor's Degree in Medical Technology", 1),
            ("Dental Assistant", ["Dental Assisting", "Patient Care", "Sterilization"], "Vocational / Dental Assisting Certificate", 0),
            ("Nutritionist", ["Nutrition", "Patient Care", "Communication"], "Bachelor's Degree in Nutrition", 2),
            ("Health Coach", ["Health Coaching", "Communication", "Behavior Change"], "Bachelor's Degree in Health or related field", 1),
            ("Midwife", ["Midwifery", "Patient Care", "Communication"], "Bachelor's Degree in Midwifery", 1),
            ("Occupational Therapist", ["Occupational Therapy", "Patient Care", "Rehabilitation"], "Bachelor's Degree in Occupational Therapy", 2),
            ("Medical Receptionist", ["Customer Service", "Healthcare Administration", "Data Entry"], "High School Graduate / College Level", 0),
            ("Healthcare Account Specialist", ["Healthcare Administration", "Billing", "Communication"], "Bachelor's Degree in any field", 1),
            ("Community Health Worker", ["Community Health", "Communication", "Patient Care"], "Bachelor's Degree in Health or related field", 1),
        ],
    },
    "Global Horizons Placement Agency": {
        "mode": "ofw",
        "locations": OFW_LOCATIONS,
        "roles": [
            ("Staff Nurse - Saudi Arabia", ["Nursing", "Patient Care", "Communication"], "Bachelor's Degree in Nursing", 2),
            ("Caregiver - Hong Kong", ["Caregiving", "Patient Care", "First Aid & CPR"], "Caregiving Certificate", 1),
            ("Welder - Qatar", ["Welding", "Fabrication", "Safety"], "Vocational / TESDA NC II", 2),
            ("Electrician - UAE", ["Electrical Wiring", "Electrical Engineering", "Safety"], "Vocational / TESDA NC II", 2),
            ("Driver - Saudi Arabia", ["Driving", "Navigation", "Vehicle Maintenance"], "High School Graduate", 2),
            ("Housekeeping - Dubai", ["Housekeeping", "Customer Service", "Attention to Detail"], "High School Graduate", 1),
            ("Factory Worker - Taiwan", ["Manufacturing", "Machine Operation", "Quality Assurance"], "High School Graduate", 1),
            ("Cook - Singapore", ["Cooking", "Food Safety", "Kitchen Operations"], "Vocational / Culinary Certificate", 2),
            ("Waiter - Qatar", ["Customer Service", "Food Service", "Communication"], "High School Graduate", 1),
            ("Barista - UAE", ["Barista", "Customer Service", "Coffee Preparation"], "High School Graduate", 1),
            ("Construction Worker - Kuwait", ["Construction", "Safety", "Physical Stamina"], "High School Graduate", 1),
            ("Mason - Saudi Arabia", ["Masonry", "Construction", "Safety"], "Vocational / TESDA NC II", 2),
            ("Carpenter - Qatar", ["Carpentry", "Construction", "Safety"], "Vocational / TESDA NC II", 2),
            ("Plumber - UAE", ["Plumbing", "Maintenance", "Safety"], "Vocational / TESDA NC II", 2),
            ("Heavy Equipment Operator - Saudi Arabia", ["Heavy Equipment Operation", "Safety", "Maintenance"], "Vocational / TESDA NC II", 3),
            ("Automotive Mechanic - Dubai", ["Automotive Repair", "Diagnostics", "Maintenance"], "Vocational / TESDA NC II", 2),
            ("Security Guard - Qatar", ["Security", "Surveillance", "Communication"], "High School Graduate", 1),
            ("Store Supervisor - Singapore", ["Retail", "Leadership", "Inventory Management"], "Bachelor's Degree in any field", 3),
            ("Hotel Receptionist - Dubai", ["Customer Service", "Hospitality", "Communication"], "Bachelor's Degree in Hospitality", 1),
            ("Farm Worker - Japan", ["Agriculture", "Farm Operations", "Physical Stamina"], "High School Graduate", 1),
        ],
    },
    "St. Jude Medical Center & Hospital Network": {
        "mode": "local",
        "locations": ["Manila", "Quezon City", "Caloocan City", "Pasig City", "Cebu City", "Davao City", "Bacolod City", "Iloilo City"],
        "roles": [
            ("Staff Nurse", ["Nursing", "Patient Care", "Communication"], "Bachelor's Degree in Nursing", 1),
            ("Nurse Supervisor", ["Nursing", "Leadership", "Patient Care"], "Bachelor's Degree in Nursing", 4),
            ("Medical Technologist", ["Medical Technology", "Laboratory Testing", "Quality Assurance"], "Bachelor's Degree in Medical Technology", 1),
            ("Pharmacist", ["Pharmacy", "Medication Management", "Customer Service"], "Bachelor's Degree in Pharmacy", 1),
            ("Radiologic Technologist", ["Radiology", "Medical Imaging", "Patient Care"], "Bachelor's Degree in Radiologic Technology", 1),
            ("Patient Coordinator", ["Patient Care", "Coordination", "Communication"], "Bachelor's Degree in any healthcare field", 1),
            ("Billing Specialist", ["Billing", "Accounting", "Excel"], "Bachelor's Degree in Accounting or Finance", 1),
            ("Medical Records Clerk", ["Medical Records", "Data Entry", "Attention to Detail"], "Bachelor's Degree in Health Information Management", 0),
            ("Housekeeping Staff", ["Housekeeping", "Sanitation", "Attention to Detail"], "High School Graduate", 0),
            ("Security Guard", ["Security", "Surveillance", "Communication"], "High School Graduate", 1),
            ("Dietitian", ["Nutrition", "Patient Care", "Communication"], "Bachelor's Degree in Nutrition", 2),
            ("Physical Therapist", ["Physical Therapy", "Patient Care", "Rehabilitation"], "Bachelor's Degree in Physical Therapy", 2),
            ("Laboratory Aide", ["Laboratory Testing", "Patient Care", "Data Entry"], "Vocational / Associate Degree", 0),
            ("Emergency Room Nurse", ["Nursing", "Emergency Care", "Patient Care"], "Bachelor's Degree in Nursing", 2),
            ("ICU Nurse", ["Nursing", "Critical Care", "Patient Care"], "Bachelor's Degree in Nursing", 3),
            ("Pediatric Nurse", ["Nursing", "Pediatric Care", "Patient Care"], "Bachelor's Degree in Nursing", 2),
            ("HR Coordinator", ["HR", "Recruitment", "Communication"], "Bachelor's Degree in HR or Psychology", 1),
            ("IT Support Specialist", ["Technical Support", "Networking", "Troubleshooting"], "Bachelor's Degree in IT", 1),
            ("Maintenance Technician", ["Maintenance", "Electrical", "HVAC"], "Vocational / TESDA NC II", 2),
            ("Ambulance Driver", ["Driving", "Emergency Response", "First Aid & CPR"], "High School Graduate / TESDA", 2),
        ],
    },
    "Global Horizon International OFW Placement & Manpower": {
        "mode": "ofw",
        "locations": OFW_LOCATIONS,
        "roles": [
            ("Hospital Nurse - United Kingdom", ["Nursing", "Patient Care", "Communication"], "Bachelor's Degree in Nursing", 3),
            ("Caregiver - Canada", ["Caregiving", "Patient Care", "First Aid & CPR"], "Caregiving Certificate", 2),
            ("Welder - Australia", ["Welding", "Fabrication", "Safety"], "Vocational / TESDA NC II", 3),
            ("Electrician - New Zealand", ["Electrical Wiring", "Electrical Engineering", "Safety"], "Vocational / TESDA NC II", 3),
            ("Truck Driver - Saudi Arabia", ["Driving", "Vehicle Maintenance", "Navigation"], "High School Graduate / Professional License", 3),
            ("Housekeeper - Dubai", ["Housekeeping", "Customer Service", "Attention to Detail"], "High School Graduate", 1),
            ("Hotel Staff - Qatar", ["Hospitality", "Customer Service", "Communication"], "Bachelor's Degree in Hospitality", 1),
            ("Chef - Singapore", ["Cooking", "Food Safety", "Kitchen Operations"], "Vocational / Culinary Certificate", 3),
            ("Factory Worker - Japan", ["Manufacturing", "Machine Operation", "Quality Assurance"], "High School Graduate", 1),
            ("Construction Worker - UAE", ["Construction", "Safety", "Physical Stamina"], "High School Graduate", 1),
            ("Farm Worker - Australia", ["Agriculture", "Farm Operations", "Physical Stamina"], "High School Graduate", 1),
            ("Nursing Assistant - Japan", ["Patient Care", "Nursing", "Communication"], "Vocational / Caregiving Certificate", 2),
            ("Plumber - Qatar", ["Plumbing", "Maintenance", "Safety"], "Vocational / TESDA NC II", 2),
            ("Mechanic - Kuwait", ["Automotive Repair", "Diagnostics", "Maintenance"], "Vocational / TESDA NC II", 2),
            ("Security Officer - UAE", ["Security", "Surveillance", "Communication"], "High School Graduate", 1),
            ("Retail Sales Associate - Dubai", ["Retail", "Customer Service", "Sales"], "High School Graduate", 1),
            ("Restaurant Manager - Singapore", ["Restaurant Management", "Leadership", "Customer Service"], "Bachelor's Degree in Hospitality", 4),
            ("Cleaner - Hong Kong", ["Cleaning", "Sanitation", "Attention to Detail"], "High School Graduate", 1),
            ("Delivery Driver - Qatar", ["Driving", "Navigation", "Customer Service"], "High School Graduate", 2),
            ("Machine Operator - Taiwan", ["Machine Operation", "Manufacturing", "Quality Assurance"], "High School Graduate", 1),
        ],
    },
    "Prime Logistics & Retail Distribution Inc.": {
        "mode": "local",
        "locations": ["Bulacan", "Valenzuela City", "Caloocan City", "Laguna", "Cavite", "Batangas", "Pampanga", "Metro Manila"],
        "roles": [
            ("Warehouse Supervisor", ["Warehouse Management", "Leadership", "Inventory Management"], "Bachelor's Degree in Logistics or Business", 3),
            ("Inventory Clerk", ["Inventory Management", "Data Entry", "Excel"], "High School Graduate / College Level", 1),
            ("Forklift Operator", ["Forklift Operation", "Safety", "Warehouse Management"], "Vocational / TESDA NC II", 1),
            ("Delivery Driver", ["Driving", "Navigation", "Customer Service"], "High School Graduate / Professional License", 2),
            ("Truck Driver", ["Driving", "Vehicle Maintenance", "Navigation"], "High School Graduate / Professional License", 3),
            ("Dispatch Coordinator", ["Dispatch", "Coordination", "Communication"], "Bachelor's Degree in Logistics", 1),
            ("Logistics Analyst", ["Logistics", "Data Analysis", "Excel"], "Bachelor's Degree in Logistics or Business", 2),
            ("Procurement Officer", ["Procurement", "Negotiation", "Excel"], "Bachelor's Degree in Business", 2),
            ("Warehouse Associate", ["Warehouse Management", "Packing", "Inventory Management"], "High School Graduate", 0),
            ("Shipping Clerk", ["Shipping", "Data Entry", "Attention to Detail"], "High School Graduate / College Level", 0),
            ("Receiving Clerk", ["Receiving", "Inventory Management", "Data Entry"], "High School Graduate / College Level", 0),
            ("Route Planner", ["Route Planning", "Logistics", "Data Analysis"], "Bachelor's Degree in Logistics", 2),
            ("Fleet Coordinator", ["Fleet Management", "Coordination", "Excel"], "Bachelor's Degree in Logistics", 2),
            ("Safety Officer", ["Occupational Safety", "Risk Assessment", "Compliance Documentation"], "Bachelor's Degree / Safety Training", 2),
            ("Operations Manager", ["Operations Management", "Leadership", "Process Improvement"], "Bachelor's Degree in Business", 4),
            ("Customer Service Associate", ["Customer Service", "Communication", "Problem Solving"], "High School Graduate / College Level", 0),
            ("Data Encoder", ["Data Entry", "Typing", "Attention to Detail"], "High School Graduate / College Level", 0),
            ("Quality Inspector", ["Quality Assurance", "Inspection", "Attention to Detail"], "High School Graduate / Vocational", 1),
            ("Material Handler", ["Warehouse Management", "Material Handling", "Safety"], "High School Graduate", 0),
            ("Supply Chain Analyst", ["Supply Chain", "Data Analysis", "Excel"], "Bachelor's Degree in Supply Chain or Business", 2),
        ],
    },
}


def get_skill(name):
    skill, _ = CentralizedSkill.objects.get_or_create(
        name=name,
        defaults={"category": "General", "description": f"Skill requirement for {name}."},
    )
    return skill


def salary_for(mode, category):
    if mode == "ofw":
        return "₱45,000 - ₱95,000"
    return {
        "IT": "₱40,000 - ₱90,000",
        "MED": "₱28,000 - ₱65,000",
        "ENG": "₱35,000 - ₱80,000",
        "BPO": "₱22,000 - ₱45,000",
        "LOG": "₱18,000 - ₱40,000",
        "ADM": "₱20,000 - ₱40,000",
    }.get(category, "₱20,000 - ₱45,000")


def category_for(title, skills):
    t = title.lower()
    if "nurse" in t or "care" in t or "medical" in t or "pharmac" in t or "therap" in t or "health" in t or "dental" in t or "nutrition" in t or "midwife" in t:
        return "MED"
    if any(k in t for k in ["engineer", "site", "cad", "bim", "estimator", "surveyor", "construction", "facilities", "environmental"]):
        return "ENG"
    if any(k in t for k in ["warehouse", "inventory", "forklift", "driver", "dispatch", "logistics", "shipping", "receiving", "fleet", "supply chain", "material"]):
        return "LOG"
    if any(k in t for k in ["customer service", "technical support", "team leader", "quality analyst", "workforce", "recruitment", "hr ", "payroll", "accounts", "collections", "sales", "retention", "chat", "email", "social media", "operations", "training", "back office", "compliance"]):
        return "BPO"
    if any(k in t for k in ["react", "developer", "devops", "data", "qa", "it support", "cloud", "mobile", "cyber", "ui/ux", "business analyst", "database", "systems", "ai", "machine", "technical writer", "scrum", "network", "api", "solutions"]):
        return "IT"
    return "GEN"


def main():
    created = 0
    for company_name, cfg in COMPANIES.items():
        employer = Profile.objects.filter(role="employer", company_name__iexact=company_name).first()
        if not employer:
            print("SKIP (employer not found):", company_name)
            continue

        existing_titles = {
            t.strip().lower()
            for t in JobVacancy.objects.filter(employer=employer).values_list("title", flat=True)
        }
        current = JobVacancy.objects.filter(employer=employer).count()
        needed = max(0, TARGET_PER_EMPLOYER - current)
        added = 0

        for idx, (title, skill_names, education, experience) in enumerate(cfg["roles"]):
            if added >= needed:
                break
            if title.lower() in existing_titles:
                continue

            category = "OFW" if cfg["mode"] == "ofw" else category_for(title, skill_names)
            if cfg["mode"] == "ofw":
                location = ofw_location(title, cfg["locations"], idx)
            else:
                location = cfg["locations"][idx % len(cfg["locations"])]
            desc = (
                f"{company_name} is looking for a {title} based in {location}. "
                f"The successful candidate will handle day-to-day {title.lower()} responsibilities, "
                "coordinate with cross-functional teams, and help maintain high-quality service delivery. "
                "Fresh graduates and experienced applicants are welcome to apply."
            )
            if cfg["mode"] == "ofw":
                desc = (
                    f"Overseas employment opportunity for a {title} in {location}. "
                    "Applicants must have a valid passport, relevant experience, and complete PESO/POEA "
                    "documentation. Agency-assisted deployment with competitive salary and benefits."
                )

            vacancy = JobVacancy.objects.create(
                title=title,
                employer=employer,
                location=location,
                description=desc,
                salary_range=salary_for(cfg["mode"], category),
                status="Open",
                category=category,
                min_education=education,
                required_experience_years=experience,
                slots=5,
                remaining_slots=5,
            )
            for skill_name in skill_names:
                JobSkillRequirement.objects.get_or_create(
                    job_vacancy=vacancy,
                    skill=get_skill(skill_name),
                )
            existing_titles.add(title.lower())
            added += 1
            created += 1

        print(f"{company_name}: {current} -> {current + added} jobs (+{added})")

    print("TOTAL NEW JOBS:", created)

if __name__ == "__main__":
    main()
