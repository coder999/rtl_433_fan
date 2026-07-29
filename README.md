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

## Status

**Blocked on physical proximity.** The Pi lives near the gas meter, which turned out to
be too far from the fan wall switches — an initial capture attempt (Pi in its normal
location, wide bandwidth, high gain, multiple button presses) picked up nothing. Before
capturing again, the Pi (or at least the RTL-SDR dongle, on a USB extension cable) needs
to be physically relocated to within a few feet of one wall switch.

## Next Steps

1. Relocate the Pi or dongle near one wall switch. Confirm it's still reachable over
   WiFi/SSH.
2. Stop the gas meter services (or just start `rtl433-mqtt.service`, which will stop
   them automatically):
   ```bash
   sudo systemctl stop rtlamr-mqtt.service rtl-tcp.service meter-health-mqtt.service
   ```
3. Capture while pressing one button on one switch repeatedly:
   ```bash
   rtl_433 -f 304250000 -s 2048000 -g 49.6 -A -R 0 -T 35
   ```
   If still silent, try a lower detection threshold with `-Y level=-3`.
4. Once pulses appear, analyze the timing/bit pattern per button per switch — need to
   distinguish all 3 switches (differing DIP-switch code) and each switch's buttons
   (differing subcode: off/speed 1-3/light).
5. Write and test a custom rtl_433 flex decoder (`-X`) matching the observed pulse
   train.
6. Update `rtl433-mqtt.sh` to the correct frequency and decoder, and wire decoded
   events into an MQTT topic.
7. Build a Home Assistant automation that corrects the Bond fan's assumed on/off/speed
   state when a wall-switch code is seen — without re-issuing the Bond command, to avoid
   a feedback loop.
8. Revisit the gas-meter/fan-monitor dongle-sharing question (permanent repurpose vs.
   a second dongle) once continuous fan monitoring is proven to work.
