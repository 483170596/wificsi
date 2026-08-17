import type { Presence } from "../api/contracts";

const CONFIG: Record<Presence, { label: string; className: string }> = {
  PRESENT: { label: "有人", className: "presence-present" },
  ABSENT: { label: "无人", className: "presence-absent" },
  UNKNOWN: { label: "初始化中", className: "presence-unknown" },
  OFFLINE: { label: "离线", className: "presence-offline" },
};

export function PresenceBadge({
  presence,
  label,
}: {
  presence: Presence;
  label?: string;
}) {
  const config = CONFIG[presence];
  return (
    <span className={`presence-badge ${config.className}`}>
      {label ?? config.label}
    </span>
  );
}
