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
- **Bedroom switch decode in progress (2026-08-15)** — light and power buttons
  confirmed (`0x7C`, `0x66` on address `0x0002`); speed button's meaning
  (relative-advance vs. per-level absolute code) is unresolved — see the `rtl_433_fan`
  repo's README for the open question and two queued tests (a clean RF-only trailing-
  bits comparison, and a Bond-side test below).
- **RESOLVED (2026-08-15): the `counter`-helper design above is obsolete.** A same-day
  Bond RF audit (see `rtl_433_fan` repo README, "Bond RF audit") confirmed the speed
  button's trailing bits are the literal absolute target speed percentage — `111`=off,
  `100`=33%, `1001`=66%, `110`=100% — the same encoding Bond itself uses when
  commanding a specific percentage. **The wall-switch bridge can decode the resulting
  speed directly from the RF packet** and call `fan.turn_on`/`turn_off` with the exact
  right percentage — no counter helper, no guessing, no "trigger a Bond status poll and
  hope" fallback needed. This should replace the whole `counter.<room>_fan_speed_level`
  approach for living/dining, not just apply to a future bedroom automation. Also
  discovered: Bond itself transmits the identical 304.25MHz protocol as the wall
  switches (not a separate protocol) — worth keeping in mind for the "single source of
  truth" idea below, since Bond and the wall switch are indistinguishable at the RF
  level, not two systems that need reconciling at that layer.
- **Should there be one canonical "true fan state" HA entity that both Bond and the
  wall-switch RF events reconcile into, instead of the wall-switch side trying to
  independently track/correct Bond's state?** (User idea, 2026-08-15, prompted by
  recalling that Bond has previously accepted a "set state" command from HA without it
  taking physical effect — see "Bond can't currently deliver commands..." above — so
  Bond's own displayed/assumed state can't be fully trusted as ground truth either.)
  The existing `counter.<room>_fan_speed_level` helpers are a first pass at this same
  idea, but only track the wall-switch side; this would be broader — a single entity
  both the wall-switch bridge and Bond-state-change events update, that automations
  and dashboards read instead of trusting Bond's fan entity directly. Not designed yet;
  revisit once the speed-button RF question above is settled, since the reconciliation
  design depends on whether the wall switch can report *which* speed it was pressed
  for or only that a press happened.
  - **Related test proposed by user, not yet run:** set the fan to a known state using
    only the wall switch (Bond untouched), then command Bond to a specific speed. If
    the real fan was already out of sync with whatever Bond internally tracks, the Bond
    command should fail to land on the intended real speed. Would show whether Bond's
    own speed-setting logic is itself relative/assumed-state-based (equally vulnerable
    to the desync problem this whole project exists to fix) or genuinely absolute.

## v2 implementation session (2026-08-15 evening): executed, then hit a real blocker

Executed the plan at `rtl_433_fan/docs/superpowers/plans/2026-08-15-fan-wallswitch-sync-v2.md`
end to end through Task 6 (bridge script rewrite using the validated `-X OOK_PWM`
decoder, full 9-entry CODE_TABLE, new automations replacing the counter-helper design,
old counters removed). Task 7 (enable + live-validate one at a time) surfaced a real
architectural problem that blocked completion. Full account below; **a fresh,
self-contained plan for resuming tomorrow is at
`docs/superpowers/plans/2026-08-16-bond-tracked-state.md`** — read that first, this
section is background/history, not instructions.

**Bug found while enabling automations:** all 9 new automations initially showed as
`unavailable` in HA (never fired, red warning badge). Root cause: an explicit
`enabled: true`/`enabled: false` key in automation YAML is not handled correctly by
this HA version (2026.8.2) - confirmed by diffing against a UI-created test automation
in the same file (which had no `enabled:` key and loaded fine). Fix: removed the
`enabled:` key from all 9 (and the 3 "remember last speed" automations added later);
they now default to on, so per-automation enable/disable has to happen some other way
if needed (entity registry `disabled_by`, not the YAML key).

