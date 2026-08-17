import type { ConnectionStatus, CsiBatch, NodeSummary } from "../api/contracts";

export interface SignalPoint {
  t: number;
  rms: number;
  variance: number;
  rssi: number;
}

export interface NodeStream {
  bootId: number;
  latest: CsiBatch | null;
  history: SignalPoint[];
}

export interface DashboardState {
  connection: ConnectionStatus;
  serverInstanceId: string | null;
  nodes: Record<string, NodeSummary>;
  streams: Record<string, NodeStream>;
  selectedNodeId: string | null;
}

export type Action =
  | { type: "CONNECTION"; status: ConnectionStatus }
  | { type: "SNAPSHOT"; nodes: NodeSummary[] }
  | { type: "HELLO"; serverInstanceId: string }
  | { type: "NODE_STATUS"; node: NodeSummary }
  | { type: "CSI_BATCH"; batch: CsiBatch }
  | { type: "SELECT"; nodeId: string };

export const HISTORY_LIMIT = 600;

export function initialState(): DashboardState {
  return {
    connection: "CONNECTING",
    serverInstanceId: null,
    nodes: {},
    streams: {},
    selectedNodeId: null,
  };
}

function selectDefault(nodes: NodeSummary[], current: string | null): string | null {
  if (current) return current;
  const online = nodes.find((node) => node.presence !== "OFFLINE");
  return online?.node_id ?? nodes[0]?.node_id ?? null;
}

export function reducer(state: DashboardState, action: Action): DashboardState {
  switch (action.type) {
    case "CONNECTION":
      return { ...state, connection: action.status };

    case "SNAPSHOT": {
      const nodes: Record<string, NodeSummary> = {};
      for (const node of action.nodes) {
        nodes[node.node_id] = node;
      }
      const streams: Record<string, NodeStream> = { ...state.streams };
      for (const node of action.nodes) {
        const stream = streams[node.node_id];
        if (stream && stream.bootId !== node.boot_id) {
          delete streams[node.node_id];
        }
      }
      return {
        ...state,
        connection: "OPEN",
        nodes,
        streams,
        selectedNodeId: selectDefault(action.nodes, state.selectedNodeId),
      };
    }

    case "HELLO": {
      const changed =
        state.serverInstanceId !== null &&
        state.serverInstanceId !== action.serverInstanceId;
      return {
        ...state,
        serverInstanceId: action.serverInstanceId,
        streams: changed ? {} : state.streams,
      };
    }

    case "NODE_STATUS": {
      const node = action.node;
      return { ...state, nodes: { ...state.nodes, [node.node_id]: node } };
    }

    case "CSI_BATCH": {
      const batch = action.batch;
      const existing = state.streams[batch.node_id];
      const history =
        existing && existing.bootId === batch.boot_id ? existing.history : [];
      const point: SignalPoint = {
        t: Date.now(),
        rms: batch.rms_amplitude,
        variance: batch.variance,
        rssi: batch.rssi_dbm,
      };
      const nextHistory = [...history, point];
      if (nextHistory.length > HISTORY_LIMIT) {
        nextHistory.splice(0, nextHistory.length - HISTORY_LIMIT);
      }
      return {
        ...state,
        streams: {
          ...state.streams,
          [batch.node_id]: {
            bootId: batch.boot_id,
            latest: batch,
            history: nextHistory,
          },
        },
      };
    }

    case "SELECT":
      return { ...state, selectedNodeId: action.nodeId };

    default:
      return state;
  }
}
