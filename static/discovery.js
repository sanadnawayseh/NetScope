// Loading the inventory never starts a scan. Authorization is consumed per click.
let discoveryJob=null,discoverySubmitting=false,discoveryPolling=false,discoveryTimer=null;
let discoveryRows=[],discoverySelection=null,discoveryInventoryBusy=false,discoveryEnrolling=false;
function renderDiscoveryJob(job) {
  $('discovery-progress').textContent=`${job.cidr} · ${job.state} · ${job.completed}/${job.total} settled · ${job.progress_percentage}% · ${job.failed} unknown/failed${job.error?' · '+job.error:''}`;
  $('discovery-cancel').disabled=!['queued','running'].includes(job.state);
}
async function pollDiscovery() {
  if(!discoveryJob||discoveryPolling)return;
  discoveryPolling=true;
  try {
    const job=await api('/api/discovery/jobs/'+discoveryJob,{signal:AbortSignal.timeout(10000)});
    renderDiscoveryJob(job);await loadDiscoveryInventory();
    if(!['queued','running'].includes(job.state)){
      discoveryJob=null;$('discovery-start').disabled=false;
    }
  } catch(error){
    $('discovery-progress').textContent=`Discovery status unavailable: ${error.message}. No scan was resubmitted.`;
    if(error.status===404||error.status===403){discoveryJob=null;$('discovery-start').disabled=false;$('discovery-cancel').disabled=true;}
  } finally {
    discoveryPolling=false;
    if(discoveryJob)discoveryTimer=setTimeout(pollDiscovery,1000);
  }
}
async function startDiscovery(event) {
  event.preventDefault();
  if(discoverySubmitting||discoveryJob)return;
  if(!$('discovery-authorized').checked){$('discovery-progress').textContent='Confirm authorization for this specific CIDR first.';return;}
  discoverySubmitting=true;$('discovery-start').disabled=true;
  const cidr=$('discovery-cidr').value.trim();
  $('discovery-authorized').checked=false;
  try {
    const job=await api('/api/discovery/jobs',{method:'POST',body:JSON.stringify({cidr,authorized:true}),signal:AbortSignal.timeout(10000)});
    discoveryJob=job.job_id;renderDiscoveryJob(job);await pollDiscovery();
  }catch(error){$('discovery-progress').textContent=`Could not confirm scan submission: ${error.message}. No automatic retry; an accepted scan may still run.`;}
  finally{discoverySubmitting=false;$('discovery-start').disabled=!!discoveryJob;}
}
async function cancelDiscovery() {
  if(!discoveryJob)return;
  $('discovery-cancel').disabled=true;
  try {
    const job=await api('/api/discovery/jobs/'+discoveryJob+'/cancel',{method:'POST',body:'{}'});
    renderDiscoveryJob(job);$('discovery-progress').textContent+=' · Cancellation requested; started probes may finish.';
  }catch(error){$('discovery-progress').textContent=`Cancellation failed: ${error.message}`;}
}
function renderDiscoveryInventory() {
  const list=$('discovery-results');list.replaceChildren();
  if(!discoveryRows.length)list.append(element('li','muted','No stored discovery observations. No scan starts automatically.'));
  for(const row of discoveryRows){
    const item=element('li','discovery-row');
    item.append(element('strong','mono',row.address),element('p','muted',`${row.classification} · ${row.method} · ${observationLabel(row.observed_at)} · Authorized scope ${row.cidr}`));
    if(row.classification==='responding'){
      const exists=devices.some(target=>target.ip===row.address);
      const choose=element('button','secondary',exists?'Already monitored':'Configure monitoring');choose.type='button';choose.disabled=exists;
      choose.onclick=()=>{discoverySelection=row;$('discovery-enroll').hidden=false;$('discovery-selected').textContent=row.address;$('discovery-name').focus();};item.append(choose);
    }
    list.append(item);
  }
}
async function loadDiscoveryInventory() {
  if(discoveryInventoryBusy)return;
  discoveryInventoryBusy=true;
  try {
    const data=await api('/api/discovery/inventory?limit=200',{signal:AbortSignal.timeout(10000)});
    if(!Array.isArray(data.observations))throw new Error('Invalid inventory response');
    discoveryRows=data.observations;mapDiscoveryFresh=true;renderDiscoveryInventory();renderNetworkMap();
    $('discovery-inventory-status').textContent=`${discoveryRows.length} latest address observations displayed${data.next_before_id?' · More available through the paginated API':''}.`;
  }catch(error){mapDiscoveryFresh=false;renderNetworkMap();$('discovery-inventory-status').textContent=`Inventory unavailable: ${error.message}. Displayed observations may be stale.`;}
  finally{discoveryInventoryBusy=false;}
}
async function enrollDiscovery(event) {
  event.preventDefault();if(!discoverySelection||discoveryEnrolling)return;
  discoveryEnrolling=true;$('discovery-enroll-submit').disabled=true;
  try {
    await api(`/api/discovery/observations/${discoverySelection.id}/enroll`,{method:'POST',body:JSON.stringify({name:$('discovery-name').value,port:$('discovery-port').value})});
    $('discovery-enroll-status').textContent='Added to monitoring. Future scheduled checks will include this device.';
    $('discovery-enroll').hidden=true;discoverySelection=null;await load(true);
  }catch(error){$('discovery-enroll-status').textContent=`Could not enroll: ${error.message}`;}
  finally{discoveryEnrolling=false;$('discovery-enroll-submit').disabled=false;}
}
function initializeDiscovery() {
  $('discovery-form').addEventListener('submit',startDiscovery);
  $('discovery-cidr').addEventListener('input',()=>{
    $('discovery-authorized').checked=false;
    $('discovery-summary').textContent=`Requested scope: ${$('discovery-cidr').value||'none'} · Up to 32 usable hosts · One ICMP echo per host · Two workers · No port scan. Backend validates exact scope.`;
  });
  $('discovery-cancel').addEventListener('click',cancelDiscovery);
  $('discovery-enroll').addEventListener('submit',enrollDiscovery);
  $('discovery-enroll-cancel').addEventListener('click',()=>{$('discovery-enroll').hidden=true;discoverySelection=null;});
}
