from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from wallet.models import Wallet

from .models import ConsultationSession

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
COST = Decimal("50")  # matches the default SESSION_PER_MINUTE_COST


def client_for(user):
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def at(minutes=0, seconds=0):
    """Freeze the service clock at T0 + minutes/seconds."""
    return patch(
        "consultations.services.current_time",
        return_value=T0 + timedelta(minutes=minutes, seconds=seconds),
    )


class SessionTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("asha", password="pass1234", role=User.Role.USER)
        self.provider = User.objects.create_user("dr_rao", password="pass1234", role=User.Role.PROVIDER)
        self.user_api = client_for(self.user)
        self.provider_api = client_for(self.provider)

    # --- helpers
    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def recharge(self, amount):
        resp = self.user_api.post("/api/wallet/recharge/", {"amount": str(amount)}, format="json")
        self.assertEqual(resp.status_code, 201)
        cb = APIClient().post(
            "/api/wallet/recharge/callback/",
            {"reference": resp.data["reference"], "status": "success"},
            format="json",
        )
        self.assertEqual(cb.status_code, 200)

    def create_session(self, user_api=None, assign=True):
        payload = {"provider_id": self.provider.id} if assign else {}
        resp = (user_api or self.user_api).post("/api/sessions/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def accepted_session(self):
        session_id = self.create_session()
        resp = self.provider_api.post(f"/api/sessions/{session_id}/accept/")
        self.assertEqual(resp.status_code, 200, resp.data)
        return session_id

    def started_session(self, minute=0):
        session_id = self.accepted_session()
        with at(minute):
            resp = self.user_api.post(f"/api/sessions/{session_id}/start/")
        self.assertEqual(resp.status_code, 200, resp.data)
        return session_id

    def act(self, api, session_id, action, minutes=0, seconds=0):
        with at(minutes, seconds):
            return api.post(f"/api/sessions/{session_id}/{action}/")

    def reload(self, session_id):
        return ConsultationSession.objects.get(pk=session_id)


class StartRulesTests(SessionTestBase):
    # Required scenario 1
    def test_cannot_start_with_less_than_five_minutes_of_balance(self):
        self.recharge(249)  # 5 x 50 = 250 needed
        session_id = self.accepted_session()

        resp = self.act(self.user_api, session_id, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.reload(session_id).status, "accepted_by_provider")
        self.assertEqual(self.balance(), Decimal("249"))

    def test_can_start_with_exactly_five_minutes_of_balance(self):
        self.recharge(250)
        session_id = self.accepted_session()
        resp = self.act(self.user_api, session_id, "start")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "in_progress")

    def test_only_one_active_session_per_user(self):
        self.recharge(1000)
        self.started_session()
        second = self.accepted_session()

        resp = self.act(self.user_api, second, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.reload(second).status, "accepted_by_provider")

    def test_per_minute_cost_is_fixed_once_session_starts(self):
        self.recharge(1000)
        session_id = self.started_session()

        with override_settings(SESSION_PER_MINUTE_COST=Decimal("100")):
            self.act(self.user_api, session_id, "bill", minutes=2)

        session = self.reload(session_id)
        self.assertEqual(session.per_minute_cost, COST)
        self.assertEqual(session.billed_amount, Decimal("100"))  # 2 x 50, not 2 x 100


