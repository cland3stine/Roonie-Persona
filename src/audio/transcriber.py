"""Local speech-to-text using faster-whisper (CTranslate2-optimized Whisper).

``WhisperTranscriber`` wraps model loading and transcription into a simple
interface that accepts a float32 NumPy array and returns text segments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    """A single transcription segment returned by Whisper."""

    text: str
    start: float
    end: float


class WhisperTranscriber:
    """Thin wrapper around ``faster_whisper.WhisperModel``.

    Parameters
    ----------
    model_size : str
        Whisper model name — e.g. ``"base.en"`` (74 MB, English-only, fast).
    device : str
        ``"cuda"`` for GPU or ``"cpu"`` for fallback.
    compute_type : str
        CTranslate2 compute type — ``"float16"`` for GPU, ``"int8"`` for CPU.
    """

    def __init__(
        self,
        *,
        model_size: str = "base.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: object | None = None

    def _ensure_model(self) -> object:
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install with: pip install faster-whisper"
            )

        device = self._device
        compute_type = self._compute_type
        # Graceful CUDA fallback.
        try:
            self._model = WhisperModel(
                self._model_size, device=device, compute_type=compute_type,
            )
        except Exception:
            if device != "cpu":
                logger.warning(
                    "CUDA unavailable for Whisper, falling back to CPU (int8)"
                )
                device = "cpu"
                compute_type = "int8"
                self._model = WhisperModel(
                    self._model_size, device=device, compute_type=compute_type,
                )
            else:
                raise

        logger.info(
            "WhisperTranscriber ready (model=%s, device=%s, compute=%s)",
            self._model_size, device, compute_type,
        )
        return self._model

    def transcribe(self, audio: np.ndarray) -> list[Segment]:
        """Transcribe a float32 audio array (16 kHz mono) into text segments."""
        if audio is None or len(audio) == 0:
            return []

        model = self._ensure_model()
        segments_iter, _info = model.transcribe(  # type: ignore[union-attr]
            audio,
            beam_size=1,
            language="en",
            vad_filter=True,
        )
        results: list[Segment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                results.append(Segment(text=text, start=seg.start, end=seg.end))
        return results

    def transcribe_text(self, audio: np.ndarray) -> str:
        """Convenience: transcribe and return concatenated text."""
        segments = self.transcribe(audio)
        return " ".join(seg.text for seg in segments).strip()
