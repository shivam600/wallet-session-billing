from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import (
    RechargeCallbackSerializer,
    RechargeInitiateSerializer,
    RechargeTransactionSerializer,
    WalletEntrySerializer,
    WalletSerializer,
)


class WalletView(APIView):
    def get(self, request):
        wallet = services.get_wallet(request.user)
        return Response(WalletSerializer(wallet).data)


class WalletEntryListView(generics.ListAPIView):
    """The user's ledger, newest first."""

    serializer_class = WalletEntrySerializer

    def get_queryset(self):
        wallet = services.get_wallet(self.request.user)
        return wallet.entries.all()


class RechargeInitiateView(APIView):
    def post(self, request):
        serializer = RechargeInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        txn = services.initiate_recharge(request.user, serializer.validated_data["amount"])
        return Response(RechargeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)


class RechargeCallbackView(APIView):
    """Mock payment-gateway webhook.

    A real gateway would call this without our auth token (it would sign the
    payload instead), so authentication is turned off here.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RechargeCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        txn, credited = services.process_recharge_callback(
            serializer.validated_data["reference"], serializer.validated_data["status"]
        )
        data = RechargeTransactionSerializer(txn).data
        data["credited"] = credited
        return Response(data, status=status.HTTP_200_OK)
