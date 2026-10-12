# Matter Pair

Commission Matter-over-Thread devices using this machine's Bluetooth adapter —
no phone needed.

## Prerequisites

- **Matter Server** add-on with `bluetooth_adapter_id: 0` in its YAML
  configuration (restart it after adding).
- **OpenThread Border Router** add-on running your Thread network.
- A working Bluetooth adapter on the Home Assistant machine.

## Usage

1. Put the device in pairing mode (re-seat its battery or press its pairing
   button) and keep it within a few meters of the Home Assistant machine.
2. Open the **Matter Pair** panel (enable "Show in sidebar" on the add-on
   page, or use *Open Web UI*).
3. Enter the pairing code — 11 or 21 digits, dashes/spaces allowed, or the
   full `MT:` payload from the QR code — and press **Pair device**.
4. Follow the streamed log. On success the device shows up under
   Settings → Devices & services → Matter.

Each attempt automatically: disables the HA Bluetooth integration (it starves
the Matter SDK's BLE scan), loads the current Thread dataset from OTBR into
the Matter server, commissions over BLE, and re-enables the Bluetooth
integration afterwards — also on failure.

## Troubleshooting

| Symptom | Fix |
|---|---|
| BLE scan timeout | Press **Restart Matter Server**, wait, pair again |
| Discovery timed out | Re-seat the device battery, move it closer |
| "Matter Server has no Bluetooth" | Add `bluetooth_adapter_id: 0` to the Matter Server add-on config, restart it |
| Bluetooth integration stuck disabled | Press **Enable Bluetooth integration** |

## Developer notes (local iteration)

The add-on builds locally from source; the Supervisor needs `build.yaml` for
the base image. Push a new version with:

```bash
# bump version: in matter_pair/config.yaml first
tar -czf - matter_pair | ssh <ha-host> 'cd /addons/local && tar -xzf -'
ssh <ha-host> 'ha store reload && ha addons update local_matter_pair'
```

Gotcha: the Supervisor does not place `SUPERVISOR_TOKEN` in the process
environment — s6-overlay v3 parks container env vars as files under
`/run/s6/container_environment/`, and the backend reads the token from there.
