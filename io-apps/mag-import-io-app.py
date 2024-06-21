#!/opt/tk-dashboard/virtualenvs/mag/venv/bin/python

import io
import logging
import time
from urllib.request import urlopen
import schedule
import PIL.Image
import PIL.Image
import PIL.ImageDraw
from lib.dashboard_io import CustomRedis, catch_log_except, wait_uptime
from conf.private_mag import REDIS_USER, REDIS_PASS, CAM_GATE_IMG_URL, CAM_DOOR_1_IMG_URL, CAM_DOOR_2_IMG_URL


# some class
class DB:
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


# some function
@catch_log_except()
def img_cam_gate_job():
    # http request
    uo_ret = urlopen(CAM_GATE_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # transform image
    pil_img = pil_img.crop((0, 0, 640, 440))
    pil_img.thumbnail([339, 228])
    # jpeg encode
    img_io = io.BytesIO()
    pil_img.save(img_io, format='JPEG')
    # store RAW jpeg to redis key
    DB.main.set('img:cam-gate:jpg', img_io.getvalue(), ex=120)


@catch_log_except()
def img_cam_door_1_job():
    # http request
    uo_ret = urlopen(CAM_DOOR_1_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # transform image
    pil_img = pil_img.crop((720, 0, 1200, 480))
    pil_img.thumbnail([339, 228])
    img_io = io.BytesIO()
    pil_img.save(img_io, format='JPEG')
    # store RAW jpeg to redis key
    DB.main.set('img:cam-door-1:jpg', img_io.getvalue(), ex=120)


@catch_log_except()
def img_cam_door_2_job():
    # http request
    uo_ret = urlopen(CAM_DOOR_2_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # transform image
    pil_img = pil_img.crop((640, 0, 1280, 440))
    pil_img.thumbnail([339, 228])
    img_io = io.BytesIO()
    pil_img.save(img_io, format='JPEG')
    # store RAW jpeg to redis key
    DB.main.set('img:cam-door-2:jpg', img_io.getvalue(), ex=120)


# main
if __name__ == '__main__':
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)
    logging.info('board-import-app started')

    # init scheduler
    schedule.every(2).seconds.do(img_cam_gate_job)
    schedule.every(2).seconds.do(img_cam_door_1_job)
    schedule.every(2).seconds.do(img_cam_door_2_job)

    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # first call
    img_cam_gate_job()
    img_cam_door_1_job()
    img_cam_door_2_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
