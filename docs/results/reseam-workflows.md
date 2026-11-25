# ReSeam 制作经验与可复用工作流

本轮将分部件制作经验补入 `reseam-uv`，提供 `rebuild`、`preview`、`collect`、`report` 四项辅助工作流。正式几何与材质操作继续调用已有JSON工具，辅助入口不增加一种质量认可。`[verified: src/reseam/workflows/__main__.py COMMANDS、run_workflow；skills/reseam-uv/scripts/tools.json]`

## 实际验证

- 非恐龙输入由两个平面部件组成，共4张三角面，各有真实蓝／红BaseColor；实际执行 `asset.prepare_parts`、OptCuts逐部件重展开、Blender凹形排布与512²颜色烘焙。目标检查和烘焙检查分别查看六视角；左右侧视沿平面边缘为空，其余视角显示表面。`[verified: output/reseam-workflows-20261125/smoke-progress.json；target-review-sheet.png；bake-review-sheet.png]`
- `collect` 消费真实 `artist.compare → artist.review` 整模型记录，导出两个具名对象，独立回读面覆盖、位置、UV和贴图。该小样为Agent目视保留，数值质量未验收；不标为人工、GPU或完整PBR通过。`[verified: output/reseam-workflows-20261125/delivery-fixed/verification.json named_objects、numeric_accepted、reviewer、full_pbr_passed]`
- 恐龙21条历史记录通过通用请求生成4张方法／历史对照图及PDF/HTML。各条仍保留自身几何、数值协议、失败和未运行状态，源文件哈希前后核对；未重跑历史展开、排布、烘焙或质量验收。`[verified: output/reseam-workflows-20261125/dragon-report-verified/evidence.json execution_scope、cases、source_sha256]`
- 独立Agent从临时安装版调用launcher，成功生成小样报告，并正确使用案例区分部件编号、真实合岛、小片删除及图块／纹理字节。首次发现单列副标题裁切后，主副标题纳入实际字体边界检查；重新安装的版本看图复测通过。`[verified: docs/assets/reseam-workflows/forward-evaluation.json；forward-fix-retest.json；tests/test_reseam_workflow_report.py test_single_column_headers_fit_canvas]`

## 修复与边界

GLB回读按Trimesh的实际V翻转／float32量化链精确核对，未采用任意误差容忍。`collect`按实际比较记录和烘焙范围识别评审，保留评审原说明。报告对绑定烘焙的所有贴图校验冻结哈希，改写贴图后拒绝沿用旧验收身份。`[verified: src/reseam/workflows/parts.py _gltf_roundtrip_uv、_review；report.py _card；相关回归测试]`

安装包携带脚本、五份请求模板、三张案例小图及匹配的执行源码快照；源码以逐文件哈希和完整内容哈希标识。独立缓存不会覆盖用户checkout，原安装合并、备份与回滚保留。安装和 `--check` 不代表所有外部后端已执行，本轮也未重装或重测GPU研究后端。`[verified: scripts/package_reseam_skills.py runtime_payload、build_bundle；install_reseam_skills.py validate_runtime、install_bundle；skills/reseam-uv/scripts/reseam_workflow.py extract_runtime；opinion: 验收范围]`

本轮相关pytest 87项通过，全项目basedpyright零错误，所选变更与相关历史脚本的Ruff通过；Skill格式、89个本地链接、JSON及随包图片检查通过。`[verified: 本轮检查输出；docs/assets/reseam-workflows/verification.json]`

入口、请求字段和示例见 [工作流说明](../../skills/reseam-uv/references/workflows.md)；过程偏差见 [实现记录](../implementation-notes-reseam-workflows.md)。`[verified: 对应文件]`
