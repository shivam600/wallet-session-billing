import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .exceptions import InsufficientBalance, RechargeConflict
from .models import RechargeTransaction, Wallet


def get_wallet(user, lock=False):
    """Return the user's wallet (created on first use).

    With lock=True the row is locked (SELECT ... FOR UPDATE) so two requests
    can't read the same balance and both spend it. Must be used inside
    transaction.atomic().
    """
    wallet, _ = Wallet.objects.get_or_create(user=user)
    if lock:
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    return wallet


def credit(wallet, amount):
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Credit amount must be positive.")
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])


def debit(wallet, amount):
    """Deduct from a wallet that the caller has already locked."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Debit amount must be positive.")
    if wallet.balance < amount:
        raise InsufficientBalance(f"Balance {wallet.balance} is less than {amount}.")
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])


def initiate_recharge(user, amount):
    return RechargeTransaction.objects.create(
        user=user,
        amount=amount,
        reference=f"RCH-{uuid.uuid4().hex[:16].upper()}",
    )


@transaction.atomic
def process_recharge_callback(reference, new_status):
    """Handle the mock gateway callback.

    Returns (transaction, credited). The wallet is credited only on the first
    successful callback; repeats are acknowledged but change nothing.
    """
    if new_status not in (RechargeTransaction.Status.SUCCESS, RechargeTransaction.Status.FAILED):
        raise ValidationError({"status": "Must be 'success' or 'failed'."})

    try:
        # Row lock => two simultaneous callbacks are processed one after the other.
        txn = RechargeTransaction.objects.select_for_update().get(reference=reference)
    except RechargeTransaction.DoesNotExist:
        raise NotFound("Unknown recharge reference.")

    if txn.status != RechargeTransaction.Status.PENDING:
        if txn.status == new_status:
            return txn, False  # duplicate delivery, nothing to do
        raise RechargeConflict()

    txn.status = new_status
    txn.completed_at = timezone.now()
    txn.save(update_fields=["status", "completed_at"])

    if new_status == RechargeTransaction.Status.SUCCESS:
        credit(get_wallet(txn.user, lock=True), txn.amount)
        return txn, True
    return txn, False
