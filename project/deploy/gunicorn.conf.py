from __future__ import unicode_literals
import multiprocessing

bind = "127.0.0.1:%(gunicorn_port)s"
workers = multiprocessing.cpu_count() * 2 + 1
loglevel = "debug"
proc_name = "%(proj_name)s"
