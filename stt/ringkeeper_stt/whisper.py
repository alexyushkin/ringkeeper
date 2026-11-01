# pyright: basic, reportMissingImports=false
# faster-whisper is only installed in the stt image, never in Ringkeeper's own environment.
"""The faster-whisper engine: CPU, int8, voice activity detection on.

Setting the language (RINGKEEPER_STT_LANGUAGE, or per recording) is strongly
recommended: the automatic detection pass doubles the time, and on short
accented clips it can pick the wrong language and write the transcript in it.
Several ("en,ru"), for someone who speaks more than one: the detection pass
only chooses among those, so it can't land on a language never spoken.
"""

import io
from itertools import chain

from ringkeeper_stt.config import SttConfig
from ringkeeper_stt.server import Decoded, DecodeError, TooLongError, Transcript, sniff

SAMPLE_RATE = 16_000


class WhisperEngine:
    def __init__(self, config: SttConfig) -> None:
        import onnxruntime  # noqa: PLC0415 (used for voice activity detection)
        from faster_whisper import WhisperModel  # noqa: PLC0415

        onnxruntime.disable_telemetry_events()
        self.config = config
        self.model = WhisperModel(
            config.model, device="cpu", compute_type="int8", cpu_threads=config.threads
        )

    def decode(self, data: bytes, max_seconds: float) -> Decoded:
        """Like faster_whisper.audio.decode_audio (16 kHz mono), from a buffer (the
        audio never touches the disk), with a known demuxer, stopping at max_seconds."""
        import av  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415 (with faster-whisper: the stt image, the desktop app)

        demuxer = sniff(data)
        if demuxer is None:
            raise DecodeError("unknown format")
        limit = int(max_seconds * SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        total = 0
        try:
            with av.open(
                io.BytesIO(data), mode="r", format=demuxer, metadata_errors="ignore"
            ) as box:
                if not box.streams.audio:
                    raise DecodeError("no audio")
                if box.duration is not None and box.duration / av.time_base > max_seconds + 1:
                    raise TooLongError(max_seconds)  # by its own header, before decoding
                resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
                frames = box.decode(box.streams.audio[0])
                for frame in chain(frames, [None]):  # None flushes the resampler
                    for out in resampler.resample(frame):
                        chunk = out.to_ndarray().reshape(-1)
                        total += chunk.size
                        if total > limit:
                            raise TooLongError(max_seconds)
                        chunks.append(chunk)
        except (DecodeError, TooLongError):
            raise
        except Exception as error:
            raise DecodeError(type(error).__name__) from None
        if not chunks:
            raise DecodeError("no samples")
        samples = np.concatenate(chunks).astype(np.float32) / 32768.0
        return Decoded(samples=samples, seconds=len(samples) / SAMPLE_RATE)

    def transcribe(
        self, audio: Decoded, hint: str | None = None, language: str | None = None
    ) -> Transcript:
        if language and "," in language:
            language = self.likeliest(audio, language.split(","))
        # hotwords: names the speaker is likely to say (projects, connections),
        # so "acme-web" and "n8n" come out spelled that way.
        segments, info = self.model.transcribe(
            audio.samples, language=language, vad_filter=True, beam_size=5,
            hotwords=hint or None,
        )  # fmt: skip
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcript(text=text, language=info.language)

    def likeliest(self, audio: Decoded, choices: list[str]) -> str:
        """Which of these the recording is in. Whisper transcribes in one language or
        detects any, so: its detection pass, read only for these."""
        try:
            _, _, scores = self.model.detect_language(audio=audio.samples, vad_filter=True)
        except Exception:  # no speech found, say: the first one
            return choices[0]
        likely = dict(scores)
        return max(choices, key=lambda code: likely.get(code, 0.0))
