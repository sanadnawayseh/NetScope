const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function dashboard() {
  const nodes = new Map();
  const requests = [];
  const pendingInitial = [];
  const timers = new Map(); let timerId = 0;
  function node() {
    return {children: [], drawCalls: [], attributes: {}, listeners: {}, scrolls: [], focuses: [], textContent: '', className: '', append(...items) {this.children.push(...items);},
      replaceChildren(...items) {this.children = items;}, setAttribute(name,value) {this.attributes[name]=value;}, addEventListener(event, handler) {this.listeners[event]=handler;},
      scrollIntoView(options) {this.scrolls.push(options);}, focus(options) {this.focuses.push(options);},
      getBoundingClientRect() {return {width: 700};},
      getContext() {const calls=this.drawCalls; return new Proxy({}, {get: (_, method) => (...args) => {calls.push([method, ...args]);}});}};
  }
  const context = vm.createContext({document: {
    getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
    createElement: node}, window: {addEventListener() {}, devicePixelRatio:1, matchMedia() {return {matches:false};}},
    fetch: path => {requests.push(path); return new Promise(resolve => pendingInitial.push(resolve));}, AbortSignal, setInterval() {},
    setTimeout(callback) {timers.set(++timerId, callback); return timerId;}, clearTimeout(id) {timers.delete(id);}, console});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8'), context);
  const text = n => [n.textContent, ...n.children.map(text)].join(' ');
  return {context, nodes, text, requests, pendingInitial, timers};
}

test('TCP timeout is explicitly TCP and ICMP displays actual RTT', () => {
  const {context, nodes, text} = dashboard();
  vm.runInContext(`devices=[{id:1,name:'Router',ip:'192.168.1.1',port:80,ping_ok:1,tcp_ok:0,latency_ms:12,ping_error:null,tcp_error:'Connection timed out'}];selectedId ??= devices[0]?.id ?? null;render();`, context);
  assert.match(text(nodes.get('device-checks')), /Responding · 12 ms/);
  assert.match(text(nodes.get('device-checks')), /TCP \/ 80: Connection timed out/);
  assert.doesNotMatch(text(nodes.get('device-checks')), /ICMP:|device failure/i);
  assert.equal(nodes.get('latency').textContent, '12');
});

test('ICMP failure remains separate from an open TCP service; missing RTT is N/A', () => {
  const {context, nodes, text} = dashboard();
  vm.runInContext(`devices=[{id:1,name:'Router',ip:'192.168.1.1',port:80,ping_ok:0,tcp_ok:1,latency_ms:null,ping_error:'Destination unreachable',tcp_error:null}];selectedId ??= devices[0]?.id ?? null;render();`, context);
  assert.match(text(nodes.get('device-checks')), /ICMP: Destination unreachable/);
  assert.match(text(nodes.get('device-checks')), /● Open/);
  assert.match(text(nodes.get('device-checks')), /No response · N\/A/);
  assert.equal(nodes.get('latency').textContent, 'N/A');
});

test('submillisecond bounds are shown honestly and excluded from exact RTT chart averages', () => {
  const {context, nodes, text} = dashboard();
  vm.runInContext(`devices=[{id:1,name:'Local',ip:'127.0.0.1',port:80,ping_ok:1,tcp_ok:1,latency_ms:null,latency_upper_bound_ms:1}];history=[{checked_at:'2026-10-14T00:00:00Z',ping_ok:1,tcp_ok:1,latency_ms:null,latency_upper_bound_ms:1}];selectedId ??= devices[0]?.id ?? null;render();drawChart();`, context);
  assert.match(text(nodes.get('device-checks')), /Responding · <1 ms/);
  assert.match(text(nodes.get('history-stats')), /Mean RTT N\/A/);
  assert.match(nodes.get('chart-status').textContent, /No exact RTT samples/);
});


test('device inspector shows real zero loss without mistaking it for missing data', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("devices=[{id:1,name:'Local',ip:'127.0.0.1',port:80,ping_ok:1,tcp_ok:1,latency_ms:2,icmp_sent:5,icmp_received:5,packet_loss_pct:0}];selectedId ??= devices[0]?.id ?? null;render();",context);
  assert.match(text(nodes.get('device-checks')), /0% · 5\/5 replies/);
});

test('100 percent ICMP loss does not hide an open TCP service or invent RTT', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("devices=[{id:1,name:'Local',ip:'127.0.0.1',port:80,ping_ok:0,tcp_ok:1,latency_ms:null,icmp_sent:5,icmp_received:0,packet_loss_pct:100}];selectedId ??= devices[0]?.id ?? null;render();",context);
  assert.match(text(nodes.get('device-checks')), /100% · 0\/5 replies/);
  assert.match(text(nodes.get('device-checks')), /No response · N\/A/);
  assert.match(text(nodes.get('device-checks')), /● Open/);
});

