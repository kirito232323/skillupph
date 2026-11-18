from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0040_normalize_applicant_proficiency"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="applicantskill",
            name="proficiency",
        ),
        migrations.RemoveField(
            model_name="jobskillrequirement",
            name="required_proficiency",
        ),
        migrations.RemoveField(
            model_name="skill",
            name="proficiency_level",
        ),
        migrations.RemoveField(
            model_name="profile",
            name="skill_level",
        ),
    ]
