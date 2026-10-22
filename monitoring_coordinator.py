"""One local monitoring batch, bounded probe workers, and coordinator-only writes."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from collections import OrderedDict
from datetime import datetime, timezone
import logging
from queue import Queue
import re
import threading
import sqlite3
import time
import uuid
from application_logging import batch_id_context


def parse_worker_limit(value):
    if type(value) is int:
        workers = value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]{1,2}', value):
        workers = int(value)
    else:
        raise ValueError('NETSCOPE_MONITOR_WORKERS must be an integer between 1 and 16')
    if not 1 <= workers <= 16:
        raise ValueError('NETSCOPE_MONITOR_WORKERS must be between 1 and 16')
    return workers


class MonitoringStopped(RuntimeError):
    pass


@dataclass
class Measurement:
    row: dict
    execution_failed: bool = False


class MonitoringCoordinator:
    def __init__(self, workers=4, batch_lock=None, logger=None):
        self.workers = parse_worker_limit(workers)
        self.batch_lock = batch_lock if batch_lock is not None else threading.Lock()
        self.stop_event = threading.Event()
        self._lifecycle = threading.Lock()
        self._worker_context = threading.local()
        self._batch_thread = None
        self._batch_done = threading.Event()
        self._batch_done.set()
        self._executor = ThreadPoolExecutor(max_workers=self.workers,
                                            thread_name_prefix='netscope-probe')
        self.logger = logger if logger is not None else logging.getLogger(__name__)
        self._jobs_lock = threading.Lock()
        self._jobs = OrderedDict()
        self.job_limit = 64

    def job_status(self, job_id):
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def submit_job(self, load_targets, collect, persist):
        """Reserve the same batch lock before returning; never queue busy work."""
        if not self.batch_lock.acquire(blocking=False):
            return False
        with self._lifecycle:
            if self.stop_event.is_set():
                self.batch_lock.release()
                raise MonitoringStopped('Monitoring is stopping')
            job_id = uuid.uuid4().hex
            job = dict(job_id=job_id, state='queued', total=0, completed=0, failed=0,
                       progress_percentage=0, started_at=None, ended_at=None, error=None)
            with self._jobs_lock:
                while len(self._jobs) >= self.job_limit:
                    self._jobs.popitem(last=False)
                self._jobs[job_id] = job
            self._batch_done.clear()
            try:
                threading.Thread(target=self._run_job, args=(job_id, load_targets, collect, persist),
                                 name='netscope-manual-job').start()
            except Exception:
                self.logger.exception('Manual job thread could not start', extra={'event': 'job_start_failure'})
                self._update_job(job_id, state='failed', ended_at=self._job_time(),
                                 error='Monitoring job could not start. Retry later.')
                self.batch_lock.release()
                self._batch_done.set()
        return self.job_status(job_id)

    @staticmethod
    def _job_time():
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

    def _update_job(self, job_id, **changes):
        if changes.get('state') in ('completed', 'failed', 'cancelled'):
            changes['ended_at'] = self._job_time()
        with self._jobs_lock:
            self._jobs[job_id].update(changes)

    def _run_job(self, job_id, load_targets, collect, persist):
        cancelled = False
        def progress(total, completed, failed, cancelled_count):
            nonlocal cancelled
            cancelled = cancelled_count > 0
            self._update_job(job_id, total=total, completed=completed, failed=failed,
                             progress_percentage=(completed + failed) * 100 / total if total else 100)
        try:
            self._update_job(job_id, state='running', started_at=self._job_time())
            result = self.run_batch(load_targets, collect, persist, _reserved=True, progress=progress)
            state = 'cancelled' if cancelled else 'failed' if result['failed'] else 'completed'
            self._update_job(job_id, state=state,
                             error=('Monitoring stopped before all checks completed.' if cancelled else
                                    'Some checks failed to execute or persist. See local logs.' if result['failed'] else None))
        except MonitoringStopped:
            self._update_job(job_id, state='cancelled', error='Monitoring stopped before the job started.')
        except BaseException:
            # A background thread has no HTTP caller to receive an exception.
            # Mark terminal, retain safe diagnostics, and always release admission.
            self.logger.exception('Manual monitoring job failed', extra={'event': 'job_failure'})
            self._update_job(job_id, state='failed', error='Monitoring job failed. See local logs.')
        finally:
            self._update_job(job_id, ended_at=self._job_time())
            with self._lifecycle:
                self._batch_thread = None
                self.batch_lock.release()
                self._batch_done.set()

    def _collect(self, collect, target, batch_id=None):
        # A queued job may be picked up between stop being signalled and the
        # executor cancelling its queue. Give starting work the same stop gate.
        with self._lifecycle:
            if self.stop_event.is_set():
                raise CancelledError()
        self._worker_context.collecting = True
        token = batch_id_context.set(batch_id)
        try:
            return collect(target)
        finally:
            batch_id_context.reset(token)
            self._worker_context.collecting = False

    def run_batch(self, load_targets, collect, persist, *, _reserved=False, progress=None):
        """Return counts, False when busy, or raise MonitoringStopped.

        The caller runs persistence. No SQLite connection crosses a thread.
        Keep the mutation lock until every submitted future has been processed.
        """
        if not _reserved and not self.batch_lock.acquire(blocking=False):
            return False
        admitted = False
        futures = {}
        completed = Queue()
        interrupted = None
        batch_id = uuid.uuid4().hex
        token = batch_id_context.set(batch_id)
        started = time.monotonic()
        phase = 'load'
        fields = {'batch_id': batch_id}
        try:
            with self._lifecycle:
                if self.stop_event.is_set():
                    raise MonitoringStopped('Monitoring is stopping; no new checks were submitted')
                self._batch_done.clear()
                self._batch_thread = threading.get_ident()
                admitted = True
            self.logger.info('Monitoring batch started', extra=dict(fields, event='batch_started', workers=self.workers))
            targets = list(load_targets())
            if progress:
                progress(len(targets), 0, 0, 0)
            phase = 'submit'
            # Serialize admission/submission against stop, never worker execution.
            try:
                with self._lifecycle:
                    if not self.stop_event.is_set():
                        for target in targets:
                            try:
                                future = self._executor.submit(self._collect, collect, target, batch_id)
                            except Exception:
                                self.logger.exception('Could not submit remaining monitoring tasks', extra=dict(fields, event='batch_submission_failure', phase='submit'))
                                break
                            futures[future] = target
            except BaseException as error:
                interrupted = error
                self.request_stop()
            for future in futures:
                # Callbacks fire for cancellation too. as_completed() can hang
                # on futures cancelled by shutdown in Python 3.10.
                future.add_done_callback(completed.put)
            failed = len(targets) - len(futures)
            cancelled_count = failed if self.stop_event.is_set() else 0
            successful = 0
            if progress:
                progress(len(targets), successful, failed, cancelled_count)
            processed = 0
            while processed < len(futures):
                try:
                    future = completed.get()
                except BaseException as error:
                    # Ctrl+C in a synchronous batch must drain its results before
                    # releasing the lock. Shutdown never waits under _lifecycle.
                    interrupted = error
                    self.request_stop()
                    continue
                processed += 1
                target = futures[future]
                try:
                    phase = 'collect'
                    measurement = future.result()
                    phase = 'persist'
                    persist(measurement.row)
                    if measurement.execution_failed:
                        failed += 1
                    else:
                        successful += 1
                except CancelledError:
                    failed += 1
                    cancelled_count += 1
                except Exception as error:
                    failed += 1
                    event = 'database_failure' if isinstance(error, sqlite3.Error) else 'monitor_task_failure'
                    self.logger.exception('Monitoring execution/persistence failed', extra=dict(fields, event=event, phase=phase))
                except BaseException as error:
                    # Do not replay an interrupted write: its commit may already
                    # have happened. Finish all other work, then propagate.
                    failed += 1
                    interrupted = error
                    self.request_stop()
                if progress:
                    progress(len(targets), successful, failed, cancelled_count)
            if interrupted is not None:
                raise interrupted
            self.logger.log(logging.WARNING if failed else logging.INFO, 'Monitoring batch completed',
                            extra=dict(fields, event='batch_completed', total=len(targets), completed=successful,
                                       failed=failed, elapsed_ms=round((time.monotonic()-started)*1000, 3)))
            return {'total': len(targets), 'completed': successful, 'failed': failed}
        except Exception as error:
            if admitted:
                self.logger.exception('Monitoring batch failed', extra=dict(fields,
                    event='database_failure' if isinstance(error, sqlite3.Error) else 'batch_failure',
                    phase=phase, elapsed_ms=round((time.monotonic()-started)*1000, 3)))
            raise
        finally:
            batch_id_context.reset(token)
            if not _reserved:
                with self._lifecycle:
                    self.batch_lock.release()
                    if admitted:
                        self._batch_thread = None
                        self._batch_done.set()

    def request_stop(self):
        """Reject admission and cancel queued tasks, without waiting for workers."""
        with self._lifecycle:
            self.stop_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def shutdown(self):
        """Call from the lifecycle owner, not a probe/persistence callback."""
        with self._lifecycle:
            if (self._batch_thread == threading.get_ident()
                    or getattr(self._worker_context, 'collecting', False)):
                raise RuntimeError('Use request_stop() inside monitoring callbacks; shutdown() would wait on itself')
        self.request_stop()
        self._batch_done.wait()
        self._executor.shutdown(wait=True, cancel_futures=True)
