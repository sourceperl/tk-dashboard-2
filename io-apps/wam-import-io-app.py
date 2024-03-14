#!/opt/tk-dashboard/virtualenvs/wam/venv/bin/python

from datetime import datetime, timezone
import io
import json
import logging
import time
from urllib.request import Request, urlopen
import feedparser
import schedule
import PIL.Image
from metar.Metar import Metar
import PIL.Image
import PIL.ImageDraw
from lib.dashboard_io import CustomRedis, catch_log_except, dt_utc_to_local, wait_uptime
from conf.private_wam import REDIS_HALL_USER, REDIS_PASS, REDIS_HALL_PORT, REDIS_HALL_USER, REDIS_HALL_PASS, \
    GMAP_IMG_URL, VIGILANCE_KEY


# some const
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'

# some var
owc_doc_dir_last_sync = 0
owc_car_dir_last_sync = 0


# some class
class DB:
    main = CustomRedis(host='localhost', username=REDIS_HALL_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)
    hall = CustomRedis(host='localhost', port=REDIS_HALL_PORT, username=REDIS_HALL_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


# some function
@catch_log_except()
def air_quality_atmo_hdf_job():
    url = 'https://services8.arcgis.com/' + \
          'rxZzohbySMKHTNcy/arcgis/rest/services/ind_hdf_3j/FeatureServer/0/query' + \
          '?where=code_zone IN (02691, 59183, 59350, 59392, 59606, 80021)' + \
          '&outFields=date_ech, code_qual, lib_qual, lib_zone, code_zone' + \
          '&returnGeometry=false&resultRecordCount=48' + \
          '&orderByFields=date_ech DESC&f=json'
    url = url.replace(' ', '%20')
    # https request
    uo_ret = urlopen(url, timeout=5.0)
    # decode json message
    atmo_raw_d = json.load(uo_ret)
    # populate zones dict with receive values
    today_dt_date = datetime.today().date()
    zones_d = {}
    for record in atmo_raw_d['features']:
        # load record data
        r_code_zone = record['attributes']['code_zone']
        r_ts = int(record['attributes']['date_ech'])
        r_dt = datetime.utcfromtimestamp(r_ts / 1000)
        r_value = record['attributes']['code_qual']
        # retain today value
        if r_dt.date() == today_dt_date:
            zones_d[r_code_zone] = r_value
    # skip key publish if zones_d is empty
    if not zones_d:
        raise ValueError('dataset is empty')
    # create and populate result dict
    d_air_quality = {'amiens': zones_d.get('80021', 0),
                     'dunkerque': zones_d.get('59183', 0),
                     'lille': zones_d.get('59350', 0),
                     'maubeuge': zones_d.get('59392', 0),
                     'saint-quentin': zones_d.get('02691', 0),
                     'valenciennes': zones_d.get('59606', 0)}
    # update redis
    DB.main.set_as_json('json:atmo', d_air_quality, ex=6*3600)


@catch_log_except()
def ble_sensor_job():
    ble_data_d = {}
    # add outdoor ble data
    ble_out_d = DB.hall.get_from_json('ble-js:outdoor')
    if ble_out_d:
        ble_data_d['outdoor'] = {'temp_c': ble_out_d.get('temp_c'), 'hum_p': ble_out_d.get('hum_p')}
    # add kitchen ble data
    ble_kit_d = DB.hall.get_from_json('ble-js:kitchen')
    if ble_kit_d:
        ble_data_d['kitchen'] = {'temp_c': ble_kit_d.get('temp_c'), 'hum_p': ble_kit_d.get('hum_p')}
    # publish
    DB.main.set_as_json('json:ble-data', ble_data_d, ex=3600)


@catch_log_except()
def img_gmap_traffic_job():
    # http request
    uo_ret = urlopen(GMAP_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # crop image
    pil_img = pil_img.crop((0, 0, 560, 328))
    # pil_img.thumbnail([632, 328])
    img_io = io.BytesIO()
    pil_img.save(img_io, format='PNG')
    # store RAW PNG to redis key
    DB.main.set('img:traffic-map:png', img_io.getvalue(), ex=2*3600)


@catch_log_except()
def local_info_job():
    # do request
    l_titles = []
    for post in feedparser.parse('https://france3-regions.francetvinfo.fr/societe/rss?r=hauts-de-france').entries:
        title = post.title
        title = title.strip()
        title = title.replace('\n', ' ')
        l_titles.append(title)
    DB.main.set_as_json('json:news', l_titles, ex=2*3600)


@catch_log_except()
def vigilance_job():
    # request json data from public-api.meteofrance.fr
    request = Request(url='https://public-api.meteofrance.fr/public/DPVigilance/v1/cartevigilance/encours',
                      headers={'apikey': VIGILANCE_KEY})
    uo_ret = urlopen(request, timeout=10.0)
    # decode json message
    vig_raw_d = json.load(uo_ret)
    # check header
    js_update_iso_str = vig_raw_d['product']['update_time']
    js_update_dt = datetime.fromisoformat(js_update_iso_str)
    since_update = datetime.now().astimezone(tz=timezone.utc) - js_update_dt
    # skip outdated json (24h old)
    if since_update.total_seconds() > 24 * 3600:
        raise RuntimeError(f'json message outdated (update="{js_update_iso_str}")')
    # init a dict for publication
    vig_d = {'update': js_update_iso_str, 'department': {}}
    # parse data structure
    for period_d in vig_raw_d['product']['periods']:
        # keep only J echeance, ignore J1
        if period_d['echeance'] == 'J':
            # populate vig_d with current vig level and list of risk at this level
            for domain_id_d in period_d['timelaps']['domain_ids']:
                # keep and format main infos
                domain_id = domain_id_d['domain_id']
                max_color_id = int(domain_id_d['max_color_id'])
                risk_id_l = []
                for ph_item_d in domain_id_d['phenomenon_items']:
                    # ignore risks at green vig level
                    if max_color_id > 1:
                        # keep only risk_id if greater or equal of current level
                        if ph_item_d['phenomenon_max_color_id'] >= max_color_id:
                            risk_id_l.append(int(ph_item_d['phenomenon_id']))
                # apply to vig_d
                vig_d['department'][domain_id] = {}
                vig_d['department'][domain_id]['vig_level'] = max_color_id
                vig_d['department'][domain_id]['risk_id'] = risk_id_l
    # publish vig_d
    DB.main.set_as_json('json:vigilance', vig_d, ex=2*3600)


@catch_log_except()
def metar_lesquin_job():
    # request data from NOAA server (METAR of Lille-Lesquin Airport)
    request = Request(url='http://tgftp.nws.noaa.gov/data/observations/metar/stations/LFQQ.TXT',
                      headers={'User-Agent': USER_AGENT})
    uo_ret = urlopen(request, timeout=10.0)
    # extract METAR message
    metar_msg = uo_ret.read().decode().split('\n')[1]
    # METAR parse
    obs = Metar(metar_msg)
    # init and populate d_today dict
    d_today = {}
    # message date and time
    if obs.time:
        d_today['update_iso'] = obs.time.strftime('%Y-%m-%dT%H:%M:%SZ')
        d_today['update_fr'] = dt_utc_to_local(obs.time).strftime('%H:%M %d/%m')
    # current temperature
    if obs.temp:
        d_today['temp'] = round(obs.temp.value('C'))
    # current dew point
    if obs.dewpt:
        d_today['dewpt'] = round(obs.dewpt.value('C'))
    # current pressure
    if obs.press:
        d_today['press'] = round(obs.press.value('hpa'))
    # current wind speed
    if obs.wind_speed:
        d_today['w_speed'] = round(obs.wind_speed.value('KMH'))
    # current wind gust
    if obs.wind_gust:
        d_today['w_gust'] = round(obs.wind_gust.value('KMH'))
    # current wind direction
    if obs.wind_dir:
        # replace 'W'est by 'O'uest
        d_today['w_dir'] = obs.wind_dir.compass().replace('W', 'O')
    # weather status str
    d_today['descr'] = 'n/a'
    # store to redis
    DB.main.set_as_json('json:metar:lesquin', d_today, ex=2*3600)


# main
if __name__ == '__main__':
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)
    logging.info('board-import-app started')

    # init scheduler
    schedule.every(60).minutes.do(air_quality_atmo_hdf_job)
    schedule.every(1).minute.do(ble_sensor_job)
    schedule.every(2).minutes.do(img_gmap_traffic_job)
    schedule.every(5).minutes.do(metar_lesquin_job)
    schedule.every(5).minutes.do(vigilance_job)
    
    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # first call
    ble_sensor_job()
    air_quality_atmo_hdf_job()
    img_gmap_traffic_job()
    metar_lesquin_job()
    vigilance_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