test('packet loss chart plots database records on a fixed 0 to 100 axis with gaps', () => {
  const {context,nodes}=dashboard();
  vm.runInContext("history=[0,null,40,100].map((loss)=>({checked_at:'2026-10-14T00:00:00Z',packet_loss_pct:loss}));drawLossChart();",context);
  const calls=nodes.get('loss-plot').drawCalls;
  const points=calls.filter(c=>c[0]==='arc');
  assert.equal(points.length,3);
  assert.equal(points[0][2],188);
  assert.equal(points[2][2],15);
  assert.equal(calls.filter(c=>c[0]==='lineTo').length,6); // Five grid lines, one segment after the gap.
  assert.match(nodes.get('loss-chart-status').textContent,/3 recorded packet-loss measurements · latest 100%/);
});

test('historical rows without packet counts remain unavailable', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("devices=[{id:1,name:'Old',ip:'127.0.0.1',port:80,ping_ok:1,tcp_ok:1,packet_loss_pct:null}];history=[{checked_at:'2026-10-14',packet_loss_pct:null}];selectedId ??= devices[0]?.id ?? null;render();drawLossChart();",context);
  assert.match(text(nodes.get('device-checks')),/ICMP PACKET LOSS N\/A/);
  assert.equal(nodes.get('loss-plot').drawCalls.filter(c=>c[0]==='arc').length,0);
  assert.match(nodes.get('loss-chart-status').textContent,/older records remain N\/A/);
});


function historyButton(nodes, index) {
  const name = index === 0 ? 'Local computer' : 'Homer Router';
  const card=nodes.get('targets').children.find(row=>row.children[0].children[0].textContent===name);
  const descendants=n=>[n,...n.children.flatMap(descendants)];
  return descendants(card).find(n=>n.textContent==='View history');
}
function seedDevices(context) {
  vm.runInContext("devices=[{id:1,name:'Local computer',ip:'127.0.0.1',port:80,ping_ok:1,tcp_ok:0},{id:2,name:'Homer Router',ip:'192.168.1.1',port:80,ping_ok:1,tcp_ok:1}];selectedId=1;selectedId ??= devices[0]?.id ?? null;render();",context);
}

test('manual job submits once, renders real progress, refreshes and stops terminal polling', async () => {
  const {context,nodes,timers}=dashboard();
  const calls=[];let refreshes=0;
  context.load=async()=>{refreshes++;};
  const states=[{state:'running',total:2,completed:0,failed:0,progress_percentage:0},
    {state:'running',total:2,completed:1,failed:0,progress_percentage:50},
    {state:'completed',total:2,completed:2,failed:0,progress_percentage:100}];
  context.api=async(path)=>{calls.push(path);return path==='/api/check-jobs'?{job_id:'one'}:states.shift();};
  const submit=nodes.get('check').listeners.click();
  await nodes.get('check').listeners.click();
  await submit;
  assert.equal(calls.filter(path=>path==='/api/check-jobs').length,1);
  assert.equal(nodes.get('check').disabled,true);
  assert.match(nodes.get('check-progress').textContent,/0\/2 processed/);
  async function tick(){const [id,callback]=timers.entries().next().value;timers.delete(id);await callback();}
  await tick();
  assert.match(nodes.get('check-progress').textContent,/1\/2 processed · 50%/);
  assert.equal(nodes.get('check').textContent,'Checking · 50%');
  await nodes.get('check').listeners.click();
  assert.equal(calls.filter(path=>path==='/api/check-jobs').length,1);
  await tick();
  assert.match(nodes.get('check-progress').textContent,/completed · 2\/2 processed · 100%/);
  assert.equal(nodes.get('check').disabled,false);
  assert.equal(nodes.get('check').textContent,'Run checks');
  assert.equal(timers.size,0);
  assert.ok(refreshes>=3);
});

test('manual job busy and submission failure restore button without automatic resubmission', async () => {
  for(const status of [409,503]){
    const {context,nodes,timers}=dashboard();let requests=0;
    context.api=async()=>{requests++;const error=new Error('Busy or unavailable');error.status=status;throw error;};
    await nodes.get('check').listeners.click();
    assert.equal(requests,1);assert.equal(nodes.get('check').disabled,false);assert.equal(timers.size,0);
    assert.match(nodes.get('check-progress').textContent,/Busy or unavailable/);
  }
});

test('manual job transient polling error retries without posting another job', async () => {
  const {context,nodes,timers}=dashboard();let posts=0,reads=0;
  context.load=async()=>{};
  context.api=async path=>{
    if(path==='/api/check-jobs'){posts++;return {job_id:'one'};}
    if(++reads===1)throw new Error('network');
    return {state:'completed',total:0,completed:0,failed:0,progress_percentage:100};
  };
  await nodes.get('check').listeners.click();
  assert.match(nodes.get('check-progress').textContent,/Retrying/);
  assert.equal(nodes.get('check').disabled,true);
  const [id,callback]=timers.entries().next().value;timers.delete(id);await callback();
  assert.equal(posts,1);assert.equal(timers.size,0);assert.equal(nodes.get('check').disabled,false);
});

