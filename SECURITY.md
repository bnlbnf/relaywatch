# Security Policy

## Reporting Issues

If you find a security issue, please do not open a public issue with secrets,
tokens, cookies, private URLs, database dumps, or exploit details.

Open a private security advisory on GitHub, or contact the project maintainer
through the contact method listed in the repository profile.

## Secrets

RelayWatch reads deployment secrets from environment variables. Do not commit:

- `.env` files
- API keys or model-provider keys
- PostgreSQL passwords
- Admin tokens
- Cookies such as `RELAYWATCH_LINUXDO_COOKIE`
- VPS IPs, SSH passwords, deployment logs, or private runbooks
- Generated collection results and database dumps

Use `.env.example` as the public template.

## Data

Generated data under `data/` is ignored by git. If you publish sample data,
make sure it does not include private keys, user feedback, private domains, or
any data you do not have permission to redistribute.
