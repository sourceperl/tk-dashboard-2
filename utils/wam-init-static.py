#!/usr/bin/env python3

import redis
from data.static import DATA_0, DATA_2


red_cli = redis.StrictRedis(host='localhost', username=None, password=None)
red_cli.set(b'img:static:logo-atmo-hdf:png', DATA_0)
red_cli.set(b'img:static:logo-mf:png', DATA_2)
