# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.4.x   | Yes       |
| < 2.4   | No        |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Email the maintainer directly (see the GitHub profile). Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 72 hours. If confirmed, a patch will be released and you will be credited (unless you prefer anonymity).

## Security Model

NibCast's dashboard runs on `localhost:7171` only — it is never exposed to the network. The following protections are in place:

| Control | Detail |
|---------|--------|
| Dashboard auth | PIN / pattern / TOTP — any combination |
| PIN storage | PBKDF2-SHA256, 100,000 iterations, per-install random salt |
| Brute-force limit | 5 failed attempts per 60 s → HTTP 429 |
| API keys | Never stored in the repo; live in `~/.nibcast/config.json` (gitignored) |
| Audio | Never written to disk — only the final text transcript is saved |
| History | Local SQLite only — no NibCast cloud, no telemetry |

## Known Limitations

- The Flask session secret is derived from the per-install salt. Restarting the app invalidates all open sessions.
- The dashboard has no HTTPS (localhost-only — TLS adds no security benefit there).
- Privacy mode suppresses transcript text from logs and history but does not encrypt the database at rest.
