import os

bind    = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', 2))
threads = 2
timeout = 120
keepalive = 5
worker_class = 'sync'
accesslog = '-'
errorlog  = '-'
loglevel  = 'info'