# Fan Wall Switch Sync v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the wall-switch → Bond sync system (bridge script + HA automations) with a version that decodes the *absolute* target state directly from RF, instead of tracking a locally-guessed counter that can never be proven correct.

**Architecture:** `rtl_433` runs one explicit `-X OOK_PWM` flex decoder (parameters empirically validated against all three switches this session — the old system relied on rtl_433's auto-guesser, which silently failed on living room's "off" state and produced jittery, unreliable trailing bits everywhere else). The bridge script parses its stable, deterministic hex output, decodes speed presses straight to a percentage (0/33/66/100), and publishes that percentage over MQTT instead of a bare trigger. New HA automations call `fan.turn_on(percentage=X)` directly for speed — no counter helper — and combine power+light into one toggle action per the confirmed "power button is a master toggle" hardware behavior.

**Tech Stack:** Python 3 (bridge script, stdlib only — matches existing script), rtl_433 (`-X` flex decoder, text output), MQTT (`mosquitto_pub`/existing broker), Home Assistant YAML automations.

**Spec:** This file — no separate spec doc. All decoder parameters and code tables below were derived and empirically validated live against the real hardware during the 2026-08-15 session (see `/home/mark/projects/rtl_433_fan/README.md` for the full derivation history). Treat the tables in this plan as the source of truth; the README documents *how* they were found.

## Global Constraints