test('manual job partial failure and cancellation show safe terminal summary and stop polling', async () => {
  for(const state of ['failed','cancelled']){
    const {context,nodes,timers}=dashboard();context.load=async()=>{};
    context.api=async path=>path==='/api/check-jobs'?{job_id:'one'}:{state,total:2,completed:1,failed:1,progress_percentage:100,error:'Safe failure summary'};
    await nodes.get('check').listeners.click();
    assert.match(nodes.get('check-progress').textContent,/1 completed · 1 failed · Safe failure summary/);
    assert.ok(nodes.get('check-progress').textContent.startsWith(state));
    assert.equal(timers.size,0);assert.equal(nodes.get('check').disabled,false);
  }
});

test('expired manual job stops polling and explains restart or retention', async () => {
  const {context,nodes,timers}=dashboard();
  context.api=async path=>{if(path==='/api/check-jobs')return {job_id:'old'};const error=new Error('missing');error.status=404;throw error;};
  await nodes.get('check').listeners.click();
  assert.match(nodes.get('check-progress').textContent,/server restart or expire/);
  assert.equal(nodes.get('check').disabled,false);assert.equal(timers.size,0);
});

test('clicking each device history selects it, requests its records, and navigates to charts', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  const requests=[];
  context.fetch=async path=>{requests.push(path);return {ok:true,json:async()=>[]};};
  for(const [index,id,name,ip] of [[1,2,'Homer Router','192.168.1.1'],[0,1,'Local computer','127.0.0.1']]){
    await historyButton(nodes,index).onclick();
    assert.equal(vm.runInContext('selectedId',context),id);
    assert.equal(requests.at(-1),`/api/targets/${id}/history`);
    assert.equal(historyButton(nodes,index).attributes['aria-pressed'],'true');
    assert.match(nodes.get('targets').children.find(row=>row.children[0].children[0].textContent===name).className,/selected/);
    for(const label of ['history-label','loss-history-label']){
      assert.ok(nodes.get(label).textContent.includes(name));
      assert.ok(nodes.get(label).textContent.includes(ip));
    }
    assert.equal(nodes.get('diagnostics').scrolls.at(-1).behavior,'smooth');
    assert.equal(nodes.get('diagnostics').scrolls.at(-1).block,'start');
    assert.equal(nodes.get('diagnostics').focuses.at(-1).preventScroll,true);
  }
});

test('empty history clears previous charts and keeps the selected device visible', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  vm.runInContext("history=[{checked_at:'2026-10-14',ping_ok:1,latency_ms:4,packet_loss_pct:0}];drawChart();",context);
  context.fetch=async()=>({ok:true,json:async()=>[]});
  await historyButton(nodes,1).onclick();
  assert.equal(vm.runInContext('history.length',context),0);
  assert.match(nodes.get('chart-status').textContent,/No measurements yet/);
  assert.match(nodes.get('loss-chart-status').textContent,/No packet-loss measurements/);
  assert.match(nodes.get('history-label').textContent,/Homer Router/);
  assert.equal(nodes.get('diagnostics').scrolls.length,1);
  assert.match(nodes.get('plot').attributes['aria-label'],/0 exact RTT samples from 0 checks/);
});

test('router exact RTT is drawn while localhost bounds never create an exact point', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  context.fetch=async path=>({ok:true,json:async()=>[{checked_at:'2026-10-14',ping_ok:1,tcp_ok:1,packet_loss_pct:0,
    latency_ms:path.includes('/2/')?4.8:null,latency_upper_bound_ms:path.includes('/1/')?1:null}]});
  nodes.get('plot')?.drawCalls.splice(0);
  await historyButton(nodes,1).onclick();
  assert.equal(nodes.get('plot').drawCalls.filter(c=>c[0]==='arc').length,1);
  assert.match(nodes.get('plot').attributes['aria-label'],/Minimum 4.8 milliseconds/);
  nodes.get('plot').drawCalls.splice(0);
  await historyButton(nodes,0).onclick();
  assert.equal(nodes.get('plot').drawCalls.filter(c=>c[0]==='arc').length,0);
  assert.match(nodes.get('chart-status').textContent,/No exact RTT samples/);
});

test('latest selection wins even when A to B to A requests finish out of order', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  const pending=[];
  context.fetch=path=>new Promise(resolve=>pending.push({path,resolve}));
  const oldLocal=historyButton(nodes,0).onclick();
  const router=historyButton(nodes,1).onclick();
  const newLocal=historyButton(nodes,0).onclick();
  const finish=(index,value)=>pending[index].resolve({ok:true,json:async()=>[{checked_at:'2026-10-14',ping_ok:1,latency_ms:value}]});
  finish(2,null);await newLocal;
  finish(1,8);await router;
  finish(0,99);await oldLocal;
  assert.equal(vm.runInContext('selectedId',context),1);
  assert.equal(vm.runInContext('history[0].latency_ms',context),null);
  assert.match(nodes.get('history-label').textContent,/Local computer/);
});

