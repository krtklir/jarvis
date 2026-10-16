"""Jarvis's ears: offline speech recognition with Vosk.

The microphone is read through `parec` (PulseAudio/PipeWire recorder) as
raw 16 kHz mono audio and fed to Vosk. Vosk is limited to a fixed list of
phrases (a "grammar"), which makes the small model far more accurate than
free dictation, and anything else is heard as "[unk]" and ignored.

Commands must start with the wake word: "jarvis, status report".
Saying just "jarvis" opens a short window where the command alone works.

Normally Vosk waits for a pause before giving its final answer. To reply
faster, Jarvis also watches the partial (still-listening) result: once it
is a complete "jarvis <command>" that isn't the start of a longer phrase,
he acts on it right away.
"""

import json
import os
import subprocess
import threading
import time

from vosk import KaldiRecognizer, Model, SetLogLevel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(BASE_DIR, "models", "vosk-model-small-en-us-0.15")

WAKE_WORD = "jarvis"
RATE = 16000
CHUNK_BYTES = 4000  # 2000 samples of 16-bit audio = 1/8 second
FOLLOW_UP_S = 6     # after a bare "jarvis", how long a command alone is accepted
MIN_CONFIDENCE = 0.6


class Ears:
    def __init__(self, phrases, on_command, voice=None, model_path=DEFAULT_MODEL):
        """phrases: {spoken phrase: command name}; on_command(name) is called on a match."""
        SetLogLevel(-1)  # Vosk is chatty on stderr
        self.phrases = phrases
        self.on_command = on_command
        self.voice = voice
        grammar = [WAKE_WORD] + list(phrases) + [f"{WAKE_WORD} {p}" for p in phrases] + ["[unk]"]
        self.recognizer = KaldiRecognizer(Model(model_path), RATE, json.dumps(grammar))
        self.recognizer.SetWords(True)  # per-word confidence scores
        # Full commands that can't be the start of a longer phrase: safe to act on early.
        full = [f"{WAKE_WORD} {p}" for p in phrases]
        self.instant = {f for f in full if not any(g.startswith(f + " ") for g in full)}
        self.awake_until = 0
        self.heard = None  # (text, time.time()) of the last thing understood, for the HUD
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def awake(self):
        return time.monotonic() < self.awake_until

    def _loop(self):
        mic = subprocess.Popen(
            ["parec", f"--rate={RATE}", "--channels=1", "--format=s16le", "--latency-msec=100"],
            stdout=subprocess.PIPE,
        )
        while True:
            chunk = mic.stdout.read(CHUNK_BYTES)
            if not chunk:
                print("ears: microphone stream ended")
                return
            if self.voice and self.voice.busy:
                # Don't listen while Jarvis is talking, or he hears himself.
                self.recognizer.Reset()
                continue
            if self.recognizer.AcceptWaveform(chunk):
                self._handle(json.loads(self.recognizer.Result()))
            else:
                partial = json.loads(self.recognizer.PartialResult()).get("partial", "")
                if partial in self.instant:
                    self.recognizer.Reset()  # drop the rest of this utterance
                    self._run(partial)

    def _handle(self, result):
        words = result.get("result", [])
        text = result.get("text", "").strip()
        if not text or "[unk]" in text:
            return
        confidence = sum(w["conf"] for w in words) / len(words)
        if confidence < MIN_CONFIDENCE:
            return

        if text == WAKE_WORD:
            self.heard = (text, time.time())
            self.awake_until = time.monotonic() + FOLLOW_UP_S
            self.on_command("wake")
            return

        if text.startswith(f"{WAKE_WORD} ") or self.awake:
            self._run(text)
        # else: a command without the wake word, outside the follow-up window

    def _run(self, text):
        command = text.removeprefix(f"{WAKE_WORD} ")
        if command in self.phrases:
            self.heard = (text, time.time())
            self.awake_until = 0
            self.on_command(self.phrases[command])
