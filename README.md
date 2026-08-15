# Ceiling Fan Wall Switch RF Capture

Goal: use the Raspberry Pi's RTL-SDR + `rtl_433` to capture and decode the RF signal
sent by the wall switches for the Ashby Park ceiling fans, so Home Assistant can detect
wall-switch use and stay in sync with the Bond Bridge's assumed fan state.

## Quick reference: decoded RF codes (current as of 2026-08-15)

Every code is `<16-bit address><8-bit command byte><trailing bits>`, transmitted OOK at
304.25MHz. Trailing bits are **not jitter** — for the speed command they're the
absolute target speed; see notes below the table.

| Room | Address | Power toggle | Light toggle | Speed command byte | Off (0%) | 33% | 66% | 100% |
|---|---|---|---|---|---|---|---|---|
| Living room | `0x01` | `0xCC` | `0xF8` | `0xFF` | not confirmed (see below) | trailing `00` | trailing `001` | trailing `10` |
| Dining room | `0x02` | `0x26` | `0x3C` | `0x3F` | trailing `111` | trailing `100` | trailing `1001` | trailing `110` |
| Master bedroom | `0x02` | `0x66` | `0x7C` | `0x7F` | trailing `111` | trailing `100` | trailing `1001` | trailing `110` |

Notes:
- **Power and light are simple toggles** — one code each, same bits regardless of
  on/off direction (confirmed via live Bond commands going both directions).
- **Speed's trailing bits are the absolute target percentage**, not a "next" pulse —
  confirmed by directly correlating presses to the switch's own light-count indicator
  (bedroom, all 4 states) and cross-confirmed against Bond's own `HassFanSetSpeed`
  commands (dining + bedroom), which produce byte-identical RF to the wall switch
  reaching the same target. See "Bond RF audit" below for the full story.
- **CORRECTION (2026-08-15, later same day): the living room speed mapping above was
  wrong, and there was never a "100% decode failure."** The earlier real-time
  button-press ground-truth test (below) mis-attributed which captured trailing value
  went with which light-count state, most likely due to message-ordering confusion
  during a fast, unconfirmed press sequence — the state that actually never got pinned
  down was **off**, not 100%. This was only caught because the user directly observed
  the physical fan and Bond/HA both agreeing on "high" while nothing had touched it
  since a Bond command I'd logged as producing "66%" — a real, physical
  contradiction that forced a re-check.

  Corrected via a clean, unambiguous method: a stale duplicate `Living Room Ceiling
  Fan` HA entity (leftover from the 2026-07-30 broken-pairing saga) was renamed by the
  user, leaving one real entity to target with `HassFanSetSpeed` directly — no more
  guessing which of two entities a command actually reaches. Full sweep, each
  percentage captured cleanly with no demod trouble at all:

  | Requested % | Trailing bits |
  |---|---|
  | 0% | routes through the power-toggle code (`0xCC`), not a speed-family code |
  | 33% | `00` |
  | 66% | `001` |
  | 100% | `10` |

  **`10` — previously logged as a stuck-at-66%/decode-failure situation — is simply the
  correct, cleanly-decoding 100%/HIGH code.** It was captured dozens of times across
  this whole session, always readable, always "Manchester coding." There was no
  decode failure. The genuinely open item is the **speed-family "off" code**, which
  Bond's 0% never sends (it uses the power-toggle path instead) — lower priority than
  it seemed, since power-toggle already covers off functionally; would still need a
  live physical-button test to pin down if ever needed.

  Power (`0xCC`, trailing `0100`) and light (`0xF8`, trailing `1001`) were correctly
  identified in the original real-time test and remain confirmed, matching the archived
  2026-07-29 codes exactly.
- Address `0x02` is shared by dining room and bedroom — still not fully explained (see
  2026-08-14 entry below) but confirmed not to cause practical collisions, since command
  bytes never overlap between the two switches.

## The Problem