test('history navigation honors reduced motion', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  context.window.matchMedia=()=>({matches:true});
  context.fetch=async()=>({ok:true,json:async()=>[]});
  await historyButton(nodes,1).onclick();
  assert.equal(nodes.get('diagnostics').scrolls.at(-1).behavior,'instant');
});

test('history errors appear at the navigated charts and keep the chosen device', async () => {
  const {context,nodes}=dashboard(); seedDevices(context);
  context.fetch=async()=>({ok:false,status:500,json:async()=>({error:'Database unavailable'})});
  await historyButton(nodes,1).onclick();
  assert.match(nodes.get('chart-status').textContent,/History unavailable: Database unavailable/);
  assert.match(nodes.get('loss-chart-status').textContent,/History unavailable/);
  assert.match(nodes.get('history-label').textContent,/Homer Router/);
  assert.equal(nodes.get('diagnostics').scrolls.length,1);
});


test('availability percentages include protocol-specific sample counts and windows', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("devices=[{id:1,name:'Local',ip:'127.0.0.1',port:80,availability:{icmp:{percentage:66.6666667,successful_checks:2,valid_checks:3,excluded_checks:2,window_start:'2026-10-14T10:00:00Z',window_end:'2026-10-14T11:00:00Z'},tcp:{percentage:0,successful_checks:0,valid_checks:4,excluded_checks:1,window_start:'2026-10-14T10:10:00Z',window_end:'2026-10-14T11:00:00Z'}}}];selectedId ??= devices[0]?.id ?? null;render();",context);
  const descendants=n=>[n,...n.children.flatMap(descendants)];
  const metrics=descendants(nodes.get('device-availability')).filter(n=>n.className==='availability-metric');
  assert.equal(metrics.length,2);
  assert.match(text(metrics[0]),/ICMP check availability 66.667% · 2\/3 valid checks/);
  assert.match(text(metrics[1]),/TCP check availability 0% · 0\/4 valid checks/);
  for(const metric of metrics){
    assert.match(text(metric),/Observation window:.*2026/);
    assert.match(text(metric),/excluded records · latest 60 stored cycles/);
  }
  assert.doesNotMatch(text(nodes.get('device-checks')),/uptime/i);
});

test('missing availability shows N/A and zero qualified samples rather than zero percent', () => {
  const {context,nodes,text}=dashboard();seedDevices(context);
  assert.match(text(nodes.get('device-availability')),/ICMP check availability N\/A · 0\/0 valid checks/);
  assert.match(text(nodes.get('device-availability')),/TCP check availability N\/A · 0\/0 valid checks/);
  assert.match(text(nodes.get('device-availability')),/Observation window: no qualifying checks/);
});

test('success failure timeline preserves independent unknown protocol states', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("history=[{checked_at:'2026-10-14T10:00:00Z',icmp_check_status:'success',tcp_check_status:'failure'},{checked_at:'2026-10-14T11:00:00Z',icmp_check_status:'unknown',tcp_check_status:'success'},{checked_at:'2026-10-14T12:00:00Z',icmp_check_status:'failure',tcp_check_status:'unrecognized'}];drawCheckTimeline();",context);
  const rows=nodes.get('check-timeline').children;
  const icmp=rows[0].children[1].children, tcp=rows[1].children[1].children;
  assert.deepEqual(icmp.map(n=>n.className),['timeline-mark success','timeline-mark unknown','timeline-mark failure']);
  assert.deepEqual(tcp.map(n=>n.className),['timeline-mark failure','timeline-mark success','timeline-mark unknown']);
  assert.match(icmp[1].attributes['aria-label'],/unknown \(excluded\)/);
  assert.equal(tcp[2].textContent,'?');
  assert.match(nodes.get('timeline-status').textContent,/3 stored cycles.*oldest to newest/);
});

test('empty and failed selected history also clear the availability timeline', async () => {
  const {context,nodes}=dashboard();seedDevices(context);
  vm.runInContext("history=[{checked_at:'2026-10-14T10:00:00Z',icmp_check_status:'success',tcp_check_status:'success'}];drawCheckTimeline();",context);
  context.fetch=async()=>({ok:true,json:async()=>[]});
  await historyButton(nodes,1).onclick();
  assert.match(nodes.get('availability-history-label').textContent,/Homer Router.*192.168.1.1/);
  assert.match(nodes.get('timeline-status').textContent,/No recorded cycles yet/);
  assert.equal(nodes.get('check-timeline').children[0].children[1].children.length,0);
  context.fetch=async()=>({ok:false,status:500,json:async()=>({error:'Database unavailable'})});
  await historyButton(nodes,0).onclick();
  assert.match(nodes.get('timeline-status').textContent,/History unavailable/);
  assert.match(nodes.get('availability-history-label').textContent,/Local computer.*127.0.0.1/);
});


