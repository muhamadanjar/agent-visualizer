from __future__ import annotations

import os

from argon2 import PasswordHasher

_TEST_PASSWORD = "phase-two-test-password"
_TEST_PASSWORD_HASH = PasswordHasher().hash(_TEST_PASSWORD)

os.environ.update(
    {
        "APP_ENV": "test",
        "OWNER_USERNAME": "owner",
        "OWNER_PASSWORD_HASH": _TEST_PASSWORD_HASH,
        "SESSION_SECRET": "test-session-secret-with-enough-entropy",
        "PUBLIC_ORIGIN": "http://testserver",
        "DATABASE_URL": "postgresql://office:test-password@localhost:5432/office",
    }
)
