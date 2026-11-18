from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0042_encrypt_id_documents"),
    ]

    operations = [
        migrations.AddField(
            model_name="certification",
            name="certificate_file",
            field=models.FileField(blank=True, null=True, upload_to="certificates/"),
        ),
    ]
