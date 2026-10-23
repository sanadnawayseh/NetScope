"""Offline clean-install verification from configured installed distributions.

Run from a development environment with requirements-dev.lock.txt installed.
No system-site-packages, network download, real database, discovery or probes.
Repacked installed wheels verify isolation, not upstream artifact provenance.
"""
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def pins(path):
    result={}
    for line in path.read_text(encoding='utf-8').splitlines():
        line=line.split('#',1)[0].strip()
        if not line:continue
        if line.startswith('-r '):result.update(pins(path.parent/line[3:].strip()));continue
        requirement,_,marker=line.partition(';')
        if marker.strip()=="python_version < '3.11'" and sys.version_info>=(3,11):continue
        match=re.fullmatch(r'([A-Za-z0-9_-]+)==([A-Za-z0-9_.]+)',requirement.strip())
        if match is None:raise ValueError('Unsupported lock entry: '+line)
        result[match[1]]=match[2]
    return result


def repack(name,version,folder):
    dist=importlib.metadata.distribution(name)
    if dist.version!=version:raise RuntimeError(name+' does not match the lock; install requirements-dev.lock.txt first')
    files=dist.files or []
    wheel=next(file for file in files if str(file).endswith('.dist-info/WHEEL'))
    info=str(wheel.parent).replace('\\','/')
    tags=[line[5:].strip() for line in dist.read_text('WHEEL').splitlines() if line.startswith('Tag: ')]
    tag=next((tag for tag in tags if not tag.startswith('py2-')),tags[0])
    root=Path(dist.locate_file('')).resolve()
    output=folder/(name.replace('-','_').lower()+'-'+version+'-'+tag+'.whl')
    records=[]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for entry in files:
            location=Path(dist.locate_file(entry)).resolve()
            try:relative=location.relative_to(root).as_posix()
            except ValueError:continue  # pip regenerates console launchers from entry_points.txt.
            if not location.is_file() or relative.endswith(('.pyc','/RECORD','/INSTALLER','/REQUESTED','/direct_url.json')):continue
            content=location.read_bytes();archive.writestr(relative,content)
            digest=base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
            records.append((relative,'sha256='+digest,len(content)))
        record=info+'/RECORD';records.append((record,'',''))
        stream=io.StringIO(newline='');csv.writer(stream).writerows(records)
        archive.writestr(record,stream.getvalue())
    return output


SMOKE=r'''
import json,os,runpy,sqlite3,threading
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from monitoring_coordinator import MonitoringCoordinator,Measurement
root=Path.cwd();database=Path(os.environ['NETSCOPE_DB']);seen=threading.Event()
class SimulatedCoordinator(MonitoringCoordinator):
 def run_batch(self,load,collect,persist,**kwargs):
  def sample(target):
   from datetime import datetime,timezone
   return Measurement(dict(target_id=target['id'],checked_at=datetime.now(timezone.utc).isoformat(),
    ping_ok=1,tcp_ok=1,latency_source='icmp_rtt',latency_ms=2.0,
    icmp_sent=5,icmp_received=5,icmp_unanswered=0,packet_loss_pct=0.0))
  def save(row):persist(row);seen.set()
  return super().run_batch(load,sample,save,**kwargs)
def serve(self,**kwargs):
 assert kwargs==dict(host='127.0.0.1',port=5000,debug=False,use_reloader=False),kwargs
 assert seen.wait(8),'Automatic monitoring did not persist its simulated sample'
def launch():
 seen.clear()
 with patch('monitoring_coordinator.MonitoringCoordinator',SimulatedCoordinator),patch.object(Flask,'run',serve),patch('subprocess.run',side_effect=AssertionError('No probe commands allowed')):
  runpy.run_path(str(root/'app.py'),run_name='__main__')
launch()
with sqlite3.connect(database) as db:
 assert db.execute('PRAGMA user_version').fetchone()[0]==5
 assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
 assert db.execute('SELECT COUNT(*) FROM results').fetchone()[0]>=1
legacy=database.parent/'legacy.db'
with sqlite3.connect(legacy) as db:
 db.execute('CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, port INTEGER NOT NULL, created_at TEXT NOT NULL)')
 db.execute('CREATE TABLE results (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE, checked_at TEXT NOT NULL, ping_ok INTEGER NOT NULL, latency_ms REAL, tcp_ok INTEGER NOT NULL, error TEXT)')
 db.execute("INSERT INTO targets VALUES(42,'Existing fixture','127.0.0.1',80,'old')")
 db.execute("INSERT INTO results VALUES(7,42,'old',1,2.5,1,NULL)")
os.environ['NETSCOPE_DB']=str(legacy);launch()
with sqlite3.connect(legacy) as db:
 assert db.execute('PRAGMA user_version').fetchone()[0]==5
 assert db.execute('SELECT latency_ms FROM results WHERE id=7').fetchone()[0]==2.5
 assert db.execute('SELECT name FROM targets WHERE id=42').fetchone()[0]=='Existing fixture'
events=[json.loads(line)['event'] for line in (Path(os.environ['NETSCOPE_LOG_DIR'])/'netscope.log').read_text().splitlines()]
for name in ('application_starting','application_started','application_stopping','application_stopped'):assert events.count(name)>=2,(name,events)
print('Launcher smoke passed: fresh and v0 migration, automatic simulated persistence, local bind flags, graceful shutdown, preserved legacy row')
'''


def run(command,env,timeout=120):
    subprocess.run([str(part) for part in command],cwd=ROOT,env=env,check=True,timeout=timeout)


def main():
    locked=pins(ROOT/'requirements-dev.lock.txt')
    with tempfile.TemporaryDirectory(prefix='netscope-clean-install-') as directory:
        temp=Path(directory);wheels=temp/'wheels';wheels.mkdir()
        for name,version in locked.items():repack(name,version,wheels)
        environment=temp/'venv';venv.EnvBuilder(with_pip=True,system_site_packages=False).create(environment)
        python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        env=os.environ.copy();env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
        for name in list(env):
            if name.startswith('NETSCOPE_'):del env[name]
        env.update(NETSCOPE_DB=str(temp/'fresh.db'),NETSCOPE_LOG_DIR=str(temp/'logs'),PYTHONDONTWRITEBYTECODE='1')
        install=[python,'-m','pip','--isolated','--disable-pip-version-check','install','--no-index','--no-cache-dir','--find-links',wheels,'-r']
        run([*install,ROOT/'requirements.lock.txt'],env,90)
        run([python,'-B','-c',"import sys,flask,reportlab,importlib.util; assert sys.prefix!=sys.base_prefix; assert importlib.util.find_spec('pypdf') is None; print('Runtime-only clean install passed; no test reader or system-site-packages')"],env)
        run([*install,ROOT/'requirements-dev.lock.txt'],env,90)
        run([python,'-m','pip','check'],env)
        run([python,'-B','-c',SMOKE],env,35)
        run([python,'-B','-m','unittest','discover','-s','tests','-v'],env,120)
        print(json.dumps(dict(clean_environment=True,source='repacked installed distributions; offline',
                             python=sys.version.split()[0],locked_packages=locked,launcher_smoke='passed')))


if __name__=='__main__':main()
