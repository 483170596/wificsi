import { describe, expect, it } from "vitest";
import type { CsiBatch, NodeSummary } from "../api/contracts";
import { HISTORY_LIMIT, initialState, reducer } from "./dashboard";

function node(node_id: string, presence: NodeSummary["presence"], boot_id = 1): NodeSummary {
  return {
    node_id,
    boot_id,
    lifecycle: "ACTIVE",
    presence,
    presence_label: "",
    last_seen_age_ms: 0,
    firmware: null,
    ap_bssid: null,
    channel: null,
    rssi_dbm: null,
    csi_rate_hz: null,
    accepted_packets: 0,
    sequence_gaps: 0,
    duplicates: 0,
    out_of_order: 0,
    queue_dropped: 0,
    udp_send_errors: 0,
    free_heap_bytes: null,
    csi_available: false,
    sensing_available: false,
  };
}

function batch(node_id: string, boot_id = 1): CsiBatch {
  return {
    node_id,
    boot_id,
    device_time_us: 1,
    rssi_dbm: -43,
    channel: 4,
    amplitude: [1, 2, 3],
    mean_amplitude: 2,
    rms_amplitude: 2.2,
    variance: 0.5,
    source_frames: 10,
    ui_frames_coalesced: 9,
  };
}

describe("dashboard reducer", () => {
  it("snapshot populates nodes and selects the first online node", () => {
    const state = reducer(initialState(), {
      type: "SNAPSHOT",
      nodes: [node("02:00:00:00:00:02", "OFFLINE"), node("02:00:00:00:00:01", "PRESENT")],
    });
    expect(Object.keys(state.nodes)).toHaveLength(2);
    expect(state.selectedNodeId).toBe("02:00:00:00:00:01");
  });

  it("node_status updates only the matching node", () => {
    let state = reducer(initialState(), {
      type: "SNAPSHOT",
      nodes: [node("a", "ABSENT"), node("b", "ABSENT")],
    });
    state = reducer(state, { type: "NODE_STATUS", node: node("a", "PRESENT") });
    expect(state.nodes["a"].presence).toBe("PRESENT");
    expect(state.nodes["b"].presence).toBe("ABSENT");
  });

  it("csi_batch is isolated per node and bounded to HISTORY_LIMIT", () => {
    let state = reducer(initialState(), {
      type: "SNAPSHOT",
      nodes: [node("a", "PRESENT"), node("b", "PRESENT")],
    });
    for (let i = 0; i < HISTORY_LIMIT + 5; i += 1) {
      state = reducer(state, { type: "CSI_BATCH", batch: batch("a") });
    }
    state = reducer(state, { type: "CSI_BATCH", batch: batch("b") });
    expect(state.streams["a"].history).toHaveLength(HISTORY_LIMIT);
    expect(state.streams["b"].history).toHaveLength(1);
  });

  it("csi_batch clears history when boot_id changes", () => {
    let state = reducer(initialState(), {
      type: "SNAPSHOT",
      nodes: [node("a", "PRESENT", 1)],
    });
    state = reducer(state, { type: "CSI_BATCH", batch: batch("a", 1) });
    state = reducer(state, { type: "CSI_BATCH", batch: batch("a", 2) });
    expect(state.streams["a"].history).toHaveLength(1);
    expect(state.streams["a"].bootId).toBe(2);
  });
});
