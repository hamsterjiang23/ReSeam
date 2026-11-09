# 美术工具交付记录

七类能力已实现为十个 `artist.*` 入口，统一从现有 JSON CLI 调用。新美术编辑采用目视保留，数值结果作参考，旧严格结果独立保留。`[verified: src/reseam/agent_tools/registry.py artist.*；artist.py visual_policy；artist_review.py review/export_visual]`

- [镜像龙工具入口](../../output/artist-tools/dragon-38/index.html)。`[verified: output/artist-tools/dragon-38/index.html]`
- [离线外观工作台](../../output/artist-tools/dragon-38/desk/review.html)，同视角比较 40 份与最终 38 份的实际贴图，支持点图定位、共享伙伴高亮、固定顶点、保护设置、闪烁、放大和复制操作请求。`[verified: output/artist-tools/dragon-38/verification.json browser；desk/comparison.json]`
- [真实 GLB 渲染](../../output/artist-tools/dragon-38/render/preview.html)，2 个角度 × 2 种灯光 × 2 个画面大小 × 2 个可用显示通道，共 16 张图。龙的 GLB 无法线贴图，已明确列为未提供。`[verified: render/preview.json images、unavailable]`
- [工具说明与请求示例](../artist-tools.md)。`[verified: docs/artist-tools.md]`

## 验证

|检查|实际结果|
|---|---|
|相关单元与回归|107 项通过，含新增 15 项美术工具测试。`[verified: output/artist-tools/dragon-38/verification.json unit_and_regression_tests；本轮 pytest 输出]`|
|代码检查|Ruff 通过，basedpyright 0 错误/警告，指定文件 git diff --check 通过。`[verified: verification.json ruff/basedpyright/git_diff_check；本轮命令输出]`|
|真实龙 CLI|11 次调用全部 OK，另有最终 compare、preview 调用。两种独立局部试做均 OK，原始快照哈希不变。`[verified: artifacts/artist_tools/real-smoke01/summary.json、trials/trials.json；desk/result.json、render/result.json]`|
|共享组与放松|实际四成员角刺组的局部放松移动 9 个主侧顶点并同步全部成员，最大 UV 位移约 0.00167。测试输出另存，没有替换最终龙。`[verified: artifacts/artist_tools/real-smoke01/relax/result.json movable_uv_count、max_uv_motion、synchronized_groups；summary.json original_hash_unchanged]`|
|浏览器交互|模型点击选中 221 面、关联 442 面；四成员组显示 174 关联面；Shift 固定点 1 个写入请求；放大滚动、闪烁、复制成功，无 JavaScript 错误。`[verified: verification.json browser；本轮 agent-browser 输出]`|
|实际模型重导入|Blender 4.5.11 LTS 读入最终 GLB，几何/UV 与快照匹配。`[verified: render/preview.json geometry_and_uv_reimport_match、blender_version]`|
|材质通道冒烟|另一个真实测试 GLB 的材质、颜色、粗糙度、金属度、法线五种视图均生成。`[verified: artifacts/artist_tools/channel-smoke01/render/preview.json images、unavailable=[]]`|
|独立复查|发现并修复六项问题，定向复核 CLEAN。涉及旧目标绕过颜色保护、共享从侧固定点、退回贴图依赖、未完成排布标记、观察视角范围与保护名称覆盖。`[verified: 本轮 artist_tools_review 复核；tests/test_artist_tools.py 对应回归]`|

工作台是离线看图与操作请求界面，使用准备好的视角，实际编辑由 Agent/CLI 执行。早期自动下载测试被浏览器取消，最终页面使用已测试的可复制请求，不包含下载按钮。`[verified: artist_web.py PAGE；verification.json browser.delivery_transfer]`

实际看过龙模型正面、背面和斜前方后，Agent 记录目视保留，生成独立目视导出记录。该记录没有改写旧数值拒绝，也不是人工、Unity 或完整 PBR 验收。`[verified: artifacts/artist_tools/real-smoke01/review/review.json；export/visual-export.json]`
