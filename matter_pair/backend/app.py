"""Matter Pair - commission Matter-over-Thread devices via the host's Bluetooth.

Orchestrates what is otherwise a manual dance:
  1. disable HA's Bluetooth integration (it starves the Matter SDK's BLE scan)
  2. load the current Thread dataset from OTBR into the Matter server
     (it does not persist across Matter Server restarts)
  3. commission_with_code over the server's BLE adapter
  4. re-enable the Bluetooth integration

Talks to: python-matter-server WS API, HA core WS API (via supervisor proxy),
OTBR REST API, and the supervisor REST API (to restart the Matter server).
"""
import asyncio
import json
import os
import re

import aiohttp
from aiohttp import web

def _token() -> str:
    # s6-overlay v3 scrubs container env; docker-injected vars live as files here
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if not tok:
        try:
            with open("/run/s6/container_environment/SUPERVISOR_TOKEN") as f:
                tok = f.read().strip()
        except OSError:
            pass
    if not tok:
        raise SystemExit("[matter-pair] no supervisor token in env or s6 container_environment")
    return tok


SUPERVISOR_TOKEN = _token()
MATTER_WS = "ws://core-matter-server:5580/ws"
CORE_WS = "ws://supervisor/core/websocket"
OTBR_DATASET_URL = "http://core-openthread-border-router:8081/node/dataset/active"
SUPERVISOR = "http://supervisor"

COMMISSION_TIMEOUT = 180  # seconds for the commission_with_code call itself


class MatterClient:
    """Minimal client for the python-matter-server WebSocket API."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._ws = None
        self._msg_id = 0
        self.server_info = None

    async def connect(self):
        self._ws = await self._session.ws_connect(MATTER_WS, max_msg_size=16 * 1024 * 1024)
        self.server_info = json.loads((await self._ws.receive()).data)
        return self

    async def close(self):
        if self._ws is not None:
            await self._ws.close()

    async def cmd(self, command: str, args: dict | None = None, timeout: float = 30):
        self._msg_id += 1
        msg_id = str(self._msg_id)
        await self._ws.send_json(
            {"message_id": msg_id, "command": command, "args": args or {}}
        )
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            msg = await asyncio.wait_for(self._ws.receive(), timeout=remaining)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError(f"matter ws closed unexpectedly ({msg.type})")
            data = json.loads(msg.data)
            if data.get("message_id") != msg_id:
                continue  # event or other traffic
            if "error_code" in data:
                raise RuntimeError(data.get("details") or f"error {data['error_code']}")
            return data.get("result")


class CoreClient:
    """Minimal client for the HA core WebSocket API via the supervisor proxy."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._ws = None
        self._msg_id = 0

    async def connect(self):
        self._ws = await self._session.ws_connect(CORE_WS)
        first = json.loads((await self._ws.receive()).data)
        if first.get("type") == "auth_required":
            await self._ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
            reply = json.loads((await self._ws.receive()).data)
            if reply.get("type") != "auth_ok":
                raise RuntimeError(f"core API auth failed: {reply}")
        return self

    async def close(self):
        if self._ws is not None:
            await self._ws.close()

    async def cmd(self, **msg):
        self._msg_id += 1
        msg["id"] = self._msg_id
        await self._ws.send_json(msg)
        while True:
            data = json.loads((await asyncio.wait_for(self._ws.receive(), timeout=30)).data)
            if data.get("id") != self._msg_id:
                continue
            if not data.get("success", False):
                raise RuntimeError(f"core command failed: {data.get('error')}")
            return data.get("result")

    async def bluetooth_entries(self):
        entries = await self.cmd(type="config_entries/get")
        return [e for e in entries if e["domain"] == "bluetooth"]

    async def set_entry_disabled(self, entry_id: str, disabled: bool):
        await self.cmd(
            type="config_entries/disable",
            entry_id=entry_id,
            disabled_by="user" if disabled else None,
        )


def normalize_code(raw: str) -> str:
    raw = raw.strip()
    if raw.upper().startswith("MT:"):
        return raw  # QR payload, pass through
    digits = re.sub(r"\D", "", raw)
    if len(digits) not in (11, 21):
        raise ValueError(
            f"pairing code must be 11 or 21 digits (got {len(digits)}) or an MT: QR payload"
        )
    return digits


async def fetch_thread_dataset(session: aiohttp.ClientSession) -> str:
    async with session.get(
        OTBR_DATASET_URL, headers={"Accept": "text/plain"}
    ) as resp:
        resp.raise_for_status()
        dataset = (await resp.text()).strip()
    if not re.fullmatch(r"[0-9A-Fa-f]+", dataset or ""):
        raise RuntimeError(f"OTBR returned an unexpected dataset: {dataset[:40]!r}")
    return dataset


def describe_node(node: dict) -> str:
    attrs = node.get("attributes", {})
    vendor = attrs.get("0/40/1", "?")
    product = attrs.get("0/40/3", "?")
    return f"node {node.get('node_id')}: {vendor} - {product}"


