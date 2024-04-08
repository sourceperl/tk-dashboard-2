#!/bin/python

import argparse
import time
import logging
import schedule
from lib.dashboard_io import catch_log_except, wait_uptime


# some functions
def valid_backlight(value: str):
    try:
        value = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('value have an invalid type')
    if not 0 <= value <= 255:
        raise argparse.ArgumentTypeError(f'value {value} is not in interval 0-255')
    return value


def set_backlight(value: int):
    logging.info(f'set screen backlight to {value}')
    with open('/home/pi/mytest', 'w') as f:
        f.write(str(value))


# main
if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--backlight', type=valid_backlight, default=None,
                        help='set backlight value (0 to 255) immediately and exit')
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    args = parser.parse_args()
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG if args.debug else logging.INFO)
    logging.info('board-screen-app started')

    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # immediate tasks
    if args.backlight is not None:
        set_backlight(value=args.backlight)
        exit(0)

    # init scheduler
    schedule.every().days.at('06:00').do(set_backlight, value=160)
    schedule.every().days.at('23:00').do(set_backlight, value=80)

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
