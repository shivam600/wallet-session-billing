from rest_framework import status
from rest_framework.exceptions import APIException


class InsufficientBalance(Exception):
    """Raised when a debit would push the wallet below zero."""


class RechargeConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This recharge was already completed with a different status."
    default_code = "recharge_conflict"
