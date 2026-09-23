'use strict';

// Path and edge inspection state. Identifiers remain decimal strings everywhere.
let viewMode = 'neighbors', activePath = [], selectedEdge = null;
let edgeByKey = new Map(), drawnEdges = [];
const edgeKey = edge => `${edge.src}:${edge.dst}`;
const exactMoney = amount => Number(amount).toLocaleString('ru-RU', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
}) + ' ₸';
const displayDate = date => date.slice(0, 10).split('-').reverse().join('.');

function positionFor(node) {
  if (viewMode === 'path') {
    const index = activePath.indexOf(node.gid);
    if (index >= 0) return {x: (index - (activePath.length - 1) / 2) * .7, y: 0};
  }
  return node;
}

function renderPathControls() {
  const controls = $('path-controls');
  controls.hidden = selected === null;
  if (selected === null) return;
  const paths = data.seed_paths[selected] || [];
  const previous = $('path-seed').value;
  $('path-seed').innerHTML = paths.map((path, index) =>
    `<option value="${index}">${path[0]} · ${path.length - 1} пер.</option>`
  ).join('');
  if (viewMode === 'path' && paths[Number(previous)]) $('path-seed').value = previous;
  $('path-seed').disabled = !paths.length;
  $('show-path').disabled = !paths.length;
  $('show-neighbors').disabled = viewMode === 'neighbors';
  $('show-path').setAttribute('aria-pressed', String(viewMode === 'path'));
  $('path-message').textContent = paths.length
    ? `${paths.length} исходных клиентов. По одному кратчайшему пути от каждого, до 4 переходов.`
    : byId.get(selected).is_seed
      ? 'Это исходный seed. Путей от других seed в пределах 4 переходов не найдено.'
      : 'В доступном графе нет направленного пути от seed в пределах 4 переходов.';
}

function showPath() {
  const path = data.seed_paths[selected]?.[Number($('path-seed').value)];
  if (!path) return;
  viewMode = 'path';
  activePath = path;
  clearTransfer();
  renderPathControls();
  const items = [];
  path.forEach((gid, index) => {
    const node = byId.get(gid);
    items.push(`<button class="path-node" data-path-gid="${gid}"><small>${index === 0 ? 'ИСХОДНЫЙ SEED' : index === path.length - 1 ? 'ВЫБРАННЫЙ КЛИЕНТ' : `ПЕРЕХОД ${index}`}</small><strong>${gid}</strong><span style="color:${roles[node.role].color}">${roles[node.role].label}</span></button>`);
    if (index < path.length - 1) {
      const edge = edgeByKey.get(`${gid}:${path[index + 1]}`);
      items.push(`<button class="path-hop" data-transfer="${edgeKey(edge)}" title="Показать все операции по связи"><strong>${exactMoney(edge.sum_kzt)}</strong><span>→</span><small>${edge.n_tx} операций · подробнее</small></button>`);
    }
  });
  $('path-summary').hidden = false;
  $('path-summary').innerHTML = `<div class="panel-title"><h2>Цепочка от seed · ${path.length - 1} перехода</h2><span class="badge">НАПРАВЛЕННЫЙ ПУТЬ</span></div><p class="path-caveat">Суммы на связях указаны за весь период. Порядок операций во времени и прохождение одних и тех же денег по всей цепочке не установлены.</p><div class="path-chain">${items.join('')}</div>`;
  $('path-summary').querySelectorAll('[data-path-gid]').forEach(button =>
    button.addEventListener('click', () => select(button.dataset.pathGid)));
  bindTransfers($('path-summary'));
  showGraph();
}

function showNeighbors() {
  viewMode = 'neighbors';
  activePath = [];
  $('path-summary').hidden = true;
  clearTransfer();
  renderPathControls();
  showGraph();
}

function clearTransfer() {
  selectedEdge = null;
  $('transfer-panel').hidden = true;
}

function bindTransfers(container) {
  container.querySelectorAll('[data-transfer]').forEach(button =>
    button.addEventListener('click', () => showTransfer(button.dataset.transfer)));
}

