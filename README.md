# wificsi

基于 ESP32-S3 + Wi-Fi CSI（Channel State Information）的多节点人体存在感知系统。ESP32-S3 节点采集 CSI 数据并通过 UDP 上报，本地感知服务器接收、解码并实时派生，网页看板在同一局域网内展示多节点的实时 CSI 曲线与官方 ACTIVE/INACTIVE 存在感知状态。

## 系统组成

```text
ESP32-S3 节点（最多 4 个）
   │  WCSI v1 UDP 遥测  →  服务器 UDP :5500
   │
感知服务器（Python 单进程，10.204.75.168）
   ├─ Stage 2 IngestProtocol / NodeRegistry   严格解码 + 节点生命周期
   ├─ Stage 3 NodeStreamHub                    10 Hz 有界派生（最新值合并）
   ├─ FastAPI REST :8000                       初始快照
   └─ WebSocket /api/v1/ws                     实时推送（约 10 Hz）
   │
网页看板（React + ECharts，同一进程托管）
   └─ 多节点总览 + 节点详情（CSI 幅值 / 信号趋势 / 健康指标）
```

## 目录结构

```text
firmware/node/       ESP32-S3 节点固件（ESP-IDF 5.4.4，WCSI v1）
server/wificsi/      感知服务器（UDP ingest + FastAPI + WebSocket）
  web/               Stage 3 看板后端（models/transform/stream/runtime/api）
web/                 网页前端（React + TypeScript + Vite + ECharts）
protocol/            WCSI v1 协议向量
tests/               后端 pytest + 前端 vitest 测试
tools/               协议向量生成与验收工具
docs/                部署指导 / 接口文档 / 二次开发指导
```

## 快速开始

### 1. 烧录节点固件

见 [部署指导](docs/部署指导.md) 第 3 节。将 `firmware/node` 烧录到每块 ESP32-S3，记录各自 STA MAC。

### 2. 启动感知服务器

```powershell
D:\Develop\uv\uv.exe sync --all-groups
D:\Develop\uv\uv.exe run wificsi-dashboard `
  --udp-host 10.204.75.168 --udp-port 5500 `
  --http-host 10.204.75.168 --http-port 8000
```

### 3. 打开网页

浏览器访问 `http://10.204.75.168:8000`，页面自动列出已发现的节点并实时显示 CSI 与存在状态。

### 无硬件时用模拟器验证

```powershell
# 分别用不同 MAC 启动模拟节点
D:\Develop\uv\uv.exe run wificsi-simulator --host 10.204.75.168 --port 5500 --node-id 02:00:00:00:00:01 --rate 50 --duration 60
```

## 文档

- [部署指导](docs/部署指导.md) — 环境、烧录、启动、网络与防火墙、验收
- [接口文档](docs/接口文档.md) — REST API 与 WebSocket JSON v1 契约
- [二次开发指导](docs/二次开发指导.md) — 代码架构、扩展点、测试与约定

## 存在状态语义

| 节点生命周期 | Web `presence` | 中文 | 含义 |
| --- | --- | --- | --- |
| `ACTIVE` | `PRESENT` | 有人 | 官方活动检测 ACTIVE（活动代理结果） |
| `INACTIVE` | `ABSENT` | 无人 | 官方活动检测 INACTIVE |
| `INITIALIZING` | `UNKNOWN` | 初始化中 | 在线但尚无稳定状态 |
| `OFFLINE` | `OFFLINE` | 离线 | 超过 5 秒无合法数据报 |

> ACTIVE/INACTIVE 是活动代理结果，不等同于经过准确率验证的静态人体存在检测。
