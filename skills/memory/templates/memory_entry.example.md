---
name: testing approach
description: Integration tests in this project must hit a real database, not a mock. Reason — prior incident with mock/prod divergence.
type: feedback
---

Integration tests must use a real database (Postgres via testcontainers). Do not mock the DB layer.

**Why:** In 2025-Q4 a mocked test suite passed locally and in CI, but the prod migration failed because the mock did not enforce the same constraints as Postgres. The team lost ~half a day rolling back. The lesson stuck: mocks lie about constraint behavior.

**How to apply:** Any new test under `tests/integration/` should depend on the shared testcontainer fixture. Unit tests in `tests/unit/` may still use mocks for fast feedback, but anything exercising a query, migration, or transaction belongs in `tests/integration/`.
