import type { ConnectionStatus, ServerEvent } from "./contracts";

export interface SocketHandlers {
  onConnectionChange: (status: ConnectionStatus) => void;
  onEvent: (event: ServerEvent) => void;
}

const BACKOFF_MS = [1000, 2000, 4000, 8000];

function buildUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/v1/ws`;
}

export function createSocket(handlers: SocketHandlers): { close: () => void } {
  let socket: WebSocket | null = null;
  let closed = false;
  let retryIndex = 0;
  let reconnectTimer: number | null = null;

  const clearTimer = () => {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const scheduleReconnect = () => {
    if (closed) return;
    handlers.onConnectionChange("RETRYING");
    const delay = retryIndex < BACKOFF_MS.length ? BACKOFF_MS[retryIndex] : 10_000;
    retryIndex += 1;
    clearTimer();
    reconnectTimer = window.setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    clearTimer();
    handlers.onConnectionChange("CONNECTING");
    socket = new WebSocket(buildUrl());

    socket.onopen = () => {
      retryIndex = 0;
      handlers.onConnectionChange("OPEN");
      socket?.send(
        JSON.stringify({ type: "subscribe", schema_version: 1, node_ids: [] }),
      );
    };

    socket.onmessage = (message) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(message.data) as ServerEvent;
      } catch {
        return;
      }
      handlers.onEvent(event);
    };

    socket.onclose = () => {
      if (closed) return;
      handlers.onConnectionChange("CLOSED");
      scheduleReconnect();
    };

    socket.onerror = () => {
      socket?.close();
    };
  };

  const reconnectNow = () => {
    if (closed) return;
    retryIndex = 0;
    clearTimer();
    connect();
  };

  const onVisibility = () => {
    if (document.visibilityState === "visible") reconnectNow();
  };
  const onOnline = () => reconnectNow();

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("online", onOnline);

  connect();

  return {
    close: () => {
      closed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      socket?.close();
    },
  };
}
