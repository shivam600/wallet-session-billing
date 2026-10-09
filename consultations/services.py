"""All session business logic lives here: the state machine and the billing.

Every public function runs in one DB transaction and locks the session row
(and the wallet row when money moves), so concurrent requests are serialised.
"""
import math
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.models import User
from wallet.models import WalletEntry
from wallet.services import debit, get_wallet

from .exceptions import ActiveSessionExists, InsufficientBalanceToStart, InvalidTransition
from .models import ConsultationSession

S = ConsultationSession.Status

# action -> {current state: next state}
# Anything that is not in this table is an invalid transition.
TRANSITIONS = {
    "assign": {S.UNASSIGNED: S.ASSIGNED},
    "accept": {S.ASSIGNED: S.ACCEPTED_BY_PROVIDER},
    "start": {S.ACCEPTED_BY_PROVIDER: S.IN_PROGRESS},
    "end": {S.IN_PROGRESS: S.COMPLETED},
    "bill": {S.IN_PROGRESS: S.IN_PROGRESS},
    "cancel": {
        S.UNASSIGNED: S.CANCELLED,
        S.ASSIGNED: S.CANCELLED,
        S.ACCEPTED_BY_PROVIDER: S.CANCELLED,
    },
}


def current_time():
    # Wrapped in a function so tests can freeze the clock.
    return timezone.now()


# ---------------------------------------------------------------- helpers

def _lock(session_id):
    return ConsultationSession.objects.select_for_update().get(pk=session_id)


def _next_state(session, action):
    target = TRANSITIONS[action].get(session.status)
    if target is None:
        raise InvalidTransition(
            f"Cannot '{action}' a session that is '{session.status}'."
        )
    return target


def _require_user(session, actor):
    if actor.id != session.user_id:
        raise PermissionDenied("Only the user who owns this session can do this.")


def _require_provider(session, actor):
    if session.provider_id is None or actor.id != session.provider_id:
        raise PermissionDenied("Only the assigned provider can do this.")


def _require_participant(session, actor):
    if actor.id not in (session.user_id, session.provider_id):
        raise PermissionDenied("You are not part of this session.")


# ------------------------------------------------------------ state machine

@transaction.atomic
def create_session(user, provider=None):
    session = ConsultationSession.objects.create(
        user=user, per_minute_cost=settings.SESSION_PER_MINUTE_COST
    )
    if provider is not None:
        _assign_locked(session, user, provider)
    return session


def _assign_locked(session, actor, provider):
    _require_user(session, actor)
    target = _next_state(session, "assign")
    if provider.role != User.Role.PROVIDER:
        raise ValidationError({"provider_id": "The selected account is not a provider."})
    session.provider = provider
    session.status = target
    session.save(update_fields=["provider", "status", "updated_at"])
    return session


@transaction.atomic
def assign_session(session_id, actor, provider):
    return _assign_locked(_lock(session_id), actor, provider)


@transaction.atomic
def accept_session(session_id, actor):
    session = _lock(session_id)
    _require_provider(session, actor)
    session.status = _next_state(session, "accept")
    session.save(update_fields=["status", "updated_at"])
    return session


@transaction.atomic
def start_session(session_id, actor, now=None):
    now = now or current_time()
    session = _lock(session_id)
    _require_user(session, actor)
    target = _next_state(session, "start")

    if ConsultationSession.objects.filter(
        user=session.user, status=S.IN_PROGRESS
    ).exists():
        raise ActiveSessionExists()

    # Price is frozen here, for the whole session.
    cost = settings.SESSION_PER_MINUTE_COST
    required = cost * settings.MIN_MINUTES_TO_START
    wallet = get_wallet(session.user, lock=True)
    if wallet.balance < required:
        raise InsufficientBalanceToStart(
            f"You need at least {required} in your wallet to start "
            f"(current balance: {wallet.balance})."
        )

    session.per_minute_cost = cost
    session.started_at = now
    session.status = target
    session.save(update_fields=["per_minute_cost", "started_at", "status", "updated_at"])
    return session


@transaction.atomic
def cancel_session(session_id, actor):
    session = _lock(session_id)
    _require_participant(session, actor)
    session.status = _next_state(session, "cancel")
    session.save(update_fields=["status", "updated_at"])
    return session


@transaction.atomic
def end_session(session_id, actor, now=None):
    """User or provider ends an in-progress session.

    Final billing is done first, so the minute in progress is paid for
    (rounded up) before the session closes.
    """
    now = now or current_time()
    session = _lock(session_id)
    _require_participant(session, actor)
    _next_state(session, "end")  # raises 400 unless in_progress

    _bill_locked(session, now)
    if session.status == S.IN_PROGRESS:  # billing did not already end it
        ended_by = (
            ConsultationSession.ENDED_BY_USER
            if actor.id == session.user_id
            else ConsultationSession.ENDED_BY_PROVIDER
        )
        _complete(session, ended_at=now, ended_by=ended_by)
    return session


# ----------------------------------------------------------------- billing

def _complete(session, ended_at, ended_by):
    session.status = S.COMPLETED
    session.ended_at = ended_at
    session.ended_by = ended_by
    session.talk_seconds = max(int((ended_at - session.started_at).total_seconds()), 0)
    session.save()


def _bill_locked(session, now):
    """Charge every minute that has started but isn't paid for yet.

    Minutes are billed in advance and rounded up: second 1 of minute N already
    owes minute N. `billed_minutes` is the high-water mark, so running this
    twice for the same moment charges nothing the second time.

    If the wallet can't cover all due minutes, we charge what it can cover and
    end the session on the system's behalf. Returns the amount charged now.
    """
    elapsed = max((now - session.started_at).total_seconds(), 0)
    minutes_due = math.ceil(elapsed / 60)
    unbilled = minutes_due - session.billed_minutes
    if unbilled <= 0:
        return Decimal("0")

    wallet = get_wallet(session.user, lock=True)
    affordable = int(wallet.balance // session.per_minute_cost)
    to_charge = min(unbilled, affordable)

    charged = session.per_minute_cost * to_charge
    if to_charge:
        debit(wallet, charged, WalletEntry.Reason.SESSION_BILLING, f"session:{session.pk}")
        session.billed_minutes += to_charge
        session.billed_amount += charged

    if to_charge < unbilled:
        # Talk time ends where the paid time ends (never later than now).
        paid_until = session.started_at + timedelta(minutes=session.billed_minutes)
        _complete(
            session,
            ended_at=min(paid_until, now),
            ended_by=ConsultationSession.ENDED_BY_SYSTEM_INSUFFICIENT,
        )
    else:
        session.save(update_fields=["billed_minutes", "billed_amount", "updated_at"])
    return charged


@transaction.atomic
def run_billing(session_id, now=None):
    """Billing entry point with no actor, used by the cron/management command."""
    now = now or current_time()
    session = _lock(session_id)
    _next_state(session, "bill")
    _bill_locked(session, now)
    return session


@transaction.atomic
def bill_session(session_id, actor, now=None):
    """Billing entry point for the API (participants only)."""
    session = _lock(session_id)
    _require_participant(session, actor)
    return run_billing(session_id, now=now)
