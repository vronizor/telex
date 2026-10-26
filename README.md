# Telex

A small self-hosted reader for news briefings that a scheduled Claude run writes into a private git repository. You define topics (what to follow, how it should be reported, how often, which languages to search in); a daily dispatcher run researches the topics that are due and commits a report per topic that says what changed since the last one; this app shows the reports and lets you add topics or ask for a refresh.

```
 Claude scheduled task (daily)  ──push reports──▶  private data repo  ◀──pull every 30 min / Sync──  this app (Pi)
                                ◀──topics, queue──                    ──push topic edits, refreshes──
```

- **This repo** holds the app and `data-template/`, the starting content of the data repo: `DISPATCHER.md` (what the scheduled run does, house rules, report format) and `scripts/dispatch.py` (scheduling, stdlib only).
- **The data repo** (private) holds `topics/`, `reports/` and `queue/`. The app imports `scripts/dispatch.py` from its checkout, so the scheduler logic exists once.

## Scheduling

- Every topic runs on a fixed rhythm from its `anchor` date: every N days, weeks or months after it. A new topic gets its first report at the next run, whatever its rhythm.
- The anchor is chosen to keep the busiest coming day as light as possible, counting every topic's projected runs over the next 12 weeks (daily ones, `every` and `until` included). It is picked when a topic is created or its cadence or `every` changes; the form shows the suggestion and lets you set the next report date yourself (useful for events).
- A missed run is caught up the next day. A refresh adds a report without moving the rhythm; the next scheduled run is skipped only if it falls within half an interval of the refresh.
- At most 4 topics per run (`MAX_PER_RUN`); refresh requests go first, then first reports, then the most overdue.
- `python3 scripts/dispatch.py forecast` lists the coming runs; `rebalance` suggests moves that would make busy days lighter, and `rebalance --apply` makes them. Existing topics are never moved otherwise.

## Morning radio

After the reports, the dispatcher writes `shows/<date>.md`, a spoken script of the day's reports (format and rules in `DISPATCHER.md`, checked by `dispatch.py check-show`). A second container, `tts`, records it on the Pi with Kokoro into `$TELEX_HOST_DATA_DIR/audio/` (not in git, kept 14 days), and the app plays it from the home page and `/radio`, with chapters per topic and lock-screen controls. A show takes about as long to record as to play. Design and operation: `docs/radio.md`.

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

`git pull && docker compose up -d --build` on the Pi. This builds both containers; the `tts` image is large (about 1 GB with the Kokoro model and spaCy) and its first build takes a while. Changes to `data-template/` do not reach the data repo by themselves: copy `DISPATCHER.md` or `scripts/dispatch.py` over and commit there.

## Develop

```sh
uv sync
uv run pytest                      # app tests against a throwaway bare repo, plus the scheduler tests
uv run ruff check && uv run ruff format --check
TELEX_REPO_URL=/path/to/a/data/repo TELEX_DATA_DIR=./data uv run uvicorn telex.app:app --reload
```

Fonts: IBM Plex Sans and Mono (SIL Open Font License, `src/telex/static/fonts/OFL.txt`), served locally.
