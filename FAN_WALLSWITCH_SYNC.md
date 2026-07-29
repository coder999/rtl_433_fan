# Fan Wall Switch → Home Assistant State Sync

## What this is

Three Ashby Park 52" ceiling fans (Hampton Bay, Model 59252) are controlled through a
Bond Bridge in this Home Assistant instance. Each fan also has a physical wall switch
(Hampton Bay model 98139) that talks to the fan directly over RF at 304.25MHz — it is
**not wired** and Bond has no visibility into it. When someone uses the wall switch,
the real fan state changes but Bond/HA's assumed state does not, so they drift out of
sync.

The fix: a Raspberry Pi (`ssh raspberrypi` from the main dev machine, on the same LAN)
runs an RTL-SDR listening for the wall switches' RF codes, republishes clean MQTT
events, and Home Assistant automations react to those events to correct Bond's assumed
state.

Full RF reverse-engineering history, protocol details, and the FCC research trail live
in a separate git repo: **`/home/mark/projects/rtl_433/README.md`** (on the main dev
machine, not this HA host). Read that first for background — this file is about the
HA-side integration and its current problem, not the RF work.

## Architecture

```
wall switch (RF, 304.25MHz)
  -> Pi's RTL-SDR + rtl_433 (analyzer mode, -A)
  -> /usr/local/bin/fan_wallswitch_bridge.py (parses rtl_433 text output,
     matches known codes, debounces, publishes MQTT)
  -> MQTT broker (this HA host, 192.168.0.100:1883 - same broker used by
     the unrelated gas-meter project, see /etc/default/meter-mqtt on the Pi
     for credentials)
  -> topics: home/fans/<room>/<button>  where room=livingroom|diningroom,
     button=power|light|speed
  -> HA automations (automations.yaml, aliased "Fan wall switch - ...")
     trigger on those MQTT topics and call Bond services to correct state
```

