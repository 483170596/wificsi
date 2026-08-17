# WiFi CSI 感知资料整理（从采集到存在感知全流程）

> 广泛汇总 Espressif 官方文档、学术资源与开源项目的知识支撑
> 编写日期：2026-08-14

---

## 一、CSI 技术基础

### 1.1 什么是 CSI

**信道状态信息（Channel State Information, CSI）** 描述无线信道的细粒度特性，包括信号**幅度、相位、频率响应**，在频域上表示为一个复数向量。

- 通过 CSI 的变化可推断引起信道变化的物理环境变化，实现**非接触式智能传感**
- CSI 对环境极敏感：既能感知人/动物**行走、奔跑等大动作**，也能捕捉**呼吸、咀嚼等细微动作**
- 来源：OFDM 将频谱划分为多个正交子载波，每个子载波独立传输；OFDM-MIMO 下多天线间的信号差异共同揭示信道状态

### 1.2 CSI vs RSSI（关键区别）

| 维度 | CSI | RSSI |
|------|-----|------|
| 信息类型 | 详细信道特性（幅度+相位，多子载波） | 标量信号强度（dBm） |
| 精确性 | 高 | 低 |
| 计算复杂度 | 高 | 低 |
| 应用 | 室内定位、手势/活动识别、呼吸检测 | 简单信号质量评估、粗略测距 |

**结论**：存在感知必须用 CSI；RSSI 只能做极粗糙的靠近检测。CSI 可作为多子载波"指纹"和频率选择性衰减模型的输入，实现比 RSSI 更高精度的定位与动作区分。

### 1.3 物理原理（为什么能感知人）

1. 人体（含水组织）会**散射/扰动**收发端之间的多径传播
2. 这种扰动产生 CSI 幅度/相位的时变模式：
   - 呼吸 ≈ 0.1–0.5 Hz 周期变化
   - 心跳 ≈ 0.8–2.0 Hz
   - 行走/跌倒 = 宽带功率突增 + 多普勒频移
3. **Fresnel 区理论**解释穿墙感知的距离上限（约 5 米量级）

---

## 二、CSI 采集

### 2.1 ESP32 系列 CSI 支持情况

**所有 ESP32 系列均支持 CSI**：ESP32 / S2 / C3 / **S3** / C5 / C6 / C61（官方 README 原文）。

- ESP32-S3 是主流选择（资源充足、有外置天线口）
- 需要 **2.4GHz** Wi-Fi（CSI 依赖 802.11n 帧）
- 官方明确：**外置 IPEX 天线效果优于 PCB 天线**，PCB 天线有方向性

### 2.2 三种 CSI 获取方式（官方 esp-csi README）

| 方式 | 实现 | 优点 | 缺点 | 适用 |
|------|------|------|------|------|
| **获取路由器 CSI** | ESP32 向路由器 Ping，收 Ping 回应中的 CSI | 只需 1 ESP32 + 路由器 | 依赖路由器位置/协议 | 单设备+有路由器 |
| **设备间 CSI** | ESP32-A/B 都向路由器 Ping，A 收 B 的 CSI | 不受路由器位置影响 | 依赖路由器协议 | ≥2 个 ESP32 |
| **特定设备 CSI** | 专用发送设备不断切换信道广播，多 ESP32 接收 | 精度最高、干扰小 | 需额外专用发送设备 | 高精度/多设备集群定位 |

**对 3 块 ESP32-S3 的启示**：方案 2（设备间）最契合——2 块组成 TX/RX，第 3 块扩展。

### 2.3 采集工具与例程

| 工具 | 说明 | 关键点 |
|------|------|--------|
| esp-csi `get-started` | 官方入门：csi_recv（收）/ csi_send（发）/ csi_recv_router（路由器触发）/ tools(csi_data_read_parse.py) | 首选起点 |
| ESP32-CSI-Tool | 学术级采集（WOWMOM 论文）：active_sta / active_ap / passive | CSV 输出、SD 卡、时间同步 |

### 2.4 采集关键配置（ESP32-CSI-Tool 经验总结）

```
Component config > Wi-Fi > WiFi CSI(Channel State Information)   → 使能 CSI
Component config > FreeRTOS > Tick rate (Hz) > 1000              → 提高回调精度
Serial flasher config > baud rate > 921600                       → 高速率防丢帧（越高越好）
```

### 2.5 CSI 数据格式

- 典型输出（串口 CSV）：`CSI_DATA,<len>,<mac>,<rssi>,<noise>,<channel>,<...>,"[I/Q 复数数组]"`
- ESP32-S3 在 802.11n **HT40** 下：192 个复数值，其中 **162 个有效数据子载波**（去掉导频/保护）
- 采样率：约 20–60 Hz（取决于配置与波特率）

---

## 三、信号处理（DSP 管线）

### 3.1 标准管线（wifi-csi-presence-detection 方法）

```
原始 I/Q 复数
  → 幅度提取 |H| = sqrt(I²+Q²)（存在感知通常只用幅度）
  → 子载波过滤（去导频/保护子载波）
  → 异常值去除（Hampel 滤波：中位数 ± k·MAD）
  → 去噪（Butterworth 带通 / Daubechies 小波）
  → 滑动窗口特征提取（2s 非重叠窗）
```

### 3.2 特征工程（存在感知核心）

| 特征 | 含义 | 用途 |
|------|------|------|
| **variance（方差）** | 子载波幅度随时间波动 | 有人时方差增大 |
| **MAD** | 中位数绝对偏差 | 稳健版方差，抗异常值 |
| **range / IQR** | 幅值范围/四分位距 | 捕捉运动幅度 |
| **jitter（抖动）** | 信号短期抖动 | **官方 esp_wifi_sensing 主特征** |

