# Telex Radio

Every morning, once the dispatcher has written the day's reports, it also writes a spoken script of them; the Pi records it with Kokoro, and the app plays it, with chapters per topic and lock-screen controls. Pull-only like the rest of Telex, English only, rendered privately on the Pi.

## How it works

```
dispatcher run ──▶ reports/<id>/<date>.md ──▶ shows/<date>.md ──check-show──▶ push
app (pulls at dispatch +15/25/40/60 min, then every 30) ──▶ checkout on the shared volume
tts service (polls every minute) ──▶ $TELEX_DATA_DIR/audio/<date>.mp3 + <date>.json (chapters)
app ──▶ home-page player, /radio page
```

- **Script.** `shows/<date>.md` in the data repo, written at the end of the dispatcher run from that day's reports only. Format and writing rules: the "Morning show" section of `data-template/DISPATCHER.md`. Segments are marked `## intro`, `## <topic-id>`, `## quiet: a, b`, `## outro`; they become the chapters.
- **Check.** `python3 scripts/dispatch.py check-show [file]` (stdlib) rejects digits, symbols, brackets, Markdown, non-Latin letters, sentences over 40 words, over-long segments (intro 60, update 280, baseline 350, quiet 10 + 15 per topic, outro 40, show 1,400 words), and segments that don't match the day's reports (every report once; quiet reports only in the quiet segment). The parser (`parse_show`, `load_show`) lives there too; the app and the tts service load it from the checkout.
- **Rendering.** `telex.radio.watch` (stdlib) runs in the `tts` container. For each script from the last `TELEX_RADIO_KEEP_DAYS` days whose content hash has no audio yet, it starts `telex.radio.render` in a child process under `nice`, so the model (~1.1 GB) is only in memory while recording. Paragraphs are joined with 0.6 s of silence, segments with 1.2 s; the show is peak-normalised and written as 64 kbps mono MP3 by libsndfile. A failed render is recorded in the JSON and not retried until the script changes. Audio older than the retention window is deleted.
- **Engine.** Kokoro v1.0 through `kokoro-onnx` 0.6.1, voice `af_heart`, misaki G2P with the espeak fallback (`telex/radio/engine.py`). Anything with `name`, `sample_rate`, `info` and `synth(text) -> float32 array` can replace it. The model files are downloaded at image build and checked against their SHA-256.
- **App.** A strip on the home page with a compact player for today's or yesterday's show (or "recording…"), and `/radio` with the full player, the running order (each topic links to its report), the script, problems the check found, and the last two weeks. The player uses the Media Session API: lock-screen play/pause, previous/next topic, seek. It remembers the position per show and the playback speed (1×, 1.15×, 1.3×) in the browser.

## Measured

- The 25 Oct 2026 test show: 452 words, 181 s of audio (about 150 words a minute, so a full 1,400-word show runs about 9½ minutes). Recorded in 86 s on a 2-core x86 cloud VM; the bake-off measured 0.92× real time on the Pi 5 with 4 threads, so budget about as long to record as to play. Peak memory 1.1 GB.
- `preshed` 3.0.14 breaks loading spaCy's English model ("Python int too large to convert to C ssize_t"); it is pinned below that in the `radio` extra.

## Decisions (25 Oct 2026)

1. One show file per day, at the end of the run, covering every report dated that day. No facts beyond the reports.
2. Per topic: headline, context, the two or three changes that matter, what to watch. Context depends on the gap since the previous report: one sentence when it is three days or less, up to three sentences for a baseline or a longer gap. Topic segments are ordered by how much changed.
3. Quiet topics share one line near the end. A day when every topic is quiet still gets a short show.
4. The confidence labels of the reports (decided/announced/proposed, interpretation, contested, only in X sources, no independent confirmation, headline only, low-confidence source) must survive in spoken form. `check-show` cannot verify this.
5. Separate `tts` service sharing the data volume; the app image stays light.
6. Extra pulls at 15, 25, 40 and 60 minutes after the dispatch time, so a show lands soon after it is committed.
7. In-app player only (Android, home-screen web app). No podcast feed.

## Operating it

```sh
docker compose logs -f tts                                    # "rendering <date>", then "rendered <date>: …"
docker compose exec tts python -m telex.radio.render \
  --checkout /data/repo --data-dir /data --date 2026-10-25     # re-record one day by hand
python3 scripts/dispatch.py check-show shows/2026-10-25.md    # in the data repo
```

Environment (all optional): `TELEX_RADIO_VOICE` (`af_heart`), `TELEX_RADIO_SPEED` (`1.0`), `TELEX_RADIO_THREADS` (onnxruntime default), `TELEX_RADIO_NICE` (`10`), `TELEX_RADIO_KEEP_DAYS` (`14`), `TELEX_RADIO_POLL_SECONDS` (`60`), `TELEX_RADIO_TIMEOUT_MINUTES` (`60`).

## Known limitations

- Kokoro anglicises foreign proper names. Possible later fix: misaki lexicon overrides, or respellings in the script.
- Heteronyms (lead/lead, read/read) were judged by ear only in the bake-off.
- The rendered audio has not been round-tripped through Whisper in this build; the bake-off did that for the same stack.

## Out of scope

Multiple voices or a two-host format (it invites padding, against the house rules), music beds, non-English shows, streaming while rendering, a podcast feed.

## Background

The engine was chosen by a bake-off of seven self-hosted engines on a Raspberry Pi 5 (8 GB), in the separate repo `<you>/telex-tts-bakeoff`: Kokoro tied for best blind naturalness (4.67/5), read raw numbers and symbols correctly where three engines produced gibberish, and was the only top-rated engine fast enough on the Pi (Pocket TTS needed 33 minutes for a 10-minute show).