test('DNS form displays actual response families and lookup duration without changing selection', async () => {
  const {context,nodes,text}=dashboard();seedDevices(context);
  context.document.getElementById('dns-domain').value='example.com';
  const requests=[];
  context.fetch=async(path,options)=>{requests.push([path,JSON.parse(options.body)]);return {ok:true,json:async()=>({ok:true,domain:'example.com',duration_ms:12.25,a:['8.8.8.8'],aaaa:['2606:4700:4700::1111']})};};
  await vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.equal(requests.length,1);assert.equal(requests[0][0],'/api/dns');
  assert.equal(requests[0][1].domain,'example.com');
  assert.match(text(nodes.get('dns-results')),/A · IPv4.*8.8.8.8/);
  assert.match(text(nodes.get('dns-results')),/AAAA · IPv6.*2606:4700:4700::1111/);
  assert.match(nodes.get('dns-status').textContent,/12.25 ms lookup duration/);
  assert.equal(vm.runInContext('selectedId',context),1);
  assert.equal(nodes.get('dns-submit').disabled,false);
});

test('DNS empty or failed lookup clears stale answers and explains failure locally', async () => {
  const {context,nodes,text}=dashboard();
  context.document.getElementById('dns-domain').value='example.com';
  context.fetch=async()=>({ok:true,json:async()=>({ok:true,domain:'example.com',duration_ms:1,a:['8.8.8.8'],aaaa:[]})});
  await vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.match(text(nodes.get('dns-results')),/No addresses returned for this family/);
  context.fetch=async()=>({ok:true,json:async()=>({ok:false,domain:'example.com',duration_ms:2,code:'no_addresses',error:'No IPv4 or IPv6 addresses found',a:[],aaaa:[]})});
  await vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.equal(nodes.get('dns-results').children.length,0);
  assert.match(nodes.get('dns-status').textContent,/No IPv4 or IPv6 addresses found/);
});

test('DNS rate errors stay separate from monitoring and restore submit controls', async () => {
  const {context,nodes}=dashboard();
  context.document.getElementById('dns-domain').value='example.com';
  context.fetch=async()=>({ok:false,status:429,json:async()=>({error:'DNS lookup limit reached'})});
  await vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.match(nodes.get('dns-status').textContent,/DNS lookup failed: DNS lookup limit reached/);
  assert.equal(nodes.get('dns-submit').disabled,false);
  assert.equal(nodes.get('dns-submit').textContent,'Look up DNS');
  assert.equal(nodes.has('global-error'),false);
});

test('DNS submission prevents duplicate in-flight requests', async () => {
  const {context,nodes}=dashboard();let finish,calls=0;
  context.document.getElementById('dns-domain').value='example.com';
  context.fetch=()=>{calls++;return new Promise(resolve=>{finish=resolve;});};
  const first=vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.equal(nodes.get('dns-submit').disabled,true);
  await vm.runInContext('runDNSLookup({preventDefault(){}})',context);
  assert.equal(calls,1);
  finish({ok:true,json:async()=>({ok:false,domain:'example.com',duration_ms:1,error:'No addresses',a:[],aaaa:[]})});
  await first;
  assert.equal(nodes.get('dns-submit').disabled,false);
});


test('traceroute never starts automatically and requires explicit destination authorization', async () => {
  const {context,nodes,requests}=dashboard();
  assert.deepEqual(requests,['/api/targets']);
  context.document.getElementById('trace-authorized').checked=false;
  let calls=0;context.fetch=async()=>{calls++;};
  await vm.runInContext('runTrace({preventDefault(){}})',context);
  assert.equal(calls,0);
  assert.match(nodes.get('trace-status').textContent,/permission/);
});

test('traceroute table preserves reported bounds and all three timeout measurements', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("renderTrace({status:'reached',destination:'10.1.2.3',duration_ms:100,message:'Destination reached.',hops:[{hop:1,ip:'192.168.1.1',rtts:['<1 ms','2 ms','*']},{hop:2,ip:null,rtts:['*','*','*']},{hop:3,ip:'10.1.2.3',rtts:['10 ms','11 ms','12 ms']}]})",context);
  assert.match(nodes.get('trace-status').textContent,/Destination reached/);
  const table=nodes.get('trace-results').children[0];
  assert.match(text(table),/Hop Responding IPv4 Probe 1 Probe 2 Probe 3/);
  const rows=table.children[2].children;
  assert.equal(rows.length,3);assert.equal(rows[0].children[2].textContent,'<1 ms');
  assert.equal(rows[1].children[1].textContent,'No reply');
  assert.deepEqual(rows[1].children.slice(2).map(n=>n.textContent),['*','*','*']);
});

test('traceroute shows incomplete and command failure distinctly and clears previous rows', () => {
  const {context,nodes}=dashboard();
  vm.runInContext("renderTrace({status:'incomplete',destination:'127.0.0.1',duration_ms:10,message:'Not confirmed.',hops:[{hop:1,ip:null,rtts:['*','*','*']}]})",context);
  assert.match(nodes.get('trace-status').textContent,/Incomplete trace/);
  vm.runInContext("renderTrace({status:'command_failure',destination:'127.0.0.1',duration_ms:1,message:'Command unavailable.',hops:[]})",context);
  assert.match(nodes.get('trace-status').textContent,/Command failure/);
  assert.equal(nodes.get('trace-results').children.length,0);
});

