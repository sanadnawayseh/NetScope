// Durable in-app events are authoritative. Desktop delivery is best effort.
let notificationRows=[],notificationStream=null,notificationCursor=null,notificationOlder=null,notificationLatest=0;
let notificationLoading=false,notificationPromise=null,notificationReadBusy=false,desktopPermissionBusy=false;
function notificationStorage(key,value) {
  try {
    if(typeof localStorage==='undefined')return null;
    if(value!==undefined)localStorage.setItem(key,String(value));
    return localStorage.getItem(key);
  } catch { return null; }
}
let desktopEnabled=notificationStorage('netscope.desktop.enabled')==='1';
let desktopSessionCursor=new Map();
function desktopSupported() { return typeof Notification!=='undefined' && window.isSecureContext!==false; }
function syncDesktopPreference() {
  $('desktop-toggle').disabled=!desktopSupported()||desktopPermissionBusy;
  $('desktop-toggle').textContent=desktopEnabled?'Disable desktop alerts':'Enable desktop alerts';
  if(!desktopSupported())$('desktop-status').textContent='Desktop alerts are unsupported here. In-app notifications remain available.';
}
async function toggleDesktopNotifications() {
  if(desktopPermissionBusy||!desktopSupported())return;
  if(desktopEnabled){desktopEnabled=false;notificationStorage('netscope.desktop.enabled','0');$('desktop-status').textContent='Desktop alerts disabled. In-app notifications remain active.';syncDesktopPreference();return;}
  desktopPermissionBusy=true;syncDesktopPreference();
  try {
    // This function is called only by the explicit preference button gesture.
    const permission=Notification.permission==='default'?await Notification.requestPermission():Notification.permission;
    desktopEnabled=permission==='granted';
    notificationStorage('netscope.desktop.enabled',desktopEnabled?'1':'0');
    if(desktopEnabled && notificationStream){
      const key='netscope.desktop.cursor.'+notificationStream;
      notificationStorage(key,notificationLatest);desktopSessionCursor.set(notificationStream,notificationLatest);
    }
    $('desktop-status').textContent=desktopEnabled?'Desktop alerts enabled for new events while this page is open.':'Desktop permission was not granted. Use browser settings if denied; in-app notifications still work.';
  } catch {desktopEnabled=false;notificationStorage('netscope.desktop.enabled','0');$('desktop-status').textContent='Desktop permission is unavailable. In-app notifications still work.';}
  finally{desktopPermissionBusy=false;syncDesktopPreference();}
}
function deliverDesktopEvents(data) {
  if(!desktopEnabled||!desktopSupported())return;
  if(Notification.permission!=='granted'){
    desktopEnabled=false;notificationStorage('netscope.desktop.enabled','0');syncDesktopPreference();
    $('desktop-status').textContent='Desktop permission is no longer granted. In-app notifications still work.';return;
  }
  const key='netscope.desktop.cursor.'+data.stream_id,stored=notificationStorage(key);
  let cursor=stored!==null && /^\d+$/.test(stored)?Number(stored):desktopSessionCursor.get(data.stream_id);
  if(cursor===undefined){
    // First opt-in/session establishes a baseline, never a historical alert burst.
    cursor=data.latest_id;notificationStorage(key,cursor);desktopSessionCursor.set(data.stream_id,cursor);return;
  }
  let shown=0;
  for(const event of data.events){
    if(event.id<=cursor)continue;
    cursor=event.id;
    // Persist before attempting OS delivery: retries should not re-alert.
    notificationStorage(key,cursor);desktopSessionCursor.set(data.stream_id,cursor);
    if(event.read_at||shown>=5)continue;
    try {
      const title=event.event_type==='incident_opened'?'NetScope · Incident opened':'NetScope · Incident resolved';
      const notice=new Notification(title,{body:`${incidentDeviceName(event)} · ${incidentConditions[event.condition]||event.condition} · ${observationLabel(event.event_at)}`,
        tag:`netscope-${data.stream_id}-${event.id}`,renotify:false});
      notice.onclick=()=>{window.focus?.();void openNotificationIncident(event);notice.close?.();};
      shown++;
    } catch {
      desktopEnabled=false;notificationStorage('netscope.desktop.enabled','0');syncDesktopPreference();
      $('desktop-status').textContent='This browser could not display desktop alerts. In-app notifications remain available.';break;
    }
  }
}
function mergeNotificationEvents(events) {
  const rows=new Map(notificationRows.map(row=>[row.id,row]));
  for(const row of events)rows.set(row.id,{...row,read_at:row.read_at||rows.get(row.id)?.read_at||null});
  notificationRows=[...rows.values()].sort((a,b)=>a.id-b.id).slice(-200);
}
function renderNotificationEvents(unread) {
  $('notification-toggle').textContent=`Notifications · ${unread} unread`;
  $('notification-toggle').setAttribute('aria-label',`Notifications, ${unread} unread`);
  const list=$('notification-list');list.replaceChildren();
  if(!notificationRows.length)list.append(element('li','muted','No notification events yet.'));
  for(const event of [...notificationRows].reverse()){
    const row=element('li',`notification-row ${event.event_type==='incident_opened'?'notification-opened':'notification-resolved'}${event.read_at?' notification-read':''}`);
    row.append(element('strong','',`${event.event_type==='incident_opened'?'Opened':'Resolved'} · ${incidentDeviceName(event)} · ${incidentConditions[event.condition]||event.condition}`),
      element('p','muted',`${observationLabel(event.event_at)} · ${event.read_at?'Read':'Unread'} · Recorded ${observationLabel(event.created_at)}`));
    const link=element('button','secondary',`View incident #${event.incident_id}`);link.type='button';link.onclick=()=>openNotificationIncident(event);row.append(link);
    if(!event.read_at){const read=element('button','secondary','Mark read');read.type='button';read.disabled=notificationReadBusy;read.onclick=()=>readNotificationEvent(event.id);row.append(read);}
    list.append(row);
  }
  $('notification-older').disabled=!notificationOlder||notificationRows.length>=200;
  $('notification-read-all').disabled=notificationReadBusy||!unread;
}
async function loadNotifications(force=false) {
  if(notificationLoading)return force?notificationPromise.then(()=>loadNotifications()):notificationPromise;
  notificationLoading=true;
  notificationPromise=(async()=>{
    try {
      let data=await api('/api/notifications?limit=50'+(notificationCursor===null?'':`&after_id=${notificationCursor}`),{signal:AbortSignal.timeout(10000)});
      if(notificationStream && notificationStream!==data.stream_id){
        notificationRows=[];notificationCursor=null;notificationOlder=null;
        data=await api('/api/notifications?limit=50',{signal:AbortSignal.timeout(10000)});
      }
      if(notificationCursor===null)notificationOlder=data.next_before_id;
      notificationStream=data.stream_id;notificationCursor=data.next_after_id;notificationLatest=data.latest_id;
      mergeNotificationEvents(data.events);
      let unread=data.unread_count;
      if($('notification-panel').hidden===false){
        // Refresh read markers changed by another local tab/client as well as new IDs.
        const snapshot=await api('/api/notifications?limit=200',{signal:AbortSignal.timeout(10000)});
        if(snapshot.stream_id===notificationStream){
          mergeNotificationEvents(snapshot.events);unread=snapshot.unread_count;
          notificationOlder=snapshot.next_before_id;notificationLatest=snapshot.latest_id;
          const current=new Map(snapshot.events.map(event=>[event.id,event]));
          data.events=data.events.map(event=>current.get(event.id)||event);
        }
      }
      deliverDesktopEvents(data);renderNotificationEvents(unread);
      $('notification-status').textContent=`${notificationRows.length} recent events displayed${data.has_more?' · More new events will load on the next refresh.':''} · Read state is shared by this local application.`;
    } catch(error){$('notification-status').textContent=`Notifications unavailable: ${error.message}. Displayed events/count may be stale; the next refresh will retry.`;}
  })().finally(()=>{notificationLoading=false;});
  return notificationPromise;
}
async function loadOlderNotifications() {
  if(!notificationOlder||notificationRows.length>=200)return;
  $('notification-older').disabled=true;
  try {
    const data=await api(`/api/notifications?limit=50&before_id=${notificationOlder}`);
    if(data.stream_id!==notificationStream){notificationCursor=null;await loadNotifications(true);return;}
    notificationOlder=data.next_before_id;mergeNotificationEvents(data.events);renderNotificationEvents(data.unread_count);
    $('notification-status').textContent=`${notificationRows.length} recent events displayed (panel limit 200). Older events remain in SQLite.`;
  }catch(error){$('notification-status').textContent=`Older notifications unavailable: ${error.message}`;$('notification-older').disabled=false;}
}
async function readNotificationEvent(id) {
  if(notificationReadBusy)return;
  notificationReadBusy=true;
  try {
    const result=await api(`/api/notifications/${id}/read`,{method:'POST',body:'{}'});
    notificationRows=notificationRows.map(row=>row.id===id?{...row,read_at:result.read_at}:row);
    renderNotificationEvents(result.unread_count);
    $('notification-action-status').textContent='Notification marked read.';
  }catch(error){$('notification-action-status').textContent=`Could not mark notification read: ${error.message}`;}
  finally{notificationReadBusy=false;await loadNotifications(true);}
}
async function readAllNotifications() {
  if(notificationReadBusy)return;
  notificationReadBusy=true;
  try {
    const snapshot=notificationLatest;
    const result=await api('/api/notifications/read',{method:'POST',body:JSON.stringify({up_to_id:snapshot})});
    notificationRows=notificationRows.map(row=>row.id<=snapshot?{...row,read_at:row.read_at||result.read_at}:row);
    renderNotificationEvents(result.unread_count);
    $('notification-action-status').textContent='Notifications through the displayed snapshot marked read. Newer events remain unread.';
  }catch(error){$('notification-action-status').textContent=`Could not mark notifications read: ${error.message}`;}
  finally{notificationReadBusy=false;await loadNotifications(true);}
}
async function openNotificationIncident(event) {
  await Promise.all([readNotificationEvent(event.id),loadIncidentDetail(event.incident_id,true)]);
  $('incidents').scrollIntoView({behavior:'auto',block:'start'});
}
function setNotificationPanel(open) {
  $('notification-panel').hidden=!open;$('notification-toggle').setAttribute('aria-expanded',String(open));
  if(open){$('notification-panel').focus({preventScroll:true});void loadNotifications();}else $('notification-toggle').focus();
}
function initializeNotifications() {
  syncDesktopPreference();
  $('notification-toggle').addEventListener('click',()=>setNotificationPanel($('notification-panel').hidden));
  $('notification-close').addEventListener('click',()=>setNotificationPanel(false));
  $('notification-panel').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();setNotificationPanel(false);}});
  $('desktop-toggle').addEventListener('click',toggleDesktopNotifications);
  $('notification-read-all').addEventListener('click',readAllNotifications);
  $('notification-older').addEventListener('click',loadOlderNotifications);
}
