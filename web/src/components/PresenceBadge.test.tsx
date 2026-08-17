import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PresenceBadge } from "./PresenceBadge";

describe("PresenceBadge", () => {
  it("renders PRESENT as 有人", () => {
    render(<PresenceBadge presence="PRESENT" />);
    expect(screen.getByText("有人")).toBeInTheDocument();
  });

  it("renders INITIALIZING as 初始化中 (not 无人)", () => {
    render(<PresenceBadge presence="UNKNOWN" />);
    expect(screen.getByText("初始化中")).toBeInTheDocument();
  });

  it("renders OFFLINE as 离线 (not 无人)", () => {
    render(<PresenceBadge presence="OFFLINE" />);
    expect(screen.getByText("离线")).toBeInTheDocument();
  });
});
