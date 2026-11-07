// Inventory reads never scan. Each submission consumes one explicit dialog confirmation.
let discoveryPending=null;
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
function ipv4Number(value) {
  if(typeof value!=='string')return null;
  const parts=value.split('.');
  if(parts.length!==4 || parts.some(part=>!/^\d{1,3}$/.test(part) || Number(part)>255 || String(Number(part))!==part))return null;
  return parts.reduce((n,part)=>n*256+Number(part),0);
}
function privateIPv4(value) {
  if(ipv4Number(value)===null)return false;
  const [a,b]=value.split('.').map(Number);
  return a===10 || a===127 || (a===172 && b>=16 && b<=31) || (a===192 && b===168);
}
function discoveryScope(value) {
  const match=/^([0-9.]+)\/(0|[1-9][0-9]?)$/.exec(value);
  if(!match || Number(match[2])>32 || ipv4Number(match[1])===null)throw new Error('Enter an exact private/loopback IPv4 CIDR, such as 192.168.1.0/30.');
  const prefix=Number(match[2]),size=2**(32-prefix),start=ipv4Number(match[1]);
  const address=n=>[24,16,8,0].map(bits=>Math.floor(n/2**bits)%256).join('.');
  if(start%size!==0)throw new Error('Use the network address: host bits must be zero.');
  if(!privateIPv4(match[1]) || !privateIPv4(address(start+size-1)))throw new Error('Only private or loopback IPv4 networks are allowed.');
  const count=prefix>=31?size:size-2;
  if(count<1 || count>32)throw new Error('A scan must contain 1 to 32 usable hosts.');
  return {cidr:value,count,first:address(start+(prefix>=31?0:1)),last:address(start+size-(prefix>=31?1:2))};
}
function closeDiscoveryConfirmation() {
  discoveryPending=null;
  if($('discovery-confirmation').open)$('discovery-confirmation').close();
}
function startDiscovery(event) {
  event.preventDefault();
  if(discoverySubmitting || discoveryJob || discoveryPending)return;
  let scope;
  try{scope=discoveryScope($('discovery-cidr').value.trim());}
  catch(error){$('discovery-progress').textContent=error.message;$('discovery-cidr').focus();return;}
  $('discovery-confirm-scope').textContent=`${scope.cidr} · ${scope.count} usable ${scope.count===1?'host':'hosts'}`;
  $('discovery-confirm-hosts').textContent=scope.first===scope.last?scope.first:`${scope.first} – ${scope.last}`;
  discoveryPending=scope;
  $('discovery-confirmation').showModal();$('discovery-confirm-cancel').focus();
}
async function confirmDiscovery() {
  if(discoverySubmitting || discoveryJob || !discoveryPending)return;
  const scope=discoveryPending;
  if($('discovery-cidr').value.trim()!==scope.cidr){closeDiscoveryConfirmation();$('discovery-progress').textContent='Network changed. Review the range and confirm again.';return;}
  discoverySubmitting=true;$('discovery-start').disabled=true;$('discovery-confirm-scan').disabled=true;
  closeDiscoveryConfirmation();
  try {
    const job=await api('/api/discovery/jobs',{method:'POST',body:JSON.stringify({cidr:scope.cidr,authorized:true}),signal:AbortSignal.timeout(10000)});
    discoveryJob=job.job_id;renderDiscoveryJob(job);await pollDiscovery();
  }catch(error){$('discovery-progress').textContent=`Could not confirm scan submission: ${error.message}. No automatic retry; an accepted scan may still run.`;}
  finally{
    discoverySubmitting=false;$('discovery-start').disabled=!!discoveryJob;$('discovery-confirm-scan').disabled=false;
    (discoveryJob?$('discovery-cancel'):$('discovery-start')).focus();
  }
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
  if(!discoveryRows.length)list.append(element('li','muted','No discovery observations.'));
  for(const row of discoveryRows){
    const item=element('li','discovery-row');
    item.append(element('strong','mono',row.address),element('p','muted',`${row.classification} · ${observationLabel(row.observed_at)} · Authorized scope ${row.cidr}`));
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
    $('discovery-inventory-status').textContent=`${discoveryRows.length} addresses${data.next_before_id?' · More observations available':''}.`;
  }catch(error){mapDiscoveryFresh=false;renderNetworkMap();$('discovery-inventory-status').textContent=`Inventory unavailable: ${error.message}. Displayed observations may be stale.`;}
  finally{discoveryInventoryBusy=false;}
}
async function enrollDiscovery(event) {
  event.preventDefault();if(!discoverySelection||discoveryEnrolling)return;
  discoveryEnrolling=true;$('discovery-enroll-submit').disabled=true;
  try {
    await api(`/api/discovery/observations/${discoverySelection.id}/enroll`,{method:'POST',body:JSON.stringify({name:$('discovery-name').value,port:$('discovery-port').value})});
    $('discovery-enroll-status').textContent='Added to monitoring.';
    $('discovery-enroll').hidden=true;discoverySelection=null;await load(true);
  }catch(error){$('discovery-enroll-status').textContent=`Could not enroll: ${error.message}`;}
  finally{discoveryEnrolling=false;$('discovery-enroll-submit').disabled=false;}
}
function initializeDiscovery() {
  $('discovery-form').addEventListener('submit',startDiscovery);
  $('discovery-cidr').addEventListener('input',()=>{
    closeDiscoveryConfirmation();
  });
  $('discovery-confirm-cancel').addEventListener('click',closeDiscoveryConfirmation);
  $('discovery-confirm-scan').addEventListener('click',confirmDiscovery);
  $('discovery-confirmation').addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const first=$('discovery-confirm-cancel'),last=$('discovery-confirm-scan');
    if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
  });
  $('discovery-confirmation').addEventListener('cancel',event=>{event.preventDefault();closeDiscoveryConfirmation();});
  $('discovery-confirmation').addEventListener('close',()=>{discoveryPending=null;if(!discoverySubmitting)$('discovery-start').focus();});
  $('discovery-cancel').addEventListener('click',cancelDiscovery);
  $('discovery-enroll').addEventListener('submit',enrollDiscovery);
  $('discovery-enroll-cancel').addEventListener('click',()=>{$('discovery-enroll').hidden=true;discoverySelection=null;});
}
