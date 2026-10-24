# pyright: basic, reportMissingImports=false
# faster-whisper is only installed in the stt image, never in Ringkeeper's own environment.
"""The faster-whisper engine: CPU, int8, voice activity detection on."""

import io

from ringkeeper_stt.config import SttConfig
from ringkeeper_stt.server import Decoded, DecodeError, Transcript

SAMPLE_RATE = 16_000


class WhisperEngine:
    def __init__(self, config: SttConfig) -> None:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        self.config = config
        self.model = WhisperModel(
            config.model, device="cpu", compute_type="int8", cpu_threads=config.threads
        )

    def decode(self, data: bytes) -> Decoded:
        from faster_whisper.audio import decode_audio  # noqa: PLC0415

        try:
            # From a buffer: the audio never touches the disk.
            samples = decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE)
        except Exception as error:
            raise DecodeError(type(error).__name__) from None
        return Decoded(samples=samples, seconds=len(samples) / SAMPLE_RATE)

    def transcribe(self, audio: Decoded) -> Transcript:
        segments, info = self.model.transcribe(
            audio.samples, language=self.config.language, vad_filter=True, beam_size=5
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcript(text=text, language=info.language)
