"""Bounded local exports. No probes, filesystem copies or monitoring locks."""
import csv
from html import escape
import io
import json
import math
from pathlib import Path
import threading
import time
import advanced_analytics
from historical_analytics import AnalyticsLimit, TIME_SQL, eligible_samples

SLOT=threading.Lock()
MAX_MEASUREMENTS=10000
MAX_CSV_INCIDENTS=1000
MAX_PDF_INCIDENTS=200
MAX_BYTES=10*1024*1024
TOTAL_SECONDS=20
FIELDS=('record_type','target_id','device_name','checked_at','icmp_status','tcp_status','ping_ok','tcp_ok',
        'latency_ms','raw_latency_ms','latency_upper_bound_ms','latency_source','packet_loss_pct',
        'icmp_sent','icmp_received','icmp_unanswered','rtt_min_ms','rtt_max_ms','rtt_sample_count',
        'ping_error','tcp_error','incident_id','condition','state','first_observed','latest_observed',
        'resolved_at','duration_seconds','acknowledged_at','metric','value','unit')
LIMITATIONS=(
    'Sampled ICMP/TCP availability is not continuous uptime. Unknown collection outcomes are excluded independently.',
    'Exact RTT statistics describe eligible cycle means, not individual echoes; bounded/unavailable RTT stays N/A.',
    'Percentiles use linear interpolation at (n-1)*p. Median requires 1 exact cycle, p95 20, p99 100.',
    'Missing counts describe stored excluded values, not inferred missed scheduled probes. Unpositionable timestamps are outside the window.',
    'Recovery spans run from first qualifying observation to confirmed recovery, including sampling gaps and confirmation delays; they are not exact physical outage durations.',
    'Unresolved counts/ages and incident states are current at generation, not a reconstruction at the historical window end.',
    'Reports contain private device names, IPv4 addresses and measurements. Store and share them deliberately.',
)


class ReportError(Exception):
    def __init__(self,message,status=503):super().__init__(message);self.status=status


def check(deadline):
    if time.monotonic()>deadline:raise ReportError('Report time budget exceeded; select a shorter window or fewer devices')


def options(args):
    if any(len(args.getlist(key))!=1 for key in args):raise ValueError('Provide each report parameter once')
    content=args.get('content','all')
    if content not in ('all','summary','incidents','measurements'):raise ValueError('Invalid report content')
    query=args.copy();query.pop('content',None)
    return advanced_analytics.window(query),content


def snapshot(conn,window,content,fmt,classify,normalize,deadline):
    ids,start,end,previous,as_of=window
    query_deadline=min(deadline,time.monotonic()+10)
    conn.execute('BEGIN')
    conn.set_progress_handler(lambda:int(time.monotonic()>query_deadline),10000)
    try:
        analytics=advanced_analytics.analyze(conn,*window,classify,normalize,query_deadline)
        placeholders=','.join('?' for _ in ids)
        targets=[dict(row) for row in conn.execute('SELECT id,name,ip,port FROM targets WHERE id IN ('+placeholders+') ORDER BY id',ids)]
        measurements=[];incidents=[]
        if fmt=='csv' and content in ('all','measurements'):
            cursor=conn.execute(f'''SELECT * FROM results WHERE target_id IN ({placeholders})
                AND ({TIME_SQL})>=julianday(?) AND ({TIME_SQL})<julianday(?)
                ORDER BY ({TIME_SQL}),target_id,id LIMIT ?''',(*ids,start,end,MAX_MEASUREMENTS+1))
            for raw in cursor:
                check(query_deadline)
                if len(measurements)>=MAX_MEASUREMENTS:raise ReportError('CSV measurement limit exceeded; choose summary content or a shorter window',413)
                row=dict(raw);states=classify(row);rtt,loss=eligible_samples(row,states)
                measurements.append(dict(row,icmp_status=states['icmp'],tcp_status=states['tcp'],
                                         raw_latency_ms=row['latency_ms'],latency_ms=rtt,packet_loss_pct=loss))
        if content in ('all','incidents'):
            limit=MAX_PDF_INCIDENTS if fmt=='pdf' else MAX_CSV_INCIDENTS
            rows=conn.execute(f'''SELECT id,target_id,original_target_id,condition,state,first_observed,
                latest_observed,resolved_at,duration_seconds,acknowledged_at FROM incidents
                WHERE target_id IN ({placeholders}) AND julianday(first_observed)<julianday(?)
                AND (resolved_at IS NULL OR julianday(resolved_at)>=julianday(?))
                ORDER BY julianday(first_observed),id LIMIT ?''',(*ids,end,start,limit+1)).fetchall()
            if len(rows)>limit:raise ReportError('Incident detail limit exceeded; choose summary content or a shorter window',413)
            incidents=[dict(row) for row in rows]
        check(query_deadline)
        return dict(analytics=analytics,targets=targets,measurements=measurements,incidents=incidents,content=content)
    except Exception as error:
        import sqlite3
        if isinstance(error,sqlite3.OperationalError) and time.monotonic()>query_deadline:
            raise ReportError('Report query time budget exceeded; select fewer devices or a shorter window') from error
        raise
    finally:conn.set_progress_handler(None,0)


