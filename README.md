This edition is designed for an iPhone-only workflow. The bot itself runs in a cloud Linux container; you control it from Safari through a password-protected mobile dashboard.

## Dashboard controls
- Start Bot
- Stop Bot
- Farm Now
- Dry-run on/off
- Cycle interval
- Farm-list IDs
- Travian server/login settings
- Live activity log

## Cloud requirements
Use a container host/VPS that supports Docker and long-running services. The container needs enough memory for Chromium; 1 GB is the practical minimum, 2 GB is safer.

## Environment variables
At minimum configure:
- `TRAVIAN_SERVER`
- `TRAVIAN_USERNAME`
- `TRAVIAN_PASSWORD`
- `DASHBOARD_PASSWORD`
- `FLASK_SECRET` (recommended)

The dashboard also lets you save the Travian server/login into the local `.env`, but setting secrets in the cloud provider's encrypted environment-variable panel is preferable.

## Important first run
Keep **Dry run** enabled. Travian pages and selectors can vary by game world and CAPTCHA/lobby/2FA may require adjustment. Review the live log before enabling live actions.

## Local Docker test (optional)
```
docker build -t travian-phone-bot .
docker run --rm -p 8080:8080 --env-file .env travian-phone-bot
```
Then open `http://localhost:8080`.

## Security
Do not expose the dashboard without a strong `DASHBOARD_PASSWORD`. Use HTTPS through your cloud host. Never share the generated `.env` or cloud environment variables.

## Game-rule warning
Gameplay automation may violate Travian: Legends rules and can put the avatar at risk of penalties.
