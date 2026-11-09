# 高低模颜色来源与多UV静态资产

源材质网格与待优化网格面角不同，或FBX具有多套UV时读取本页。工具参数以 `tools describe` 为准，不把普通静态颜色绑定当成Unity材质复现。`[opinion]`

## 不同高低模

默认同面角目标仍拒绝不同几何。先用低模和明确的高模 `reference` 导入，调用 `material.target source_mode=projected_high`；冻结原高模哈希、原低模三角形、投射规则与距离门限。当前点先按低模原面重心坐标定位，再查询原高模实际面与重心坐标，不能用低模角点UV插值或一张低模烘焙图替代原高模信号。`[verified: color_reuse.py _source/freeze_target；projected_source.py ProjectedSource.sample]`

`projection_max_fraction` 默认0.02，归一化尺度是原低模包围盒对角线；不是目标贴图尺寸，也不是原高模尺寸。已存在来源映射时继承其门限，只有显式请求才覆盖。越界保留投射证据并拒绝，不自动放宽。`[verified: projected_source.py rule；mirror_asset.py projected分支；views.py projected_source分支]`

镜像时显式指定 `source_mode=projected_high`，或继承父快照的投射来源。新面角映射先追溯保留半侧、再追溯原低模和原高模；不把高模UV数组按新面索引直接读取。相同镜像几何的共享／解除共享使用同一冻结目标。`[verified: mirror_asset.py projected_lineage；ColorTarget projected_source]`

镜像变体的源颜色已经来自保留侧。该变体内颜色球零差或同目标重建通过，不能证明原父模型另一侧的颜色未改变；须另看原父模型与镜像来源预览，单列几何和有意外观修改。`[opinion]`

对称平面须结合真实颜色多视图判断。点云残差更小的平面也可能改变头冠、尖端或轮廓的排列；先保留原件，在独立候选中对比不同方向，外观不合适就撤回整个变体。片状能量刀可以检查正反厚度方向，不能把平面内不等长的尖刺误当成应该镜像的左右轮廓。面数因裁切重三角化增加须单列。`[opinion]`

烘焙、验收及来源视图输出 `source_map.json/npz`。所有查询进入数量、距离和逐面统计；精确见证只保存确定性执行顺序的前16384个，不能称全体查询均逐条归档。投射正确不等于高低模轮廓相同；颜色验收也不包含Normal/ORM。`[verified: projected_source.py save_audit；opinion: 解释边界]`

## FBX静态绑定与第二UV

`asset.import` 可显式传入 `basecolor_texture` 和 `uv_channel`（零基整数或精确层名），与 `reference` 互斥。该分支冻结真实图像并建立静态颜色参考，不加载FBX动画；原FBX保留，元数据记录Unity Shader未复现。未经这条显式路径，原有动态资产拒绝规则不变。`[verified: assets.py import_asset；scripts/agent_asset_worker.py main]`

读取FBX身份恢复记录：只有控制点、原多边形、loop与UV值都匹配时，才使用原UVIndex连接。退回保守loop身份时，不能把新增切分当成作者的真实岛数。不能按三维或UV坐标接近焊接。`[verified: scripts/agent_fbx_uv.py restore_loop_uv_ids]`

非活动UV保存在哈希绑定的逐角数组中。固定几何分支保持这些值；镜像分支通过来源重心映射继承，导出为 `TEXCOORD_1+`。独立重新导入导出GLB核对，不能只检查原FBX还存在就称第二UV已保留。`[verified: extra_uv.py inherit_extra_uv/append_extra_uv_glb；material.py _preserve_export_uv]`

## 执行顺序

先小尺寸导入与来源视图，再无共享烘焙、区域共享和紧凑排布。已有原始交叠单列，不能冒称新增复用；无UV低模必须明确初始化来源，不套用其它模型的原岛数。保留源码版本、父子状态、真实贴图、失败及同镜头前后图；数值拒绝与Agent外观判断分开记录。`[opinion]`
