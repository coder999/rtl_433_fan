# Bond Tracked-State Fix Implementation Plan

> **For agentic workers:** This is a diagnostic-then-implementation plan, not a
> from-scratch build. Read the whole "Background" section before doing anything -
> it's what makes this plan usable without the prior session's conversation history.
> Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:executing-plans or
> just work through it directly - it's small enough not to need subagent dispatch.

**Goal:** Get wall-switch-triggered Home Assistant automations to correct Bond's
believed fan/light state **without transmitting any RF to the physical devices** -
the previous attempt (calling normal `fan.toggle`/`light.toggle`/`fan.turn_on`)
caused a real, disruptive infinite on/off loop, and the follow-up attempt (Bond's
documented "tracked state" services) also transmitted real RF despite every source
saying it shouldn't. This plan finds out why and fixes it.

**Architecture:** A dedicated Bond MCP tool (installed 2026-08-15 night, authenticated
directly against the Bond Bridge's local API, key via 1Password) lets this session
test Bond's actual local API behavior directly, bypassing Home Assistant's Bond
integration layer entirely - isolating whether the bug is in HA's translation layer
or Bond's own firmware/API. Once a genuinely-confirmed no-RF update path is found,
wire the 9 wall-switch automations to use it, then resume the one-at-a-time live
validation that was interrupted last night.

**Tech Stack:** Home Assistant 2026.8.2 (HAOS, root SSH via `ssh ha`), Bond Bridge
local API v2 (Olibra BD-1000, `192.168.0.110`, firmware v4.34.1), Raspberry Pi
(`ssh raspberrypi`) running `rtl433-mqtt.service` (currently **stopped** - leave it
that way until this plan's fix is validated).

**Spec:** No separate spec doc - this file is self-contained. Background section
below has everything needed; `FAN_WALLSWITCH_SYNC.md`'s "v2 implementation session"
entry has the full narrative history if more context is ever needed, but reading it
shouldn't be necessary to execute this plan.

## Global Constraints

- **Never call a real Bond control service (`fan.toggle`, `light.toggle`,
  `fan.turn_on`, `bond.set_fan_speed_tracked_state`, `bond.set_light_power_tracked_state`,
  or any raw Bond `/actions/` endpoint) against a real device without the user
  present and watching it.** Two real disruptions happened testing this already.
- **Do not start `rtl433-mqtt.service` on the Pi** until Step-by-step Task 3 below
  confirms the fix actually works - starting it re-enables the RF trigger path for
  all 9 automations, which currently still call the broken tracked-state services.
- **Do not restart Home Assistant Core without asking the user first** (explicit
  instruction from 2026-08-15 session).
- Destructive edits to `/config/automations.yaml` (or any live HA config) need
  explicit user approval before running, same as every edit in this project so far.
- All automation/helper edits go in `/config/automations.yaml` and
  `/config/input_numbers.yaml` on the HA host directly via SSH - `ha core check`
  validates syntax but **does not catch semantic bugs** (confirmed twice
  2026-08-15: the `enabled:` key issue and the tracked-state RF issue both passed
  `ha core check` cleanly). Always also validate structurally with a local
  `python3 -c "import yaml; ..."` parse before and after any live edit, and get
  explicit confirmation before reloading/restarting HA.

## Background (read this before doing anything)

### What's already built and working

A bridge script on the Pi (`/usr/local/bin/fan_wallswitch_bridge.py`, canonical copy
in this repo) listens for the three ceiling fan wall switches' RF (304.25MHz, decoded
via a validated `-X OOK_PWM` flex decoder) and publishes clean MQTT events:
- `home/fans/<room>/power` and `home/fans/<room>/light` - bare trigger (pure RF
  toggle buttons, no target state available)
- `home/fans/<room>/speed` - payload is the *exact* target percentage (0/33/66/100),
  decoded directly from the RF, no guessing needed

`<room>` is `livingroom`, `diningroom`, or `bedroom`. This part is fully validated
and correct - not in question, don't re-test it today.

### What's built but broken

9 Home Assistant automations (ids `1786000000001` through `1786000000009` in
`/config/automations.yaml` on the HA host) subscribe to those MQTT topics and are
*supposed to* correct Bond's believed state. Plus 3 more (`1786000000010-012`) that
track each room's last-known non-zero fan speed in `input_number.<room>_last_fan_speed`
helpers, so the power-toggle automation knows what speed to resume to (confirmed via
live test: these fans resume their last real speed on power-on, not a fixed default).

**The 9 main automations currently call `bond.set_fan_speed_tracked_state` /
`bond.set_light_power_tracked_state`, which is wrong** - confirmed live 2026-08-15
night that this transmits real RF to the physical device, contradicting Bond's own
docs, HA's service description, and the `bond-async` library's own routing logic
(which correctly routes the underlying `SET_STATE_BELIEF` action to `PATCH
/v2/devices/<id>/state`, not `/actions/<name>`, per the library source). The root
cause of *why* it still transmits despite that routing looking correct is not yet
known - that's this plan's first job.

**Why this matters at all:** Bond transmits the *identical* RF protocol as the wall
switches (proven earlier 2026-08-15, "Bond RF audit" in this repo's README). So any
real control service called in response to a detected wall-switch press causes Bond
to transmit RF that the bridge's own always-on receiver detects as a new press,
re-triggering the automation - an infinite loop, confirmed live (a bedroom light
stuck oscillating on/off every few seconds until the Pi's service was stopped).
Independent of that software loop, it's *also* a real correctness bug: power/light
are pure toggle pulses at the RF level, so a redundant real command physically
toggles the device again, undoing the user's actual press.

### Entity and device IDs (all confirmed 2026-08-15, safe to trust)

| Room | HA fan entity_id | HA light entity_id | Bond device_id |
|---|---|---|---|
| Living room | `fan.living_room_living_room_ceiling_fan` | `light.living_room_living_room_ceiling_fan` | `ce4d90389da6937f` (confirmed) |
| Dining room | `fan.dining_room_ceiling_fan_2` | `light.dining_room_ceiling_fan` | one of `3e9252a7323111d2` / `33c72108a1a2548d` - **not yet identified, do this first (Task 1)** |
| Bedroom | `fan.master_bedroom_ceiling_fan` | `light.master_bedroom_ceiling_fan` | one of `3e9252a7323111d2` / `33c72108a1a2548d` - **not yet identified, do this first (Task 1)** |

Bond Bridge local API: `http://192.168.0.110`, API v2, firmware v4.34.1. Device list
and read-only `GET` calls need no auth (confirmed working via plain `curl` on
2026-08-15). Write calls likely need the Bond local token - the new Bond MCP tool
should have this already configured; use it in preference to raw `curl` for anything
that writes.

### Two reference behaviors worth understanding before trusting any fix

Both found in the Bond app's Advanced Settings (Fan device → Settings → Advanced →
this screen), screenshotted by the user 2026-08-15 night:

1. **"Fix Tracked State"** - manual, one-off UI action: user drags a slider to the
   real device state and taps Save. This is Bond's own officially-supported way to do
   exactly what this plan needs to do programmatically. Official docs (see
   `FAN_WALLSWITCH_SYNC.md`'s "v2 implementation session" entry for sources) say this
   uses `PATCH /v2/devices/<id>/state` and does not transmit.
2. **"Trust Tracked State"`** - a toggle, confirmed **ON** in this account already.
   Description: "When you send an On/Off command, the Bond Bridge will not transmit
   the toggle command if the device is already in the desired state." **This is a
   different mechanism** - it's about skipping transmission on a *normal* On/Off
   command when the target matches the current belief, not a dedicated no-transmit
   write path. Don't assume it's the same thing as `set_state`/`SET_STATE_BELIEF` -
   worth fully understanding before concluding any test result is a clean signal.

---

### Task 1: Identify dining room and bedroom's Bond device IDs

**Files:** none - read-only API exploration.

- [ ] **Step 1: Check what the new Bond MCP tool exposes**

Search for it (it wasn't available in the prior session, should be now):
```
ToolSearch(query="bond", max_results=10)
```
Load whatever's found and read its description before using it.

- [ ] **Step 2: Get device names for the two unidentified IDs**

Either via the Bond MCP tool, or fall back to plain curl (confirmed working
unauthenticated for reads 2026-08-15):
```bash
curl -s "http://192.168.0.110/v2/devices/3e9252a7323111d2" | head -c 500
curl -s "http://192.168.0.110/v2/devices/33c72108a1a2548d" | head -c 500
```
Each response has a `"name"` field (e.g. `"Living Room Ceiling Fan"` was confirmed
for `ce4d90389da6937f` this way) - match against "Dining Room Ceiling Fan" / "Master
Bedroom Ceiling Fan" to identify which ID is which.

- [ ] **Step 3: Record the mapping**

Update the table in this file's Background section (or just keep the mapping in your
working notes for the rest of this session - not critical to persist further unless
this plan gets re-run another day).

---

### Task 2: Establish a clean reference behavior before testing programmatically

**Files:** none - manual verification with the user, in the Bond app.

- [ ] **Step 1: Ask the user to test "Fix Tracked State" themselves, in the Bond app**

Pick one light (suggest dining room, untouched by last night's testing). In the Bond
app: select the device → Settings → Advanced → Fix Tracked State → move the slider
to a state that's the *opposite* of the light's current real state → Save. Ask the
user to confirm: did the physical light respond at all, or only the app's displayed
state change?

- [ ] **Step 2: Interpret the result**

If the physical light did **not** respond: good, this confirms Bond's own
documented no-RF write path genuinely exists and works on this account/firmware -
the bug from last night is specifically in HA's integration layer or in how it's
being called, not a fundamental Bond limitation. Proceed to Task 3 to find the
HA-side bug.

If the physical light **did** respond even via Bond's own native app feature: this
is a deeper problem (firmware behavior, or a setting like "Trust Tracked State"
interacting unexpectedly) that HA-side changes can't fix - stop and reconsider the
whole approach with the user rather than continuing this plan's remaining tasks.

---

### Task 3: Test Bond's local API directly, bypassing Home Assistant

**Files:** none - API testing only, no automation edits yet.

**Only proceed here if Task 2 confirmed Bond's own no-RF path works.**

- [ ] **Step 1: Pick one already-identified device (living room, ID `ce4d90389da6937f`) and get its current real state**

```bash
curl -s "http://192.168.0.110/v2/devices/ce4d90389da6937f/state"
```
Note the current `light` value (0 or 1).

- [ ] **Step 2: With the user watching the physical light, PATCH the state directly**

Use the Bond MCP tool if it has a state-write capability; otherwise construct the
raw call (needs the Bond local token - check the MCP tool's config or ask the user
where it's stored, don't guess or try to extract it from 1Password yourself):
```bash
curl -s -X PATCH "http://192.168.0.110/v2/devices/ce4d90389da6937f/state" \
  -H "BOND-Token: <token>" \
  -H "Content-Type: application/json" \
  -d '{"light": <opposite of what Step 1 showed>}'
```

- [ ] **Step 3: Ask the user to confirm: did the physical light respond?**

If **no physical response** and the `GET .../state` call afterward shows the belief
updated: the raw local API works correctly, confirming the bug is specifically in
HA's Bond integration layer (or in how the automations called it). Proceed to Task 4
to fix the automations to call the local API directly instead of going through HA's
`bond.set_*_tracked_state` services.

If the physical light **did** respond: the raw API itself is transmitting despite
its documented behavior - this means Task 2's "Fix Tracked State" test and this raw
API test are hitting different code paths, or there's a request-format detail this
plan is missing (e.g. maybe the JSON body needs additional fields, or the device
needs to be addressed differently for combo CF+light devices specifically). Stop and
investigate the exact request Bond's own app makes (proxy/inspect if possible) rather
than guessing further.

---

### Task 4: Rewire the automations to use the confirmed-working no-RF path

**Files:**
- Modify (HA host): `/config/automations.yaml` - automations `1786000000001` through
  `1786000000009` (the 3 "remember last speed" ones, `010-012`, don't call Bond at
  all and don't need changes)

**Interfaces:**
- Consumes: whichever mechanism Task 3 confirmed works - either continue using
  `bond.set_fan_speed_tracked_state`/`bond.set_light_power_tracked_state` if the bug
  turns out to be something else entirely (e.g. a parameter format issue fixable
  within the same service calls), or replace those actions with direct local-API
  calls (via `shell_command:`/`rest_command:` configured to hit
  `192.168.0.110` directly, or via the Bond MCP tool if it's callable from within an
  HA automation somehow - unlikely, MCP tools are for this Claude session, not HA
  automations, so a `rest_command:`/`shell_command:` is the realistic path if the fix
  requires bypassing HA's Bond integration).

- [ ] **Step 1: Based on Task 3's finding, decide the exact fix**

This step can't be fully scripted in advance since it depends on Task 3's result.
Two likely branches:

**Branch A - HA's service call itself is fine, something else was wrong:** (e.g. the
"Trust Tracked State" setting interacting with the *first* call after a state
change, or a race condition, or the specific `power_state` boolean template
evaluating unexpectedly). Fix within the existing automation structure - re-read the
current live YAML (`ssh ha "sed -n '1342,1600p' /config/automations.yaml"`) before
changing anything, don't assume last night's version is still exactly as described
above without checking.

**Branch B - HA's Bond integration doesn't work for this, need direct local API
calls:** add a `rest_command:` to `/config/configuration.yaml`:
```yaml
rest_command:
  bond_set_state:
    url: "http://192.168.0.110/v2/devices/{{ device_id }}/state"
    method: PATCH
    headers:
      BOND-Token: "<token - get from wherever Task 3 sourced it, don't hardcode a
        guess>"
      Content-Type: "application/json"
    payload: "{{ body }}"
```
Then each automation's action becomes e.g.:
```yaml
- action: rest_command.bond_set_state
  data:
    device_id: ce4d90389da6937f
    body: '{"light": {{ 1 if not is_state(...) else 0 }}}'
```
Adjust exact field names (`light`, `power`, `speed`) to match what Task 3's raw
`GET .../state` calls showed for each device.

- [ ] **Step 2: Validate structurally before deploying**

Same pattern as every other edit this project: pull the file locally, parse with
`python3 -c "import yaml; ..."`, confirm exactly 9 (or however many changed) automations
match the expected new structure, no `enabled:` key anywhere, no duplicate ids, other
automations (`airplay_indoor_start`, `nexus_mqtt_summary_publisher`, etc.) untouched.

- [ ] **Step 3: Get explicit user confirmation, then deploy**

Deploy via `scp`, run `ha core check`, confirm the auto-commit
(`ssh ha "cd /config && git log --oneline -3"`).

- [ ] **Step 4: One isolated live test before trusting it broadly**

Same method as Task 3 Step 2 - use the automation editor's "Run" action on ONE
automation (suggest the dining room light toggle, lowest stakes) with the user
watching the physical device. Confirm no physical response, and confirm (via
`mcp__homeassistant__GetLiveContext` or the Bond app) that the *believed* state did
update.

---

### Task 5: Resume normal live validation (the original Task 7 from yesterday's plan)

**Files:** none - operational testing.

**Only proceed here once Task 4's isolated test passes clean.**

- [ ] **Step 1: Turn the Pi back on if it isn't already, confirm no stale processes**

```bash
ssh raspberrypi 'ps aux | grep -i rtl | grep -v grep'
```
Should be empty (or only unrelated processes like the gas-meter project's `jq`).

- [ ] **Step 2: With explicit user go-ahead, start the bridge service**

```bash
ssh raspberrypi 'sudo systemctl start rtl433-mqtt.service && sleep 2 && systemctl is-active rtl433-mqtt.service'
ssh raspberrypi 'journalctl -u rtl433-mqtt.service -n 20 --no-pager'
```

- [ ] **Step 3: One real physical wall-switch press per automation, one at a time, watching logs and the physical device each time**

Order: living room speed (this one was mid-test when the tracked-state bug was found
last night, do it first), then the remaining 8, same room-by-room order as before.
For each: press the real button, confirm exactly one `seen`/`firing` log line pair on
the Pi (`ssh raspberrypi 'journalctl -u rtl433-mqtt.service -f'`), confirm via
`GetLiveContext` that HA's believed state now matches, and confirm the physical
device did **not** additionally respond to *our* correction (only to the original
press).

- [ ] **Step 4: Update `FAN_WALLSWITCH_SYNC.md` with the resolution**

Document what Task 3's diagnosis found (root cause of the tracked-state bug) and
confirm all 9 automations are validated live. Commit and push from
`/home/mark/projects/rtl_433_fan`.
