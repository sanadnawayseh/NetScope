const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function dashboard(options={}) {
  const nodes = new Map();
  const requests = [];
  const pendingInitial = [];
  const timers = new Map(); let timerId = 0;
  function node(tag) {
    return {tagName:tag,children: [], drawCalls: [], attributes: {}, listeners: {}, scrolls: [], focuses: [], style: {}, textContent: '', className: '', append(...items) {this.children.push(...items);},
      replaceChildren(...items) {this.children = items;}, setAttribute(name,value) {this.attributes[name]=value;}, addEventListener(event, handler) {this.listeners[event]=handler;},
      scrollIntoView(options) {this.scrolls.push(options);}, focus(options) {this.focuses.push(options);}, click(){this.clicked=true;},remove(){this.removed=true;},
      getBoundingClientRect() {return {width: 700,height:420,left:0,top:0};},
      getContext() {const calls=this.drawCalls; return new Proxy({}, {get: (_, method) => (...args) => {calls.push([method, ...args]);}});}};
  }
  const context = vm.createContext({document: {
    getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
    body:node('body'),createElement: node,createElementNS(namespace,tag){return node(tag);}}, window: {addEventListener() {}, devicePixelRatio:1, matchMedia() {return {matches:false};}},
    fetch: path => {requests.push(path); return new Promise(resolve => pendingInitial.push(resolve));}, AbortSignal, setInterval() {},
    setTimeout(callback) {timers.set(++timerId, callback); return timerId;}, clearTimeout(id) {timers.delete(id);}, console,...options});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/reports.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/analytics.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/network-map.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/discovery.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/notifications.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8'), context);
  for(const id of ['discovery-authorized','discovery-name','discovery-port'])context.document.getElementById(id);
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

test('in-app notifications survive reload, deduplicate repeated polls and open incident links',async()=>{
  const data=notificationFixture();
  for(let reload=0;reload<2;reload++){
    const {context,nodes}=dashboard();const paths=[];
    context.api=async(path,options)=>{
      paths.push(path);
      if(options?.method==='POST')return {id:1,read_at:'2026-10-29T00:00:00Z',unread_count:0};
      if(path==='/api/incidents/1')return incidentFixture().record;
      return data;
    };
    await vm.runInContext('loadNotifications()',context);
    await vm.runInContext('loadNotifications()',context);
    assert.equal(nodes.get('notification-list').children.length,1);
    assert.match(nodes.get('notification-toggle').textContent,/1 unread/);
    await vm.runInContext('openNotificationIncident(notificationRows[0])',context);
    assert.ok(paths.includes('/api/notifications/1/read'));
    assert.ok(paths.includes('/api/incidents/1'));
    assert.equal(paths.some(path=>path.includes('acknowledge')),false);
    assert.equal(nodes.get('incidents').scrolls.length,1);
  }
});

test('older in-app events merge once and a new database stream replaces old rows',async()=>{
  const {context,nodes}=dashboard();const event=notificationFixture().events[0];
  context.api=async path=>path.includes('before_id=2')?notificationFixture([event]):{...notificationFixture([{...event,id:2}]),next_before_id:2};
  await vm.runInContext('loadNotifications()',context);
  await vm.runInContext('loadOlderNotifications()',context);
  assert.equal(nodes.get('notification-list').children.length,2);
  context.api=async()=>({...notificationFixture([{...event,id:3}]),stream_id:'b'.repeat(32)});
  await vm.runInContext('loadNotifications()',context);
  assert.equal(nodes.get('notification-list').children.length,1);
  assert.equal(vm.runInContext('notificationRows[0].id',context),3);
});

function notificationFixture(events=null){
  events=events||[{id:1,incident_id:1,target_id:1,original_target_id:1,condition:'tcp_unavailable',event_type:'incident_opened',event_at:'2026-10-23T00:00:00Z',created_at:'2026-10-23T00:00:01Z',read_at:null}];
  return {events,unread_count:events.filter(e=>!e.read_at).length,stream_id:'a'.repeat(32),latest_id:events.at(-1)?.id||0,next_after_id:events.at(-1)?.id||0,next_before_id:null,has_more:false};
}

test('notification indicator and panel render stored unread count and both event types',async()=>{
  const {context,nodes,text}=dashboard();seedDevices(context);
  const first=notificationFixture().events[0],data=notificationFixture([first,{...first,id:2,event_type:'incident_resolved',read_at:'2026-10-23T00:01:00Z'}]);
  context.api=async()=>data;
  await vm.runInContext('loadNotifications()',context);
  assert.match(nodes.get('notification-toggle').textContent,/1 unread/);
  const rows=nodes.get('notification-list').children;
  assert.match(rows[0].className,/notification-resolved.*notification-read/);
  assert.match(text(rows[1]),/Opened · Local computer · TCP service unavailable.*Unread/);
  assert.equal(nodes.get('notification-toggle').attributes['aria-label'],'Notifications, 1 unread');
});

test('notification polling uses forward cursor, merges once, and runs with dashboard updates',async()=>{
  const {context,nodes,pendingInitial}=dashboard();const paths=[],first=notificationFixture();
  context.api=async path=>{paths.push(path);if(path.startsWith('/api/notifications'))return first;if(path==='/api/incidents/summary')return incidentFixture([]).summary;if(path.startsWith('/api/incidents?'))return incidentFixture([]).data;return [];};
  pendingInitial[0]({ok:true,json:async()=>[{id:1,name:'Local',ip:'127.0.0.1',port:80}]});
  await vm.runInContext('load()',context);
  await vm.runInContext('loadNotifications()',context);
  assert.ok(paths.includes('/api/notifications?limit=50&after_id=1'));
  assert.equal(nodes.get('notification-list').children.length,1);
});

test('notification mark-read controls preserve incident lifecycle and bulk snapshot cursor',async()=>{
  const {context,nodes,text}=dashboard();let data=notificationFixture();const posts=[];
  context.api=async(path,options)=>{
    if(options?.method==='POST'){
      posts.push([path,JSON.parse(options.body)]);
      data=notificationFixture(data.events.map(e=>({...e,read_at:'2026-10-23T00:01:00Z'})));
      return {id:1,read_at:'2026-10-23T00:01:00Z',unread_count:0};
    }return data;
  };
  await vm.runInContext('loadNotifications()',context);
  await vm.runInContext('readNotificationEvent(1)',context);
  assert.equal(posts[0][0],'/api/notifications/1/read');
  assert.match(nodes.get('notification-toggle').textContent,/0 unread/);
  assert.match(text(nodes.get('notification-list')),/Read/);
  await vm.runInContext('readAllNotifications()',context);
  assert.deepEqual(posts[1],['/api/notifications/read',{up_to_id:1}]);
  assert.equal(posts.some(([path])=>path.includes('acknowledge')),false);
});

test('notification panel opens and closes with keyboard focus and empty state',async()=>{
  const {context,nodes}=dashboard();context.api=async()=>notificationFixture([]);
  vm.runInContext('setNotificationPanel(true)',context);
  await vm.runInContext('loadNotifications()',context);
  assert.equal(nodes.get('notification-toggle').attributes['aria-expanded'],'true');
  nodes.get('notification-panel').listeners.keydown({key:'Escape',preventDefault(){}});
  assert.equal(nodes.get('notification-panel').hidden,true);
  assert.equal(nodes.get('notification-toggle').focuses.length,1);
});

test('notification API and read failures preserve displayed events and recover on retry',async()=>{
  const {context,nodes}=dashboard();const data=notificationFixture();
  context.api=async()=>data;await vm.runInContext('loadNotifications()',context);
  context.api=async()=>{throw new Error('Database unavailable');};
  await vm.runInContext('loadNotifications()',context);
  assert.match(nodes.get('notification-status').textContent,/may be stale/);
  assert.equal(nodes.get('notification-list').children.length,1);
  await vm.runInContext('readNotificationEvent(1)',context);
  assert.match(nodes.get('notification-action-status').textContent,/Could not mark/);
  assert.equal(vm.runInContext('notificationReadBusy',context),false);
  context.api=async()=>data;await vm.runInContext('loadNotifications()',context);
  assert.match(nodes.get('notification-toggle').textContent,/1 unread/);
});

test('open notification panel refreshes read markers changed by another local client',async()=>{
  const {context,nodes,text}=dashboard();const data=notificationFixture();
  context.api=async()=>data;await vm.runInContext('loadNotifications()',context);
  nodes.get('notification-panel').hidden=false;
  const read={...data,events:data.events.map(e=>({...e,read_at:'2026-10-23T00:01:00Z'})),unread_count:0};
  context.api=async path=>path.includes('limit=200')?read:{...data,events:[],next_after_id:1};
  await vm.runInContext('loadNotifications()',context);
  assert.match(nodes.get('notification-toggle').textContent,/0 unread/);
  assert.match(text(nodes.get('notification-list')),/Read/);
  assert.equal(nodes.get('notification-list').children[0].children.some(n=>n.textContent==='Mark read'),false);
});

function incidentFixture(rows=null){
  const record={id:1,target_id:1,original_target_id:1,condition:'tcp_unavailable',state:'OPEN',first_observed:'2026-10-22T12:00:00Z',latest_observed:'2026-10-22T12:00:02Z',resolved_at:null,duration_seconds:null,acknowledged_at:null,operator_note:null,triggering_result_id:3,latest_result_id:3,resolving_result_id:null,triggering_measurements:{first:{result_id:1,value:1},trigger:{result_id:3,value:1}},thresholds:{threshold:0,open_checks:3,recovery_checks:2}};
  rows=rows||[record];
  const by_condition=Object.fromEntries(['icmp_unavailable','tcp_unavailable','packet_loss','high_latency'].map(c=>[c,{OPEN:0,ACKNOWLEDGED:0}]));
  for(const row of rows)if(row.state!=='RESOLVED')by_condition[row.condition][row.state]++;
  const open=rows.filter(r=>r.state==='OPEN').length,acknowledged=rows.filter(r=>r.state==='ACKNOWLEDGED').length;
  return {record,data:{incidents:rows,next_before_id:null},summary:{active:open+acknowledged,open,acknowledged,by_condition}};
}

test('incident dashboard renders real counts, device conditions, sampled spans and chronological order',async()=>{
  const {context,nodes,text}=dashboard();seedDevices(context);const {record}=incidentFixture();
  const older={...record,id:9,target_id:null,original_target_id:7,state:'RESOLVED',first_observed:'2026-10-22T10:00:00Z',resolved_at:'2026-10-22T10:01:00Z',duration_seconds:60};
  const fixture=incidentFixture([older,record]);
  context.api=async path=>path==='/api/incidents/summary'?fixture.summary:fixture.data;
  await vm.runInContext('loadIncidents()',context);
  assert.match(nodes.get('incident-overview').textContent,/1 active · 1 open · 0 acknowledged/);
  assert.match(text(nodes.get('incident-counts')),/TCP service unavailable: 1 open/);
  assert.match(text(nodes.get('incident-timeline').children[0]),/Local computer.*TCP service unavailable.*OPEN.*elapsed since first observation/);
  assert.match(text(nodes.get('incident-timeline').children[1]),/Removed device #7.*RESOLVED.*sampled span.*Recovered/);
  assert.match(text(nodes.get('incident-timeline')),/threshold > 0 · 3 opening \/ 2 recovery checks/);
});

test('incident filters request device, condition, state and range without changing inspector',async()=>{
  const {context,nodes}=dashboard();seedDevices(context);const fixture=incidentFixture();const paths=[];
  context.api=async path=>{paths.push(path);return path==='/api/incidents/summary'?fixture.summary:fixture.data;};
  nodes.get('incident-device').value='2';nodes.get('incident-condition').value='high_latency';nodes.get('incident-state').value='ACKNOWLEDGED';nodes.get('incident-range').value='6h';
  nodes.get('incident-condition').listeners.change();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(paths.some(p=>p.includes('target_id=2')&&p.includes('condition=high_latency')&&p.includes('state=ACKNOWLEDGED')&&p.includes('range=6h')));
  assert.equal(vm.runInContext('selectedId',context),1);
});

test('incident acknowledgement prevents repeated submissions and renders acknowledgement safely',async()=>{
  const {context,nodes,text}=dashboard();seedDevices(context);const fixture=incidentFixture();let current=fixture.record,resolve,posts=0,body;
  context.api=async(path,options)=>{
    if(options?.method==='POST'){posts++;body=JSON.parse(options.body);return new Promise(done=>{resolve=done;});}
    if(path==='/api/incidents/summary')return incidentFixture([current]).summary;
    if(path.startsWith('/api/incidents?'))return incidentFixture([current]).data;
    return current;
  };
  await vm.runInContext('loadIncidentDetail(1,true)',context);
  assert.equal(nodes.get('incident-detail').focuses.length,1);
  vm.runInContext("incidentNoteDraft='<script>literal note</script>'",context);
  const pending=vm.runInContext('acknowledgeSelectedIncident()',context);
  await vm.runInContext('acknowledgeSelectedIncident()',context);
  assert.equal(posts,1);assert.equal(body.note,'<script>literal note</script>');
  current={...current,state:'ACKNOWLEDGED',acknowledged_at:'2026-10-23T00:00:00Z',operator_note:body.note};resolve(current);await pending;
  assert.match(text(nodes.get('incident-detail')),/ACKNOWLEDGED.*Note: <script>literal note<\/script>/);
  assert.equal(nodes.get('incident-detail').children.some(n=>n.textContent==='Acknowledge'),false);
  assert.match(nodes.get('incident-action-status').textContent,/verified recovery continue/);
});

test('incident list and details update automatically with existing dashboard polling',async()=>{
  const {context,nodes,pendingInitial}=dashboard();const fixture=incidentFixture();const paths=[];
  context.api=async path=>{paths.push(path);if(path==='/api/incidents/summary')return fixture.summary;if(path.startsWith('/api/incidents?'))return fixture.data;if(path==='/api/incidents/1')return fixture.record;return [];};
  pendingInitial[0]({ok:true,json:async()=>[{id:1,name:'Local',ip:'127.0.0.1',port:80}]});
  await vm.runInContext('load()',context);
  assert.ok(paths.some(p=>p.startsWith('/api/incidents?')));
  assert.match(nodes.get('incident-status').textContent,/1 incidents displayed/);
});

test('incident loading, empty, API error, acknowledgement error and note drafts stay scoped',async()=>{
  const {context,nodes,text}=dashboard();const fixture=incidentFixture([]);
  context.api=async path=>path==='/api/incidents/summary'?fixture.summary:fixture.data;
  await vm.runInContext('loadIncidents()',context);
  assert.match(text(nodes.get('incident-timeline')),/No incidents match/);
  context.api=async()=>{throw new Error('Database unavailable');};
  await vm.runInContext('loadIncidents()',context);
  assert.match(nodes.get('incident-status').textContent,/may be stale/);
  const row=incidentFixture().record;context.api=async()=>row;
  await vm.runInContext('loadIncidentDetail(1)',context);
  vm.runInContext("incidentNoteDraft='unfinished note'",context);
  context.api=async()=>{throw new Error('Already resolved');};
  await vm.runInContext('acknowledgeSelectedIncident()',context);
  assert.match(nodes.get('incident-action-status').textContent,/Acknowledgement failed: Already resolved/);
  assert.equal(vm.runInContext('incidentNoteDraft',context),'unfinished note');
  assert.equal(vm.runInContext('incidentAckBusy',context),false);
});

test('incident stale filter responses cannot replace the latest list',async()=>{
  const {context,nodes,text}=dashboard();const fixture=incidentFixture();let resolve;
  context.api=async path=>path==='/api/incidents/summary'?fixture.summary:new Promise(done=>{resolve=done;});
  const pending=vm.runInContext('loadIncidents()',context);
  context.api=async path=>path==='/api/incidents/summary'?incidentFixture([]).summary:incidentFixture([]).data;
  await vm.runInContext('loadIncidents()',context);
  resolve(fixture.data);await pending;
  assert.match(text(nodes.get('incident-timeline')),/No incidents match/);
});

test('incident detail polling preserves a focused note draft and refreshes metadata after blur',async()=>{
  const {context,nodes}=dashboard();let row=incidentFixture().record;
  context.api=async()=>row;
  await vm.runInContext('loadIncidentDetail(1,true)',context);
  const heading=nodes.get('incident-detail').children[0];
  vm.runInContext("incidentNoteDraft='draft still being typed'",context);
  context.document.activeElement={id:'incident-note'};
  row={...row,latest_observed:'2026-10-22T12:00:05Z'};
  await vm.runInContext('loadIncidentDetail(1,false)',context);
  assert.equal(nodes.get('incident-detail').children[0],heading);
  context.document.activeElement=null;
  await vm.runInContext('loadIncidentDetail(1,false)',context);
  assert.notEqual(nodes.get('incident-detail').children[0],heading);
  assert.equal(vm.runInContext('incidentNoteDraft',context),'draft still being typed');
});

test('analytics timestamp spacing and interruption gaps use elapsed time on both charts', () => {
  const {context,nodes}=dashboard();
  vm.runInContext("history=[0,1,10].map(m=>({checked_at:new Date(Date.UTC(2026,9,22,12,m)).toISOString(),ping_ok:1,latency_ms:2,packet_loss_pct:0}));drawChart();",context);
  for(const id of ['plot','loss-plot']){
    const calls=nodes.get(id).drawCalls,points=calls.filter(c=>c[0]==='arc');
    assert.equal(points.length,3);
    assert.ok(Math.abs((points[1][1]-points[0][1])/(points[2][1]-points[0][1])-.1)<.001);
    const grid=id==='plot'?4:5;
    assert.equal(calls.filter(c=>c[0]==='lineTo').length,grid+1); // No line across the 9 minute interruption.
  }
  const marks=nodes.get('check-timeline').children[0].children[1].children;
  assert.deepEqual(marks.map(m=>m.style.left),['0%','10%','100%']);
});

function analyticsFixture(ids=[1,2]){
  const summary={record_count:2,valid_measurements:2,unknown_measurements:0,exact_rtt_count:1,missing_rtt_count:1,average_latency_ms:2,minimum_latency_ms:2,maximum_latency_ms:2,valid_loss_count:2,missing_loss_count:0,average_packet_loss_pct:50,
    icmp:{percentage:50,successful_checks:1,valid_checks:2,excluded_checks:0},tcp:{percentage:100,successful_checks:2,valid_checks:2,excluded_checks:0}};
  return {start:'2026-10-22T11:00:00Z',end:'2026-10-22T12:00:00Z',series:ids.map(id=>({target_id:id,name:`Device ${id}`,summary,truncated:false,points:[
    {checked_at:'2026-10-22T11:30:00Z',ping_ok:1,latency_ms:2,packet_loss_pct:0,icmp_check_status:'success',tcp_check_status:'success'},
    {checked_at:'2026-10-22T11:31:00Z',ping_ok:0,latency_ms:null,packet_loss_pct:100,icmp_check_status:'failure',tcp_check_status:'success'}]}))};
}

test('analytics comparison shares axes and shows independent sampled availability and summaries',async()=>{
  const {context,nodes,text}=dashboard();
  context.api=async()=>analyticsFixture();
  vm.runInContext('comparisonIds=new Set([1,2]);',context);
  await vm.runInContext('loadComparison()',context);
  assert.equal(nodes.get('comparison-panel').hidden,false);
  assert.match(text(nodes.get('comparison-summary')),/Device 1.*missing RTT 1.*ICMP sampled 50%.*TCP sampled 100%.*Device 2/);
  assert.equal(nodes.get('compare-rtt').drawCalls.filter(c=>c[0]==='arc').length,2); // No invented second RTT.
  assert.equal(nodes.get('compare-availability').drawCalls.filter(c=>c[0]==='strokeRect').length,4);
  const points=nodes.get('compare-loss').drawCalls.filter(c=>c[0]==='arc');
  assert.equal(points[0][1],points[2][1]);
  assert.match(nodes.get('comparison-status').textContent,/never continuous uptime/);
});

test('analytics range changes load full-window single-device statistics and retain inspector',async()=>{
  const {context,nodes,text}=dashboard();seedDevices(context);const calls=[];
  context.api=async path=>{calls.push(path);return analyticsFixture([1]);};
  nodes.get('history-range').value='6h';
  nodes.get('history-range').listeners.change();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('/api/history?targets=1&range=6h'));
  assert.match(text(nodes.get('history-stats')),/2 records.*exact RTT 1.*mean loss 50%/);
  assert.match(nodes.get('history-label').textContent,/Local computer.*6h/);
  assert.equal(vm.runInContext('selectedId',context),1);
});

test('analytics comparison refreshes automatically with dashboard polling',async()=>{
  const {context,nodes,pendingInitial}=dashboard();const paths=[];
  vm.runInContext('comparisonIds=new Set([1,2]);',context);
  context.api=async path=>{paths.push(path);return path.startsWith('/api/history?')?analyticsFixture():[];};
  pendingInitial[0]({ok:true,json:async()=>[{id:1,name:'One',ip:'127.0.0.1',port:80},{id:2,name:'Two',ip:'127.0.0.2',port:80}]});
  await vm.runInContext('load()',context);
  assert.ok(paths.includes('/api/history?targets=1,2&range=24h'));
  assert.equal(nodes.get('comparison-devices').children.length,2);
});

test('analytics empty comparison, stale responses and invalid times remain honest',async()=>{
  const {context,nodes,text}=dashboard();let resolve;
  context.api=()=>new Promise(done=>{resolve=done;});
  vm.runInContext('comparisonIds=new Set([1,2]);',context);
  const pending=vm.runInContext('loadComparison()',context);
  vm.runInContext('comparisonIds=new Set();',context);
  await vm.runInContext('loadComparison()',context);
  resolve(analyticsFixture());await pending;
  assert.equal(nodes.get('comparison-panel').hidden,true);
  vm.runInContext("history=[{checked_at:null,ping_ok:1,latency_ms:5},{checked_at:'bad',packet_loss_pct:100}];drawChart();",context);
  assert.equal(nodes.get('plot').drawCalls.filter(c=>c[0]==='arc').length,0);
  assert.equal(nodes.get('loss-plot').drawCalls.filter(c=>c[0]==='arc').length,0);
  assert.equal(nodes.get('check-timeline').children[0].children[1].children.length,0);
});

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
  context.fetch=async path=>({ok:true,json:async()=>[{checked_at:'2026-10-14T00:00:00Z',ping_ok:1,tcp_ok:1,packet_loss_pct:0,
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
  context.fetch=path=>path.startsWith('/api/analytics')?
    Promise.resolve({ok:true,json:async()=>advancedFixture()}):new Promise(resolve=>pending.push({path,resolve}));
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
  assert.deepEqual(requests,['/api/analytics?targets=2&range=24h','/api/targets/2/history']);
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
const discoveryRow={id:8,scan_id:'scan',address:'192.168.1.2',cidr:'192.168.1.0/30',classification:'responding',method:'icmp_echo',observed_at:'2026-10-23T12:00:00Z'};
const discoveryJobFixture=(state='running',completed=0)=>({job_id:'scan',cidr:'192.168.1.0/30',state,total:2,completed,failed:0,progress_percentage:completed*50,error:null});
test('discovery requires fresh authorization and never starts from inventory polling',async()=>{
 const {context,nodes,requests}=dashboard();let posts=0;
 context.api=async(path,options)=>{if(options?.method==='POST')posts++;return {observations:[]};};
 await vm.runInContext('loadDiscoveryInventory();',context);
 await vm.runInContext('startDiscovery({preventDefault(){}})',context);
 assert.equal(posts,0);assert.match(nodes.get('discovery-progress').textContent,/Confirm authorization/);
 assert.ok(!requests.some(path=>path.includes('/api/discovery/jobs')));
 nodes.get('discovery-authorized').checked=true;nodes.get('discovery-cidr').value='10.0.0.0/30';
 nodes.get('discovery-cidr').listeners.input();assert.equal(nodes.get('discovery-authorized').checked,false);
 assert.match(nodes.get('discovery-summary').textContent,/10.0.0.0\/30.*32.*Two workers/);
});
test('discovery submission prevents repeated clicks and tracks terminal progress',async()=>{
 const {context,nodes}=dashboard();const posts=[];let resolvePost;
 nodes.get('discovery-authorized').checked=true;nodes.get('discovery-cidr').value='192.168.1.0/30';
 context.api=async(path,options)=>{
  if(options?.method==='POST'){posts.push([path,JSON.parse(options.body)]);return new Promise(resolve=>resolvePost=resolve);}
  if(path.includes('/inventory'))return {observations:[]};return discoveryJobFixture('completed',2);
 };
 const pending=vm.runInContext('startDiscovery({preventDefault(){}})',context);
 await vm.runInContext('startDiscovery({preventDefault(){}})',context);
 assert.equal(posts.length,1);assert.deepEqual(posts[0][1],{cidr:'192.168.1.0/30',authorized:true});
 assert.equal(nodes.get('discovery-authorized').checked,false);
 resolvePost(discoveryJobFixture());await pending;
 assert.match(nodes.get('discovery-progress').textContent,/completed.*2\/2.*100%/);
 assert.equal(nodes.get('discovery-start').disabled,false);
 assert.equal(vm.runInContext('discoveryJob',context),null);
});
test('discovery renders observed classifications and explicit enrollment selection safely',async()=>{
 const {context,nodes,text}=dashboard();
 context.api=async()=>({observations:[discoveryRow,{...discoveryRow,id:9,address:'192.168.1.3',classification:'unresponsive'},{...discoveryRow,id:10,address:'192.168.1.4',classification:'unknown'}]});
 await vm.runInContext('loadDiscoveryInventory()',context);
 const rows=nodes.get('discovery-results').children;
 assert.match(text(rows[0]),/192.168.1.2.*responding.*icmp_echo/);
 assert.match(text(rows[1]),/unresponsive/);assert.equal(rows[1].children.length,2);
 rows[0].children[2].onclick();assert.equal(nodes.get('discovery-enroll').hidden,false);
 assert.equal(nodes.get('discovery-selected').textContent,'192.168.1.2');
 vm.runInContext("devices=[{ip:'192.168.1.2'}];renderDiscoveryInventory()",context);
 assert.equal(nodes.get('discovery-results').children[0].children[2].disabled,true);
});
test('discovery cancellation requests stop while polling continues and errors do not resubmit',async()=>{
 const {context,nodes}=dashboard();const paths=[];
 vm.runInContext("discoveryJob='scan'",context);
 context.api=async(path,options)=>{paths.push([path,options?.method]);if(options?.method==='POST')return discoveryJobFixture();throw new Error('Disconnected');};
 await vm.runInContext('cancelDiscovery()',context);
 assert.equal(paths[0][0],'/api/discovery/jobs/scan/cancel');
 assert.match(nodes.get('discovery-progress').textContent,/Cancellation requested/);
 await vm.runInContext('pollDiscovery()',context);
 assert.match(nodes.get('discovery-progress').textContent,/Disconnected.*No scan was resubmitted/);
 assert.equal(paths.filter(p=>p[1]==='POST').length,1);
 context.api=async()=>discoveryJobFixture('cancelled',1);
 await vm.runInContext('pollDiscovery()',context);
 assert.equal(vm.runInContext('discoveryJob',context),null);
});
test('discovery enrollment sends selected observation and configured port and preserves errors',async()=>{
 const {context,nodes}=dashboard();const calls=[];
 vm.runInContext("discoverySelection={id:8};load=async()=>{}",context);
 nodes.get('discovery-name').value='Local device';nodes.get('discovery-port').value='443';
 context.api=async(path,options)=>{calls.push([path,JSON.parse(options.body)]);throw new Error('Address is already monitored');};
 await vm.runInContext('enrollDiscovery({preventDefault(){}})',context);
 assert.equal(calls[0][0],'/api/discovery/observations/8/enroll');assert.equal(calls[0][1].port,'443');
 assert.match(nodes.get('discovery-enroll-status').textContent,/already monitored/);
 context.api=async()=>({id:3});await vm.runInContext('enrollDiscovery({preventDefault(){}})',context);
 assert.equal(nodes.get('discovery-enroll').hidden,true);assert.match(nodes.get('discovery-enroll-status').textContent,/Added to monitoring/);
});
test('discovery handles busy submissions, stale inventory, and missing jobs without auto retry',async()=>{
 const {context,nodes,text}=dashboard();nodes.get('discovery-authorized').checked=true;nodes.get('discovery-cidr').value='10.0.0.0/30';
 context.api=async()=>{const error=new Error('Monitoring is active');error.status=409;throw error;};
 await vm.runInContext('startDiscovery({preventDefault(){}})',context);
 assert.equal(nodes.get('discovery-start').disabled,false);assert.match(nodes.get('discovery-progress').textContent,/Monitoring is active/);
 context.api=async()=>({observations:[discoveryRow]});await vm.runInContext('loadDiscoveryInventory()',context);
 context.api=async()=>{const error=new Error('Missing');error.status=404;throw error;};
 await vm.runInContext('loadDiscoveryInventory()',context);assert.match(text(nodes.get('discovery-results')),/192.168.1.2/);
 assert.match(nodes.get('discovery-inventory-status').textContent,/stale/);
 vm.runInContext("discoveryJob='old'",context);await vm.runInContext('pollDiscovery()',context);
 assert.equal(vm.runInContext('discoveryJob',context),null);
});
function mapTarget(changes={}) {return {id:1,ip:'192.168.1.2',name:'User router label',port:443,checked_at:new Date().toISOString(),stale_after_seconds:90,icmp_check_status:'success',tcp_check_status:'failure',ping_ok:1,tcp_ok:0,latency_ms:2,packet_loss_pct:0,...changes};}
function mapSeed(context,targets=[],observations=[]) {
 context.mapFixtureTargets=targets;context.mapFixtureRows=observations;
 vm.runInContext('devices=mapFixtureTargets;discoveryRows=mapFixtureRows;mapTargetsFresh=true;mapDiscoveryFresh=true;renderNetworkMap()',context);
}
function mapRendered(nodes){return nodes.get('network-map-layer').children.filter(node=>node.attributes['data-map-ip']);}
test('network map renders merged monitored and discovered address nodes with no fabricated links',()=>{
 const {context,nodes,text}=dashboard();
 mapSeed(context,[mapTarget()],[{...discoveryRow,observed_at:new Date(Date.now()-1000).toISOString()},{...discoveryRow,id:9,address:'10.0.0.1'}]);
 const rendered=mapRendered(nodes);assert.equal(rendered.length,2);
 assert.match(text(rendered.find(n=>n.attributes['data-map-ip']==='192.168.1.2')),/User router label.*monitored target.*discovered.*TCP 443: Unavailable/);
 assert.match(text(rendered.find(n=>n.attributes['data-map-ip']==='10.0.0.1')),/Discovery only.*TCP: not checked/);
 const descendants=n=>[n,...n.children.flatMap(descendants)];
 assert.ok(!descendants(nodes.get('network-map-layer')).some(n=>['line','path','polyline'].includes(n.tagName)));
 assert.match(text(nodes.get('network-map-layer')),/192.168.1.0\/24 · display group only/);
 assert.equal(rendered[0].attributes.role,'button');assert.equal(rendered[0].attributes.tabindex,'0');
});
test('network map preserves unknown outcomes, TCP independence, stale timestamps and failed refreshes',()=>{
 const {context,nodes,text}=dashboard();
 mapSeed(context,[mapTarget({icmp_check_status:'unknown',tcp_check_status:'success',ping_ok:0})],[{...discoveryRow,observed_at:new Date(Date.now()-10000).toISOString()}]);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-unknown/);
 assert.match(text(mapRendered(nodes)[0]),/TCP 443: Open/);
 mapSeed(context,[mapTarget({checked_at:'2000-01-01T00:00:00Z'})],[]);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-stale/);
 mapSeed(context,[mapTarget({icmp_check_status:'failure'})],[]);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-unresponsive/);
 assert.match(text(mapRendered(nodes)[0]),/No ICMP reply/);
 vm.runInContext('mapTargetsFresh=false;renderNetworkMap()',context);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-stale/);
 assert.match(nodes.get('network-map-status').textContent,/sources unavailable/);
});
test('network map rejects invalid display addresses and invalid/future times remain unknown',()=>{
 const {context,nodes}=dashboard();
 mapSeed(context,[mapTarget({ip:'8.8.8.8'}),mapTarget({ip:'192.168.001.2'})],[{...discoveryRow,address:'10.0.0.1',observed_at:'bad'}]);
 assert.equal(mapRendered(nodes).length,1);assert.match(mapRendered(nodes)[0].attributes.class,/map-unknown/);
 mapSeed(context,[mapTarget({checked_at:'2999-01-01T00:00:00Z'})],[]);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-unknown/);
 mapSeed(context,[mapTarget({checked_at:null,icmp_check_status:'unknown',tcp_check_status:'unknown'})],[]);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-unknown/);
});
test('network map node selection shows evidence, sampled availability, incidents and inspector navigation',async()=>{
 const {context,nodes,text}=dashboard();const paths=[];
 mapSeed(context,[mapTarget({availability:{icmp:{percentage:75,valid_checks:4,excluded_checks:1}}})],[{...discoveryRow,observed_at:'2000-01-01T00:00:00Z'}]);
 context.api=async path=>{paths.push(path);return {incidents:[{id:4,target_id:1,condition:'tcp_unavailable',state:'ACKNOWLEDGED'}],next_before_id:null};};
 await mapRendered(nodes)[0].listeners.click();
 const panel=nodes.get('network-map-details');assert.match(text(panel),/192.168.1.2.*ICMP.*Discovery: icmp_echo.*75%.*4 valid.*Incident #4/);
 assert.equal(panel.focuses.length,1);
 let inspected=null;context.selectHistory=id=>{inspected=id;};panel.children.find(n=>n.textContent.startsWith('Open inspector')).onclick();assert.equal(inspected,1);
 assert.ok(paths.every(p=>p.startsWith('/api/incidents?')));
 assert.match(mapRendered(nodes)[0].attributes['aria-label'],/User router label/);
});
test('network map filters status and enrollment and keeps useful empty states',()=>{
 const {context,nodes}=dashboard();
 mapSeed(context,[mapTarget()],[{...discoveryRow,address:'10.0.0.1',classification:'unresponsive',observed_at:new Date().toISOString()}]);
 nodes.get('network-map-enrollment').value='discovered';nodes.get('network-map-enrollment').listeners.change();assert.equal(mapRendered(nodes).length,1);
 assert.equal(mapRendered(nodes)[0].attributes['data-map-ip'],'10.0.0.1');
 nodes.get('network-map-status-filter').value='reachable';nodes.get('network-map-status-filter').listeners.change();
 assert.equal(mapRendered(nodes).length,0);assert.equal(nodes.get('network-map-empty').hidden,false);
 assert.match(nodes.get('network-map-empty').textContent,/No devices match/);
 mapSeed(context,[],[]);assert.match(nodes.get('network-map-empty').textContent,/No stored devices/);
});
test('network map pan, zoom bounds, reset and keyboard navigation are deterministic',async()=>{
 const {context,nodes}=dashboard();const svg=nodes.get('network-map-svg');
 mapSeed(context,[],[{...discoveryRow,observed_at:new Date().toISOString()}]);
 svg.listeners.pointerdown({button:0,pointerId:1,clientX:100,clientY:100,target:svg});
 svg.listeners.pointermove({pointerId:1,clientX:140,clientY:125});svg.listeners.pointerup();
 assert.match(nodes.get('network-map-layer').attributes.transform,/translate\(40 25\)/);
 nodes.get('network-map-zoom-in').listeners.click();assert.equal(nodes.get('network-map-zoom-level').textContent,'120%');
 vm.runInContext('zoomNetworkMap(100)',context);assert.equal(nodes.get('network-map-zoom-level').textContent,'300%');
 vm.runInContext('zoomNetworkMap(.001)',context);assert.equal(nodes.get('network-map-zoom-level').textContent,'50%');
 nodes.get('network-map-reset').listeners.click();assert.equal(nodes.get('network-map-layer').attributes.transform,'translate(0 0) scale(1)');
 let prevented=0;svg.listeners.keydown({target:svg,key:'ArrowDown',preventDefault(){prevented++;}});assert.match(nodes.get('network-map-layer').attributes.transform,/translate\(0 -40\)/);
 svg.listeners.keydown({target:svg,key:'Home',preventDefault(){prevented++;}});assert.equal(prevented,2);
 mapRendered(nodes)[0].listeners.keydown({key:'Enter',preventDefault(){prevented++;}});
 assert.equal(vm.runInContext('mapSelected',context),'192.168.1.2');assert.equal(prevented,3);
});
test('network map layout avoids overlaps across 32 addresses and narrow screens',()=>{
 const {context,nodes}=dashboard();const rows=Array.from({length:32},(_,i)=>({...discoveryRow,id:i+1,address:'10.0.0.'+(i+1)}));
 mapSeed(context,[],rows);assert.equal(mapRendered(nodes).length,32);
 assert.equal(new Set(mapRendered(nodes).map(n=>n.attributes.transform)).size,32);
 nodes.get('network-map-viewport').getBoundingClientRect=()=>({width:310});vm.runInContext('renderNetworkMap()',context);
 assert.ok(mapRendered(nodes).every(n=>n.attributes.transform.startsWith('translate(20 ')));
 assert.equal(new Set(mapRendered(nodes).map(n=>n.attributes.transform)).size,32);
});
test('network map auto-refresh uses existing read APIs and never submits discovery or probes',async()=>{
 const {context,nodes,pendingInitial}=dashboard();let target=mapTarget();const calls=[];
 context.api=async(path,options)=>{
  calls.push([path,options?.method||'GET']);
  if(path==='/api/targets')return [target];
  if(path.startsWith('/api/discovery/inventory'))return {observations:[],next_before_id:null};
  if(path.startsWith('/api/notifications'))return notificationFixture([]);
  if(path==='/api/incidents/summary')return incidentFixture([]).summary;
  if(path.startsWith('/api/incidents?'))return incidentFixture([]).data;
  return [];
 };
 pendingInitial[0]({ok:true,json:async()=>[target]});await vm.runInContext('load()',context);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-reachable/);
 target=mapTarget({icmp_check_status:'failure'});await vm.runInContext('load(true)',context);
 assert.match(mapRendered(nodes)[0].attributes.class,/map-unresponsive/);
 assert.ok(calls.some(([p])=>p==='/api/discovery/inventory?limit=200'));
 assert.ok(calls.every(([p,m])=>m==='GET'&&!p.includes('/api/discovery/jobs')&&!p.includes('/api/check')));
});
test('network map incident failures, selection races and truncated pages remain explicit',async()=>{
 const {context,nodes,text}=dashboard();
 mapSeed(context,[mapTarget()],[]);
 context.api=async()=>{throw new Error('Unavailable');};await vm.runInContext("selectMapNode('192.168.1.2')",context);
 assert.match(text(nodes.get('network-map-details')),/Active incidents unavailable/);
 context.api=async()=>({incidents:[],next_before_id:30});await vm.runInContext('refreshMapIncidents()',context);
 assert.match(text(nodes.get('network-map-details')),/not a complete count/);
 mapSeed(context,[],[]);assert.match(text(nodes.get('network-map-details')),/no longer/);
});
function advancedFixture(){
 const stats={count:2,mean:15,median:15,p95:null,p99:null,minimum:10,maximum:20};
 const summary={record_count:3,valid_measurements:2,unknown_measurements:1,invalid_timestamp_count:0,missing_rtt_count:1,missing_loss_count:1,icmp:{percentage:50,successful_checks:1,valid_checks:2,excluded_checks:1},tcp:{percentage:100,successful_checks:2,valid_checks:2,excluded_checks:1}};
 const current={summary,rtt_ms:stats,packet_loss_pct:{...stats,mean:20},rtt_distribution:[{lower:0,upper:20,count:1},{lower:20,upper:null,count:1}],loss_distribution:[{label:'0%',count:1},{label:'(20,50]%',count:1}]};
 const trend={current_count:2,previous_count:0,sufficient:false,delta:null,relative_change_pct:null,unit:'ms'};
 return {start:'2026-10-22T11:00:00Z',end:'2026-10-22T12:00:00Z',previous_start:'2026-10-22T10:00:00Z',previous_end:'2026-10-22T11:00:00Z',as_of:'2026-10-23T12:00:00Z',series:[{name:'Local test',target_id:1,current,previous:current,trends:{mean_rtt_ms:trend,mean_loss_pct:trend,icmp_sampled_availability:trend,tcp_sampled_availability:trend}}],incidents:{opened:2,resolved:1,unresolved:1,resolved_sampled_span_seconds:{...stats,count:1,median:120,maximum:120},mean_sampled_time_to_recovery_seconds:120,unresolved_age_seconds:{count:1,mean:200,maximum:200},by_condition:{tcp_unavailable:{opened:2,resolved:1,unresolved:1}},duration_distribution:[{lower:60,upper:300,count:1}],by_device:[{target_id:null,original_target_id:5,opened:2,resolved:1,unresolved:1}],devices_truncated:false,invalid_open_timestamp_count:0,invalid_resolved_timestamp_count:0,invalid_duration_count:0,invalid_age_count:0}};
}
test('advanced analytics renders accurate sample counts, N/A percentiles, distributions and incident spans',async()=>{
 const {context,nodes,text}=dashboard();context.fixture=advancedFixture();context.api=async()=>context.fixture;
 vm.runInContext('selectedId=1',context);await vm.runInContext('loadAdvancedAnalytics()',context);
 const rendered=text(nodes.get('analytics-summary'));
 assert.match(rendered,/3 stored observations.*2 classifiable.*1 unknown/);
 assert.match(rendered,/p95.*N\/A.*p99.*N\/A/);
 assert.match(rendered,/ICMP sampled availability 50%.*TCP sampled availability 100%/);
 assert.match(rendered,/needs ≥5 valid samples/);
 assert.match(text(nodes.get('analytics-incidents')),/2 opened \/ 1 resolved.*1 currently unresolved.*120 s.*Removed device #5/);
 assert.match(nodes.get('analytics-window').textContent,/end excluded.*Preceding equal window.*Unresolved incidents are current/);
});
test('advanced analytics reuses history range and compared device selection and global incident scope',async()=>{
 const {context,nodes}=dashboard();const paths=[];context.api=async path=>{paths.push(path);return advancedFixture();};
 vm.runInContext("comparisonIds=new Set([2,3]);historyRange='7d'",context);await vm.runInContext('loadAdvancedAnalytics()',context);
 assert.equal(paths[0],'/api/analytics?targets=2,3&range=7d');
 nodes.get('analytics-scope').value='all_incidents';await nodes.get('analytics-scope').listeners.change();
 assert.equal(paths[1],'/api/analytics/incidents?range=7d');
});
test('advanced analytics shows valid trends with sample counts and incomplete device breakdown warnings',async()=>{
 const {context,nodes,text}=dashboard();const data=advancedFixture();
 data.series[0].trends.mean_rtt_ms={sufficient:true,delta:5,relative_change_pct:50,current_count:8,previous_count:5,unit:'ms'};
 data.incidents.devices_truncated=true;context.api=async()=>data;await vm.runInContext('loadAdvancedAnalytics()',context);
 assert.match(text(nodes.get('analytics-summary')),/Mean exact RTT: \+5 ms \(50%\).*8 current \/ 5 previous/);
 assert.match(text(nodes.get('analytics-incidents')),/limited to 100 groups/);
});
test('advanced analytics stale responses and failed queries cannot replace current selection',async()=>{
 const {context,nodes,text}=dashboard();let firstResolve;let calls=0;
 context.api=async()=>{if(calls++===0)return new Promise(resolve=>firstResolve=resolve);const data=advancedFixture();data.series[0].name='Latest selection';return data;};
 const old=vm.runInContext('loadAdvancedAnalytics()',context);await vm.runInContext('loadAdvancedAnalytics()',context);
 firstResolve(advancedFixture());await old;assert.match(text(nodes.get('analytics-summary')),/Latest selection/);
 context.api=async()=>{throw new Error('Analytics observation limit exceeded');};await vm.runInContext('loadAdvancedAnalytics()',context);
 assert.match(nodes.get('analytics-status').textContent,/observation limit exceeded/);assert.equal(nodes.get('analytics-summary').children.length,0);
});
test('advanced analytics empty device lists use global incidents and dashboard polling refreshes summaries',async()=>{
 const {context,nodes,pendingInitial}=dashboard();const paths=[];
 context.api=async path=>{paths.push(path);if(path==='/api/targets')return [];if(path.startsWith('/api/analytics')){const data=advancedFixture();data.series=[];return data;}if(path.startsWith('/api/discovery/inventory'))return {observations:[]};if(path.startsWith('/api/notifications'))return notificationFixture([]);if(path==='/api/incidents/summary')return incidentFixture([]).summary;if(path.startsWith('/api/incidents?'))return incidentFixture([]).data;return [];};
 pendingInitial[0]({ok:true,json:async()=>[]});await vm.runInContext('load()',context);
 await vm.runInContext('load(true)',context);
 assert.equal(paths.filter(path=>path==='/api/analytics/incidents?range=24h').length,2);
 assert.match(nodes.get('analytics-status').textContent,/All stored incidents/);
 assert.ok(paths.every(path=>!path.includes('/api/check')&&!path.includes('/api/discovery/jobs')));
});
function reportHarness(){
 const created=[],revoked=[];const view=dashboard({URL:{createObjectURL(blob){created.push(blob);return 'blob:local-report';},revokeObjectURL(url){revoked.push(url);}}});
 for(const id of ['report-format','report-content','report-range','report-device-source','report-start','report-end','report-status','report-submit','report-form'])view.context.document.getElementById(id);
 return {...view,created,revoked};
}
function reportResponse(format='csv',size=300){return {ok:true,headers:{get(){return format==='pdf'?'application/pdf':'text/csv; charset=utf-8';}},blob:async()=>({size})};}
test('report controls open accessibly and reuse selected devices and range without generating automatically',()=>{
 const {context,nodes,requests}=reportHarness();seedDevices(context);nodes.get('report-form').hidden=true;
 nodes.get('report-toggle').listeners.click();
 assert.equal(nodes.get('report-form').hidden,false);assert.equal(nodes.get('report-toggle').attributes['aria-expanded'],'true');
 assert.equal(nodes.get('report-format').focuses.length,1);assert.match(nodes.get('report-selection').textContent,/Local computer/);
 nodes.get('report-range').value='custom';nodes.get('report-range').listeners.change();
 assert.equal(nodes.get('report-start').required,true);assert.equal(nodes.get('report-custom-dates').hidden,false);
 nodes.get('report-form').listeners.keydown({key:'Escape',preventDefault(){}});assert.equal(nodes.get('report-form').hidden,true);
 assert.ok(!requests.some(path=>path.startsWith('/api/reports')));
});
test('report generation prevents duplicate submissions, downloads a blob and revokes its URL',async()=>{
 const {context,nodes,created,revoked,timers}=reportHarness();seedDevices(context);let resolve;let count=0;let path;
 context.fetch=request=>{count++;path=request;return new Promise(done=>resolve=done);};
 const pending=vm.runInContext('exportReport({preventDefault(){}})',context);
 assert.equal(nodes.get('report-submit').disabled,true);assert.match(nodes.get('report-status').textContent,/Generating/);
 await vm.runInContext('exportReport({preventDefault(){}})',context);assert.equal(count,1);
 resolve(reportResponse());await pending;
 assert.match(path,/^\/api\/reports.csv\?targets=1&content=all&range=24h$/);
 const anchor=context.document.body.children.at(-1);assert.equal(anchor.download,'netscope-report.csv');assert.equal(anchor.clicked,true);assert.equal(anchor.removed,true);
 assert.equal(created.length,1);assert.equal(nodes.get('report-submit').disabled,false);
 [...timers.values()].at(-1)();assert.deepEqual(revoked,['blob:local-report']);
});
test('report options select compared devices, PDF and custom UTC dates',async()=>{
 const {context,nodes}=reportHarness();seedDevices(context);const paths=[];
 vm.runInContext('comparisonIds=new Set([1,2])',context);
 nodes.get('report-format').value='pdf';nodes.get('report-content').value='summary';nodes.get('report-range').value='custom';
 nodes.get('report-start').value='2026-10-22T11:00';nodes.get('report-end').value='2026-10-22T12:00';
 context.fetch=async path=>{paths.push(path);return reportResponse('pdf');};
 await vm.runInContext('exportReport({preventDefault(){}})',context);
 assert.match(paths[0],/^\/api\/reports.pdf\?targets=1,2&content=summary&start=/);
 assert.ok(paths[0].includes(encodeURIComponent(new Date('2026-10-22T11:00').toISOString())));
 assert.equal(context.document.body.children.at(-1).download,'netscope-report.pdf');
});
test('report validation rejects no devices, invalid windows and raw PDF measurements before fetch',async()=>{
 const {context,nodes}=reportHarness();let calls=0;context.fetch=async()=>{calls++;return reportResponse();};
 await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/Select at least one/);
 seedDevices(context);nodes.get('report-format').value='pdf';nodes.get('report-content').value='measurements';
 await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/Raw measurements are available in CSV/);
 nodes.get('report-format').value='csv';nodes.get('report-range').value='custom';nodes.get('report-start').value='bad';
 await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/positive custom window/);
 assert.equal(calls,0);
});
test('report failures and unexpected responses restore controls without downloads or retries',async()=>{
 const {context,nodes,created}=reportHarness();seedDevices(context);
 context.fetch=async()=>({ok:false,status:429,json:async()=>({error:'Another report is generating'})});
 await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/Another report is generating/);assert.equal(nodes.get('report-submit').disabled,false);
 context.fetch=async()=>reportResponse('pdf');await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/Unexpected report response/);
 context.fetch=async()=>reportResponse('csv',0);await vm.runInContext('exportReport({preventDefault(){}})',context);assert.match(nodes.get('report-status').textContent,/empty or exceeds/);
 context.fetch=async()=>{throw new Error('Timeout');};await vm.runInContext('exportReport({preventDefault(){}})',context);
 assert.match(nodes.get('report-status').textContent,/Timeout.*No automatic retry/);assert.equal(created.length,0);
});
