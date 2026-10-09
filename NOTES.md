# Implementation notes

Short notes on the decisions that matter most for this assignment.

## 1. Business logic lives in `services.py`, views stay thin
Views only check roles, parse input and serialise output. The state machine
and billing are plain functions in `consultations/services.py` and
`wallet/services.py`, which makes them easy to test and reason about.

## 2. State machine = one lookup table
`TRANSITIONS` maps `action -> {current_state: next_state}`. If the current
state is not in the table for that action, an `InvalidTransition` (HTTP 400)
is raised with a message that names the action and the current state. There
are no scattered `if status == ...` checks. I added a `cancelled` state
(reachable before the session starts) as the optional cancellation path.

## 3. Wallet can never go negative (three layers)
1. `debit()` raises `InsufficientBalance` before changing anything.
2. The wallet row is locked (`select_for_update`) while money moves, so two
   concurrent requests can't spend the same balance.
3. A DB `CheckConstraint` (`balance >= 0`) as a final safety net.

## 3b. Wallet ledger
Every credit and debit also writes an append-only `WalletEntry` (type, amount,
`balance_after`, reason, reference) in the same transaction as the balance
change, so the balance can always be explained and re-computed: credits minus
debits equals the wallet balance (checked in a test). A partial unique
constraint allows only one `recharge` entry per recharge reference, a
second database-level guard for idempotency. Exposed at `GET /api/wallet/entries/`.

## 4. Idempotent recharge callback
The callback locks the `RechargeTransaction` row and only credits when the
status is still `pending`. A repeated `success` callback returns 200 with
`credited: false` and changes nothing. A conflicting callback (`failed`
after `success`, or the reverse) returns 409. The unique `reference` column
guarantees one transaction per reference.

## 5. Billing model
- **Minutes are billed in advance and rounded up.** The moment a new minute
  begins (second 1 of minute N) minute N is owed. So 3 minutes of talk = 3 x
  per-minute cost, and ending at 2m10s costs 3 minutes. The assignment
  example (3 min x Rs 50 = Rs 150) holds exactly.
- **No double charging:** `billed_minutes` is a high-water mark stored on the
  session. Each run charges only `minutes_due - billed_minutes`, so running
  billing twice (API, cron, or both) for the same moment charges nothing the
  second time. The session row is locked during billing.
- **Late billing is safe:** if billing wasn't triggered for a while, one run
  charges all missed minutes the wallet can afford, then ends the session if
  it can't afford the rest.
- **Insufficient balance:** the affordable minutes are charged, the wallet is
  left untouched for the minute it can't pay (e.g. Rs 40 stays Rs 40), the
  session becomes `completed` with
  `ended_by = "system : Insufficient Balance"`. `talk_seconds` is capped at
  the paid time (`billed_minutes x 60`), because that is the time the user
  actually paid for.
- If a user or provider ends a session and the final started minute can't be
  paid, it is recorded as a system end for the same reason.
- `per_minute_cost` is copied from settings when the session starts and
  never modified afterwards (tested by changing the setting mid-session).

## 6. One active session per user
Checked in `start_session` (clear 400 error) and enforced by a partial unique
constraint on `(user)` where `status = 'in_progress'`, so a race can't slip
past the check.

## 7. Time is injectable
`services.current_time()` is the only place the clock is read. Tests patch it,
so billing tests are deterministic and fast with no `sleep()`.

## 8. Periodic billing without background workers
As the assignment allows, billing is synchronous: a `bill` endpoint and a
`bill_active_sessions` management command meant to be run by cron every minute.

## 9. Auth and roles
DRF token authentication. `User.role` is `USER` or `PROVIDER`. Creating,
assigning and starting sessions is for users; accepting is for the assigned
provider; either participant can bill or end. Non-participants get 404, wrong
role or wrong participant gets 403.

## Known simplifications
- The recharge callback is unauthenticated (like a real gateway webhook) and
  doesn't verify a signature, since the gateway is mocked. In production it
  would verify an HMAC signature.
- Billing granularity is per minute only; no refunds or provider payouts.
- Amounts are in a single currency (INR), as per the out-of-scope list.
