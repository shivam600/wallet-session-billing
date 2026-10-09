from rest_framework import status
from rest_framework.exceptions import APIException


class InvalidTransition(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This action is not allowed in the session's current state."
    default_code = "invalid_transition"


class ActiveSessionExists(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "You already have a session in progress."
    default_code = "active_session_exists"


class InsufficientBalanceToStart(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Balance is too low to start a session."
    default_code = "insufficient_balance"
