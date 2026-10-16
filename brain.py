"""Jarvis's personality: which phrases he understands and what he says back.

Everything Jarvis says lives here. Replies come in a few variations so he
doesn't sound like a recording, and he avoids repeating the last one.
"""

import os
import random
import subprocess
from datetime import datetime

import monitor  # sysmon's monitor.py, for its warning thresholds (server.py sets up the path)

ADDRESS = "brodda"  # what Jarvis calls you
LAUNCHER = os.path.expanduser("~/Applications/artcraft-launcher.AppImage")

# Same limits as sysmon, so Jarvis and sysmon's pop-ups agree.
TEMP_WARN_C = monitor.TEMP_WARN_C
MEM_WARN_PERCENT = monitor.MEM_WARN_PERCENT
DISK_WARN_PERCENT = monitor.DISK_WARN_PERCENT

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
    "disk space": "disk",
    "network": "network",
    "internet speed": "network",
    "uptime": "uptime",
    "how long have you been running": "uptime",
    "open the launcher": "launcher",
    "how are you": "how_are_you",
    "who are you": "who",
    "introduce yourself": "who",
    "tell me a joke": "joke",
    "hello": "hello",
    "good morning": "good_morning",
    "good night": "good_night",
    "i am back": "back",
    "thank you": "thanks",
    "goodbye": "goodbye",
}

JOKES = [
    "There are ten kinds of people in the world. Those who understand binary, and those who don't.",
    f"I would tell you a UDP joke, {ADDRESS}, but you might not get it.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I asked the CPU how it was doing. It said it was under a lot of load.",
    f"Your laptop has no battery, {ADDRESS}. Which makes us both creatures of the wall socket.",
]

_last = {}  # command -> the reply used last time, so variations don't repeat


def pick(key, options):
    """A random option, but never the same one twice in a row."""
    choices = [o for o in options if o != _last.get(key)] or options
    _last[key] = random.choice(choices)
    return _last[key]


def part_of_day():
    hour = datetime.now().hour
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def clock():
    return datetime.now().strftime("%-I:%M %p")


def greeting():
    return pick("greeting", [
        f"Good {part_of_day()}, {ADDRESS}. Jarvis online. All systems are being monitored.",
        f"Good {part_of_day()}, {ADDRESS}. I'm up and running.",
        f"Jarvis online. Good {part_of_day()}, {ADDRESS}.",
    ])


def describe_uptime(seconds):
    h, m = seconds // 3600, seconds % 3600 // 60
    if h >= 24:
        return f"{h // 24} days and {h % 24} hours"
    if h:
        return f"{h} hours and {m} minutes"
    return f"{m} minutes"


def temp_comment(t):
    if t < 50:
        return "Nice and cool."
    if t < 65:
        return "Comfortable."
    if t < TEMP_WARN_C:
        return "Getting warm."
    return f"That's hot, {ADDRESS}."


def trouble(m):
    return ((m["temp_c"] or 0) >= TEMP_WARN_C
            or m["mem_percent"] >= MEM_WARN_PERCENT
            or m["disk_percent"] >= DISK_WARN_PERCENT)


def status_report(m):
    """One spoken paragraph summarizing the latest sample."""
    lines = [pick("report_intro", [f"Here's the rundown, {ADDRESS}.", f"Right, {ADDRESS}.", "Status report."])]
    lines.append(f"It's {clock()}.")
    cpu = f"CPU load is {round(m['cpu_percent'])} percent"
    lines.append(f"{cpu}, at {round(m['temp_c'])} degrees." if m["temp_c"] is not None else f"{cpu}.")
    lines.append(f"Memory is {round(m['mem_percent'])} percent used, "
                 f"with {m['mem_available_mb']} megabytes free.")
    if m["top_processes"]:
        top = m["top_processes"][0]
        lines.append(f"The heaviest process is {top['name']}, using {top['mem_mb']} megabytes.")
    lines.append("Some systems need attention." if trouble(m) else "All systems nominal.")
    return " ".join(lines)


