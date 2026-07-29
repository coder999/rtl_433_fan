#!/usr/bin/env python3
"""
Parses rtl_433's -A (analyzer) text output for the Ashby Park ceiling fan wall
switches and republishes clean MQTT events per (room, button).

Why -A instead of a registered -X flex decoder: this protocol is PWM-encoded with
inter-bit gaps close to typical packet-reset thresholds, which made every built-in
rtl_433 demod mode (OOK_PWM, OOK_MC_ZEROBIT, OOK_PIWM_RAW) either mis-frame or drop
the data when registered as a real decoder. rtl_433's own -A analyzer reliably
decodes it via its per-capture auto-tuned Manchester-zerobit attempt instead -
validated across dozens of live captures. This script rides that same path by
running rtl_433 in analyzer mode and parsing its printed bit lines directly.

Only the leading 24 bits (3 bytes: address + command) are used for matching, since
trailing bits are known to jitter run-to-run due to the demod's auto-tuning.
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

# (addr_hi, addr_lo, command) -> (room, button)
CODE_TABLE = {
    (0x00, 0x01, 0xCC): ("livingroom", "power"),
    (0x00, 0x01, 0xF8): ("livingroom", "light"),
    (0x00, 0x01, 0xFF): ("livingroom", "speed"),
    (0x00, 0x02, 0x26): ("diningroom", "power"),
    (0x00, 0x02, 0x3C): ("diningroom", "light"),
    (0x00, 0x02, 0x3F): ("diningroom", "speed"),
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

LINE_RE = re.compile(r"^\[00\]\s*\{\d+\}\s*([0-9a-fA-F ]+?)\s*:")


def rtl433_cmd(source_args):
    return ["rtl_433", *source_args, "-A", "-R", "0"]


def publish(room, button):
    topic = f"{BASE_TOPIC}/{room}/{button}"
    payload = str(int(time.time()))
    cmd = ["mosquitto_pub", "-h", MQTT_HOST, "-p", str(MQTT_PORT)]
    if MQTT_USER:
        cmd += ["-u", MQTT_USER, "-P", MQTT_PASS]
    cmd += ["-t", topic, "-m", payload]
    subprocess.run(cmd, check=False)
    print(f"published {topic} = {payload}", flush=True)


def fire(room, button, dry_run):
    print(f"firing {room}/{button} after quiet period", flush=True)
    if not dry_run:
        publish(room, button)


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
        hex_bytes = m.group(1).split()
        if len(hex_bytes) < 3:
            continue
        try:
            key = tuple(int(b, 16) for b in hex_bytes[:3])
        except ValueError:
            continue
        match = CODE_TABLE.get(key)
        if not match:
            continue
        room, button = match
        print(f"seen {room}/{button} from {line}", flush=True)
        with timers_lock:
            existing = pending_timers.get(key)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(QUIET_PERIOD_SECONDS, fire, args=(room, button, dry_run))
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
