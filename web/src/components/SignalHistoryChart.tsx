import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { SignalPoint } from "../state/dashboard";

export function SignalHistoryChart({ history }: { history: SignalPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current = echarts.init(containerRef.current);
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    if (history.length === 0) {
      chartRef.current.clear();
      return;
    }
    const times = history.map((point) => point.t);
    chartRef.current.setOption({
      animation: false,
      grid: { left: 56, right: 16, top: 32, bottom: 36 },
      legend: { data: ["RMS 幅值", "方差", "RSSI"], top: 0 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "time" },
      yAxis: [
        { type: "value", name: "RMS / 方差", position: "left" },
        { type: "value", name: "RSSI (dBm)", position: "right" },
      ],
      series: [
        {
          name: "RMS 幅值",
          type: "line",
          showSymbol: false,
          lineStyle: { width: 1 },
          data: history.map((point) => [point.t, point.rms]),
        },
        {
          name: "方差",
          type: "line",
          showSymbol: false,
          lineStyle: { width: 1 },
          data: history.map((point) => [point.t, point.variance]),
        },
        {
          name: "RSSI",
          type: "line",
          yAxisIndex: 1,
          showSymbol: false,
          lineStyle: { width: 1 },
          data: history.map((point) => [point.t, point.rssi]),
        },
      ],
    });
    // times is intentionally kept for a future axis range bound.
    void times;
  }, [history]);

  return (
    <div className="chart-panel">
      <h3>最近 60 秒信号趋势</h3>
      <div className="chart-wrap">
        <div ref={containerRef} className="chart" />
        {history.length === 0 && <div className="chart-empty">暂无趋势数据</div>}
      </div>
    </div>
  );
}
