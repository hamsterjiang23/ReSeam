# 防御塔制作过程与全方法对照

[制作过程 PDF](../../../output/pdf/tower-making-process/防御塔制作过程图解.pdf)共6页。页面依次展示输入、六种基线、原形接缝整理、半边镜像、共享后的继续合岛、颜色与法线近景。`[verified: ../../../output/pdf/tower-making-process/evidence.json pages；verification.json pages]`

## 直接查看

- [UV 全方法对照图](report/UV全方法对照.png)。`[verified: report/figure_sources.json]`
- [颜色与法线对照图](report/颜色与法线对照.png)。`[verified: report/figure_sources.json]`
- [关键部位近景对照图](report/关键部位近景对照.png)。`[verified: report/appearance_review.json reviewed_closeup_views]`
- [完整指标 CSV](report/metrics.csv)、[原始结果 JSON](report/results.json)及[外观检查记录](report/appearance_review.json)。`[verified: report/metrics.csv；results.json；appearance_review.json]`
- [HTML 报告](report/index.html)包含各版本的模型链接，完成下方还原后可在本地打开。`[verified: report/index.html href]`

最终保留的镜像塔为7,650个三角形、48个连通岛、24个独立图块，覆盖率56.82%。同一几何的无共享对照为48个独立图块。两者均使用2K贴图，完整mip的解码字节均为67,108,860。`[verified: report/results.json method=mirror_negative_exhaustive_minimum_shared / mirror_negative_exhaustive_minimum_unshared]`

Agent检查结论为VISUAL_KEEP。数值结果仍为DIAGNOSTIC，美术人工与游戏Shader记录为NOT_RUN。镜像会将保留侧的细小不对称装饰复制到另一侧，实际观察范围和差异记录在外观检查文件中。`[verified: report/appearance_review.json decision/numeric_status/human_acceptance/game_shader/observations]`

FlattenAnything的原生重叠结果、OptCuts的原生失败与修复后超时、ReSeam的失败候选和修正步骤均收录在完整归档中。`[verified: report/results.json method=flatten_anything / optcuts；report/run_index.json]`

## 完整实验归档

`archive/`保存`artifacts/tower_uv_comparison/round01`与`round02`全部文件的ZIP分卷。`archive_manifest.json`记录分卷顺序、大小和SHA-256，`source_files.json`记录每个原始文件的路径、字节与SHA-256。`[verified: archive_manifest.json parts/source_file_count；source_files.json files]`

归档包含输入副本、请求、日志、原生算法输出、不可变快照、各版本GLB、真实贴图、完整mip、渲染、报告、代码快照，以及打包并重读过的19模型Blender对照场景。`[verified: source_files.json files；report/integrity.json model_checks；round02/comparison_scene_receipt.json model_count/packed_and_reopened]`

采用分卷ZIP保存完整实验，单个分卷最多40MiB。报告副本中的模型路径已改为指向还原后的仓库内实验目录，归档内保留原始报告字节。`[verified: archive_manifest.json parts；report/index.html href；source_files.json files]`

### 还原方法

在新检出的仓库根目录执行以下PowerShell代码。该操作校验所有分卷与合并ZIP的SHA-256，再恢复原有实验目录。`[verified: archive_manifest.json archive_sha256/parts；下方代码]`

```powershell
$towerPackage = 'docs/assets/tower-uv-comparison'
$towerMeta = Get-Content -Raw -LiteralPath "$towerPackage/archive_manifest.json" | ConvertFrom-Json
New-Item -ItemType Directory -Path tmp/tower-archive -Force | Out-Null
$towerZip = Join-Path (Get-Location) "tmp/tower-archive/$($towerMeta.archive_name)"
$towerOutput = [IO.File]::Create($towerZip)
try {
    foreach ($towerPart in $towerMeta.parts) {
        $towerPartPath = Join-Path $towerPackage $towerPart.path
        $towerPartHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $towerPartPath).Hash.ToLowerInvariant()
        if ($towerPartHash -ne $towerPart.sha256) { throw "分卷校验失败 $towerPartPath" }
        $towerPartStream = [IO.File]::OpenRead((Join-Path (Get-Location) $towerPartPath))
        try { $towerPartStream.CopyTo($towerOutput) }
        finally { $towerPartStream.Dispose() }
    }
}
finally { $towerOutput.Dispose() }
$towerZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $towerZip).Hash.ToLowerInvariant()
if ($towerZipHash -ne $towerMeta.archive_sha256) { throw '完整ZIP校验失败' }
uv run --no-sync python -m zipfile -e $towerZip .
```

还原后打开下列文件。`[verified: source_files.json files]`

- `artifacts/tower_uv_comparison/round02/report/index.html`
- `artifacts/tower_uv_comparison/round02/comparison.blend`
- `artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_shared_2048/export/asset.glb`

原始记录中的绝对路径以`E:\ReSem`为来源位置。其他目录检出时，HTML相对链接仍指向对应文件，重新运行工具请求前需替换请求内的绝对路径。`[verified: report/results.json snapshot/folder/model；report/index.html href；source_files.json files]`
