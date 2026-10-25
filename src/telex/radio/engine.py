"""Text-to-speech engines. Anything with `name`, `sample_rate`, `info` and `synth(text)` works."""

import os
from pathlib import Path

import numpy as np


class Kokoro:
    name = "kokoro"

    def __init__(
        self,
        model_dir: Path,
        voice: str = "af_heart",
        speed: float = 1.0,
        threads: int | None = None,
    ):
        import onnxruntime as rt
        from kokoro_onnx import Kokoro as KokoroOnnx
        from misaki import en, espeak

        options = rt.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
        session = rt.InferenceSession(
            str(model_dir / "kokoro-v1.0.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.kokoro = KokoroOnnx.from_session(session, str(model_dir / "voices-v1.0.bin"))
        self.g2p = en.G2P(trf=False, british=False, fallback=espeak.EspeakFallback(british=False))
        self.voice = voice
        self.speed = speed
        self.sample_rate = 24000
        self.info = {"engine": "kokoro-v1.0", "voice": voice, "speed": speed, "g2p": "misaki"}

    def synth(self, text: str) -> np.ndarray:
        phonemes, _ = self.g2p(text)
        audio, rate = self.kokoro.create(
            phonemes, voice=self.voice, speed=self.speed, is_phonemes=True
        )
        self.sample_rate = rate
        return np.asarray(audio, dtype=np.float32)


def from_env() -> Kokoro:
    env = os.environ.get
    threads = env("TELEX_RADIO_THREADS")
    return Kokoro(
        Path(env("TELEX_RADIO_MODELS", "/models/kokoro")),
        voice=env("TELEX_RADIO_VOICE", "af_heart"),
        speed=float(env("TELEX_RADIO_SPEED", "1.0")),
        threads=int(threads) if threads else None,
    )
