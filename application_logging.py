"""Privacy-conscious JSON events using only standard-library logging."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

batch_id_context = ContextVar('netscope_batch_id', default=None)
FIELDS = {'batch_id', 'elapsed_ms', 'total', 'completed', 'failed', 'workers',
          'protocol', 'phase'}


class JSONFormatter(logging.Formatter):
    def format(self, record):
        # Do not serialize messages, arguments, arbitrary extras, exception text,
        # or traceback locals: these may contain destinations, paths or secrets.
        event = getattr(record, 'event', 'application_message')
        if not isinstance(event, str) or not re.fullmatch(r'[a-z_]{1,64}', event):
            event = 'application_message'
        data = dict(timestamp=datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
                    severity=record.levelname, event=event)
        for key in FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                if key == 'batch_id' and isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value):
                    data[key] = value
                elif key in ('protocol', 'phase') and value in ('icmp', 'tcp', 'load', 'submit', 'collect', 'persist', 'startup'):
                    data[key] = value
                elif key not in ('batch_id', 'protocol', 'phase') and type(value) in (int, float):
                    if math.isfinite(value):
                        data[key] = value
        if record.exc_info and record.exc_info[0]:
            data['exception_type'] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=True, allow_nan=False)


class BatchContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'batch_id'):
            record.batch_id = batch_id_context.get()
        return True


def configure_logging(logger, base, include_server=False):
    """Called by the local launcher only; fail startup on invalid configuration."""
    level = os.environ.get('NETSCOPE_LOG_LEVEL', 'INFO').upper()
    if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        raise ValueError('NETSCOPE_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL')
    def integer(name, default, low, high):
        value = os.environ.get(name, str(default))
        if not re.fullmatch(r'[0-9]{1,9}', value) or not low <= int(value) <= high:
            raise ValueError(f'{name} must be between {low} and {high}')
        return int(value)
    size = integer('NETSCOPE_LOG_MAX_BYTES', 1048576, 1024, 104857600)
    backups = integer('NETSCOPE_LOG_BACKUPS', 3, 1, 20)
    directory = Path(os.environ.get('NETSCOPE_LOG_DIR', Path(base) / 'logs'))
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(directory / 'netscope.log', maxBytes=size,
                                       backupCount=backups, encoding='utf-8')
    handlers = [logging.StreamHandler(), file_handler]
    for handler in handlers:
        handler.setFormatter(JSONFormatter())
        handler.addFilter(BatchContextFilter())
    # Replace only handlers owned by this logger; leave global/root logging alone.
    loggers = [logger]
    if include_server:
        loggers.append(logging.getLogger('werkzeug'))
    for configured in loggers:
        for handler in list(configured.handlers):
            configured.removeHandler(handler)
            handler.close()
        configured.setLevel(level)
        configured.propagate = False
        for handler in handlers:
            configured.addHandler(handler)
