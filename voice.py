"""Jarvis's voice: Piper text-to-speech, streamed to PipeWire.

The voice model is loaded once (that's the slow part, a few seconds).
Piper produces audio one sentence at a time, and each sentence is piped
straight into `pacat` as soon as it's ready, so Jarvis starts talking
after the first sentence instead of after the whole reply.

Lines go into a queue and a single worker thread speaks them in order,
so callers never wait and Jarvis never talks over himself.
"""

import os
import queue
import subprocess
import threading
import time

from piper import PiperVoice, SynthesisConfig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(BASE_DIR, "voices", "en_GB-northern_english_male-medium.onnx")

# >1 speaks slower, <1 faster. A touch slower sounds calmer, more butler-like.
LENGTH_SCALE = 1.05


class Voice:
    def __init__(self, model_path=DEFAULT_MODEL):
        self.piper = PiperVoice.load(model_path)
        self.config = SynthesisConfig(length_scale=LENGTH_SCALE)
        self.rate = self.piper.config.sample_rate
        list(self.piper.synthesize("Ready.", self.config))  # warm-up: the first run is slow
        self.lines = queue.Queue()
        self.speaking = None  # text being spoken right now, for the HUD
        self.busy = False     # True from queueing a line until it finishes playing
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        """Queue a line to be spoken. Returns immediately."""
        self.busy = True  # set now, so the ears stop before the worker even starts
        self.lines.put(text)

    def _worker(self):
        while True:
            text = self.lines.get()
            self.busy = True
            try:
                self._speak(text)
            except Exception as e:
                print(f"voice error: {e}")
            finally:
                self.speaking = None
                # Let the room's echo die down before the ears listen again.
                time.sleep(0.3)
                self.busy = not self.lines.empty()

    def _speak(self, text):
        player = subprocess.Popen(
            ["pacat", "--raw", f"--rate={self.rate}", "--channels=1", "--format=s16le",
             "--latency-msec=60"],
            stdin=subprocess.PIPE,
        )
        try:
            for chunk in self.piper.synthesize(text, self.config):
                self.speaking = text
                player.stdin.write(chunk.audio_int16_bytes)
                player.stdin.flush()
        finally:
            player.stdin.close()  # tells pacat that's all; it finishes playing and exits
            player.wait(timeout=120)
