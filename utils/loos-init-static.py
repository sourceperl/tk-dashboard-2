#!/usr/bin/env python3

import redis
from data.loos_static import DATA_0, DATA_1


red_cli = redis.StrictRedis(host='localhost', username=None, password=None)
red_cli.set(b'img:static:logo-atmo-hdf:png', DATA_0)
red_cli.set(b'img:static:logo-grt:png', DATA_1)
