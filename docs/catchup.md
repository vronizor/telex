# Catch-ups

A catch-up is a one-off look back at a subject the owner lost track of: "UK politics since the 2024 election". The next dispatcher run writes a cited catch-up report and, from it, a spoken special of about eight minutes that the Pi records like the morning show. Afterwards the owner can keep following the subject as a normal topic.

## How it works

```
Catch me up form ──▶ topics/<id>.toml (catchup = true, window = "…")
dispatcher run: daily reports ──▶ morning show ──▶ catch-up report ──▶ specials/<date>-<id>.md ──check-special──▶ push
tts service ──▶ $TELEX_DATA_DIR/audio/specials/<date>-<id>.mp3 + .json (chapters per storyline)
app ──▶ topic page (report + Listen), /radio/specials/<date>-<id>, home strip while unplayed
```

- **Topic.** `catchup = true` and `window` (the period in the owner's words) in the topic file; the brief is an optional angle. No anchor or slot: a catch-up topic is due when it has no report yet or a refresh ("Redo catch-up") is queued, and never otherwise. `dispatch.py` owns this (`due_on`, `forecast`, `plan`).
- **Plan.** `plan` lists catch-ups apart from the daily topics, under `catchup`, at most one per run (`MAX_CATCHUPS_PER_RUN`), oldest request first; the rest are in `catchups_deferred`. The run writes it after the morning show is pushed, so a long catch-up never delays the daily work.
- **Report.** `reports/<id>/<date>.md` with `status = "catchup"`, `window` and `period_start`: Where it stands, Storylines (`###` per storyline), Who's who, Watch next, Left out, Sources. No Story so far and no diff list. Format and method: "Catch-ups" in `DISPATCHER.md`.
- **Special.** `specials/<date>-<id>.md`: `## intro`, three to five `## story: <title>`, optional `## cast`, `## outro`. Story titles become chapter titles and are not read aloud. `check-special` checks the structure, 800 to 1,300 words in total, per-segment limits and everything `check-show` checks for speakability. The morning show leaves catch-ups out (`check-show` rejects a segment for one).
- **Radio.** Audio keys: a date for a morning show, `specials/<date>-<id>` for a special (`library.script_path`). Specials are kept as long as their script is in the data repo, not pruned by `TELEX_RADIO_KEEP_DAYS`. Morning shows render first in each tick.
- **Keep following.** `/t/<id>/follow` is the normal topic form; saving drops `catchup` and `window` and picks an anchor. The first update has the catch-up as `previous_report` and writes a new Story so far from its storylines.
- **E-reader.** A catch-up report goes into that week's edition as its own chapter (`CATCH-UP`).

## Decisions (15 Nov 2026)

1. One-off by default, with a "Keep following" option; not a mode of existing topics. Redoing a catch-up builds it from scratch.
2. Storylines rather than chronology, both in writing and in audio. Easy to revisit.
3. About eight minutes of audio, whatever the window; the written report holds the detail.
4. Selection rules and a "Left out" section, because choosing what fits is framing. Background sources may map the period but never be cited; every event needs a fetched source; nothing from the model's memory.
5. A separate episode, not part of the morning show.

## Operating it

```sh
python3 scripts/dispatch.py check-special specials/2026-11-16-uk-politics.md   # in the data repo
docker compose exec tts python -m telex.radio.render \
  --checkout /data/repo --data-dir /data --key specials/2026-11-16-uk-politics   # re-record by hand
```
