"""Disposable simulated history benchmark; no network operations or user DB."""
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
    with tempfile.TemporaryDirectory() as directory,patch.object(app,'DB_PATH',Path(directory)/'benchmark.db'):
        app.init_db()
        with app.get_db() as db:
            db.executemany("INSERT INTO targets(id,name,ip,port,created_at) VALUES(?,?,'127.0.0.1',80,'2026-10-22T00:00:00Z')",[(i,'Simulated '+str(i)) for i in (2,3,4)])
            db.executemany("""INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok,latency_source,
                latency_ms,icmp_sent,icmp_received,icmp_unanswered,packet_loss_pct)
                VALUES(?,?,1,1,'icmp_rtt',?,5,5,0,0)""",
                [(i%4+1,'2026-10-22T11:30:00Z' if i%8<4 else '2026-10-22T10:30:00Z',float(i%100+1)) for i in range(50000)])
            evidence=json.dumps({'trigger':{'observed_at':'2026-10-22T11:00:00Z'}})
            db.executemany("""INSERT INTO incidents(target_id,original_target_id,condition,state,first_observed,
                latest_observed,resolved_at,duration_seconds,triggering_result_id,latest_result_id,triggering_measurements,thresholds)
                VALUES(?,?,'tcp_unavailable','RESOLVED','2026-10-22T10:59:00Z','2026-10-22T11:30:00Z','2026-10-22T11:30:00Z',1860,1,1,?,'{}')""",
                [(i%4+1,i%4+1,evidence) for i in range(2000)])
            plan=[tuple(row) for row in db.execute(f"EXPLAIN QUERY PLAN SELECT * FROM results WHERE target_id=? AND ({app.TIME_SQL})>=julianday(?) AND ({app.TIME_SQL})<julianday(?) ORDER BY ({app.TIME_SQL}),id",(1,'2026-10-22T11:00:00Z','2026-10-22T12:00:00Z'))]
        client=app.app.test_client();times=[];sizes=[]
        for _ in range(3):
            started=time.perf_counter()
            response=client.get('/api/analytics?targets=1,2,3,4&start=2026-10-22T11:00:00Z&end=2026-10-22T12:00:00Z')
            times.append(time.perf_counter()-started);sizes.append(len(response.data))
            assert response.status_code==200,response.json
            assert sum(s[window]['summary']['record_count'] for s in response.json['series'] for window in ('current','previous'))==50000
            assert response.json['incidents']['resolved']==2000
        print(json.dumps(dict(simulated_rows=50000,incidents=2000,devices=4,runs_seconds=times,
                             median_seconds=statistics.median(times),response_bytes=sizes,query_plan=plan)))


if __name__=='__main__':main()
