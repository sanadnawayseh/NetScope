"""Bounded exact statistics over the existing history classification path."""
from datetime import datetime, timedelta, timezone
import json
import math
import re
import time
from historical_analytics import AnalyticsLimit, query_history, time_window
from incident_management import CONDITIONS

MAX_ROWS=100000
MAX_INCIDENTS=20000
MIN_TREND_SAMPLES=5
METHOD='Linear interpolation at (n-1)*p (Hyndman-Fan type 7); median n>=1, p95 n>=20, p99 n>=100'


def window(args, now=None, require_targets=True):
    now=now or datetime.now(timezone.utc)
    allowed={'targets','range','start','end'}
    if set(args)-allowed or any(len(args.getlist(key))!=1 for key in args):
        raise ValueError('Unsupported or repeated analytics parameter')
    ids=None
    if 'targets' in args:
        tokens=args['targets'].split(',')
        if not 1<=len(tokens)<=4 or any(not re.fullmatch(r'[1-9][0-9]{0,9}',token) for token in tokens):
            raise ValueError('Select one to four distinct target IDs')
        ids=[int(token) for token in tokens]
        if len(set(ids))!=len(ids):raise ValueError('Select distinct target IDs')
    elif require_targets:raise ValueError('Provide targets')
    if 'start' in args or 'end' in args:
        if 'range' in args or not {'start','end'}<=args.keys():
            raise ValueError('Provide start and end together without range')
        try:
            start,end=(datetime.fromisoformat(args[key].replace('Z','+00:00')) for key in ('start','end'))
            if start.tzinfo is None or end.tzinfo is None:raise ValueError()
            start,end=start.astimezone(timezone.utc),end.astimezone(timezone.utc)
        except (ValueError,OverflowError):raise ValueError('Dates must be timezone-aware ISO timestamps') from None
        if not timedelta(0)<end-start<=timedelta(days=30) or end>now+timedelta(seconds=60):
            raise ValueError('Window must be positive, at most 30 days, and not in the future')
    else:
        start,end=(datetime.fromisoformat(value) for value in time_window(args.get('range','24h'),now))
    try:previous=start-(end-start)
    except OverflowError:raise ValueError('Preceding window is outside supported dates') from None
    return ids,start.isoformat(),end.isoformat(),previous.isoformat(),now.isoformat()


def percentile(sorted_values,p,minimum=1):
    if len(sorted_values)<minimum:return None
    position=(len(sorted_values)-1)*p
    lower=math.floor(position);upper=math.ceil(position)
    return sorted_values[lower]+(sorted_values[upper]-sorted_values[lower])*(position-lower)


def statistics(values):
    values.sort()
    n=len(values)
    return dict(count=n,mean=math.fsum(v/n for v in values) if n else None,
                median=percentile(values,.5),p95=percentile(values,.95,20),p99=percentile(values,.99,100),
                minimum=values[0] if n else None,maximum=values[-1] if n else None)


def distribution(values,bounds):
    counts=[0]*(len(bounds)+1)
    for value in values:
        bucket=next((i for i,upper in enumerate(bounds) if value<upper),len(bounds))
        counts[bucket]+=1
    return [dict(lower=0 if i==0 else bounds[i-1],upper=bounds[i] if i<len(bounds) else None,count=count) for i,count in enumerate(counts)]


class Budget:
    def __init__(self,deadline):self.deadline=deadline;self.rows=0
    def check(self):
        if time.monotonic()>self.deadline:raise AnalyticsLimit('Analytics time budget exceeded; select fewer devices or a shorter window')
    def consume(self):
        self.rows+=1
        if self.rows>MAX_ROWS:raise AnalyticsLimit('Analytics observation limit exceeded; select fewer devices or a shorter window')
        if self.rows%256==0:self.check()


def measurement_stats(conn,target,start,end,classify,normalize,budget):
    latency=[];loss=[]
    def collect(rtt,packet_loss):
        budget.consume()
        if rtt is not None:latency.append(rtt)
        if packet_loss is not None:loss.append(packet_loss)
    result=query_history(conn,target,start,end,classify,normalize,budget.deadline,
                         sample_sink=collect,end_exclusive=True,point_limit=0)
    rtt=statistics(latency)
    result['summary']['average_latency_ms']=rtt['mean']
    loss_bins=[dict(label=label,count=sum(predicate(v) for v in loss)) for label,predicate in (
        ('0%',lambda v:v==0),('(0,20]%',lambda v:0<v<=20),('(20,50]%',lambda v:20<v<=50),
        ('(50,100)%',lambda v:50<v<100),('100%',lambda v:v==100))]
    return dict(summary=result['summary'],rtt_ms=rtt,
                rtt_distribution=distribution(latency,[1,5,20,100]),
                packet_loss_pct=statistics(loss),loss_distribution=loss_bins)


