from fastapi.testclient import TestClient
import pytest

from wificsi.protocol import Header, Hello, Packet
from wificsi.web.api import create_app
from wificsi.web.settings import DashboardSettings


NODE_ID = bytes.fromhex("288485872bf4")
PEER_ID = bytes.fromhex("5ee388d95b42")


def _settings():
    return DashboardSettings(udp_host="127.0.0.1", udp_port=0, http_host="127.0.0.1", http_port=8000)


def _hello_packet():
    hello = Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, PEER_ID)
    return Packet(Header(NODE_ID, 1, 1, 1000), hello)


def test_health_reports_udp_bound_and_empty_nodes():
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["udp_bound"] is True
        assert body["known_nodes"] == 0
        assert body["online_nodes"] == 0


def test_nodes_empty_list():
    with TestClient(create_app(_settings())) as client:
        body = client.get("/api/v1/nodes").json()
        assert body["schema_version"] == 1
        assert body["nodes"] == []


def test_nodes_returns_injected_node_sorted():
    with TestClient(create_app(_settings())) as client:
        runtime = client.app.state.runtime
        runtime.registry.accept(_hello_packet(), ("127.0.0.1", 5501))
        body = client.get("/api/v1/nodes").json()
        assert len(body["nodes"]) == 1
        node = body["nodes"][0]
        assert node["node_id"] == "28:84:85:87:2b:f4"
        assert node["presence"] == "UNKNOWN"
        assert node["presence_label"] == "初始化中"


def test_unknown_node_returns_404():
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/v1/nodes/02:00:00:00:00:01")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "node_not_found"


def test_malformed_mac_returns_422():
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/v1/nodes/not-a-mac")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_node_id"


def test_latest_csi_404_when_no_sample():
    with TestClient(create_app(_settings())) as client:
        runtime = client.app.state.runtime
        runtime.registry.accept(_hello_packet(), ("127.0.0.1", 5501))
        response = client.get("/api/v1/nodes/28:84:85:87:2b:f4/latest-csi")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "csi_not_available"


def test_websocket_hello_then_snapshot():
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["data"]["server_instance_id"]
            snapshot = ws.receive_json()
            assert snapshot["type"] == "nodes_snapshot"


def test_websocket_subscribe_all_then_resnapshot():
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # nodes_snapshot
            ws.send_json({"type": "subscribe", "schema_version": 1, "node_ids": []})
            snapshot = ws.receive_json()
            assert snapshot["type"] == "nodes_snapshot"


def test_websocket_invalid_message_returns_error():
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # nodes_snapshot
            ws.send_json({"type": "bogus"})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["data"]["code"] == "invalid_message"


def test_websocket_bad_subscription_returns_error():
    with TestClient(create_app(_settings())) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "subscribe", "schema_version": 1, "node_ids": ["zz"]})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["data"]["code"] == "invalid_subscription"
