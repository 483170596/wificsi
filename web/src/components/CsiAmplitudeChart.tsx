import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export function CsiAmplitudeChart({ amplitude }: { amplitude: number[] | null }) {
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
    if (!amplitude || amplitude.length === 0) {
      chartRef.current.clear();
      return;
    }
    const data = amplitude.map((value, index) => [index, value]);
    chartRef.current.setOption({
      animation: false,
      grid: { left: 48, right: 16, top: 16, bottom: 36 },
      xAxis: { type: "value", name: "采样对索引", nameLocation: "middle", nameGap: 24 },
      yAxis: { type: "value", name: "幅值" },
      series: [
        {
          type: "line",
          data,
          showSymbol: false,
          lineStyle: { width: 1 },
          areaStyle: { opacity: 0.15 },
        },
      ],
    });
  }, [amplitude]);

  const empty = !amplitude || amplitude.length === 0;

  return (
    <div className="chart-panel">
      <h3>最新 CSI 幅值曲线</h3>
      <div className="chart-wrap">
        <div ref={containerRef} className="chart" />
        {empty && <div className="chart-empty">暂无 CSI 数据</div>}
      </div>
    </div>
  );
}
