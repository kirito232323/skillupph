from django.db import migrations

# (name, category, description)
SKILLS = [
    # Transferable / cross-industry skills
    ("API Integration", "Transferable Skills", "Connect systems and services through REST/JSON APIs, including authentication, error handling and data mapping."),
    ("System Integration", "Transferable Skills", "Combine separate applications, data sources or hardware into one working workflow."),
    ("Process Improvement", "Transferable Skills", "Analyze a process, remove waste and redesign it for better speed, cost and quality."),
    ("Workflow Automation", "Transferable Skills", "Automate repetitive steps using scripts, rules, integrations or no-code tools."),
    ("Requirements Gathering", "Transferable Skills", "Elicit, document and confirm business and user requirements with stakeholders."),
    ("Stakeholder Management", "Transferable Skills", "Keep clients, managers and teams aligned through clear communication and expectation setting."),
    ("Project Coordination", "Transferable Skills", "Plan tasks, track progress and coordinate people and resources to deliver on time."),
    ("Risk Assessment", "Transferable Skills", "Identify, rate and mitigate operational, technical and compliance risks."),
    ("Root Cause Analysis", "Transferable Skills", "Investigate incidents systematically to find and fix the underlying cause."),
    ("Technical Documentation", "Transferable Skills", "Write clear manuals, procedures, API references and handover notes."),
    ("Cross-Functional Collaboration", "Transferable Skills", "Work effectively with people from different teams, disciplines and backgrounds."),
    ("Customer Needs Analysis", "Transferable Skills", "Ask the right questions and translate customer pain points into workable solutions."),
    ("Presentation Skills", "Transferable Skills", "Explain ideas clearly to small and large audiences using structured visuals."),
    ("Negotiation", "Transferable Skills", "Reach mutually acceptable agreements on scope, price, deadlines or priorities."),
    ("Training & Facilitation", "Transferable Skills", "Teach, coach and run workshops that build capability in others."),
    ("Change Management", "Transferable Skills", "Help people adopt new tools, processes and ways of working with minimal disruption."),
    ("Resource Planning", "Transferable Skills", "Forecast and allocate people, time, budget and materials to meet demand."),
    ("Budget Tracking", "Transferable Skills", "Monitor spending against a plan and flag variances early."),
    ("Compliance Documentation", "Transferable Skills", "Maintain records and reports that satisfy regulatory and audit requirements."),
    ("Digital Literacy", "Transferable Skills", "Use common productivity, collaboration and cloud tools confidently and safely."),
    ("Cloud Fundamentals", "Transferable Skills", "Understand cloud storage, hosting, identity and shared-responsibility concepts."),
    ("Cybersecurity Awareness", "Transferable Skills", "Recognize phishing, protect credentials and follow safe data-handling practices."),
    ("Database Fundamentals", "Transferable Skills", "Understand tables, relationships, queries and basic data integrity."),
    ("Version Control", "Transferable Skills", "Track changes and collaborate using Git-style repositories and branching."),
    ("Agile Methodology", "Transferable Skills", "Plan and deliver work in short iterations with regular review and feedback."),
    ("Remote Collaboration", "Transferable Skills", "Work productively across time zones using shared documents and online meetings."),
    ("Data Storytelling", "Transferable Skills", "Turn data into clear narratives and visuals that support decisions."),
    ("Quality Assurance", "Transferable Skills", "Check work against standards and catch defects before customers do."),
    ("Vendor Management", "Transferable Skills", "Select, coordinate and evaluate suppliers and service providers."),
    ("Business Process Mapping", "Transferable Skills", "Document how work flows across roles, systems and approvals."),
    # Skills required by the job-posting templates
    ("Blueprint Reading", "TESDA Trade", "Read and interpret technical drawings, symbols and dimensions."),
    ("Food Safety", "Vocational", "Handle, store and prepare food according to hygiene and safety standards."),
    ("Food Preparation", "Vocational", "Prepare ingredients and dishes using standard recipes and portion control."),
    ("Electrical Wiring", "Vocational", "Install and maintain electrical circuits, fixtures and protective devices."),
    ("Automotive Repair", "TESDA Trade", "Diagnose and repair vehicle engines, brakes, electrical and drive systems."),
    ("Record Keeping", "General", "Maintain accurate, retrievable paper and digital records."),
    ("Computer Literacy", "Digital Skills", "Use computers, files, email and office applications for daily work."),
    ("Bookkeeping", "General", "Record financial transactions and maintain accurate ledgers."),
    ("Measurement", "Vocational", "Take accurate dimensions and use measuring tools for trade work."),
]


def seed_skills(apps, schema_editor):
    CentralizedSkill = apps.get_model("tracker", "CentralizedSkill")
    for name, category, description in SKILLS:
        exists = CentralizedSkill.objects.filter(name__iexact=name).exists()
        if not exists:
            CentralizedSkill.objects.create(
                name=name,
                category=category,
                description=description,
            )


def unseed_skills(apps, schema_editor):
    # Keep the rows on rollback: they may already be referenced by profiles/jobs.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0035_profile_id_document_profile_id_number_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_skills, unseed_skills),
    ]
