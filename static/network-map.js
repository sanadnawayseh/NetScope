// A spatial inventory, not topology: no device-to-device edges are rendered.
const MAP_DISCOVERY_STALE_MS=300000,MAP_NODE_LIMIT=200;
let mapTargetsFresh=false,mapDiscoveryFresh=false,mapSelected=null,mapNodes=[];
let mapView={x:0,y:0,zoom:1},mapDrag=null,mapDetailRequest=0,mapIncidentData=null,mapIncidentError=null;
function mapSvg(tag,attributes={},content) {
  const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));
  if(content!==undefined)node.textContent=String(content);
  return node;
}
function mapTime(value) {
  return typeof value==='string'&&/(?:Z|[+-]\d\d:\d\d)$/.test(value)?Date.parse(value):NaN;
}
function mapAddress(value) {
  // Display only addresses allowed by the existing monitoring/discovery boundary.
  if(typeof value!=='string'||!/^\d{1,3}(\.\d{1,3}){3}$/.test(value))return false;
  const parts=value.split('.').map(Number);
  return parts.every(n=>n<=255)&&parts.map(String).join('.')===value&&
    (parts[0]===10||parts[0]===127||(parts[0]===172&&parts[1]>=16&&parts[1]<=31)||(parts[0]===192&&parts[1]===168));
}
function mapProtocol(target,protocol) {
  const value=target[protocol+'_check_status'];
  return value==='success'?'reachable':value==='failure'?'unresponsive':'unknown';
}
function mapObservation(observation,now) {
  if(!observation)return {state:'unknown',reason:'No recorded observation',time:null};
  const timestamp=mapTime(observation.time);
  if(!Number.isFinite(timestamp)||timestamp>now+60000)return {...observation,state:'unknown',reason:'Timestamp unavailable or ahead of this browser clock'};
  if(!observation.fresh||now-timestamp>observation.staleMs)return {...observation,state:'stale',reason:!observation.fresh?'Source refresh failed; showing stored outcome':'Observation exceeds the display freshness window'};
  return {...observation,state:observation.outcome,reason:observation.outcome==='unknown'?'Unclassified or collection failure; no confirmed network result':''};
}
function buildMapNodes(targets,observations,now=Date.now()) {
  const byAddress=new Map();
  for(const target of targets){
    if(!mapAddress(target.ip))continue;
    if(!byAddress.has(target.ip))byAddress.set(target.ip,{ip:target.ip,targets:[],discovery:null});
    byAddress.get(target.ip).targets.push(target);
  }
  for(const row of observations){
    if(!mapAddress(row.address))continue;
    if(!byAddress.has(row.address))byAddress.set(row.address,{ip:row.address,targets:[],discovery:null});
    const node=byAddress.get(row.address);
    if(!node.discovery||row.id>node.discovery.id)node.discovery=row;
  }
  const nodes=[...byAddress.values()].map(node=>{
    const samples=node.targets.filter(t=>t.checked_at).map(t=>({time:t.checked_at,outcome:mapProtocol(t,'icmp'),
      source:'Monitoring',fresh:mapTargetsFresh,staleMs:(t.stale_after_seconds||90)*1000}));
    if(node.discovery)samples.push({time:node.discovery.observed_at,outcome:node.discovery.classification==='responding'?'reachable':node.discovery.classification==='unresponsive'?'unresponsive':'unknown',source:'Discovery ICMP echo',fresh:mapDiscoveryFresh,staleMs:MAP_DISCOVERY_STALE_MS});
    // A newer unknown outcome must not be concealed by an older success.
    samples.sort((a,b)=>(Number.isFinite(mapTime(b.time))?mapTime(b.time):Infinity)-(Number.isFinite(mapTime(a.time))?mapTime(a.time):Infinity)||Number(a.source!=='Monitoring')-Number(b.source!=='Monitoring'));
    const observation=mapObservation(samples[0],now);
    return {...node,name:node.targets.map(t=>t.name).filter(Boolean).join(' / '),observation,
      group:node.ip.split('.').slice(0,3).join('.')+'.0/24',
      services:node.targets.map(target=>({target,observation:mapObservation(target.checked_at?{time:target.checked_at,outcome:mapProtocol(target,'tcp'),source:'Monitoring',fresh:mapTargetsFresh,staleMs:(target.stale_after_seconds||90)*1000}:null,now)}))};
  });
  nodes.sort((a,b)=>Number(!!b.targets.length)-Number(!!a.targets.length)||a.ip.split('.').reduce((n,v)=>n*256+Number(v),0)-b.ip.split('.').reduce((n,v)=>n*256+Number(v),0));
  return {nodes:nodes.slice(0,MAP_NODE_LIMIT),total:nodes.length};
}
function mapStateLabel(state,protocol='ICMP') {
  return state==='reachable'?(protocol==='TCP'?'Open':'Echo reply'):state==='unresponsive'?(protocol==='TCP'?'Unavailable':'No ICMP reply'):state==='stale'?'Stale':'Unknown / not verified';
}
function mapApplyView() {
  $('network-map-layer').setAttribute('transform',`translate(${mapView.x} ${mapView.y}) scale(${mapView.zoom})`);
  $('network-map-zoom-level').textContent=`${Math.round(mapView.zoom*100)}%`;
}
function zoomNetworkMap(factor,anchor=null) {
  const zoom=Math.max(.5,Math.min(3,mapView.zoom*factor));
  const bounds=$('network-map-svg').getBoundingClientRect();
  const center=anchor||{x:bounds.width/2,y:(bounds.height||420)/2};
  const ratio=zoom/mapView.zoom;
  mapView={x:center.x-(center.x-mapView.x)*ratio,y:center.y-(center.y-mapView.y)*ratio,zoom};mapApplyView();
}
function resetNetworkMap() {mapView={x:0,y:0,zoom:1};mapApplyView();}
function panNetworkMap(x,y) {mapView.x=Math.max(-10000,Math.min(10000,mapView.x+x));mapView.y=Math.max(-10000,Math.min(10000,mapView.y+y));mapApplyView();}
function renderNetworkMap() {
  const data=buildMapNodes(devices,discoveryRows);mapNodes=data.nodes;
  const status=$('network-map-status-filter').value||'all',enrollment=$('network-map-enrollment').value||'all';
  const visible=mapNodes.filter(node=>(status==='all'||node.observation.state===status)&&(enrollment==='all'||(enrollment==='monitored'?node.targets.length:!node.targets.length)));
  const layer=$('network-map-layer'),focused=document.activeElement?.getAttribute?.('data-map-ip');
  layer.replaceChildren();
  const width=Math.max(280,$('network-map-viewport').getBoundingClientRect().width),columns=Math.max(1,Math.min(4,Math.floor((width-32)/212)));
  const groups=new Map();for(const node of visible){if(!groups.has(node.group))groups.set(node.group,[]);groups.get(node.group).push(node);}
  let y=20;
  for(const [group,nodes] of [...groups].sort((a,b)=>a[0].localeCompare(b[0],undefined,{numeric:true}))){
    const rows=Math.ceil(nodes.length/columns),height=rows*142+48;
    layer.append(mapSvg('rect',{x:8,y,width:columns*212+16,height,rx:6,class:'map-bucket'}),mapSvg('text',{x:20,y:y+22,class:'map-group-label'},`${group} · display group only`));
    nodes.forEach((node,index)=>{
      const x=20+(index%columns)*212,top=y+38+Math.floor(index/columns)*142;
      const label=`${node.ip}${node.name?' · '+node.name:''} · ${node.targets.length?'Monitored':'Discovery only'} · ${mapStateLabel(node.observation.state)} · ${observationLabel(node.observation.time)}`;
      const button=mapSvg('g',{class:`map-node map-${node.observation.state}${node.ip===mapSelected?' map-selected':''}`,transform:`translate(${x} ${top})`,role:'button',tabindex:0,'aria-label':label,'aria-pressed':String(node.ip===mapSelected),'data-map-ip':node.ip});
      button.append(mapSvg('title',{},label),mapSvg('rect',{width:196,height:128,rx:6}),mapSvg('circle',{cx:14,cy:18,r:4}),mapSvg('text',{x:26,y:23,class:'map-address'},node.ip),
        mapSvg('text',{x:12,y:43},node.name.length>24?node.name.slice(0,23)+'…':node.name||'No assigned name'),
        mapSvg('text',{x:12,y:62,class:'map-small'},node.targets.length?`${node.targets.length} monitored target(s)${node.discovery?' · discovered':''}`:'Discovery only'),
        mapSvg('text',{x:12,y:81,class:'map-small'},mapStateLabel(node.observation.state)),
        mapSvg('text',{x:12,y:100,class:'map-small'},node.services.length===1?`TCP ${node.services[0].target.port}: ${mapStateLabel(node.services[0].observation.state,'TCP')}`:node.services.length?`TCP: ${node.services.length} services · see details`:'TCP: not checked'),
        mapSvg('text',{x:12,y:118,class:'map-small'},node.observation.time?new Date(node.observation.time).toLocaleTimeString():'No observation'));
      button.addEventListener('click',()=>selectMapNode(node.ip));
      button.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();void selectMapNode(node.ip);}});
      layer.append(button);if(focused===node.ip)button.focus({preventScroll:true});
    });
    y+=height+16;
  }
  $('network-map-svg').setAttribute('viewBox',`0 0 ${width} 420`);
  $('network-map-empty').hidden=visible.length>0;
  $('network-map-empty').textContent=mapNodes.length?'No devices match these filters.':'No stored devices or discovery observations. This map does not start scans.';
  $('network-map-status').textContent=`${visible.length} displayed / ${data.total} addresses${data.total>MAP_NODE_LIMIT?' · Display capped at 200; monitored addresses take priority':''} · ${!mapTargetsFresh||!mapDiscoveryFresh?'Some sources unavailable/loading; stored outcomes may be stale.':'Stored data refreshed.'} No verified links available.`;
  mapApplyView();renderMapDetails();
}
function renderMapDetails() {
  const panel=$('network-map-details'),node=mapNodes.find(n=>n.ip===mapSelected);panel.replaceChildren();
  if(!node){panel.append(element('p','muted',mapSelected?'Selected address is no longer in this bounded inventory.':'Select a node to inspect stored observations.'));return;}
  panel.append(element('h3','',`${node.name||'Unassigned device'} · ${node.ip}`),element('p','',node.targets.length?'Actively monitored configuration':'Discovery only · not monitored'));
  const observed=node.observation;
  panel.append(element('p','muted',`Latest ICMP outcome: ${mapStateLabel(observed.state)} · ${observed.source||'No source'} · ${observationLabel(observed.time)}${observed.state==='stale'?' · Stored result: '+mapStateLabel(observed.outcome):''}${observed.reason?' · '+observed.reason:''}`));
  if(node.discovery){
    const discovery=mapObservation({time:node.discovery.observed_at,outcome:node.discovery.classification==='responding'?'reachable':node.discovery.classification==='unresponsive'?'unresponsive':'unknown',fresh:mapDiscoveryFresh,staleMs:MAP_DISCOVERY_STALE_MS},Date.now());
    panel.append(element('p','muted',`Discovery: ${node.discovery.method} · ${mapStateLabel(discovery.state)} · stored ${node.discovery.classification} · ${observationLabel(node.discovery.observed_at)} · authorized scope ${node.discovery.cidr}`));
  }
  else panel.append(element('p','muted','No retained discovery observation.'));
  for(const {target,observation:tcp} of node.services){
    const icmp=mapObservation(target.checked_at?{time:target.checked_at,outcome:mapProtocol(target,'icmp'),fresh:mapTargetsFresh,staleMs:(target.stale_after_seconds||90)*1000}:null,Date.now());
    panel.append(element('h4','',`${target.name} · target #${target.id}`),element('p','muted',`Monitoring ${observationLabel(target.checked_at)} · ICMP ${mapStateLabel(icmp.state)} · TCP ${target.port}: ${mapStateLabel(tcp.state,'TCP')}${tcp.state==='stale'?' (stored '+mapStateLabel(tcp.outcome,'TCP')+')':''}`),
      element('p','muted',`Exact RTT ${latencyText(target)} · packet loss ${Number.isFinite(target.packet_loss_pct)?fmt(target.packet_loss_pct)+'%':'N/A'}`));
    for(const [protocol,error] of [['ICMP',target.ping_error],['TCP',target.tcp_error]])if(error)panel.append(element('p','muted',`${protocol} diagnostic: ${error}`));
    for(const protocol of ['icmp','tcp']){
      const summary=target.availability?.[protocol];
      panel.append(element('p','muted',`${protocol.toUpperCase()} sampled availability: ${Number.isFinite(summary?.percentage)?fmt(summary.percentage)+'%':'N/A'} · ${summary?.valid_checks||0} valid / ${summary?.excluded_checks||0} excluded · latest 60 records`));
    }
    const inspector=element('button','secondary',`Open inspector · ${target.name}`);inspector.type='button';inspector.onclick=()=>selectHistory(target.id);panel.append(inspector);
  }
  if(!node.services.length)panel.append(element('p','muted','TCP not checked; discovery does not scan services.'));
  if(node.targets.length){
    if(mapIncidentError)panel.append(element('p','muted',`Active incidents unavailable: ${mapIncidentError}. Stored incident details may be stale.`));
    if(mapIncidentData){
      const ids=new Set(node.targets.map(t=>t.id));const incidents=mapIncidentData.incidents.filter(row=>ids.has(row.target_id));
      panel.append(element('p','muted',`${incidents.length} active incident(s) in the retrieved page${mapIncidentData.next_before_id?' · Active incident page is capped; this is not a complete count.':''}`));
      for(const row of incidents){const link=element('button','secondary',`Incident #${row.id} · ${incidentConditions[row.condition]||row.condition} · ${row.state}`);link.type='button';link.onclick=async()=>{await loadIncidentDetail(row.id,true);$('incidents').scrollIntoView({behavior:'auto',block:'start'});};panel.append(link);}
    }else if(!mapIncidentError)panel.append(element('p','muted','Loading active incident information…'));
    const link=element('a','secondary','Open incident dashboard');link.setAttribute('href','#incidents');panel.append(link);
  }
}
async function refreshMapIncidents() {
  const node=mapNodes.find(n=>n.ip===mapSelected);if(!node?.targets.length)return;
  const token=++mapDetailRequest;
  try {
    const data=await api('/api/incidents?state=ACTIVE&range=30d&limit=200',{signal:AbortSignal.timeout(10000)});
    if(token!==mapDetailRequest)return;mapIncidentData=data;mapIncidentError=null;
  }catch(error){if(token!==mapDetailRequest)return;mapIncidentError=error.message;}
  renderMapDetails();
}
async function selectMapNode(ip) {
  if(!mapNodes.some(node=>node.ip===ip))return;
  if(mapSelected!==ip){mapDetailRequest++;mapIncidentData=null;mapIncidentError=null;}
  mapSelected=ip;renderNetworkMap();$('network-map-details').focus({preventScroll:true});await refreshMapIncidents();
}
function initializeNetworkMap() {
  $('network-map-status-filter').addEventListener('change',renderNetworkMap);
  $('network-map-enrollment').addEventListener('change',renderNetworkMap);
  $('network-map-reset').addEventListener('click',resetNetworkMap);
  $('network-map-zoom-in').addEventListener('click',()=>zoomNetworkMap(1.2));
  $('network-map-zoom-out').addEventListener('click',()=>zoomNetworkMap(1/1.2));
  $('network-map-refresh').addEventListener('click',()=>load(true));
  const svg=$('network-map-svg');
  svg.addEventListener('wheel',event=>{event.preventDefault();const rect=svg.getBoundingClientRect();zoomNetworkMap(event.deltaY<0?1.15:1/1.15,{x:event.clientX-rect.left,y:event.clientY-rect.top});},{passive:false});
  svg.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.target.closest?.('[data-map-ip]'))return;
    mapDrag={id:event.pointerId,x:event.clientX,y:event.clientY};svg.setPointerCapture?.(event.pointerId);
  });
  svg.addEventListener('pointermove',event=>{if(mapDrag?.id!==event.pointerId)return;panNetworkMap(event.clientX-mapDrag.x,event.clientY-mapDrag.y);mapDrag={id:event.pointerId,x:event.clientX,y:event.clientY};});
  const endDrag=()=>{mapDrag=null;};svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);svg.addEventListener('lostpointercapture',endDrag);
  svg.addEventListener('keydown',event=>{
    if(event.target!==svg)return;
    const direction={ArrowLeft:[40,0],ArrowRight:[-40,0],ArrowUp:[0,40],ArrowDown:[0,-40]}[event.key];
    if(direction){event.preventDefault();panNetworkMap(...direction);}
    else if(['+','=','-','0','Home'].includes(event.key)){event.preventDefault();if(event.key==='0'||event.key==='Home')resetNetworkMap();else zoomNetworkMap(event.key==='-'?1/1.2:1.2);}
  });
  window.addEventListener('resize',renderNetworkMap);renderNetworkMap();
}
