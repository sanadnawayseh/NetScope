"""Reproducible simulated-data exports. Optional ignored PDF for visual QA."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    with tempfile.TemporaryDirectory() as directory,patch.object(app,'DB_PATH',Path(directory)/'report.db'):
        app.init_db()
        with app.get_db() as db:
            db.execute("UPDATE targets SET name='Simulated gateway' WHERE id=1")
            db.executemany("INSERT INTO targets(id,name,ip,port,created_at) VALUES(?,?,?,443,'2026-10-22T00:00:00Z')",[(2,'Simulated Café <edge> & 漢字 😀','127.0.0.2'),(3,'Simulated device with a long descriptive operator-assigned name','127.0.0.3'),(4,'Simulated unknown observer','127.0.0.4')])
            db.executemany("""INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok,latency_source,
                latency_ms,icmp_sent,icmp_received,icmp_unanswered,packet_loss_pct)
                VALUES(?,'2026-10-22T11:30:00Z',1,1,'icmp_rtt',?,5,5,0,0)""",[(i%4+1,float(i%100+1)) for i in range(10000)])
            evidence=json.dumps({'trigger':{'observed_at':'2026-10-22T11:00:00Z'}})
            db.executemany("""INSERT INTO incidents(target_id,original_target_id,condition,state,first_observed,
                latest_observed,resolved_at,duration_seconds,triggering_result_id,latest_result_id,triggering_measurements,thresholds)
                VALUES(?,?,'tcp_unavailable','RESOLVED','2026-10-22T10:59:00Z','2026-10-22T11:30:00Z','2026-10-22T11:30:00Z',1860,1,1,?,'{}')""",[(i%4+1,i%4+1,evidence) for i in range(12)])
            db.execute("""INSERT INTO incidents(target_id,original_target_id,condition,state,first_observed,latest_observed,triggering_result_id,latest_result_id,triggering_measurements,thresholds)
                VALUES(1,1,'icmp_unavailable','ACKNOWLEDGED','2026-10-22T11:00:00Z','2026-10-22T11:30:00Z',1,1,?,'{}')""",(evidence,))
        client=app.app.test_client();results={}
        for fmt in ('csv','pdf'):
            times=[];sizes=[]
            for _ in range(3):
                started=time.perf_counter()
                response=client.get('/api/reports.'+fmt+'?targets=1,2,3,4&start=2026-10-22T11:00:00Z&end=2026-10-22T12:00:00Z&content=all')
                times.append(time.perf_counter()-started);sizes.append(len(response.data))
                assert response.status_code==200,response.json
            results[fmt]=dict(runs_seconds=times,median_seconds=statistics.median(times),bytes=sizes)
            if fmt=='pdf' and args.preview:
                folder=app.BASE/'tmp'/'pdfs';folder.mkdir(parents=True,exist_ok=True)
                (folder/'netscope-simulated-report.pdf').write_bytes(response.data)
                results['preview']=str(folder/'netscope-simulated-report.pdf')
        print(json.dumps(dict(simulated_measurements=10000,simulated_incidents=13,devices=4,**results)))


if __name__=='__main__':main()
