"""Static visual experiment report plus a genuinely anonymized human-review package."""

import html
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .mesh import sha256
from .pipeline import write_json
from .report_details import anytime_svg, atlas_overlay, detail_crops, editing_scene, enrich

CSS = """
:root{--paper:#eeeae0;--ink:#172d2a;--accent:#af542e;--line:#b5b9ab}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px 'Microsoft YaHei',sans-serif}
header{padding:50px 5vw 30px;border-bottom:2px solid var(--ink)}h1{font:52px Georgia,serif;margin:5px 0 16px}
.eyebrow{letter-spacing:.2em;color:var(--accent);font-size:12px}p{line-height:1.8;max-width:1000px}
main{padding:24px 5vw}.toolbar{display:flex;gap:16px;align-items:center;margin-bottom:25px;flex-wrap:wrap}
select,button,input,textarea{font:inherit;padding:9px;border:1px solid var(--line);background:#faf8f2;color:var(--ink)}
button{cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:26px}
article{border-top:3px solid var(--ink);padding-top:12px;min-width:0}h2{font-size:20px;margin:8px 0}.status{color:var(--accent)}
.views{display:grid;grid-template-columns:1fr 1fr;gap:7px}.views img{width:100%;background:#182021;aspect-ratio:1;object-fit:contain}
figure{margin:0}figcaption{font-size:12px;padding:6px 0}.numbers{display:flex;gap:20px;flex-wrap:wrap;margin:12px 0;font-size:13px}
.numbers b{display:block;font:24px Georgia,serif}.note{font-size:13px;color:#56625c;overflow-wrap:anywhere}.hidden{display:none}
a{color:var(--ink)}details{margin:16px 0}summary{cursor:pointer}.review{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
textarea{width:100%;min-height:70px}@media(max-width:600px){h1{font-size:36px}header{padding:28px 6vw}.grid{grid-template-columns:1fr}}
.atlas-images{position:relative;background:#101820}.atlas-images img{display:block;width:100%}.atlas-outline{position:absolute;inset:0;pointer-events:none}.atlas input:not(:checked)~.atlas-images .atlas-outline{display:none}
"""


def figure(root: Path, path: Path, label: str) -> str:
    if not path.exists():
        return f'<figure><p class="note">{html.escape(label)}：未生成</p></figure>'
    relative = html.escape(path.relative_to(root).as_posix(), quote=True)
    return f'<figure><a href="{relative}"><img loading="lazy" src="{relative}" alt="{html.escape(label)}"></a><figcaption>{html.escape(label)}</figcaption></figure>'


