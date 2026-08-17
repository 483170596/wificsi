"""FastAPI application: read-only REST routes and realtime WebSocket."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..registry import NodeLifecycle
from .models import build_node_summary, build_sensing_detail, csi_batch_data, parse_mac
from .runtime import DashboardRuntime
from .settings import DashboardSettings


class _ProtocolError(Exception):
    """A client violated the WebSocket JSON v1 contract."""


def create_app(settings: DashboardSettings) -> FastAPI:
    runtime = DashboardRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="WIFICSI Dashboard", lifespan=lifespan)

    @app.get("/api/v1/health")
    async def health(request: Request) -> dict:
        rt: DashboardRuntime = request.app.state.runtime
        nodes = rt.registry.list()
        online = sum(1 for node in nodes if node.lifecycle is not NodeLifecycle.OFFLINE)
        return {
            "status": "ok",
            "server_time_ms": int(time.time() * 1000),
            "udp_bound": rt.udp_bound,
            "udp_endpoint": rt.udp_endpoint,
            "publisher_hz": rt.settings.publish_hz,
            "known_nodes": len(nodes),
            "online_nodes": online,
        }

    @app.get("/api/v1/nodes")
    async def nodes(request: Request) -> dict:
        rt: DashboardRuntime = request.app.state.runtime
        return {
            "schema_version": 1,
            "server_time_ms": int(time.time() * 1000),
            **rt.snapshot_data(),
        }

    def _resolve_node(request: Request, node_id: str) -> bytes:
        try:
            node_id_bytes = parse_mac(node_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "invalid_node_id"}) from error
        rt: DashboardRuntime = request.app.state.runtime
        if rt.registry.get(node_id_bytes) is None:
            raise HTTPException(status_code=404, detail={"code": "node_not_found"})
        return node_id_bytes

    @app.get("/api/v1/nodes/{node_id}")
    async def node_detail(node_id: str, request: Request) -> dict:
        node_id_bytes = _resolve_node(request, node_id)
        rt: DashboardRuntime = request.app.state.runtime
        snapshot = rt.registry.get(node_id_bytes)
        now_mono = time.monotonic()
        summary = build_node_summary(
            snapshot,
            now_mono=now_mono,
            csi_rate_hz=rt.csi_rate_hz(node_id_bytes, now_mono),
        )
        sensing = build_sensing_detail(snapshot)
        return {
            "schema_version": 1,
            "server_time_ms": int(time.time() * 1000),
            "node": summary.model_dump(mode="json"),
            "sensing": sensing.model_dump(mode="json") if sensing is not None else None,
        }

    @app.get("/api/v1/nodes/{node_id}/latest-csi")
    async def latest_csi(node_id: str, request: Request) -> dict:
        node_id_bytes = _resolve_node(request, node_id)
        rt: DashboardRuntime = request.app.state.runtime
        sample = rt.hub.latest(node_id_bytes)
        if sample is None:
            raise HTTPException(status_code=404, detail={"code": "csi_not_available"})
        payload = csi_batch_data(sample)
        payload["sample_pair_count"] = len(sample.amplitude)
        return {
            "schema_version": 1,
            "server_time_ms": int(time.time() * 1000),
            **payload,
        }

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        rt: DashboardRuntime = websocket.app.state.runtime
        connection = rt.connections.add()
        now_wall = time.time()
        await websocket.send_json(rt.envelope("hello", {
            "server_instance_id": rt.server_instance_id,
            "publish_hz": rt.settings.publish_hz,
            "offline_after_ms": int(rt.settings.offline_after_s * 1000),
        }, now_wall))
        await websocket.send_json(rt.envelope("nodes_snapshot", rt.snapshot_data(), now_wall))

        async def sender() -> None:
            try:
                while True:
                    events = await connection.mailbox.next_event()
                    if not events:
                        if connection.mailbox.closed:
                            break
                        continue
                    for event in events:
                        await websocket.send_json(event)
            except Exception:
                pass

        async def pinger() -> None:
            try:
                while True:
                    await asyncio.sleep(20)
                    await websocket.send_json(rt.envelope("ping", {}))
            except Exception:
                pass

        sender_task = asyncio.create_task(sender())
        pinger_task = asyncio.create_task(pinger())
        protocol_errors = 0
        try:
            while True:
                try:
                    message = await websocket.receive_json()
                except WebSocketDisconnect:
                    break
                except Exception:
                    protocol_errors += 1
                    await _send_error(websocket, rt, "invalid_message", "expected a JSON object")
                    if protocol_errors >= 3:
                        await websocket.close(code=1008)
                        break
                    continue

                if not isinstance(message, dict) or message.get("type") != "subscribe":
                    protocol_errors += 1
                    await _send_error(websocket, rt, "invalid_message", "expected a subscribe message")
                    if protocol_errors >= 3:
                        await websocket.close(code=1008)
                        break
                    continue

                try:
                    subscription = _parse_subscription(message)
                except _ProtocolError as error:
                    protocol_errors += 1
                    await _send_error(websocket, rt, "invalid_subscription", str(error))
                    if protocol_errors >= 3:
                        await websocket.close(code=1008)
                        break
                    continue

                rt.connections.set_subscription(connection, subscription)
                await websocket.send_json(
                    rt.envelope("nodes_snapshot", rt.snapshot_data())
                )
        finally:
            sender_task.cancel()
            pinger_task.cancel()
            rt.connections.remove(connection)

    _mount_spa(app, settings)
    return app


def _parse_subscription(message: dict) -> set[bytes] | None:
    if message.get("schema_version") != 1:
        raise _ProtocolError("schema_version must be 1")
    raw_ids = message.get("node_ids")
    if not isinstance(raw_ids, list):
        raise _ProtocolError("node_ids must be a list")
    if len(raw_ids) > 16:
        raise _ProtocolError("node_ids must contain at most 16 valid MAC addresses")
    parsed: set[bytes] = set()
    for raw in raw_ids:
        if not isinstance(raw, str):
            raise _ProtocolError("node_ids must contain only strings")
        try:
            parsed.add(parse_mac(raw))
        except ValueError:
            raise _ProtocolError("node_ids must contain valid MAC addresses") from None
    return parsed if parsed else None


async def _send_error(websocket: WebSocket, rt: DashboardRuntime, code: str, message: str) -> None:
    await websocket.send_json(rt.envelope("error", {"code": code, "message": message}))


def _mount_spa(app: FastAPI, settings: DashboardSettings) -> None:
    dist = settings.web_dist
    if not dist:
        return
    dist_path = Path(dist)
    if not dist_path.is_dir() or not (dist_path / "index.html").is_file():
        return
    app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="web")
