from django.db import migrations

# Skills referenced by the React Developer Candidate Evaluation Guide that
# were missing from the central taxonomy. Existing names are skipped.
SKILLS = [
    ("DOM Manipulation", "Frontend Dev", "Select, update and respond to browser DOM elements and events."),
    ("JSON", "Backend Dev", "Read, write and exchange structured data using JSON."),
    ("GitHub", "DevOps", "Host repositories, review pull requests and collaborate on GitHub."),
    ("GitLab", "DevOps", "Manage repositories, merge requests and CI pipelines on GitLab."),
    ("Vite", "Frontend Dev", "Fast frontend build tooling and dev server for modern JavaScript apps."),
    ("npm", "Frontend Dev", "Manage JavaScript packages, scripts and project dependencies with npm."),
    ("Vitest", "QA", "Unit and component testing for modern JavaScript and Vue/React projects."),
    ("Cypress", "QA", "End-to-end and component testing for web applications."),
    ("JWT", "Backend Dev", "Implement token-based authentication and authorization with JSON Web Tokens."),
    ("OAuth", "Backend Dev", "Integrate third-party authorization flows using OAuth 2.0."),
    ("MySQL", "Backend Dev", "Design, query and maintain relational databases in MySQL."),
    ("Flask", "Backend Dev", "Build lightweight Python web services and APIs with Flask."),
    ("Object-Oriented Programming", "Transferable Skills", "Model systems with classes, objects, encapsulation and inheritance."),
    ("Functional Programming", "Transferable Skills", "Use pure functions, immutability and composable transformations."),
]


def seed(apps, schema_editor):
    CentralizedSkill = apps.get_model("tracker", "CentralizedSkill")
    for name, category, description in SKILLS:
        if not CentralizedSkill.objects.filter(name__iexact=name).exists():
            CentralizedSkill.objects.create(name=name, category=category, description=description)


def unseed(apps, schema_editor):
    # Keep the skills on rollback: profiles and vacancies may already reference them.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0037_capitalize_names"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
