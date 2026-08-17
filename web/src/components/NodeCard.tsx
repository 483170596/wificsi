import type { NodeSummary } from "../api/contracts";
import { PresenceBadge } from "./PresenceBadge";

function formatAge(ms: number | null): string {
  if (ms === null) return "–";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatMac(mac: string): { prefix: string; suffix: string } {
  const parts = mac.split(":");
  return {
    prefix: parts.slice(0, 4).join(":"),
    suffix: parts.slice(4).join(":"),
  };
}

export function NodeCard({
  node,
  selected,
  onSelect,
}: {
  node: NodeSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const { prefix, suffix } = formatMac(node.node_id);
  const warning = node.queue_dropped > 0 || node.udp_send_errors > 0 || node.sequence_gaps > 0;

  return (
    <button
      type="button"
      className={`node-card ${selected ? "selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <div className="node-card-header">
        <span className="node-mac">
          {prefix}:<strong>{suffix}</strong>
        </span>
        <PresenceBadge presence={node.presence} />
      </div>
      <dl className="node-card-metrics">
        <div>
          <dt>RSSI</dt>
          <dd>{node.rssi_dbm !== null ? `${node.rssi_dbm} dBm` : "–"}</dd>
        </div>
        <div>
          <dt>CSI 速率</dt>
          <dd>{node.csi_rate_hz !== null ? `${node.csi_rate_hz.toFixed(1)} Hz` : "–"}</dd>
        </div>
        <div>
          <dt>数据年龄</dt>
          <dd>{formatAge(node.last_seen_age_ms)}</dd>
        </div>
      </dl>
      {warning && (
        <div className="node-card-warning">
          丢包 {node.queue_dropped} · 发送错误 {node.udp_send_errors} · 缺口{" "}
          {node.sequence_gaps}
        </div>
      )}
    </button>
  );
}
