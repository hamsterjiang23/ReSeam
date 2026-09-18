# dragon / v1_round03

源码 `4714e78e23b50b73883182e622681d0cbfcd0434`；每次重新初始化，总预算 180 秒；种子 [17, 29, 43]。

本页只汇总本轮原始证据。SUCCESS 表示该配置通过自动门禁，不表示已达到 artist 交付标准。

## 几何配置

| 方法 | 有效/计划 | 接缝长度中位数 | 岛数中位数 | 平均SD中位数 | 全部运行耗时中位数 |
|---|---|---|---|---|---|
| optcuts | 3/3 | 12.82760 | 9 | 0.02477 | 27.06 s |
| xatlas | 0/3 | — | — | — | 0.78 s |
| blender_smart | 0/3 | — | — | — | 2.47 s |
| partuv | 0/3 | — | — | — | 64.67 s |
| optcuts_restart | 3/3 | 11.13313 | 7 | 0.02493 | 90.48 s |
| reseam_fixed | 3/3 | 12.82760 | 9 | 0.02477 | 32.31 s |
| reseam_small | 3/3 | 12.82760 | 9 | 0.02477 | 173.08 s |
| reseam_large | 3/3 | 12.82760 | 9 | 0.02477 | 172.66 s |
| reseam_adaptive | 3/3 | 12.82760 | 9 | 0.02477 | 172.33 s |
| reseam_low_fragments | 3/3 | 12.82760 | 9 | 0.02477 | 172.64 s |
| reseam_artist | 3/3 | 12.82760 | 9 | 0.02477 | 172.67 s |

只对有效输出汇总质量，失败率单独保留；不能只按幸存样本的中位数宣布方法获胜。
低碎片与 artist 配置改变了目标，不能将目标变化归因于搜索机制；所有指标原值在 result.json。

## 预定下游组合

| 方法 | 状态 | UV覆盖率 | Blender MAE | 优化后 MAE | 原因 |
|---|---|---|---|---|---|
| optcuts | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| xatlas | NOT_RUN_INVALID_UV | — | — | — | UV_INVALID |
| blender_smart | NOT_RUN_INVALID_UV | — | — | — | UV_INVALID |
| partuv | NOT_RUN_INVALID_UV | — | — | — | UV_INVALID |
| optcuts_restart | SUCCESS | 34.39% | 0.03588 | 0.03576 |  |
| reseam_fixed | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| reseam_small | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| reseam_large | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| reseam_adaptive | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| reseam_low_fragments | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |
| reseam_artist | SUCCESS | 28.09% | 0.03593 | 0.03581 |  |

下游仅预定种子17；diagnostic_only 输出不计作通过 UV 质量门禁。
法线视图、mip、接缝带及投射探针原始指标收录于 delivery.json；四个面内探针不等于逐 texel 投射验证。

## 支持域审计

共同候选 8/8；小范围失败 8；大范围救回且改善 0。
缺失候选 0；审计耗时 299.28 秒。完整搜索轨迹会分叉，不能代替共同起点诊断。

## 人工验收边界

待真人确认语义区域、完成匿名限时修整和重烘焙检查。未经确认的建议只改变软权重。
两套固定模型是反复使用的开发与回归集，不代表未见模型泛化。