- wifi-csi-presence：162 子载波 × 4 特征 = 648 维特征向量 → RF/SVM/XGBoost/MLP
- 官方 esp_wifi_sensing：用 `jitter_value` → 平滑缩放 → 与进入/退出阈值比较 → ACTIVE/INACTIVE 状态机

### 3.3 频域/多普勒分析（进阶）

- FFT 谱：分离呼吸（0.1-0.5Hz）与运动（宽带）
- 多普勒谱：区分走近/走远（多普勒频移符号）
- DAPD（动态幅度概率密度）：DapFall 提出，降低静态分量、突出跌倒突发

---

## 四、存在感知算法

### 4.1 官方方案：esp_wifi_sensing 组件（推荐）

来自 `esp-csi/examples/esp-radar/wifi_sensing_demo`，**片上运行、无需训练**：

- API 流程：`esp_wifi_sensing_fsm_create()` → 添加 AP + 2 peer 信道 → 注册 ACTIVE/INACTIVE 回调 → `fsm_control(START)`
- 核心诊断参数：`jitter_value`（抖动）、`smooth_scaled`（平滑值）、`enter_level_scaled`/`exit_level_scaled`（进出阈值）、`state`、`init_stage`
- 现场标定：`RESET_BASELINE`；调参：`motion_sensitivity`、`active_jitter_min`、`active_filter_ms`
- 输出：二值状态（ACTIVE=有人活动 / INACTIVE=无人）

### 4.2 阈值法（WaveSight 模式）

- jitter/RSSI 抖动超过阈值 → 判定"运动"
- "Someone Timeout"：最后一次运动后 N 秒仍无 → 判定空房间
- **自动标定**：空房间采样 60s，学习背景阈值

### 4.3 机器学习法

- **EvilRoom**：XGBoost + PCA + SMOTE，4 区域分类 80.5%（有混淆矩阵、视频 PoC）
- **wifi-csi-presence**：二分类（空/占），目标 F1≥0.90
- **Wriple**：Conv-LSTM 深度学习，15s 校准 + 噪声门限（<50）+ 每 2s 一次判定

---

## 五、大动作识别与跌倒检测

### 5.1 学术方法

| 方向 | 代表工作 | 要点 |
|------|---------|------|
| 跌倒检测 | DapFall（IEEE Sensors 2025） | DAPD 特征，跨环境跌倒检测，数据在 Google Drive |
| 活动识别综述 | WiFi Sensing with CSI: A Survey | 特征+分类框架总览 |
| 行为识别 | 双向 LSTM（注意力） | 时序建模主流路线 |
| 资源大全 | Awesome-WiFi-CSI-Research | 论文/代码/数据集索引 |

### 5.2 大动作检测的工程思路

1. **走过**：jitter/方差突增（宽带功率）+ 多普勒，可用阈值法直接判
2. **跌倒 vs 走过**：跌倒特征为"突然的、剧烈的、短时的幅度冲击 + 后续静止"；走过为"持续的运动"
3. **建议路线**：统计特征（方差/峰值/持续时间）→ XGBoost 起步 → LSTM 提升时序判别

---

## 六、部署与集成

### 6.1 传感器放置指南（ESPectre 实测经验）

- **距离路由器 3-8 米最优**（<2m 信号过强敏感性低，>10-15m 太弱噪声大）
- **高度 1-1.5 米**（桌面高度）
- **外置 IPEX 天线**优于 PCB 天线
- 避免金属障碍物（冰箱/金属柜）、避免角落

### 6.2 集成方案

| 方案 | 技术 | 门槛 |
|------|------|------|
| 智能家居 | ESPectre（ESPHome + Home Assistant） | 10-15 分钟、YAML、无编程 |
| 自建 Dashboard | WaveSight / Wriple | ESP-IDF 烧录 + Web UI |
| 轻量输出 | GPIO/LED + 串口 JSON | 最低 |

---

## 七、参考资料清单（权威来源）

### 官方（首要依据）
1. esp-csi 仓库：https://github.com/espressif/esp-csi（Apache-2.0，1498★，2021 至今活跃）
2. ESP-CSI 方案介绍（ESP-Techpedia）：https://docs.espressif.com/projects/esp-techpedia/zh_CN/latest/esp-friends/solution-introduction/esp-csi/
3. ESP-IDF WiFi CSI 指南：https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/wifi.html#wi-fi-channel-state-information
4. esp-csi 官方文档（docs/zh_CN）：信号处理基础、OFDM 介绍、无线信道基础、CSI 与 RSSI、测距定位、应用案例

### 学术
5. WiFi Sensing with CSI: A Survey
6. DapFall: Dynamic Amplitude Probability Density Profile（IEEE Sensors Journal 2025, DOI:10.1109/JSEN.2025.3544552）
7. Hernandez & Bulut (2022) WiFi Sensing on the Edge, IEEE COMST
8. Awesome-WiFi-CSI-Research / Awesome-WiFi-CSI-Sensing（资源索引）

### 开源项目（详见"可复用项目评估报告"）
9. ESPectre、WaveSight、EvilRoom、Wriple、wifi-csi-presence-detection、ESP32-CSI-Tool、ruview-lite、DapFall 等

---

*本资料整理覆盖"采集 → 预处理 → 特征 → 存在感知 → 大动作/跌倒 → 部署"全流程，每环节均给出官方/权威来源。*
