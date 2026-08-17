import { useEffect, useReducer } from "react";
import { fetchNodes } from "../api/http";
import { createSocket } from "../api/socket";
import type { ConnectionStatus, NodeSummary } from "../api/contracts";
import { initialState, reducer } from "../state/dashboard";
import { NodeGrid } from "../components/NodeGrid";
import { PresenceBadge } from "../components/PresenceBadge";
import { CsiAmplitudeChart } from "../components/CsiAmplitudeChart";
import { SignalHistoryChart } from "../components/SignalHistoryChart";

const CONNECTION_LABEL: Record<ConnectionStatus, string> = {
  CONNECTING: "连接中",
  OPEN: "已连接",
  RETRYING: "重连中",
  CLOSED: "已断开",
};

export function DashboardPage() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  useEffect(() => {
    let cancelled = false;
    fetchNodes()
      .then((response) => {
        if (!cancelled) dispatch({ type: "SNAPSHOT", nodes: response.nodes });
      })
      .catch(() => {
        /* the websocket will resync when it connects */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const socket = createSocket({
      onConnectionChange: (status) => dispatch({ type: "CONNECTION", status }),
      onEvent: (event) => {
        switch (event.type) {
          case "hello":
            dispatch({ type: "HELLO", serverInstanceId: event.data.server_instance_id });
            break;
          case "nodes_snapshot":
            dispatch({ type: "SNAPSHOT", nodes: event.data.nodes });
            break;
          case "node_status":
            dispatch({ type: "NODE_STATUS", node: event.data });
            break;
          case "csi_batch":
            dispatch({ type: "CSI_BATCH", batch: event.data });
            break;
          default:
            break;
        }
      },
    });
    return () => socket.close();
  }, []);

  const nodes = Object.values(state.nodes);
  const onlineCount = nodes.filter((node) => node.presence !== "OFFLINE").length;
  const selectedNode = state.selectedNodeId ? state.nodes[state.selectedNodeId] : null;
  const selectedStream = state.selectedNodeId ? state.streams[state.selectedNodeId] : null;

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>WIFICSI 多节点感知看板</h1>
        <div className="dashboard-status">
          <span className={`connection-badge connection-${state.connection.toLowerCase()}`}>
            {CONNECTION_LABEL[state.connection]}
          </span>
          <span className="online-count">
            在线 {onlineCount} / 共 {nodes.length} 节点
          </span>
        </div>
      </header>

      {state.connection !== "OPEN" && (
        <div className="connection-banner">服务器连接中断，正在重连…（节点状态为最后已知值）</div>
      )}

      <NodeGrid
        nodes={nodes}
        selectedNodeId={state.selectedNodeId}
        onSelect={(nodeId) => dispatch({ type: "SELECT", nodeId })}
      />

      {selectedNode ? (
        <section className="node-detail">
          <NodeDetailHeader node={selectedNode} />
          <div className="charts-row">
            <CsiAmplitudeChart amplitude={selectedStream?.latest?.amplitude ?? null} />
            <SignalHistoryChart history={selectedStream?.history ?? []} />
          </div>
          <HealthPanel node={selectedNode} />
        </section>
      ) : (
        <section className="node-detail empty-detail">选择一个节点查看实时 CSI 与状态</section>
      )}
    </div>
  );
}

function NodeDetailHeader({ node }: { node: NodeSummary }) {
  return (
    <div className="node-detail-header">
      <span className="node-mac">{node.node_id}</span>
      <PresenceBadge presence={node.presence} />
      <span className="raw-state">
        原始状态 {node.lifecycle}
      </span>
      <span className="boot-id">boot_id {node.boot_id}</span>
    </div>
  );
}

function HealthPanel({ node }: { node: NodeSummary }) {
  const metrics: Array<[string, string | number]> = [
    ["接收包数", node.accepted_packets],
    ["序列缺口", node.sequence_gaps],
    ["重复包", node.duplicates],
    ["乱序包", node.out_of_order],
    ["设备队列丢弃", node.queue_dropped],
    ["设备发送错误", node.udp_send_errors],
    ["空闲堆 (B)", node.free_heap_bytes ?? "–"],
    ["固件版本", node.firmware ?? "–"],
    ["AP BSSID", node.ap_bssid ?? "–"],
    ["信道", node.channel ?? "–"],
  ];

  return (
    <div className="health-panel">
      <h3>链路与节点健康</h3>
      <dl className="health-grid">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className={isWarnLabel(label) && value !== 0 && value !== "–" ? "health-warn" : ""}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function isWarnLabel(label: string): boolean {
  return label === "序列缺口" || label === "重复包" || label === "乱序包" || label === "设备队列丢弃" || label === "设备发送错误";
}