Three Ashby Park 52" ceiling fans (Model 59252) are controlled through a Bond Bridge in
Home Assistant. Each fan also has a wall switch that talks to the fan directly over RF
— it is **not wired** to the fan and Bond/HA has no visibility into it. If someone uses
the wall switch, the physical fan state changes but Home Assistant's assumed state does
not, so HA and reality drift out of sync (e.g. HA thinks a fan is on when it's actually
been turned off at the wall).

The fix: listen for the wall switches' RF transmissions directly with the Pi's RTL-SDR
and use those events to correct Home Assistant's state, instead of relying solely on
commands sent through Bond.

## Hardware Involved

- Raspberry Pi (`raspberrypi` on the local network), previously dedicated full-time to
  gas meter reading — see [`../gas_meter_reading`](../gas_meter_reading).
- One RTL-SDR dongle (RTL2838 + R820T tuner) — currently the **only** SDR on the Pi.
- 3x Hampton Bay wall switches, model **98139** — "3-Speed Universal Ceiling Fan
  Wireless Wall Control (Damp Rated)".

## The Wall Switch, Identified

- Model: Hampton Bay **98139**
- FCC ID: **KUJCE10321** (Chungear Industrial Co.)
- Confirmed carrier frequency (from the FCC filing, fccid.io/KUJCE10321):
  **304.25 MHz**
- Filed under FCC Part 15.231 — the rule class for simple periodic/security
  remote-control transmitters. Consistent with a fixed-code OOK/ASK protocol using
  DIP-switch pairing (each switch/remote has 4 DIP switches that must match the
  receiver in the fan canopy — this is how the 3 switches stay independent of each
  other).
- No existing rtl_433 decoder covers this remote. The plan is to capture raw pulse
  timing with `rtl_433 -A` and either match it to one of rtl_433's generic fixed-code
  OOK decoders, or write a custom flex decoder (`-X`).
- Modulation type and occupied bandwidth were not disclosed in the public FCC filing
  summary; the underlying test report PDF may have more detail if it's ever needed:
  https://fccid.io/KUJCE10321/Test-Report/Test-Report-2489352

## The Dongle-Sharing Problem

The Pi has only one RTL-SDR, and it's currently dedicated to `rtl-tcp.service` →
`rtlamr-mqtt.service` for gas meter reading at 912.6MHz. The gas meter is wake-up-mode
and only transmits during the monthly utility drive-by (see the gas meter project's
`lessons_learned.md`), so it's reasonable to temporarily or permanently repurpose the
dongle for fan-switch capture — just not both at once without a second dongle.

There is leftover scaffolding on the Pi from an earlier session for this purpose:

- `rtl433-mqtt.service` (currently inactive) + `/usr/local/bin/rtl433-mqtt.sh`
- The unit file has `Conflicts=rtlamr-mqtt.service rtl-tcp.service`, so starting it
  will automatically stop the gas meter services.