**Bigger bug found during the first live test:** enabling the automations (which used
`fan.toggle`/`light.toggle`/`fan.turn_on` — the same services a normal Bond-app control
uses) caused a real, disruptive infinite on/off loop on a physical light. Root cause:
**Bond transmits the identical RF protocol as the wall switches** (established earlier
this session, "Bond RF audit" in the `rtl_433_fan` README) — so calling a normal Bond
control service in response to a detected wall-switch press makes Bond transmit RF that
looks, to our own always-on RF receiver, exactly like a new physical press. That
re-triggers the automation, which calls the service again, which transmits again.... On
top of the software loop, there's a **physical correctness problem independent of our
own receiver**: since power/light are pure toggle pulses at the RF level (not
"set to explicit state"), the real device receiver toggles again on the redundant Bond
command, undoing the user's actual press, regardless of whether anything is listening.

**Attempted fix (2026-08-15 night): Bond's "tracked state" services.** Researched and
confirmed (Bond's own Local API docs, HA's Bond integration docs, and the `bond-async`
library source) that Bond has a dedicated mechanism for exactly this: `PATCH
/v2/devices/<id>/state` (the same one Bond's own mobile app uses for its "Fix Tracked
State" feature) updates Bond's belief about a device's state **without transmitting
any RF**. HA exposes this as `bond.set_fan_speed_tracked_state` and
`bond.set_light_power_tracked_state`. Rewrote all 9 automations to call these instead
of `toggle`/`turn_on`, added 3 new `input_number` helpers (`<room>_last_fan_speed`)
plus 3 new automations to keep them accurate (state-triggered on each fan's percentage
attribute becoming non-zero), so the power-toggle automation can resume the fan to its
real last speed on power-on, matching the physical fan's own confirmed
resume-last-speed behavior (tested live on the bedroom fan tonight: powered on from
fully off, real fan resumed at its last speed, not a fixed default). All of this
deployed and structurally validated (YAML, `ha core check`, byte-diff against what was
tested).

**Live-tested via the automation editor's "Run" action (bypasses the RF/MQTT trigger
path entirely, so didn't need the Pi, which was already turned off for the night):
`bond.set_light_power_tracked_state` still caused a real, physical light to turn on.**
This contradicts every source consulted (Bond's own docs, HA's service description,
the `bond-async` library's routing logic, which does correctly route the
`SET_STATE_BELIEF` action to `PATCH /state` not `/actions/`). The deployed YAML was
verified correct (right service name, right entity, right template) via direct
inspection of the live file - not a typo or config mistake on the implementation side.
Root cause not yet found. **Stopped live testing for the night after this — two real
physical disruptions in one evening is enough; further blind testing needs a safer,
more isolated diagnostic approach, not more guessing against real devices.**

**New resource for tomorrow:** the user installed a dedicated Bond MCP tool overnight,
authenticated directly against the Bond Bridge's local API (key sourced via 1Password),
independent of Home Assistant's integration layer entirely. This should let tomorrow's
session test Bond's *actual* local API behavior directly - isolating whether the bug is
in HA's Bond integration's translation layer, or in Bond's own firmware/API not
honoring the belief-only semantics its docs describe for this device/action
combination. Also worth checking: the Bond app's Advanced Settings has both **"Fix
Tracked State"** (manual one-off correction, tap-through UI) and **"Trust Tracked
State"** (toggle, confirmed ON in this account already - "the Bond Bridge will not
transmit the toggle command if the device is already in the desired state") - the
latter is a different mechanism (transmission-skipping based on belief match, not a
dedicated no-transmit write path) and may be worth understanding fully before
concluding tomorrow's local-API test is a clean apples-to-apples comparison.

**State left overnight:** `rtl433-mqtt.service` on the Pi is **stopped** (not just the
Pi being off) - this was deliberate, to break the feedback loop, and should stay
stopped until the tracked-state bug is actually resolved. All 12 new/rewritten
automations are enabled in HA, but harmless while the service is stopped, since nothing
publishes to the MQTT topics they trigger on without it running.

## ✅ RESOLVED (2026-08-16): root cause found, fix deployed, all 9 automations live-validated

Executed `docs/superpowers/plans/2026-08-16-bond-tracked-state.md` end to end. Summary
for anyone who doesn't need the full plan file's detail:

**Root cause of the tracked-state bug:** confirmed via direct comparison - Bond's own
raw local API (`PATCH /v2/devices/<id>/state`, same mechanism as the app's "Fix Tracked
State") genuinely does not transmit RF (re-confirmed 2026-08-15 night on the bedroom
fan, both via the Bond app's UI and a raw API call). **HA's
`bond.set_fan_speed_tracked_state` / `bond.set_light_power_tracked_state` services do
transmit real RF**, contradicting their own documentation - the bug is specifically in
HA's Bond integration layer (exact line of HA integration source not identified; not
needed once the working alternative was confirmed).

**Fix: bypass HA's Bond integration entirely.** Added to the HA host's
`/config/configuration.yaml`:
```yaml
rest_command:
  bond_set_state:
    url: "http://192.168.0.110/v2/devices/{{ device_id }}/state"
    method: PATCH
    headers:
      BOND-Token: !secret bond_token
      Content-Type: "application/json"
    payload: "{{ body }}"
```
(`bond_token` added to `/config/secrets.yaml`, sourced from 1Password
`op://CLI/bond-bridge-local/credential` - see `bond_api.md` in this repo for the full
raw-API reference.) Rewrote all 9 "Fan wall switch" automations
(`1786000000001`-`009`) to call `rest_command.bond_set_state` instead of the broken
tracked-state services, converting the wall switch's percentage payloads (0/33/66/100)
to Bond's native 1-3 speed-step range (all three fans confirmed `max_speed: 3` via
`GET .../properties`). `rest_command` doesn't hot-reload, so this required one HA Core
restart (explicit user go-ahead obtained first, per the standing constraint from last
night).

