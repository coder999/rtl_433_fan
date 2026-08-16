#!/usr/bin/env python3
"""
Parses rtl_433's -X flex-decoder text output for the Ashby Park ceiling fan wall
switches and republishes clean MQTT events per (room, button), including the exact
target speed percentage for speed-button presses.

History: an earlier version of this script used -A (analyzer mode) with rtl_433's
auto-tuned Manchester-zerobit guess, on the theory that this protocol's PWM timing
couldn't be reliably matched by an explicit decoder. That theory was wrong - the
auto-guesser produced non-deterministic, jittery output and silently failed outright
on one specific code (living room's "off" state, every time, despite a strong clean
signal). Replaced 2026-08-15 with an explicit `-X OOK_PWM` decoder tuned to the
protocol's actual measured pulse widths, which decodes every button on every switch
deterministically - same code in, same code out, no jitter, no silent failures. See
README.md's "Bond RF audit" and living-room correction sections for the full
derivation.

The decoded value's low 3 bits are always 0 (padding), bits 3-4 are a 2-bit speed
target counter (0=33%, 1=66%, 2=100%, 3=off - meaningless for power/light, which are
simple toggles with no target), and everything above bit 4 is a fixed identifier for
which switch+button sent it. See CODE_TABLE below.
"""
import os
import re
import subprocess
import sys
import threading
import time

MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.0.100")
MQTT_PORT = os.environ.get("MQTT_PORT", "1883")
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
BASE_TOPIC = os.environ.get("FAN_MQTT_BASE_TOPIC", "home/fans")

RTL433_FREQ = os.environ.get("RTL433_FAN_FREQ", "304250000")
RTL433_RATE = os.environ.get("RTL433_FAN_RATE", "2048000")
RTL433_GAIN = os.environ.get("RTL433_FAN_GAIN", "49.6")

SPEED_COUNTER_TO_PERCENTAGE = {0: 33, 1: 66, 2: 100, 3: 0}  # 3 = off

# stable_id -> (room, button). stable_id = decoded_code_int >> 5.
CODE_TABLE = {
    0x1FF: ("livingroom", "speed"),
    0x27F: ("diningroom", "speed"),
    0x2FF: ("bedroom", "speed"),
    0x1F9: ("livingroom", "light"),
    0x279: ("diningroom", "light"),
    0x2F9: ("bedroom", "light"),
    0x1D9: ("livingroom", "power"),
    0x259: ("diningroom", "power"),
    0x2D9: ("bedroom", "power"),
}

# Trailing-edge debounce: a held button re-transmits the same code repeatedly with
# small gaps between repeats. We want ONE event per physical press-and-release, no
# matter how long it's held, so we wait for this much silence after the LAST matching
# packet before actually publishing (resetting the timer on every new repeat) rather
# than a leading-edge "ignore repeats within N seconds of the first match" scheme -
# the latter lets a long-enough hold outlast the window and fire again mid-hold.
#
# Measured live (2026-07-30): a single quick tap on this remote sends 4 separate
# repeat-bursts roughly 2s apart (not one continuous burst) - this is the remote's
# own reliability behavior, unrelated to how long the button is physically held. The
# quiet period must comfortably exceed that gap or each burst fires independently,
# quadruple-triggering the automation for one press. 3.0s bridges the observed ~2s
# gaps with margin; if real-world use ever shows it's still multi-firing, increase
# this further (check `journalctl -u rtl433-mqtt.service` for the "seen"/"firing"
# pattern to diagnose).
QUIET_PERIOD_SECONDS = 3.0

LINE_RE = re.compile(r"^codes\s*:\s*\{25\}([0-9a-f]+)$")

FLEX_DECODER = "n=fan,m=OOK_PWM,s=744,l=376,r=900,g=900,t=150,y=0"


def rtl433_cmd(source_args):
    return ["rtl_433", *source_args, "-R", "0", "-X", FLEX_DECODER]


def decode_hex(hex_code):
    code_int = int(hex_code, 16)
    counter = (code_int >> 3) & 0b11
    stable_id = code_int >> 5
    return stable_id, counter


def publish(room, button, percentage):
    topic = f"{BASE_TOPIC}/{room}/{button}"
    payload = str(percentage) if percentage is not None else str(int(time.time()))
    cmd = ["mosquitto_pub", "-h", MQTT_HOST, "-p", str(MQTT_PORT)]
    if MQTT_USER:
        cmd += ["-u", MQTT_USER, "-P", MQTT_PASS]
    cmd += ["-t", topic, "-m", payload]
    subprocess.run(cmd, check=False)
    print(f"published {topic} = {payload}", flush=True)


def fire(room, button, percentage, dry_run):
    print(f"firing {room}/{button} percentage={percentage} after quiet period", flush=True)
    if not dry_run:
        publish(room, button, percentage)


def run(source_args, dry_run=False):
    pending_timers = {}
    timers_lock = threading.Lock()
    proc = subprocess.Popen(
        rtl433_cmd(source_args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        m = LINE_RE.match(line)
        if not m:
            continue
        stable_id, counter = decode_hex(m.group(1))
        match = CODE_TABLE.get(stable_id)
        if not match:
            continue
        room, button = match
        percentage = SPEED_COUNTER_TO_PERCENTAGE[counter] if button == "speed" else None
        key = (room, button, percentage)
        print(f"seen {room}/{button} percentage={percentage} from {line}", flush=True)
        with timers_lock:
            existing = pending_timers.get(key)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(QUIET_PERIOD_SECONDS, fire, args=(room, button, percentage, dry_run))
            t.daemon = True
            pending_timers[key] = t
            t.start()
    proc.wait()
    # let any in-flight quiet-period timer fire before exiting (mainly matters for
    # the -r file replay / --dry-run test path, which reaches EOF almost instantly)
    with timers_lock:
        timers = list(pending_timers.values())
    for t in timers:
        t.join(timeout=QUIET_PERIOD_SECONDS + 1)
    return proc.returncode


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if args:
        # e.g. ["-r", "/path/to/file.cu8"]
        source_args = args
    else:
        source_args = ["-f", RTL433_FREQ, "-s", RTL433_RATE, "-g", RTL433_GAIN]
    sys.exit(run(source_args, dry_run=dry_run))
