"""Run and visualize the aligned dragon's approximate vertex-to-surface mirror matches."""

import argparse
import importlib.metadata
import json
import time
from pathlib import Path

import numpy as np

from reseam.mesh import load_mesh, sha256
from reseam.pipeline import write_json
from reseam.surface_match import fit_offset, match_surfaces


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", type=Path, default=Path("assets/v1/dragon/low.obj"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    mesh = load_mesh(args.mesh)
    input_hash = sha256(args.mesh)
    normal = np.asarray([1.0, 0, 0])
    offset, before, after = fit_offset(mesh, normal)
    matches = match_surfaces(mesh, normal, offset)
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    wing = np.abs(mesh.vertices[matches.source_ids, 0] - offset) >= 0.2 * np.ptp(mesh.vertices[:, 0])
    records = []
    for i, source_id in enumerate(matches.source_ids):
        records.append(
            {
                "source_vertex": int(source_id),
                "target_face": int(matches.target_faces[i]),
                "target_vertices": mesh.faces[matches.target_faces[i]].tolist(),
                "target_barycentric": matches.barycentric[i].tolist(),
                "target_point": matches.target_points[i].tolist(),
                "nearest_target_vertex": int(matches.nearest_vertex_ids[i]),
                "surface_error_fraction": float(matches.surface_error[i]),
                "vertex_error_fraction": float(matches.vertex_error[i]),
                "cycle_error_fraction": float(matches.cycle_error[i]),
                "normal_angle_degrees": float(matches.normal_angle_degrees[i]),
                "side": int(matches.side[i]),
                "outer_wing_candidate": bool(wing[i]),
            }
        )
    sweep = []
    for tolerance in (0.0025, 0.005, 0.01, 0.02):
        valid = (
            (matches.surface_error <= tolerance)
            & (matches.cycle_error <= tolerance)
            & (matches.normal_angle_degrees <= 45)
        )
        sweep.append(
            {
                "tolerance_fraction": tolerance,
                "all_accepted": int(valid.sum()),
                "outer_accepted": int(np.sum(valid & wing)),
                "outer_total": int(wing.sum()),
                "left_outer_accepted": int(np.sum(valid & wing & (matches.side < 0))),
                "right_outer_accepted": int(np.sum(valid & wing & (matches.side > 0))),
                "surface_interior_or_edge_matches": int(
                    np.sum(valid & wing & (matches.barycentric.max(axis=1) < 1 - 1e-7))
                ),
            }
        )
    data = {
        "model": "dragon",
        "mesh": str(args.mesh),
        "input_sha256": input_hash,
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
        "matches": records,
        "plane": {
            "normal": normal.tolist(),
            "offset": offset,
            "before_fit_objective": before,
            "after_fit_objective": after,
        },
        "diagonal": diagonal,
        "normal_angle_limit": 45,
        "sweep": sweep,
        "all_source_vertices": len(mesh.vertices),
        "queried_source_vertices": len(matches.source_ids),
        "outer_surface_error_median": float(np.median(matches.surface_error[wing])),
        "outer_surface_error_p95": float(np.quantile(matches.surface_error[wing], 0.95)),
        "outer_vertex_error_median": float(np.median(matches.vertex_error[wing])),
        "outer_cycle_error_median": float(np.median(matches.cycle_error[wing])),
        "scope": "Geometry candidates only; directed matches, not a bijection, UV, material, or semantic approval",
        "roi": "abs(x-plane_offset) >= 0.20 * mesh_x_span; geometric outer-wing proxy, not confirmed semantic labels",
        "packages": {p: importlib.metadata.version(p) for p in ("numpy", "scipy", "trimesh")},
        "code_sha256": {
            p: sha256(Path(p)) for p in ("src/reseam/surface_match.py", "scripts/inspect_surface_symmetry.py")
        },
        "input_unchanged": sha256(args.mesh) == input_hash,
    }
    assert np.allclose(matches.barycentric.sum(axis=1), 1, atol=1e-8)
    assert matches.barycentric.min() >= -1e-8
    assert np.allclose(
        np.einsum("fc,fcd->fd", matches.barycentric, mesh.vertices[mesh.faces[matches.target_faces]]),
        matches.target_points,
        atol=1e-8,
    )
    page = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    (args.output / "index.html").write_text(page, encoding="utf-8")
    data["wall_seconds"] = time.monotonic() - started
    write_json(args.output / "matches.json", data)
    compact = {k: v for k, v in data.items() if k not in ("vertices", "faces", "matches")}
    write_json(args.output / "summary.json", compact)
    print(json.dumps(compact, ensure_ascii=False, indent=2))