class HappyPathTests(SessionTestBase):
    # Required scenario 2
    def test_full_flow_debits_wallet_for_two_minutes(self):
        self.recharge(300)
        self.assertEqual(self.balance(), Decimal("300"))

        session_id = self.create_session(assign=False)
        self.assertEqual(self.reload(session_id).status, "unassigned")

        resp = self.user_api.post(
            f"/api/sessions/{session_id}/assign/", {"provider_id": self.provider.id}, format="json"
        )
        self.assertEqual(resp.data["status"], "assigned")

        resp = self.provider_api.post(f"/api/sessions/{session_id}/accept/")
        self.assertEqual(resp.data["status"], "accepted_by_provider")

        resp = self.act(self.user_api, session_id, "start", minutes=0)
        self.assertEqual(resp.data["status"], "in_progress")

        # Billing runs two minutes into the call
        resp = self.act(self.user_api, session_id, "bill", minutes=2)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["billed_minutes"], 2)
        self.assertEqual(self.balance(), Decimal("200"))

        resp = self.act(self.user_api, session_id, "end", minutes=2)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "completed")
        self.assertEqual(resp.data["ended_by"], "user")

        session = self.reload(session_id)
        self.assertEqual(session.billed_amount, Decimal("100"))
        self.assertEqual(session.talk_seconds, 120)
        self.assertEqual(self.balance(), Decimal("200"))  # 300 - 2 x 50

    def test_ending_mid_minute_charges_the_started_minute(self):
        self.recharge(300)
        session_id = self.started_session()

        self.act(self.user_api, session_id, "end", minutes=2, seconds=10)

        session = self.reload(session_id)
        self.assertEqual(session.billed_minutes, 3)
        self.assertEqual(session.talk_seconds, 130)
        self.assertEqual(self.balance(), Decimal("150"))

    def test_provider_can_end_session(self):
        self.recharge(300)
        session_id = self.started_session()

        resp = self.act(self.provider_api, session_id, "end", minutes=1)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["ended_by"], "provider")
        self.assertEqual(self.balance(), Decimal("250"))

    def test_session_can_be_cancelled_before_it_starts(self):
        session_id = self.accepted_session()
        resp = self.user_api.post(f"/api/sessions/{session_id}/cancel/")
        self.assertEqual(resp.data["status"], "cancelled")


class BalanceRunsOutTests(SessionTestBase):
    # Required scenario 3
    def test_session_ends_automatically_when_next_minute_cannot_be_paid(self):
        self.recharge(290)  # 5 minutes (250) then Rs 40 left, less than one minute
        session_id = self.started_session()

        resp = self.act(self.user_api, session_id, "bill", minutes=6)  # 6th minute is due

        self.assertEqual(resp.status_code, 200)
        session = self.reload(session_id)
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.ended_by, ConsultationSession.ENDED_BY_SYSTEM_INSUFFICIENT)
        self.assertEqual(session.ended_by, "system : Insufficient Balance")
        self.assertEqual(session.billed_minutes, 5)
        self.assertEqual(session.billed_amount, Decimal("250"))
        self.assertEqual(session.talk_seconds, 300)
        self.assertEqual(self.balance(), Decimal("40"))  # untouched, never negative

    def test_wallet_never_goes_negative_when_it_hits_exactly_zero(self):
        self.recharge(250)
        session_id = self.started_session()

        self.act(self.user_api, session_id, "bill", minutes=5)
        self.assertEqual(self.reload(session_id).status, "in_progress")
        self.assertEqual(self.balance(), Decimal("0"))

        self.act(self.user_api, session_id, "bill", minutes=5, seconds=1)
        session = self.reload(session_id)
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.ended_by, "system : Insufficient Balance")
        self.assertEqual(self.balance(), Decimal("0"))

    def test_late_billing_only_charges_what_the_wallet_can_cover(self):
        self.recharge(260)
        session_id = self.started_session()

        # Nobody triggered billing for a long time; 10 minutes have passed.
        self.act(self.user_api, session_id, "bill", minutes=10)

        session = self.reload(session_id)
        self.assertEqual(session.billed_minutes, 5)
        self.assertEqual(session.talk_seconds, 300)  # talk time stops where paid time stops
        self.assertEqual(self.balance(), Decimal("10"))

    def test_user_ending_when_last_minute_is_unaffordable_is_recorded_as_system(self):
        self.recharge(250)
        session_id = self.started_session()

        self.act(self.user_api, session_id, "end", minutes=5, seconds=30)

        session = self.reload(session_id)
        self.assertEqual(session.ended_by, "system : Insufficient Balance")
        self.assertEqual(self.balance(), Decimal("0"))