def alert(kind, value):
    """Spoken warning when a sysmon threshold is crossed."""
    return {
        "temp": f"{ADDRESS.capitalize()}, heads up. The CPU is at {round(value)} degrees.",
        "mem": f"{ADDRESS.capitalize()}, memory is {round(value)} percent full. Might want to close something.",
        "disk": f"{ADDRESS.capitalize()}, the disk is {round(value)} percent full.",
    }[kind]


def reply(name, m):
    """What Jarvis says for a command. m is the latest stats sample (or None)."""
    if name == "wake":
        return pick(name, [f"Yes, {ADDRESS}?", f"Go ahead, {ADDRESS}.", "At your service.", "Listening."])
    if name == "hello":
        return pick(name, [f"Hello, {ADDRESS}.", f"Hey, {ADDRESS}. At your service.", f"Alright, {ADDRESS}?"])
    if name == "thanks":
        return pick(name, ["You're welcome.", f"Any time, {ADDRESS}.", "My pleasure.", "Don't mention it."])
    if name == "goodbye":
        return pick(name, [f"Later, {ADDRESS}. I'll keep an eye on things.", f"See you, {ADDRESS}."])
    if name == "back":
        return pick(name, [f"Welcome back, {ADDRESS}.", f"Good to have you back, {ADDRESS}. Everything held together."])
    if name == "who":
        return ("I'm Jarvis. Just a rather very intelligent system, "
                "running on your trusty ASUS, keeping watch over the hardware.")
    if name == "joke":
        return pick(name, JOKES)
    if name == "time":
        return pick(name, [f"It's {clock()}, {ADDRESS}.", f"{clock()}.", f"The time is {clock()}."])
    if name in ("good_morning", "good_night"):
        said, actual = name.removeprefix("good_"), part_of_day()
        if said == "night":
            return pick(name, [f"Good night, {ADDRESS}. I'll be here.", f"Sleep well, {ADDRESS}."])
        if actual == "morning":
            return pick(name, [f"Good morning, {ADDRESS}.", f"Morning, {ADDRESS}. Systems are all warmed up."])
        return f"Morning? It's the {actual}, {ADDRESS}. But good morning to you too."
    if name == "launcher":
        if not os.path.exists(LAUNCHER):
            return "I can't find the launcher."
        # start_new_session: the launcher keeps running if Jarvis stops.
        subprocess.Popen([LAUNCHER], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return pick(name, ["Opening the launcher.", f"On it, {ADDRESS}.", "Launcher coming up."])

    # Everything below needs a stats sample.
    if not m:
        return "Still gathering data. Give me a second."
    if name == "report":
        return status_report(m)
    if name == "how_are_you":
        if trouble(m):
            return f"Honestly, {ADDRESS}, a bit stressed. " + status_report(m)
        return pick(name, [
            f"All systems nominal, {ADDRESS}. Thanks for asking.",
            f"Running smooth, {ADDRESS}. CPU at {round(m['cpu_percent'])} percent and feeling fine.",
            f"Can't complain, {ADDRESS}. Well, I could, but the numbers are all good.",
        ])
    if name == "temp":
        if m["temp_c"] is None:
            return "I can't read the temperature."
        return f"The CPU is at {round(m['temp_c'])} degrees. {temp_comment(m['temp_c'])}"
    if name == "memory":
        return (f"Memory is {round(m['mem_percent'])} percent used, "
                f"with {m['mem_available_mb']} megabytes free.")
    if name == "disk":
        room = "Plenty of room." if m["disk_percent"] < 70 else "Getting full."
        return f"The disk is {round(m['disk_percent'])} percent full. {room}"
    if name == "network":
        return (f"Downloading at {round(m['net_rx_kbps'])} kilobytes a second, "
                f"uploading at {round(m['net_tx_kbps'])}.")
    if name == "uptime":
        return f"We've been up for {describe_uptime(m['uptime_s'])}, {ADDRESS}."
    return None