test('manual traceroute submits only bounded options and does not change device selection', async () => {
  const {context,nodes}=dashboard();seedDevices(context);
  context.document.getElementById('trace-ip').value='127.0.0.1';
  context.document.getElementById('trace-hops').value='12';
  context.document.getElementById('trace-authorized').checked=true;
  const calls=[];
  context.fetch=async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return {ok:true,json:async()=>({status:'reached',destination:'127.0.0.1',duration_ms:1,message:'Destination reached.',hops:[{hop:1,ip:'127.0.0.1',rtts:['<1 ms','<1 ms','<1 ms']}]})};};
  await vm.runInContext('runTrace({preventDefault(){}})',context);
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/traceroute');
  assert.deepEqual(calls[0][1],{ip:'127.0.0.1',max_hops:12,authorized:true});
  assert.equal(vm.runInContext('selectedId',context),1);
  assert.equal(nodes.get('trace-authorized').checked,false);
  assert.equal(nodes.get('trace-submit').disabled,false);
});

test('traceroute rejects duplicate submissions while a request is in flight', async () => {
  const {context,nodes}=dashboard();let finish,calls=0;
  context.document.getElementById('trace-ip').value='127.0.0.1';
  context.document.getElementById('trace-hops').value='12';
  context.document.getElementById('trace-authorized').checked=true;
  context.fetch=()=>{calls++;return new Promise(resolve=>{finish=resolve;});};
  const first=vm.runInContext('runTrace({preventDefault(){}})',context);
  assert.equal(nodes.get('trace-submit').disabled,true);
  await vm.runInContext('runTrace({preventDefault(){}})',context);assert.equal(calls,1);
  finish({ok:true,json:async()=>({status:'incomplete',destination:'127.0.0.1',duration_ms:1,message:'Not confirmed.',hops:[]})});
  await first;assert.equal(nodes.get('trace-submit').disabled,false);
});

test('traceroute API errors stay local and restore controls', async () => {
  const {context,nodes}=dashboard();
  context.document.getElementById('trace-ip').value='127.0.0.1';
  context.document.getElementById('trace-hops').value='12';
  context.document.getElementById('trace-authorized').checked=true;
  context.fetch=async()=>({ok:false,status:429,json:async()=>({error:'A traceroute is already running'})});
  await vm.runInContext('runTrace({preventDefault(){}})',context);
  assert.match(nodes.get('trace-status').textContent,/already running/);
  assert.equal(nodes.get('trace-submit').disabled,false);
  assert.equal(nodes.has('global-error'),false);
});


test('history failure does not mark a successful target refresh disconnected and survives redraw', async () => {
  const {context,nodes,pendingInitial}=dashboard();
  context.fetch=async()=>({ok:false,status:503,json:async()=>({error:'History database unavailable'})});
  pendingInitial[0]({ok:true,json:async()=>[{id:1,name:'Local',ip:'127.0.0.1',port:80}]});
  await vm.runInContext('load()',context);
  assert.match(nodes.get('connection').textContent,/Backend connected/);
  assert.match(nodes.get('chart-status').textContent,/History unavailable/);
  vm.runInContext('drawChart()',context);
  assert.match(nodes.get('chart-status').textContent,/History unavailable/);
  assert.match(nodes.get('timeline-status').textContent,/History unavailable/);
});

test('mutation refresh waits for an active poll then fetches fresh device state', async () => {
  const {context,nodes,pendingInitial}=dashboard();const requests=[];
  context.fetch=async path=>{requests.push(path);return {ok:true,json:async()=>path==='/api/targets'?[{id:2,name:'New',ip:'127.0.0.2',port:80}]:[]};};
  const refresh=vm.runInContext('load(true)',context);
  pendingInitial[0]({ok:true,json:async()=>[{id:1,name:'Old',ip:'127.0.0.1',port:80}]});
  await refresh;
  assert.equal(vm.runInContext('devices[0].id',context),2);
  assert.equal(requests.filter(p=>p==='/api/targets').length,1);
  assert.match(nodes.get('history-label').textContent,/New.*127.0.0.2/);
});

test('unknown incomplete probes are not displayed as confirmed device failures', () => {
  const {context,nodes,text}=dashboard();
  vm.runInContext("devices=[{id:1,name:'Unknown',ip:'127.0.0.1',port:80,checked_at:'2026-10-14T00:00:00Z',ping_ok:0,tcp_ok:0,icmp_check_status:'unknown',tcp_check_status:'unknown'}];selectedId ??= devices[0]?.id ?? null;render();",context);
  assert.match(text(nodes.get('device-checks')),/Unknown \/ incomplete/);
  assert.match(text(nodes.get('device-checks')),/Unknown \/ unavailable/);
  assert.doesNotMatch(text(nodes.get('device-checks')),/No response/);
});

