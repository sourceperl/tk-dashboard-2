#!/usr/bin/env python3

from data.static import DATA_0, DATA_1

import redis

red_cli = redis.StrictRedis(host='localhost', username=None, password=None)
red_cli.set(b'img:static:logo-atmo:png', DATA_0)
red_cli.set(b'img:static:logo-grt:png', DATA_1)