function showTransfer(key) {
  const edge = edgeByKey.get(key);
  if (!edge) return;
  // A reverse transfer or a sidebar operation may be outside the displayed path.
  // Reveal its endpoints before highlighting it on the graph.
  if (!edges.some(item => edgeKey(item) === key)) {
    if (selected !== edge.src && selected !== edge.dst) select(edge.dst);
    else showNeighbors();
  }
  selectedEdge = key;
  const reverse = edgeByKey.get(`${edge.dst}:${edge.src}`);
  const panel = $('transfer-panel');
  panel.hidden = false;
  panel.innerHTML = `<div class="panel-title"><h2>Переводы по связи</h2><button id="close-transfer" aria-label="Закрыть операции">× Закрыть</button></div>
    <div class="transfer-endpoints"><button data-endpoint="${edge.src}"><small>ОТПРАВИТЕЛЬ</small>${edge.src}</button><span>→</span><button data-endpoint="${edge.dst}"><small>ПОЛУЧАТЕЛЬ</small>${edge.dst}</button></div>
    <div class="transfer-metrics"><div><small>Сумма переводов</small><strong id="transfer-total">${exactMoney(edge.sum_kzt)}</strong></div><div><small>Операции</small><strong id="transfer-count">${edge.n_tx}</strong></div><div><small>Первая дата</small><strong>${displayDate(edge.first_date)}</strong></div><div><small>Последняя дата</small><strong>${displayDate(edge.last_date)}</strong></div></div>
    ${reverse && edge.src !== edge.dst ? `<button class="reverse-transfer" data-transfer="${edgeKey(reverse)}">↶ Обратное направление · ${reverse.n_tx} операций · ${exactMoney(reverse.sum_kzt)}</button>` : ''}
    <p class="path-caveat">Все операции этой направленной связи из исходной выгрузки. Порядок операций внутри одной даты неизвестен.</p>
    <div class="table-scroll"><table class="transactions-table"><thead><tr><th>№ в списке</th><th>Дата</th><th>Сумма, KZT</th></tr></thead><tbody>${edge.transactions.map((tx, index) => `<tr><td>${index + 1}</td><td>${displayDate(tx.date)}</td><td>${exactMoney(tx.sum_kzt)}</td></tr>`).join('')}</tbody></table></div>`;
  panel.querySelectorAll('[data-endpoint]').forEach(button =>
    button.addEventListener('click', () => select(button.dataset.endpoint)));
  $('close-transfer').addEventListener('click', () => { clearTransfer(); draw(); });
  bindTransfers(panel);
  draw();
  panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function edgeCurve(edge) {
  const source = byId.get(edge.src), target = byId.get(edge.dst);
  const a = coordinates(source), b = coordinates(target);
  if (edge.src === edge.dst) {
    // Cubic loop, above the node; direction follows the order of the samples.
    const r = radius(source), start = {x: a.x + r * .7, y: a.y - r * .7};
    const end = {x: a.x - r * .7, y: a.y - r * .7};
    return Array.from({length: 21}, (_, index) => {
      const t = index / 20, u = 1 - t;
      return {x: u ** 3 * start.x + 3 * u * u * t * (a.x + 32) + 3 * u * t * t * (a.x - 32) + t ** 3 * end.x,
        y: u ** 3 * start.y + 3 * u * u * t * (a.y - 38) + 3 * u * t * t * (a.y - 38) + t ** 3 * end.y};
    });
  }
  const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy) || 1;
  const bend = viewMode !== 'path' && edgeByKey.has(`${edge.dst}:${edge.src}`) ? Math.min(28, distance * .2) : 0;
  const control = {x: (a.x + b.x) / 2 - dy / distance * bend, y: (a.y + b.y) / 2 + dx / distance * bend};
  const point = t => ({x: (1-t)**2*a.x + 2*(1-t)*t*control.x + t*t*b.x, y: (1-t)**2*a.y + 2*(1-t)*t*control.y + t*t*b.y});
  const trimStart = Math.min(.2, (radius(source) + 1) / distance), trimEnd = Math.min(.2, (radius(target) + 3) / distance);
  return Array.from({length: 17}, (_, index) => point(trimStart + (1 - trimStart - trimEnd) * index / 16));
}

function drawEdges() {
  drawnEdges = [];
  // Selected edge is painted last so its direction remains visible.
  const ordered = [...edges].sort((a, b) => Number(edgeKey(a) === selectedEdge) - Number(edgeKey(b) === selectedEdge));
  for (const edge of ordered) {
    const key = edgeKey(edge), points = edgeCurve(edge);
    const chosen = key === selectedEdge, onPath = viewMode === 'path';
    const neighbor = selected !== null && (edge.src === selected || edge.dst === selected);
    ctx.strokeStyle = chosen ? '#ffc578' : onPath ? '#b5ef79' : neighbor ? '#7e979799' : '#536e7955';
    ctx.lineWidth = chosen ? 3.5 : onPath ? 2.5 : neighbor ? 1.3 : .65;
    ctx.beginPath();
    points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
    ctx.stroke();
    const end = points[points.length - 1], previous = points[points.length - 2];
    const angle = Math.atan2(end.y - previous.y, end.x - previous.x), size = onPath || chosen ? 8 : neighbor ? 5 : 3;
    ctx.fillStyle = chosen ? '#ffc578' : onPath ? '#b5ef79' : '#91a6ad';
    ctx.beginPath();ctx.moveTo(end.x, end.y);
    ctx.lineTo(end.x - Math.cos(angle - .45) * size, end.y - Math.sin(angle - .45) * size);
    ctx.lineTo(end.x - Math.cos(angle + .45) * size, end.y - Math.sin(angle + .45) * size);
    ctx.closePath();ctx.fill();
    drawnEdges.push({key, points});
    if (onPath) {
      const mid = points[Math.floor(points.length / 2)];
      ctx.font = '10px system-ui';ctx.textAlign = 'center';ctx.fillStyle = '#d3eabf';
      ctx.fillText(money(edge.sum_kzt), mid.x, mid.y - 12);ctx.textAlign = 'left';
    }
  }
}

function edgeAt(x, y) {
  let best = null, distance = 7;
  for (const item of drawnEdges) {
    for (let i = 1; i < item.points.length; i++) {
      const a = item.points[i - 1], b = item.points[i], dx = b.x - a.x, dy = b.y - a.y;
      const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      const d = Math.hypot(x - a.x - t * dx, y - a.y - t * dy);
      if (d < distance) {best = item.key; distance = d;}
    }
  }
  return best;
}

document.getElementById('show-path').addEventListener('click', showPath);
document.getElementById('show-neighbors').addEventListener('click', showNeighbors);
document.getElementById('path-seed').addEventListener('change', () => { if (viewMode === 'path') showPath(); });