Pi-side files:
- `/usr/local/bin/fan_wallswitch_bridge.py` — the parser/matcher/publisher
- `/usr/local/bin/rtl433-mqtt.sh` — systemd ExecStart wrapper, sets env vars
- `rtl433-mqtt.service` (systemd, `Conflicts=rtlamr-mqtt.service rtl-tcp.service`
  since there's only one RTL-SDR dongle, shared with the unrelated gas meter project)
- Source of truth / canonical copies: `/home/mark/projects/rtl_433/` on the main dev
  machine (git repo) — `fan_wallswitch_bridge.py` and `ha_fan_wallswitch_sync.yaml`

HA-side (this host, `/config`, also git-tracked):
- `configuration.yaml` — has `counter: !include counters.yaml` added
- `counters.yaml` — two counter helpers, see below
- `automations.yaml` — 6 automations, ids `1785343046230`-`1785343046235`, all
  aliased `Fan wall switch - <Room> <button> toggle`

### Known decoded codes (living room + dining room only; bedroom not yet decoded)

| Room | Address | power | light | speed |
|---|---|---|---|---|
| Living room | 0x01 | 0xCC | 0xF8 | 0xFF |
| Dining room | 0x02 | 0x26 | 0x3C | 0x3F |

### Entity IDs

- `fan.living_room_ceiling_fan_2` / `light.living_room_ceiling_fan`
- `fan.dining_room_ceiling_fan_2` / `light.dining_room_ceiling_fan`

### Automation behavior

- **power/light toggle**: calls `fan.toggle` / `light.toggle` directly — mirrors the
  physical switch exactly, no state tracking needed.
- **speed toggle**: the physical button cycles the fan through 4 states, but always
  sends the *same* RF code regardless of resulting speed (confirmed via RE) — it's a
  relative "advance" command, not per-level codes. Since Bond has no visibility into
  wall-switch RF either, there's no way to ask Bond what the real speed is. So a
  `counter` helper (`counter.livingroom_fan_speed_level` /
  `counter.diningroom_fan_speed_level`, range 0-3) tracks it locally: on each
  speed-toggle event, increment the counter (wrapping 3→0 via `counter.reset` +
  `fan.turn_off`), and call `fan.turn_on` with `percentage = counter_value_before_increment
  * 33` to match. This actively commands Bond, which is why the bug below matters.

## ⚠️ Current status: automations disabled, real bug found, needs fixing

**As of 2026-07-30, all 6 "Fan wall switch" automations are disabled**
(`enabled: false` added to each entry in `automations.yaml`) because live testing
uncovered a real bug that actively mis-set the living room fan's real speed.

### The bug

`fan_wallswitch_bridge.py` used a *leading-edge* debounce (ignore repeats within N
seconds of the first-seen match) intended to collapse one physical button
press/hold into a single MQTT event. Two problems were found testing live against the
real fans:

1. **The remote sends ~4 separate repeat-bursts about 2 seconds apart per single
   tap** — not one continuous burst while held. This is inherent transmitter behavior
   (probably a reliability/persistence mechanism), not related to how long the button
   is physically held. A leading-edge debounce shorter than that ~2s gap lets each
   burst independently trigger the automation, so **one physical press fired the
   automation ~4 times in ~6-8 seconds.**
2. For the speed automation specifically, 4 rapid `fan.turn_on`/`fan.turn_off` calls
   in that short a window caused **Bond's assumed state to desync from the real
   fan** — almost certainly because the physical fan/receiver rate-limits or drops
   closely-spaced RF commands from Bond, while Bond has no feedback channel and just
   assumes each command it sent succeeded. Observed: real fan physically at 100%,
   Bond/HA showing 0%/off, after what was supposed to be one clean test press.

This means the speed automation's "actively command Bond to match" design is riskier
than first thought: a desync doesn't just mean a wrong dashboard reading anymore, it
means the automation can actively push the physical fan to the wrong speed. Worth
reconsidering whether to keep the active-set design at all (see Open Questions).

### The fix in progress (drafted, NOT yet deployed/validated)

`fan_wallswitch_bridge.py` was rewritten to use *trailing-edge* debounce instead: on
each matching packet, (re)start a timer; only publish once the timer expires with no
new matching packet arriving (i.e. wait for actual silence, using
`threading.Timer`, keyed per (room,button), cancelling/restarting on every repeat).
`QUIET_PERIOD_SECONDS` was bumped from an initial `1.0` (still too short — this is
what caused the 4x-fire incident) to `3.0` (should comfortably bridge the observed
~2s gap, but this is based on a **single data point** — the remote might occasionally
have longer gaps or send more than 4 bursts for a longer real hold).

**This updated version has been edited into the repo copy
(`/home/mark/projects/rtl_433/fan_wallswitch_bridge.py`) but has NOT been redeployed
to the Pi or re-tested.** The Pi's `rtl433-mqtt.service` was stopped entirely as a
safety measure and has not been restarted.

## Immediate TODO

Rough priority order:

1. **Resync the living room fan's HA state to reality** (real fan was left at
   physical 100% while HA showed 0%/off, from the bug incident). Two corrections
   needed, both require genuine service-call access (Developer Tools > States does
   NOT reliably persist for `counter` helpers — confirmed the hard way):
   - `counter.set_value` on `counter.livingroom_fan_speed_level` → `3`
   - Correct `fan.living_room_ceiling_fan_2`'s displayed state to `on` / 100% —
     for a normal integration-backed entity (unlike counter helpers) Developer
     Tools > States "Set State" genuinely does work for this without sending a
     real command to the device, since it's just a debug override of HA's cache
     and Bond won't proactively overwrite it without a new command being sent.
   - Double check `counter.diningroom_fan_speed_level` (should be `2`, matching
     the dining room fan's last known 66%) — probably still correct, wasn't
     touched during the buggy testing, but verify.
2. **Deploy the updated `fan_wallswitch_bridge.py`** (trailing-edge debounce,
   `QUIET_PERIOD_SECONDS=3.0`) to the Pi:
   ```
   scp /home/mark/projects/rtl_433/fan_wallswitch_bridge.py raspberrypi:/tmp/
   ssh raspberrypi 'sudo install -m 755 -o root -g root /tmp/fan_wallswitch_bridge.py /usr/local/bin/fan_wallswitch_bridge.py'
   ```
3. **Validate offline first**, not against the real fans — the Pi has saved raw I/Q
   captures for every known button at `~/fan_rf_captures/<room>_<button>/*.cu8`.
   Dry-run against those:
   ```
   ssh raspberrypi 'python3 /usr/local/bin/fan_wallswitch_bridge.py --dry-run -r ~/fan_rf_captures/livingroom_power/g001_304.25M_2048k.cu8'
   ```
   Confirm exactly one "firing" line per file.
4. **Start the service** and do ONE very carefully monitored live test with
   automations still disabled — watch `journalctl -u rtl433-mqtt.service -f` on the
   Pi in real time while pressing a button once, and confirm exactly one
   "seen"→"firing" cycle (or at least that repeat bursts are all successfully
   bridged into one firing) before considering it safe.
5. Only then **re-enable the automations** (remove `enabled: false` from the 6
   entries in `automations.yaml`, or flip them on in the UI) and do one more
   careful supervised real test per automation type.

## Open questions / things worth reconsidering

- **Is 3.0s actually enough?** Only one real burst-timing sample exists. If a longer
  hold or a different remote unit sends bursts spaced further apart, 3.0s could still
  under-debounce. Consider either a larger safety margin (5s+) or capturing more
  timing samples before trusting this.
- **Should the speed automation actively command Bond at all?** Given a desync in the
  active-set design causes *worse* real-world harm (wrong physical fan speed) than the
  original problem (just a wrong dashboard reading), a passive design — log/notify on
  a speed-toggle event without calling `fan.turn_on`/`turn_off` — might be the safer
  long-term choice, accepting that the displayed speed won't self-correct. This
  tradeoff was raised with the user but not conclusively resolved before automations
  were disabled for safety.
- **Bedroom switch still isn't decoded** (different SKU/frequency suspected — see the
  rtl_433 project README) and isn't part of any of this yet.
- **Power/light toggle automations were probably also affected** by the multi-fire bug
  (same debounce code path) but appeared to work correctly in testing purely by luck —
  an even number of extra `fan.toggle`/`light.toggle` calls nets back to the same
  visible state, masking the bug. Don't assume they're fine just because they looked
  fine; they need the same re-validation as speed once the fix is deployed.
