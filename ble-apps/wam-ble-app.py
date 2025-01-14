#!/opt/tk-dashboard/virtualenvs/wam/venv/bin/python

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from struct import pack, unpack

# sudo pip3 install bleak==0.22.3
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from conf.private_wam import ID_NAME_DICT
from lib.custom_redis import CustomRedis

# a logger for this script
logger = logging.getLogger(__name__)


# some class
class DB:
    main = CustomRedis(host='localhost', socket_timeout=4, socket_keepalive=True)


@dataclass
class BleMessage:
    address: str

    @property
    def name(self) -> str:
        global id_name_dict
        try:
            return id_name_dict[self.address]
        except KeyError:
            return ''


@dataclass
class TP357Msg(BleMessage):
    temperature_c: float
    humidity_p: int


@dataclass
class SwitchBotMsg(BleMessage):
    temperature_c: float
    humidity_p: int
    battery_p: int = None


# some functions
def on_ble_msg(ble_msg: BleMessage):
    # date and time of arrival
    receive_dt = datetime.now().astimezone()
    # debug message
    logger.debug(f'sensor {ble_msg.name}: {ble_msg}')
    # store sensors values to DB
    if isinstance(ble_msg, TP357Msg):
        # sensor
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:model', dict(value='TP357'), ex=3600)
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:last_rx', dict(value=receive_dt.isoformat()), ex=3600)
        # data: temperature and humidity
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:temp_c', dict(value=ble_msg.temperature_c), ex=3600)
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:hum_p', dict(value=ble_msg.humidity_p), ex=3600)
    elif isinstance(ble_msg, SwitchBotMsg):
        # sensor
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:model', dict(value='SwitchBot'), ex=3600)
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:last_rx', dict(value=receive_dt.isoformat()), ex=3600)
        # SwitchBot sensor have battery level as percent (not available on passive scan)
        if ble_msg.battery_p is not None:
            DB.main.set_as_json(f'json:ble:{ble_msg.name}:batt_lvl', dict(value=ble_msg.battery_p), ex=3600)
        # data: temperature and humidity
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:temp_c', dict(value=ble_msg.temperature_c), ex=3600)
        DB.main.set_as_json(f'json:ble:{ble_msg.name}:hum_p', dict(value=ble_msg.humidity_p), ex=3600)


def ble_detection_callback(device: BLEDevice, adv_data: AdvertisementData) -> None:
    global id_name_dict, while_list_set
    # skip unauthorized bluetooth addresses
    if device.address not in while_list_set:
        return
    # debug
    logger.debug(f'rx: [addr {device.address}] {adv_data!r}')
    # decode messages from different types of sensors
    # ThermoPro TP357 sensor messages
    if device.name.startswith('TP357'):
        # ensure message have manufacturer data set
        if not adv_data.manufacturer_data:
            return
        # TP357 uses the Bluetooth assigned enterprise IDs as regular data, so we need a conversion step
        # adv_data.manufacturer_data -> manuf_data bytearray
        # {comp_id: data_bytes, ...} -> [comp_id lsb, comp_id msb, data_bytes[0], data_bytes[1],...]
        first_key = next(iter(adv_data.manufacturer_data))
        first_value = adv_data.manufacturer_data[first_key]
        manuf_data = pack('<H', first_key) + first_value
        # ensure valid length
        if len(manuf_data) < 6:
            return
        # extract temperature and humidity fields
        (temp, hum) = unpack('<hB', manuf_data[1:4])[:2]
        temp_c = float(temp/10)
        hum_p = int(hum)
        # notify message
        on_ble_msg(TP357Msg(address=device.address, temperature_c=temp_c, humidity_p=hum_p))
    # SwitchBot W3400010 sensor message with company ID == 0x0969 (Woan technology)
    elif 0x0969 in adv_data.manufacturer_data:
        # add temp and hum data from first manuf data field
        manuf_data = adv_data.manufacturer_data[0x0969]
        # extract temperature and humidity fields
        try:
            # temperature
            if manuf_data[9] < 0x80:
                temp_c = -manuf_data[9] + manuf_data[8] / 10.0
            else:
                temp_c = manuf_data[9] - 0x80 + manuf_data[8] / 10.0
            # humidity
            hum_p = manuf_data[10]
        except IndexError:
            return
        # extract optional battery level
        try:
            svc_data = adv_data.service_data['0000fd3d-0000-1000-8000-00805f9b34fb']
            bat_p = svc_data[2] & 0x7f
        except (KeyError, IndexError):
            bat_p = None
        # notify message
        on_ble_msg(SwitchBotMsg(address=device.address, temperature_c=temp_c, humidity_p=hum_p, battery_p=bat_p))


def wait_uptime(min_s: float):
    """block until system uptime reach min_s seconds"""
    while True:
        uptime = float(open('/proc/uptime', 'r').readline().split()[0])
        if uptime > min_s:
            break
        time.sleep(0.1)


async def ble_task():
    scanner = BleakScanner(ble_detection_callback, scanning_mode='active')
    while True:
        # scan for 10 seconds
        await scanner.start()
        await asyncio.sleep(10)
        await scanner.stop()
        # 50 seconds break
        await asyncio.sleep(50)


if __name__ == '__main__':
    # parse command line args
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='debug mode')
    parser.add_argument('-w', '--wait-up', action='store', type=float, default=30.0,
                        help='wait min sys uptime before start (default is 30s)')
    # populate global app_conf
    app_conf = parser.parse_args()
    # at startup: wait system ready (DB, display, RTC sync...)
    # set min uptime (default is 30s)
    wait_uptime(app_conf.wait_up)
    # logging setup
    logging.basicConfig(format='%(asctime)s - %(name)-20s - %(message)s', level=logging.INFO)
    app_log_lvl = logging.DEBUG if app_conf.debug else logging.INFO
    logger.setLevel(app_log_lvl)
    logging.getLogger('lib.custom_redis').setLevel(app_log_lvl)
    # startup message
    logger.info('board-ble-app started')
    # format id_name_dict to match address format (like 'AA:BB:CC:DD:EE:FF')
    id_name_dict = {key.upper().replace('-', ':'): value.lower().strip() for key, value in ID_NAME_DICT.items()}
    # build a set of authorized addresses
    while_list_set = set(id_name_dict)
    # start aio task(s)
    loop = asyncio.get_event_loop()
    loop.create_task(ble_task())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
