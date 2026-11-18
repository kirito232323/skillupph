import re

from django.db import migrations


def capitalize_name(value):
    value = (value or "").strip()
    if not value:
        return ""
    return re.sub(r"(^|[\s\-'])(\w)", lambda m: m.group(1) + m.group(2).upper(), value)


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    for user in User.objects.all().iterator():
        first = capitalize_name(user.first_name)
        last = capitalize_name(user.last_name)
        if first != user.first_name or last != user.last_name:
            user.first_name = first
            user.last_name = last
            user.save(update_fields=["first_name", "last_name"])


def backwards(apps, schema_editor):
    # Names cannot be reliably restored to their original casing.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0036_seed_transferable_skills"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
