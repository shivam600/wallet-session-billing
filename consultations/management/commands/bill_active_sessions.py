from django.core.management.base import BaseCommand

from consultations import services
from consultations.models import ConsultationSession


class Command(BaseCommand):
    help = (
        "Bill every in-progress session once. Run it every minute (cron, "
        "systemd timer, ...). It is safe to run more often: a minute is "
        "never charged twice."
    )

    def handle(self, *args, **options):
        ids = ConsultationSession.objects.filter(
            status=ConsultationSession.Status.IN_PROGRESS
        ).values_list("id", flat=True)
        for session_id in ids:
            session = services.run_billing(session_id)
            self.stdout.write(
                f"session {session.id}: {session.status}, billed_minutes={session.billed_minutes}"
            )
        self.stdout.write(self.style.SUCCESS(f"Checked {len(ids)} active session(s)."))
