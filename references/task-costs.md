# 本地CPU实验成本与读写范围

三模型本轮每条资产路径内部串行调用重工具，Blender／数值库限制单线程；两个历史资产在独立进程中并行，CPU忙时将本轮自有进程设为Idle优先级。启动时系统可用内存约20.8GiB；先保留256像素冒烟，再启动2048。`[verified: runner.py environment；本轮Get-Process PriorityClass=64及Win32_OperatingSystem FreePhysicalMemory=21828604KiB；opinion: 运行策略]`

|任务|成本|读|写|
|---|---|---|---|
|Chloe 450面静态颜色|round02镜像256：烘焙约2.2秒、验收约6.1秒；其它尺寸独立记录|原FBX、指定PNG、冻结源码|独立import_smoke和round目录|
|高低模颜色投射、2048烘焙|round02能量刀xatlas：烘焙约307.5秒、验收约120.3秒；龙OptCuts：245.9／354.9秒；2K请求显式上限900秒，普通工具300秒，整路径2400秒|固定高低模、来源映射、冻结目标|独立工具目录、真实纹理和见证|
|CPU几何/排布测试|小型先验；执行时保存实际耗时|构造案例、临时快照|pytest缓存/临时目录|
|三模型45项只读补测|Chloe单案例64点AO探针0.594秒；正式39条目53.064秒，1000点、8射线、单线程，复用6份几何AO场景|round02已冻结快照、UV与颜色验收；旧指标协议|metrics-round01保留；标签修正的最终结果为metrics-round02，不改原数据|

无工具写入原资产；正式结果仅比较相同代码哈希的调用。若修正影响验收，创建新轮次保留旧结果。`[opinion]`

以上时间来自 `artifacts/legacy_assets_reseam/round02/calls.json` 对应 `material.bake`／`quality.validate` 调用，属于CPU实测，不代表数值质量通过。ROI探针加速只在独立的1万查询基准中成立，不能套作整轮加速比。`[verified: round02 calls.json；projection_roi_benchmark01/benchmark.json]`

45项补测耗时单列，来源为`docs/assets/legacy-assets-reseam/metrics-round02/summary.json`的metric_seconds和unique_ao_geometries；这是逐案例测量时间之和，不含PDF排版和交付检查。`[verified: summary.json metric_seconds=53.06399999989662、unique_ao_geometries=6]`

## 镜像龙全身整理 round02

|任务|本轮已测成本|读|写|
|---|---|---|---|
|11338面局部展开与左右恢复|胸腹展开14.9秒、来源配对18.0秒、应用15.7秒；同一候选紧凑排布29.4秒。CPU忙时自有进程Idle导致部分调用约翻倍。|同一固定几何父快照、镜像创建来源、104份冻结源码|各候选独立工具目录与shape_trials.json|
|2048同身份BaseColor|基线烘焙50.5秒、LOD0/1/2验收181.9秒；大腿53.3／184.1秒。检查范围96像素探针，数值渲染拒绝保留。|共同冻结source_original目标、候选UV|appearance独立目录及独立calls.json|

几何批次与外观检查使用独立目录、独立调用账本，读取相同冻结输入；至多两个CPU工具并行。启动外观路径时空闲内存约20.7GiB、CPU55%，后续仍监控；不使用GPU。具体实测来自`artifacts/dragon_wholebody_refine/round02/`及`appearance/calls.json`。`[verified: 两份calls.json；本轮资源快照；opinion: 并行与监控规则]`

## 镜像龙四成员共享 round03

|任务|本轮已测成本|读|写|
|---|---|---|---|
|同一固定几何的四成员候选与三起点对照|19次公开调用合计478.283秒，包含3次真实四成员检查、3次应用和12次紧凑排布。|固定11338面父状态、镜像来源、107份冻结源码|round03各独立调用目录与measurements.json|
|共同目标下的两份2048真实颜色对照|15次调用合计623.435秒，包含目标／视图、两次烘焙、LOD0/1/2验证及诊断导出。|相同冻结目标、候选与40份对照UV|appearance独立账本、真实GLB与完整mip|

上述是调用墙钟时间之和，数值严格渲染均拒绝，Agent预览接受单列。阶段结束核对调用账本与实际顶层result.json一一对应，并独立重读两个导出；PDF排版与只读审核不计入几何调用账本。`[verified: round03/closure.json costs；appearance/summary.json；appearance/export_integrity.json]`