- The script is still hardcoded to 912.6MHz (the meter's frequency) and will need to be
  updated to 304.25MHz once a working decoder exists.

Long-term, a second RTL-SDR dongle (~$15-25) would let both listeners run permanently
side by side — not yet decided/purchased.

## Status (2026-07-30)

The proximity problem is solved — the Pi was physically relocated near the living room
switch, and captures succeeded there and in the dining room. The physical layer and
per-switch/per-button codes are now characterized for 2 of 3 switches. Raw I/Q for all
9 (switch × button) combos is saved on the Pi at `~/fan_rf_captures/`.

### Physical layer

OOK, carrier 304.25MHz. Every button press transmits a fixed-length **25-pulse burst**
(~27.7ms), repeated continuously for as long as the button is held. This is **PWM
encoding** (not Manchester, despite rtl_433's auto-guesser calling it that) — pulse
width alone encodes the bit, with an approximately fixed total bit period:

- Short pulse ~320-370µs
- Long pulse ~700-735µs
- Total bit period ~1140-1150µs regardless of bit value

rtl_433's built-in Manchester-zerobit demod (`OOK_MC_ZEROBIT`) happens to decode this
usably (same command bytes come out reliably) but introduces 1-4 bits of jitter at the
end of each row — treat only the **leading bits as trustworthy** and ignore the tail
when matching codes. A dedicated `OOK_PWM` flex decoder should be the "real" fix but a
first attempt at tuning one (`-X 'n=fansw,m=OOK_PWM,s=330,l=730,r=900,g=900,t=150'`)
didn't match anything, including on the strongest capture — needs further iteration,
not yet solved.

### Decoded codes so far (superseded — see "Quick reference" table at the top of this file)

**This section is left as historical record of the original 2026-07-29
characterization. It's stale as of 2026-08-15** — bedroom is now fully decoded, and
the "trailing bits are jitter" claim below turned out to be wrong (see the 2026-08-15
entries further down). Use the Quick reference table at the top of this file instead.

All codes share the structure `<16-bit switch address><~10-12 bit command>`. Values
below are the **stable leading bits**; trailing bits vary run-to-run due to the demod
jitter mentioned above and should not be used for matching.

| Switch (room) | Address prefix | Power toggle | Light toggle | Fan speed toggle |
|---|---|---|---|---|
| Living room | `00000001` (0x01) | `11001100` (0xCC) | `11111000` (0xF8) | `11111111` (0xFF) |
| Dining room | `00000010` (0x02) | `00100110` (0x26) | `00111100` (0x3C) | `00111111` (0x3F) |
| Master bedroom | unknown | unknown | unknown | unknown |

Notes:
- Addresses look sequential (0x01, 0x02, ...) — consistent with a factory-paired
  3-fan kit rather than independently random DIP-switch settings. Bedroom is likely
  0x03, unconfirmed.
- The fan speed toggle sends the **same code on every press**, regardless of which
  speed it's cycling to (confirmed on living room: 2 separate presses, both decoded to
  the same command byte). It's a relative "advance to next speed" command, not
  per-speed-level absolute codes. **Implication for the HA integration**: we can detect
  *that* the speed was changed at the wall, but not *which* speed it landed on — the
  automation will need to trigger a Bond status poll/refresh rather than infer the
  exact new speed from the RF code alone.
- **Master bedroom switch still not decoded as of 2026-07-30, second attempt.**
  Original hypothesis (too far from Pi) turned out to be wrong/incomplete — see below.
- User also reports the master bedroom fan's speed button behaves "out of sync" with
  the other two fans — e.g. speed level 3 seems to run the lowest actual speed and
  level 1 the highest (not necessarily exact, but reversed-ish). Most likely unrelated
  to the RF capture difficulty and instead points to the fan's low/medium/high motor
  tap wiring being connected to the receiver differently than the other two fans — a
  physical install quirk, not a protocol difference.
- **The bedroom switch's buttons have visibly different icons than the living
  room/dining room switches**, raising the possibility it's a different SKU entirely,
  possibly on a different carrier frequency, not just a farther-away unit of the same
  model 98139. **Confirmed 2026-07-29** by popping the switch off the wall plate: it's
  model **TR223A**, FCC ID **KUJCE10321** (Chungear Industrial Co.) — a genuinely
  different remote from the 98139 units, not just a distant unit of the same model.
  Its filing includes hold-to-dim light control and natural-wind/timer modes that the
  98139 switches don't appear to have, consistent with a different underlying protocol.
  Still need to pull the actual carrier frequency/modulation from its FCC filing
  (fccid.io/KUJCE10321) — the 303.9-304.25MHz range explored in the session below was
  guesswork based on the (now known to be wrong) same-model assumption and shouldn't be
  trusted for TR223A.

### Bedroom troubleshooting session 2 (2026-07-30, Pi relocated to bedroom)

Moving the Pi into the bedroom did fix reachability (confirmed real signal is present,
unlike the totally-silent first attempt from across the house), but a clean full
25-pulse decode was never achieved despite ~10 attempts across a wide parameter sweep:

| Frequency | Gain (dB) | Distance | Result |
|---|---|---|---|
| 304.25M | 49.6 | 6 inches | Overloaded/garbled (`ff fb`-type all-1s pattern), high level ~13-15k |
| 304.25M | 20.7-25.4 | 6in-1ft | Still garbled or partial (3-13 of 25 pulses) |
| 304.25M | 8.7 | 1-2 ft | No signal (too low gain for distance) |
| 304.25M | 32.8-49.6 | 1-2 ft | Partial fragments (1-10 pulses), inconsistent level (1k-15.8k) |
| 303.9M | 40.2 | 1-2 ft | **Level jumped to ~15,900 (matches living/dining room strength!)** but packets fragmented into multiple short pieces (22, 13, 11, 3 pulses) rather than one clean 25-pulse burst — looks like clipping/overload again, just at a different gain than at 304.25M |
| 303.9M | 12.5-28.0 | 1-2 ft | Level dropped back to ~1000, fragments only (1-8 pulses) |

Key finding: **303.9MHz produces a much stronger raw level than 304.25MHz** at the same
gain/distance (~15,900 vs ~1,000-15,000 inconsistent) — this is the strongest evidence
yet that the bedroom switch's actual carrier is nearer 303.9MHz, not 304.25MHz,
supporting the "different SKU" theory above. However even at 303.9MHz we couldn't find
a gain sweet spot that avoided both under-driving (weak, fragmented) and over-driving
(strong but garbled/fragmented) — the transition between the two seemed to happen very
abruptly between gain ~28 and ~40, without a clean middle ground found.

Also worth noting: many capture windows caught 0-1 packages instead of the expected
3-6 (multiple mid-session interruptions — phone call, bathroom break — likely caused
some "press now" windows to have no actual press happen).

**Decision: paused bedroom troubleshooting for this session** rather than continuing
trial-and-error. Living room and dining room are fully decoded and that work can
proceed independently.

## HA-Side Integration Status (living room + dining room)

The living/dining room switches (model 98139) were fully decoded, and an MQTT
bridge + Home Assistant automation set was built to keep Bond's assumed fan state in
sync with real wall-switch presses. Full current status (this changes often) is
tracked separately, not here: **`FAN_WALLSWITCH_SYNC.md`** in this repo. Read that
before touching the live service or the 6 "Fan wall switch" automations.

**Status as of 2026-07-30, briefly:** the original debounce bug is fixed and fully
validated, including on real hardware. But live automation testing then surfaced a
deeper, currently-blocking problem unrelated to the wall switch at all — **Bond lost
the ability to control the living room fan entirely** (confirmed via both HA and the
Bond app directly; Bond Bridge itself is online, so the fault is Bond↔fan or the fan
itself). Re-pairing was attempted and is incomplete; the remote used for pairing broke
partway through from repeated button presses. All 6 automations remain disabled.
Nothing further can be tested here until Bond can control the fan again — see
`FAN_WALLSWITCH_SYNC.md`'s Immediate TODO for the current blocking priority order.

## Status update (2026-08-14)

Resumed bedroom-switch troubleshooting in person, Pi physically in the bedroom.

- **Root cause of all prior bedroom capture failures: dead/dying battery in the
  switch**, not a wrong frequency, gain, or model mismatch. SNR degraded
  progressively across a session (23dB → ~9dB) even as capture parameters were
  tuned, which in hindsight tracked a dying battery, not RF tuning. Replacing the
  battery fixed it immediately — SNR jumped to 30-39dB and captures went from
  fragmented partial bursts to clean, fully-repeating 25-pulse frames.
- **FCC filing double-checked and confirmed single-model**: KUJCE10321 covers
  exactly one device, "CEILING FAN REMOTE CONTROLLER (TRANSMITTER)", model
  **CE10321**, 304.25MHz, single grant (Dec 2014). No multi-model split in the
  filing. This supports the user's own recollection (bought as one 3-pack from
  Home Depot, identical packaging) over the earlier "TR223A is a different SKU"
  theory — the different button icons noted on 2026-07-29 are most likely a
  rebadge/label difference on the same CE10321 internals, not a different RF
  protocol. **304.25MHz is confirmed correct for the bedroom switch too**; no
  need to keep chasing 303.9MHz.
