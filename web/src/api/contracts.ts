// TypeScript types mirroring the JSON v1 Web API contract.

export type Presence = "PRESENT" | "ABSENT" | "UNKNOWN" | "OFFLINE";

export type ConnectionStatus = "CONNECTING" | "OPEN" | "RETRYING" | "CLOSED";

export interface NodeSummary {
  node_id: string;
  boot_id: number;
  lifecycle: string;
  presence: Presence;
  presence_label: string;
  last_seen_age_ms: number | null;
  firmware: string | null;
  ap_bssid: string | null;
  channel: number | null;
  rssi_dbm: number | null;
  csi_rate_hz: number | null;
  accepted_packets: number;
  sequence_gaps: number;
  duplicates: number;
  out_of_order: number;
  queue_dropped: number;
  udp_send_errors: number;
  free_heap_bytes: number | null;
  csi_available: boolean;
  sensing_available: boolean;
}

export interface CsiBatch {
  node_id: string;
  boot_id: number;
  device_time_us: number;
  rssi_dbm: number;
  channel: number;
  amplitude: number[];
  mean_amplitude: number;
  rms_amplitude: number;
  variance: number;
  source_frames: number;
  ui_frames_coalesced: number;
}

export interface NodesResponse {
  schema_version: number;
  server_time_ms: number;
  nodes: NodeSummary[];
}

interface Envelope<T> {
  type: string;
  schema_version: number;
  event_id: number;
  server_time_ms: number;
  data: T;
}

export interface HelloData {
  server_instance_id: string;
  publish_hz: number;
  offline_after_ms: number;
}

export interface SnapshotData {
  nodes: NodeSummary[];
}

export interface ErrorData {
  code: string;
  message: string;
}

export type ServerEvent =
  | Envelope<HelloData> & { type: "hello" }
  | Envelope<SnapshotData> & { type: "nodes_snapshot" }
  | Envelope<NodeSummary> & { type: "node_status" }
  | Envelope<CsiBatch> & { type: "csi_batch" }
  | Envelope<ErrorData> & { type: "error" }
  | Envelope<Record<string, never>> & { type: "ping" };

export function normalizeNodeId(nodeId: string): string {
  return nodeId.toLowerCase();
}
