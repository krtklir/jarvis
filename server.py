"""Jarvis HUD server: serves the HUD page and live system stats as JSON.

The stats come from sysmon's reader functions (~/sysmon/monitor.py),
sampled every 2s instead of waiting for sysmon's 60s log.

Jarvis speaks through voice.py (Piper), which lives in the .venv, so run
the server with the venv's Python:

Jarvis listens through ears.py (Vosk); say "Jarvis, status report".

Run:
    .venv/bin/python server.py           # then open http://127.0.0.1:8765
    .venv/bin/python server.py -p 9000   # use a different port
    .venv/bin/python server.py --mute    # HUD only, no voice
    .venv/bin/python server.py --deaf    # don't listen to the microphone
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSMON_DIR = os.environ.get("SYSMON_DIR", os.path.expanduser("~/sysmon"))
sys.path.insert(0, SYSMON_DIR)

import monitor  # noqa: E402  (needs the sys.path line above)

SAMPLE_EVERY_S = 2
# How many samples the CPU/RAM graph remembers (90 x 2s = 3 minutes).
HISTORY_LEN = 90
# Don't repeat the same spoken warning more often than this.
ALERT_COOLDOWN_S = 10 * 60
# How long the HUD shows what Jarvis heard.
HEARD_SHOW_S = 5
LAUNCHER = os.path.expanduser("~/Applications/artcraft-launcher.AppImage")

# What Jarvis understands after his name -> which command it runs.
# Vosk's small model only knows dictionary words (no "artcraft").
PHRASES = {
    "status report": "report",
    "report": "report",
    "what time is it": "time",
    "time": "time",
    "temperature": "temp",
    "how hot is it": "temp",
    "memory": "memory",
    "open the launcher": "launcher",
    "hello": "hello",
    "thank you": "thanks",
}


class Stats:
    """Samples the system every SAMPLE_EVERY_S seconds in a background thread.

    Sampling runs whether or not a browser is open, so Jarvis can still
    notice a hot CPU, and every HUD tab just reads the latest sample.
    """

    def __init__(self, voice=None):
        self.voice = voice
        self.ears = None  # set in main() once the ears are running
        self.latest = None
        self.history = deque(maxlen=HISTORY_LEN)
        self.alerted = {}  # alert key -> time.monotonic() it was last spoken
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        prev = monitor.take_snapshot()
        while True:
            time.sleep(SAMPLE_EVERY_S)
            curr = monitor.take_snapshot()
            try:
                m = monitor.collect(prev, curr)
                self.history.append({"cpu": m["cpu_percent"], "mem": m["mem_percent"]})
                m["history"] = list(self.history)
                m["hostname"] = socket.gethostname()
                m["uptime_s"] = read_uptime()
                m["cpu_count"] = os.cpu_count()
                self.latest = m
                self._check_alerts(m)
            except Exception as e:
                print(f"sample failed: {e}")
            prev = curr

    def get(self):
        m = dict(self.latest or {})
        m["voice"] = self.voice is not None
        m["speaking"] = self.voice.speaking if self.voice else None
        m["ears"] = self.ears is not None
        m["awake"] = bool(self.ears and self.ears.awake)
        heard = self.ears.heard if self.ears else None
        m["heard"] = heard[0] if heard and time.time() - heard[1] < HEARD_SHOW_S else None
        return m

    def _check_alerts(self, m):
        """Speak a warning when a sysmon threshold is crossed (same limits as sysmon)."""
        if not self.voice:
            return
        alerts = []
        if m["temp_c"] is not None and m["temp_c"] >= monitor.TEMP_WARN_C:
            alerts.append(("temp", f"Warning. CPU temperature is {round(m['temp_c'])} degrees."))
        if m["mem_percent"] >= monitor.MEM_WARN_PERCENT:
            alerts.append(("mem", f"Warning. Memory is {round(m['mem_percent'])} percent full."))
        if m["disk_percent"] >= monitor.DISK_WARN_PERCENT:
            alerts.append(("disk", f"Warning. The disk is {round(m['disk_percent'])} percent full."))
        now = time.monotonic()
        for key, line in alerts:
            if now - self.alerted.get(key, -ALERT_COOLDOWN_S) >= ALERT_COOLDOWN_S:
                self.alerted[key] = now
                self.voice.say(line)


def greeting():
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}. Jarvis online. All systems are being monitored."


def time_line():
    return f"It is {datetime.now().strftime('%-I:%M %p')}."


def status_report(m):
    """One spoken paragraph summarizing the latest sample."""
    lines = [time_line()]
    lines.append(f"CPU load is {round(m['cpu_percent'])} percent")
    if m["temp_c"] is not None:
        lines[-1] += f", at {round(m['temp_c'])} degrees."
    else:
        lines[-1] += "."
    lines.append(f"Memory is {round(m['mem_percent'])} percent used, "
                 f"with {m['mem_available_mb']} megabytes free.")
    if m["top_processes"]:
        top = m["top_processes"][0]
        lines.append(f"The heaviest process is {top['name']}, using {top['mem_mb']} megabytes.")
    trouble = (
        (m["temp_c"] or 0) >= monitor.TEMP_WARN_C
        or m["mem_percent"] >= monitor.MEM_WARN_PERCENT
        or m["disk_percent"] >= monitor.DISK_WARN_PERCENT
    )
    lines.append("Some systems need attention." if trouble else "All systems nominal.")
    return " ".join(lines)


def run_command(name, stats):
    """Turn a command name from the ears into something Jarvis says or does."""
    m = stats.latest
    if name == "wake":
        reply = "Yes?"
    elif name == "report":
        reply = status_report(m) if m else "Still gathering data."
    elif name == "time":
        reply = time_line()
    elif name == "temp":
        reply = (f"The CPU is at {round(m['temp_c'])} degrees."
                 if m and m["temp_c"] is not None else "I can't read the temperature.")
    elif name == "memory":
        reply = (f"Memory is {round(m['mem_percent'])} percent used, "
                 f"with {m['mem_available_mb']} megabytes free." if m else "Still gathering data.")
    elif name == "launcher":
        if os.path.exists(LAUNCHER):
            # start_new_session: the launcher keeps running if Jarvis stops.
            subprocess.Popen([LAUNCHER], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reply = "Opening the launcher."
        else:
            reply = "I can't find the launcher."
    elif name == "hello":
        reply = "Hello. At your service."
    elif name == "thanks":
        reply = "You're welcome."
    else:
        return
    stats.voice.say(reply)


def read_uptime():
    """Seconds since boot, the first number in /proc/uptime."""
    with open("/proc/uptime") as f:
        return int(float(f.read().split()[0]))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(BASE_DIR, "static"), **kwargs)

    def do_GET(self):
        if self.path == "/api/stats":
            self.send_json(self.server.stats.get())
        else:
            super().do_GET()

    def do_POST(self):
        stats, voice = self.server.stats, self.server.stats.voice
        if self.path == "/api/report":
            if not voice or not stats.latest:
                self.send_json({"ok": False}, status=503)
                return
            voice.say(status_report(stats.latest))
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # the page polls every 2s; logging each request would flood the terminal


def main():
    parser = argparse.ArgumentParser(description="Serve the Jarvis HUD.")
    parser.add_argument("-p", "--port", type=int, default=8765)
    parser.add_argument("--mute", action="store_true", help="run without the voice")
    parser.add_argument("--deaf", action="store_true", help="don't listen to the microphone")
    args = parser.parse_args()

    voice = None
    if not args.mute:
        try:
            from voice import Voice
            print("Loading voice…")
            voice = Voice()
        except Exception as e:  # piper not installed, or the model file is missing
            print(f"Voice unavailable ({e}); running silent. Use .venv/bin/python?")

    # 127.0.0.1 = only this laptop can open the HUD, not others on the Wi-Fi.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.stats = Stats(voice)

    # Ears need the voice to answer, so a muted Jarvis doesn't listen either.
    if voice and not args.deaf:
        try:
            from ears import Ears
            print("Loading ears…")
            server.stats.ears = Ears(PHRASES, lambda name: run_command(name, server.stats), voice)
        except Exception as e:  # vosk not installed, or the model folder is missing
            print(f"Ears unavailable ({e}); not listening.")

    if voice:
        voice.say(greeting())
    print(f"Jarvis HUD online at http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis HUD offline.")


if __name__ == "__main__":
    main()
