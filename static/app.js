const $ = id => document.getElementById(id);
let selectedId = null, devices = [], history = [], loading = false, historyRequest = 0, loadPromise = null, historyError = null, historyLoading = false;
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
}
for (const key of Object.keys(sortColumns)) $('sort-' + key).addEventListener('click', () => sortDevices(key));
async function selectHistory(id) {
  selectedId = id;
  const selected = devices.find(t => t.id === id);
  if (selected && !traceBusy) { $('trace-ip').value = selected.ip; $('trace-authorized').checked = false; }
  history = []; historyError = null; historyLoading = false;
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
      if (!devices.some(t => t.id === selectedId)) { selectedId = devices[0]?.id ?? null; history = []; }
      render();
      $('connection').textContent = `● Backend connected · Refreshed ${new Date().toLocaleTimeString()}`;
      $('global-error').hidden = true;
    } catch (e) {
      $('connection').textContent = '● Backend unavailable · Results may be stale'; failure(e);
      if (!devices.length) tableEmpty('Cannot load devices. Start the local Flask server and retry.');
      return;
    }
    // History errors have their own visible chart states. A successful target
    // refresh is still evidence of a connected backend.
    try { await drawHistory(); } catch {}
  })().finally(() => { loading = false; });
  return loadPromise;
}