**Second, unrelated bug found and fixed along the way:** the user renamed all fan/light
entities the night before (2026-08-15) for naming consistency but the rename never made
it into `automations.yaml`. Automations `1,2,4,5` (the Bond-calling ones) and `10,11`
(the livingroom/diningroom "remember last speed" trackers) still referenced the old,
now-nonexistent entity_ids (`fan.living_room_living_room_ceiling_fan`,
`fan.dining_room_ceiling_fan_2`, `light.dining_room_ceiling_fan`), which silently broke
their `is_state()`/condition checks - always-false conditions, so e.g. the power toggle
would never correctly detect "currently on." Bedroom was unaffected (its entity_ids
didn't change in the rename). Fixed via global string replacement, re-validated
structurally, redeployed, confirmed via live HA state queries. **Current correct
entity_ids:**

| Room | HA fan entity_id | HA light entity_id | Bond device_id |
|---|---|---|---|
| Living room | `fan.living_room_ceiling_fan` | `light.living_room_ceiling_fan` | `ce4d90389da6937f` |
| Dining room | `fan.dining_room_ceiling_fan` | `light.dining_room_ceiling_fan_light` | `33c72108a1a2548d` |
| Bedroom | `fan.master_bedroom_ceiling_fan` | `light.master_bedroom_ceiling_fan` | `3e9252a7323111d2` |

One more stale automation was found with the same old entity_id (`1779048188186`,
"Dining Room Scene 2 Cycle Fan Speed", an unrelated Z-wave scene-controller automation)
- **left untouched, out of scope for this plan**, but it's almost certainly also broken
by the same rename and worth fixing separately.

**Operational gotcha found on the Pi:** starting `rtl433-mqtt.service` after it's been
stopped a while can stall for up to ~90 seconds if `rtl-tcp.service` (the unrelated
gas-meter project's RTL-SDR server) is running - the unit's `Conflicts=` directive
correctly triggers a stop of `rtl-tcp.service`, but that service didn't respond to
SIGTERM promptly and had to wait out systemd's default `TimeoutStopSec` before being
force-killed. `rtl433-mqtt.service` crash-loops with `status=2/INVALIDARGUMENT` (device
busy) every ~10s during that window - benign, just wait it out.

**False-positive RF trigger on startup:** immediately after `rtl433-mqtt.service` came
up, it fired one spurious `livingroom/power` event with no real button press - most
likely an SDR power-on/PLL-lock transient (matches a "PLL not locked!" warning logged by
`rtl_tcp` earlier). This flipped Bond's believed living room fan state to
on/100% while the physical fan was actually off. Caught by checking physical state
against belief before starting the room-by-room test, corrected via a raw no-RF PATCH.
**Worth treating the first event after any service (re)start with suspicion** - verify
against physical reality before trusting it.

**All 9 automations live-validated one at a time** (real physical wall-switch presses,
watching both the physical device and HA's believed state each time): living room
speed/power/light, dining room power/speed/light, bedroom power/speed/light. **Zero
false RF transmissions, zero double-toggles observed anywhere.** One live mismatch was
found and diagnosed as a known, pre-existing, self-healing limitation rather than a new
bug: bedroom's `input_number.bedroom_last_fan_speed` helper had gone stale (held `66`
from a manual seed value on 2026-08-15, while the fan's real speed had drifted to `33`
through some path outside RF tracking) - this only affects what percentage the
power-toggle automation resumes to, and self-corrects the next time a genuine non-zero
wall-switch speed press comes in (confirmed live: pressing bedroom speed to 100%
refreshed the helper to `100` immediately). Not fixed further - it's inherent to the
"remember last real speed" design and documented as such in `input_numbers.yaml`'s
comments.

**Correction to a misconception baked into the original automation descriptions:** the
phrase "not wired to Bond" (used to describe the light-only wall switch buttons)
suggested those buttons might not actually control the physical fixture. Live testing
confirmed this is **not true** - all three room's light-only buttons, like the
power and speed buttons, directly and reliably control the physical light via RF (same
304.25MHz protocol as everything else in this project). The one apparent mismatch
during testing (living room light didn't change on a press) was a one-off RF reception
miss, immediately reproduced-away by pressing again - not a hardware/wiring limitation.
The phrase should be read as "Bond has no visibility into this button" (true, and the
whole reason this project exists), not "this button has no physical effect."

**Final state:** `rtl433-mqtt.service` running and healthy on the Pi, all 9
automations enabled and live-validated, no known blockers. This closes out the
tracked-state bug that blocked the 2026-08-15 session.

## Future simplification idea (not implemented, thought experiment 2026-08-16)

Discussed removing HA/MQTT from this pipeline entirely and having the Pi's
`fan_wallswitch_bridge.py` call Bond's local API directly (belief-only `PATCH
.../state`, never a real action) on each decoded press - "Bond as single source of
truth." This would remove MQTT, the 9-12 HA automations, and `rest_command` from the
picture; HA's Bond integration would become a pure passive consumer of Bond's belief
for the UI/voice/other automations instead of an active participant correcting it.
Not implemented - current design works and isn't broken, this is optional future
cleanup, not a fix.

**One open question got resolved empirically while discussing it:** does the physical
fan itself remember its last real speed, or does that memory live in the wall switch
(which would make a Pi-direct-to-Bond design unable to replicate correct resume-on-power
behavior without its own speed tracking, same as today's `input_number` helpers)?
Tested live on the bedroom fan, entirely through Bond's real (transmit-type) API
actions - **never touching the wall switch at any point**, ruling out "switch
remembers" as an explanation:
1. Real `SetSpeed` action → 33%, confirmed physically.
2. Real `TurnOff` action, confirmed physically off.
3. Real bare `TurnOn` action (**no speed argument**) → fan confidently confirmed back
   at 33%, not some other/default speed.

**Conclusion: the fan hardware itself has last-speed memory**, independent of Bond,
HA, and the wall switch. This means the `input_number.<room>_last_fan_speed` helpers
(and their equivalent in any future Pi-direct design) exist *only* to keep **Bond's own
belief** accurate for the UI/voice control - they play no role in the physical device
behaving correctly for the user, since the hardware already does the right thing on
its own from a bare power-on. Doesn't change today's working design, but is a genuine
data point in favor of the simplification idea above: if that helper logic later moves
into the Pi's Python process, it can be treated as low-stakes/best-effort persisted
state rather than something that has to be perfectly accurate for the physical devices
to work correctly.

Diagnostic note: `mcp__bond__send_custom_action` returned `401 Unauthorized` for write
actions (reads via the MCP tool work fine) - fell back to raw `curl` with the
`bond-bridge-local` 1Password token for the real `SetSpeed`/`TurnOff`/`TurnOn` calls
used in this test. Worth fixing the MCP tool's write auth if it gets used for
diagnostics like this again.

## In-progress replacement: Bond-rtl_433 RF Sync add-on (started 2026-08-16)

The simplification idea above is being built for real, as a proper Home Assistant
add-on rather than a Pi-side script: **`bond-rtl433-rf-sync`** in the
`coder999/tuttleHAaddons` repo. It absorbs the Pi's decode/match/debounce
logic plus the belief-only Bond correction directly into one self-contained add-on -
no MQTT, no HA automations, no `rest_command` - configurable to run against a local
USB dongle or a remote `rtl_tcp` source (the Pi, in this household's case).

As of 2026-08-16: fully built (11 code tasks, TDD throughout, 60 tests), deployed to
this HA instance, and dry-run validated against all 9 real wall-switch button
combinations with zero mismatches.

## ✅ CUTOVER COMPLETE (2026-08-16)

Live validation (real Bond corrections, `dry_run: false`) then passed all 9
combinations with zero errors and zero double-RF responses - same rigor as the
2026-08-15 dry-run pass, this time actually writing to Bond. Cutover executed
immediately after:

1. **The 9 "Fan wall switch" HA automations are now disabled** (via the UI toggle,
   entity registry `disabled_by` - not deleted, not the broken YAML `enabled:` key).
   The 3 "remember last speed" automations (`010`-`012`) were left as-is per the
   plan's scope - they're now vestigial (their `input_number` helpers are unused by
   the new add-on, which has its own internal last-speed store) but harmless.
2. **The Pi's role is now just `rtl-tcp.service`**, reconfigured to bind `0.0.0.0`
   (was `127.0.0.1`-only) and made permanent (`enable --now`). `rtl433-mqtt.service`
   and `fan_wallswitch_bridge.py`'s job are retired - `rtl433-mqtt.service` is
   `disable --now`'d but the unit file and script remain in this repo for history.
3. **`bond-rtl433-rf-sync`** (in `coder999/tuttleHAaddons`) is now the sole thing
   correcting Bond's believed fan/light state from wall-switch RF, running
   continuously against the Pi's `rtl_tcp` stream, `boot: auto`.

One operational note carried forward from the plan's Global Constraints, still true
after cutover: the Pi's single SDR dongle is still shared with the unrelated gas-meter
project (`rtlamr-mqtt.service`, also via `rtl-tcp.service`) - `rtl_tcp` only serves one
client at a time, so the fan add-on and gas-meter reading still can't both be actively
connected simultaneously. That's an unchanged, pre-existing limitation of this
household's single-dongle setup, not a regression from this cutover.

This file's setup section above (MQTT topics, the bridge script, the 9+3 automations,
`rest_command`) is now **historical** - accurate as a record of how this problem was
first solved, but no longer the live system. See `coder999/tuttleHAaddons`'s
`bond-rtl433-rf-sync/` for the current implementation.

**Follow-up cleanup (2026-08-16):** the 12 disabled/vestigial automations and 3
`input_number.*_last_fan_speed` helpers were removed from `automations.yaml` /
`input_numbers.yaml` (validated with `ha core check`, one HA Core restart to apply
the `configuration.yaml` change). Their entity registry entries didn't disappear
automatically - HA leaves orphaned `unavailable` entities behind when config is
removed - so those were deleted manually via Settings → Devices & Services →
Entities (filter: Unavailable). Confirmed zero `fan_wall_switch`/`last_fan_speed`
entities remain anywhere in HA's live state. Nothing in this household's HA config
references the old pipeline anymore.
