"""Render one day's show script to MP3 plus a JSON of chapters."""

import argparse
import hashlib
import io
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from telex.checkout import dispatch
from telex.radio import library

log = logging.getLogger("telex.radio")
PARAGRAPH_GAP_S = 0.6
SEGMENT_GAP_S = 1.2
PEAK = 0.89
MP3_COMPRESSION = 0.6


def silence(seconds: float, rate: int) -> np.ndarray:
    return np.zeros(int(round(seconds * rate)), dtype=np.float32)


def render(show: dict, engine, titles: dict[str, str]) -> tuple[np.ndarray, list[dict]]:
    pieces: list[np.ndarray] = []
    chapters = []
    elapsed = 0
    for s_index, segment in enumerate(show["segments"]):
        if s_index:
            pieces.append(silence(SEGMENT_GAP_S, engine.sample_rate))
            elapsed += len(pieces[-1])
        chapters.append(
            {
                "start": round(elapsed / engine.sample_rate, 2),
                "kind": segment["kind"],
                "topics": [{"id": t, "title": titles.get(t, t)} for t in segment["topics"]],
            }
        )
        for p_index, paragraph in enumerate(segment["paragraphs"]):
            if p_index:
                pieces.append(silence(PARAGRAPH_GAP_S, engine.sample_rate))
                elapsed += len(pieces[-1])
            pieces.append(np.asarray(engine.synth(paragraph), dtype=np.float32).reshape(-1))
            elapsed += len(pieces[-1])
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0
    if peak > 0:
        audio = audio * (PEAK / peak)
    return audio.astype(np.float32), chapters


def encode_mp3(audio: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(
        buffer,
        audio,
        rate,
        format="MP3",
        subtype="MPEG_LAYER_III",
        bitrate_mode="CONSTANT",
        compression_level=MP3_COMPRESSION,
    )
    return buffer.getvalue()


def render_day(checkout: Path, data_dir: Path, day: date, engine) -> dict:
    raw = (library.shows_dir(checkout) / f"{day}.md").read_bytes()
    d = dispatch(checkout)
    show = d.parse_show(raw.decode("utf-8"))
    check = d.check_show(checkout, library.shows_dir(checkout) / f"{day}.md")
    titles = {t["id"]: t.get("title", t["id"]) for t in d.load_topics(checkout)}
    started = time.perf_counter()
    audio, chapters = render(show, engine, titles)
    render_s = time.perf_counter() - started
    library.write_atomic(
        library.audio_dir(data_dir) / f"{day}.mp3", encode_mp3(audio, engine.sample_rate)
    )
    meta = {
        "date": day.isoformat(),
        "digest": hashlib.sha256(raw).hexdigest()[:16],
        "duration": round(len(audio) / engine.sample_rate, 2),
        "chapters": chapters,
        "problems": check["problems"],
        "words": check.get("words", 0),
        "render_s": round(render_s, 1),
        "engine": engine.info,
        "rendered": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    library.write_meta(data_dir, day, meta)
    return meta


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(prog="telex.radio.render")
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    from telex.radio.engine import from_env

    engine = from_env()
    meta = render_day(args.checkout, args.data_dir, args.date, engine)
    log.info(
        "rendered %s: %.0fs of audio in %.0fs, %d words, %d check problems",
        args.date,
        meta["duration"],
        meta["render_s"],
        meta["words"],
        len(meta["problems"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
