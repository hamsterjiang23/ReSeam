# 美术工具与目视验收

本轮按用户要求增加七类能力。新流程以同角度实际颜色图和导出模型的目视判断决定保留或退回，数字留作参考。原数值验收入口保留，目视接受单独记录，不改写历史检查结果。`[verified: 用户“全部补充工具……只需要肉眼判断没有什么差别”；artist_review.py review/export_visual]`

## 可以做什么

|能力|入口|具体行为|
|---|---|---|
|模型与展开图对应|`artist.select`|从图片像素、展开图坐标、面或区域选取，返回对应小片和全部共享伙伴。共用位置同时命中多个表面。`[verified: artist.py select]`|
|保护重要部位|`artist.protect`|给白爪、眼嘴等起名字，保护展开形状、固定位置、颜色或同分辨率下的清晰度。不同区域不能无提示覆盖同名保护。`[verified: artist.py protect/enforce_locks]`|
|相同角度看变化|`artist.compare`|读取每个状态真实烘焙的颜色图，输出前后、差异、局部放大和离线工作台。支持模型/展开图点击、共享伙伴高亮和闪烁切换。`[verified: artist_review.py compare；artist_web.py PAGE]`|
|试做与退回|`artist.trials`、`artist.checkpoint`|同一父版本最多八个独立方案，保留失败记录，不自动按数字选赢家。恢复完整旧版本以及经过哈希核对的贴图引用。`[verified: artist.py trials/checkpoint]`|
|固定点与局部放松|`artist.relax`|固定边界和指定顶点，调整整块内部位置。同组其它表面同步移动，在另一成员上设置的固定点也会生效。`[verified: artist.py relax]`|
|编辑共享组|`artist.groups`|列出所有成员，选择颜色主部位、移出一个完整成员或解除整组共享。四成员组也可编辑，移出后需重新摆放。`[verified: artist.py groups]`|
|真实交付预览|`artist.preview`|Blender 重新读入实际导出的 GLB，核对几何和展开位置，再用不同灯光、远近大小和材质通道渲染。缺失法线贴图明确列为未提供。`[verified: artist_review.py preview；scripts/artist_render_worker.py main]`|

`artist.review` 记录看图后的保留/退回意见；`artist.export` 依据已保留的对比记录导出。记录者区分 Agent 和美术本人，不能把 Agent 结论填写成人工确认。`[verified: artist_review.py review/export_visual]`

## 一次完整操作

1. 从当前模型保存 `artist.checkpoint`，把当前 `bake.json` 放入 `files`。工具也会记录其贴图和目标依赖，恢复时检查这些文件是否被改动。`[verified: artist.py checkpoint]`
2. 通过 `artist.select` 定位部位，需要时调用 `artist.protect`。保护形状允许整块平移、旋转和统一缩放；保护位置固定坐标；保护清晰度禁止 UV 面积下降，针对相同贴图分辨率。颜色保护指保留源颜色目标，不允许通过旧共享目标绕过。`[verified: artist.py enforce_locks；color_reuse.py ColorTarget]`
3. 使用 `artist.relax` 或 `artist.groups`，也可通过 `artist.trials` 比较几种展开/排布方案。每个方案从同一父状态开始。修改不覆盖父文件。`[verified: artist.py finish/trials]`
4. 需要重新摆放时调用 `uv.pack`。重新冻结颜色目标，调用 `material.bake mode=basecolor_reuse` 生成真正贴图。在新美术流程中使用 `acceptance_policy="visual"`，颜色目标与烘焙保留数值报告，但不会因数值超限自动否决。不能显示的非有限/零面积展开、过期文件和明确设置的保护冲突仍会报错。`[verified: color_reuse.py freeze_target/bake_color_reuse；artist.py visual_policy]`
5. 调用 `artist.compare` 生成同视角对比，实际看过准备的视角后用 `artist.review` 写一句观察结果。局部对比只记录局部意见；整模导出需要整模对比且已查看其中准备的视角。这里检查的是“看了什么”，不设置颜色误差分数门槛。`[verified: artist_review.py compare/review/export_visual]`
6. 保留后用 `artist.export` 导出，必要时用 `artist.preview` 检查实际交付文件。退回则使用 `artist.checkpoint action=restore` 返回完整旧版本。`[verified: artist_review.py export_visual/preview；artist.py checkpoint]`

## 调用方法

统一沿用 JSON CLI，每次使用一个全新的输出目录。`[verified: registry.py；runner.py run_tool]`

```powershell
uv run reseam tools describe artist.relax
uv run reseam tools run artist.relax --request E:/ReSem/my-relax.json --output E:/ReSem/artifacts/my-relax-01
```

局部放松请求示意，路径和面编号换成当前模型的实际值。`[opinion]`

```json
{
  "snapshot": "E:/ReSem/artifacts/current/snapshot/snapshot.json",
  "selected_faces": [100, 101],
  "pin_uv_ids": [20],
  "pin_boundary": true,
  "iterations": 20,
  "strength": 0.25
}
```

比较请求中的 `before` 包含旧版本和旧贴图，外层是新版本及新贴图。工具核对实际状态与烘焙绑定，防止误把旧颜色图贴到新展开位置上。`[verified: artist_review.py _bound_bake/compare]`

```json
{
  "snapshot": "E:/ReSem/artifacts/new/snapshot/snapshot.json",
  "bake": "E:/ReSem/artifacts/new/bake/bake.json",
  "before": {
    "snapshot": "E:/ReSem/artifacts/old/snapshot/snapshot.json",
    "bake": "E:/ReSem/artifacts/old/bake/bake.json"
  },
  "size": 640,
  "cameras": [
    {"name": "front", "azimuth": 0, "elevation": 0},
    {"name": "back", "azimuth": 180, "elevation": 0}
  ]
}
```

工作台支持离线打开，按钮生成绑定当前版本的请求 JSON，可直接复制给 Agent，或另存后通过 CLI 执行。页面没有后台服务，点击不会直接写模型文件。Shift＋点击展开顶点可添加/取消固定点。页面列出已有保护名称，新增保护需命名。`[verified: artist_web.py PAGE]`

## 实际范围

- 当前比较和目视导出使用真实 BaseColor 烘焙。Blender 预览呈现 GLB 已包含的材质，不能把基础颜色模型升级成完整 PBR 材质。`[verified: artist_review.py _bound_bake/export_visual；artist_render_worker.py channel_material]`
- 远近预览通过正交镜头改变模型在画面中的大小，不冒充指定 mip 等级或 Unity/GPU 验收。`[verified: artist_render_worker.py rendered.json distance_mode]`
- 局部放松采用固定边界的邻点平均与避免翻转的步长回退。同组通过当前共同展开位置传递位移；超出主部位覆盖范围时明确拒绝并建议先移出该成员。`[verified: artist.py relax]`
- 工作台中的图块组数是当前声明的摆放组数。移出成员之后仍须重新摆放，不能把重叠着的独立小片当作已完成排布。`[verified: artist.py basic_check/groups；geometry.py pack_uv]`
- 快照包含共享关系与保护设置。新快照绑定新状态哈希，继续使用时应重新生成绑定该状态的烘焙与对照，不直接沿用旧检查凭证。`[verified: state.py save_snapshot；color_reuse.py _load]`
