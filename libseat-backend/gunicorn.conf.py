import multiprocessing, os
bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
workers = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
threads = 8
worker_class = "gthread"
timeout = 30
keepalive = 5