- Also found and cleared an unrelated Pi issue: a stuck `rtl_test -t` process
  (started 2026-08-09, presumably an unkilled `timeout` from a prior session) had
  been holding the RTL-SDR's USB claim open for 5 days, causing
  "usb_claim_interface error -6" on any new rtl_433/rtl_test invocation. Killed;
  no recurrence expected but worth checking `ps aux | grep rtl_433` first if this
  error shows up again.
- Capture tuning note: use the **default sample rate (~250 kS/s, i.e. omit `-s`)**
  for hand-held diagnostic captures, not the deployed script's `-s 2048000`. The
  wider 2.048MHz capture bandwidth costs ~9dB of noise floor for no benefit on
  this narrowband OOK signal and was the difference between a 23dB-SNR clean
  capture and a 9dB-SNR unusable one at otherwise-identical gain/distance.
- **Bedroom switch, light toggle: decoded, stable across 18 consecutive repeats.**
  Raw leading bits: `00000000 00000010 01111100` (24 bits, trailing bits per
  usual jitter). As (addr_hi, addr_lo, command) matching the CODE_TABLE format in
  `fan_wallswitch_bridge.py`: **`(0x00, 0x02, 0x7C)`**.
  - Note: the address bytes (`0x00, 0x02`) are identical to the *dining room*
    switch's address, not the `0x03` the sequential-numbering guess predicted.
    The command byte (`0x7C`) doesn't collide with any of dining room's three
    known commands (`0x26`/`0x3C`/`0x3F`), so the CODE_TABLE lookup (matched on
    the full 3-byte tuple) is still unambiguous — not a blocker, just a sign the
    16-bit field isn't a simple per-switch sequential ID (possibly a narrower
    DIP-style selector within a larger fixed word). Not yet fully explained.
  - Power and speed buttons not yet captured — paused for the night, plan to
    resume with the same setup (fresh battery already in, 304.25MHz confirmed,
    default sample rate, `-g 30`, background capture via
    `nohup timeout 45 rtl_433 -f 304.25M -g 30 -A -R 0 > ~/lightcapture.log 2>&1 &`).

