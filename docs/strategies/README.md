# 交易策略文档中心

本文档中心用于管理所有交易策略的完整描述、版本迭代和归档。

## 目录结构

```
docs/strategies/
├── README.md              # 本文档 — 总览与使用说明
├── template.md            # 策略文档模板（新建策略时使用）
├── current/               # 当前生效的策略文档（最新版本）
│   ├── 00_meta.md         # 版本元信息、变更记录、迭代计划
│   ├── 01_risk_policy.md  # 全局风控策略文档
│   └── 02_*.md            # 各策略文档（按编号排序）
└── archive/               # 历史版本归档
    └── v{VERSION}_{DATE}/
        ├── 00_meta.md
        ├── 01_risk_policy.md
        └── 02_*.md
```

## 版本管理规范

### 版本号格式

`v主版本.次版本.修订版本` — 例如 `v1.2.3`

| 版本位 | 升级条件 |
|--------|---------|
| 主版本 | 策略核心逻辑发生重大变化（如更换趋势判断方法、入场条件重构） |
| 次版本 | 策略参数调优、新增辅助条件、风控规则调整 |
| 修订版本 | 文档修正、代码 Bug 修复、日志优化（不影响策略逻辑） |

### 迭代流程

**每次策略更新时，按以下步骤执行：**

1. **确定新版本号**
   - 根据变更范围确定是主/次/修订版本升级
   - 记录变更日期

2. **备份旧版本**
   ```bash
   # 将当前版本复制到归档目录
   cp -r docs/strategies/current/* docs/strategies/archive/v{旧版本}_{旧日期}/
   ```

3. **更新当前版本**
   - 在 `current/` 目录下修改相应策略文档
   - 更新 `00_meta.md` 中的版本信息、变更记录、回测摘要

4. **归档记录**
   - 在 `00_meta.md` 的「版本历史」中新增一行
   - 注明变更内容、变更人、变更日期

### 文档命名规则

- `00_meta.md` — 版本元信息（固定）
- `01_risk_policy.md` — 风控策略文档（固定）
- `02_{strategy_name}.md` — 策略文档，按创建顺序编号
- 新增策略时取下一个可用编号

## 当前版本概览

| 文档 | 说明 | 最后更新 |
|------|------|---------|
| [00_meta.md](current/00_meta.md) | 版本元信息 v1.7.0 | 2026-09-15 |
| [01_risk_policy.md](current/01_risk_policy.md) | 全局风控策略 v1.1.0 | 2026-09-15 |
| [02_support_resistance.md](current/02_support_resistance.md) | 支撑/阻力位策略（BTC/ETH/SOL/PAXG/BNB/UNI）v1.7.0 | 2026-09-15 |
| [03_eth_5m.md](current/03_eth_5m.md) | ETH/USDT 1h 策略 | 2026-07-01 |
| [04_paxg_ema.md](current/04_paxg_ema.md) | ⚠️ PAXG/USDT EMA 策略（已废弃） | 2026-09-13 |
| [05_manual_query.md](current/05_manual_query.md) | 手动交易辅助 S/R 查询 | 2026-07-08 |
| [06_paxg_sr.md](current/06_paxg_sr.md) | PAXG/USDT S/R 策略 +9.99%（5x 风险平价） | 2026-09-15 |
| [07_uni_sr.md](current/07_uni_sr.md) | UNI/USDT 5m S/R 策略 +12.41% | 2026-09-15 |

## 快速参考

- **当前版本号**：v1.7.0（详见 `current/00_meta.md`）
- **最新回测结果**：见 `current/00_meta.md` 中的「回测摘要」
- **待办迭代计划**：见 `current/00_meta.md` 中的「迭代计划」
