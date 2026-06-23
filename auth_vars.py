AUTH_PASSWORD = getenv("ADMIN_PASSWORD")
AUTH_PASSWORD_HASH = getenv("ADMIN_PASSWORD_HASH", "").strip()
AUTH_DOMAIN = ""  # no domain restriction