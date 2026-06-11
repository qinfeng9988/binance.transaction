# Volume Profile VAH/VAL 增强设计（Phase 1）

## 背景

当前 Volume Profile 已实现 POC/HVN 分层评分，效果良好（POC 近区 +2.0）。但经典 Volume Profile 理论中还有两个关键价位未利用：
- **VAH** (Value Area High)：价值区上沿
- **VAL** (Value Area Low)：价值区下沿

引入 VAH/VAL 可以提供更宽的价格支撑/压力区域，帮助策略识别"价格在价值区内/外"的市场结构。

## 设计决策（最终版）

### 1. VAH/VAL 不加入聚类，只作评分加成
- **理由**：降低回归风险，保持现有聚类逻辑不变
- VAH/VAL 作为"区域边界"性质与传统 swing high/low 不同

### 2. B+D 组合评分矩阵（最终锁定）

**B 方案：缩小 POC 覆盖范围**
```
层级 1: POC 核心区域
  ├─±0.5% → +2.0  (POC_near，保持不变)
  └─±0.7% → +1.0  (POC_far，从 ±1.0% 缩小)

层级 2: VAH/VAL 边界（温和加分）
  ├─价格接近 VAH/VAL ±0.5% → +0.5（从 +1.0 降低）
  └─价格落在 VAL~VAH 区间内 → +0.3（保持不变）

层级 3: HVN 高量节点（现有）
  ├─±0.5% → +1.5
  └─±1.0% → +0.5

层级 4: 普通高量兜底（现有）
  └─top10 ±1% → +0.5
```

**D 方案：Value Area 内部微弱认可**
- 所有在 VAL~VAH 区间内的 S/R 位，若未被 POC/HVN 覆盖，获得 +0.3 基础加分

### 3. 分币种锁定方案

| 币种 | 周期 | use_vah_val | 理由 |
|------|------|-------------|------|
| **BTC** | 5m | **true** | 差距最小（-0.7%），PF 接近基准（3.78 vs 3.91），保留结构增强 |
| **ETH** | 1h | **false** | 几乎无提升，保持基准最稳 |
| **SOL** | 5m | **false** | 轻微负贡献，SOL 已通过 FR 优化，避免额外噪音 |
| PAXG | - | false | 默认关闭 |

### 4. 关键参数（全部参数化）

```yaml
use_vah_val: false              # 是否启用 VAH/VAL
value_area_pct: 0.70            # Value Area 累计成交量百分比
poc_near_threshold_pct: 0.005   # POC 近区 ±0.5%
poc_far_threshold_pct: 0.007    # POC 远区 ±0.7%（B 方案缩小）
vah_val_near_threshold_pct: 0.005 # VAH/VAL 近区 ±0.5%
vah_val_near_bonus: 0.5         # VAH/VAL 边界加分（温和 +0.5）
vah_val_inner_bonus: 0.3        # VA 内部加分（+0.3）
```

## 回测验证结果

### 三版本对比（YTD 2026-01-01 ~ 2026-06-09）

| 币种 | 方案 | 收益率 | 交易 | 胜率 | PF | MDD |
|------|------|--------|------|------|-----|-----|
| **BTC 5m** | 基准 (false) | **+46.60%** | 57 | 38.6% | **3.91** | 10.91% |
| | 激进版 (+1.0, POC 1.0%) | +42.68% | 62 | 38.7% | 3.39 | 12.41% |
| | **B+D (+0.5/0.3, POC 0.7%)** | **+45.90%** | 59 | 39.0% | **3.78** | 10.91% |
| **ETH 1h** | 基准 (false) | **+6.57%** | 61 | 65.6% | **1.30** | 11.27% |
| | B+D | +6.58% | 61 | 65.6% | 1.30 | 11.27% |
| **SOL 5m** | 基准 (false) | **+8.11%** | 505 | 59.4% | **1.33** | 3.50% |
| | B+D | +7.84% | 518 | 59.7% | 1.32 | 3.39% |

### 关键发现

1. **B+D 大幅优于激进版**：BTC 收益率从 +42.68% 修复到 +45.90%，PF 从 3.39 提升到 3.78
2. **POC 范围过宽是核心问题**：原始 ±1.0% 几乎覆盖了整个 Value Area（典型宽度 0.5%~1.5%），导致 VAH/VAL 无法独立发挥
3. **温和加分更安全**：+0.5 边界加分避免了激进版 +1.0 引入的低质量信号
4. **distance 过滤仍是主导**：即使 quality_score 提高，大多数新增信号仍被 distance 过滤拦截

## 算法：Value Area 计算

从 POC 所在 bin 开始，向上下两侧逐层扩展，每次选择成交量更大的一侧，直到累计成交量达到总量 × value_area_pct。

```
1. 按价格排序 bin（低到高）
2. 找到 POC 索引（成交量最大）
3. 初始化 included = {poc_idx}, cum_vol = poc_volume
4. While cum_vol < target:
     lower = min(included) - 1
     upper = max(included) + 1
     选择 volume[lower] 和 volume[upper] 中较大的有效侧加入
5. VAH = max(prices_included), VAL = min(prices_included)
```

## 集成点

### 修改文件
1. `quant_trader/technical/support_resistance.py`
   - `compute_volume_profile()`：新增 VAH/VAL 计算
   - `_enrich_levels()`：B+D 评分矩阵（POC 范围参数化 + 温和 VAH/VAL 叠加）
   - `identify_support_resistance()`：透传全部 6 个新参数

2. `quant_trader/strategies/s_r_levels.py`
   - `on_bar()`：读取 6 个新参数，透传给全部 5 处 `identify_support_resistance()` 调用

3. `quant_trader/config/strategies.yaml`
   - BTC: `use_vah_val: true`, `poc_far_threshold_pct: 0.007`
   - ETH/SOL: `use_vah_val: false`

## 后续迭代方向

| 优先级 | 内容 | 条件 |
|--------|------|------|
| P1 | Phase 1.5：VAH/VAL 作为独立 S/R 加入聚类 | 当前 B+D 效果不满意时 |
| P2 | EMA 平滑 VAH/VAL | 观察到明显抖动时 |
| P3 | POC 区间量比（Phase 2） | Funding Rate 过滤稳定后 |