TEMPLATE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>龙翼近似对称点 · ReSeam</title><style>
body{margin:0;background:#eeeae0;color:#18322d;font:15px/1.6 'Microsoft YaHei',sans-serif}main{max-width:1400px;margin:auto;padding:24px}h1{font-size:28px;margin:0 0 10px}.layout{display:grid;grid-template-columns:3fr 1fr;gap:20px}canvas{width:100%;height:620px;background:#182628;touch-action:none;cursor:grab}aside{background:#fffdf7;padding:18px}button,select,input{font:inherit}button{padding:5px 12px;background:#fffdf7;border:1px solid #9cab9d;color:#18322d}#stats{font-size:19px;margin:15px 0}#details{white-space:pre-wrap;font:14px/1.8 monospace}.controls{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:16px 0}.note{font-size:13px;color:#536961}a{color:#276d63}@media(max-width:850px){.layout{grid-template-columns:1fr}canvas{height:480px}}</style><main><h1>左右三角形不同，也能找到对应点</h1><p>橙点：原始顶点。蓝点：另一侧曲面上的对应位置，可能落在三角形内部。编号相同为一组；这里只验证几何对应候选，没有修改模型或 UV。</p>
<div class="controls"><label>位置与往返误差上限 <select id="tolerance"><option value="0.0025">模型对角线的 0.25%</option><option value="0.005" selected>模型对角线的 0.5%</option><option value="0.01">模型对角线的 1%</option><option value="0.02">模型对角线的 2%</option></select></label><label><input id="wing" type="checkbox" checked>只看外侧翼区候选</label><button id="front">前视</button><button id="oblique">斜视</button><button id="side">侧视</button></div>
<div id="stats"></div><div class="layout"><div><canvas id="view"></canvas><p class="note">拖动旋转，滚轮缩放，点击点查看原始 ID 和重心坐标。为便于观察，只展示右→左方向中空间分散的最多 14 组；统计包含两个方向，不能当作独立的一一配对数量。</p></div><aside><h2>对应点详情</h2><div id="details"></div><p><a href="matches.json">下载全部顶点—曲面对应</a></p><p class="note">法线夹角 ≤45°。外侧区域按 |x−对称平面| ≥ 横向总跨度的 20% 选择，是几何启发式范围，尚未人工确认语义。最近点与往返检查不能保证全局双射或排除全部语义错配。</p></aside></div></main><script>
const data=__DATA__, canvas=document.querySelector('#view'),ctx=canvas.getContext('2d');let yaw=0,pitch=0,zoom=1,selected=0,points=[],screen=[],drag=null,moved=false;
const verts=data.vertices,center=[0,1,2].map(k=>(Math.max(...verts.map(v=>v[k]))+Math.min(...verts.map(v=>v[k])))/2);
function project(v){let x=v[0]-center[0],y=v[1]-center[1],z=v[2]-center[2];let a=x*Math.cos(yaw)+z*Math.sin(yaw),b=-x*Math.sin(yaw)+z*Math.cos(yaw),c=y*Math.cos(pitch)-b*Math.sin(pitch),depth=y*Math.sin(pitch)+b*Math.cos(pitch);let scale=Math.min(canvas.width,canvas.height)*.95/data.diagonal*zoom;return[canvas.width/2+a*scale,canvas.height/2-c*scale,depth]}
function update(){const t=+document.querySelector('#tolerance').value,w=document.querySelector('#wing').checked;const population=data.matches.filter(p=>!w||p.outer_wing_candidate),valid=population.filter(p=>p.surface_error_fraction<=t&&p.cycle_error_fraction<=t&&p.normal_angle_degrees<=45);document.querySelector('#stats').textContent=`${valid.length} / ${population.length} 个顶点通过当前几何门限（${(100*valid.length/population.length).toFixed(1)}%）`;let available=valid.filter(p=>p.side>0);points=[];if(available.length){points=[available[0]];while(points.length<Math.min(14,available.length)){let next=null,best=-1;for(const p of available){if(points.includes(p))continue;const v=verts[p.source_vertex];const d=Math.min(...points.map(q=>verts[q.source_vertex].reduce((s,x,k)=>s+(x-v[k])**2,0)));if(d>best){best=d;next=p}}if(!next)break;points.push(next)}}selected=0;draw()}
function draw(){canvas.width=Math.round(canvas.clientWidth*devicePixelRatio);canvas.height=Math.round(canvas.clientHeight*devicePixelRatio);ctx.clearRect(0,0,canvas.width,canvas.height);const pv=verts.map(project);const ordered=data.faces.map(f=>({f,z:f.reduce((s,i)=>s+pv[i][2],0)/3})).sort((a,b)=>a.z-b.z);for(const{f}of ordered){ctx.beginPath();f.forEach((id,k)=>k?ctx.lineTo(pv[id][0],pv[id][1]):ctx.moveTo(pv[id][0],pv[id][1]));ctx.closePath();ctx.fillStyle='#324848';ctx.fill();ctx.strokeStyle='#627571';ctx.lineWidth=.35*devicePixelRatio;ctx.stroke()}screen=[];for(let i=0;i<points.length;i++){const p=points[i],a=project(verts[p.source_vertex]),b=project(p.target_point),active=i===selected;ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.strokeStyle=active?'#f4dd9d':'#91afa566';ctx.lineWidth=(active?2:1)*devicePixelRatio;ctx.stroke();for(const[v,color]of[[a,'#ff9c52'],[b,'#55d4eb']]){ctx.beginPath();ctx.arc(v[0],v[1],(active?6:4)*devicePixelRatio,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();ctx.font=`${12*devicePixelRatio}px sans-serif`;ctx.fillText(String(i+1),v[0]+7*devicePixelRatio,v[1]-5*devicePixelRatio);screen.push({x:v[0],y:v[1],i})}}const p=points[selected];document.querySelector('#details').textContent=p?`组 ${selected+1}\n源顶点：${p.source_vertex}\n目标三角形：${p.target_face}\n目标顶点：${p.target_vertices.join(', ')}\n重心权重：${p.target_barycentric.map(x=>x.toFixed(4)).join(', ')}\n\n曲面误差：${(p.surface_error_fraction*100).toFixed(4)}% D\n最近顶点误差：${(p.vertex_error_fraction*100).toFixed(4)}% D\n往返误差：${(p.cycle_error_fraction*100).toFixed(4)}% D\n法线夹角：${p.normal_angle_degrees.toFixed(2)}°\n\nD = 模型包围盒对角线\n所有 ID 从 0 开始。`:'当前门限无可显示对应点';}
canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];moved=false;canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag[0],dy=e.clientY-drag[1];if(Math.abs(dx)+Math.abs(dy)>1)moved=true;yaw+=dx*.006;pitch+=dy*.006;drag=[e.clientX,e.clientY];draw()};canvas.onpointerup=e=>{drag=null;if(!moved){const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*devicePixelRatio,y=(e.clientY-r.top)*devicePixelRatio;const hit=screen.map(p=>({...p,d:(p.x-x)**2+(p.y-y)**2})).sort((a,b)=>a.d-b.d)[0];if(hit&&hit.d<(25*devicePixelRatio)**2){selected=hit.i;draw()}}};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.3,Math.min(4,zoom*Math.exp(-e.deltaY*.001)));draw()};
document.querySelector('#tolerance').onchange=update;document.querySelector('#wing').onchange=update;document.querySelector('#front').onclick=()=>{yaw=0;pitch=0;draw()};document.querySelector('#oblique').onclick=()=>{yaw=.6;pitch=-.3;draw()};document.querySelector('#side').onclick=()=>{yaw=Math.PI/2;pitch=0;draw()};window.onresize=draw;update();
</script></html>"""


if __name__ == "__main__":
    main()
