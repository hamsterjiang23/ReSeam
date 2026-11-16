# 防御塔 PBR 贴图处理与单排全方法对照

[查看1×21高清对照图](防御塔PBR全方法对照_1x21.png)，共21列，包含原始材质输入和20个实验版本，其中18个版本完成贴图烘焙，2个版本保留实际失败阶段。每列展示材质渲染及BaseColor、Normal、Roughness、ORM四张图，最终共享版位于最右侧。`[verified: manifest.json layout/columns/display_channels；channels.csv；原实验report/results.json]`

[下载最终共享版模型与PBR贴图包](最终共享版_PBR模型与贴图.zip)，包含可直接打开的GLB、六类独立贴图、完整12级MIP和表面占用掩码，共75个归档条目。包内模型嵌入的三张图与外置BaseColor、Normal、ORM逐字节一致。`[verified: verification.json bundle_members/embedded_glb_images_match_separate_maps/bundle_crc_passed]`

## 这次贴图怎样得到

1. **读取原材质。** 输入为8192² BaseColor和2048² Normal。颜色以sRGB读取，Normal按数据读取。粗糙度固定0.6、金属度固定0，AO使用默认值1。`[verified: S1 pbr_textures/pbr_factors/normal_convention；S2 source_material_scope.source_materials；S4 channels；所有输出coverage内ORM像素核对]`
2. **建立镜像来源。** 镜像路线保留X小于0的一侧，按来源三角形和重心坐标记录新表面对应，同时反射法线、切线并修正切线方向符号。`[verified: S2 plane_axis/plane_offset/keep_positive/source_uv_interpolation；src/reseam/agent_tools/mirror_pbr_reference.py L92–L108]`
3. **按新UV重采样。** 本塔最终镜像路线采用同表面面角对应，逐面查询源材质。颜色按来源位置采样，法线先还原为表面空间方向，再转换到目标的新切线坐标系。共享成员先分别采样，再在一致的目标方向下合成。`[verified: S4 projection/composition/source_normal_map_included；S5 PBRTarget.sample L183–L217、compose L460–L541]`
4. **写出2K贴图。** 输出BaseColor、Normal及ORM，ORM的R、G、B分别保存AO、Roughness、Metallic。本目录另外将三个标量通道逐像素拆成灰度图，便于逐张查看与导入。`[verified: S4 channels；channels.csv operation；各ORM与拆分图像逐像素核对]`
5. **生成MIP并看图。** 原实验保存12级MIP，Normal在每级重归一化。最终候选已由Agent在记录的视角看图保留，数值身份为DIAGNOSTIC，人工与游戏Shader状态为NOT_RUN。`[verified: S4 mip_policy；S3 mip_count/numerical_status；S6 decision/human_acceptance/game_shader]`

图中的灰度和橙色区域反映本轮固定材质参数。有效UV范围内，AO灰度为255，粗糙度灰度为153，金属度灰度为0，对应线性值1、0.6、0。图集空白区保留烘焙器默认值，`surface_coverage.png`提供实际表面占用掩码。`[verified: manifest.json constant_channel_check/map_scope；maps各版本surface_coverage.png与ORM.png像素读取]`

## 贴图目录

|目录|内容|记录|
|---|---|---|
|`maps/source`|原始BaseColor与Normal的逐字节副本|`[verified: channels.csv method=source / operation]`|
|`maps/<方法名称>`|18个成功版本各自的BaseColor、Normal、Roughness、Metallic、AO、ORM及表面占用图|`[verified: manifest.json maps；channels.csv mip=0]`|
|`maps/mirror_negative_exhaustive_minimum_shared`|最终48岛／24图块共享版|`[verified: S3 method=mirror_negative_exhaustive_minimum_shared]`|
|`maps/mirror_negative_exhaustive_minimum_unshared`|相同镜像几何的48岛／48图块无共享对照|`[verified: S3 method=mirror_negative_exhaustive_minimum_unshared]`|
|上述两个最终对照的`mips`目录|mip1至mip11，顶层文件为mip0|`[verified: manifest.json mip_scope；channels.csv mip]`|
|`channels.csv`|每张贴图的来源、尺寸、SHA-256与复制／拆分操作|`[verified: channels.csv列定义]`|

BaseColor使用sRGB，Normal、AO、Roughness、Metallic和ORM使用线性数据解释。Normal约定沿用原实验声明的OpenGL。`[verified: S4 channels；S1 normal_convention；scripts/agent_pbr_binding.py颜色空间绑定]`

## 来源索引

以下路径相对于仓库根目录，文件哈希记录在`manifest.json`。`[verified: manifest.json source_ids/source_sha256]`

- **S1** — `artifacts/tower_uv_comparison/round02/clean_import/import_request.json`
- **S2** — `artifacts/tower_uv_comparison/round02/mirror/negative_exhaustive_minimum_clean/mirror_variant.json`
- **S3** — `artifacts/tower_uv_comparison/round02/report/results.json`
- **S4** — `artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_shared_2048/bake/texture/material_metadata.json`
- **S5** — `src/reseam/agent_tools/pbr_reuse.py`
- **S6** — `artifacts/tower_uv_comparison/round02/report/appearance_review.json`

完整原实验的还原方法见[防御塔实验交付包](../../tower-uv-comparison/README.md)，图表与通道整理入口为`scripts/build_tower_pbr_process.py`。`[verified: ../../tower-uv-comparison/archive_manifest.json；scripts/build_tower_pbr_process.py main]`
