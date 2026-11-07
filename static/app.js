const $ = id => document.getElementById(id);
let selectedId = null, devices = [], history = [], loading = false, historyRequest = 0, loadPromise = null, historyError = null, historyLoading = false;
let historyRange = '', historyWindow = null, rangeSummary = null;
let comparisonIds = new Set(), comparisonData = null, comparisonRequest = 0, comparisonChoicesKey = '';
const CHART_GAP_MS = 300000;
function chartTime(row) { return typeof row.checked_at === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(row.checked_at) ? Date.parse(row.checked_at) : NaN; }
function timeBounds(rows, window = null) {
  if (window) return [Date.parse(window.start), Date.parse(window.end)];
  const times = rows.map(chartTime).filter(Number.isFinite);
  return times.length ? [Math.min(...times), Math.max(...times)] : [0, 0];
}
function timeFraction(row, bounds) { const time = chartTime(row); return !Number.isFinite(time) ? null : bounds[0] === bounds[1] ? .5 : (time-bounds[0])/(bounds[1]-bounds[0]); }
const fmt = n => Number.isFinite(n) ? String(Number(n.toFixed(3))) : 'N/A';
const latencyText = t => Number.isFinite(t.latency_ms) ? `${fmt(t.latency_ms)} ms` : Number.isFinite(t.latency_upper_bound_ms) ? `<${fmt(t.latency_upper_bound_ms)} ms` : 'N/A';
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json'}, signal: options.signal || AbortSignal.timeout(120000)});
  if (!response.ok) { let error = {}; try { error = await response.json(); } catch {} const problem = new Error(error.error || `Request failed (${response.status})`); problem.status = response.status; throw problem; }
  return response.status === 204 ? null : response.json();
}
function element(tag, className, content) { const n = document.createElement(tag); n.className = className || ''; if (content !== undefined) n.textContent = String(content); return n; }
function failure(error) { $('global-error').hidden = false; $('global-error').textContent = `${error.message}. Previously loaded results may be stale; retry when the issue is resolved.`; }
function result(label, ok, yes, no, pending = 'Awaiting check') { const n = element('div', 'result'); n.append(element('span', '', label), element('span', `chip ${ok == null ? '' : ok ? 'good' : 'bad'}`, ok == null ? pending : ok ? `● ${yes}` : `● ${no}`)); return n; }
function observationLabel(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? date.toLocaleString(undefined, {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'}) : 'Unknown time';
}
function availabilityMetric(label, metric) {
  const section = element('div', 'availability-metric');
  section.append(element('span', 'availability-label', label));
  const valid = metric?.valid_checks || 0, successful = metric?.successful_checks || 0;
  section.append(element('strong', '', `${Number.isFinite(metric?.percentage) ? fmt(metric.percentage) + '%' : 'N/A'} · ${successful}/${valid} valid checks`));
  section.append(element('small', '', metric?.window_start && metric?.window_end ? `Observation window: ${observationLabel(metric.window_start)} → ${observationLabel(metric.window_end)}` : 'Observation window: no qualifying checks'));
  section.append(element('small', '', `${metric?.excluded_checks || 0} excluded records · latest 60 stored cycles`));
  return section;
}
let sortKey = 'name', sortDirection = 1;
const sortColumns = {name:'Device', ip:'IPv4', icmp:'ICMP', tcp:'TCP / port', rtt:'RTT', loss:'Loss', checked:'Last checked'};
function checkStatus(t, protocol) {
  const flag = protocol === 'icmp' ? t.ping_ok : t.tcp_ok;
  return t[protocol + '_check_status'] === 'unknown' || flag == null ? null : flag;
}
function sortValue(t, key) {
  if (key === 'name') return t.name.toLocaleLowerCase();
  if (key === 'ip') return t.ip.split('.').reduce((n, part) => n * 256 + Number(part), 0);
  if (key === 'icmp' || key === 'tcp') return checkStatus(t, key);
  // Bounds are not exact RTT values: keep them with unavailable numeric samples.
  if (key === 'rtt') return Number.isFinite(t.latency_ms) ? t.latency_ms : null;
  if (key === 'loss') return Number.isFinite(t.packet_loss_pct) ? t.packet_loss_pct : null;
  const time = t.checked_at ? new Date(t.checked_at).getTime() : NaN;
  return Number.isFinite(time) ? time : null;
}
function sortedDevices() {
  return [...devices].sort((a, b) => {
    const x = sortValue(a, sortKey), y = sortValue(b, sortKey);
    if (x == null || y == null) return x == null && y == null ? a.id - b.id : x == null ? 1 : -1;
    const order = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return order * sortDirection || a.id - b.id;
  });
}
function sortDevices(key) {
  if (!(key in sortColumns)) return;
  sortDirection = sortKey === key ? -sortDirection : 1;
  sortKey = key;
  render();
}
function tableEmpty(message) {
  const row = element('tr'), cell = element('td', 'empty', message);
  cell.setAttribute('colspan', '8'); row.append(cell);
  $('targets').replaceChildren(row);
}
function render() {
  $('total').textContent = devices.length; $('device-count').textContent = devices.length;
  $('reachable').textContent = devices.filter(t => t.ping_ok === 1 && t.icmp_check_status !== 'unknown').length;
  $('services').textContent = devices.filter(t => t.tcp_ok === 1 && t.tcp_check_status !== 'unknown').length;
  const values = devices.filter(t => t.ping_ok === 1 && Number.isFinite(t.latency_ms)).map(t => t.latency_ms);
  $('latency').textContent = values.length ? fmt(values.reduce((a,b) => a+b, 0) / values.length) : 'N/A';
  for (const [key,label] of Object.entries(sortColumns)) {
    const active = key === sortKey;
    $('sort-heading-' + key).setAttribute('aria-sort', active ? sortDirection === 1 ? 'ascending' : 'descending' : 'none');
    $('sort-' + key).textContent = label + (active ? sortDirection === 1 ? ' ↑' : ' ↓' : '');
  }
  $('targets').replaceChildren();
  if (!devices.length) tableEmpty('No devices yet. Add an authorized local device to start monitoring.');
  for (const t of sortedDevices()) {
    const row = element('tr', `target ${t.id === selectedId ? 'selected' : ''}`);
    const device = element('td', 'device-name'), select = element('button', 'device-select', t.name);
    select.setAttribute('aria-pressed', String(t.id === selectedId));
    select.setAttribute('aria-controls', 'diagnostics packet-loss-history availability-history');
    select.onclick = () => selectHistory(t.id); device.append(select);
    const icmp = checkStatus(t, 'icmp'), tcp = checkStatus(t, 'tcp');
    const status = (value, yes, no) => element('td', `mono status ${value == null ? 'unknown' : value ? 'good' : 'bad'}`, value == null ? t.checked_at ? 'Unknown' : 'Pending' : value ? yes : no);
    const pingCell = status(icmp, 'Responding', 'No response');
    const tcpCell = status(tcp, `Open / ${t.port}`, `${t.tcp_error?.startsWith('Connection refused') ? 'Refused' : t.tcp_error?.startsWith('Connection timed out') ? 'Timeout' : 'Failed'} / ${t.port}`);
    if (t.ping_error) pingCell.setAttribute('title', 'ICMP: ' + t.ping_error);
    if (t.tcp_error) tcpCell.setAttribute('title', `TCP / ${t.port}: ${t.tcp_error}`);
    const loss = Number.isFinite(t.packet_loss_pct) ? `${fmt(t.packet_loss_pct)}%` : 'N/A';
    const lossCell = element('td', 'mono numeric', loss);
    if (Number.isFinite(t.packet_loss_pct)) lossCell.setAttribute('title', `${t.icmp_received}/${t.icmp_sent} Echo Replies`);
    const stale = t.checked_at && Date.now() - new Date(t.checked_at).getTime() > (t.stale_after_seconds || 90) * 1000;
    const checked = element('td', `mono checked ${stale ? 'stale' : ''}`, t.checked_at ? `${stale ? 'Stale · ' : ''}${new Date(t.checked_at).toLocaleTimeString()}` : 'Not checked');
    if (t.checked_at) checked.setAttribute('title', observationLabel(t.checked_at));
    const actions = element('td', 'actions');
    const view = element('button', 'secondary', 'View history');
    view.setAttribute('aria-pressed', String(t.id === selectedId)); view.setAttribute('aria-label', `View history for ${t.name}`);
    view.setAttribute('aria-controls', 'diagnostics packet-loss-history availability-history'); view.onclick = () => selectHistory(t.id);
    const remove = element('button', 'secondary remove', 'Remove'); remove.setAttribute('aria-label', `Remove ${t.name}`);
    remove.onclick = async () => { if (!confirm(`Remove ${t.name} and its saved history?`)) return; remove.disabled = true; try { await api(`/api/targets/${t.id}`, {method:'DELETE'}); await load(true); } catch (e) { failure(e); } finally { remove.disabled = false; } };
    actions.append(view, remove);
    row.append(device, element('td', 'mono', t.ip), pingCell, tcpCell, element('td', 'mono numeric', latencyText(t)), lossCell, checked, actions);
    $('targets').append(row);
  }
  const selected = devices.find(t => t.id === selectedId);
  $('detail-name').textContent = selected ? selected.name : 'Select a device';
  $('detail-address').textContent = selected ? `${selected.ip} : ${selected.port}` : 'Select a device to inspect history and diagnostics.';
  $('device-availability').replaceChildren(); $('device-checks').replaceChildren();
  if (selected) {
    $('device-availability').append(availabilityMetric('ICMP check availability', selected.availability?.icmp), availabilityMetric('TCP check availability', selected.availability?.tcp));
    $('device-checks').append(result('ICMP', checkStatus(selected, 'icmp'), `Responding · ${latencyText(selected)}`, 'No response · N/A', selected.checked_at ? 'Unknown / incomplete' : 'Awaiting check'), result(`TCP / ${selected.port}`, checkStatus(selected, 'tcp'), 'Open', 'Unavailable', selected.checked_at ? 'Unknown / unavailable' : 'Awaiting check'));
    const loss = element('div', 'result');
    loss.append(element('span', '', 'ICMP PACKET LOSS'), element('span', 'mono', Number.isFinite(selected.packet_loss_pct) ? `${fmt(selected.packet_loss_pct)}% · ${selected.icmp_received}/${selected.icmp_sent} replies` : 'N/A'));
    $('device-checks').append(loss);
    for (const [label,message] of [['ICMP',selected.ping_error],[`TCP / ${selected.port}`,selected.tcp_error],['Historical diagnostic (check not recorded)',selected.legacy_error]]) {
      if (message) $('device-checks').append(element('p', 'target-error', `${label}: ${message}`));
    }
  }
  $('availability-history-label').textContent = selected ? `Selected device: ${selected.name} · ${selected.ip}:${selected.port} · latest 60 stored cycles` : 'Select a device to inspect its check results.';
  $('loss-history-label').textContent = selected ? `Selected device: ${selected.name} · ${selected.ip}:${selected.port} · latest 60 checks` : 'Select a device to inspect its packet-loss history.';
  $('history-label').textContent = selected ? `Selected device: ${selected.name} · ${selected.ip}:${selected.port} · latest 60 checks` : 'Select a device to inspect its latest 60 checks.';
  if (selected && historyRange) for (const id of ['history-label','loss-history-label','availability-history-label']) $(id).textContent = `Selected device: ${selected.name} · ${selected.ip}:${selected.port} · selected ${historyRange} window`;
}
for (const key of Object.keys(sortColumns)) $('sort-' + key).addEventListener('click', () => sortDevices(key));
async function selectHistory(id) {
  selectedId = id;
  void loadAdvancedAnalytics();
  const selected = devices.find(t => t.id === id);
  if (selected && !traceBusy) { $('trace-ip').value = selected.ip; $('trace-authorized').checked = false; }
  history = []; historyWindow = null; rangeSummary = null; historyError = null; historyLoading = false;
  render();
  drawChart();
  // Make selection visible immediately, including empty or failed histories.
  const section = $('diagnostics');
  section.focus({preventScroll: true});
  section.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start'});
  try { await drawHistory(); } catch {}
}
function load(force = false) {
  // Mutations must refresh after an older poll, not silently skip the refresh.
  if (loading) return force ? loadPromise.then(() => load()) : loadPromise;
  loading = true;
  loadPromise = (async () => {
    try {
      devices = await api('/api/targets');
      mapTargetsFresh=true;
      if (!devices.some(t => t.id === selectedId)) { selectedId = devices[0]?.id ?? null; history = []; }
      render();
      $('connection').textContent = `● Backend connected · Refreshed ${new Date().toLocaleTimeString()}`;
      $('global-error').hidden = true;
    } catch (e) {
      mapTargetsFresh=false;renderNetworkMap();
      $('connection').textContent = '● Backend unavailable · Results may be stale'; failure(e);
      if (!devices.length) tableEmpty('Cannot load devices. Start the local Flask server and retry.');
      return;
    }
    // History errors have their own visible chart states. A successful target
    // refresh is still evidence of a connected backend.
    renderComparisonChoices();
    renderIncidentDeviceFilter();
    try { await Promise.all([drawHistory(), loadComparison(), loadIncidents(), loadNotifications(), loadDiscoveryInventory(), loadAdvancedAnalytics()]); } catch {}
    renderNetworkMap();await refreshMapIncidents();
  })().finally(() => { loading = false; });
  return loadPromise;
}

async function drawHistory() {
  const id = selectedId, requestId = ++historyRequest;
  if (id === null) { history = []; historyWindow = null; rangeSummary = null; historyError = null; historyLoading = false; drawChart(); return; }
  historyError = null; historyLoading = true;
  $('chart-status').textContent = 'Loading history…'; $('loss-chart-status').textContent = 'Loading history…'; $('timeline-status').textContent = 'Loading history…';
  try {
    const data = await api(historyRange ? `/api/history?targets=${id}&range=${historyRange}` : `/api/targets/${id}/history`);
    if (id !== selectedId || requestId !== historyRequest) return;
    historyWindow = historyRange ? {start:data.start,end:data.end} : null;
    rangeSummary = historyRange ? data.series[0] : null;
    history = (historyRange ? data.series[0].points : data).slice().sort((a,b)=>(Number.isFinite(chartTime(a))?chartTime(a):Infinity)-(Number.isFinite(chartTime(b))?chartTime(b):Infinity));
    historyLoading = false; drawChart();
  }
  catch (e) { if (id !== selectedId || requestId !== historyRequest) return; history = []; rangeSummary = null; historyWindow = null; historyLoading = false; historyError = `History unavailable: ${e.message}. Select View history to retry.`; drawChart(); throw e; }
}
function drawChart() {
  drawLossChart();
  drawCheckTimeline();
  const canvas = $('plot'), ctx = canvas.getContext('2d'), scale = window.devicePixelRatio || 1;
  const width = canvas.getBoundingClientRect().width || 700, height = 220;
  canvas.width = Math.round(width * scale); canvas.height = height * scale; ctx.setTransform(scale, 0, 0, scale, 0, 0);
  const left = 48, right = 16, top = 15, bottom = 32;
  const bounds = timeBounds(history, historyWindow);
  const valid = history.filter(d => d.ping_ok === 1 && Number.isFinite(d.latency_ms) && Number.isFinite(chartTime(d)));
  const max = Math.max(10, ...valid.map(d => d.latency_ms)) * 1.2;
  ctx.font = '10px Segoe UI'; ctx.fillStyle = '#7f90a6'; ctx.strokeStyle = '#26313e'; ctx.lineWidth = 1;
  for (let i=0; i<4; i++) { const y = top + (height-top-bottom)*i/3; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(width-right,y); ctx.stroke(); ctx.fillText(`${Math.round(max*(1-i/3))}`, 8, y+3); }
  let previous = null;
  history.forEach((d,i) => {
    const fraction = timeFraction(d, bounds);
    if (d.ping_ok !== 1 || !Number.isFinite(d.latency_ms) || fraction === null) { previous = null; return; }
    const x = left + (width-left-right)*fraction, y = top+(height-top-bottom)*(1-d.latency_ms/max);
    ctx.strokeStyle = '#65b9ae'; ctx.lineWidth = 2;
    if (previous && chartTime(d)-previous[2] <= CHART_GAP_MS) { ctx.beginPath(); ctx.moveTo(previous[0],previous[1]); ctx.lineTo(x,y); ctx.stroke(); }
    ctx.fillStyle = '#65b9ae'; ctx.beginPath(); ctx.arc(x,y,2.5,0,Math.PI*2); ctx.fill(); previous = [x,y,chartTime(d)];
  });
  ctx.fillStyle = '#7f90a6';
  if (history.length || historyWindow) { ctx.textAlign = 'left'; ctx.fillText(new Date(bounds[0]).toLocaleString(),left,height-7); ctx.textAlign = 'right'; ctx.fillText(new Date(bounds[1]).toLocaleString(),width-right,height-7); }
  $('chart-status').textContent = !history.length ? 'No measurements yet. Run checks to collect the first sample.' : !valid.length ? 'No exact RTT samples in this window. Responses may report a bound, have unavailable RTT, or be historical process timings. Review ICMP and TCP results separately.' : `${history.length} recorded checks · ${valid.length} exact RTT samples`;
  canvas.setAttribute('aria-label', `ICMP round-trip time: ${valid.length} exact RTT samples from ${history.length} checks. ${valid.length ? `Minimum ${fmt(Math.min(...valid.map(d=>d.latency_ms)))} milliseconds, maximum ${fmt(Math.max(...valid.map(d=>d.latency_ms)))} milliseconds.` : ''}`);
  $('history-stats').replaceChildren();
  for (const [label, value] of [['Ping responses', history.length ? `${history.filter(d=>d.ping_ok===1).length}/${history.length}` : '—'], ['TCP accepted', history.length ? `${history.filter(d=>d.tcp_ok===1).length}/${history.length}` : '—'], ['Mean RTT', valid.length ? `${fmt(valid.reduce((s,d)=>s+d.latency_ms,0)/valid.length)} ms` : 'N/A']]) { const n = element('span','',label); n.append(element('b','',value)); $('history-stats').append(n); }
  if (rangeSummary) {
    $('history-stats').replaceChildren(element('span','',summaryText(rangeSummary.summary)));
    $('chart-status').textContent += rangeSummary.truncated ? ' · Plot capped at newest 2000 records; summary covers full window.' : ' · Summary covers full window.';
  }
  if (historyError || historyLoading) {
    for (const id of ['chart-status', 'loss-chart-status', 'timeline-status']) $(id).textContent = historyError || 'Loading history…';
  }
}
function drawLossChart() {
  const canvas=$('loss-plot'), ctx=canvas.getContext('2d'), scale=window.devicePixelRatio||1;
  const width=canvas.getBoundingClientRect().width||700, height=220, left=48, right=16, top=15, bottom=32;
  const bounds = timeBounds(history, historyWindow);
  canvas.width=Math.round(width*scale); canvas.height=height*scale; ctx.setTransform(scale,0,0,scale,0,0);
  ctx.font='12px Segoe UI'; ctx.strokeStyle='#26313e'; ctx.fillStyle='#7f90a6'; ctx.lineWidth=1;
  for(const percent of [100,75,50,25,0]){
    const y=top+(height-top-bottom)*(1-percent/100);
    ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(`${percent}%`,5,y+3);
  }
  let previous=null;
  const valid=history.filter(d=>Number.isFinite(d.packet_loss_pct));
  history.forEach((d,i)=>{
    const fraction = timeFraction(d, bounds);
    if(!Number.isFinite(d.packet_loss_pct) || fraction === null){previous=null;return;}
    const x=left+(width-left-right)*fraction;
    const y=top+(height-top-bottom)*(1-d.packet_loss_pct/100);
    ctx.strokeStyle='#bbaa80';ctx.fillStyle='#bbaa80';ctx.lineWidth=2;
    if(previous && chartTime(d)-previous[2]<=CHART_GAP_MS){ctx.beginPath();ctx.moveTo(previous[0],previous[1]);ctx.lineTo(x,y);ctx.stroke();}
    ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();previous=[x,y,chartTime(d)];
  });
  ctx.fillStyle='#7f90a6';
  if(history.length || historyWindow){ctx.textAlign='left';ctx.fillText(new Date(bounds[0]).toLocaleString(),left,height-7);ctx.textAlign='right';ctx.fillText(new Date(bounds[1]).toLocaleString(),width-right,height-7);}
  $('loss-chart-status').textContent=valid.length?`${valid.length} recorded packet-loss measurements · latest ${fmt(valid.at(-1).packet_loss_pct)}%`:'No packet-loss measurements available. Run checks to collect a five-probe batch; older records remain N/A.';
  canvas.setAttribute('aria-label', `ICMP packet-loss history: ${valid.length} measurements.${valid.length?` Latest ${fmt(valid.at(-1).packet_loss_pct)} percent; minimum ${fmt(Math.min(...valid.map(d=>d.packet_loss_pct)))} percent; maximum ${fmt(Math.max(...valid.map(d=>d.packet_loss_pct)))} percent.`:''}`);
}
// Group collisions without aggregating or discarding the underlying observations.
function timelineLayout(records, bounds, width) {
  const span=bounds[1]-bounds[0], groups=[], undated=[];
  const points=records.map((record,index)=>({record,index,time:chartTime(record)}));
  for(const point of points.filter(p=>!Number.isFinite(p.time))) undated.push(point);
  const dated=points.filter(p=>Number.isFinite(p.time) && p.time>=bounds[0] && p.time<=bounds[1]).sort((a,b)=>a.time-b.time || a.index-b.index);
  for(const point of dated) {
    const fraction=span?(point.time-bounds[0])/span:.5, last=groups.at(-1);
    if(last && (fraction-last.start)*width<16) {last.points.push(point);last.end=fraction;}
    else groups.push({points:[point],start:fraction,end:fraction});
  }
  const separated=[];
  for(const group of groups) {
    const previous=separated.at(-1);
    if(previous && ((group.start+group.end-previous.start-previous.end)/2)*width<16) {
      previous.points.push(...group.points);previous.end=group.end;
    } else separated.push(group);
  }
  return {groups:separated,undated,dated};
}
function timelineState(record,key) {return ['success','failure'].includes(record[key])?record[key]:'unknown';}
let timelineZoom=null, timelineDevice=null, timelineSelection=null, timelineReturnFocus=null, timelineStatusText='', timelineVisibleCycles=0, timelinePage=0;
const TIMELINE_PAGE_SIZE=5;
function timelineCycleSummary(shown) {
  const total=Number.isInteger(rangeSummary?.summary?.record_count)?rangeSummary.summary.record_count:history.length;
  const zone=Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local time';
  return `${shown}/${total} cycles${total?'':' · No recorded cycles.'} · ${zone} · oldest to newest${timelineZoom?' · Zoomed':''}${rangeSummary?.truncated?` · Newest ${history.length} loaded`:''}`;
}
function timelineDuration(ms) {
  const minutes=Math.floor(ms/60000);
  return minutes>=1440?`${Math.floor(minutes/1440)}d ${Math.floor(minutes%1440/60)}h`:minutes>=60?`${Math.floor(minutes/60)}h ${minutes%60}m`:minutes?`${minutes}m`:`${Math.round(ms/1000)}s`;
}
function showTimelineRecords(points, trigger) {
  timelineReturnFocus?.setAttribute('aria-expanded','false');
  timelineSelection=points;timelinePage=0;timelineReturnFocus=trigger;trigger.setAttribute('aria-expanded','true');
  $('timeline-details').hidden=false;
  $('timeline-detail-heading').textContent=`${points.length} selected ${points.length===1?'observation':'observations'}`;
  const summaries=[['ICMP','icmp_check_status'],['TCP','tcp_check_status']].map(([label,key])=>{
    const count={success:0,failure:0,unknown:0};points.forEach(p=>count[timelineState(p.record,key)]++);
    return `${label}: ${count.success} success · ${count.failure} failure · ${count.unknown} unknown`;
  });
  $('timeline-selection-summary').replaceChildren(...summaries.map(summary=>element('span','',summary)));
  const times=points.map(p=>p.time).filter(Number.isFinite);
  $('timeline-zoom').disabled=times.length<2 || Math.min(...times)===Math.max(...times);
  renderTimelinePage();$('timeline-detail-heading').focus({preventScroll:true});
}
function renderTimelinePage() {
  if(!timelineSelection)return;
  const start=timelinePage*TIMELINE_PAGE_SIZE,body=$('timeline-records');body.replaceChildren();
  for(const {record,index,time} of timelineSelection.slice(start,start+TIMELINE_PAGE_SIZE)) {
    const row=element('tr');row.setAttribute('title',`Cycle ${record.id??index+1}`);
    row.append(element('td','observation-time',Number.isFinite(time)?observationLabel(record.checked_at):'Unknown time'));
    for(const key of ['icmp_check_status','tcp_check_status']) {
      const state=timelineState(record,key);
      row.append(element('td',`history-state ${state}`,state==='success'?'Success':state==='failure'?'Failure':'Unknown'));
    }
    body.append(row);
  }
  $('timeline-pagination').hidden=timelineSelection.length<=TIMELINE_PAGE_SIZE;
  $('timeline-page-status').textContent=`${start+1}–${Math.min(start+TIMELINE_PAGE_SIZE,timelineSelection.length)} of ${timelineSelection.length}`;
  $('timeline-page-prev').disabled=timelinePage===0;
  $('timeline-page-next').disabled=start+TIMELINE_PAGE_SIZE>=timelineSelection.length;
}
$('timeline-page-prev').addEventListener('click',()=>{if(timelineSelection && timelinePage>0){timelinePage--;renderTimelinePage();}});
$('timeline-page-next').addEventListener('click',()=>{if(timelineSelection && (timelinePage+1)*TIMELINE_PAGE_SIZE<timelineSelection.length){timelinePage++;renderTimelinePage();}});
function closeTimelineRecords() {
  $('timeline-details').hidden=true;timelineSelection=null;timelineReturnFocus?.setAttribute('aria-expanded','false');timelineReturnFocus?.focus({preventScroll:true});
}
function drawCheckTimeline(force=false) {
  // Preserve an open record table and keyboard focus during background refresh.
  if(!force && history.length && !historyError && timelineDevice===selectedId && (timelineSelection || document.activeElement?.closest?.('#check-timeline'))) {
    timelineStatusText=timelineCycleSummary(timelineVisibleCycles);
    $('timeline-status').textContent=timelineStatusText;return;
  }
  if(!history.length || historyError){timelineSelection=null;$('timeline-details').hidden=true;}
  if(timelineDevice!==selectedId) {timelineZoom=null;timelineSelection=null;$('timeline-details').hidden=true;timelineDevice=selectedId;}
  const timeline=$('check-timeline');timeline.replaceChildren();
  const bounds=timelineZoom || timeBounds(history,historyWindow),width=Math.max(80,timeline.getBoundingClientRect().width-(window.matchMedia('(max-width: 600px)').matches?66:92));
  const layout=timelineLayout(history,bounds,width);
  $('timeline-range').value=historyRange;$('timeline-reset').hidden=!timelineZoom;
  for(const [key,label] of [['icmp_check_status','ICMP'],['tcp_check_status','TCP']]) {
    const row=element('div','timeline-row'),track=element('div','timeline-track');
    track.setAttribute('role','group');track.setAttribute('aria-label',label);
    const marks=[];
    for(const group of layout.groups) {
      const counts={success:0,failure:0,unknown:0};group.points.forEach(p=>counts[timelineState(p.record,key)]++);
      const states=Object.keys(counts).filter(s=>counts[s]),state=states.length===1?states[0]:'mixed',single=group.points.length===1;
      const text=single?`${label} · cycle ${group.points[0].index+1} · ${observationLabel(group.points[0].record.checked_at)} · ${state}${state==='unknown'?' (excluded)':''}`:
        `${label} · ${group.points.length} cycles · ${observationLabel(group.points[0].record.checked_at)} to ${observationLabel(group.points.at(-1).record.checked_at)} · ${counts.success} success, ${counts.failure} failure, ${counts.unknown} unknown (excluded). Show records`;
      const mark=element('button',`timeline-mark ${state}`);
      mark.type='button';mark.setAttribute('aria-label',text);mark.setAttribute('title',text);mark.setAttribute('aria-controls','timeline-details');mark.setAttribute('aria-expanded','false');
      mark.style.left=`${(group.start+group.end)/2*100}%`;
      // Fixed-width glyphs represent observations, never elapsed monitoring duration.
      const extent=6;
      const sample=element('span','observation-sample');sample.setAttribute('aria-hidden','true');sample.style.width=`${extent}px`;
      for(const status of states) sample.append(element('span',`observation-state ${status}`));
      mark.append(sample);mark.tabIndex=marks.length===0?0:-1;marks.push(mark);
      mark.addEventListener('keydown',event=>{
        const index=marks.indexOf(mark);
        const next=event.key==='ArrowRight'?Math.min(marks.length-1,index+1):event.key==='ArrowLeft'?Math.max(0,index-1):event.key==='Home'?0:event.key==='End'?marks.length-1:null;
        if(next===null)return;event.preventDefault();marks.forEach((m,i)=>m.tabIndex=i===next?0:-1);marks[next].focus({preventScroll:true});
      });
      mark.addEventListener('click',()=>{marks.forEach(m=>m.tabIndex=m===mark?0:-1);showTimelineRecords(group.points,mark);});track.append(mark);
    }
    row.append(element('span','timeline-label',label),track);timeline.append(row);
  }
  const axis=element('div','timeline-axis'),ticks=element('div','timeline-ticks');
  const tickCount=bounds[0]===bounds[1]?1:Math.max(2,Math.min(4,Math.floor(width/160)+1));
  if(layout.dated.length || historyWindow)for(let i=0;i<tickCount;i++) {
    const fraction=tickCount===1?.5:i/(tickCount-1),date=new Date(bounds[0]+(bounds[1]-bounds[0])*fraction);
    const tick=element('span','timeline-tick');tick.style.left=`${fraction*100}%`;
    if(i===0)tick.className+=' first';if(i===tickCount-1)tick.className+=' last';
    tick.append(element('span','',date.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})),element('span','',date.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:bounds[1]-bounds[0]<600000?'2-digit':undefined,hour12:false})));ticks.append(tick);
  }
  axis.append(element('span','timeline-label',''),ticks);timeline.append(axis);
  const gaps=element('div','timeline-gaps');
  for(let i=1;i<layout.dated.length;i++) {
    const prev=layout.dated[i-1],next=layout.dated[i],span=bounds[1]-bounds[0];
    if(span && (next.time-prev.time)/span*width>160) {
      const gap=element('span','timeline-gap',`No records · ${timelineDuration(next.time-prev.time)}`);
      gap.style.left=`${((prev.time+next.time)/2-bounds[0])/span*100}%`;gaps.append(gap);
    }
  }
  const gapRow=element('div','timeline-axis');gapRow.append(element('span','timeline-label',''),gaps);timeline.append(gapRow);
  if(layout.undated.length) {
    const button=element('button','secondary',`${layout.undated.length} cycles with unknown time`);button.type='button';button.addEventListener('click',()=>showTimelineRecords(layout.undated,button));timeline.append(button);
  }
  timelineVisibleCycles=layout.dated.length+layout.undated.length;
  timelineStatusText=timelineCycleSummary(timelineVisibleCycles);
  $('timeline-status').textContent=timelineStatusText;
}
$('timeline-close').addEventListener('click',closeTimelineRecords);
$('timeline-details').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeTimelineRecords();}});
$('timeline-zoom').addEventListener('click',()=>{
  const times=timelineSelection?.map(p=>p.time).filter(Number.isFinite) || [];
  if(times.length<2 || Math.min(...times)===Math.max(...times))return;
  timelineZoom=[Math.min(...times),Math.max(...times)];closeTimelineRecords();drawCheckTimeline(true);$('timeline-reset').focus();
});
$('timeline-reset').addEventListener('click',()=>{timelineZoom=null;timelineSelection=null;$('timeline-details').hidden=true;drawCheckTimeline(true);$('timeline-range').focus();});
function summaryText(s) {
  const availability = p => `${p.toUpperCase()} sampled ${fmt(s[p].percentage)}${s[p].percentage == null ? '' : '%'} (${s[p].successful_checks}/${s[p].valid_checks}; ${s[p].excluded_checks} unknown)`;
  return `${s.record_count} records · ${s.valid_measurements} valid · ${s.unknown_measurements} unknown · exact RTT ${s.exact_rtt_count}, mean ${fmt(s.average_latency_ms)} ms, min ${fmt(s.minimum_latency_ms)} / max ${fmt(s.maximum_latency_ms)} ms · missing RTT ${s.missing_rtt_count} · mean loss ${fmt(s.average_packet_loss_pct)}% (${s.valid_loss_count} valid; ${s.missing_loss_count} missing) · ${availability('icmp')} · ${availability('tcp')}`;
}
function renderComparisonChoices() {
  const available = new Set(devices.map(t=>t.id));
  comparisonIds = new Set([...comparisonIds].filter(id=>available.has(id)));
  const key=JSON.stringify(devices.map(t=>[t.id,t.name]));
  if(key===comparisonChoicesKey)return;
  comparisonChoicesKey=key;
  $('comparison-devices').replaceChildren();
  for (const device of devices) {
    const label = element('label', 'compare-choice'), input = element('input');
    input.type = 'checkbox'; input.checked = comparisonIds.has(device.id);
    input.addEventListener('change', () => {
      if (input.checked && comparisonIds.size >= 4) { input.checked = false; $('comparison-status').textContent = 'Select at most four devices.'; return; }
      if (input.checked) comparisonIds.add(device.id); else comparisonIds.delete(device.id);
      void loadComparison();
      void loadAdvancedAnalytics();
    });
    label.append(input, element('span','',device.name)); $('comparison-devices').append(label);
  }
}
async function loadComparison() {
  const requestId = ++comparisonRequest;
  if (comparisonIds.size < 2) {
    comparisonData = null; $('comparison-panel').hidden = true;
    $('comparison-status').textContent = 'Select two to four devices to compare. Comparison uses the chosen range, or 24 hours when Latest 60 is selected.';
    return;
  }
  $('comparison-panel').hidden = false;
  $('comparison-status').textContent = 'Loading comparison…';
  try {
    const data = await api(`/api/history?targets=${[...comparisonIds].join(',')}&range=${historyRange || '24h'}`);
    if (requestId !== comparisonRequest) return;
    comparisonData = data; renderComparison();
  } catch (error) {
    if (requestId !== comparisonRequest) return;
    comparisonData = null; $('comparison-summary').replaceChildren();
    for (const id of ['compare-rtt','compare-loss','compare-availability']) { const canvas=$(id); canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height); }
    $('comparison-status').textContent = `Comparison unavailable: ${error.message}. Retrying on the next dashboard refresh.`;
  }
}
const comparisonColors = ['#65b9ae','#c6ad81','#8eaedf','#c99ccf'];
function renderComparison() {
  if (!comparisonData) return;
  $('comparison-summary').replaceChildren();
  comparisonData.series.forEach((series,index)=>{
    const s=series.summary,row=element('article','comparison-card');
    row.style.borderLeft=`3px solid ${comparisonColors[index]}`;
    row.append(element('h3','',`${index+1}. ${series.name}`),element('p','muted',`${s.record_count} records · ${s.valid_measurements} valid · ${s.unknown_measurements} unknown`));
    const groups=element('div','comparison-metrics');
    const group=(title,details)=>{const section=element('section');section.append(element('h4','',title),element('p','',details));groups.append(section);};
    group('Exact RTT · ms',`${s.exact_rtt_count} exact · mean ${fmt(s.average_latency_ms)} ms · min ${fmt(s.minimum_latency_ms)} / max ${fmt(s.maximum_latency_ms)} ms · missing RTT ${s.missing_rtt_count}`);
    group('Packet loss · %',`Mean ${fmt(s.average_packet_loss_pct)}% · ${s.valid_loss_count} valid · ${s.missing_loss_count} missing`);
    for(const protocol of ['icmp','tcp']){const v=s[protocol];group(`${protocol.toUpperCase()} sampled availability`,`${protocol.toUpperCase()} sampled ${fmt(v.percentage)}${v.percentage==null?'':'%'} · ${v.successful_checks}/${v.valid_checks} successful / valid · ${v.excluded_checks} unknown`);}
    row.append(groups);
    if(series.truncated)row.append(element('p','chart-note','Plot capped at newest 2000 rows; summary covers full range.'));
    $('comparison-summary').append(row);
  });
  for (const metric of ['rtt','loss','availability']) drawComparisonChart(metric);
  $('comparison-status').textContent = `${observationLabel(comparisonData.start)} → ${observationLabel(comparisonData.end)} · Common time axes. Lines break after 5 minutes; availability marks are individual sampled checks, never continuous uptime. No records means missing data, not downtime.`;
}
function drawComparisonAvailability() {
  const canvas=$('compare-availability'),ctx=canvas.getContext('2d'),scale=window.devicePixelRatio||1;
  const width=canvas.getBoundingClientRect().width||700,left=90,right=16,top=12,rowHeight=30;
  const height=top+comparisonData.series.length*2*rowHeight+40,bounds=timeBounds([],comparisonData);
  canvas.style.height=`${height}px`;canvas.width=Math.round(width*scale);canvas.height=height*scale;ctx.setTransform(scale,0,0,scale,0,0);ctx.font='12px Segoe UI';
  comparisonData.series.forEach((series,index)=>{
    for(const [offset,protocol] of ['icmp','tcp'].entries()){
      const y=top+(index*2+offset+.5)*rowHeight;
      ctx.fillStyle=comparisonColors[index];ctx.textAlign='left';ctx.fillText(`${index+1} · ${protocol.toUpperCase()}`,8,y+4);
      ctx.strokeStyle='#2c3034';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();
      for(const row of series.points){
        const fraction=timeFraction(row,bounds);if(fraction===null)continue;
        const x=left+(width-left-right)*fraction,state=row[`${protocol}_check_status`];
        ctx.strokeStyle=comparisonColors[index];ctx.fillStyle=comparisonColors[index];ctx.lineWidth=2;
        if(state==='success')ctx.fillRect(x-1.5,y-5,3,10);
        else if(state==='failure'){ctx.beginPath();ctx.moveTo(x-3,y-4);ctx.lineTo(x+3,y+4);ctx.moveTo(x-3,y+4);ctx.lineTo(x+3,y-4);ctx.stroke();}
        else {ctx.fillStyle='#c3c8cd';ctx.textAlign='center';ctx.fillText('?',x,y+4);}
      }
    }
  });
  ctx.fillStyle='#aeb5bb';ctx.textAlign='left';ctx.fillText(new Date(bounds[0]).toLocaleString(),left,height-9);
  ctx.textAlign='right';ctx.fillText(new Date(bounds[1]).toLocaleString(),width-right,height-9);
  canvas.setAttribute('aria-label',`Sampled availability tracks. ${comparisonData.series.map((s,i)=>`${i+1}: ${s.name}, ICMP and TCP`).join('; ')}. Bar: successful; cross: failed; question mark: unknown/excluded. Blank spans: no stored observations. Not continuous uptime.`);
}
function drawComparisonChart(metric) {
  if(metric==='availability'){drawComparisonAvailability();return;}
  const canvas=$(`compare-${metric}`),ctx=canvas.getContext('2d'),scale=window.devicePixelRatio||1;
  const width=canvas.getBoundingClientRect().width||700,height=220,left=90,right=16,top=15,bottom=38;
  canvas.width=Math.round(width*scale);canvas.height=height*scale;ctx.setTransform(scale,0,0,scale,0,0);
  const bounds=timeBounds([],comparisonData), value=row=>metric==='rtt'?row.latency_ms:row.packet_loss_pct;
  const exact=comparisonData.series.flatMap(s=>s.points.map(value)).filter(Number.isFinite);
  const maximum=metric==='rtt'?Math.max(10,...exact)*1.2:100;
  ctx.font='12px Segoe UI';ctx.fillStyle='#949a9f';ctx.strokeStyle='#2c3034';
  for(let i=0;i<3;i++){const y=top+(height-top-bottom)*i/2;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(fmt(maximum*(1-i/2)),4,y+3);}
  comparisonData.series.forEach((series,index)=>{
    ctx.strokeStyle=comparisonColors[index];ctx.fillStyle=comparisonColors[index];ctx.lineWidth=1.5;
    let previous=null;
    series.points.forEach(row=>{
      const sample=value(row),fraction=timeFraction(row,bounds),time=chartTime(row);
      if(!Number.isFinite(sample)||fraction===null){previous=null;return;}
      const x=left+(width-left-right)*fraction,y=top+(height-top-bottom)*(1-sample/maximum);
      if(previous && time-previous[2]<=CHART_GAP_MS){ctx.beginPath();ctx.moveTo(previous[0],previous[1]);ctx.lineTo(x,y);ctx.stroke();}
      ctx.beginPath();ctx.arc(x,y,2,0,Math.PI*2);ctx.fill();
      previous=[x,y,time];
    });
  });
  ctx.fillStyle='#949a9f';ctx.textAlign='left';ctx.fillText(new Date(bounds[0]).toLocaleString(),left,height-9);
  ctx.textAlign='right';ctx.fillText(new Date(bounds[1]).toLocaleString(),width-right,height-9);
  canvas.setAttribute('aria-label',`${metric} comparison. ${comparisonData.series.map(s=>s.name).join(', ')}. See per-device summaries for values.`);
}
function changeHistoryRange(value) {
  timelineZoom=null;timelineSelection=null;$('timeline-details').hidden=true;
  $('history-range').value=value;$('timeline-range').value=value;
  historyRange=value; historyWindow=null; rangeSummary=null; history=[];
  render();
  void drawHistory().catch(()=>{}); void loadComparison();
  void loadAdvancedAnalytics();
}
$('history-range').addEventListener('change',()=>changeHistoryRange($('history-range').value));
$('timeline-range').addEventListener('change',()=>changeHistoryRange($('timeline-range').value));
window.addEventListener('resize', renderComparison);
const incidentConditions = {icmp_unavailable:'ICMP unavailable',tcp_unavailable:'TCP service unavailable',packet_loss:'Excessive packet loss',high_latency:'High exact RTT'};
let incidentRows=[],incidentRequest=0,incidentDetailId=null,incidentDetail=null,incidentDetailRequest=0,incidentAckBusy=false,incidentNoteDraft='',incidentDeviceKey='',incidentRenderedKey='';
function incidentDeviceName(row) { return row.target_id == null ? `Removed device #${row.original_target_id}` : devices.find(d=>d.id===row.target_id)?.name || `Device #${row.target_id}`; }
function incidentDuration(row) {
  const seconds=row.state==='RESOLVED'?row.duration_seconds:Math.max(0,(Date.now()-Date.parse(row.first_observed))/1000);
  const whole=Math.floor(seconds),span=whole>=86400?`${Math.floor(whole/86400)}d ${Math.floor(whole%86400/3600)}h`:whole>=3600?`${Math.floor(whole/3600)}h ${Math.floor(whole%3600/60)}m`:whole>=60?`${Math.floor(whole/60)}m ${whole%60}s`:`${whole}s`;
  return Number.isFinite(seconds)?`${span} ${row.state==='RESOLVED'?'sampled span':'elapsed since first observation'}`:'Duration unavailable';
}
function incidentValues(row) {
  const trigger=row.triggering_measurements?.trigger,config=row.thresholds;
  return trigger && config ? `Trigger ${fmt(trigger.value)}${row.condition==='high_latency'?' ms':row.condition==='packet_loss'?'%':' (1 = failed check)'} · threshold > ${fmt(config.threshold)} · ${config.open_checks} opening / ${config.recovery_checks} recovery checks` : 'Trigger details unavailable';
}
function renderIncidentDeviceFilter() {
  const key=JSON.stringify(devices.map(d=>[d.id,d.name]));
  if(key===incidentDeviceKey)return;
  incidentDeviceKey=key;
  const select=$('incident-device'),value=select.value||'';
  select.replaceChildren();const all=element('option','','All devices');all.value='';select.append(all);
  for(const device of devices){const option=element('option','',device.name);option.value=String(device.id);select.append(option);}
  if(value && !devices.some(d=>String(d.id)===value)){const option=element('option','',`Device #${value} (removed)`);option.value=value;select.append(option);}
  select.value=value;
}
function incidentQuery() {
  const filters={state:$('incident-state').value||'ACTIVE',range:$('incident-range').value||'30d',limit:'100'};
  if(filters.state==='ALL')delete filters.state;
  if($('incident-device').value)filters.target_id=$('incident-device').value;
  if($('incident-condition').value)filters.condition=$('incident-condition').value;
  return Object.entries(filters).map(([key,value])=>`${key}=${encodeURIComponent(value)}`).join('&');
}
function renderIncidentRows() {
  const list=$('incident-timeline');list.replaceChildren();
  const sorted=[...incidentRows].sort((a,b)=>Date.parse(b.first_observed)-Date.parse(a.first_observed)||b.id-a.id);
  if(!sorted.length){list.append(element('li','muted','No incidents match the selected filters.'));return;}
  for(const row of sorted){
    const item=element('li',`incident-row incident-${row.state.toLowerCase()}`);
    item.append(element('h3','',`${incidentDeviceName(row)} · ${incidentConditions[row.condition]||row.condition}`),element('span','mono',row.state));
    item.append(element('p','muted',`First observed ${observationLabel(row.first_observed)} · Open confirmed ${observationLabel(row.triggering_measurements?.trigger?.observed_at)} · ${incidentDuration(row)}${row.resolved_at?' · Recovered '+observationLabel(row.resolved_at):''}${row.acknowledged_at?' · Acknowledged '+observationLabel(row.acknowledged_at):''}`));
    item.append(element('p','muted',incidentValues(row)));
    const button=element('button','secondary',`Details #${row.id}`);button.type='button';button.setAttribute('aria-controls','incident-detail');button.onclick=()=>loadIncidentDetail(row.id,true);
    item.append(button);list.append(item);
  }
}
async function loadIncidents() {
  const requestId=++incidentRequest;
  $('incident-status').textContent='Loading incidents…';
  try{
    const [data,summary]=await Promise.all([api('/api/incidents?'+incidentQuery()),api('/api/incidents/summary')]);
    if(requestId!==incidentRequest)return;
    incidentRows=data.incidents;
    $('incident-overview').textContent=`${summary.active} active · ${summary.open} open · ${summary.acknowledged} acknowledged (all stored incidents, including removed devices)`;
    $('incident-counts').replaceChildren();
    for(const [condition,label] of Object.entries(incidentConditions)){
      const counts=summary.by_condition[condition];$('incident-counts').append(element('span','',`${label}: ${counts.OPEN} open / ${counts.ACKNOWLEDGED} acknowledged`));
    }
    renderIncidentRows();
    $('incident-status').textContent=`${incidentRows.length} incidents displayed · newest first by observation time${data.next_before_id?' · Newest 100 records loaded; narrow filters for older history.':''} · Refreshed ${new Date().toLocaleTimeString()}`;
    if(incidentDetailId!==null)await loadIncidentDetail(incidentDetailId,false);
  }catch(error){
    if(requestId!==incidentRequest)return;
    $('incident-status').textContent=`Incidents unavailable: ${error.message}. Previously displayed incidents may be stale; retry or wait for the next refresh.`;
  }
}
function renderIncidentDetail() {
  const panel=$('incident-detail');panel.replaceChildren();panel.hidden=false;
  if(!incidentDetail)return;
  const row=incidentDetail;
  incidentRenderedKey=JSON.stringify(row);
  panel.append(element('h3','',`Incident #${row.id} · ${incidentDeviceName(row)} · ${row.state}`),element('p','',incidentConditions[row.condition]||row.condition));
  panel.append(element('p','muted',`First ${observationLabel(row.first_observed)} · Latest ${observationLabel(row.latest_observed)} · ${incidentDuration(row)}`),element('p','muted',incidentValues(row)));
  if(row.resolved_at)panel.append(element('p','muted',`Verified recovery ${observationLabel(row.resolved_at)} · Resolution result #${row.resolving_result_id} · Recovery value ${fmt(row.recovery?.measurement?.value)} · Recovery threshold ${fmt(row.recovery?.thresholds?.threshold)}`));
  panel.append(element('p','muted',`First qualifying result #${row.triggering_measurements?.first?.result_id ?? 'N/A'} · Trigger result #${row.triggering_result_id} · Latest result #${row.latest_result_id}`));
  if(row.acknowledged_at)panel.append(element('p','muted',`Acknowledged ${observationLabel(row.acknowledged_at)}${row.operator_note?' · Note: '+row.operator_note:''}`));
  if(row.state==='OPEN'){
    const label=element('label','','Optional operator note (500 characters; avoid secrets)'),note=element('textarea');
    note.id='incident-note';note.maxLength=500;note.value=incidentNoteDraft;note.rows=2;note.disabled=incidentAckBusy;
    note.addEventListener('input',()=>{incidentNoteDraft=note.value;});label.append(note);
    const button=element('button','',incidentAckBusy?'Acknowledging…':'Acknowledge');button.type='button';button.disabled=incidentAckBusy;button.onclick=acknowledgeSelectedIncident;
    panel.append(label,button);
  }
}
async function loadIncidentDetail(id,focus=false) {
  if(incidentDetailId!==id){incidentNoteDraft='';incidentDetail=null;$('incident-action-status').textContent='';}
  incidentDetailId=id;const requestId=++incidentDetailRequest;
  if(focus){$('incident-detail').hidden=false;$('incident-detail').textContent='Loading incident details…';$('incident-detail').focus({preventScroll:true});}
  try{
    const row=await api(`/api/incidents/${id}`);
    if(requestId!==incidentDetailRequest||incidentDetailId!==id)return;
    incidentDetail=row;
    if(focus || (JSON.stringify(row)!==incidentRenderedKey && !(row.state==='OPEN' && document.activeElement?.id==='incident-note')))renderIncidentDetail();
  }catch(error){if(requestId===incidentDetailRequest)$('incident-detail').textContent=`Incident details unavailable: ${error.message}`;}
}
async function acknowledgeSelectedIncident() {
  if(incidentAckBusy||!incidentDetail||incidentDetail.state!=='OPEN')return;
  incidentAckBusy=true;++incidentDetailRequest;const id=incidentDetail.id;renderIncidentDetail();
  try{
    const row=await api(`/api/incidents/${id}/acknowledge`,{method:'POST',body:JSON.stringify({note:incidentNoteDraft})});
    if(incidentDetailId===id){++incidentDetailRequest;incidentDetail=row;incidentNoteDraft='';renderIncidentDetail();}
    $('incident-action-status').textContent=`Incident #${id} acknowledged. Monitoring and verified recovery continue.`;
    await loadIncidents();
  }catch(error){$('incident-action-status').textContent=`Acknowledgement failed: ${error.message}. Refresh details before retrying if the response was lost.`;}
  finally{incidentAckBusy=false;renderIncidentDetail();}
}
for(const id of ['incident-device','incident-condition','incident-state','incident-range'])$(id).addEventListener('change',()=>{void loadIncidents();});
$('incident-refresh').addEventListener('click',()=>loadIncidents());
let dnsBusy = false;
async function runDNSLookup(event) {
  event.preventDefault();
  if (dnsBusy) return;
  dnsBusy = true;
  const button = $('dns-submit');
  button.disabled = true; button.textContent = 'Resolving…';
  $('dns-results').replaceChildren();
  $('dns-status').textContent = 'Looking up addresses with the system resolver…';
  try {
    const data = await api('/api/dns', {method:'POST', body:JSON.stringify({domain:$('dns-domain').value}), signal:AbortSignal.timeout(8000)});
    $('dns-status').textContent = `${data.domain} · ${fmt(data.duration_ms)} ms lookup duration · ${data.ok ? 'System resolver results' : data.error}`;
    if (data.ok) {
      for (const [key,label] of [['a','A · IPv4'],['aaaa','AAAA · IPv6']]) {
        const section = element('div', 'dns-answer'); section.append(element('h4', '', label));
        const addresses = data[key] || [];
        if (!addresses.length) section.append(element('p', 'muted', 'No addresses returned for this family.'));
        else { const list = element('ul', 'dns-addresses'); for (const address of addresses) list.append(element('li', '', address)); section.append(list); }
        $('dns-results').append(section);
      }
      if (data.omitted_non_public) $('dns-results').append(element('p', 'message', 'Non-public addresses were withheld.'));
      if (data.truncated) $('dns-results').append(element('p', 'muted', 'Results limited to 32 addresses per family.'));
    }
  } catch (error) {
    $('dns-status').textContent = `DNS lookup failed: ${error.name === 'TimeoutError' ? 'The request took too long. The system resolver may still be busy; try again later.' : error.message}`;
  } finally {
    dnsBusy = false; button.disabled = false; button.textContent = 'Look up DNS';
  }
}
$('dns-form').addEventListener('submit', runDNSLookup);
let traceBusy = false;
function renderTrace(data) {
  const labels = {reached:'Destination reached', incomplete:'Incomplete trace', command_failure:'Command failure'};
  $('trace-status').textContent = `${labels[data.status] || 'Command failure'} · ${data.destination} · ${fmt(data.duration_ms)} ms elapsed. ${data.message}${data.parse_warning ? ' ' + data.parse_warning : ''}`;
  $('trace-results').replaceChildren();
  if (!data.hops?.length) return;
  const table = element('table', 'trace-table');
  const caption = element('caption', '', `Trace to ${data.destination} · actual Windows tracert results`);
  const head = element('thead'), headings = element('tr');
  for (const title of ['Hop', 'Responding IPv4', 'Probe 1', 'Probe 2', 'Probe 3']) {const cell=element('th','',title);cell.setAttribute('scope','col');headings.append(cell);}
  head.append(headings); const body = element('tbody');
  for (const hop of data.hops) {
    const row = element('tr');
    row.append(element('td','',hop.hop),element('td','',hop.ip || 'No reply'));
    for (const rtt of hop.rtts) row.append(element('td',rtt === '*' ? 'trace-timeout' : '',rtt));
    body.append(row);
  }
  table.append(caption,head,body);$('trace-results').append(table);
}
async function runTrace(event) {
  event.preventDefault();
  if (traceBusy) return;
  if (!$('trace-authorized').checked) {
    $('trace-status').textContent = 'Confirm that you own or have permission to trace this specific destination.';
    return;
  }
  traceBusy = true;
  const button = $('trace-submit');button.disabled = true;button.textContent = 'Tracing…';
  const data = {ip:$('trace-ip').value, max_hops:Number($('trace-hops').value), authorized:true};
  $('trace-authorized').checked = false;
  $('trace-results').replaceChildren();
  $('trace-status').textContent = `Tracing ${data.ip}… This may take up to 35 seconds.`;
  try {
    renderTrace(await api('/api/traceroute',{method:'POST',body:JSON.stringify(data),signal:AbortSignal.timeout(40000)}));
  } catch (error) {
    $('trace-status').textContent = `Traceroute request failed: ${error.name === 'TimeoutError' ? 'The request timed out. The server trace has its own execution limit; wait before retrying.' : error.message}`;
  } finally {
    traceBusy = false;button.disabled = false;button.textContent = 'Run traceroute';
  }
}
$('trace-ip').addEventListener('input', () => { $('trace-authorized').checked = false; });
$('trace-form').addEventListener('submit', runTrace);
function setAddDeviceOpen(open) {
  $('add-device-panel').hidden = !open;
  $('add-device-toggle').setAttribute('aria-expanded', String(open));
  if (open) $('device-name').focus();
  else $('add-device-toggle').focus();
}
$('add-device-toggle').addEventListener('click', () => setAddDeviceOpen($('add-device-panel').hidden));
$('add-device-close').addEventListener('click', () => setAddDeviceOpen(false));
$('add-device-panel').addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('add-device-panel').hidden) {
    event.preventDefault();
    event.stopPropagation();
    setAddDeviceOpen(false);
  }
});
$('form').addEventListener('submit', async e => {
  e.preventDefault(); const button = e.target.querySelector('button'); button.disabled = true; $('message').textContent = 'Adding device…';
  try { const created = await api('/api/targets', {method:'POST', body:JSON.stringify(Object.fromEntries(new FormData(e.target)))}); selectedId = created.id; e.target.reset(); $('message').textContent = 'Device added. Run checks now or wait for the next monitoring cycle.'; await load(true); if (devices.some(t => t.id === created.id) && selectedId !== created.id) { selectedId = created.id; render(); await drawHistory(); } }
  catch (e) { $('message').textContent = e.message; } finally { button.disabled = false; }
});
let manualJobId = null, manualSubmitting = false, manualPollTimer = null, manualPollBusy = false, manualProcessed = -1;
function finishManualJob() {
  if (manualPollTimer !== null) clearTimeout(manualPollTimer);
  manualPollTimer = null; manualJobId = null;
  $('check').disabled = false; $('check').textContent = 'Run checks';
}
async function pollManualJob() {
  if (!manualJobId || manualPollBusy) return;
  manualPollBusy = true;
  try {
    const job = await api(`/api/check-jobs/${manualJobId}`, {signal:AbortSignal.timeout(10000)});
    const processed = job.completed + job.failed;
    $('check-progress').textContent = `${job.state} · ${processed}/${job.total} processed · ${fmt(job.progress_percentage)}% · ${job.completed} completed · ${job.failed} failed${job.error ? ' · ' + job.error : ''}`;
    $('check').textContent = `Checking · ${fmt(job.progress_percentage)}%`;
    if (processed !== manualProcessed) { manualProcessed = processed; void load(true); }
    if (['completed', 'failed', 'cancelled'].includes(job.state)) {
      finishManualJob(); void load(true);
    }
  } catch (error) {
    $('check-progress').textContent = error.status === 404 ? 'Job status is no longer available. Jobs are lost on server restart or expire from memory.' : `Progress unavailable: ${error.message}. Retrying; the server job may still be running.`;
    if (error.status === 404 || error.status === 403) finishManualJob();
  } finally {
    manualPollBusy = false;
    if (manualJobId) manualPollTimer = setTimeout(pollManualJob, 1000);
  }
}
async function startManualJob() {
  if (manualSubmitting || manualJobId) return;
  manualSubmitting = true; $('check').disabled = true; $('check').textContent = 'Starting checks…';
  $('check-progress').textContent = 'Submitting monitoring job…';
  try {
    const job = await api('/api/check-jobs', {method:'POST', signal:AbortSignal.timeout(10000)});
    manualJobId = job.job_id; manualProcessed = -1;
    $('check-progress').textContent = 'Monitoring job accepted. Loading progress…';
    await pollManualJob();
  } catch (error) {
    $('check-progress').textContent = error.status === 409 ? error.message : `Could not confirm job submission: ${error.message}. A submitted job may still run; no automatic resubmission was made.`;
    finishManualJob();
  } finally { manualSubmitting = false; }
}
$('check').addEventListener('click', startManualJob);
window.addEventListener('resize', drawChart);
initializeNotifications(); initializeDiscovery(); initializeNetworkMap(); initializeAdvancedAnalytics(); load(); setInterval(load, 10000);

