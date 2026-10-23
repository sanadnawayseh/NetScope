let advancedRequest=0,advancedData=null;
function analyticsValue(value,unit='') {return Number.isFinite(value)?`${fmt(value)}${unit}`:'N/A';}
function analyticsTable(headers,rows) {
  const table=element('table','analytics-table'),head=element('thead'),body=element('tbody'),title=element('tr');
  for(const text of headers){const cell=element('th','',text);cell.setAttribute('scope','col');title.append(cell);}head.append(title);
  for(const values of rows){const row=element('tr');for(const value of values)row.append(element('td','',value));body.append(row);}
  table.append(head,body);return table;
}
function analyticsDistribution(rows,total,unit) {
  return analyticsTable(['Distribution','Samples','Share'],rows.map(row=>[
    row.label||`${row.lower}–${row.upper===null?'∞':('<'+row.upper)} ${unit}`,
    row.count,total?`${fmt(row.count*100/total)}%`:'N/A']));
}
function renderAdvancedAnalytics() {
  if(!advancedData)return;
  const data=advancedData,summary=$('analytics-summary');summary.replaceChildren();
  $('analytics-window').textContent=`${observationLabel(data.start)} to ${observationLabel(data.end)} (end excluded). Preceding equal window: ${observationLabel(data.previous_start)} to ${observationLabel(data.previous_end)}. Unresolved incidents are current as of ${observationLabel(data.as_of)}.`;
  if(!data.series.length)summary.append(element('p','muted','Global incident analytics · no measurement devices selected. Deleted-device incidents remain included.'));
  for(const series of data.series){
    const card=element('article','analytics-card'),current=series.current,s=current.summary,rtt=current.rtt_ms,loss=current.packet_loss_pct;
    card.append(element('h3','',`${series.name} · #${series.target_id}`),element('p','muted',`${s.record_count} stored observations · ${s.valid_measurements} classifiable · ${s.unknown_measurements} unknown · ${s.invalid_timestamp_count} invalid zoned timestamps in SQL window`),
      element('p','muted',`Exact RTT ${rtt.count} samples · ${s.missing_rtt_count} unavailable/bounded · valid loss ${loss.count} · missing/invalid loss ${s.missing_loss_count}`));
    card.append(analyticsTable(['Exact cycle RTT','Value'],[['Mean',analyticsValue(rtt.mean,' ms')],['Median',analyticsValue(rtt.median,' ms')],['p95 (≥20 exact cycles)',analyticsValue(rtt.p95,' ms')],['p99 (≥100 exact cycles)',analyticsValue(rtt.p99,' ms')],['Minimum / maximum',`${analyticsValue(rtt.minimum,' ms')} / ${analyticsValue(rtt.maximum,' ms')}`]]));
    card.append(element('h4','','Exact cycle RTT distribution'),analyticsDistribution(current.rtt_distribution,rtt.count,'ms'),
      element('p','',`Packet loss mean ${analyticsValue(loss.mean,'%')} · median ${analyticsValue(loss.median,'%')}`),analyticsDistribution(current.loss_distribution,loss.count,'%'));
    for(const protocol of ['icmp','tcp']){const metric=s[protocol];card.append(element('p','muted',`${protocol.toUpperCase()} sampled availability ${analyticsValue(metric.percentage,'%')} · ${metric.successful_checks}/${metric.valid_checks} valid · ${metric.excluded_checks} excluded`));}
    card.append(element('h4','','Change from preceding equal window'));
    const names={mean_rtt_ms:'Mean exact RTT',mean_loss_pct:'Mean packet loss',icmp_sampled_availability:'ICMP sampled availability',tcp_sampled_availability:'TCP sampled availability'};
    for(const [key,label] of Object.entries(names)){
      const trend=series.trends[key];
      card.append(element('p','muted',`${label}: ${trend.sufficient?`${trend.delta>0?'+':''}${analyticsValue(trend.delta)} ${trend.unit==='ms'?'ms':'percentage points'}${trend.relative_change_pct!==null?' ('+analyticsValue(trend.relative_change_pct,'%')+')':''}`:'N/A · needs ≥5 valid samples in each window'} · ${trend.current_count} current / ${trend.previous_count} previous`));
    }
    summary.append(card);
  }
  const incidents=data.incidents,panel=$('analytics-incidents');panel.replaceChildren();
  panel.append(element('h3','','Incident statistics'),element('p','',`${incidents.opened} opened / ${incidents.resolved} resolved during window · ${incidents.unresolved} currently unresolved (open + acknowledged)`),
    element('p','muted',`Resolved sampled recovery spans: ${incidents.resolved_sampled_span_seconds.count} valid · mean ${analyticsValue(incidents.mean_sampled_time_to_recovery_seconds,' s')} · median ${analyticsValue(incidents.resolved_sampled_span_seconds.median,' s')} · longest ${analyticsValue(incidents.resolved_sampled_span_seconds.maximum,' s')}`),
    element('p','muted',`Unresolved age since first qualifying observation: ${incidents.unresolved_age_seconds.count} valid · mean ${analyticsValue(incidents.unresolved_age_seconds.mean,' s')} · oldest ${analyticsValue(incidents.unresolved_age_seconds.maximum,' s')} · includes incidents outside the selected window.`));
  panel.append(analyticsTable(['Condition','Opened','Resolved','Unresolved'],Object.entries(incidents.by_condition).map(([condition,value])=>[incidentConditions[condition]||condition,value.opened,value.resolved,value.unresolved])),
    element('h4','','Resolved sampled span distribution'),analyticsDistribution(incidents.duration_distribution,incidents.resolved_sampled_span_seconds.count,'s'));
  const name=row=>row.target_id===null?`Removed device #${row.original_target_id}`:devices.find(d=>d.id===row.target_id)?.name||`Device #${row.target_id}`;
  panel.append(analyticsTable(['Device','Opened','Resolved','Unresolved'],incidents.by_device.map(row=>[name(row),row.opened,row.resolved,row.unresolved])));
  if(incidents.devices_truncated)panel.append(element('p','muted','Device breakdown limited to 100 groups; totals cover all scanned incidents. Filter devices for more detail.'));
  panel.append(element('p','muted',`Excluded evidence: ${incidents.invalid_open_timestamp_count} invalid opening timestamps · ${incidents.invalid_resolved_timestamp_count} invalid recovery timestamps · ${incidents.invalid_duration_count} invalid spans · ${incidents.invalid_age_count} invalid ages.`));
}
async function loadAdvancedAnalytics() {
  syncReportSelection();
  const token=++advancedRequest;
  const ids=comparisonIds.size?[...comparisonIds]:selectedId!==null?[selectedId]:[];
  const global=$('analytics-scope').value==='all_incidents'||!ids.length;
  $('analytics-status').textContent='Loading bounded analytics…';
  try {
    const data=await api(`/api/analytics${global?'/incidents':''}?${global?'':`targets=${ids.join(',')}&`}range=${historyRange||'24h'}`,{signal:AbortSignal.timeout(15000)});
    if(token!==advancedRequest)return;
    advancedData=data;renderAdvancedAnalytics();
    $('analytics-status').textContent=`${global?'All stored incidents':'Selected device'+(ids.length>1?'s':'')} · ${historyRange||'24h'} · percentile method: linear interpolation · statistics use full bounded windows, not chart truncation.`;
  }catch(error){
    if(token!==advancedRequest)return;
    advancedData=null;$('analytics-summary').replaceChildren();$('analytics-incidents').replaceChildren();$('analytics-window').textContent='';
    $('analytics-status').textContent=`Analytics unavailable: ${error.message}. Choose fewer devices/a shorter window or wait for the next refresh.`;
  }
}
function initializeAdvancedAnalytics() {
  initializeReports();
  $('analytics-scope').addEventListener('change',loadAdvancedAnalytics);
  $('analytics-refresh').addEventListener('click',loadAdvancedAnalytics);
}
