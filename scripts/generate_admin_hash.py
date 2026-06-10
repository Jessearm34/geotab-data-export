#!/usr/bin/env python3
"""Generate a bcrypt ADMIN_PASSWORD_HASH for production deployment.

Usage:
    python scripts/generate_admin_hash.py
    # Prompts for password (hidden input)
    # Prints the hash to stdout — copy it into Railway env vars as ADMIN_PASSWORD_HASH

The hash format is the bcrypt $2b$ prefix, which passlib (the app's hashing
library) can verify.  Example output (actual hash will differ):

    $2b$12$o33anPDM0MHai9NMYanBOuW...
"""

import getpass
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

if __name__ == "__main__":
    password = getpass.getpass("Admin password: ")
    hashed = pwd_context.hash(password)
    print(hashed)
