#!/opt/tk-dashboard/virtualenvs/loos/venv/bin/python

import argparse
import json
import logging
import os
import zlib

import paho.mqtt.client as mqtt
from conf.private_loos import (
    MQTT_FLY_KEY,
    MQTT_FLY_TOPIC,
    MQTT_HOST,
    REDIS_PASS,
    REDIS_USER,
)
from cryptography.fernet import Fernet, InvalidToken
from lib.dashboard_io import CustomRedis, catch_log_except


# some class
class DB:
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


# some functions
@catch_log_except()
def fly_handler(json_msg: str):
    # request
    data_d = json.loads(json_msg)
    # search your raw message
    try:
        raw_msg = data_d['fly_tne_raw']
    except (IndexError, KeyError):
        raise RuntimeError('key missing in mqtt message')
    # check length or raw message
    if not 20 < len(raw_msg) <= 2000:
        raise RuntimeError('raw message have a wrong size')
    # decrypt raw message (loses it's validity 20 mn after being encrypted)
    try:
        fernet = Fernet(key=MQTT_FLY_KEY)
        msg_zip_plain = fernet.decrypt(raw_msg, ttl=20*60)
    except InvalidToken:
        raise RuntimeError('unable to decrypt message')
    # decompress
    msg_plain = zlib.decompress(msg_zip_plain)
    # check format
    try:
        js_obj = json.loads(msg_plain)
    except json.JSONDecodeError:
        raise RuntimeError('decrypt message is not a valid json')
    if type(js_obj) is not dict:
        raise RuntimeError('json message is not a dict')
    try:
        titles_l = list(js_obj['nord'])
    except (TypeError, KeyError):
        raise RuntimeError('key "nord" is missing or have bad type in json message')
    # if all is ok: publish json to redis
    key = 'json:flyspray-nord'
    logging.debug(f'update redis key {key} with {titles_l}')
    DB.main.set_as_json(key, titles_l, ex=3600)


# MQTT callback
@catch_log_except()
def on_connect(mqttc, obj, flags, reason_code, properties):
    logging.debug(f'connected to MQTT broker "{MQTT_HOST}" with code "{reason_code}"')
    mqttc.subscribe(MQTT_FLY_TOPIC)


@catch_log_except()
def on_message(mqttc, obj, msg):
    logging.debug(f"message received: {msg.topic} {msg.payload.decode()}")
    # call handlers for know topics
    if msg.topic == MQTT_FLY_TOPIC:
        fly_handler(json_msg=msg.payload.decode())


if __name__ == '__main__':
    # parse command line args
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true', default=False, help='debug mode')
    app_conf = parser.parse_args()
    # extract script name
    base_name = os.path.basename(__file__)
    script_name = os.path.splitext(base_name)[0]
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG if app_conf.debug else logging.INFO)
    logging.info(f'{script_name} started')
    # init MQTT client
    mqttc = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    mqttc.connect(MQTT_HOST, port=1883, keepalive=60)
    mqttc.loop_forever()
