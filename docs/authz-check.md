# Authorization check (Phase 0)

Re-runnable proof that a member cannot reach trainer routes or another member's
data. Every command below must return the status in the "Expect" column.

## Setup

```bash
BASE=http://localhost:8000          # or https://gymlog-jtd8.onrender.com
MEMBER_EMAIL=mo@example.com
MEMBER_PW=pw12345
```

Get a member's bearer token (JSON API):

```bash
MTOK=$(curl -s -X POST $BASE/api/auth/login \
  -d "username=$MEMBER_EMAIL&password=$MEMBER_PW" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

Get a member's web session cookie:

```bash
curl -s -c member.cookies -X POST $BASE/login \
  -d "email=$MEMBER_EMAIL&password=$MEMBER_PW&role=member" -o /dev/null
```

`OTHER_ID` = another member's user id, `OTHER_LOG` = a log id belonging to them.

## JSON API — as a member

| # | Command | Expect |
|---|---------|--------|
| 1 | `curl -s -o /dev/null -w "%{http_code}" $BASE/api/users -H "Authorization: Bearer $MTOK"` | `403` |
| 2 | `curl -s -o /dev/null -w "%{http_code}" $BASE/api/users/$OTHER_ID -H "Authorization: Bearer $MTOK"` | `403` |
| 3 | `curl -s -o /dev/null -w "%{http_code}" -X POST $BASE/api/exercises -H "Authorization: Bearer $MTOK" -H "Content-Type: application/json" -d '{"name":"Hack"}'` | `403` |
| 4 | `curl -s -o /dev/null -w "%{http_code}" -X DELETE $BASE/api/exercises/1 -H "Authorization: Bearer $MTOK"` | `403` |
| 5 | `curl -s -o /dev/null -w "%{http_code}" $BASE/api/logs/$OTHER_LOG -H "Authorization: Bearer $MTOK"` | `404` |
| 6 | `curl -s -o /dev/null -w "%{http_code}" -X PUT $BASE/api/logs/$OTHER_LOG -H "Authorization: Bearer $MTOK" -H "Content-Type: application/json" -d '{"weight":1}'` | `404` |
| 7 | `curl -s -o /dev/null -w "%{http_code}" -X DELETE $BASE/api/logs/$OTHER_LOG -H "Authorization: Bearer $MTOK"` | `404` |
| 8 | `curl -s -o /dev/null -w "%{http_code}" -X POST $BASE/api/logs -H "Authorization: Bearer $MTOK" -H "Content-Type: application/json" -d "{\"exercise_id\":1,\"date\":\"2026-01-01\",\"user_id\":$OTHER_ID}"` | `403` |
| 9 | `curl -s -o /dev/null -w "%{http_code}" $BASE/api/logs` | `401` (no token) |

Forged filter must return only the caller's own rows (not an error, but no leak):

```bash
curl -s "$BASE/api/logs?user_id=$OTHER_ID" -H "Authorization: Bearer $MTOK"
# every element's "user_id" must be the member's own id
```

## Web routes — as a member (cookie)

| # | Command | Expect |
|---|---------|--------|
| 10 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies $BASE/exercises` | `403` |
| 11 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies $BASE/members` | `403` |
| 12 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies -X POST $BASE/exercises -d "name=Hack"` | `403` |
| 13 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies -X POST $BASE/exercises/1/delete` | `403` |
| 14 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies -X POST $BASE/members -d "name=X&email=x@y.com&password=pw12345"` | `403` |
| 15 | `curl -s -b member.cookies "$BASE/dashboard?user_id=$OTHER_ID" \| grep -c "<tbody>"` | renders, but the table body contains **none of the other member's rows** |
| 16 | `curl -s -o /dev/null -w "%{http_code}" -b member.cookies $BASE/logs/$OTHER_LOG/edit` | `303` → `/dashboard` (never the other member's log) |

## Registration lock-down

Members are created by the trainer. Self-registration is open only to bootstrap
the first account (the trainer) on an empty database.

| # | Command | Expect |
|---|---------|--------|
| 17 | `curl -s -o /dev/null -w "%{http_code}" $BASE/register` | `403` (once a trainer exists) |
| 18 | `curl -s -o /dev/null -w "%{http_code}" -X POST $BASE/register -d "name=H&email=h@x.com&password=pw12345"` | `403` — and no user row is created |

Override for local testing only: set `ALLOW_OPEN_REGISTRATION=true`.

## Demo reset is local-only

`/danger/reset` requires **both** a SQLite database (local) **and** a matching
`RESET_TOKEN`. On Render (Postgres) it is permanently `404`, whatever the env vars say.

| # | Command | Expect |
|---|---------|--------|
| 19 | `curl -s -o /dev/null -w "%{http_code}" "https://gymlog-jtd8.onrender.com/danger/reset?token=anything"` | `404` |
| 20 | local, `RESET_TOKEN` unset: `curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/danger/reset?token=x"` | `404` |

## Automated equivalent

The same matrix runs as a script; all checks passed on the Phase 0 commit.
Rerun after any routing change.
