"""Build an unblinded comparison page from immutable run and review evidence."""

import argparse
import json
from pathlib import Path
from statistics import median


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=Path, required=True)
    args = parser.parse_args()
    folder = args.round.resolve()
    if folder.name != "v2_joint_round01":
        raise ValueError("This curated narrative is specific to v2_joint_round01")
    rows = json.loads((folder / "matrix.json").read_text(encoding="utf-8"))
    audit = json.loads((folder / "audit.json").read_text(encoding="utf-8"))
    data = []
    for row in rows:
        packet = audit["proxy"].get(f"{row['model']}_seed_{row['seed']}", {})
        review = next((s for s in packet.get("samples", []) if s["track"] == row["track"]), {})
        diagnostic = next((r for r in audit["downstream"] if r["path"] == row["path"]), {})
        quality = row.get("quality") or {}
        data.append(
            {
                **{
                    key: row[key] for key in ("model", "seed", "track", "path", "status", "downstream_status")
                },
                "coverage": quality.get("unique_coverage"),
                "gain": quality.get("gain"),
                "charts": quality.get("charts"),
                "reuse_groups": row.get("reuse_groups", 0),
                "scores": {d: value["median"] for d, value in review.get("scores", {}).items()},
                "recommendation": review.get("recommendation", "UNSCORED"),
                "source_coverage_flags": len(diagnostic.get("projection_or_frame_invalid_faces", [])),
            }
        )
    ratio = median(
        r["density_ratio"]
        for r in audit["paired_comparisons"]
        if r["model"] == "energy_blade" and r["comparison"] == "joint_vs_packing_only" and r["eligible"]
    )
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    template = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReSeam V2 · 联合实验</title><style>
:root{--paper:#eeeae0;--ink:#182825;--muted:#58635c;--line:#c9cec4;--accent:#9c4b30}*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 'DengXian','Microsoft YaHei',sans-serif}main{max-width:1500px;margin:auto;padding:32px}
.eyebrow{font:13px ui-monospace,monospace;letter-spacing:.09em;color:var(--muted)}h1{font:42px/1.2 'SimSun',Georgia,serif;margin:12px 0}h2{font-size:23px;margin:0 0 12px}h3{font-size:18px;margin:0}
.lede{max-width:960px;font-size:19px}.findings{display:grid;grid-template-columns:1fr 1fr;gap:36px;padding:22px 0;border-top:1px solid var(--ink);border-bottom:1px solid var(--ink);margin:24px 0}.number{font:30px Georgia,serif}small,.note{font-size:13px;color:var(--muted)}a{color:#235c50;text-underline-offset:3px}
.controls{display:flex;gap:14px;flex-wrap:wrap;align-items:end;margin:22px 0}label{display:flex;flex-direction:column;gap:5px;font-size:13px}select{font:15px 'DengXian','Microsoft YaHei',sans-serif;padding:8px 12px;background:#fffdf6;border:1px solid var(--line);color:var(--ink)}
#cases{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.case{background:#fffdf7;padding:16px;min-width:0}.case img{width:100%;display:block;background:#101820;aspect-ratio:1;object-fit:contain;margin:12px 0}.case .sub{font:12px ui-monospace;color:var(--muted)}.metrics{font-size:14px;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:12px}.scores{font-size:13px}.warn{color:var(--accent)}.links{font-size:13px;margin-top:13px;display:flex;gap:12px;flex-wrap:wrap}
#reference{display:flex;gap:20px;align-items:center;margin:22px 0;padding:18px 0;border-top:1px solid var(--line)}#reference[hidden]{display:none}#reference img{width:220px;max-width:40vw;background:#101820}.evidence{margin-top:28px;border-top:1px solid var(--ink);padding-top:20px}.evidence p{max-width:1050px}footer{margin-top:28px;font-size:13px;color:var(--muted)}
@media(max-width:1100px){#cases{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){main{padding:20px}h1{font-size:32px}.findings{grid-template-columns:1fr;gap:20px}#cases{grid-template-columns:1fr}}
</style><main>
<div class="eyebrow">RESEAM / V2 JOINT / ROUND 01</div><h1>排布有小幅改善，近似复用仍未成功。</h1>
<p class="lede">24项搜索、24项下游流程及18份代理盲评完成。有效改动来自切分与再排布；真实模型没有接受复用组。代理评价全部为 NEEDS_WORK，人工尚未复核。</p>
<p class="note">[verified: matrix.json / audit.json / review_summary.json] · 固定1024²，组间距16px，边界8px。</p>
<div class="findings"><section><h2>能量刀 / 500面</h2><div class="number">47.04% → 49.14%</div><p>唯一覆盖率的三种子中位数：固定岛 → 联合。<br>成对有效纹素密度比中位数 <strong>+__DENSITY__%</strong>。</p></section>
<section><h2>幻想龙 / 2000面</h2><div class="number">28.21% → 28.21%</div><p>四轨道、三个种子均保留初始UV。<br>120个复用候选未通过完整几何门禁。</p></section></div>
<h2>同模型、同种子、同视角对拍</h2><div class="controls">
<label>模型<select id="model"><option value="energy_blade">能量刀</option><option value="dragon">幻想龙</option></select></label>
<label>种子<select id="seed"><option>17</option><option>29</option><option>43</option></select></label>
<label>展示内容<select id="kind"><option value="uv">UV图集</option><option value="base">Base Color</option><option value="normal">法线预览</option><option value="mip_128">128² mip</option><option value="additional">冻结后附加视角</option></select></label>
<label>相机<select id="camera"></select></label></div><div id="cases"></div><div id="reference" hidden></div>
<section class="evidence"><h2>结论的范围</h2>
<p>UV占用率按三角形面积并集计算，不按贴图黑色像素计算。代理盲评的36组成对多数结论均为“平”，UV可编辑性中位分均为3；密度指标的改善尚未转化为可见的代理偏好。</p>
<p>龙有相同23个面出现Base Color alpha覆盖不足，约占表面积1.47%；法线alpha与TBN通过。这是独立下游诊断，不能归因为复用失败。真实复用候选没有进入材质门禁。</p>
<p>主视角属于既有回归集。附加视角在算法冻结后预设，单独保存，不进入原盲评包；两模型不提供独立模型泛化证据。</p>
<div class="links"><a href="audit.json">完整审计</a><a href="matrix.json">24项矩阵</a><a href="review_summary.json">评分与分歧</a><a href="protocol.json">锁定协议</a><a href="review_control/coordinator_final.json">评审尝试与重试</a><a href="additional_views/protocol.json">附加相机协议</a></div></section>
<footer>AGENT_EVALUATED ≠ artist验收。原始评分、失败候选、种子结果和耗时全部保留。来源：本目录冻结运行产物。</footer></main>
<script>const rows=__DATA__;const names={packing_only:'固定岛排布',seam_pack:'接缝—排布',reuse_pack:'仅近似复用',joint:'联合'};
const mainAngles=[[30,10],[120,10],[210,10],[300,10],[45,65],[225,-65]],extraAngles=[[15,35],[105,35],[195,35],[285,35]];
const model=document.getElementById('model'),seed=document.getElementById('seed'),kind=document.getElementById('kind'),camera=document.getElementById('camera');
function cameras(){const keep=camera.value||'0';const angles=kind.value==='additional'?extraAngles:mainAngles;camera.innerHTML=angles.map((v,i)=>`<option value="${i}">方位${v[0]}° / 仰角${v[1]}°</option>`).join('');camera.value=Number(keep)<angles.length?keep:'0';camera.disabled=kind.value==='uv';}
function render(){const mode=kind.value,id=Number(camera.value||0),file=`view_${String(id).padStart(2,'0')}.png`;const selected=rows.filter(r=>r.model===model.value&&String(r.seed)===seed.value);
document.getElementById('cases').innerHTML=selected.map(r=>{const path=mode==='uv'?`${r.path}/uv.png`:mode==='additional'?`additional_views/${r.path}/base/${file}`:`${r.path}/downstream/${mode}/${file}`;
const scores=['appearance','seams','normal_mip','uv_editability'].map((d,i)=>`${['外观','接缝','法线/mip','编辑性'][i]} ${r.scores[d]??'未评分'}`).join(' · ');
return `<article class="case"><h3>${names[r.track]}</h3><div class="sub">${r.track} / seed ${r.seed}</div><a href="${path}" target="_blank"><img src="${path}" alt="${names[r.track]} ${mode}"></a><div class="metrics">本种子唯一覆盖 <strong>${(r.coverage*100).toFixed(2)}%</strong><br>${r.charts}个岛 · 复用组${r.reuse_groups} · G=${r.gain.toFixed(4)}</div><div class="scores">代理中位分<br>${scores}<br><span class="warn">${r.recommendation}</span></div>${r.source_coverage_flags?`<p class="note warn">源覆盖诊断：${r.source_coverage_flags}面</p>`:''}<div class="links"><a href="${r.path}/downstream/asset.glb">GLB模型</a><a href="${r.path}/result.json">候选日志</a></div></article>`;}).join('');
const reference=document.getElementById('reference');reference.hidden=mode==='uv';if(mode!=='uv'){const p=mode==='additional'?`additional_views/reference/${model.value}/base/${file}`:`reference/${model.value}/${mode==='normal'?'normal':'base'}/${file}`;reference.innerHTML=`<a href="${p}" target="_blank"><img src="${p}" alt="高模参考"></a><div><h3>高模参考 · 同一相机</h3><p class="note">所有方法使用同一参考、相机与展示比例。<br>点击图片查看原始分辨率。</p></div>`;}}
for(const el of [model,seed,camera])el.addEventListener('change',render);kind.addEventListener('change',()=>{cameras();render();});cameras();render();</script></html>"""
    result = template.replace("__DATA__", payload).replace("__DENSITY__", f"{(ratio - 1) * 100:.2f}")
    (folder / "index.html").write_text(result, encoding="utf-8")
    print(folder / "index.html")


if __name__ == "__main__":
    main()
