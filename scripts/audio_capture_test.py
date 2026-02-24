#!/usr/bin/env python3
"""Standalone audio capture + transcription validation script.

Usage:
    python scripts/audio_capture_test.py --device "Broadcast Stream Mix"
    python scripts/audio_capture_test.py --list-devices
    python scripts/audio_capture_test.py                    # default input device

Records 10 seconds of audio, transcribes it with Whisper, and prints the
result.  Use this to validate the pipeline before wiring into Roonie.
"""
from __future__ import annotations

import argparse
import sys
import time


def list_devices() -> None:
    import sounddevice as sd

    print("Available audio devices:")
    print(sd.query_devices())


def capture_and_transcribe(device: str | None, duration: float) -> None:
    import numpy as np
    import sounddevice as sd
    from audio.capture import AudioCapture
    from audio.transcriber import WhisperTranscriber
    from audio.wake_word import WakeWordDetector

    print(f"Device: {device or '(system default)'}")
    print(f"Duration: {duration}s")
    print()

    capture = AudioCapture(device=device, sample_rate=16_000, channels=1, chunk_seconds=duration)
    capture.start()
    print(f"Recording {duration}s of audio...")
    time.sleep(duration + 0.5)
    capture.stop()
    capture.join(timeout=2.0)

    chunk = capture.get_chunk()
    if chunk is None or len(chunk) == 0:
        print("No audio captured. Check your device name and audio routing.")
        return

    print(f"Captured {len(chunk)} samples ({len(chunk) / 16_000:.1f}s)")
    print()

    print("Transcribing...")
    transcriber = WhisperTranscriber(model_size="base.en", device="cuda")
    segments = transcriber.transcribe(chunk)
    if not segments:
        print("No speech detected.")
        return

    print("Transcription:")
    for seg in segments:
        print(f"  [{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")
    full_text = " ".join(seg.text for seg in segments)
    print()
    print(f"Full text: {full_text}")
    print()

    detector = WakeWordDetector()
    wake = detector.detect(full_text)
    if wake.detected:
        print(f"Wake word DETECTED: trigger={wake.trigger_phrase!r}, "
              f"confidence={wake.confidence}, remaining={wake.remaining_text!r}")
    else:
        print("No wake word detected.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audio capture + transcription test")
    parser.add_argument("--device", default=None, help="Audio input device name or index")
    parser.add_argument("--list-devices", action="store_true", help="List available audio devices")
    parser.add_argument("--duration", type=float, default=10.0, help="Recording duration in seconds")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return 0

    capture_and_transcribe(args.device, args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
