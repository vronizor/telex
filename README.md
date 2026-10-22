# Telex

A small self-hosted reader for news briefings that a scheduled Claude run writes into a private git repository. You define topics (what to follow, how it should be reported, how often, which languages to search in); a daily dispatcher run researches the topics that are due and commits a report per topic that says what changed since the last one; this app shows the reports and lets you add topics or ask for a refresh.

```
 Claude scheduled task (daily)  ──push reports──▶  private data repo  ◀──pull every 30 min / Sync──  this app (Pi)
                                ◀──topics, queue──                    ──push topic edits, refreshes──
```

- **This repo** holds the app and `data-template/`, the starting content of the data repo: `DISPATCHER.md` (what the scheduled run does, house rules, report format) and `scripts/dispatch.py` (scheduling, stdlib only).
- **The data repo** (private) holds `topics/`, `reports/` and `queue/`. The app imports `scripts/dispatch.py` from its checkout, so the scheduler logic exists once.

## Scheduling

- Weekly topics get the least busy weekday, monthly topics the least busy day 1–28, when created; daily topics run every day.
- A new topic gets its first report at the next run, whatever its slot. A missed run is caught up the next day.
- At most 4 topics per run (`MAX_PER_RUN`); refresh requests go first, then first reports, then the most overdue.

## Weekly e-reader edition

Every week (`TELEX_EDITION_DAY` at `TELEX_EDITION_TIME`, default Saturday 08:00) the app builds an EPUB from the last seven days of reports, and rebuilds it if those reports change. It is served as an OPDS catalog at `<TELEX_BASE_URL>/opds/`.

- Cover: the week, one line per topic with its tag, change counts and headline.
- Contents: topics with updates, then the quiet ones in one list.
- One chapter per topic: the latest "Story so far", then each report of the week (its changes; "Where it stands" and "Watch next" only from the latest), sources as endnotes that the `[TAG]` links jump to.

Build one by hand with `docker compose exec app python -m telex.edition [--date YYYY-MM-DD]`. To list it inside inkbook-digest's catalog, set `EXTRA_CATALOGS=Telex Weekly|<TELEX_BASE_URL>/opds/` there; or add the URL as a second catalog on the e-reader.

## Set up

1. Create two GitHub repositories: `telex` (this code) and `telex-data` (**private**).
2. Initialise the data repo from the template:
   ```sh
   cp -r data-template ../telex-data && cd ../telex-data
   git init -b main && git add -A && git commit -m "init from template"
   git remote add origin git@github.com:<you>/telex-data.git && git push -u origin main
   ```
3. On the Pi, in a clone of this repo, make a deploy key and add `deploy_key.pub` to the **data** repo under Settings → Deploy keys, with **write access**:
   ```sh
   ssh-keygen -t ed25519 -f deploy_key -N "" -C telex-pi && chmod 600 deploy_key
   ```
4. `cp .env.example .env`, set `TELEX_REPO_URL`, `TELEX_BASE_URL` and the host paths, then `docker compose up -d --build`. The first start clones the data repo into `$TELEX_HOST_DATA_DIR/repo`. Check with `docker compose logs -f app`.
5. Give it a name on the tailnet with Tailscale Services: define a service `telex` with port `tcp:443` in the admin console, run `sudo tailscale serve --service=svc:telex --https=443 127.0.0.1:8020` (your `APP_PORT`), then approve the host on the Services page. Leave Funnel off. The app is then at `https://telex.<tailnet>.ts.net`. There is no login: it relies on the tailnet being private, and refuses cross-site form posts.
6. Create the scheduled task (Claude, daily at `TELEX_DISPATCH_TIME`, Europe/Madrid) with this prompt:
   > Attach the GitHub repository `<you>/telex-data` with push access, clone it, and from its root follow `DISPATCHER.md` exactly. Today's date is the current date in Europe/Madrid.

## Deploy updates

`git pull && docker compose up -d --build` on the Pi. Changes to `data-template/` do not reach the data repo by themselves: copy `DISPATCHER.md` or `scripts/dispatch.py` over and commit there.

## Develop

```sh
uv sync
uv run pytest                      # app tests against a throwaway bare repo, plus the scheduler tests
uv run ruff check && uv run ruff format --check
TELEX_REPO_URL=/path/to/a/data/repo TELEX_DATA_DIR=./data uv run uvicorn telex.app:app --reload
```

Fonts: IBM Plex Sans and Mono (SIL Open Font License, `src/telex/static/fonts/OFL.txt`), served locally.
