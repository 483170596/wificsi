"""Dashboard runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    udp_host: str = "10.204.75.168"
    udp_port: int = 5500
    http_host: str = "10.204.75.168"
    http_port: int = 8000
    publish_hz: float = 10.0
    offline_after_s: float = 5.0
    max_nodes: int = 16
    max_history: int = 100
    web_dist: str | None = None
