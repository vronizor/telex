# Briefings — working notes for Claude

- What it is and how it runs: `README.md`. What the scheduled run does: `data-template/DISPATCHER.md`.
- No database: the data repo checkout is the store. Topics are TOML, reports are Markdown with TOML front matter, a refresh request is an empty file in `queue/`.
- Scheduling and the topic file format live only in `data-template/scripts/dispatch.py` (stdlib, also run by the dispatcher). The app loads it from the checkout; don't reimplement it in the app.
- Every write from the app goes through `Repo.commit_and_push` (pull, add, commit, push, one retry on rejection), under one lock; run a single uvicorn worker.
- Theme: all colors, fonts and radii live in `static/themes/<name>.css` (`BRIEFINGS_THEME`); `app.css` uses only those variables, never raw values.
- UI: server-rendered Jinja, one small `app.js`, no build step. Mono (`.label`) for labels, tags, dates and codes; sans for reading. Light and dark via CSS variables; check both.
- Access: Tailscale Services (`tailscale serve --service=svc:briefings`) proxies to `127.0.0.1:$APP_PORT`; no Caddy, no login.
- Conventions: polars (never pandas) if data work appears, few dependencies, minimal comments, ruff.

## Public repository
This repo may be public. Never commit hostnames, tailnet names, IPs, usernames, topics or reports (use placeholders like `<you>`). Real topics and reports belong in the private data repo only.

## Deployment
The owner deploys with `git pull && docker compose up -d --build` on the Pi. Give exact commands plus what to expect, and ask for the output.
