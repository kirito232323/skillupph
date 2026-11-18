from django.core.management.base import BaseCommand, CommandError

from tracker.skill_embeddings import build_embeddings


class Command(BaseCommand):
    help = "Build Gemini embeddings for all centralized skills."

    def handle(self, *args, **options):
        try:
            result = build_embeddings()
        except Exception as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Built {result['count']} skill embeddings with {result['model']}."
        ))
