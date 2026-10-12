# Matter Pair

Local Home Assistant add-on that commissions Matter-over-Thread devices using
the HA machine's own Bluetooth adapter — no phone / companion app needed
(the Android 17 GMS Wi-Fi bug makes the phone flow unusable anyway).

Appears as a sidebar panel ("Matter Pair"). Enter the device's 11/21-digit
pairing code (or the `MT:` QR payload), keep the device in pairing mode within
BLE range of the HA box, hit **Pair device**.

Per attempt it automatically:

1. disables HA's Bluetooth integration (it starves the Matter SDK's BLE scan),
2. loads the live Thread dataset from the OTBR add-on into the Matter server
   (not persisted there across restarts),
3. runs `commission_with_code` over BLE,
4. re-enables the Bluetooth integration (always, also on failure).

Requirements on the HA box:

- Matter Server add-on with `bluetooth_adapter_id: 0` in its config,
- OpenThread Border Router add-on (REST on port 8081),
- a working Bluetooth adapter (hci0).

Deploy (see `~/.claude/skills/ha-addon-deploy`): bump `version:` in
`config.yaml`, then

```bash
cd ~/workspace
tar --exclude=.git -czf - matter-pair | ssh ha 'cd /addons/local && tar -xzf -'
ssh ha 'ha store reload && ha addons update local_matter_pair'   # or install
```