def generate_report(root: Path) -> None:
    ledger = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    repository = Path(__file__).resolve().parents[2]
    report_files = [
        "src/reseam/report.py",
        "src/reseam/report_details.py",
        "src/reseam/diagnostics.py",
        "docs/artist-review-task.md",
        "scripts/reseam_annotation_addon.py",
    ]
    write_json(
        root / "report_source.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip(),
            "source_sha256": {path: sha256(repository / path) for path in report_files},
            "scope": "Report generation and offline diagnostics; geometric experiment source remains source.json",
        },
    )
    offline = {}
    for record in ledger["runs"]:
        try:
            offline[record["path"]] = enrich(root / record["path"])
        except (ValueError, TypeError, RuntimeError, OSError) as error:
            offline[record["path"]] = {"status": "DIAGNOSTIC_FAILED", "reason": str(error)}
    write_json(root / "offline_diagnostics.json", offline)
    detail_crops(root, ledger["runs"])
    cards = []
    summaries = []
    for record in ledger["runs"]:
        path = root / record["path"]
        quality = record.get("quality") or {}
        images = path / "downstream"
        downstream_data = (
            json.loads((images / "result.json").read_text(encoding="utf-8"))
            if (images / "result.json").exists()
            else {}
        )
        coverage = downstream_data.get("packing", {}).get("coverage")
        uv = images / "pack/uv_layout.png"
        if not uv.exists():
            uv = path / "uv_layout.png"
        panels = figure(root, uv, "UV 布局") + figure(
            root, images / "renders_blender/view_00.png", "Blender 烘焙低模"
        )
        panels += figure(root, images / "renders_high/view_00.png", "真实材质高模参考")
        panels += figure(root, images / "renders_differentiable/view_00.png", "可微优化低模")
        if (images / "blender/BaseColor.png").exists():
            panels += figure(root, images / "blender/BaseColor.png", "烘焙 Base Color")
            panels += figure(root, images / "blender/Normal.png", "烘焙切线空间法线")
        if (path / "details/blender.png").exists():
            panels += figure(root, path / "details/high.png", "共同选区：高模局部")
            panels += figure(root, path / "details/blender.png", "共同选区：烘焙局部")
        metrics = "".join(
            f"<span>{label}<b>{value}</b></span>"
            for label, value in (
                ("UV 岛", quality.get("charts", "—")),
                (
                    "接缝长度",
                    f"{quality['seam_length_normalized']:.4f}"
                    if "seam_length_normalized" in quality
                    else "—",
                ),
                ("平均畸变", f"{quality['sd_mean']:.5f}" if quality.get("sd_mean") is not None else "—"),
                ("最大畸变", f"{quality['sd_max']:.3f}" if quality.get("sd_max") is not None else "—"),
                ("UV 覆盖率", f"{coverage:.2%}" if coverage is not None else "—"),
            )
        )
        evidence = (
            f'<a href="{html.escape(record["path"])}/result.json">运行证据</a>'
            if (path / "result.json").exists()
            else '<span class="note">未运行项见完整矩阵</span>'
        )
        extra = offline.get(record["path"], {})
        diagnostics = extra.get("diagnostics", {})
        diagnostic_text = ""
        if diagnostics.get("status") == "COMPUTED":
            diagnostic_text = (
                f"细长岛面积占比 {diagnostics['thin_chart_area_fraction']:.2%}；"
                f"边界转折代理 {diagnostics['turn_ratio_area_weighted']:.3f}。几何代理不等于艺术家评分。"
            )
        elif diagnostics.get("flagged_face_area_fraction") is not None:
            diagnostic_text = f"被标记面占原表面积 {diagnostics['flagged_face_area_fraction']:.8%}；严格失败不等于整张图都不可用。"
        density = extra.get("packing_diagnostics")
        if density:
            diagnostic_text += (
                f" 岛间 Texel Density 变异系数 {density['chart_density_area_weighted_cv']:.3%}；"
                f"排布前后面内各向异性最大相对变化 {density['face_anisotropy_max_relative_change_after_packing']:.3%}。"
            )
        downloads = ""
        if (images / "blender/asset.glb").exists():
            downloads += (
                f'<a href="{html.escape(record["path"])}/downstream/blender/asset.glb">烘焙模型</a> · '
            )
        if extra.get("optimized_asset", {}).get("status") == "VERIFIED":
            downloads += f'<a href="{html.escape(record["path"])}/downstream/differentiable/asset.glb">优化后模型</a> · '
        downstream_status = "未安排或上游未通过"
        if downstream_data:
            downstream_status = str(downstream_data["status"])
            if downstream_data.get("reason"):
                downstream_status += ": " + str(downstream_data["reason"])
        cards.append(
            f'<article data-model="{record["model"]}" data-seed="{record["seed"]}" data-status="{record["status"]}">'
            f'<div class="eyebrow">{record["model"]} / seed {record["seed"]}</div>'
            f'<h2>{html.escape(record["method"])}</h2><span class="status">{record["status"]}</span>'
            f'<div class="numbers">{metrics}</div><div class="views">{panels}</div>'
            f'{atlas_overlay(root, path, str(len(cards)))}{anytime_svg(path)}<p class="note">{html.escape(diagnostic_text)}</p>'
            f'<p class="note">下游：{html.escape(downstream_status)}</p>'
            f'<p class="note">{html.escape(str(quality.get("errors", record.get("reason", ""))))}</p>'
            f"{downloads}{evidence}</article>"
        )
    for model in sorted({r["model"] for r in ledger["runs"]}):
        for method in sorted({r["method"] for r in ledger["runs"]}):
            group = [r for r in ledger["runs"] if r["model"] == model and r["method"] == method]
            values = [
                r["quality"]["seam_length_normalized"]
                for r in group
                if r["status"] == "SUCCESS" and r.get("quality")
            ]
            summaries.append(
                {
                    "model": model,
                    "method": method,
                    "planned": len(group),
                    "success": len(values),
                    "seam_median": float(np.median(values)) if values else None,
                    "seam_min": min(values) if values else None,
                    "seam_max": max(values) if values else None,
                }
            )
    write_json(
        root / "summary.json",
        {
            "methods": summaries,
            "execution_complete": ledger["execution_complete"],
            "v1_complete": False,
            "artist_assessment": ledger["artist_assessment"],
            "scope": "Two repeatedly used development assets; no unseen-model generalization claim",
        },
    )
    completed = sum(r["status"] not in ("PENDING", "RUNNING", "NOT_RUN_PHASE_BUDGET") for r in ledger["runs"])
    intro = f"已记录 {completed}/{len(ledger['runs'])} 个几何配置。失败与超时保留在矩阵中。Artist 人工验收：待完成。"
    if ledger.get("stop_reason"):
        intro += f" 本轮已停止：{html.escape(ledger['stop_reason'])}；产物仅作诊断，不用于最终同预算排名。"
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += f'<title>ReSeam · {ledger["round_id"]}</title><style>{CSS}</style><header><div class="eyebrow">RESEAM / RESEARCH LOG</div>'
    page += f'<h1>接缝，重新组织。</h1><p>{intro}</p><p class="note">只比较固定低模与预定设置。岛数、畸变与烘焙质量分别报告；多轮和多种子不增加独立模型数。</p>'
    page += '<p class="note">在不牺牲拉伸、合理 Texel Density 与安全 Padding 的前提下提高覆盖率；岛间距不是越小越好。UV 覆盖率按三角形面积统计，不含扩边填色。当前统一为 1024²、岛间距至少 16 px、烘焙扩边 8 px；该诊断不替代 V1 展开质量或 artist 验收。</p>'
    page += '<a href="summary.json">汇总数据</a> · <a href="ledger.json">完整矩阵</a> · <a href="blind/index.html">匿名评审</a></header>'
    page += '<main><div class="toolbar"><label>模型 <select id="model"><option value="">全部</option><option>energy_blade</option><option>dragon</option></select></label>'
    page += '<label>种子 <select id="seed"><option>17</option><option>29</option><option>43</option><option value="">全部</option></select></label>'
    page += '<label><input id="valid" type="checkbox">仅查看有效 UV</label><span class="note">点击图像查看原图</span></div><div class="grid">'
    page += (
        "".join(cards)
        + '</div></main><script>function filter(){document.querySelectorAll("article").forEach(a=>a.classList.toggle("hidden",(model.value&&a.dataset.model!==model.value)||(seed.value&&a.dataset.seed!==seed.value)||(valid.checked&&a.dataset.status!=="SUCCESS")))}model.onchange=filter;seed.onchange=filter;valid.onchange=filter;filter();</script></html>'
    )
    (root / "index.html").write_text(page, encoding="utf-8")
    notes = [f"# {ledger['round_id']} 实验报告", "", intro, "", "## 方法汇总", ""]
    for row in summaries:
        notes.append(
            f"- {row['model']} / {row['method']}：有效输出 {row['success']}/{row['planned']}；接缝长度中位数 {row['seam_median']}。"
        )
    notes += [
        "",
        "## 边界",
        "",
        "- 完整搜索轨迹的比较与固定候选池支持域实验分开解释。",
        "- 几何实验使用三个种子；昂贵下游验收按预先配置只运行种子 17，不代表另两个种子的烘焙已验证。",
        "- 未确认的自动建议仅作软偏好；无人为确认或盲评结果时，V1 的 artist 目标未验收。",
        "- 数值验证不等于形式化证明，两个模型不代表独立模型泛化。",
        "",
        "[视觉对比](index.html) · [完整执行记录](ledger.json) · [匿名评审](blind/index.html)",
    ]
    (root / "REPORT.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    blind_report(root, ledger)


def blind_report(root: Path, ledger: dict[str, Any]) -> None:
    folder = root / "blind"
    folder.mkdir(exist_ok=True)
    repository = Path(__file__).resolve().parents[2]
    shutil.copy2(repository / "docs/artist-review-task.md", folder / "task.md")
    items = []
    for record in ledger["runs"]:
        downstream = root / record["path"] / "downstream/result.json"
        if record["seed"] == 17 and record["status"] == "SUCCESS" and downstream.exists():
            data = json.loads(downstream.read_text(encoding="utf-8"))
            if (
                data.get("input_uv_unchanged")
                and (downstream.parent / "renders_blender/view_00.png").exists()
            ):
                items.append(record)
    np.random.default_rng(2026).shuffle(items)
    key_path = root / "blind_key.private.json"
    keys = json.loads(key_path.read_text(encoding="utf-8")) if key_path.exists() else {}
    path_codes = {path: code for code, path in keys.items()}
    cards = []
    for record in items:
        code = path_codes.get(record["path"])
        if code is None:
            index = max((int(key.removeprefix("sample_")) for key in keys), default=0) + 1
            code = f"sample_{index:02d}"
            keys[code] = record["path"]
            path_codes[record["path"]] = code
        source = root / record["path"]
        paths = [
            (source / "downstream/pack/uv_layout.png", "uv.png"),
            (source / "downstream/renders_blender/view_00.png", "view.png"),
            (source / "downstream/blender/asset.glb", "asset.glb"),
            (source / "downstream/renders_high/view_00.png", "reference.png"),
            (source / "details/blender.png", "detail.png"),
            (source / "details/high.png", "detail_reference.png"),
        ]
        if not paths[0][0].exists():
            paths[0] = (source / "uv_layout.png", "uv.png")
        destination = folder / code
        destination.mkdir(exist_ok=True)
        for origin, name in paths:
            if origin.exists():
                shutil.copy2(origin, destination / name)
        figures = figure(folder, destination / "uv.png", "UV") + figure(
            folder, destination / "view.png", "贴图低模"
        )
        figures += figure(folder, destination / "reference.png", "高模参考")
        if (destination / "detail.png").exists():
            figures += figure(folder, destination / "detail.png", "共同选区局部")
            figures += figure(folder, destination / "detail_reference.png", "高模局部参考")
        if (destination / "asset.glb").exists():
            figures += f'<a href="{code}/asset.glb" download>下载匿名模型进行编辑</a>'
        try:
            scene = editing_scene(repository, source, destination)
            if scene is not None:
                figures += f' · <a href="{code}/edit_scene/editable.blend" download>可编辑 Blender 场景</a>'
        except (ValueError, RuntimeError, OSError) as error:
            write_json(destination / "editing_scene_error.json", {"reason": str(error)})
        cards.append(
            f'<article data-code="{code}"><h2>{code} / {record["model"]}</h2><div class="views">{figures}</div>'
            f'<div class="review"><button onclick="start(this)">开始修整计时</button><button onclick="stop(this)">停止</button><span class="elapsed">0 秒</span></div>'
            '<label>偏好评分（1–5） <input class="score" type="number" min="1" max="5"></label>'
            '<label><input class="done" type="checkbox">达到约定交付标准</label><input class="technical" placeholder="技术检查结果文件名（勾选完成时必填）"><textarea placeholder="修改操作、接缝位置与未完成原因"></textarea></article>'
        )
    write_json(root / "blind_key.private.json", keys)
    page = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ReSeam 匿名评审</title><style>{CSS}</style>'
    page += '<header><div class="eyebrow">RESEAM / HUMAN REVIEW</div><h1>人工可用性评审</h1><p>方法名称已隐藏。每样例最多 15 分钟；记录真实修整时间和操作。几何合法性与确认约束需由验证器复核，勾选不自动证明通过。</p>'
    page += (
        '<a href="task.md">统一编辑任务与验收规则</a><br><label>评审者 <input id="artist"></label> <button onclick="save()">导出评审记录</button><p id="form_error" role="alert" class="status"></p></header><main><div class="grid">'
        + "".join(cards)
    )
    page += """</div></main><script>
const times={};function start(b){const a=b.closest('article');times[a.dataset.code]={start:Date.now(),seconds:0};}
function stop(b){const a=b.closest('article'),t=times[a.dataset.code];if(t){t.seconds=(Date.now()-t.start)/1000;t.stopped=true;a.querySelector('.elapsed').textContent=t.seconds.toFixed(1)+' 秒';}}
function error(message){document.getElementById('form_error').textContent=message;}function save(){error('');if(!artist.value.trim()){error('请填写评审者');return;}const records=[];for(const a of document.querySelectorAll('article')){const raw=a.querySelector('.score').value,score=raw===''?null:Number(raw),t=times[a.dataset.code],done=a.querySelector('.done').checked,notes=a.querySelector('textarea').value,technical=a.querySelector('.technical').value;if(score!==null&&(!Number.isInteger(score)||score<1||score>5)){error('评分必须是1–5的整数');return;}if(done&&(!t?.stopped||t.seconds>900||!technical.trim()||!notes.trim()||score===null)){error('完成声明需要限时停止计时、评分、操作记录和技术检查结果文件名');return;}records.push({sample:a.dataset.code,seconds:t?.seconds??null,timer_stopped:t?.stopped??false,score,claimed_complete:done,technical_report_reference:technical,notes});}const blob=new Blob([JSON.stringify({artist:artist.value,task_version:'artist-review-v1',records},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='artist_review.json';a.click();}
</script></html>"""
    (folder / "index.html").write_text(page, encoding="utf-8")
