# Diagrams

## Session state machine

```mermaid
stateDiagram-v2
    [*] --> unassigned: create session
    unassigned --> assigned: assign (user)
    assigned --> accepted_by_provider: accept (provider)
    accepted_by_provider --> in_progress: start (user, balance >= 5 min)
    in_progress --> completed: end (user / provider)
    in_progress --> completed: end (system, insufficient balance)
    unassigned --> cancelled: cancel
    assigned --> cancelled: cancel
    accepted_by_provider --> cancelled: cancel
    completed --> [*]
    cancelled --> [*]
```

Plain-text version:

```
unassigned --assign--> assigned --accept--> accepted_by_provider --start--> in_progress
                                                                               |
                          user end / provider end / system end (no balance) ---+
                                                                               v
                                                                           completed

unassigned | assigned | accepted_by_provider  --cancel-->  cancelled
```

Any action that is not an arrow above returns HTTP 400.

## ER diagram

```mermaid
erDiagram
    USER ||--|| WALLET : has
    WALLET ||--o{ WALLET_ENTRY : "ledger"
    USER ||--o{ RECHARGE_TRANSACTION : initiates
    USER ||--o{ CONSULTATION_SESSION : "books (user)"
    USER |o--o{ CONSULTATION_SESSION : "serves (provider)"

    USER {
        int id PK
        string username
        string role "USER | PROVIDER"
    }
    WALLET {
        int id PK
        int user_id FK "unique"
        decimal balance "CHECK >= 0"
    }
    WALLET_ENTRY {
        int id PK
        int wallet_id FK
        string entry_type "credit | debit"
        decimal amount "CHECK > 0"
        decimal balance_after
        string reason "recharge | session_billing"
        string reference "RCH-... or session:id"
    }
    RECHARGE_TRANSACTION {
        int id PK
        int user_id FK
        decimal amount "CHECK > 0"
        string reference "unique"
        string status "pending | success | failed"
        datetime completed_at
    }
    CONSULTATION_SESSION {
        int id PK
        int user_id FK
        int provider_id FK "nullable"
        string status
        decimal per_minute_cost "frozen at start"
        datetime started_at
        datetime ended_at
        int billed_minutes "billing high-water mark"
        decimal billed_amount
        int talk_seconds
        string ended_by "user | provider | system : Insufficient Balance"
    }
```
