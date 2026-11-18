from pathlib import Path

from django.conf import settings
from django.db import migrations, models

import tracker.id_storage


def encrypt_existing_ids(apps, schema_editor):
    from tracker.id_crypto import encrypt_bytes, is_encrypted

    Profile = apps.get_model("tracker", "Profile")
    for profile in Profile.objects.exclude(id_document="").exclude(id_document__isnull=True):
        path = Path(settings.MEDIA_ROOT) / profile.id_document.name
        if not path.exists():
            continue
        raw = path.read_bytes()
        if not is_encrypted(raw):
            path.write_bytes(encrypt_bytes(raw))

    # Encrypt abandoned signup uploads too; they may contain an ID image.
    temp_dir = Path(settings.MEDIA_ROOT) / "temp_ids"
    if temp_dir.exists():
        for path in temp_dir.iterdir():
            if path.is_file():
                raw = path.read_bytes()
                if not is_encrypted(raw):
                    path.write_bytes(encrypt_bytes(raw))


def reverse(apps, schema_editor):
    # Decryption on rollback would weaken the at-rest guarantee; leave files encrypted.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0041_remove_proficiency_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="id_document",
            field=models.FileField(
                blank=True,
                null=True,
                storage=tracker.id_storage.EncryptedIDStorage(),
                upload_to="id_documents/",
            ),
        ),
        migrations.RunPython(encrypt_existing_ids, reverse),
    ]