def safe_csv(value):
    if value is None:return ''
    if isinstance(value,float) and not math.isfinite(value):return ''
    if not isinstance(value,str):return value
    value=''.join(char if ord(char)>=32 or char in '\t\r\n' else '\\u%04x'%ord(char) for char in value)
    if value.startswith(('\t','\r','\n')) or value.lstrip(' \t\r\n\ufeff').startswith(('=','+','-','@')):
        return "'"+value
    return value


def flatten(value,prefix=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from flatten(item,prefix+'.'+str(key) if prefix else str(key))
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from flatten(item,prefix+'.'+str(i))
    else:yield prefix,value


def csv_report(data,deadline):
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=FIELDS,quoting=csv.QUOTE_ALL)
    writer.writeheader();names={row['id']:row['name'] for row in data['targets']}
    def write(**row):
        check(deadline);writer.writerow({key:safe_csv(value) for key,value in row.items() if key in FIELDS})
        if output.tell()>MAX_BYTES:raise ReportError('Report exceeds the size limit; select less content',413)
    a=data['analytics']
    for key in ('start','end','as_of','previous_start','previous_end','interval','percentile_method'):
        write(record_type='report',metric=key,value=a[key])
    for index,note in enumerate(LIMITATIONS):write(record_type='report',metric='limitation.'+str(index),value=note)
    for target in data['targets']:
        for key in ('ip','port'):write(record_type='device',target_id=target['id'],device_name=target['name'],metric=key,value=target[key])
    if data['content'] in ('all','summary'):
        for series in a['series']:
            for key,value in flatten({k:series[k] for k in ('current','previous','trends')}):
                write(record_type='summary',target_id=series['target_id'],device_name=series['name'],metric=key,value=value)
    if data['content'] in ('all','summary','incidents'):
        for key,value in flatten(a['incidents']):write(record_type='incident_summary',metric=key,value=value)
    for row in data['measurements']:
        write(**{**{key:row.get(key) for key in FIELDS},'record_type':'measurement','device_name':names[row['target_id']]})
    for row in data['incidents']:
        write(**{**{key:row.get(key) for key in FIELDS},'record_type':'incident','incident_id':row['id'],'device_name':names[row['target_id']]})
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def pdf_report(data,deadline):
    try:
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
        from reportlab.platypus.doctemplate import LayoutError
    except ImportError as error:raise ReportError('PDF dependencies unavailable; install requirements.txt') from error
    fontdir=Path(reportlab.__file__).parent/'fonts'
    if 'NetScopeReport' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('NetScopeReport',str(fontdir/'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('NetScopeReportBold',str(fontdir/'VeraBd.ttf')))
    supported=pdfmetrics.getFont('NetScopeReport').face.charWidths
    def text(value):
        value=str(value)
        value=''.join(char if ord(char) in supported else ('\\u%04x'%ord(char) if ord(char)<=65535 else '\\U%08x'%ord(char)) for char in value)
        return escape(value)
    styles=getSampleStyleSheet()
    for key in ('Normal','Title','Heading1','Heading2','Heading3'):
        styles[key].fontName='NetScopeReportBold' if key!='Normal' else 'NetScopeReport'
    styles['Normal'].fontSize=8.5;styles['Normal'].leading=12
    cellstyle=ParagraphStyle('ReportCell',parent=styles['Normal'],fontSize=8,leading=11,splitLongWords=True)
    story=[];a=data['analytics']
    def p(value,style='Normal'):return Paragraph(text(value),styles[style])
    def table(headers,rows,widths):
        check(deadline)
        values=[[Paragraph(text(value),cellstyle) for value in row] for row in [headers]+rows]
        result=Table(values,colWidths=widths,repeatRows=1,hAlign='LEFT')
        result.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8eef1')),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#647782')),
            ('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#d9e0e4')),('LEFTPADDING',(0,0),(-1,-1),7),
            ('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.extend([result,Spacer(1,12)])
    def number(value,unit=''):
        return f'{value:.3f}'.rstrip('0').rstrip('.')+unit if type(value) in (int,float) and math.isfinite(value) else 'N/A'
    story.extend([p('NetScope','Title'),p('Network performance report','Heading1'),
        p(f"Reporting period: {a['start']} to {a['end']} (end excluded)"),p('Generated / data snapshot: '+a['as_of']),Spacer(1,14),p('Monitored devices','Heading2')])
    table(['Target','IPv4 / TCP port'],[[f"#{t['id']} {t['name']}",f"{t['ip']} / {t['port']}"] for t in data['targets']],[310,185])
    if data['content'] in ('all','summary'):
        for index,series in enumerate(a['series']):
            if index:story.append(PageBreak())
            current=series['current'];summary=current['summary'];rtt=current['rtt_ms'];loss=current['packet_loss_pct']
            story.extend([p(series['name'],'Heading2'),p(f"{summary['record_count']} stored observations; {summary['valid_measurements']} classifiable, {summary['unknown_measurements']} unknown. Exact RTT samples {rtt['count']}; missing/bounded {summary['missing_rtt_count']}. Valid loss samples {loss['count']}; missing {summary['missing_loss_count']}."),Spacer(1,10)])
            table(['Metric','Value / sample context'],[
                ['ICMP sampled availability',number(summary['icmp']['percentage'],'%')+f"; {summary['icmp']['successful_checks']}/{summary['icmp']['valid_checks']} valid, {summary['icmp']['excluded_checks']} excluded"],
                ['TCP sampled availability',number(summary['tcp']['percentage'],'%')+f"; {summary['tcp']['successful_checks']}/{summary['tcp']['valid_checks']} valid, {summary['tcp']['excluded_checks']} excluded"],
                ['Mean / median exact cycle RTT',number(rtt['mean'],' ms')+' / '+number(rtt['median'],' ms')],
                ['p95 / p99 exact cycle RTT',number(rtt['p95'],' ms')+' / '+number(rtt['p99'],' ms')+'; minimum 20 / 100 samples'],
                ['Minimum / maximum cycle RTT',number(rtt['minimum'],' ms')+' / '+number(rtt['maximum'],' ms')],
                ['Packet loss mean / median',number(loss['mean'],'%')+' / '+number(loss['median'],'%')]], [210,285])
            table(['Exact RTT bucket (ms)','Cycle count'],[[str(row['lower'])+' to '+('below '+str(row['upper']) if row['upper'] is not None else 'unbounded'),row['count']] for row in current['rtt_distribution']],[310,185])
    if data['content'] in ('all','summary','incidents'):
        stats=a['incidents'];story.extend([p('Incident statistics','Heading2'),p(f"Opened {stats['opened']}; resolved {stats['resolved']} in window. Currently unresolved {stats['unresolved']} (OPEN + ACKNOWLEDGED)."),
            p('Resolved sampled spans: '+str(stats['resolved_sampled_span_seconds']['count'])+' valid; mean '+number(stats['mean_sampled_time_to_recovery_seconds'],' s')+'; median '+number(stats['resolved_sampled_span_seconds']['median'],' s')+'; longest '+number(stats['resolved_sampled_span_seconds']['maximum'],' s')+'.'),
            p('Current unresolved ages: '+str(stats['unresolved_age_seconds']['count'])+' valid; oldest '+number(stats['unresolved_age_seconds']['maximum'],' s')+'.'),Spacer(1,10)])
        table(['Condition','Opened','Resolved','Unresolved'],[[condition,*[counts[key] for key in ('opened','resolved','unresolved')]] for condition,counts in stats['by_condition'].items()],[225,90,90,90])
        if data['incidents']:
            story.append(p('Relevant incident records (current state)','Heading3'))
            table(['ID / target / condition','State / observations','Sampled span'],[[f"#{row['id']} / target {row['target_id']} / {row['condition']}",f"{row['state']} | first {row['first_observed']} | latest {row['latest_observed']} | resolved {row['resolved_at'] or 'N/A'}",number(row['duration_seconds'],' s')] for row in data['incidents']],[180,240,75])
        elif data['content']!='summary':story.append(p('No incident records overlap the selected window.'))
    story.extend([p('Interpretation and data quality','Heading2')]+[p(note) for note in LIMITATIONS]+[p('Unsupported font characters in PDF names are written as Unicode escapes; CSV retains Unicode text. Operator notes and full database records are not exported.')])
    class BoundedDocument(SimpleDocTemplate):
        def afterFlowable(self,flowable):
            check(deadline)
            if self.page>40:raise ReportError('PDF page limit exceeded; choose summary content',413)
    output=io.BytesIO()
    def footer(canvas,document):
        canvas.saveState();canvas.setFont('NetScopeReport',8);canvas.setFillColor(colors.HexColor('#53616b'))
        canvas.drawString(50,28,'NetScope | Local monitoring observations');canvas.drawRightString(545,28,f'Page {document.page}');canvas.restoreState()
    try:
        BoundedDocument(output,pagesize=(595,842),leftMargin=50,rightMargin=50,topMargin=42,bottomMargin=48,title='NetScope network performance report',author='NetScope').build(story,onFirstPage=footer,onLaterPages=footer)
    except LayoutError as error:raise ReportError('PDF layout exceeded supported limits; choose summary content') from error
    check(deadline)
    return output.getvalue()


def generate(db,window,content,fmt,classify,normalize):
    if fmt=='pdf' and content=='measurements':raise ReportError('Raw measurements are available in CSV; choose PDF summary or all content',400)
    if not SLOT.acquire(blocking=False):raise ReportError('Another report is generating; retry after it finishes',429)
    try:
        deadline=time.monotonic()+TOTAL_SECONDS
        with db() as conn:data=snapshot(conn,window,content,fmt,classify,normalize,deadline)
        payload=csv_report(data,deadline) if fmt=='csv' else pdf_report(data,deadline)
        check(deadline)
        if len(payload)>MAX_BYTES:raise ReportError('Report exceeds the size limit; select less content',413)
        return payload
    finally:SLOT.release()
