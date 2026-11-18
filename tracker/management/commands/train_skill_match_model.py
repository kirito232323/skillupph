from django.core.management.base import BaseCommand

from tracker.skill_ml import model_status, train_model


class Command(BaseCommand):
    help = "Train the skill-matching ML model from historical referral outcomes."

    def handle(self, *args, **options):
        result = train_model()
        if not result.get("trained"):
            self.stdout.write(self.style.WARNING(result.get("reason", "Model not trained.")))
            return
        self.stdout.write(self.style.SUCCESS(
            "Skill-match model trained: "
            f"transferable weight={result['transferable_weight']}, "
            f"samples={result['samples']}, accuracy={result['accuracy']}."
        ))
        status = model_status()
        if not status['active']:
            self.stdout.write(self.style.WARNING(
                "Model is saved but not active: " + status['reason']
            ))