## Status update (2026-08-15)

Resumed bedroom-switch capture (fresh battery still in from 2026-08-14, dongle freed
again — `rtl-tcp.service`/`rtlamr-mqtt.service` had come back on their own after an
apparent Pi reboot, since both are `enabled` at boot; stopped manually, may recur after
any future reboot — worth disabling if the gas-meter services are meant to stay off
indefinitely).

**Light and power buttons decoded, clean and repeatable:**
- Light: `(0x00, 0x02, 0x7C)`
- Power: `(0x00, 0x02, 0x66)`
- Speed: `(0x00, 0x02, 0x7F)` — see open question below before trusting this one for
  anything level-specific

All three share address `0x0002`, same as the dining room switch — see the note on this
in the 2026-08-14 entry above; still unexplained, not yet a practical problem since
command bytes don't collide.

### Open question: is the speed button's signal relative or absolute? Not yet settled.

Captured a 4-press sequence on the speed button, correlating the switch's own light
indicator to the decoded RF each time:

| Press | Switch's own light count | Leading 24 bits (addr+cmd) |
|---|---|---|
| 1 | 3 | `00000000 00000010 01111111` (0x7F) |
| 2 | 2 | `00000000 00000010 01111111` (0x7F) |
| 3 | 1 | `00000000 00000010 01111111` (0x7F) |
| 4 | 0 (off) | `00000000 00000010 01111111` (0x7F) |

