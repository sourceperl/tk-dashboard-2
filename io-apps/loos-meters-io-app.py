#!/opt/tk-dashboard/virtualenvs/loos/venv/bin/python

import logging
import time
import schedule
from pyHMI.DS_ModbusTCP import ModbusTCPDevice
from pyHMI.DS_Redis import RedisDevice
from pyHMI.Tag import Tag
from lib.dashboard_io import catch_log_except, wait_uptime
from conf.private_loos import REDIS_USER, REDIS_PASS


# some const
LTX_IP = '192.168.0.62'
# modbus address for IEM 3155 and 2155
AD_3155_LIVE_PWR = 3059
AD_2155_LIVE_PWR = 3053
AD_3155_INDEX_PWR = 3205
AD_2155_INDEX_PWR = 3205


# some class
class Devices(object):
    # redis datasource
    rd = RedisDevice(host='localhost', client_adv_args=dict(username=REDIS_USER, password=REDIS_PASS))
    # modbus datasource
    # meter 'garage'
    meter_garage = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=1)
    meter_garage.add_floats_table(AD_3155_LIVE_PWR)
    meter_garage.add_longs_table(AD_3155_INDEX_PWR)
    # meter 'cold water'
    meter_cold_water = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=2)
    meter_cold_water.add_floats_table(AD_3155_LIVE_PWR)
    meter_cold_water.add_longs_table(AD_3155_INDEX_PWR)
    # meter 'light'
    meter_light = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=3)
    meter_light.add_floats_table(AD_3155_LIVE_PWR)
    meter_light.add_longs_table(AD_3155_INDEX_PWR)
    # meter 'tech'
    meter_tech = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=4)
    meter_tech.add_floats_table(AD_3155_LIVE_PWR)
    meter_tech.add_longs_table(AD_3155_INDEX_PWR)
    # meter 'CTA' (air process)
    meter_cta = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=5)
    meter_cta.add_floats_table(AD_3155_LIVE_PWR)
    meter_cta.add_longs_table(AD_3155_INDEX_PWR)
    # meter 'heater room'
    meter_heat = ModbusTCPDevice(LTX_IP, timeout=2.0, refresh=2.0, unit_id=6)
    meter_heat.add_floats_table(AD_2155_LIVE_PWR)
    meter_heat.add_longs_table(AD_2155_INDEX_PWR)


class Tags(object):
    # redis tags
    RD_TOTAL_PWR = Tag(0, src=Devices.rd, ref={'type': 'int',
                                               'key': 'int:loos_elec:pwr_act',
                                               'ttl': 60})
    RD_TODAY_WH = Tag(0.0, src=Devices.rd, ref={'type': 'float',
                                                'key': 'float:loos_elec:today_wh',
                                                'ttl': 86400})
    RD_YESTERDAY_WH = Tag(0.0, src=Devices.rd, ref={'type': 'float',
                                                    'key': 'float:loos_elec:yesterday_wh',
                                                    'ttl': 172800})
    RD_TIMESTAMP_WH = Tag(0.0, src=Devices.rd, ref={'type': 'float',
                                                    'key': 'float:loos_elec:timestamp_wh',
                                                    'ttl': 172800})
    # modbus tags
    GARAGE_PWR = Tag(0.0, src=Devices.meter_garage, ref={'type': 'float', 'addr': AD_3155_LIVE_PWR, 'span': 1000})
    GARAGE_I_PWR = Tag(0, src=Devices.meter_garage, ref={'type': 'long', 'addr': AD_3155_INDEX_PWR, 'span': 1 / 1000})
    COLD_WATER_PWR = Tag(0.0, src=Devices.meter_cold_water, ref={'type': 'float',
                                                                 'addr': AD_3155_LIVE_PWR,
                                                                 'span': 1000})
    COLD_WATER_I_PWR = Tag(0, src=Devices.meter_cold_water, ref={'type': 'long',
                                                                 'addr': AD_3155_INDEX_PWR,
                                                                 'span': 1 / 1000})
    LIGHT_PWR = Tag(0.0, src=Devices.meter_light, ref={'type': 'float', 'addr': AD_3155_LIVE_PWR, 'span': 1000})
    LIGHT_I_PWR = Tag(0, src=Devices.meter_light, ref={'type': 'long', 'addr': AD_3155_INDEX_PWR, 'span': 1 / 1000})
    TECH_PWR = Tag(0.0, src=Devices.meter_tech, ref={'type': 'float', 'addr': AD_3155_LIVE_PWR, 'span': 1000})
    TECH_I_PWR = Tag(0, src=Devices.meter_tech, ref={'type': 'long', 'addr': AD_3155_INDEX_PWR, 'span': 1 / 1000})
    CTA_PWR = Tag(0.0, src=Devices.meter_cta, ref={'type': 'float', 'addr': AD_3155_LIVE_PWR, 'span': 1000})
    CTA_I_PWR = Tag(0, src=Devices.meter_cta, ref={'type': 'long', 'addr': AD_3155_INDEX_PWR, 'span': 1 / 1000})
    HEAT_PWR = Tag(0.0, src=Devices.meter_heat, ref={'type': 'float', 'addr': AD_2155_LIVE_PWR, 'span': 1000})
    HEAT_I_PWR = Tag(0.0, src=Devices.meter_heat, ref={'type': 'long', 'addr': AD_2155_INDEX_PWR, 'span': 1 / 1000})
    # virtual tags
    # total power consumption
    TOTAL_PWR = Tag(0.0, get_cmd=lambda: Tags.GARAGE_PWR.val + Tags.COLD_WATER_PWR.val + Tags.LIGHT_PWR.val +
                    Tags.TECH_PWR.val + Tags.CTA_PWR.val + Tags.HEAT_PWR.val)


@catch_log_except()
def db_refresh_job():
    since_last_integrate = time.time() - Tags.RD_TIMESTAMP_WH.val
    Tags.RD_TIMESTAMP_WH.val += since_last_integrate
    # integrate active power for daily index (if time since last integrate is regular)
    if 0 < since_last_integrate < 7200:
        Tags.RD_TODAY_WH.val += Tags.TOTAL_PWR.val * since_last_integrate / 3600
    # publish active power
    Tags.RD_TOTAL_PWR.val = Tags.TOTAL_PWR.e_val


@catch_log_except()
def db_midnight_job():
    # backup daily value to yesterday then reset it for new day start
    Tags.RD_YESTERDAY_WH.val = Tags.RD_TODAY_WH.val
    Tags.RD_TODAY_WH.val = 0


if __name__ == '__main__':
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    logging.info('board-meters-app started')

    # wait DS_ModbusTCP thread start
    time.sleep(1.0)

    # init scheduler
    schedule.every(5).seconds.do(db_refresh_job)
    schedule.every().day.at('00:00').do(db_midnight_job)
    
    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # first call
    db_refresh_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1.0)
