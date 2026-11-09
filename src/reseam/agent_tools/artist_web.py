"""Offline artist review desk. Data are escaped by the authoring code."""

PAGE = r'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>ReSeam 外观工作台</title>
<style>
body{font:15px system-ui,"Microsoft YaHei",sans-serif;background:#eef2f6;color:#213344;margin:24px}
h1{margin-bottom:6px}button,select,input,textarea{font:inherit;padding:8px;margin:4px;border:1px solid #b5c7d1;border-radius:6px}
button{background:white;cursor:pointer}button:hover{background:#dceef3}.row{display:flex;gap:16px;flex-wrap:wrap}
.panel{background:white;padding:16px;border-radius:12px;flex:1;min-width:260px}canvas{max-width:100%;width:100%;cursor:crosshair}
.canvas-wrap{overflow:auto;max-height:620px}.canvas-wrap canvas{max-width:none}
textarea{display:block;width:90%;height:66px}.sub{color:#526878}#info{white-space:pre-wrap;max-height:180px;overflow:auto}
.good{background:#d7eee6}.bad{background:#ffe5dc}label{display:inline-block}#zoom{width:220px;height:220px;border:1px solid #bdd0da}
</style><h1>外观工作台</h1><div class="sub">同一个角度看修改前后。点击模型或展开图，定位对应部位与共用伙伴。</div>
<div><label>视角 <select id="camera"></select></label><label>显示 <select id="mode"><option value="after">修改后</option><option value="difference">差异提示</option><option value="before">修改前</option></select></label>
<button id="blink">闪烁对比</button><label>大小 <input id="scale" type="range" min="1" max="2" step="0.25" value="1"></label>
<label><input type="checkbox" id="linked" checked>显示所有共用伙伴</label><label>选取 <select id="selection-mode"><option value="chart">整块</option><option value="face">单个三角面</option></select></label></div>
<div class="row"><section class="panel"><h3>修改前</h3><div class="canvas-wrap"><canvas id="before"></canvas></div></section>
<section class="panel"><h3>修改后 / 差异</h3><div class="canvas-wrap"><canvas id="after"></canvas></div></section>
<section class="panel"><h3>展开图</h3><div class="canvas-wrap"><canvas id="uv"></canvas></div></section></div>
<div class="row" style="margin-top:16px"><section class="panel"><h3>选择与操作</h3><div id="info">点击模型或展开图开始选择</div>
<div><label>保护名称 <input id="lock-name" placeholder="例如：白爪"></label><label><input type="checkbox" id="lock-shape" checked>形状</label><label><input type="checkbox" id="lock-color" checked>颜色</label><label><input type="checkbox" id="lock-position">位置</label><label><input type="checkbox" id="lock-density">清晰度</label><p id="locks" class="sub"></p></div>
<button id="protect">保护选中部位</button><button id="relax">固定边界，局部放松</button><button id="checkpoint">保存当前版本</button>
<p class="sub">Shift＋点击展开图顶点可固定或取消固定，局部放松时保留这些点。</p>
<label>共享组 <select id="groups"></select></label><label>成员 <select id="members"></select></label>
<button id="master">作为颜色主部位</button><button id="release">移出共享组</button>
<p class="sub">操作按钮生成请求，可直接复制给 Agent 执行。</p></section>
<section class="panel"><h3>外观结论</h3><p id="counts"></p><label>记录者 <select id="reviewer"><option value="human">美术本人</option><option value="agent">Agent</option></select></label>
<textarea id="notes" placeholder="例如：已查看正面和背面，白爪与眼嘴没有明显变化。"></textarea>
<button class="good" id="keep">看起来没明显差别，保留</button><button class="bad" id="revert">有明显问题，退回</button>
<div class="sub">数值只作参考。这里只记录实际查看过的视角。</div></section>
<section class="panel" style="flex:0"><h3>局部放大</h3><canvas id="zoom"></canvas><p class="sub">移动鼠标查看当前位置</p></section></div>
<details id="request-panel" class="panel" style="margin-top:16px"><summary>生成的操作请求</summary><p id="request-title"></p><textarea id="request-output" readonly style="height:160px"></textarea><button id="copy-request">复制请求</button><span id="copy-status"></span></details>
<script>const D=__DATA__;const $=id=>document.getElementById(id);let chosen=new Set(),pins=new Set(),timer=null,flip=false;
let visited=new Set(),loaded={},generation=0;for(const f of D.frames){let o=document.createElement('option');o.value=f.name;o.textContent=f.name;$('camera').append(o)}
for(const g of D.groups){let o=document.createElement('option');o.value=g.id;o.textContent=g.id+' ('+g.members.length+' 个部位)';$('groups').append(o)}
function memberOptions(){let g=D.groups.find(g=>g.id===$('groups').value);$('members').replaceChildren();if(g)g.members.forEach((m,i)=>{let o=document.createElement('option');o.value=i;o.textContent='部位 '+(i+1)+' · '+m.length+' 面';$('members').append(o)})}memberOptions();
function frame(){return D.frames.find(f=>f.name===$('camera').value)}function affected(){let ids=new Set(chosen);if($('linked').checked)for(let g of D.groups)if(g.members.some(m=>m.some(f=>chosen.has(f))))g.members.flat().forEach(f=>ids.add(f));return ids}
async function picture(src){let img=new Image();img.src=src;await img.decode();return img}
async function draw(){const ticket=++generation,f=frame();visited.add(f.name);let mode=timer?(flip?'before':'after'):$('mode').value;
let imgs=await Promise.all([picture(f.before),picture(f[mode]),picture(D.uv_image)]);if(ticket!==generation)return;
let ids=affected();['before','after','uv'].forEach((key,i)=>{let c=$(key);c.width=c.height=D.size;c.style.width=(100*Number($('scale').value))+'%';let ctx=c.getContext('2d');ctx.drawImage(imgs[i],0,0,D.size,D.size);loaded[key]=imgs[i];
if(key!=='uv'&&ids.size){let mask=ctx.getImageData(0,0,D.size,D.size);f.face_ids.forEach((face,j)=>{if(ids.has(face)){mask.data[j*4]=Math.round(mask.data[j*4]*.55+110);mask.data[j*4+1]=Math.round(mask.data[j*4+1]*.55+60);mask.data[j*4+2]=Math.round(mask.data[j*4+2]*.55)}});ctx.putImageData(mask,0,0)}
if(key==='uv'){ctx.strokeStyle='#f0472c';ctx.lineWidth=2;for(let id of ids){let t=D.uv[id];ctx.beginPath();t.forEach((p,j)=>{let x=12+p[0]*(D.size-24),y=D.size-12-p[1]*(D.size-24);j?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.closePath();ctx.stroke()}ctx.fillStyle='#0a3475';for(let i of pins){let p=D.uv_vertices[i];ctx.beginPath();ctx.arc(12+p[0]*(D.size-24),D.size-12-p[1]*(D.size-24),4,0,Math.PI*2);ctx.fill()}}});
$('info').textContent='选中 '+chosen.size+' 个三角面；含共用伙伴 '+ids.size+' 个；固定点 '+pins.size+' 个。\n面编号：'+Array.from(chosen).slice(0,50).join(', ')}
function pos(e,c){let r=c.getBoundingClientRect();return [Math.min(D.size-1,Math.max(0,Math.floor((e.clientX-r.left)*D.size/r.width))),Math.min(D.size-1,Math.max(0,Math.floor((e.clientY-r.top)*D.size/r.height)))]}
function expand(){if($('selection-mode').value==='chart'){let labels=new Set(Array.from(chosen).map(i=>D.chart_labels[i]));D.chart_labels.forEach((l,i)=>{if(labels.has(l))chosen.add(i)})}}
for(let key of ['before','after'])$(key).onclick=e=>{let [x,y]=pos(e,$(key)),face=frame().face_ids[y*D.size+x];chosen=new Set(face<0?[]:[face]);expand();draw()};
$('uv').onclick=e=>{let[x,y]=pos(e,$('uv')),p=[(x-12)/(D.size-24),(D.size-12-y)/(D.size-24)];if(e.shiftKey){let nearest=-1,best=12/(D.size-24);D.uv_vertices.forEach((v,i)=>{let d=Math.hypot(v[0]-p[0],v[1]-p[1]);if(d<best){nearest=i;best=d}});if(nearest>=0){pins.has(nearest)?pins.delete(nearest):pins.add(nearest)}draw();return}chosen=new Set();D.uv.forEach((t,i)=>{let cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);let s=t.map((a,j)=>cross(a,t[(j+1)%3],p));if(s.every(v=>v>=-1e-10)||s.every(v=>v<=1e-10))chosen.add(i)});expand();draw()};
for(let key of ['before','after','uv'])$(key).onmousemove=e=>{if(!loaded[key])return;let[x,y]=pos(e,$(key)),c=$('zoom');c.width=c.height=220;let ctx=c.getContext('2d');ctx.imageSmoothingEnabled=false;ctx.drawImage(loaded[key],x-32,y-32,64,64,0,0,220,220)};
function chooseMember(){let g=D.groups.find(g=>g.id===$('groups').value);if(g){chosen=new Set(g.members[Number($('members').value)]);draw()}}
for(let id of ['camera','mode','scale','linked'])$(id).onchange=draw;$('groups').onchange=()=>{memberOptions();chooseMember()};$('members').onchange=chooseMember;
$('blink').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('blink').textContent='闪烁对比'}else{timer=setInterval(()=>{flip=!flip;draw()},650);$('blink').textContent='停止闪烁'}draw()};
function prepareRequest(tool,request){$('request-output').value=JSON.stringify({...D.snapshot,...request},null,2);$('request-title').textContent=tool;$('request-panel').open=true;$('copy-status').textContent='';$('request-panel').scrollIntoView({behavior:'smooth',block:'nearest'})}
$('copy-request').onclick=async()=>{let field=$('request-output');try{await navigator.clipboard.writeText(field.value);$('copy-status').textContent='已复制'}catch(e){field.focus();field.select();$('copy-status').textContent='已选中，请按 Ctrl+C'}};
function selection(){if(!chosen.size){alert('请先选择部位');return null}return Array.from(chosen)}
$('protect').onclick=()=>{let f=selection(),name=$('lock-name').value.trim(),kinds=['shape','color','position','density'].filter(k=>$('lock-'+k).checked);if(!name){alert('请给这处保护起一个名字');return}if(!kinds.length){alert('请选择保护内容');return}if(f)prepareRequest('artist.protect',{name,selected_faces:f,kinds})};
$('relax').onclick=()=>{let f=selection();if(f)prepareRequest('artist.relax',{selected_faces:f,pin_uv_ids:Array.from(pins),pin_boundary:true,iterations:20,strength:.25})};
$('checkpoint').onclick=()=>prepareRequest('artist.checkpoint',{action:'save',files:{bake:D.report.after.bake,comparison:D.comparison_path}});
for(let[action,id]of[['set_master','master'],['release_member','release']])$(id).onclick=()=>{if($('groups').value)prepareRequest('artist.groups',{action,group_id:$('groups').value,member:Number($('members').value)})};
for(let decision of ['keep','revert'])$(decision).onclick=()=>{if(!$('notes').value.trim()){alert('请写一句实际观察结果');return}prepareRequest('artist.review',{comparison:D.comparison_path,decision,reviewer:$('reviewer').value,notes:$('notes').value,reviewed_cameras:Array.from(visited)})};
$('locks').textContent='已有保护：'+((D.locks||[]).map(l=>l.name).join('、')||'无');$('counts').textContent='图块位置组 '+D.report.before_counts.packing_owners+' → '+D.report.after_counts.packing_owners+'；贴图字节 '+D.report.before_texture_bytes+' → '+D.report.after_texture_bytes;draw();
</script></html>'''
