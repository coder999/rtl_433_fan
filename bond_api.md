# Bond Bridge Local API — Ceiling Fans

Notes on talking to the Bond Bridge directly via its Local API, bypassing the
Bond MCP server (which only wraps a subset of actions and can't patch tracked
state — see rationale below).

## Bridge

- **IP:** `192.168.0.110`
- **API base:** `http://192.168.0.110/v2`
- Auth header: `BOND-Token: <token>`

## API token (1Password)

The Bond local token is stored in 1Password, vault `CLI`, item
`bond-bridge-local`, field `credential`.

```bash
export BOND_TOKEN=$(op read "op://CLI/bond-bridge-local/credential")
```

If using a service-account `op` CLI (no default vault), pass the vault
explicitly by ID/name, or via `op item get bond-bridge-local --vault CLI`.
Never `echo`/print `$BOND_TOKEN` — pass it straight into `curl` headers.

## Device ID map

| Bond ID | Name | Location | Template |
|---|---|---|---|
| `ce4d90389da6937f` | Living Room Ceiling Fan | Living Room | (none) |
| `3e9252a7323111d2` | Ceiling fan | Master Bedroom | RCF161 |
| `33c72108a1a2548d` | Dining Room Ceiling Fan | Dining Room | B2 |

Re-derive this list any time with:

```bash
curl -s -H "BOND-Token: $BOND_TOKEN" "http://192.168.0.110/v2/devices" \
  | python3 -c "import json,sys; print(list(json.load(sys.stdin).keys()))"

# then, per id:
curl -s -H "BOND-Token: $BOND_TOKEN" "http://192.168.0.110/v2/devices/<id>" \
  | python3 -m json.tool   # name, location, template, actions
```

## Reading state

```bash
curl -s -H "BOND-Token: $BOND_TOKEN" \
  "http://192.168.0.110/v2/devices/<id>/state" | python3 -m json.tool
```

Fields for a ceiling fan device: `power` (0/1), `speed` (integer 1..max_speed),
`light` (0/1).

Max speed is per-device — check `properties`:

```bash
curl -s -H "BOND-Token: $BOND_TOKEN" \
  "http://192.168.0.110/v2/devices/<id>/properties" | python3 -m json.tool
# -> max_speed: 3 for the fans above, so speed 1/2/3 ≈ 33%/67%/100%
```

## Setting tracked state (no RF/IR transmit)

This is the API equivalent of the Bond app's **Settings → Fix Tracked
State** screen: it patches what the Bridge *believes* the device's state is,
without sending a signal to the fan. Use this when the physical remote and
the Bond have drifted out of sync (see `Trust Tracked State` in the app,
which suppresses redundant toggle transmits once tracked state is trusted).

```bash
curl -s -X PATCH -H "BOND-Token: $BOND_TOKEN" -H "Content-Type: application/json" \
  -d '{"power": 1, "speed": 3}' \
  "http://192.168.0.110/v2/devices/<id>/state" | python3 -m json.tool
```

### Important: `power` and `speed` are independent tracked fields

Setting `speed` alone does **not** imply `power: 1` in the tracked state —
the Bond fan has a separate on/off characteristic distinct from its speed
level. If you only patch `speed`, the tracked `power` field is left
unchanged (it can still read `0`/off even though speed is set). To
correctly reflect "fan running at speed N," set both fields in the same
PATCH:

```bash
curl -s -X PATCH -H "BOND-Token: $BOND_TOKEN" -H "Content-Type: application/json" \
  -d '{"power": 1, "speed": 3}' \
  "http://192.168.0.110/v2/devices/3e9252a7323111d2/state" | python3 -m json.tool
```

## Why not just use the Bond MCP server?

The MCP tools available in this environment
(`toggle_device_power`, `set_fan_speed`, `set_fan_direction`,
`set_light_brightness`, `control_shades`, `send_custom_action`,
`get_device_state`/`get_device_info`/`list_devices`) only wrap
**transmit-type** actions — they send an actual RF/IR command to the fan.
None of them expose the state-only `PATCH /v2/devices/{id}/state` endpoint,
so fixing drifted tracked state (without physically toggling the device)
requires calling the Local API directly as above.

## Worked example: Master Bedroom fan

```bash
export BOND_TOKEN=$(op read "op://CLI/bond-bridge-local/credential")
ID=3e9252a7323111d2

# Fix tracked light state to off
curl -s -X PATCH -H "BOND-Token: $BOND_TOKEN" -H "Content-Type: application/json" \
  -d '{"light": 0}' "http://192.168.0.110/v2/devices/$ID/state"

# Fix tracked power + speed to on / max (3)
curl -s -X PATCH -H "BOND-Token: $BOND_TOKEN" -H "Content-Type: application/json" \
  -d '{"power": 1, "speed": 3}' "http://192.168.0.110/v2/devices/$ID/state"
```
