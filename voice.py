"""Jarvis's voice: Piper text-to-speech, played through PipeWire.

The voice model is loaded once (that's the slow part, a few seconds),
then each line takes about a second to synthesize. Lines go into a
queue and a single worker thread speaks them in order, so callers
never wait and Jarvis never talks over himself.
"""

import os
import queue
import subprocess
import tempfile
import threading
import wave

from piper import PiperVoice

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(BASE_DIR, "voices", "en_GB-alan-medium.onnx")


class Voice:
    def __init__(self, model_path=DEFAULT_MODEL):
        self.piper = PiperVoice.load(model_path)
        self.lines = queue.Queue()
        self.speaking = None  # text being spoken right now, for the HUD
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        """Queue a line to be spoken. Returns immediately."""
        self.lines.put(text)

    def _worker(self):
        while True:
            text = self.lines.get()
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                    with wave.open(tmp.name, "wb") as wav:
                        self.piper.synthesize_wav(text, wav)
                    self.speaking = text
                    subprocess.run(["pw-play", tmp.name], check=False, timeout=120)
            except Exception as e:
                print(f"voice error: {e}")
            finally:
                self.speaking = None