def trends(current,previous):
    result={}
    specs=[('mean_rtt_ms',current['rtt_ms']['mean'],previous['rtt_ms']['mean'],current['rtt_ms']['count'],previous['rtt_ms']['count'],'ms'),
           ('mean_loss_pct',current['packet_loss_pct']['mean'],previous['packet_loss_pct']['mean'],current['packet_loss_pct']['count'],previous['packet_loss_pct']['count'],'percentage_points')]
    for protocol in ('icmp','tcp'):
        a,b=current['summary'][protocol],previous['summary'][protocol]
        specs.append((protocol+'_sampled_availability',a['percentage'],b['percentage'],a['valid_checks'],b['valid_checks'],'percentage_points'))
    for key,a,b,na,nb,unit in specs:
        enough=na>=MIN_TREND_SAMPLES and nb>=MIN_TREND_SAMPLES
        relative=(a-b)/b*100 if enough and b!=0 and unit=='ms' else None
        if relative is not None and not math.isfinite(relative):relative=None
        result[key]=dict(current_count=na,previous_count=nb,sufficient=enough,unit=unit,
                         delta=a-b if enough else None,
                         relative_change_pct=relative)
    return result


def incident_stats(conn,ids,start,end,as_of,normalize,budget):
    # Opening confirmation is stored in JSON evidence, not first_observed.
    # Scan a bounded projected incident set once to count independent transitions
    # and currently unresolved records (which may predate the selected window).
    query='''SELECT target_id,original_target_id,condition,state,first_observed,
        resolved_at,duration_seconds,triggering_measurements FROM incidents'''
    values=[]
    if ids is not None:
        query+=' WHERE target_id IN ('+','.join('?' for _ in ids)+')';values+=ids
    query+=' LIMIT ?';values.append(MAX_INCIDENTS+1)
    total=dict(opened=0,resolved=0,unresolved=0,invalid_open_timestamp_count=0,invalid_resolved_timestamp_count=0,invalid_duration_count=0,invalid_age_count=0)
    conditions={key:dict(opened=0,resolved=0,unresolved=0) for key in CONDITIONS}
    devices={};durations=[];ages=[];scanned=0
    for raw in conn.execute(query,values):
        scanned+=1
        if scanned>MAX_INCIDENTS:raise AnalyticsLimit('Incident analytics limit exceeded; filter devices')
        if scanned%256==0:budget.check()
        row=dict(raw);key=(row['target_id'],row['original_target_id'])
        group=devices.setdefault(key,dict(target_id=key[0],original_target_id=key[1],opened=0,resolved=0,unresolved=0))
        condition=conditions[row['condition']]
        def increment(metric):
            total[metric]+=1;group[metric]+=1;condition[metric]+=1
        try:opened=normalize(json.loads(row['triggering_measurements']).get('trigger',{}).get('observed_at'))
        except (ValueError,TypeError,AttributeError):opened=None
        if opened is None:total['invalid_open_timestamp_count']+=1
        elif start<=opened<end:increment('opened')
        if row['state']=='RESOLVED':
            resolved=normalize(row['resolved_at'])
            if resolved is None:total['invalid_resolved_timestamp_count']+=1
            elif start<=resolved<end:
                increment('resolved');duration=row['duration_seconds']
                if type(duration) in (int,float) and math.isfinite(duration) and duration>=0:durations.append(duration)
                else:total['invalid_duration_count']+=1
        else:
            increment('unresolved');first=normalize(row['first_observed'])
            if first is not None:
                age=(datetime.fromisoformat(as_of)-datetime.fromisoformat(first)).total_seconds()
                if age>=0:ages.append(age)
                else:total['invalid_age_count']+=1
            else:total['invalid_age_count']+=1
    ranked=sorted((group for group in devices.values() if group['opened'] or group['resolved'] or group['unresolved']),
                  key=lambda g:(-g['opened']-g['resolved']-g['unresolved'],g['original_target_id'],g['target_id'] is None))
    spans=statistics(durations)
    return dict(**total,by_condition=conditions,by_device=ranked[:100],devices_truncated=len(ranked)>100,
                resolved_sampled_span_seconds=spans,mean_sampled_time_to_recovery_seconds=spans['mean'],
                duration_distribution=distribution(durations,[60,300,3600]),
                unresolved_age_seconds=statistics(ages),unresolved_as_of=as_of,scanned_incidents=scanned)


def analyze(conn,ids,start,end,previous,as_of,classify,normalize,deadline,incidents_only=False):
    budget=Budget(deadline);budget.check()
    series=[]
    if not incidents_only:
        for target in ids:
            row=conn.execute('SELECT id,name FROM targets WHERE id=?',(target,)).fetchone()
            if row is None:raise LookupError('Device not found')
            current=measurement_stats(conn,target,start,end,classify,normalize,budget)
            prior=measurement_stats(conn,target,previous,start,classify,normalize,budget)
            series.append(dict(target_id=target,name=row['name'],current=current,previous=prior,trends=trends(current,prior)))
    incidents=incident_stats(conn,ids,start,end,as_of,normalize,budget);budget.check()
    return dict(start=start,end=end,previous_start=previous,previous_end=start,as_of=as_of,
                interval='start inclusive, end exclusive',percentile_method=METHOD,minimum_trend_samples=MIN_TREND_SAMPLES,
                observation_limit=MAX_ROWS,series=series,incidents=incidents)
