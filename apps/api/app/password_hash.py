"""Hash an owner password supplied on stdin; never echo the plaintext."""

from __future__ import annotations

import sys

from argon2 import PasswordHasher
from argon2.exceptions import HashingError


def main() -> int:
    password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < 12:
        print("password must contain at least 12 characters", file=sys.stderr)
        return 2
    try:
        encoded = PasswordHasher().hash(password)
    except HashingError:
        print("password hashing failed", file=sys.stderr)
        return 1
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
