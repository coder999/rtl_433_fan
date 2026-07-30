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
machine, not this HA host; also on GitHub at `coder999/rtl_433_fan`). Read that first
for background — this file is about the HA-side integration and its current problem,
not the RF work.

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
  machine (git repo, also pushed to GitHub as `coder999/rtl_433_fan`) —
  `fan_wallswitch_bridge.py` and `ha_fan_wallswitch_sync.yaml`

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

### Bedroom switch, identified (RF decode not yet done)

Confirmed 2026-07-29 by popping the switch off the wall plate: model **TR223A**,
FCC ID **KUJCE10321** (Chungear Industrial Co.) — genuinely different hardware from
the 98139 units used in the living/dining rooms, not just a farther-away unit of the
same model. Its FCC filing includes hold-to-dim light control and natural-wind/timer
modes the 98139 switches don't appear to have. Actual RF decode work for this switch
happens in the `rtl_433` repo, not here — see that repo's README for status.

### Automation behavior (as originally designed — see "What we got wrong" below)

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

**This design has known errors — see "What we got wrong today" below before trusting
any of the above.**

## ⚠️ Current status (2026-07-30): blocked on Bond itself, not just the wall-switch bridge

All 6 "Fan wall switch" automations are **disabled**. What started as a debounce bug in
the RF bridge turned out to be layered on top of a deeper, currently-blocking problem:
**Bond can't control the living room fan at all right now**, independent of anything
wall-switch related. Session paused for the day; resume tomorrow.

### Debounce fix: validated, working

The original leading-edge debounce bug (remote sends ~4 repeat RF bursts ~2s apart per
tap; each burst independently fired the automation, causing ~4 rapid Bond commands in
6-8 seconds, which desynced the living room fan's real speed from Bond's assumed
state) is fixed. `fan_wallswitch_bridge.py` now uses trailing-edge debounce
(`QUIET_PERIOD_SECONDS = 3.0`, restart-on-repeat `threading.Timer` per (room,button)).

- Deployed to the Pi (`/usr/local/bin/fan_wallswitch_bridge.py`), confirmed via md5sum.
- **Offline-validated** against all 18 saved RF captures in `~/fan_rf_captures/`:
  every capture that decoded a matching code produced exactly one `firing` line,
  including ones with up to 12 repeat "seen" bursts. (8 of 18 captures decoded nothing
  at all — likely weak/empty recordings, unrelated to the debounce logic.)
- **Live-hardware-validated 2026-07-30**: pressed each living room button (power,
  light, speed) once for real. All three collapsed multiple "seen" bursts into exactly
  one "firing" + one MQTT publish. One speed-button press initially produced zero
  detections (no signal at all, matching the pattern seen in offline captures); a
  second attempt worked normally.

**This part of the fix is done and doesn't need to be revisited.**

### What we got wrong today (found via live automation testing)

With the debounce fix validated, the two lowest-risk automations (living room power +
light toggle) were briefly re-enabled for one supervised live test each. That surfaced
three separate, previously-undocumented problems:

1. **Speed cycle direction is backwards in the automation.** The automation counts
   *up*: 33%→66%→100%→off. Real hardware counts *down* from off: off→**100%**→66%→
   33%→off. Confirmed directly by the user. This needs fixing in the automation logic
   regardless of which speed-automation design (active-set / passive / guardrail) gets
   used — see Open Questions.
2. **The physical "power" button is a master fan+light toggle, not fan-only.**
   Confirmed both by direct user observation of the wall switch's real behavior, and
   independently by HA: calling `fan.toggle` on `fan.living_room_ceiling_fan_2`
   through Bond also flipped `light.living_room_ceiling_fan`'s state at the exact same
   timestamp, even though the light automation was disabled and no light RF code was
   seen. The current design (fully independent power/light automations) doesn't match
   this — needs a redesign, likely a single combined automation, or the power
   automation needs to also drive the light entity to match.
