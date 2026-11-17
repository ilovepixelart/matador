# Security Policy

## Supported Versions

Security fixes are released only for the latest `1.x` version. Releases
before `1.0.0` are no longer maintained; upgrade to the latest `1.x`
release to receive a fix.

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| 0.x     | :x:                |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security problems.**

Report vulnerabilities privately via GitHub's
[private vulnerability reporting](https://github.com/ilovepixelart/matador/security/advisories/new)
form. This routes the report directly to the maintainer through a private
advisory and keeps the details out of the public issue tracker until a fix
is available.

When reporting, please include:

- The affected version(s) of `matador-dashboard` and `toro-queue`
- A minimal reproduction (the `create_app` call, how it is mounted or
  proxied, the request or browser steps, observed vs expected behavior)
- The impact you believe the issue has (authentication or `can_mutate`
  bypass, CSRF, XSS, information exposure, denial of service, etc.)

## Response Expectations

- **Acknowledgement:** within 7 days of the report.
- **Triage and fix window:** targeted within 30 days for confirmed issues,
  depending on severity and complexity.
- **Disclosure:** coordinated via the GitHub advisory. A CVE will be
  requested where applicable, and a patched release will be published to
  PyPI before the advisory is made public.

## Scope

In scope:

- The `matador` package source in this repository, including its
  templates and static assets.
- The published distribution on PyPI (`matador-dashboard`).

Out of scope:

- Vulnerabilities in toro itself: report those to
  [toro](https://github.com/ilovepixelart/toro/security/advisories/new).
- Vulnerabilities in FastAPI or the Redis server: report those to
  [FastAPI](https://github.com/fastapi/fastapi/security) or
  [Redis](https://github.com/redis/redis/security).
- A dashboard served without authentication. matador ships no auth of its
  own: it inherits the host app's through `dependencies=`, or sits behind
  an authenticating reverse proxy, and logs a warning when the app is
  built without `dependencies`.

## Security model

[docs/security.md](docs/security.md) describes the authentication
contract, read-only mode (`can_mutate`), the same-origin CSRF guard, the
response headers, input bounds, and the limits worth knowing.
