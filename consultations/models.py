from django.conf import settings
from django.db import models


class ConsultationSession(models.Model):
    class Status(models.TextChoices):
        UNASSIGNED = "unassigned", "Unassigned"
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED_BY_PROVIDER = "accepted_by_provider", "Accepted by provider"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    ENDED_BY_USER = "user"
    ENDED_BY_PROVIDER = "provider"
    ENDED_BY_SYSTEM_INSUFFICIENT = "system : Insufficient Balance"
    ENDED_BY_CHOICES = [
        (ENDED_BY_USER, "User"),
        (ENDED_BY_PROVIDER, "Provider"),
        (ENDED_BY_SYSTEM_INSUFFICIENT, "System : Insufficient Balance"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions_as_user"
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions_as_provider",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UNASSIGNED)

    # Price per minute. Re-read from settings at start, never touched afterwards.
    per_minute_cost = models.DecimalField(max_digits=10, decimal_places=2)

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Billing bookkeeping: how much of the session has already been paid for.
    billed_minutes = models.PositiveIntegerField(default=0)
    billed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    talk_seconds = models.PositiveIntegerField(default=0)

    ended_by = models.CharField(max_length=40, choices=ENDED_BY_CHOICES, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # DB-level guarantee: one in_progress session per user.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="in_progress"),
                name="one_active_session_per_user",
            ),
        ]

    def __str__(self):
        return f"Session #{self.pk} [{self.status}]"
