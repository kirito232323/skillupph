from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0038_seed_react_transferable_skills"),
    ]

    operations = [
        migrations.AddField(
            model_name="referral",
            name="screening_evaluation",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
