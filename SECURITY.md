# Security policy

AMORA IA is designed so runtime identity, memory, credentials and private context stay outside source control.

## Never commit secrets or private runtime data

Do not commit or paste into issues, pull requests, Actions logs, screenshots or documentation:

- API keys or provider tokens;
- GitHub personal access tokens;
- pairing tokens or `LOCAL_API_TOKEN` values;
- Android signing keystores, private keys or signing passwords;
- `.env` files containing real credentials;
- the local `~/.amora` runtime directory;
- SQLite databases, backups, journals or conversation exports containing private data;
- raw transcripts or screenshots containing secrets or authentication material.

The repository `.gitignore` intentionally excludes common runtime, database, environment and signing-key files. Runtime secrets belong in Amora's local `SecretStore`. CI-only signing material belongs in GitHub Actions Secrets and must be decoded only inside the temporary runner.

## Public-repository rule

A file being safe to commit to a private repository does not automatically make it safe to publish. Before changing repository visibility or merging a branch that handles credentials, audit the current tree and relevant branch history for sensitive material.

If a credential or private signing key is ever committed, treat it as compromised: stop using it, rotate it, remove it from active refs where practical, and never rely on deletion alone as a security control.

## Reporting

Do not open a public issue containing an exploitable secret or private user data. Contact the repository owner privately through GitHub first and include only the minimum information necessary to reproduce the problem.

## Scope

Third-party providers, models, SDKs and dependencies have their own security policies. This document covers the AMORA IA repository and its project-controlled code and operational practices.
