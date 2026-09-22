# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under the Security tab), or by opening a minimal issue that does
not contain exploit details and asking a maintainer to make contact.

Do not open a public issue with reproduction steps for an unfixed vulnerability.

## Scope and design notes

- This is a **paper-trading** simulator. There is no wallet, no private key, and no real
  money anywhere in this project. Nothing here can move funds.
- The leaderboard client stores a bearer API key supplied by the caller. Never commit an
  API key, token, or `.env` file; load credentials from the environment.
- Secrets are supplied through environment variables or a secret manager, never checked in.

## Supported versions

The latest released `0.x` version receives security fixes.