- Every RF capture/validation step targets the Raspberry Pi at `ssh raspberrypi` (RTL-SDR dongle attached). Always confirm no other `rtl_433`/`rtl_test` process is running first (`ps aux | grep -i rtl | grep -v grep`) — this project has repeatedly hit "usb_claim_interface error -6" from stale/overlapping processes.
- All Bond-triggered validation uses the `mcp__homeassistant__HassFanSetSpeed` / `HassTurnOn` / `HassTurnOff` tools against real entities — **not** simulated. Every automation, before being enabled, must be validated against **one real physical wall-switch press**, not just Bond traffic (Bond and the wall switch produce identical RF, but only a real press proves the end-to-end pipeline, matching the caution already established in `FAN_WALLSWITCH_SYNC.md` after the 2026-07-30 incident where careless enabling caused real bugs).
- The HA host is reachable at `ssh ha` (root, Home Assistant OS, Terminal & SSH addon). `/config` is git-tracked with an **automatic** commit-on-change mechanism (Home Assistant Version Control) — no manual `git commit` needed there, just verify with `git log` after edits.
- Canonical copies of the bridge script and automation logic live in `/home/mark/projects/rtl_433_fan/` (this repo, pushed to `coder999/rtl_433_fan` on GitHub) — deploy *from* here *to* the Pi (`/usr/local/bin/fan_wallswitch_bridge.py`) and HA host (`/config/automations.yaml`), not the other way around.
- Destructive edits to `/config/automations.yaml` or `/config/counters.yaml` on the live HA host require explicit user approval before execution (per this session's established rule) — do not `sed -i` those files without asking first, even mid-plan.

---

## File Structure

- **Modify:** `/home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py` — the parser/matcher/publisher. Complete rewrite of the matching logic; debounce logic (`QUIET_PERIOD_SECONDS`, trailing-edge `threading.Timer`) is unchanged and reused as-is.
- **Modify:** `/home/mark/projects/rtl_433_fan/rtl433-mqtt.sh` — update the `rtl_433` invocation to use the new `-X` flex decoder instead of `-A -R 0`.
- **Modify (HA host, `/config/automations.yaml`):** add 6 new automations (3 rooms × {power+light combined, speed}), replacing the 6 already-deleted ones.
- **Modify (HA host, `/config/counters.yaml`):** remove the 2 now-obsolete `*_fan_speed_level` counters (pending separate user approval — see Task 6).
- **Reference only, no changes:** `/home/mark/projects/rtl_433_fan/README.md` (decoder derivation history), `/home/mark/projects/rtl_433_fan/FAN_WALLSWITCH_SYNC.md` (architecture doc — update in Task 7 once the new system is live).

## Decoder Configuration (validated, all three switches)

```
rtl_433 -f 304.25M -g 30 -R 0 -X "n=fan,m=OOK_PWM,s=744,l=376,r=900,g=900,t=150,y=0"
```

Output line format to parse (one per repeat burst, ~5-10 identical repeats per physical press):
```
codes     : {25}0003fe0
```
Regex: `^codes\s*:\s*\{25\}([0-9a-f]+)$`. Parse the hex as an integer. This decoder is **fully deterministic** — unlike the old auto-guesser, there is no jitter; every repeat of the same press produces the byte-identical code.

**Bit layout** (validated across dozens of captures this session): the low 3 bits are always 0 (padding), bits 3-4 are a 2-bit speed-target counter (only meaningful for speed presses), everything above bit 4 is a fixed per-switch-per-button identifier.

```python
def decode(hex_code: str) -> tuple[int, int]:
    """Returns (stable_id, counter) where stable_id identifies the
    switch+button and counter is only meaningful when stable_id is a
    known speed-family id."""
    code_int = int(hex_code, 16)
    counter = (code_int >> 3) & 0b11
    stable_id = code_int >> 5
    return stable_id, counter
```

## CODE_TABLE (validated speed values; power/light values marked TBD are filled in during Task 3)

All `stable_id` values below were computed by actually running `code_int >> 5` (verified with a Python one-liner, not by hand — a hand-computed first draft of this table was wrong and caught during self-review; don't shortcut this arithmetic again in Task 3, run it):

```python
SPEED_COUNTER_TO_PERCENTAGE = {0: 33, 1: 66, 2: 100, 3: 0}  # 3 = off

# stable_id -> (room, button)
CODE_TABLE = {
    0x1FF: ("livingroom", "speed"),  # validated: 33/66/100/off all confirmed live, all four give stable_id 0x1FF
    0x27F: ("diningroom", "speed"),  # validated: 33/66/100/off all confirmed live, all four give stable_id 0x27F
    0x2FF: ("bedroom", "speed"),     # 66% confirmed live (code 0x5fe8 -> stable_id 0x2FF); 33/100/off NOT independently confirmed - CONFIRM in Task 3, don't trust unconfirmed
    # Light and power stable_ids: TBD, fill in during Task 3 using the
    # capture procedure below. Do not guess these — light/power failed to
    # decode cleanly for some switches under the OLD auto-guesser in past
    # sessions and must be re-verified under the new decoder specifically,
    # even though the underlying RF is unchanged.
}
```

**Known light stable_id (1 of 3 already captured this session, reuse — don't recapture):**
- Dining room light: code `0x0004f28` → `stable_id = 0x279` (computed as `0x4f28 >> 5`, verified) — light toggle, single code regardless of on/off direction (confirmed: `codes: {25}0004f28` appeared 10/10 identical repeats).

**Still needed (Task 3):** living room light, living room power, dining power, bedroom light, bedroom power. Living room and bedroom light are reachable via Bond (`HassTurnOn`/`HassTurnOff` on the `light` domain) same as dining's was. Living room power is reachable via Bond (`HassTurnOn`/`HassTurnOff` on the `fan` domain with **no** `percentage` argument — this specific switch's Bond integration routes plain on/off through its power-toggle code, unlike dining/bedroom). **Dining and bedroom power are *not* reachable via Bond** (their plain on/off always routes through the speed-family code, confirmed twice this session) — these two specifically need one real physical press on the wall switch each.

---

### Task 1: Rewrite the decode/match core of `fan_wallswitch_bridge.py`

**Files:**
- Modify: `/home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py`

**Interfaces:**
- Produces: `decode_hex(hex_code: str) -> tuple[int, int]` — returns `(stable_id, counter)`, used inline in `run()`'s per-line loop (not a separate line-parsing wrapper — the regex match and lookup stay in `run()`, matching the existing script's structure).
- Produces: `CODE_TABLE: dict[int, tuple[str, str]]` and `SPEED_COUNTER_TO_PERCENTAGE: dict[int, int]` as module-level constants (from the CODE_TABLE section above, with light/power entries added once Task 3 confirms them).
- Produces: `fire(room, button, percentage, dry_run)` and `publish(room, button, percentage)` — signatures extended with `percentage` (Steps 5-6), called from the same debounce-timer machinery as before.
- Consumes: the existing `QUIET_PERIOD_SECONDS`, `pending_timers`, `timers_lock` machinery, unchanged in structure (only the dict key shape changes, per Step 4).

- [ ] **Step 1: Read the current script to confirm what's being replaced vs. reused**

Run: `cat /home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py`

Confirm `LINE_RE`, the `run()` function's per-line matching block (`hex_bytes = m.group(1).split()` through `match = CODE_TABLE.get(key)`), and `rtl433_cmd()` are the only things being replaced. `publish()`, `fire()`, the debounce timer logic, and `__main__` argument handling are unchanged.

- [ ] **Step 2: Replace the command builder**

Replace:
```python
def rtl433_cmd(source_args):
    return ["rtl_433", *source_args, "-A", "-R", "0"]
```
With:
```python
FLEX_DECODER = "n=fan,m=OOK_PWM,s=744,l=376,r=900,g=900,t=150,y=0"

def rtl433_cmd(source_args):
    return ["rtl_433", *source_args, "-R", "0", "-X", FLEX_DECODER]
```

- [ ] **Step 3: Replace the line-matching regex and decode logic**

Replace:
```python
LINE_RE = re.compile(r"^\[00\]\s*\{\d+\}\s*([0-9a-fA-F ]+?)\s*:")
```
With:
```python
LINE_RE = re.compile(r"^codes\s*:\s*\{25\}([0-9a-f]+)$")

SPEED_COUNTER_TO_PERCENTAGE = {0: 33, 1: 66, 2: 100, 3: 0}

# Filled in from the CODE_TABLE section of the plan/README once Task 3
# confirms the remaining light/power values.
CODE_TABLE = {
    0x1FF: ("livingroom", "speed"),
    0x27F: ("diningroom", "speed"),
    0x2FF: ("bedroom", "speed"),
}

def decode_hex(hex_code):
    code_int = int(hex_code, 16)
    counter = (code_int >> 3) & 0b11
    stable_id = code_int >> 5
    return stable_id, counter
```

- [ ] **Step 4: Replace the per-line handling in `run()`**

Find this block:
```python
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
```

Replace with:
```python
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
        key = (room, button, percentage)  # percentage is None for power/light
        print(f"seen {room}/{button} percentage={percentage} from {line}", flush=True)
```

Note `key` changes shape (was a 3-byte tuple, now `(room, button, percentage)`) — this is what gets used below as the debounce dict key and what `fire()`/`publish()` need next.

- [ ] **Step 5: Update `publish()` to carry the percentage for speed events**

Replace:
```python
def publish(room, button):
    topic = f"{BASE_TOPIC}/{room}/{button}"
    payload = str(int(time.time()))
```
With:
```python
def publish(room, button, percentage):
    topic = f"{BASE_TOPIC}/{room}/{button}"
    payload = str(percentage) if percentage is not None else str(int(time.time()))
```

- [ ] **Step 6: Update `fire()` and the debounce-timer construction to pass percentage through**

Replace:
```python
def fire(room, button, dry_run):
    print(f"firing {room}/{button} after quiet period", flush=True)
    if not dry_run:
        publish(room, button)
```
With:
```python
def fire(room, button, percentage, dry_run):
    print(f"firing {room}/{button} percentage={percentage} after quiet period", flush=True)
    if not dry_run:
        publish(room, button, percentage)
```

Then in `run()`, where the timer is constructed:
```python
        with timers_lock:
            existing = pending_timers.get(key)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(QUIET_PERIOD_SECONDS, fire, args=(room, button, dry_run))
            pending_timers[key] = t
            t.start()
```
Change the `args=` tuple to `args=(room, button, percentage, dry_run)`, matching `fire()`'s new signature. `pending_timers` is now keyed by the 3-tuple `key` from Step 4, which is correct — it means a mid-cycle *change of target speed* (e.g. someone presses 33% then quickly 66% before the quiet period elapses) is treated as a **new, separate pending event** rather than collapsed into the first one, which is the correct behavior now that percentage is meaningful (the old design didn't have this problem because every speed press carried identical, meaningless payload).

