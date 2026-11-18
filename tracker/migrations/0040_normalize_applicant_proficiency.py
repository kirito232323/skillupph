from django.db import migrations


def normalize_proficiency(apps, schema_editor):
    ApplicantSkill = apps.get_model("tracker", "ApplicantSkill")
    # AI resume scanning previously wrote 85 (a percentage) instead of a 1-5 level.
    ApplicantSkill.objects.filter(proficiency__gt=5, source="AI Resume Scan").update(proficiency=3)
    # Safety net for any other out-of-range values.
    ApplicantSkill.objects.filter(proficiency__gt=5).update(proficiency=5)
    ApplicantSkill.objects.filter(proficiency__lt=1).update(proficiency=1)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0039_referral_screening_evaluation"),
    ]

    operations = [
        migrations.RunPython(normalize_proficiency, backwards),
    ]
