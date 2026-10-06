# Inventory configuration

Required production environment variables:

- `DATABASE_URL`: PostgreSQL SQLAlchemy URL for production.
- `JWT_ALGORITHM`: exactly `HS256` or `RS256`.
- `JWT_SECRET`: required for `HS256`; store it in a secret manager.
- `JWT_PUBLIC_KEY`: required for `RS256`; store the public key in deployment configuration.

Optional:

- `JWT_LEEWAY_SECONDS`: bounded clock-skew allowance, default `0`.

SQLite is supported only for local tests. Never commit JWT keys, tokens, or
database credentials. Configure structured log handlers separately and ensure
request headers and exception causes are redacted.