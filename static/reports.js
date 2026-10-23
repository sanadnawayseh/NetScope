let reportBusy=false;
function reportSelectedIds() {
  return $('report-device-source').value==='inspector'?(selectedId===null?[]:[selectedId]):comparisonIds.size?[...comparisonIds]:selectedId===null?[]:[selectedId];
}
function syncReportSelection() {
  const ids=reportSelectedIds();
  $('report-selection').textContent=ids.length?`Export devices: ${ids.map(id=>devices.find(d=>d.id===id)?.name||'#'+id).join(', ')}. Use the existing comparison checkboxes or inspector to change devices.`:'Select a monitored device in the inspector or comparison controls before exporting.';
  const custom=$('report-range').value==='custom';$('report-custom-dates').hidden=!custom;
  $('report-start').required=custom;$('report-end').required=custom;
}
function toggleReportForm() {
  const open=$('report-form').hidden;
  $('report-form').hidden=!open;$('report-toggle').setAttribute('aria-expanded',String(open));
  if(open){syncReportSelection();$('report-format').focus();}else $('report-toggle').focus();
}
async function exportReport(event) {
  event.preventDefault();if(reportBusy)return;
  const format=$('report-format').value||'csv',content=$('report-content').value||'all';
  const ids=reportSelectedIds();
  if(!ids.length){$('report-status').textContent='Select at least one monitored device before exporting.';return;}
  if(!['csv','pdf'].includes(format)||!['all','summary','incidents','measurements'].includes(content)){$('report-status').textContent='Choose a supported report format and content.';return;}
  if(format==='pdf'&&content==='measurements'){$('report-status').textContent='Raw measurements are available in CSV. Choose PDF summary or all content.';return;}
  const query=[`targets=${ids.join(',')}`,`content=${content}`];
  if($('report-range').value==='custom'){
    const start=new Date($('report-start').value),end=new Date($('report-end').value);
    if(!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime())||end<=start||end-start>30*86400000){$('report-status').textContent='Choose a positive custom window of at most 30 days.';return;}
    query.push('start='+encodeURIComponent(start.toISOString()),'end='+encodeURIComponent(end.toISOString()));
  }else query.push('range='+encodeURIComponent($('report-range').value||historyRange||'24h'));
  reportBusy=true;$('report-submit').disabled=true;$('report-submit').textContent='Generating…';
  $('report-status').textContent='Generating a local report (up to 20 seconds). No scan or probe is started.';
  try {
    const response=await fetch(`/api/reports.${format}?${query.join('&')}`,{signal:AbortSignal.timeout(25000),headers:{Accept:format==='pdf'?'application/pdf':'text/csv'}});
    if(!response.ok){let body={};try{body=await response.json();}catch{}throw new Error(body.error||`Report failed (${response.status})`);}
    const type=response.headers.get('Content-Type')||'';
    if(!type.startsWith(format==='pdf'?'application/pdf':'text/csv'))throw new Error('Unexpected report response; no file downloaded');
    const blob=await response.blob();
    if(!blob.size||blob.size>10*1024*1024)throw new Error('Report is empty or exceeds the download limit');
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    try {link.href=url;link.download=`netscope-report.${format}`;document.body.append(link);link.click();}
    finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
    $('report-status').textContent='Report download started. It contains private monitoring data; store and share it deliberately.';
  }catch(error){$('report-status').textContent=`Could not download report: ${error.message}. No automatic retry was made.`;}
  finally{reportBusy=false;$('report-submit').disabled=false;$('report-submit').textContent='Download report';}
}
function initializeReports() {
  $('report-toggle').addEventListener('click',toggleReportForm);
  $('report-form').addEventListener('submit',exportReport);
  $('report-form').addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('report-form').hidden){event.preventDefault();toggleReportForm();}});
  $('report-range').addEventListener('change',syncReportSelection);
  $('report-device-source').addEventListener('change',syncReportSelection);
}
