# Gym Log

FastAPI + Postgres + JWT auth gym log tracker for one trainer and their members.

## Roles

- **trainer**: sees and manages every member's logs, manages the exercise list.
- **member**: sees and manages only their own logs.

The first account ever registered becomes the trainer; every account after
that registers as a member. There's no separate "make someone a trainer"
endpoint -- with one trainer this doesn't need to be self-service.

## Local setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

With no `DATABASE_URL` set, it falls back to a local `gym.db` SQLite file --
fine for trying things out. Set `DATABASE_URL` (see `.env.example`) to point
at Postgres for anything real.

## curl walkthrough

```bash
BASE=http://localhost:8000

# Register the trainer (first user ever = trainer)
curl -s -X POST $BASE/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada Trainer","email":"trainer@example.com","password":"pw12345"}'

# Register a member
curl -s -X POST $BASE/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Mo Member","email":"mo@example.com","password":"pw12345"}'

# Log in (OAuth2 password flow: form-encoded, "username" is the email)
TRAINER_TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -d "username=trainer@example.com&password=pw12345" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

MEMBER_TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -d "username=mo@example.com&password=pw12345" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Trainer adds an exercise
curl -s -X POST $BASE/api/exercises \
  -H "Authorization: Bearer $TRAINER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Back Squat"}'

# Member logs a workout (exercise_id=1 from the response above)
curl -s -X POST $BASE/api/logs \
  -H "Authorization: Bearer $MEMBER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"exercise_id":1,"date":"2026-07-07","weight":100,"reps":5,"sets":3,"notes":"felt good"}'

# Member sees only their own logs
curl -s $BASE/api/logs -H "Authorization: Bearer $MEMBER_TOKEN"

# Authorization check: member tries to view another user's logs by
# forging the user_id query param -- server ignores it and returns only
# their own logs anyway (enforced in app/crud.py, not just hidden in the UI)
curl -s "$BASE/api/logs?user_id=1" -H "Authorization: Bearer $MEMBER_TOKEN"

# Trainer sees everyone's logs
curl -s $BASE/api/logs -H "Authorization: Bearer $TRAINER_TOKEN"

# Trainer filters to just that member
curl -s "$BASE/api/logs?user_id=2" -H "Authorization: Bearer $TRAINER_TOKEN"
```

Interactive API docs are at `/docs` (Swagger's "Authorize" button works with
the `/api/auth/login` form directly).

## Deploying (Railway / Render / EC2)

1. Add a Postgres instance -- Railway/Render set `DATABASE_URL` for you
   automatically when you attach one.
2. Set env vars from `.env.example`: `JWT_SECRET` (generate a real one),
   `COOKIE_SECURE=true` once you're on https.
3. Deploy the `Dockerfile` as-is; it reads `$PORT` at runtime.
