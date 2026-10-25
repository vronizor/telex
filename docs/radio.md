# Telex Radio: design notes and handoff

Status: engine chosen, nothing built yet. This file is the starting point for the session that builds the feature.

## What it is

Every morning, once the dispatcher has written the day's reports, Telex turns them into a short spoken
show (a "little radio programme") that the owner can play from the Telex page on the phone. Pull-only like the rest of
Telex: no notifications. English only. Rendered on the Pi, privately, with an open-weight model.

## Decided

**Engine: Kokoro v1.0 (82M), voice `af_heart`, rendered on the Pi's CPU.** Chosen by a bake-off of seven
self-hosted engines (Piper, Kokoro, KittenTTS 0.8, Supertonic 3, Pocket TTS, KittenTTS 2, NeuTTS Air) on a
Raspberry Pi 5 (8 GB), in the separate repo `<you>/telex-tts-bakeoff`. What decided it:

- Blind listening (owner, 1–5 naturalness): Kokoro 4.67, tied top with Pocket TTS; the rest 2.7–4.0.
- Accuracy on scripts written for the ear: all engines within 5–9 ordinary-word errors per ~870 words
  (Whisper round trip), i.e. no meaningful difference. Foreign proper names are mangled by all engines, and
  partly by Whisper itself.
- Robustness to raw text (`$117.40`, `±4.4`, `1,206,331`, `km²`, acronyms): Kokoro and KittenTTS 2 read it
  correctly; Supertonic, Pocket and NeuTTS produced gibberish; NeuTTS also looped on a word.
- Cost on the Pi 5 (4 threads): Kokoro renders at 0.92× real time (a 10-minute show in ~9 minutes), peak
  RSS ~1.1 GB, load ~4 s. Pocket needed 33 min and KittenTTS 2 69 min and 3.2 GB for the same show.

**Stack, as validated in the bake-off** (`engines/kokoro/` there; copy, don't reinvent):

- `kokoro-onnx==0.6.1` with the model files from its GitHub release `model-files-v1.1`
  (`kokoro-v1.0.onnx`, `voices-v1.0.bin`), 24 kHz mono.
- G2P: `misaki==0.9.4` installed with `--no-deps` plus `num2words spacy addict regex` and `en_core_web_sm`,
  with `espeak.EspeakFallback`; pass phonemes to Kokoro with `is_phonemes=True`. Misaki is why numbers and
  symbols come out right. System `espeak-ng` from Debian.
- Paragraphs joined with 0.6 s of silence (`harness/run.py` in the bake-off has the joining code).
- Keep the engine behind a small interface (`sample_rate`, `synth(text) -> float32 array`) so it can be
  swapped later; the bake-off adapters are that interface.

**Pipeline shape:** the dispatcher (Claude) writes a spoken script; the Pi renders it; the app plays it.
The script, not the TTS engine, decides whether it sounds like radio.

## Open questions to settle with the owner before building

1. **One show file or one script per report?** Recommendation: the dispatcher writes one
   `shows/<date>.md` at the end of the run, covering that day's reports in order, with a short intro and
   outro and spoken transitions. A per-report script can't do transitions or ordering.
2. **Length and shape per topic.** The reports are long (several thousand characters each, mostly the
   "Story so far"). Proposal: per topic, the headline in one sentence, two or three sentences of context
   condensed from the Story so far, then what changed, then what to watch; about 90–120 seconds. Quiet
   topics get one line ("Nothing material on X today"), or are skipped. The owner suspects most of the
   content comes from the Story so far; for a daily topic, repeating context every day may get old, so
   consider full context only on baselines, weekly topics, or once a week.
3. **Spoken-style rules** (new section in `DISPATCHER.md`): no `[TAG]`s; sources named in passing
   ("Reuters reports"); numbers, dates, currencies and percentages written as words; acronyms expanded on
   first use or written as they are spoken; no tables, lists or markdown; short sentences; label
   interpretation as in the reports.
4. **A deterministic check before commit.** Add `dispatch.py check-show <file>` (stdlib) that rejects
   digits, `[`, `]`, `%`, `$`, `€`, `±`, markdown and over-length scripts, and have the dispatcher fix and
   re-run until it passes. Kokoro would cope with most slips, but the check keeps the engine swappable.
5. **Where rendering runs.** Recommendation: a separate `tts` service in `compose.yaml`, sharing the data
   volume, so the app image stays light (Kokoro + spaCy + onnxruntime is several hundred MB). It watches
   the checkout for a `shows/<date>.md` without audio, renders to `$TELEX_DATA_DIR/audio/<date>.mp3`
   (generated output, not committed to git), records chapter times per topic, and keeps the last N days.
   The app's 30-minute pull may be the trigger; a show is ready roughly a pull interval plus ten minutes
   after the dispatcher commits it.
6. **Delivery.** An in-app player (on the home page or `/radio`) with the Media Session API so lock-screen
   controls work, chapter links per topic, and the script text alongside. Optionally a private podcast RSS
   feed for clients that fetch on the device (e.g. AntennaPod); cloud-fetching apps (Pocket Casts,
   Overcast) cannot reach the tailnet.
7. **Encoding.** libsndfile writes MP3 directly (confirmed in the bake-off containers); ~64 kbps mono is
   plenty for speech.

## Known limitations

- Kokoro anglicises foreign proper names. Possible later fix: misaki lexicon
  overrides or respellings in the script. Not needed for a first version.
- Heteronyms (lead/lead, read/read) were judged by ear only; Whisper can't check them.

## Out of scope for the first version

Multiple voices or a two-host format (it invites padding, against the house rules), music beds,
non-English shows, streaming while rendering.