async def pair_handler(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    resp.enable_chunked_encoding()
    await resp.prepare(request)

    async def log(line: str):
        await resp.write((line + "\n").encode())

    session = request.app["session"]
    re_enable_entry = None
    core = None
    try:
        code = normalize_code(body.get("code", ""))
        await log(f"▸ pairing code accepted ({'QR' if code.startswith('MT:') else 'manual'})")

        matter = await MatterClient(session).connect()
        try:
            info = matter.server_info
            await log(
                f"▸ matter server up (SDK {info.get('sdk_version')}, "
                f"bluetooth_enabled={info.get('bluetooth_enabled')})"
            )
            if not info.get("bluetooth_enabled"):
                raise RuntimeError(
                    "Matter Server has no Bluetooth: set bluetooth_adapter_id: 0 "
                    "in the Matter Server add-on config and restart it"
                )

            core = await CoreClient(session).connect()
            bt = await core.bluetooth_entries()
            active = [e for e in bt if e.get("disabled_by") is None]
            if active:
                entry = active[0]
                await log(f"▸ disabling Bluetooth integration ({entry['title']}) …")
                await core.set_entry_disabled(entry["entry_id"], True)
                re_enable_entry = entry["entry_id"]
                await asyncio.sleep(3)  # let BlueZ release the adapter
            else:
                await log("▸ Bluetooth integration already disabled or absent")

            await log("▸ fetching Thread dataset from OTBR …")
            dataset = await fetch_thread_dataset(session)
            await matter.cmd("set_thread_dataset", {"dataset": dataset})
            await log("▸ Thread credentials loaded into Matter server")

            await log("▸ commissioning over Bluetooth — keep the device in pairing "
                      "mode near this machine (can take 1-2 min) …")
            node = await matter.cmd(
                "commission_with_code",
                {"code": code, "network_only": False},
                timeout=COMMISSION_TIMEOUT,
            )
            await log(f"✔ SUCCESS: {describe_node(node)}")
            await log("  The device appears in Settings → Devices & services → Matter.")
        finally:
            await matter.close()
    except Exception as err:  # noqa: BLE001 - everything lands in the UI log
        await log(f"✖ FAILED: {err}")
        msg = str(err)
        if "Timeout" in msg or "Discovery timed out" in msg or "failed for node" in msg:
            await log("  Hints: re-seat the device battery (pairing window is short), "
                      "move it closer to this machine,")
            await log("  or hit 'Restart Matter Server' below if the BLE scan is wedged, "
                      "then try again.")
    finally:
        if re_enable_entry and core is not None:
            try:
                await core.set_entry_disabled(re_enable_entry, False)
                await resp.write("▸ Bluetooth integration re-enabled\n".encode())
            except Exception as err:  # noqa: BLE001
                await resp.write(
                    f"✖ could not re-enable Bluetooth integration: {err} — "
                    "re-enable it manually in Settings → Devices & services\n".encode()
                )
        if core is not None:
            await core.close()
    await resp.write_eof()
    return resp


async def status_handler(request: web.Request) -> web.Response:
    session = request.app["session"]
    out = {}
    try:
        matter = await MatterClient(session).connect()
        out["matter"] = matter.server_info
        await matter.close()
    except Exception as err:  # noqa: BLE001
        out["matter_error"] = str(err)
    try:
        core = await CoreClient(session).connect()
        out["bluetooth_entries"] = [
            {"title": e["title"], "disabled": e["disabled_by"] is not None}
            for e in await core.bluetooth_entries()
        ]
        await core.close()
    except Exception as err:  # noqa: BLE001
        out["core_error"] = str(err)
    return web.json_response(out)


async def bluetooth_handler(request: web.Request) -> web.Response:
    """Manually toggle HA's Bluetooth integration (escape hatch)."""
    disabled = bool((await request.json()).get("disabled"))
    core = await CoreClient(request.app["session"]).connect()
    try:
        entries = await core.bluetooth_entries()
        if not entries:
            return web.json_response({"ok": False, "error": "no bluetooth entry"}, status=404)
        for entry in entries:
            await core.set_entry_disabled(entry["entry_id"], disabled)
    finally:
        await core.close()
    return web.json_response({"ok": True, "disabled": disabled})


async def restart_matter_handler(request: web.Request) -> web.Response:
    session = request.app["session"]
    async with session.post(
        f"{SUPERVISOR}/addons/core_matter_server/restart"
    ) as resp:
        if resp.status != 200:
            return web.json_response(
                {"ok": False, "error": await resp.text()}, status=502
            )
    # wait for the WS to come back
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            matter = await MatterClient(session).connect()
            await matter.close()
            return web.json_response({"ok": True})
        except Exception:  # noqa: BLE001
            continue
    return web.json_response({"ok": False, "error": "matter server did not come back"})


async def index_handler(request: web.Request) -> web.Response:
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


async def make_app() -> web.Application:
    app = web.Application()
    app["session"] = aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    )
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/status", status_handler)
    app.router.add_post("/api/pair", pair_handler)
    app.router.add_post("/api/bluetooth", bluetooth_handler)
    app.router.add_post("/api/restart-matter", restart_matter_handler)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=8099)