Leading 24 bits (address + command) were bit-identical across all four presses,
regardless of the switch's own displayed target. Trailing bits (not yet confirmed
meaningful vs. jitter) did differ: `110`, `1001`, `100`, `111` respectively.

This was initially read as confirming the living-room finding (speed button is a
relative "advance" command, same code every press, no per-level encoding) — matches
the already-documented living room behavior. **User pushed back with a real-world
test**: set the fan to a known speed (3) via Bond, physically confirmed at the fan
(motor visibly at speed 3) — but the wall switch's own indicator was still at "1" at
that point (unrelated to Bond, left over from earlier testing). Pressed the switch once
(indicator 1→0); the real fan turned off, not down to a real speed of 2.

This is consistent with either:
1. **Relative/stateful**: the fan's receiver tracks its own position independently of
   Bond (possibly because Bond talks to the fan via the *original bundled remote's*
   protocol — likely discrete per-speed buttons — while this wall switch is a separate
   "universal" add-on using its own simple relative-advance protocol; how one receiver
   arbitrates commands from two different protocols is not actually known, not
   something to assert further without evidence), and the switch's own generic
   "advance" pulse only ever drives that internal position, independent of whatever
   Bond most recently set the real motor to.
2. **Absolute, encoded in the trailing bits**: the "jitter" bits currently being
   discarded as demod noise might actually carry the real per-press target and were
   dismissed too quickly.

**Not settled either way — the fan-behavior test doesn't distinguish between the two
hypotheses**, since both predict "off" from a switch-side state of "1". Only the RF
bits (not fan behavior) can settle it, and the trailing-bit comparison hasn't been done
with a clean, confound-free test yet.

**Trailing-bits test: run 2026-08-15, result reverses the earlier "it's just jitter"
assumption.** Confound-free (no Bond involved) repeat-state comparison, using an
independent second session (2026-08-15) against the original capture (2026-08-14) as
the "same state, different time" comparison instead of two presses in one sitting:

| State | 2026-08-14 trailing bits (total bits) | 2026-08-15 trailing bits (total bits) | Match? |
|---|---|---|---|
| 3 lights | `110` (27) | `110` (27) | exact match |
| 2 lights | `1001` (28) | `1001` (28) | exact match |
| 1 light | `100` (27) | `100` (27) | exact match |
| off | `111` (27) | `111` (27) | exact match |

**All four states now confirmed reproducible**, not just 3 of 4.

Bit-for-bit and bit-count identical across two sessions hours apart, different physical
button presses each time. **This is not consistent with demod jitter** (which should
vary run to run) — it's reproducible, deterministic per resulting state. The earlier
"trailing bits are just noise, ignore them" assumption (inherited from the
living/dining room RE work, `fan_wallswitch_bridge.py`'s doc comment, and this repo's
own 2026-07-30 notes) **needs to be revisited for all switches, not just bedroom** —
it was never actually confirmed this rigorously for living/dining, just assumed based
on the demod's known jittery behavior in general.