test('table sorting uses numeric IPv4, RTT and loss values, keeps unknowns last, and preserves selection', () => {
  const {context,nodes} = dashboard();
  vm.runInContext(`devices=[
    {id:1,name:'Zulu',ip:'10.0.0.10',port:80,latency_ms:null,latency_upper_bound_ms:1,packet_loss_pct:null},
    {id:2,name:'Alpha',ip:'10.0.0.2',port:443,latency_ms:12,packet_loss_pct:100},
    {id:3,name:'Beta',ip:'127.0.0.1',port:22,latency_ms:2,packet_loss_pct:0}];selectedId=1;render();`, context);
  const order=()=>nodes.get('targets').children.map(row=>row.children[0].children[0].textContent);
  assert.deepEqual(order(),['Alpha','Beta','Zulu']);
  vm.runInContext("sortDevices('ip')",context);
  assert.deepEqual(order(),['Alpha','Zulu','Beta']);
  assert.equal(nodes.get('sort-heading-ip').attributes['aria-sort'],'ascending');
  vm.runInContext("sortDevices('ip')",context);
  assert.deepEqual(order(),['Beta','Zulu','Alpha']);
  assert.equal(nodes.get('sort-heading-ip').attributes['aria-sort'],'descending');
  vm.runInContext("sortDevices('rtt')",context);
  assert.deepEqual(order(),['Beta','Alpha','Zulu']);
  vm.runInContext("sortDevices('rtt')",context);
  assert.deepEqual(order(),['Alpha','Beta','Zulu']);
  vm.runInContext("sortDevices('loss')",context);
  assert.deepEqual(order(),['Beta','Alpha','Zulu']);
  assert.equal(vm.runInContext('selectedId',context),1);
  assert.equal(nodes.get('detail-name').textContent,'Zulu');
  assert.equal(nodes.get('sort-heading-rtt').attributes['aria-sort'],'none');
  assert.equal(vm.runInContext('devices[0].id',context),1); // Presentation sort never mutates backend order.
});

test('table status sorting keeps independent unknown protocols last and timestamps chronological', () => {
  const {context,nodes}=dashboard();
  vm.runInContext(`devices=[
    {id:1,name:'Unknown',ip:'127.0.0.1',port:80,ping_ok:0,tcp_ok:1,icmp_check_status:'unknown',tcp_check_status:'success',checked_at:'2026-10-14T12:00:00Z'},
    {id:2,name:'Failure',ip:'127.0.0.2',port:80,ping_ok:0,tcp_ok:0,checked_at:'2026-10-14T09:00:00Z'},
    {id:3,name:'Success',ip:'127.0.0.3',port:80,ping_ok:1,tcp_ok:null,checked_at:null}];render();sortDevices('icmp');`,context);
  const order=()=>nodes.get('targets').children.map(row=>row.children[0].children[0].textContent);
  assert.deepEqual(order(),['Failure','Success','Unknown']);
  vm.runInContext("sortDevices('tcp')",context);
  assert.deepEqual(order(),['Failure','Unknown','Success']);
  vm.runInContext("sortDevices('checked')",context);
  assert.deepEqual(order(),['Failure','Unknown','Success']);
  vm.runInContext("sortDevices('checked')",context);
  assert.deepEqual(order(),['Unknown','Failure','Success']);
});

test('device-name selection opens actual history, updates inspector, prefills trace and resets consent without running diagnostics', async () => {
  const {context,nodes,text}=dashboard();seedDevices(context);
  const requests=[];
  context.fetch=async path=>{requests.push(path);return {ok:true,json:async()=>[]};};
  vm.runInContext("$('trace-authorized').checked=true",context);
  const router=nodes.get('targets').children.find(row=>row.children[0].children[0].textContent==='Homer Router');
  await router.children[0].children[0].onclick();
  assert.deepEqual(requests,['/api/targets/2/history']);
  assert.equal(nodes.get('detail-name').textContent,'Homer Router');
  assert.equal(nodes.get('detail-address').textContent,'192.168.1.1 : 80');
  assert.equal(nodes.get('trace-ip').value,'192.168.1.1');
  assert.equal(nodes.get('trace-authorized').checked,false);
  assert.match(text(nodes.get('device-checks')),/TCP \/ 80/);
  assert.equal(nodes.get('device-availability').children.length,2);
  assert.equal(nodes.get('diagnostics').focuses.length,1);
  assert.match(nodes.get('chart-status').textContent,/No measurements yet/);
});

test('selecting a device leaves an in-flight traceroute destination untouched', async () => {
  const {context,nodes}=dashboard();seedDevices(context);
  context.fetch=async()=>({ok:true,json:async()=>[]});
  nodes.get('trace-ip').value='127.0.0.9';
  vm.runInContext('traceBusy=true',context);
  await historyButton(nodes,1).onclick();
  assert.equal(nodes.get('trace-ip').value,'127.0.0.9');
  assert.equal(vm.runInContext('selectedId',context),2);
});

