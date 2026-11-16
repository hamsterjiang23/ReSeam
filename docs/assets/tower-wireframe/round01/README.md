# 镜像共享防御塔多视角线框

[打开1×8高清线框图](镜像共享防御塔_多视角线框_1x8.png)，尺寸7232×1120，依次展示正面、背面、左侧、右侧、右前、左前、右后、左后。`[verified: manifest.json image_size/views；verification.json layout/view_count]`

来源为最终镜像共享版，7,650个三角形、48个UV连通岛、24个共享图块。渲染所用快照的状态哈希与最终GLB的导出记录一致，GLB文件SHA-256匹配既有独立重读检查记录。`[verified: manifest.json state_hash/source_export/model_sha256；verification.json render_state_matches_final_export；scripts/build_tower_wireframe_sheet.py main来源检查]`

图像由ReSeam的`view.render mode=wireframe`生成，采用正交自动适配、逐像素深度测试和灰色表面叠加可见三角网格。各镜头沿用该模型已保存的材质预览参数，角度见`render-request.json`。`[verified: render-request.json mode/cameras；render-report.json visibility/coordinate_convention；manifest.json renderer/camera_contract]`

8张独立1024×1024视角图位于`views/`，逐图SHA-256、可见表面边界和相机参数记录在`manifest.json`。8张图均已检查完整轮廓和线框显示。`[verified: manifest.json views；verification.json all_foreground_bounds_inside_frames/visual_review]`

源实验及快照的还原方法见[防御塔完整实验交付包](../../tower-uv-comparison/README.md)。复现渲染与拼图可在仓库根目录执行以下命令，渲染输出目录需使用新的路径。`[verified: ../../tower-uv-comparison/archive_manifest.json；render-request.json；scripts/build_tower_wireframe_sheet.py RUN/main；view.render工具契约]`

```powershell
uv run --no-sync reseam tools run view.render --request docs/assets/tower-wireframe/round01/render-request.json --output artifacts/tower-wireframe-views/round01/render
uv run --no-sync python scripts/build_tower_wireframe_sheet.py
```

本地原始渲染、面索引光栅和工具执行记录位于`artifacts/tower-wireframe-views/round01`。`[verified: render-report.json face_rasters；manifest.json source_sha256]`