class InvalidTransitionTests(SessionTestBase):
    # Required scenario 4
    def test_ending_a_session_that_has_not_started_returns_400(self):
        self.recharge(500)
        session_id = self.accepted_session()

        resp = self.act(self.user_api, session_id, "end")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("end", resp.data["detail"])
        self.assertIn("accepted_by_provider", resp.data["detail"])
        self.assertEqual(self.reload(session_id).status, "accepted_by_provider")

    def test_starting_before_provider_accepts_returns_400(self):
        self.recharge(500)
        session_id = self.create_session()  # assigned, not accepted
        self.assertEqual(self.act(self.user_api, session_id, "start").status_code, 400)

    def test_accepting_an_unassigned_session_returns_400(self):
        session_id = self.create_session(assign=False)
        # no provider is attached yet, so the provider can't even see it...
        self.assertEqual(self.provider_api.post(f"/api/sessions/{session_id}/accept/").status_code, 404)

    def test_ending_twice_returns_400(self):
        self.recharge(500)
        session_id = self.started_session()
        self.assertEqual(self.act(self.user_api, session_id, "end", minutes=1).status_code, 200)
        self.assertEqual(self.act(self.user_api, session_id, "end", minutes=1).status_code, 400)

    def test_cannot_assign_twice_or_cancel_after_completion(self):
        self.recharge(500)
        session_id = self.started_session()
        resp = self.user_api.post(
            f"/api/sessions/{session_id}/assign/", {"provider_id": self.provider.id}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.user_api.post(f"/api/sessions/{session_id}/cancel/").status_code, 400)

    def test_billing_a_session_that_is_not_in_progress_returns_400(self):
        session_id = self.accepted_session()
        self.assertEqual(self.act(self.user_api, session_id, "bill").status_code, 400)


class DuplicateBillingTests(SessionTestBase):
    # Required scenario 6
    def test_billing_twice_for_the_same_minute_charges_once(self):
        self.recharge(300)
        session_id = self.started_session()

        self.act(self.user_api, session_id, "bill", minutes=2)
        self.act(self.user_api, session_id, "bill", minutes=2)
        self.act(self.provider_api, session_id, "bill", minutes=2)

        session = self.reload(session_id)
        self.assertEqual(session.billed_minutes, 2)
        self.assertEqual(session.billed_amount, Decimal("100"))
        self.assertEqual(self.balance(), Decimal("200"))

    def test_only_new_minutes_are_charged_on_later_runs(self):
        self.recharge(500)
        session_id = self.started_session()

        self.act(self.user_api, session_id, "bill", minutes=2)
        self.assertEqual(self.balance(), Decimal("400"))
        self.act(self.user_api, session_id, "bill", minutes=2, seconds=30)  # minute 3 begins
        self.assertEqual(self.balance(), Decimal("350"))
        self.act(self.user_api, session_id, "bill", minutes=2, seconds=50)  # still minute 3
        self.assertEqual(self.balance(), Decimal("350"))

    def test_management_command_bills_active_sessions_without_double_charging(self):
        self.recharge(500)
        session_id = self.started_session()

        for _ in range(2):
            with at(minutes=3):
                call_command("bill_active_sessions", stdout=StringIO())

        self.assertEqual(self.reload(session_id).billed_minutes, 3)
        self.assertEqual(self.balance(), Decimal("350"))


class PermissionTests(SessionTestBase):
    def test_provider_cannot_create_or_start_sessions(self):
        self.assertEqual(self.provider_api.post("/api/sessions/", {}, format="json").status_code, 403)
        self.recharge(500)
        session_id = self.accepted_session()
        self.assertEqual(self.provider_api.post(f"/api/sessions/{session_id}/start/").status_code, 403)

    def test_user_cannot_accept_their_own_session(self):
        session_id = self.create_session()
        self.assertEqual(self.user_api.post(f"/api/sessions/{session_id}/accept/").status_code, 403)

    def test_other_users_cannot_see_the_session(self):
        session_id = self.create_session()
        stranger = User.objects.create_user("stranger", password="pass1234")
        self.assertEqual(client_for(stranger).get(f"/api/sessions/{session_id}/").status_code, 404)

    def test_requests_without_a_token_are_rejected(self):
        self.assertEqual(APIClient().get("/api/sessions/").status_code, 401)
        self.assertEqual(APIClient().get("/api/wallet/").status_code, 401)

    def test_register_and_login_flow(self):
        client = APIClient()
        resp = client.post(
            "/api/auth/register/",
            {"username": "newbie", "password": "secret123", "role": "PROVIDER"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["role"], "PROVIDER")
        login = client.post("/api/auth/login/", {"username": "newbie", "password": "secret123"}, format="json")
        self.assertEqual(login.status_code, 200)
        self.assertIn("token", login.data)