test('empty device list renders a valid table row and clears inspector data', () => {
  const {context,nodes,text}=dashboard();seedDevices(context);
  vm.runInContext('devices=[];selectedId=null;render();',context);
  const row=nodes.get('targets').children[0];
  assert.equal(row.children[0].attributes.colspan,'8');
  assert.match(text(row),/No devices yet/);
  assert.equal(nodes.get('detail-name').textContent,'Select a device');
  assert.equal(nodes.get('device-checks').children.length,0);
  assert.equal(nodes.get('device-availability').children.length,0);
});

test('all table columns retain true zero values, bounds and protocol-specific failures', () => {
  const {context,nodes}=dashboard();
  vm.runInContext(`devices=[{id:1,name:'Local',ip:'127.0.0.1',port:443,ping_ok:1,tcp_ok:0,latency_ms:null,latency_upper_bound_ms:1,packet_loss_pct:0,icmp_received:5,icmp_sent:5,tcp_error:'Connection refused (port is not accepting connections)'}];render();`,context);
  const cells=nodes.get('targets').children[0].children;
  assert.equal(cells.length,8);
  assert.equal(cells[2].textContent,'Responding');
  assert.equal(cells[3].textContent,'Refused / 443');
  assert.match(cells[3].attributes.title,/TCP \/ 443: Connection refused/);
  assert.equal(cells[4].textContent,'<1 ms');
  assert.equal(cells[5].textContent,'0%');
  assert.equal(cells[5].attributes.title,'5/5 Echo Replies');
  assert.equal(cells[6].textContent,'Not checked');
});

test('sorting does not mix remove actions between devices', async () => {
  const {context,nodes}=dashboard();seedDevices(context);
  context.confirm=()=>true;
  const calls=[];
  context.fetch=async(path,options)=>{calls.push([path,options?.method]);return {ok:true,status:204};};
  vm.runInContext('load=async()=>{}',context);
  const router=nodes.get('targets').children.find(row=>row.children[0].children[0].textContent==='Homer Router');
  await router.children[7].children[1].onclick();
  assert.deepEqual(calls,[['/api/targets/2','DELETE']]);
});

test('initial backend failure keeps a valid table error state and no invented device rows', async () => {
  const {context,nodes,text,pendingInitial}=dashboard();
  pendingInitial[0]({ok:false,status:503,json:async()=>({error:'Database unavailable'})});
  await vm.runInContext('loadPromise',context);
  const row=nodes.get('targets').children[0];
  assert.equal(row.children[0].attributes.colspan,'8');
  assert.match(text(row),/Cannot load devices/);
  assert.match(nodes.get('global-error').textContent,/Database unavailable/);
  assert.equal(vm.runInContext('devices.length',context),0);
});

test('Add device disclosure toggles visibility, updates ARIA and moves focus without network work', () => {
  const {nodes,requests}=dashboard();
  const panel=nodes.get('add-device-panel'), toggle=nodes.get('add-device-toggle');
  panel.hidden=true;
  const initialRequests=requests.length;
  toggle.listeners.click();
  assert.equal(panel.hidden,false);
  assert.equal(toggle.attributes['aria-expanded'],'true');
  assert.equal(nodes.get('device-name').focuses.length,1);
  toggle.listeners.click();
  assert.equal(panel.hidden,true);
  assert.equal(toggle.attributes['aria-expanded'],'false');
  assert.equal(toggle.focuses.length,1);
  assert.equal(requests.length,initialRequests);
});

test('Cancel and Escape close the device form, restore trigger focus and preserve input', () => {
  const {context,nodes}=dashboard();
  vm.runInContext('setAddDeviceOpen(true)',context);
  nodes.get('device-name').value='Unsubmitted device';
  nodes.get('add-device-close').listeners.click();
  assert.equal(nodes.get('add-device-panel').hidden,true);
  assert.equal(nodes.get('device-name').value,'Unsubmitted device');
  vm.runInContext('setAddDeviceOpen(true)',context);
  const handled=[];
  nodes.get('add-device-panel').listeners.keydown({key:'Escape',preventDefault(){handled.push('prevent');},stopPropagation(){handled.push('stop');}});
  assert.deepEqual(handled,['prevent','stop']);
  assert.equal(nodes.get('add-device-panel').hidden,true);
  assert.equal(nodes.get('add-device-toggle').attributes['aria-expanded'],'false');
  assert.equal(nodes.get('add-device-toggle').focuses.length,2);
  assert.equal(nodes.get('device-name').value,'Unsubmitted device');
});

test('device polling and sorting leave an open form and its draft intact', () => {
  const {context,nodes}=dashboard();seedDevices(context);
  vm.runInContext('setAddDeviceOpen(true)',context);
  nodes.get('device-name').value='Keep this draft';
  vm.runInContext("render();sortDevices('ip')",context);
  assert.equal(nodes.get('add-device-panel').hidden,false);
  assert.equal(nodes.get('add-device-toggle').attributes['aria-expanded'],'true');
  assert.equal(nodes.get('device-name').value,'Keep this draft');
  nodes.get('add-device-panel').listeners.keydown({key:'Enter'});
  assert.equal(nodes.get('add-device-panel').hidden,false);
});
