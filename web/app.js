'use strict';
const roles = {
  consolidator: {label: 'Консолидация', color: '#e7b971'},
  transit: {label: 'Транзит', color: '#68bce0'},
  distributor: {label: 'Распределение', color: '#b5ef79'},
  terminal: {label: 'Конечный получатель', color: '#dc8baf'},
  coordinator: {label: 'Координация', color: '#ad99ec'},
  peripheral: {label: 'Периферия', color: '#637785'},
};
const $ = id => document.getElementById(id);
const number = n => Number(n).toLocaleString('ru-RU', {maximumFractionDigits: 0});
const money = n => number(n) + ' ₸';
const escapeHTML = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data, byId, selected = null, cluster = '', nodes = [], edges = [], width = 1, height = 1;
let scale = 1, panX = 0, panY = 0, drag = null;
const canvas = $('graph'), ctx = canvas.getContext('2d');
const coordinates = n => ({x: width / 2 + positionFor(n).x * Math.min(width, height) * .44 * scale + panX, y: height / 2 + positionFor(n).y * Math.min(width, height) * .44 * scale + panY});
function stat(label, value, note) { return `<div class="stat"><label>${label}</label><strong>${value}</strong><small>${note}</small></div>`; }
function showGraph() {
  nodes = data.nodes.filter(n => cluster === '' || n.cluster_id === Number(cluster));
  if (viewMode === 'path') {
    nodes = activePath.map(gid => byId.get(gid));
  } else if (selected !== null) {
    const neighbors = new Set([selected]);
    data.edges.forEach(e => { if (e.src === selected || e.dst === selected) {neighbors.add(e.src); neighbors.add(e.dst);} });
    // Include every direct counterparty, even across cluster boundaries.
    nodes = data.nodes.filter(n => neighbors.has(n.gid));
  }
  const ids = new Set(nodes.map(n => n.gid));
  edges = viewMode === 'path'
    ? activePath.slice(1).map((gid, index) => edgeByKey.get(`${activePath[index]}:${gid}`))
    : data.edges.filter(e => ids.has(e.src) && ids.has(e.dst));
  $('graph-count').textContent = `${number(nodes.length)} узлов · ${number(edges.length)} связей`;
  fit();
}
function fit() {
  if (!nodes.length) return;
  const xs = nodes.map(n => positionFor(n).x), ys = nodes.map(n => positionFor(n).y);
  const loX = Math.min(...xs), hiX = Math.max(...xs), loY = Math.min(...ys), hiY = Math.max(...ys);
  const base = Math.min(width, height) * .44;
  scale = Math.min(20, (width - 90) / (Math.max(.08, hiX - loX) * base), (height - 90) / (Math.max(.08, hiY - loY) * base));
  panX = -(loX + hiX) / 2 * base * scale;
  panY = -(loY + hiY) / 2 * base * scale;
  draw();
}
function radius(n) {return selected === n.gid ? 9 : 2.5 + 4 * n.priority_score;}
function draw() {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#27323a';
  for (let x = 12; x < width; x += 24) for (let y = 12; y < height; y += 24) {ctx.beginPath();ctx.arc(x,y,.6,0,Math.PI*2);ctx.fill();}
  drawEdges();
  for (const n of nodes) {
    const p=coordinates(n), r=radius(n);
    if(p.x < -20 || p.x > width+20 || p.y < -20 || p.y > height+20) continue;
    if(n.gid === selected){ctx.beginPath();ctx.arc(p.x,p.y,r+7,0,Math.PI*2);ctx.fillStyle=roles[n.role].color+'22';ctx.fill();}
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=roles[n.role].color;ctx.fill();
    if(n.is_seed || n.truncated_by_depth){ctx.setLineDash(n.truncated_by_depth?[2,2]:[]);ctx.strokeStyle=n.is_seed?'#eef2d5':'#96a6b0';ctx.lineWidth=1;ctx.beginPath();ctx.arc(p.x,p.y,r+2,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);}
    if(viewMode === 'path'){
      ctx.textAlign='center';ctx.fillStyle='#d7e3e9';ctx.font='10px system-ui';
      ctx.fillText('…'+n.gid.slice(-6),p.x,p.y+r+18);
      ctx.fillStyle='#b5ef79';ctx.fillText(n.gid===activePath[0]?'SEED':n.gid===selected?'ЦЕЛЬ':'',p.x,p.y-r-12);
      ctx.textAlign='left';
    }else if(nodes.length < 65 || n.gid === selected){ctx.fillStyle='#d7e3e9';ctx.font='10px system-ui';ctx.fillText(String(n.gid),p.x+r+5,p.y+3);}
  }
}
function select(gid) {
  const n=byId.get(String(gid));if(!n)return;
  selected=n.gid;$('search-message').textContent='';
  viewMode='neighbors';activePath=[];clearTransfer();$('path-summary').hidden=true;
  renderPathControls();
  document.querySelectorAll('.rank-item').forEach(el=>el.classList.toggle('selected',el.dataset.gid===selected));
  const connections=data.edges.filter(e=>e.src===selected||e.dst===selected).sort((a,b)=>b.sum_kzt-a.sum_kzt);
  const topInfo=data.top.find(t=>t.gid===selected);
  $('detail').innerHTML=`<div class="detail-eyebrow">КАРТОЧКА УЗЛА ${n.is_seed?'· SEED':''}</div><h3>Клиент ${n.gid}</h3><span class="role-pill"><i class="dot" style="background:${roles[n.role].color}"></i>${roles[n.role].label}</span><div class="detail-metrics"><div><small>Приоритет</small><strong>${n.priority_score.toFixed(3)}</strong></div><div><small>Сила признаков роли</small><strong>${Number(n.role_score).toFixed(2)}</strong></div><div><small>Входящий поток</small><strong>${money(n.in_kzt)}</strong></div><div><small>Исходящий поток</small><strong>${money(n.out_kzt)}</strong></div></div><div class="detail-label">ПРАВИЛО РОЛИ</div><p>${escapeHTML(n.role_rule)}</p><div class="detail-label">ОСНОВАНИЕ ГИПОТЕЗЫ</div><p>${escapeHTML(n.evidence)}</p>${n.truncated_by_depth?'<p class="detail-warning">Исходящие неизвестны: граница выгрузки. Запросите следующее колено и более полный период операций.</p>':''}<div class="detail-label">ПОЗИЦИЯ В СЕТИ</div><p>Колено ${n.depth} · кластер ${n.cluster_id}<br>Достижим от ${n.seed_reach} seed по путям до 4 переходов.<br>Контрагентов: ${n.in_deg} входящих / ${n.out_deg} исходящих.</p><div class="detail-label">ПОЧЕМУ ТАКОЙ ПРИОРИТЕТ</div><p>${escapeHTML(n.priority_why || topInfo?.why || n.evidence)}</p><div class="detail-label">КРУПНЕЙШИЕ СВЯЗИ · ${connections.length}</div><div class="connections">${connections.slice(0,8).map(e=>`<div class="connection-row"><button data-gid="${e.src===selected?e.dst:e.src}">${e.src===selected?'→':'←'} ${e.src===selected?e.dst:e.src}<span>${money(e.sum_kzt)}</span></button><button class="inspect-transfer" data-transfer="${edgeKey(e)}" title="Показать отдельные переводы">Операции</button></div>`).join('')||'<p>В выгрузке нет связей.</p>'}</div><p class="section-note" style="padding:16px 0 0">Оценки — эвристики по наблюдаемой сети, не вероятность нарушения.</p>`;
  $('detail').querySelectorAll('[data-gid]').forEach(b=>b.addEventListener('click',()=>select(b.dataset.gid)));bindTransfers($('detail'));showGraph();
}
function resetSelection(){selected=null;viewMode='neighbors';activePath=[];clearTransfer();$('path-summary').hidden=true;renderPathControls();$('detail').innerHTML='<div class="empty">Выберите узел на графе или в списке приоритетов</div>';document.querySelectorAll('.rank-item').forEach(el=>el.classList.remove('selected'));}
function setCluster(value){cluster=String(value);$('cluster').value=cluster;resetSelection();showGraph();}
$('search').addEventListener('submit',e=>{e.preventDefault();const value=$('gid').value.trim();if(!/^\d+$/.test(value)||!byId?.has(value)){$('search-message').textContent='Такой gid не найден в выгрузке.';return;}select(value);});
$('cluster').addEventListener('change',e=>setCluster(e.target.value));
$('reset').addEventListener('click',()=>{setCluster('');$('gid').value='';$('search-message').textContent='';});
function zoom(factor,x=width/2,y=height/2){const next=Math.max(.2,Math.min(100,scale*factor)),ratio=next/scale;panX=(panX+width/2-x)*ratio+x-width/2;panY=(panY+height/2-y)*ratio+y-height/2;scale=next;draw();}
$('zoom-in').addEventListener('click',()=>zoom(1.3));$('zoom-out').addEventListener('click',()=>zoom(1/1.3));
canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.001),e.clientX-r.left,e.clientY-r.top);},{passive:false});
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing';});
canvas.addEventListener('pointermove',e=>{if(!drag)return;if(Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>4)drag.moved=true;panX+=e.clientX-drag.x;panY+=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;draw();});
canvas.addEventListener('pointerup',e=>{if(!drag)return;const moved=drag.moved;drag=null;canvas.style.cursor='grab';if(moved)return;const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const hit=nodes.map(n=>({n,d:Math.hypot(coordinates(n).x-x,coordinates(n).y-y)})).filter(v=>v.d<radius(v.n)+5).sort((a,b)=>a.d-b.d)[0];if(hit)select(hit.n.gid);else{const edge=edgeAt(x,y);if(edge)showTransfer(edge);}});
canvas.addEventListener('pointercancel',()=>{drag=null;canvas.style.cursor='grab';});
new ResizeObserver(()=>{width=canvas.clientWidth;height=canvas.clientHeight;const dpr=window.devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);if(data)fit();}).observe(canvas);
async function init(){
  const response=await fetch('/api/graph');if(!response.ok)throw new Error(`HTTP ${response.status}`);data=await response.json();edgeByKey=new Map(data.edges.map(e=>[edgeKey(e),e]));byId=new Map(data.nodes.map(n=>[n.gid,n]));const m=data.meta;
  $('period').textContent=`${m.period_start} — ${m.period_end}`;
  $('stats').innerHTML=stat('Участники сети',number(m.n_nodes),`${m.n_seed} исходных клиентов · seed`)+stat('Денежные связи',number(m.n_edges),`${number(m.n_transactions)} транзакций`)+stat('Наблюдаемый оборот',(m.total_kzt/1e6).toLocaleString('ru-RU',{maximumFractionDigits:2})+' млн ₸','Сумма переводов, не уникальные деньги')+stat('Кластеры',number(m.n_clusters),`${m.n_components} компонент с изолятами`)+stat('Граница наблюдения',number(m.n_truncated),'Узлов с неизвестными исходящими');
  $('ranking').innerHTML=data.top.map(t=>{const n=byId.get(t.gid);return `<button class="rank-item" data-gid="${n.gid}"><span class="rank-number">${String(t.rank).padStart(2,'0')}</span><span class="rank-info"><strong>Клиент ${n.gid}</strong><small style="color:${roles[n.role].color}">${roles[n.role].label}</small></span><span class="rank-score">${n.priority_score.toFixed(2)}<span class="score-bar"><span style="width:${n.priority_score*100}%"></span></span></span></button>`;}).join('');
  $('ranking').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>select(b.dataset.gid)));
  $('legend').innerHTML=Object.values(roles).map(r=>`<span><i class="dot" style="background:${r.color}"></i>${r.label}</span>`).join('')+'<span>○ Seed · пунктир: граница</span>';
  $('cluster').innerHTML+=[...data.clusters].map(c=>`<option value="${c.cluster_id}">Кластер ${c.cluster_id} · ${c.n_nodes}</option>`).join('');
  $('clusters').innerHTML=data.clusters.map(c=>`<tr tabindex="0" data-cluster="${c.cluster_id}" aria-label="Открыть кластер ${c.cluster_id}"><td><span class="badge">${String(c.cluster_id).padStart(2,'0')}</span></td><td>${c.n_nodes}</td><td>${c.n_seed}</td><td>${money(c.sum_kzt_internal)}</td><td>${escapeHTML(c.hypothesis)}</td></tr>`).join('');
  $('clusters').querySelectorAll('tr').forEach(row=>{const open=()=>{setCluster(row.dataset.cluster);document.querySelector('.graph-panel').scrollIntoView({behavior:'smooth',block:'center'});};row.addEventListener('click',open);row.addEventListener('keydown',e=>{if(e.key==='Enter')open();});});
  $('runtime').textContent=`Расчёт: ${m.elapsed_seconds} с · Изолированных узлов: ${m.n_isolates} · Все данные локально`;
  showGraph();
}
init().catch(error=>{$('error').hidden=false;$('error').textContent='Не удалось загрузить результаты: '+error.message;});
