# Changelog

## 0.2.0 — 2026-10-12

- Restructured into a standard add-on repository layout (`matter_pair/`
  subdirectory + `repository.yaml`), installable as a store repository.
- Public documentation (README, DOCS, LICENSE).

## 0.1.4 — 2026-10-12

- Initial working version: commission Matter-over-Thread devices over the
  host's Bluetooth adapter via the Matter server WS API.
- Automates: disable HA Bluetooth integration → load Thread dataset from
  OTBR → `commission_with_code` → re-enable Bluetooth integration.
- Buttons to restart the Matter Server and re-enable the Bluetooth
  integration; live streamed pairing log; ingress sidebar panel.
