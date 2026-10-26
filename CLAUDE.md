# Telex — working notes for Claude

- What it is and how it runs: `README.md`. What the scheduled run does: `data-template/DISPATCHER.md`.
- No database: the data repo checkout is the store. Topics are TOML, reports are Markdown with TOML front matter, a refresh request is an empty file in `queue/`.
- Topics may carry `every` (repeat every N days/weeks/months, default 1), `until` (last day) and `anchor` (the date their rhythm starts from; `slot` mirrors its weekday or day of month). Scheduling and the topic file format live only in `data-template/scripts/dispatch.py` (stdlib, also run by the dispatcher). The app loads it from the checkout; don't reimplement it in the app.
- Every write from the app goes through `Repo.commit_and_push` (pull, add, commit, push, one retry on rejection), under one lock; run a single uvicorn worker.
- Theme: all colors, fonts and radii live in `static/themes/<name>.css` (`TELEX_THEME`); `app.css` uses only those variables, never raw values.
- UI: server-rendered Jinja, one small `app.js`, no build step. Mono (`.label`) for labels, tags, dates and codes; sans for reading. Light and dark via CSS variables; check both.
- E-reader edition: `edition.py` builds a weekly EPUB (ebooklib, Pillow cover) into `$DATA_DIR/editions/`, rebuilt when the week's reports change (digest in the `.json`; bump `FORMAT_VERSION` when the output format changes). E-ink: greyscale, no flex/grid in `static/epub.css`. Served at `/opds/`.
- Radio: the dispatcher writes `shows/<date>.md`; `dispatch.py` owns its format (`parse_show`) and `check-show`. The `tts` service (`radio.Dockerfile`, `telex.radio.watch`) records it to `$DATA_DIR/audio/<date>.mp3` + `.json` (chapters) in a child process; the app reads those through the stdlib `telex.radio.library` only and never imports the engine. Design and decisions: `docs/radio.md`.
- Identity: `static/icon.svg` is the source (amber punched tape spelling TELEX in ITA2); `apple-touch-icon.png` is a 180px render of it. Wordmark = mono "TELEX" + accent block cursor. The EPUB cover draws the same tape with `draw_tape`.
- "Chat about this" links to `claude.ai/new?q=`. When `TELEX_REPO_URL` is on GitHub, the prompt is a pointer (`pointer_prompt`): repo, the last commit touching the report file, and its path, so Claude reads the full report and can open earlier ones. Otherwise it falls back to a compact pasted prompt (`chat_prompt`, capped at 1800 chars because long prefilled URLs fail). "Copy full report" puts the whole Markdown on the clipboard.
- Languages and sources: topics carry `local` and `extra_languages`; the data repo's `reader.toml` lists the languages the owner reads and `sources.toml` lists outlets by domain (role reporter/voice, tier prefer/avoid, reach). Reports may carry `searched`, `unreachable` and `contested` in their front matter. `dispatch.load_sources` is the only parser. Reachability: `reach/queue/<domain>` requests a test (Sources page), `reach/history/<date>.tsv` keeps every result, `REACH.md` is the procedure (daily run for queued outlets, monthly run with `--all`).
- Wire: `telex/wire.py` (stdlib) fetches the outlets' `feeds` hourly into `$DATA_DIR/wire/items/` (45 days, not in git), matches topics' `watch` terms into `$DATA_DIR/wire/<id>.jsonl` (topic page), and at dispatch − 15 min copies those to the data repo's `wire/` in one commit. The term format, matcher, look-back and wire file format live in `dispatch.py` (`watch_matcher`, `wire_start`, `read_wire`/`write_wire`); the run uses `dispatch.py wire` and `watch`. Never store or fetch article bodies (`docs/feeds.md`, "Out of scope").
- Access: Tailscale Services (`tailscale serve --service=svc:telex`) proxies to `127.0.0.1:$APP_PORT`; no Caddy, no login.
- Conventions: polars (never pandas) if data work appears, few dependencies, minimal comments, ruff.

## Public repository
This repo may be public. Never commit hostnames, tailnet names, IPs, usernames, topics or reports (use placeholders like `<you>`). Real topics and reports belong in the private data repo only.

## Deployment
The owner deploys with `git pull && docker compose up -d --build` on the Pi. Give exact commands plus what to expect, and ask for the output.
