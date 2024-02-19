#!/usr/bin/env python3

import logging
import schedule
import time
from lib.dashboard_io import CustomRedis, catch_log_except
from conf.private_loos import REDIS_USER, REDIS_PASS


# some class
class DB:
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


@catch_log_except()
def loos_redis_export_job():
    # fill Messein share keyspace
    share_keys_l = ['img:grt-twitter-cloud:png',
                    'json:tweets:@grtgaz',
                    'json:flyspray-est']
    for k in share_keys_l:
        DB.main.execute_command('COPY', k, f'to:messein:{k}', 'REPLACE')


# main
if __name__ == '__main__':
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    logging.info('board-export-app started')

    # init scheduler
    schedule.every(2).minutes.do(loos_redis_export_job)
    # first call
    loos_redis_export_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