Doesn't necessarily mean a clean "absolute speed level" encoding in the sense of a tidy
binary count (`110`/`1001`/`100`/`111` isn't an obvious binary sequence for 3/2/1/0) —
but it does mean each button-press outcome carries its own consistent signature, not
one generic "advance" pulse repeated identically regardless of target. Re-opens the
question of whether the living/dining automations' "same code every press, so track
state locally via a counter helper" design (see `FAN_WALLSWITCH_SYNC.md`) is actually
right, or whether those switches' trailing bits were similarly dismissed too fast.

### Full audit (2026-08-15): replayed all archived captures, all switches, all buttons

Replayed the original 2026-07-29 `~/fan_rf_captures/*.cu8` I/Q files on the Pi through
`rtl_433 -r <file> -s 2048000 -A -R 0` — no hardware/button presses needed, these are
the archived captures from the original characterization session. Grouped by button
across all three switches:

| Button | Living room (addr 0x01) | Dining room (addr 0x02) | Bedroom (addr 0x02) |
|---|---|---|---|
| Speed | 1 distinct trailing value seen: `001`/`00` (likely 1-bit-truncated same value across 2 captures) | 2 distinct values: `110`, `1001` | 4 distinct values, confirmed against switch's own light-count indicator: 3→`110`, 2→`1001`, 1→`100`, off→`111` |
| Power | 2 distinct values: `0100`, `01001` | 2 distinct values: `001001`, `00100` | 2 distinct values: `001001`, `00100` (matches dining exactly) |
| Light | 1 value, consistent across all captures: `1001` | 1 value, consistent: `01001` | 1 value, consistent (live 2026-08-14/15): `01001` (matches dining) |

**Conclusion: the "trailing bits are jitter" assumption was wrong for every switch,
not just bedroom.** Power and light are simple two-state toggles and only ever show
1-2 distinct trailing values (consistent with on/off), which is why they read as
"stable" in the original characterization — there just wasn't enough variety in the
samples taken to notice. Speed is the button where this actually matters (4 real
states), and only bedroom has been tested against real ground truth (the switch's own
light-count indicator) for all 4 states so far.

**Notable pattern, not yet explained:** bedroom and dining room's trailing values are
*identical* for both power (`001001`/`00100`) and speed (`110`/`1001` — dining's two
observed values exactly match bedroom's "3 lights" and "2 lights" states). Bedroom and
dining also happen to share the same 16-bit address field (`0x0002`, see the
2026-08-14 entry above). Living room, which has its own distinct address (`0x0001`),
has its own distinct pair of trailing values that don't overlap with bedroom/dining's.
This looks like the trailing bits encode a target/direction index that's paired with
the address field — not a coincidence, not a checksum unique to each switch — but
unconfirmed; needs more data to be sure the address-sharing and trailing-value-sharing
aren't independently coincidental.

### Bond RF audit (2026-08-15): Bond uses the exact same protocol — trailing bits fully decoded

Captured 304.25MHz continuously while commanding all three fans/lights through Home
Assistant's Bond integration (`HassFanSetSpeed`/`HassTurnOn`/`HassTurnOff`), no wall
switches touched. Findings:

- **Bond transmits the identical Hampton Bay/CE10321 protocol** used by the wall
  switches — same frequency, same address/command-byte structure, same trailing-bit
  scheme. Not a different protocol, not IR, not something the receiver has to
  reconcile from two different sources — it's one protocol, two transmitters. This
  resolves the "how would the receiver know if it's Bond or the wall switch"
  question raised earlier — it doesn't need to; there's nothing to distinguish.
- **The speed button's trailing bits are the absolute target speed, full stop.**
  `HassFanSetSpeed` calls used the exact same `(address, speed-command-byte)` as the
  physical speed button, with trailing bits matching the requested percentage exactly,
  confirmed on both dining room (`0x3F`) and bedroom (`0x7F`):

  | Trailing bits | Speed |
  |---|---|
  | `111` | Off (0%) |
  | `100` | 33% |
  | `1001` | 66% |
  | `110` | 100% |

  This matches bedroom's ground-truth-confirmed values from earlier in this session
  exactly. **The original hypothesis (each press sends a specific target-speed code,
  not a generic "advance" pulse) was correct.** The `counter`-helper design in
  `FAN_WALLSWITCH_SYNC.md`, built on the opposite assumption, needs to be replaced with
  direct decode-to-percentage logic — no local state tracking needed at all, for any
  of the three switches.
- **Living room's plain `HassTurnOn`/`HassTurnOff` (no percentage) triggered the
  power-toggle code (`0xCC`, address `0x01`) instead of a speed-family code.**
  Dining/bedroom's plain `HassTurnOff` used the speed-family "off" code (trailing
  `111`) instead of their own power-toggle codes (`0x26`/`0x66`). So which code path
  Bond uses depends on the HA service called and/or that fan's individual Bond
  configuration — not yet explained, and living room is also the one switch with a
  known-broken Bond pairing, so may not be representative. Worth re-checking once
  living room's Bond pairing is fixed.
- Light toggle (dining `0x3C`, bedroom `0x7C`) sent the identical code for both on and
  off — a pure toggle, no separate on/off codes, consistent with the single stable
  trailing value seen for light in the earlier full-switch audit.
- Confirmed via real captured RF (not just HA's reported success) that the end-of-test
  cleanup commands (all fans/lights off) actually transmitted and landed correctly.

**Remaining next steps:**
- ~~Work out what the trailing bits actually encode structurally~~ — **solved by the
  Bond RF audit below**: trailing bits are the absolute target speed percentage.
- **Methodological lesson (2026-08-15, see the living room correction above): a direct
  Bond percentage sweep against a single unambiguous HA entity is more reliable ground
  truth than correlating live button presses to narrated light-count states.** The
  latter produced a real, wrong mapping for living room (caught only because a physical
  contradiction forced a re-check) — a fast press-and-narrate sequence with the demod
  occasionally failing to decode is genuinely easy to mis-pair. Dining and bedroom's
  mappings were captured via the reliable Bond-sweep method already (not button
  narration) during the original Bond RF audit, so they don't need the same redo — but
  **if either ever needs re-verification, use a Bond sweep, not another button-press
  narration session.**
- **Re-record dining room (and living room) *power/light* button presses with
  real-time ground truth, not just replay the July 29 archives (queued, not urgent).**
  This is now lower-stakes for *speed* specifically (Bond sweeps are the trusted
  source for that), but power/light codes still come from the original archived
  captures with no logged ground truth beyond the directory name. Living room's
  power/light were spot-checked live and matched the archive exactly; dining room's
  haven't been.
- The `counter` helper design in `FAN_WALLSWITCH_SYNC.md`, which assumes the speed
  button sends one generic code and so needs a locally-tracked counter to know the
  resulting state, was built on the same now-disproven "jitter" assumption for
  living/dining, not just bedroom — needs reconsideration for all three switches, not
  just bedroom's automation design (which doesn't exist yet).

**Second proposed test (user, not yet run), on the Bond side rather than the RF side:**
set the fan to a known state using *only* the wall switch, then command Bond to a
specific speed. If the real fan state was already out of sync with whatever Bond
internally believes/tracks, the Bond command should fail to reach the intended target
speed. Useful for probing whether Bond's own speed-setting logic is itself
relative/assumed-state-based (in which case it's just as vulnerable to desync as the
wall-switch automations this whole project is trying to fix) or genuinely absolute.

## Next Steps

1. **Bedroom switch, RF work (this repo):**
   - Look up TR223A/KUJCE10321's actual carrier frequency and modulation from its FCC
     filing (fccid.io/KUJCE10321 test report PDF) rather than continuing to guess
     around 303.9-304.25MHz, which was based on the now-disproven same-model-as-98139
     assumption.
   - Once frequency is confirmed, retry capture with a calm, uninterrupted ~1 minute
     window — several attempts in the last session likely missed presses due to
     mid-session interruptions.
   - Fix/finish the `OOK_PWM` flex decoder so decoding doesn't depend on the
     Manchester-zerobit demod's lucky-but-jittery behavior (this applies to all
     switches, not just the bedroom one).
   - Build a small matcher (prefix-match on the stable leading bits, ignoring jittery
     trailing bits) that maps a decoded packet to `(switch, button)`, and extend
     `fan_wallswitch_bridge.py` / `rtl433-mqtt.sh` to cover the bedroom switch once
     its codes are known.
2. **Living/dining room, HA-side integration:** see `FAN_WALLSWITCH_SYNC.md` — next
   step there is one carefully supervised live test with the fixed bridge script before
   re-enabling the 6 disabled automations.
3. Decide the gas-meter/fan-monitor dongle-sharing approach long-term (permanent
   repurpose vs. a second dongle). For now, the meter services are being left stopped
   indefinitely per user preference — no need to restart them between capture sessions.
