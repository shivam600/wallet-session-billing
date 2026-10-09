# Wallet-Based Per-Minute Session Billing

A small Django + DRF backend for paid 1:1 consultation sessions. Users keep a
prepaid wallet, sessions move through a strict state machine, and the user is
billed per minute from the wallet. If the wallet can't cover the next minute,
the system ends the session automatically.

**Stack:** Python 3.10+, Django 5.x, Django REST Framework, token auth.
SQLite by default, PostgreSQL optional.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### Using PostgreSQL (optional)

```bash
pip install psycopg2-binary
export DB_ENGINE=postgres DB_NAME=wallet_billing DB_USER=postgres DB_PASSWORD=secret DB_HOST=localhost
python manage.py migrate
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SESSION_PER_MINUTE_COST` | `50` | Price per minute (INR) |
| `DJANGO_SECRET_KEY` | dev key | Set this outside local development |
| `DJANGO_DEBUG` | `1` | Set `0` in production |

The minimum balance to start a session is `5 x SESSION_PER_MINUTE_COST`.

## Running the tests

```bash
python manage.py test
```

41 tests, all passing. They cover the six required scenarios (each is marked
with a `# Required scenario N` comment) plus permissions, rounding and
edge cases. Time is frozen in tests, so there are no `sleep()` calls.

| # | Required scenario | Where |
|---|---|---|
| 1 | Insufficient balance | `consultations/tests.py::StartRulesTests` |
| 2 | Happy path | `consultations/tests.py::HappyPathTests` |
| 3 | Balance runs out | `consultations/tests.py::BalanceRunsOutTests` |
| 4 | Invalid state transition | `consultations/tests.py::InvalidTransitionTests` |
| 5 | Duplicate recharge callback | `wallet/tests.py::test_duplicate_success_callback_credits_only_once` |
| 6 | Duplicate billing | `consultations/tests.py::DuplicateBillingTests` |

## API

All endpoints except register, login and the recharge callback need the header
`Authorization: Token <token>`.

| Method | URL | Who | Purpose |
|---|---|---|---|
| POST | `/api/auth/register/` | anyone | `{username, password, role: USER or PROVIDER}`, returns a token |
| POST | `/api/auth/login/` | anyone | `{username, password}`, returns a token |
| GET | `/api/wallet/` | any | Current balance |
| GET | `/api/wallet/entries/` | any | Ledger: every credit and debit, newest first |
| POST | `/api/wallet/recharge/` | any | `{amount}`, creates a **pending** recharge and returns its `reference` |
| POST | `/api/wallet/recharge/callback/` | mock gateway | `{reference, status: success or failed}` |
| POST | `/api/sessions/` | USER | Create a session. Optional `{provider_id}` assigns it right away |
| GET | `/api/sessions/` , `/api/sessions/<id>/` | participant | List or view own sessions |
| POST | `/api/sessions/<id>/assign/` | session's USER | `{provider_id}` |
| POST | `/api/sessions/<id>/accept/` | assigned PROVIDER | Provider accepts |
| POST | `/api/sessions/<id>/start/` | session's USER | Start (balance and one-active-session checks) |
| POST | `/api/sessions/<id>/bill/` | participant | Bill every minute due so far |
| POST | `/api/sessions/<id>/end/` | USER or PROVIDER | End the session (bills first) |
| POST | `/api/sessions/<id>/cancel/` | participant | Cancel before it starts |

Invalid transitions return **400** with a message, for example:

```json
{"detail": "Cannot 'end' a session that is 'accepted_by_provider'."}
```

### Periodic billing

Billing can be triggered through the `bill` endpoint or, for all active
sessions at once, with a management command. Schedule it every minute:

```bash
python manage.py bill_active_sessions
# crontab:  * * * * * cd /path/to/project && venv/bin/python manage.py bill_active_sessions
```

Running it more often than once a minute is harmless; a minute is never charged twice.

### Quick walkthrough (curl)

```bash
# 1. register a user and a provider (note the tokens)
curl -X POST localhost:8000/api/auth/register/ -H 'Content-Type: application/json' \
     -d '{"username":"asha","password":"secret123","role":"USER"}'
curl -X POST localhost:8000/api/auth/register/ -H 'Content-Type: application/json' \
     -d '{"username":"dr_rao","password":"secret123","role":"PROVIDER"}'

# 2. recharge Rs 500 (initiate, then call the mock callback with the returned reference)
curl -X POST localhost:8000/api/wallet/recharge/ -H "Authorization: Token $USER_TOKEN" \
     -H 'Content-Type: application/json' -d '{"amount":"500"}'
curl -X POST localhost:8000/api/wallet/recharge/callback/ -H 'Content-Type: application/json' \
     -d '{"reference":"RCH-XXXXXXXXXXXXXXXX","status":"success"}'

# 3. create (assigned to provider id 2), accept, start
curl -X POST localhost:8000/api/sessions/ -H "Authorization: Token $USER_TOKEN" \
     -H 'Content-Type: application/json' -d '{"provider_id":2}'
curl -X POST localhost:8000/api/sessions/1/accept/ -H "Authorization: Token $PROVIDER_TOKEN"
curl -X POST localhost:8000/api/sessions/1/start/  -H "Authorization: Token $USER_TOKEN"

# 4. bill / end
curl -X POST localhost:8000/api/sessions/1/bill/ -H "Authorization: Token $USER_TOKEN"
curl -X POST localhost:8000/api/sessions/1/end/  -H "Authorization: Token $USER_TOKEN"
```

## Project layout

```
config/          settings, root urls
accounts/        custom User (role: USER / PROVIDER), registration, role permissions
wallet/          Wallet, WalletEntry (ledger), RechargeTransaction, wallet services, recharge API, tests
consultations/   ConsultationSession, state machine + billing (services.py), API, tests,
                 management command bill_active_sessions
docs/            state diagram and ER diagram
NOTES.md         implementation decisions
```

See `docs/DIAGRAMS.md` for diagrams and `NOTES.md` for the reasoning behind the main decisions.