3. **Bond can't currently deliver commands to the living room fan at all.** After the
   power-toggle automation fired once (a real `fan.toggle` call), Bond/HA showed the
   fan on/100% and light on, but the physical fan and light were confirmed still off.
   Two more real commands were tried in isolation to debug this (`fan.toggle` again,
   then an explicit `fan.turn_on`) — **both had zero physical effect**, despite HA
   reporting success each time. This is not the debounce/multi-fire bug; it's a single
   clean command failing to do anything, repeatedly. Diagnosis so far:
   - **Not a network/connectivity issue**: the Bond Bridge (`192.168.0.110`) is online
     and reachable — responds `200` on its local API (`/v2/sys/version`).
   - **Not an HA/automation-specific issue**: controlling the fan directly from the
     Bond app also failed to move it, ruling out anything in our HA automation/
     integration layer as the cause.
   - So the fault is somewhere between Bond and the fan's RF receiver, or the fan
     itself.
   - **User's response**: deleted the fan's device pairing in Bond and started
     re-adding it from scratch. Re-pairing requires repeatedly pressing buttons on the
     fan's remote so Bond can learn the signal. That repeated pressing has now left
     **the remote itself non-functional** — it stopped responding entirely partway
     through re-pairing.

### State as of pausing (2026-07-30)

- Bond's fan device for the living room: **deleted, re-pairing not completed.**
- The remote used for Bond pairing: **not functioning.**
- All 6 wall-switch automations: **disabled** (the two briefly re-enabled for testing
  were disabled again once the command-drop problem was found).
- HA's displayed state was manually corrected to match physical reality before
  pausing: `fan.living_room_ceiling_fan_2` off/0%, `light.living_room_ceiling_fan`
  off, `counter.livingroom_fan_speed_level` reset to `0`. (These corrections used the
  raw-state-override debug technique, which was empirically confirmed today to be
  side-effect-free — see Open Questions.)
- Dining room was not touched this session; still fully out of scope pending living
  room being stable end-to-end.

## Immediate TODO

Rough priority order — **everything below is blocked on the first item**:

1. **Get Bond back to being able to control the living room fan at all.** This means,
   in some order: get or improvise a working remote (repair/replace the broken one, or
   find another way to let Bond learn the fan's RF protocol), and complete the Bond
   device re-pairing that was left mid-way. Nothing else here can be tested until this
   works — confirm via the Bond app directly, independent of HA, before touching any
   automation again.
2. **Fix the speed automation's direction bug** (`fan_wallswitch_bridge.py`/
   `ha_fan_wallswitch_sync.yaml` percentage math currently computes
   `(counter+1)*33` ascending; needs to model off→100→66→33→off descending instead).
3. **Redesign the power/light automation split** to account for the power button's
   real master-toggle behavior (currently two fully independent automations, which is
   wrong).
4. Once 1-3 are done, **redo the supervised live test sequence**: power, then light
   (or their combined replacement), then speed — same one-press-at-a-time,
   watch-the-logs approach used today, since the debounce fix itself is already
   validated and doesn't need to be re-proven.
5. Dining room: same process, after living room is fully stable.

## Open questions / things worth reconsidering

- **Is "toggle" fundamentally unreliable for state-correction here?** `fan.toggle`/
  `light.toggle` decide direction by reading HA's *current believed* state and
  commanding the opposite. If that believed state is already stale — which is exactly
  the scenario these automations exist to fix — toggle can compute the wrong
  direction, or behave unpredictably. Explicit `turn_on`/`turn_off` avoids that
  particular failure mode, but doesn't solve the deeper problem: the wall-switch RF
  event only tells us *a change happened*, not what the resulting real state is,
  unless the fan's true prior state was already known — which is the same visibility
  gap this whole project exists to close. Worth rethinking the automation logic here,
  not just swapping toggle for turn_on/turn_off.
- **Should the speed automation actively command Bond at all?** This was already an
  open question after the original debounce bug, and today's finding that Bond can
  drop even a single, non-rapid-fire command makes the case for active-set *weaker*,
  not stronger — the risk isn't limited to rapid bursts, any single command can
  silently fail with no feedback to HA. A passive/log-only design (no
  `fan.turn_on`/`turn_off` calls, just track/notify) avoids ever pushing the fan to a
  wrong physical speed, at the cost of the displayed speed not self-correcting.
- **Is 3.0s of debounce quiet-period actually enough long-term?** Validated against
  one offline dataset and confirmed live today, but still based on limited samples of
  real burst timing.
- **Debug state overrides (`POST /api/states/...`, no service call) were empirically
  confirmed safe today** — set `light.living_room_ceiling_fan` to `on` via raw
  override only; physical light did not respond, confirming the technique is inert
  and doesn't reach Bond/the device. This validates the resync technique used
  throughout this project (e.g. TODO #1 originally, and today's end-of-session
  corrections).
- **Bedroom switch still isn't decoded** — see the `rtl_433` repo, unrelated to any of
  the above.
