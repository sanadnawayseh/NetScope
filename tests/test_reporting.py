"""Local exports use simulated rows and disposable SQLite only."""
import csv
import io
import unittest
from unittest.mock import patch
from pypdf import PdfReader
import app
import reporting
import test_app
import test_historical_analytics
import test_advanced_analytics

WINDOW='targets=1&start=2026-10-22T11:00:00Z&end=2026-10-22T12:00:00Z'


class ReportingTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown
    insert=test_historical_analytics.HistoricalAnalyticsTests.insert
    incident=test_advanced_analytics.AdvancedAnalyticsTests.incident

    def get(self,fmt='csv',query=WINDOW):return self.client.get('/api/reports.'+fmt+'?'+query)
    def rows(self,response):return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))

    def test_csv_structure_exact_values_bounds_unknown_and_order(self):
        self.insert(latency_ms=20,checked_at='2026-10-22T11:45:00Z')
        self.insert(latency_ms=None,latency_upper_bound_ms=1,checked_at='2026-10-22T11:15:00Z')
        self.insert(latency_source=None,latency_ms=999)
        self.insert(checked_at='2026-10-22T12:00:00Z')
        response=self.get();self.assertEqual(response.status_code,200)
        rows=self.rows(response);measurements=[r for r in rows if r['record_type']=='measurement']
        self.assertEqual(len(measurements),3)
        self.assertEqual(measurements[0]['latency_ms'],'')
        self.assertEqual(measurements[0]['latency_upper_bound_ms'],'1.0')
        self.assertEqual(measurements[1]['icmp_status'],'unknown')
        self.assertEqual(measurements[1]['raw_latency_ms'],'999.0')
        self.assertEqual(measurements[-1]['latency_ms'],'20.0')
        metric=next(r for r in rows if r['metric']=='current.rtt_ms.mean')
        self.assertEqual(metric['value'],'20.0')
        self.assertEqual(tuple(rows[0]),reporting.FIELDS)

    def test_csv_formula_injection_quotes_unicode_and_control_characters(self):
        name=' =HYPERLINK("bad", "é漢字")'
        with app.get_db() as db:db.execute('UPDATE targets SET name=? WHERE id=1',(name,))
        self.insert(ping_error='@SUM(1+1)')
        response=self.get();rows=self.rows(response)
        row=next(r for r in rows if r['record_type']=='measurement')
        self.assertEqual(row['device_name'],"'"+name)
        self.assertEqual(row['ping_error'],"'@SUM(1+1)")
        for value in ('=1','+1','-1','@x','\t=1','\r=1','\ufeff=1','  +x'):
            self.assertTrue(reporting.safe_csv(value).startswith("'"))
        self.assertEqual(reporting.safe_csv(-10),-10)
        self.assertEqual(reporting.safe_csv('safe text'),'safe text')
        self.assertIn('\\u0000',reporting.safe_csv('a\0b'))

    def test_pdf_validity_metrics_headers_and_unicode_name(self):
        with app.get_db() as db:db.execute('UPDATE targets SET name=? WHERE id=1',('Café <edge> & 漢字 😀',))
        self.insert(latency_ms=20);self.insert(latency_ms=None,latency_upper_bound_ms=1)
        self.incident()
        response=self.get('pdf');self.assertEqual(response.status_code,200,response.get_data(as_text=False)[:200])
        self.assertTrue(response.data.startswith(b'%PDF-'))
        reader=PdfReader(io.BytesIO(response.data));text='\n'.join(page.extract_text() for page in reader.pages)
        for expected in ('NetScope','Network performance report','Café <edge> &','\\u6f22','\\U0001f600',
                         'ICMP sampled availability','p95 / p99','N/A','20 ms','Incident statistics','tcp_unavailable','Page 1','not exact physical outage'):
            self.assertIn(expected,text)
        self.assertEqual(reader.metadata.title,'NetScope network performance report')

    def test_incident_records_and_summary_reuse_existing_analytics(self):
        self.incident();self.incident(state='ACKNOWLEDGED',resolved_at=None,duration_seconds=None,condition='high_latency')
        rows=self.rows(self.get())
        records=[r for r in rows if r['record_type']=='incident']
        self.assertEqual({r['state'] for r in records},{'RESOLVED','ACKNOWLEDGED'})
        self.assertEqual(next(r for r in records if r['state']=='RESOLVED')['duration_seconds'],'1860.0')
        self.assertEqual(next(r for r in rows if r['record_type']=='incident_summary' and r['metric']=='unresolved')['value'],'1')
        self.assertEqual(next(r for r in rows if r['record_type']=='incident_summary' and r['metric']=='mean_sampled_time_to_recovery_seconds')['value'],'1860.0')

    def test_empty_windows_still_generate_valid_reports_without_fabricated_values(self):
        rows=self.rows(self.get())
        self.assertFalse(any(r['record_type']=='measurement' for r in rows))
        self.assertEqual(next(r for r in rows if r['metric']=='current.rtt_ms.mean')['value'],'')
        pdf=self.get('pdf');text=' '.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.data)).pages)
        self.assertIn('0 stored observations',text);self.assertIn('N/A',text)

    def test_formats_content_and_time_input_validation(self):
        for query in ('range=1h','targets=1&range=year','targets=1,1','targets=0',WINDOW+'&bad=1',WINDOW+'&content=bad',
                      WINDOW+'&content=all&content=summary','targets=1&start=x&end=y','targets=1&range=1h&start=x&end=y',
                      'targets=1&start=2026-01-01T00:00:00Z&end=2026-03-01T00:00:00Z'):
            self.assertEqual(self.get(query=query).status_code,400,query)
        self.assertEqual(self.get('pdf',WINDOW+'&content=measurements').status_code,400)
        self.assertEqual(self.get('html').status_code,400)
        self.assertEqual(self.get(query='targets=999&range=1h').status_code,404)
        for preset in ('1h','6h','24h','7d','30d'):
            self.assertEqual(self.get(query='targets=1&range='+preset+'&content=summary').status_code,200)

    def test_local_boundary_and_safe_download_headers(self):
        for fmt in ('csv','pdf'):
            response=self.get(fmt)
            self.assertEqual(response.mimetype,'text/csv' if fmt=='csv' else 'application/pdf')
            self.assertEqual(response.headers['Content-Disposition'],f'attachment; filename="netscope-report.{fmt}"')
            self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
            self.assertIn('sandbox',response.headers['Content-Security-Policy'])
            for kwargs in (dict(environ_base={'REMOTE_ADDR':'10.0.0.1'}),dict(headers={'Host':'evil.test'}),dict(headers={'Origin':'https://evil.test'})):
                self.assertEqual(self.client.get('/api/reports.'+fmt+'?'+WINDOW,**kwargs).status_code,403)

    def test_content_options_and_multiple_device_selection(self):
        target=self.client.post('/api/targets',json={'name':'Second','ip':'127.0.0.2'}).json['id']
        self.insert();self.insert(target_id=target,latency_ms=30);self.incident()
        rows=self.rows(self.get(query=WINDOW.replace('targets=1','targets=1,'+str(target))))
        self.assertEqual({r['target_id'] for r in rows if r['record_type']=='measurement'},{'1',str(target)})
        for content,excluded in (('summary','measurement'),('incidents','measurement'),('measurements','incident')):
            rows=self.rows(self.get(query=WINDOW+'&content='+content))
            self.assertFalse(any(row['record_type']==excluded for row in rows))

    def test_busy_does_not_take_monitoring_lock_and_releases_slot(self):
        reporting.SLOT.acquire()
        try:self.assertEqual(self.get().status_code,429)
        finally:reporting.SLOT.release()
        app.lock.acquire()
        try:self.assertEqual(self.get().status_code,200)
        finally:app.lock.release()
        with patch.object(reporting,'csv_report',side_effect=OSError('private path')),patch.object(app.app.logger,'exception'):
            response=self.get();self.assertEqual(response.status_code,503);self.assertNotIn('private path',response.get_data(as_text=True))
        self.assertEqual(self.get().status_code,200)

    def test_row_byte_incident_and_time_limits_return_clear_errors(self):
        for _ in range(4):self.insert()
        with patch.object(reporting,'MAX_MEASUREMENTS',3):self.assertEqual(self.get().status_code,413)
        with patch.object(reporting,'MAX_BYTES',100):self.assertEqual(self.get().status_code,413)
        self.incident();self.incident(condition='high_latency')
        with patch.object(reporting,'MAX_PDF_INCIDENTS',1):self.assertEqual(self.get('pdf').status_code,413)
        with patch.object(reporting,'TOTAL_SECONDS',-1):self.assertEqual(self.get().status_code,503)
        self.assertEqual(self.get(query=WINDOW+'&content=summary').status_code,200)

    def test_deleted_devices_database_failure_and_data_preservation(self):
        self.insert();self.incident()
        with app.get_db() as db:before=[tuple(r) for r in db.execute('SELECT * FROM results')]
        self.get();self.get('pdf')
        with app.get_db() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM results')],before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
        self.client.delete('/api/targets/1');self.assertEqual(self.get().status_code,404)
        with app.get_db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],1)
        with patch.object(app,'get_db',side_effect=app.sqlite3.OperationalError('private')),patch.object(app.app.logger,'exception'):
            self.assertEqual(self.get().status_code,503)

    def test_large_measurement_csv_is_complete_and_pdf_summaries_stay_bounded(self):
        with app.get_db() as db:
            db.executemany("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(1,'2026-10-22T11:30:00Z',0,0)",[()]*1200)
        rows=self.rows(self.get())
        self.assertEqual(sum(r['record_type']=='measurement' for r in rows),1200)
        self.assertEqual(self.get('pdf',WINDOW+'&content=summary').status_code,200)