- [ ] **Step 7: Verify by reading the whole file back**

Run: `cat /home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py`

Confirm: no remaining references to the old `hex_bytes`/3-byte-tuple matching, `fire()` and `publish()` both take `percentage`, `CODE_TABLE` values are 2-tuples keyed by integer `stable_id` (not 3-byte tuples).

- [ ] **Step 8: Commit**

```bash
cd /home/mark/projects/rtl_433_fan
git add fan_wallswitch_bridge.py
git commit -m "Rewrite bridge script to use validated -X OOK_PWM decoder

Replaces the -A auto-guesser (unreliable: silently failed on living
room's off state, produced jittery non-deterministic trailing bits
everywhere) with an explicit flex decoder tuned to the measured pulse
widths, validated this session against all three switches. Speed
presses now decode to an exact target percentage instead of a bare
trigger, eliminating the need for the counter helpers.

Light/power stable_ids are not yet populated - CODE_TABLE only has
speed entries until Task 3's live validation fills in the rest."
```

---

### Task 2: Deploy and confirm the bridge script decodes speed live for all three rooms

**Files:**
- Read-only reference: `/home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py` (from Task 1)
- Deploy target: `raspberrypi:/usr/local/bin/fan_wallswitch_bridge.py`

**Interfaces:**
- Consumes: `fan_wallswitch_bridge.py` from Task 1, unchanged.
- Produces: a running bridge process on the Pi whose stdout can be observed directly (this task runs it in the foreground/dry-run, not as the systemd service yet — that's Task 7).

- [ ] **Step 1: Confirm the dongle is free**

Run: `ssh raspberrypi 'ps aux | grep -i rtl | grep -v grep'`
Expected: no `rtl_433`/`rtl_test` processes (ignore unrelated `jq`/`rtlamr` processes).

- [ ] **Step 2: Copy the updated script to the Pi**

```bash
scp /home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py raspberrypi:/usr/local/bin/fan_wallswitch_bridge.py
```

- [ ] **Step 3: Run the bridge script directly (dry-run, foreground) to watch its output live**

```bash
ssh raspberrypi 'timeout 60 python3 /usr/local/bin/fan_wallswitch_bridge.py -f 304.25M -g 30 --dry-run'
```
(background this with `nohup ... & disown` and redirect to a log file, same pattern as every other capture this session, so you can trigger Bond commands from a separate tool call while it runs)

- [ ] **Step 4: Trigger all 4 speed states on dining room via Bond, confirm each decodes to the right percentage**

Use `mcp__homeassistant__HassFanSetSpeed` with `name: "Dining Room Ceiling Fan"` at 33, 66, 100, then 0. After each, check the bridge log for a `seen diningroom/speed percentage=<N>` line matching what was requested, and a `firing` line 3s later with the same percentage.

Expected: all 4 percentages match exactly what was requested, no `seen ... percentage=None` lines for these speed events.

- [ ] **Step 5: Repeat Step 4 for bedroom (`Master Bedroom Ceiling Fan`) and living room (`Living Room Ceiling Fan`, target by `name` — the duplicate-entity problem was already fixed by renaming, confirm only one match)**

Same expected outcome: all 4 percentages decode correctly for each room.

- [ ] **Step 6: If any percentage doesn't match, stop and diagnose before continuing**

This would mean the `SPEED_COUNTER_TO_PERCENTAGE` mapping or `CODE_TABLE` stable_ids from Task 1 have an error — do not proceed to Task 3 until all 12 (3 rooms × 4 states) cases pass.

---

### Task 3: Capture and add the missing light/power CODE_TABLE entries

**Files:**
- Modify: `/home/mark/projects/rtl_433_fan/fan_wallswitch_bridge.py` (add entries to `CODE_TABLE`)

**Interfaces:**
- Consumes: the running dry-run bridge process from Task 2 (keep it running, or restart the same way).
- Produces: a complete `CODE_TABLE` with all 9 room×button combinations (3 rooms × {power, light, speed}).

- [ ] **Step 1: Capture dining room light via Bond, confirm stable_id, add to CODE_TABLE**

Trigger `HassTurnOn` then `HassTurnOff` on `light.dining_room_ceiling_fan` (domain `["light"]`), confirm both produce the same code (`0004f28` expected, per this session's data), compute `stable_id = 0x0004f28 >> 5`, add `stable_id: ("diningroom", "light")` to `CODE_TABLE`.

- [ ] **Step 2: Capture living room light via Bond, add to CODE_TABLE**

Same procedure against `Living Room Ceiling Fan Light` (or whatever the current non-duplicate light entity name is — check with `mcp__homeassistant__GetLiveContext` `area: "Living Room", domain: "light"` first if unsure).

- [ ] **Step 3: Capture bedroom light via Bond, add to CODE_TABLE**

Same procedure against `Master Bedroom Ceiling Fan` (`domain: ["light"]`).

- [ ] **Step 4: Capture living room power via Bond, add to CODE_TABLE**

`HassTurnOn`/`HassTurnOff` on `Living Room Ceiling Fan` (`domain: ["fan"]`), **no** `percentage` argument — this is the one fan whose plain on/off routes through its power-toggle code via Bond.

- [ ] **Step 5: Dining and bedroom power — request one physical wall-switch press each from the user**

Bond cannot reach these two power-toggle codes (confirmed twice this session — plain on/off always routes through the speed family for these two). Ask the user to press the physical power button once on the dining room switch, then once on the bedroom switch, while the dry-run bridge log is being watched. Capture and add both `stable_id`s.

- [ ] **Step 6: Confirm all 9 CODE_TABLE entries are present**

`CODE_TABLE` should have exactly 9 keys: `{livingroom, diningroom, bedroom} × {power, light, speed}`.

- [ ] **Step 7: Re-deploy the updated script and re-run Task 2's Step 3-5 validation for power and light too (not just speed)**

Trigger power and light for all 3 rooms via Bond (and the two physical presses again if easiest), confirm each produces the expected `seen <room>/<button>` line with no unmatched codes.

- [ ] **Step 8: Commit**

```bash
cd /home/mark/projects/rtl_433_fan
git add fan_wallswitch_bridge.py
git commit -m "Complete CODE_TABLE with all 9 room/button light+power entries

All entries validated live via Bond except dining/bedroom power, which
required one physical wall-switch press each (Bond routes their plain
on/off through the speed family, not the power-toggle code)."
git push origin master
```

---

### Task 4: Update the systemd deployment script and deploy the service (not yet enabled)

**Files:**
- Modify: `/home/mark/projects/rtl_433_fan/rtl433-mqtt.sh`
- Deploy target: `raspberrypi:/usr/local/bin/rtl433-mqtt.sh`

**Interfaces:**
- Consumes: `fan_wallswitch_bridge.py` from Task 3 (already deployed to the Pi).
- Produces: an updated wrapper script ready for `rtl433-mqtt.service` to use — service itself stays disabled/inactive until Task 7.

- [ ] **Step 1: Read the current wrapper script**

Run: `ssh raspberrypi 'cat /usr/local/bin/rtl433-mqtt.sh'`

- [ ] **Step 2: Confirm no changes are actually needed to the wrapper itself**

The wrapper only sets environment variables and execs `fan_wallswitch_bridge.py`; the `-X` flex decoder change lives entirely inside the Python script's `rtl433_cmd()` (Task 1), which the wrapper doesn't touch. If `cat` in Step 1 shows the wrapper hardcodes any `rtl_433` flags itself (rather than delegating fully to the Python script), update it to remove them — the Python script now owns the full command line.

- [ ] **Step 3: Copy the (possibly unchanged) wrapper to the Pi and confirm the local copy in this repo matches**

```bash
scp /home/mark/projects/rtl_433_fan/rtl433-mqtt.sh raspberrypi:/usr/local/bin/rtl433-mqtt.sh
ssh raspberrypi 'diff /usr/local/bin/rtl433-mqtt.sh /home/mark/projects/rtl_433_fan/rtl433-mqtt.sh 2>/dev/null || echo "no local copy on Pi to diff against, that is fine"'
```

- [ ] **Step 4: Do NOT start `rtl433-mqtt.service` yet** — it has `Conflicts=rtlamr-mqtt.service rtl-tcp.service` and will stop the gas-meter services the moment it starts. Leave this for Task 7, alongside enabling the first automation, so both changes are validated together in one supervised window.

---

### Task 5: Write the new HA automations

**Files:**
- Modify (HA host): `/config/automations.yaml`

**Interfaces:**
- Consumes: MQTT topics `home/fans/<room>/{power,light,speed}` as published by the Task 3 bridge script (`speed` payload is now a percentage string, `power`/`light` payloads are still bare timestamps — only their arrival matters).
- Produces: 6 new automations, one per room × {power+light combined, speed}.

- [ ] **Step 1: Read the current automations.yaml around the deletion point to confirm placement**

Run: `ssh ha "sed -n '1330,1345p' /config/automations.yaml"`

Confirm line ~1342 is still `- id: airplay_indoor_start` (i.e. nothing else has been inserted there since the deletion earlier this session).

- [ ] **Step 2: Compose the 6 new automation blocks**

For **each room** (`livingroom`/`Living Room Ceiling Fan`+`Living Room Ceiling Fan Light` entity names, `diningroom`/`fan.dining_room_ceiling_fan_2`+`light.dining_room_ceiling_fan`, `bedroom`/`fan.master_bedroom_ceiling_fan`+`light.master_bedroom_ceiling_fan` — confirm exact current entity_ids with `mcp__homeassistant__GetLiveContext` before writing these, since living room's entity_id changed when the duplicate was renamed):

```yaml
- id: '<new unique id, e.g. 1786000000001>'
  alias: Fan wall switch - <Room> power+light toggle
  enabled: false
  description: Physical power button is a master fan+light toggle (confirmed
    2026-07-30) - both entities are toggled together, matching real hardware
    behavior, rather than the old design's independent power/light automations.
  triggers:
  - trigger: mqtt
    topic: home/fans/<room>/power
  conditions: []
  actions:
  - action: fan.toggle
    target:
      entity_id: <fan entity id>
  - action: light.toggle
    target:
      entity_id: <light entity id>
  mode: single
- id: '<new unique id>'
  alias: Fan wall switch - <Room> speed set
  enabled: false
  description: Speed presses now decode to an exact target percentage (see
    fan_wallswitch_bridge.py) - sets it directly, no local counter needed.
  triggers:
  - trigger: mqtt
    topic: home/fans/<room>/speed
  conditions: []
  actions:
  - choose:
    - conditions:
      - condition: template
        value_template: "{{ trigger.payload | int == 0 }}"
      sequence:
      - action: fan.turn_off
        target:
          entity_id: <fan entity id>
    default:
    - action: fan.turn_on
      target:
        entity_id: <fan entity id>
      data:
        percentage: "{{ trigger.payload | int }}"
  mode: single
```

Note the old design had a separate light-only automation too; that's intentionally dropped here since the light button's own presses already publish to `home/fans/<room>/light` — add that trigger back only if you want light-button presses to *also* re-toggle the light (they shouldn't need to, since the light button press already changed the physical light state and Bond has no way to know that either — this is the same fundamental "toggle-only, no ground truth" limitation discussed this session, out of scope for this plan to solve, just don't make it worse by double-toggling). For v2, publish a light automation identical in shape to the power one but only calling `light.toggle`:

```yaml
- id: '<new unique id>'
  alias: Fan wall switch - <Room> light toggle
  enabled: false
  description: Corrects <Room> light assumed state when the RF wall switch
    light button (not wired to Bond) is used.
  triggers:
  - trigger: mqtt
    topic: home/fans/<room>/light
  conditions: []
  actions:
  - action: light.toggle
    target:
      entity_id: <light entity id>
  mode: single
```

So: 3 automations per room (power+light combined, light-only, speed) × 3 rooms = 9 total, all `enabled: false` initially.

- [ ] **Step 3: Insert the 9 automation blocks**

Write the full YAML (all 9 blocks from Step 2, with real entity IDs substituted) to a local scratch file, then insert it into `/config/automations.yaml` right before the `- id: airplay_indoor_start` line found in Step 1. **This edits live production config — confirm with the user before running the insert command**, per the Global Constraints.

- [ ] **Step 4: Validate the YAML**

```bash
ssh ha "ha core check 2>&1 | tail -20"
```
Expected: `Command completed successfully.` If it fails, fix the YAML and re-check before proceeding — do not leave broken YAML in a live config file.

- [ ] **Step 5: Reload automations in HA**

Ask the user to reload via the UI (Developer Tools → YAML → Automations), same as after the deletion earlier this session — no direct API/token access, per this session's established boundary.

- [ ] **Step 6: Confirm the automatic git commit captured the change**

```bash
ssh ha "cd /config && git log --oneline -3"
```
Expected: a new `automations.yaml` commit from `Home Assistant Version Control`.

---

### Task 6: Remove the obsolete speed-level counters

**Files:**
- Modify (HA host): `/config/counters.yaml`
- Modify (HA host): `/config/configuration.yaml` (only if the `counter: !include counters.yaml` line needs removing too — check first, it likely doesn't need to change since an empty/near-empty include is valid)

**Interfaces:**
- Consumes: nothing (independent of the other tasks — safe to do any time after confirming the new speed automations don't need them, i.e. after Task 5).

- [ ] **Step 1: Confirm nothing else references these counters**

```bash
ssh ha "grep -rn 'livingroom_fan_speed_level\|diningroom_fan_speed_level' /config/*.yaml"
```
Expected: only the definitions in `counters.yaml` itself (the new automations from Task 5 don't reference them). If anything else references them, stop and investigate before deleting.

- [ ] **Step 2: Get explicit user approval, then empty counters.yaml**

This is a separate destructive edit to live config — ask before running, same as the automation deletion earlier this session.

```bash
ssh ha "echo '{}' > /config/counters.yaml"
```

- [ ] **Step 3: Validate and confirm the auto-commit**

```bash
ssh ha "ha core check 2>&1 | tail -20"
ssh ha "cd /config && git log --oneline -3"
```

- [ ] **Step 4: Ask the user to reload Helpers in the HA UI (Settings → Devices & Services → Helpers), or restart Core if a live reload option isn't available for counters specifically**

---

### Task 7: Enable one automation at a time with live validation, matching the caution from the 2026-07-30 incident

**Files:** none (all changes already deployed by Tasks 1-6; this task is purely operational/testing)

**Interfaces:** none new.

- [ ] **Step 1: Start `rtl433-mqtt.service` on the Pi**

```bash
ssh raspberrypi 'sudo systemctl start rtl433-mqtt.service && sleep 2 && systemctl is-active rtl433-mqtt.service'
```
Expected: `active`. This will also stop `rtlamr-mqtt.service`/`rtl-tcp.service` (the `Conflicts=` directive) — expected and already the accepted state per this project's prior sessions (gas meter services left stopped indefinitely).

```bash
ssh raspberrypi 'journalctl -u rtl433-mqtt.service -n 20 --no-pager'
```
Confirm it started cleanly, no Python tracebacks.

- [ ] **Step 2: Enable the lowest-risk automation first — living room speed**

In the HA UI, enable only `Fan wall switch - livingroom speed set`. Leave the other 8 disabled.

- [ ] **Step 3: One supervised live test — press the living room switch's speed button once for real**

Watch `ssh raspberrypi 'journalctl -u rtl433-mqtt.service -f'` while the physical button is pressed. Confirm exactly one `seen`/`firing` pair with the correct percentage, and confirm via `mcp__homeassistant__GetLiveContext` that the fan's real percentage now matches.

- [ ] **Step 4: Enable and test the remaining 8 automations one at a time, same procedure**

Order: living room light, living room power+light, dining speed, dining light, dining power+light, bedroom speed, bedroom light, bedroom power+light. Do not enable the next one until the current one has passed a real physical-button test.

- [ ] **Step 5: Update FAN_WALLSWITCH_SYNC.md to reflect the new live system**

Replace the "Known decoded codes" table, the `counter` helper description, and the "Current status: blocked" section (now resolved) with the new architecture — mirror the structure already used in `README.md`'s "Quick reference" table.

```bash
cd /home/mark/projects/rtl_433_fan
git add FAN_WALLSWITCH_SYNC.md
git commit -m "Document the live v2 wall-switch sync system

Replaces the counter-helper design (obsolete) with direct
percentage decoding. All 9 automations enabled and validated
against real physical button presses, one at a time."
git push origin master
```
