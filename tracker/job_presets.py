"""Reusable job-posting templates for the employer "Post a Job" form.

Each preset pre-fills the title, category, salary range, description, skills,
certifications and requirements. Everything remains editable by the employer.
"""

JOB_PRESETS = {
    "software_dev": {
        "title": "Web Developer",
        "aliases": [
            "web developer", "software developer", "software engineer",
            "full stack developer", "frontend developer", "front end developer",
            "backend developer", "back end developer", "programmer",
            "web programmer", "application developer",
        ],
        "category": "IT",
        "location": "Metro Manila / Hybrid",
        "min_salary": 30000,
        "max_salary": 60000,
        "slots": 2,
        "experience": 1,
        "education": "College graduate",
        "description": (
            "Design, build and maintain responsive web applications and REST APIs. "
            "Collaborate with designers and product owners to translate requirements "
            "into reliable, tested features. Review code, troubleshoot production issues "
            "and document integrations with third-party services."
        ),
        "remarks": (
            "Portfolio or GitHub link is an advantage. Hybrid setup with flexible hours; "
            "equipment and internet allowance provided."
        ),
        "skills": [
            "HTML", "CSS", "JavaScript", "Git", "SQL", "API Integration",
            "REST API Development", "Responsive Web Design", "Troubleshooting",
        ],
        "certifications": ["Computer Systems Servicing NC II"],
    },
    "welder_smaw": {
        "title": "Shielded Metal Arc Welder (SMAW)",
        "aliases": [
            "welder", "smaw", "shielded metal arc welder", "mig welder",
            "tig welder", "structural welder", "pipefitter",
        ],
        "category": "TVT",
        "location": "Valenzuela City / On-site",
        "min_salary": 18000,
        "max_salary": 28000,
        "slots": 3,
        "experience": 1,
        "education": "High school",
        "description": (
            "Fabricate and weld structural metal components using SMAW and related "
            "processes. Read blueprints and work orders, prepare joints, inspect welds "
            "and follow strict workshop safety and quality standards."
        ),
        "remarks": "Personal protective equipment and welding consumables provided. Overtime pay applies.",
        "skills": ["Welding", "Blueprint Reading", "Occupational Safety", "Measurement"],
        "certifications": ["SMAW NC II"],
    },
    "bpo_support": {
        "title": "Customer Service Representative",
        "aliases": [
            "customer service representative", "customer support", "bpo",
            "call center agent", "contact center", "technical support representative",
            "chat support", "customer service associate",
        ],
        "category": "BPO",
        "location": "Makati City / Hybrid",
        "min_salary": 20000,
        "max_salary": 30000,
        "slots": 8,
        "experience": 0,
        "education": "High school",
        "description": (
            "Handle customer inquiries through voice, email and chat with empathy and "
            "accuracy. Resolve issues within service-level targets, document interactions "
            "and escalate complex cases to the appropriate team."
        ),
        "remarks": "Paid training, night differential, HMO on day one and performance incentives.",
        "skills": [
            "Customer Service", "Communication", "English Communication",
            "Active Listening", "Troubleshooting",
        ],
        "certifications": ["Contact Center Services NC II"],
    },
    "admin_assistant": {
        "title": "Administrative Assistant",
        "aliases": [
            "administrative assistant", "admin assistant", "office assistant",
            "executive assistant", "office clerk", "administrative officer",
            "secretary", "encoder",
        ],
        "category": "ADM",
        "location": "Quezon City / On-site",
        "min_salary": 18000,
        "max_salary": 25000,
        "slots": 2,
        "experience": 1,
        "education": "College graduate",
        "description": (
            "Provide day-to-day administrative support including document preparation, "
            "records management, scheduling, correspondence and coordination with internal "
            "teams and external partners. Maintain accurate filing and tracking systems."
        ),
        "remarks": "Government benefits upon regularization. Monday to Friday, day shift.",
        "skills": [
            "Communication", "MS Office", "Time Management", "Documentation",
            "Record Keeping", "Computer Literacy",
        ],
        "certifications": [],
    },
    "fb_staff": {
        "title": "Food Service Crew",
        "aliases": [
            "food service crew", "food and beverage", "f&b staff", "restaurant crew",
            "cook", "kitchen crew", "barista", "waiter", "waitress", "service crew",
        ],
        "category": "F&B",
        "location": "Pasig City / On-site",
        "min_salary": 16000,
        "max_salary": 22000,
        "slots": 5,
        "experience": 0,
        "education": "High school",
        "description": (
            "Prepare and serve food and beverages according to standard recipes and "
            "presentation guidelines. Maintain cleanliness and food safety compliance, "
            "manage stock rotation and deliver friendly, efficient customer service."
        ),
        "remarks": "Free meals on duty, service charge share and attendance incentives.",
        "skills": [
            "Food Safety", "Food Preparation", "Customer Service", "Communication",
            "Workplace Safety",
        ],
        "certifications": ["Food and Beverage Services NC II", "Cookery NC II"],
    },
    "automotive_tech": {
        "title": "Automotive Mechanic",
        "aliases": [
            "automotive mechanic", "auto mechanic", "automotive technician",
            "automotive servicing", "diesel mechanic", "motorcycle mechanic",
            "car technician",
        ],
        "category": "MCH",
        "location": "Cebu City / On-site",
        "min_salary": 18000,
        "max_salary": 30000,
        "slots": 2,
        "experience": 1,
        "education": "Vocational / Tech Grad",
        "description": (
            "Diagnose, repair and maintain light vehicles and motorcycles. Perform "
            "preventive maintenance, engine and electrical troubleshooting, and quality "
            "checks. Document work performed and advise customers on needed repairs."
        ),
        "remarks": "Tool allowance and TESDA training sponsorship available.",
        "skills": [
            "Automotive Servicing", "Automotive Repair", "Troubleshooting",
            "Occupational Safety",
        ],
        "certifications": ["Automotive Servicing NC II"],
    },
    "electrician": {
        "title": "Building Electrician",
        "aliases": [
            "electrician", "building electrician", "electrical technician",
            "electrical installer", "maintenance electrician", "wireman",
        ],
        "category": "ELC",
        "location": "Metro Manila / On-site",
        "min_salary": 20000,
        "max_salary": 32000,
        "slots": 2,
        "experience": 1,
        "education": "Vocational / Tech Grad",
        "description": (
            "Install, inspect and repair electrical wiring, fixtures and control systems "
            "in commercial and residential buildings. Read electrical plans, follow the "
            "Philippine Electrical Code and perform preventive maintenance safely."
        ),
        "remarks": "Safety gear, tools and NC II renewal support provided.",
        "skills": [
            "Electrical Wiring", "Troubleshooting", "Occupational Safety", "Measurement",
        ],
        "certifications": ["Electrical Installation & Maintenance NC II"],
    },
}


def match_preset_key(title):
    """Return the preset key whose alias best matches a job title, if any."""
    if not title:
        return None
    text = str(title).strip().lower()
    if len(text) < 3:
        return None
    best_key = None
    best_len = 0
    for key, preset in JOB_PRESETS.items():
        for alias in [preset["title"], *preset.get("aliases", [])]:
            alias = alias.lower()
            if text == alias or alias in text or (len(text) >= 5 and text in alias):
                if len(alias) > best_len:
                    best_key = key
                    best_len = len(alias)
    return best_key
