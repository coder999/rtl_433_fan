# Ceiling Fan Wall Switch RF Capture

Goal: use the Raspberry Pi's RTL-SDR + `rtl_433` to capture and decode the RF signal
sent by the wall switches for the Ashby Park ceiling fans, so Home Assistant can detect
wall-switch use and stay in sync with the Bond Bridge's assumed fan state.

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

### Decoded codes so far

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
  room/dining room switches.** This raises a real possibility it's a different SKU
  entirely, possibly on a different carrier frequency, not just a farther-away unit of
  the same model 98139. Not yet confirmed by checking the physical model
  number/FCC ID on the unit itself (would require popping it off the wall) — this is
  the most promising lead for next time.

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

## Next Steps

1. **Before another live capture attempt**: check the bedroom switch's actual model
   number / FCC ID (may require popping it off the wall plate, or checking the
   original box/manual if kept) to confirm or rule out the different-SKU/
   different-frequency theory. This is more likely to unblock things than further
   gain/distance guessing.
2. Once frequency is confirmed (or if sticking with 303.9-304.25MHz), retry capture
   dedicating a calm, uninterrupted ~1 minute window — several attempts this session
   likely missed presses due to mid-session interruptions.
3. Fix/finish the `OOK_PWM` flex decoder so decoding doesn't depend on the
   Manchester-zerobit demod's lucky-but-jittery behavior.
4. Build a small matcher (prefix-match on the stable leading bits, ignoring jittery
   trailing bits) that maps a decoded packet to `(switch, button)`.
5. Update `rtl433-mqtt.sh` to the correct frequency and wire the matcher's output into
   an MQTT topic per switch/button event.
6. Build a Home Assistant automation that reacts to a wall-switch event by refreshing/
   correcting the Bond fan's assumed state — for power/light toggles this can directly
   flip assumed state; for the speed toggle it should trigger a Bond status poll since
   the exact resulting speed isn't recoverable from the RF code alone.
7. Decide the gas-meter/fan-monitor dongle-sharing approach long-term (permanent
   repurpose vs. a second dongle). For now, the meter services are being left stopped
   indefinitely per user preference — no need to restart them between capture sessions.
