import type { NodeSummary } from "../api/contracts";
import { NodeCard } from "./NodeCard";

function byOnlineThenId(a: NodeSummary, b: NodeSummary): number {
  const aOffline = a.presence === "OFFLINE" ? 1 : 0;
  const bOffline = b.presence === "OFFLINE" ? 1 : 0;
  if (aOffline !== bOffline) return aOffline - bOffline;
  return a.node_id.localeCompare(b.node_id);
}

export function NodeGrid({
  nodes,
  selectedNodeId,
  onSelect,
}: {
  nodes: NodeSummary[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const sorted = [...nodes].sort(byOnlineThenId);
  return (
    <div className="node-grid">
      {sorted.map((node) => (
        <NodeCard
          key={node.node_id}
          node={node}
          selected={node.node_id === selectedNodeId}
          onSelect={() => onSelect(node.node_id)}
        />
      ))}
    </div>
  );
}
