#!/usr/bin/env python3
"""Generate an ADMIN_PASSWORD_HASH for production deployment.

Usage:
    python scripts/generate_admin_hash.py
    # Prompts for password (hidden input)
    # Prints the hash to stdout — copy it into Railway env vars as ADMIN_PASSWORD_HASH

The hash format is pbkdf2_sha256$<salt>$<digest>, verified by the app's
_verify_hash() using stdlib hashlib.pbkdf2_hmac (no passlib dependency).
"""

import getpass
import hashlib
import secrets

if __name__ == "__main__":
    password = getpass.getpass("Admin password: ")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 250_000).hex()
    print(f"pbkdf2_sha256${salt}${digest}")
