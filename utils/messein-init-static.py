#!/usr/bin/env python3

import redis
from data.static import DATA_0, DATA_1


red_cli = redis.StrictRedis(host='localhost', username=None, password=None)
# TODO replace DATA_0 by valid one
red_cli.set(b'img:static:logo-atmo-ge:png', DATA_0)
red_cli.set(b'img:static:logo-grt:png', DATA_1)
