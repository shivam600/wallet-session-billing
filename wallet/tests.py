from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from accounts.models import User

from .exceptions import InsufficientBalance
from .models import RechargeTransaction, Wallet
from .services import debit, get_wallet


class WalletTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("asha", password="pass1234", role=User.Role.USER)
        token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def initiate(self, amount="500"):
        resp = self.client.post("/api/wallet/recharge/", {"amount": amount}, format="json")
        self.assertEqual(resp.status_code, 201)
        return resp.data["reference"]

    def callback(self, reference, status="success"):
        # The webhook is called by the "gateway", so no auth header here.
        return APIClient().post(
            "/api/wallet/recharge/callback/",
            {"reference": reference, "status": status},
            format="json",
        )

    def test_new_wallet_starts_at_zero(self):
        resp = self.client.get("/api/wallet/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data["balance"]), 0)

    def test_initiated_recharge_is_pending_and_does_not_credit(self):
        reference = self.initiate("500")
        txn = RechargeTransaction.objects.get(reference=reference)
        self.assertEqual(txn.status, "pending")
        self.assertEqual(self.balance(), 0)

    def test_successful_callback_credits_wallet(self):
        reference = self.initiate("500")
        resp = self.callback(reference)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "success")
        self.assertEqual(self.balance(), Decimal("500"))

    # Required scenario 5
    def test_duplicate_success_callback_credits_only_once(self):
        reference = self.initiate("500")

        first = self.callback(reference)
        second = self.callback(reference)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.data["credited"])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["credited"])
        self.assertEqual(self.balance(), Decimal("500"))

    def test_failed_callback_does_not_credit(self):
        reference = self.initiate("500")
        resp = self.callback(reference, "failed")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RechargeTransaction.objects.get(reference=reference).status, "failed")
        self.assertEqual(self.balance(), 0)

    def test_success_after_failed_is_rejected(self):
        reference = self.initiate("500")
        self.callback(reference, "failed")
        resp = self.callback(reference, "success")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.balance(), 0)

    def test_unknown_reference_returns_404(self):
        self.assertEqual(self.callback("RCH-DOESNOTEXIST").status_code, 404)

    def test_recharge_amount_must_be_positive(self):
        for bad in ("0", "-100"):
            resp = self.client.post("/api/wallet/recharge/", {"amount": bad}, format="json")
            self.assertEqual(resp.status_code, 400)

    def test_debit_cannot_make_balance_negative(self):
        wallet = get_wallet(self.user)
        wallet.balance = Decimal("40")
        wallet.save()
        with self.assertRaises(InsufficientBalance):
            with transaction.atomic():
                debit(get_wallet(self.user, lock=True), Decimal("50"))
        self.assertEqual(self.balance(), Decimal("40"))

    def test_database_rejects_negative_balance(self):
        wallet = get_wallet(self.user)
        wallet.balance = Decimal("-1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                wallet.save()