async function drawHistory() {
  const id = selectedId, requestId = ++historyRequest;
  if (id === null) { history = []; historyError = null; historyLoading = false; drawChart(); return; }
  historyError = null; historyLoading = true;
  $('chart-status').textContent = 'Loading history…'; $('loss-chart-status').textContent = 'Loading history…'; $('timeline-status').textContent = 'Loading history…';
  try { const data = await api(`/api/targets/${id}/history`); if (id !== selectedId || requestId !== historyRequest) return; history = data; historyLoading = false; drawChart(); }
  catch (e) { if (id !== selectedId || requestId !== historyRequest) return; history = []; historyLoading = false; historyError = `History unavailable: ${e.message}. Select View history to retry.`; drawChart(); throw e; }
}
function drawChart() {
  drawLossChart();
  drawCheckTimeline();
  const canvas = $('plot'), ctx = canvas.getContext('2d'), scale = window.devicePixelRatio || 1;
  const width = canvas.getBoundingClientRect().width || 700, height = 220;
  canvas.width = Math.round(width * scale); canvas.height = height * scale; ctx.setTransform(scale, 0, 0, scale, 0, 0);
  const left = 48, right = 16, top = 15, bottom = 32;
  const valid = history.filter(d => d.ping_ok === 1 && Number.isFinite(d.latency_ms));
  const max = Math.max(10, ...valid.map(d => d.latency_ms)) * 1.2;
  ctx.font = '10px Segoe UI'; ctx.fillStyle = '#7f90a6'; ctx.strokeStyle = '#26313e'; ctx.lineWidth = 1;
  for (let i=0; i<4; i++) { const y = top + (height-top-bottom)*i/3; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(width-right,y); ctx.stroke(); ctx.fillText(`${Math.round(max*(1-i/3))}`, 8, y+3); }
  let previous = null;
  history.forEach((d,i) => {
    if (d.ping_ok !== 1 || !Number.isFinite(d.latency_ms)) { previous = null; return; }
    const x = left + (width-left-right)*(history.length === 1 ? .5 : i/(history.length-1)), y = top+(height-top-bottom)*(1-d.latency_ms/max);
    ctx.strokeStyle = '#65b9ae'; ctx.lineWidth = 2;
    if (previous) { ctx.beginPath(); ctx.moveTo(...previous); ctx.lineTo(x,y); ctx.stroke(); }
    ctx.fillStyle = '#65b9ae'; ctx.beginPath(); ctx.arc(x,y,2.5,0,Math.PI*2); ctx.fill(); previous = [x,y];
  });
  ctx.fillStyle = '#7f90a6';
  if (history.length) { ctx.textAlign = 'left'; ctx.fillText(new Date(history[0].checked_at).toLocaleTimeString(),left,height-7); ctx.textAlign = 'right'; ctx.fillText(new Date(history.at(-1).checked_at).toLocaleTimeString(),width-right,height-7); }
  $('chart-status').textContent = !history.length ? 'No measurements yet. Run checks to collect the first sample.' : !valid.length ? 'No exact RTT samples in this window. Responses may report a bound, have unavailable RTT, or be historical process timings. Review ICMP and TCP results separately.' : `${history.length} recorded checks · ${valid.length} exact RTT samples`;
  canvas.setAttribute('aria-label', `ICMP round-trip time: ${valid.length} exact RTT samples from ${history.length} checks. ${valid.length ? `Minimum ${fmt(Math.min(...valid.map(d=>d.latency_ms)))} milliseconds, maximum ${fmt(Math.max(...valid.map(d=>d.latency_ms)))} milliseconds.` : ''}`);
  $('history-stats').replaceChildren();
  for (const [label, value] of [['Ping responses', history.length ? `${history.filter(d=>d.ping_ok===1).length}/${history.length}` : '—'], ['TCP accepted', history.length ? `${history.filter(d=>d.tcp_ok===1).length}/${history.length}` : '—'], ['Mean RTT', valid.length ? `${fmt(valid.reduce((s,d)=>s+d.latency_ms,0)/valid.length)} ms` : 'N/A']]) { const n = element('span','',label); n.append(element('b','',value)); $('history-stats').append(n); }
  if (historyError || historyLoading) {
    for (const id of ['chart-status', 'loss-chart-status', 'timeline-status']) $(id).textContent = historyError || 'Loading history…';
  }
}
function drawLossChart() {
  const canvas=$('loss-plot'), ctx=canvas.getContext('2d'), scale=window.devicePixelRatio||1;
  const width=canvas.getBoundingClientRect().width||700, height=220, left=48, right=16, top=15, bottom=32;
  canvas.width=Math.round(width*scale); canvas.height=height*scale; ctx.setTransform(scale,0,0,scale,0,0);
  ctx.font='10px Segoe UI'; ctx.strokeStyle='#26313e'; ctx.fillStyle='#7f90a6'; ctx.lineWidth=1;
  for(const percent of [100,75,50,25,0]){
    const y=top+(height-top-bottom)*(1-percent/100);
    ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(`${percent}%`,5,y+3);
  }
  let previous=null;
  const valid=history.filter(d=>Number.isFinite(d.packet_loss_pct));
  history.forEach((d,i)=>{
    if(!Number.isFinite(d.packet_loss_pct)){previous=null;return;}
    const x=left+(width-left-right)*(history.length===1?.5:i/(history.length-1));
    const y=top+(height-top-bottom)*(1-d.packet_loss_pct/100);
    ctx.strokeStyle='#bbaa80';ctx.fillStyle='#bbaa80';ctx.lineWidth=2;
    if(previous){ctx.beginPath();ctx.moveTo(...previous);ctx.lineTo(x,y);ctx.stroke();}
    ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();previous=[x,y];
  });
  ctx.fillStyle='#7f90a6';
  if(history.length){ctx.textAlign='left';ctx.fillText(new Date(history[0].checked_at).toLocaleTimeString(),left,height-7);ctx.textAlign='right';ctx.fillText(new Date(history.at(-1).checked_at).toLocaleTimeString(),width-right,height-7);}
  $('loss-chart-status').textContent=valid.length?`${valid.length} recorded packet-loss measurements · latest ${fmt(valid.at(-1).packet_loss_pct)}%`:'No packet-loss measurements available. Run checks to collect a five-probe batch; older records remain N/A.';
  canvas.setAttribute('aria-label', `ICMP packet-loss history: ${valid.length} measurements.${valid.length?` Latest ${fmt(valid.at(-1).packet_loss_pct)} percent; minimum ${fmt(Math.min(...valid.map(d=>d.packet_loss_pct)))} percent; maximum ${fmt(Math.max(...valid.map(d=>d.packet_loss_pct)))} percent.`:''}`);
}
function drawCheckTimeline() {
  const timeline = $('check-timeline');
  timeline.replaceChildren();
  for (const [key, label] of [['icmp_check_status', 'ICMP checks'], ['tcp_check_status', 'TCP checks']]) {
    const row = element('div', 'timeline-row'), track = element('div', 'timeline-track');
    track.setAttribute('role', 'list'); track.setAttribute('aria-label', label);
    history.forEach((record, index) => {
      const state = ['success', 'failure'].includes(record[key]) ? record[key] : 'unknown';
      const text = `${label} · cycle ${index + 1} · ${observationLabel(record.checked_at)} · ${state}${state === 'unknown' ? ' (excluded)' : ''}`;
      const mark = element('span', `timeline-mark ${state}`, state === 'success' ? '+' : state === 'failure' ? '−' : '?');
      mark.setAttribute('role', 'listitem'); mark.setAttribute('tabindex', '0');
      mark.setAttribute('aria-label', text); mark.setAttribute('title', text);
      track.append(mark);
    });
    row.append(element('span', 'timeline-label', label), track); timeline.append(row);
  }
  const times = history.map(d=>d.checked_at).filter(t=>t && Number.isFinite(new Date(t).getTime())).sort((a,b)=>new Date(a)-new Date(b));
  $('timeline-status').textContent = history.length ? `${history.length} stored cycles · ${times.length ? observationLabel(times[0]) + ' → ' + observationLabel(times.at(-1)) : 'Observation times unavailable'} · oldest to newest` : 'No recorded cycles yet. Run checks to begin an observation window.';
}
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
$('check').addEventListener('click', async () => { const b = $('check'); b.disabled = true; b.textContent = 'Checking devices…'; try { await api('/api/check', {method:'POST'}); await load(true); } catch(e) { if (e.status === 409) { $('global-error').hidden = false; $('global-error').textContent = e.message; } else failure(e); } finally { b.disabled = false; b.textContent = 'Run checks'; } });
window.addEventListener('resize', drawChart);
load(); setInterval(load, 10000);

