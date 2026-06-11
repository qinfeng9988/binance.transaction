# Volume Profile VAH/VAL 增强任务列表

## 任务 1：计算层 — 新增 VAH/VAL 计算
- [x] 修改 `compute_volume_profile()` 增加 Value Area 计算逻辑
- [x] 返回新增字段：`vah`, `val`, `value_area_pct`
- [x] 保持向后兼容（现有调用方不受影响）

## 任务 2：评分层 — B+D 组合评分矩阵
- [x] POC 距离阈值参数化（`poc_near_threshold_pct`, `poc_far_threshold_pct`）
- [x] B 方案：POC far 从 ±1.0% 缩小到 ±0.7%
- [x] D 方案：VAH/VAL 边界加分 +0.5（温和），内部加分 +0.3
- [x] VAH/VAL 与 POC/HVN 叠加加分（不再受 vp_bonus < 1.0 限制）
- [x] 更新 `vp_detail` 字段（`VAH_near`, `VAL_near`, `VA_inner`）

## 任务 3：接口层 — 全部参数透传
- [x] `identify_support_resistance()` 新增 6 个参数：
  - `use_vah_val`, `value_area_pct`, `vah_val_near_threshold_pct`
  - `poc_near_threshold_pct`, `poc_far_threshold_pct`
  - `vah_val_near_bonus`, `vah_val_inner_bonus`
- [x] 当 `use_vah_val=False` 时零开销跳过 VAH/VAL 计算

## 任务 4：策略层 — 参数读取与传递
- [x] `s_r_levels.py` `on_bar()` 读取 6 个新参数
- [x] 调用 `identify_support_resistance()` 时透传参数（全部 5 处调用）

## 任务 5：配置层 — 分币种锁定方案
- [x] BTC: `use_vah_val: true`, `value_area_pct: 0.65`, `poc_far_threshold_pct: 0.007`
- [x] ETH: `use_vah_val: false`, `value_area_pct: 0.70`
- [x] SOL: `use_vah_val: false`, `value_area_pct: 0.72`
- [x] PAXG: `use_vah_val: false`, `value_area_pct: 0.70`

## 任务 6：回测验证 — 三版本对比
- [x] BTC 5m YTD：基准 +46.60% / 激进版 +42.68% / **B+D +45.90%**
- [x] ETH 1h YTD：基准 +6.57% / B+D +6.58%（无变化）
- [x] SOL 5m YTD：基准 +8.11% / B+D +7.84%（轻微负贡献）
- [x] 对比 FILTER SUMMARY：BTC +1 信号，ETH 0 变化，SOL +4 信号
- [x] **锁定方案：BTC 开启，ETH/SOL 关闭**
