#!/opt/tk-dashboard/virtualenvs/loos/venv/bin/python

import argparse
import io
import json
import logging
import time
import zlib
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

import dateutil.parser
import feedparser
import PIL.Image
import schedule
from conf.private_loos import (
    CAM_DOOR_1_IMG_URL,
    CAM_DOOR_2_IMG_URL,
    CAM_GATE_IMG_URL,
    FLY_KEY,
    FLY_SHARE_URL,
    GMAP_IMG_URL,
    GSHEET_URL,
    OW_APP_ID,
    REDIS_PASS,
    REDIS_USER,
    SFTP_DOC_DIR,
    SFTP_HOSTNAME,
    SFTP_IMG_DIR,
    SFTP_USERNAME,
    VIGILANCE_KEY,
)
from cryptography.fernet import Fernet, InvalidToken
from lib.dashboard_io import (
    CustomRedis,
    RedisFile,
    TrySync,
    catch_log_except,
    wait_uptime,
)
from lib.sftp import SftpFileIndex
from metar.Metar import Metar

logger = logging.getLogger(__name__)


# some const
SITE_ID = 'loos'
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'
KEY_CAR_INFOS = 'dir:carousel:infos'
KEY_CAR_RAW = 'dir:carousel:raw:min-png'
KEY_DOC_INFOS = 'dir:doc:infos'
KEY_DOC_RAW = 'dir:doc:raw'
FILE_MAX_SIZE = 10 * 1024 * 1024


# some class
class DB:
    # create connector
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


# some function
def sync_sftp_img(sftp_index: SftpFileIndex):
    """
    Synchronizes carousel images from SFTP to local Redis storage.
    Handles adding new, updating changed, and removing deleted files.
    """
    # log sync start
    logger.info('start of sync for images carousel')
    redis_file = RedisFile(DB.main, infos_key=KEY_CAR_INFOS, raw_key=KEY_CAR_RAW)
    redis_file.sync_with_sftp(sftp_index, allow_site=SITE_ID, allow_max_size=FILE_MAX_SIZE)
    logger.info('end of images carousel sync')


def sync_sftp_doc(sftp_index: SftpFileIndex):
    """
    Synchronizes of document from SFTP to local Redis storage.
    Handles adding new, updating changed, and removing deleted files.
    """
    # log sync start
    logger.info('start of document sync')
    redis_file = RedisFile(DB.main, infos_key=KEY_DOC_INFOS, raw_key=KEY_DOC_RAW)
    redis_file.sync_with_sftp(sftp_index, allow_site=SITE_ID, allow_max_size=FILE_MAX_SIZE)
    logger.info('end of document sync')


# define all jobs
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
        r_dt = datetime.fromtimestamp(r_ts / 1000, tz=timezone.utc)
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
def flyspray_job():
    # request
    uo_ret = urlopen(FLY_SHARE_URL, timeout=10.0)
    dweet_msg = uo_ret.read()
    data_d = json.loads(dweet_msg)
    # search your raw message
    try:
        raw_msg = data_d['fly_tne_raw']
    except (IndexError, KeyError):
        raise RuntimeError('key missing in message')
    # check length or raw message
    if not 20 < len(raw_msg) <= 2000:
        raise RuntimeError('raw message have a wrong size')
    # decrypt raw message (loses it's validity 120 mn after being encrypted)
    try:
        fernet = Fernet(key=FLY_KEY)
        msg_zip_plain = fernet.decrypt(raw_msg, ttl=120*60)
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
    logger.debug(f'update redis key {key} with {titles_l}')
    DB.main.set_as_json(key, titles_l, ex=3600)


@catch_log_except()
def gsheet_job():
    # https request
    uo_ret = urlopen(GSHEET_URL, timeout=10.0)
    # process response
    d = dict()
    for line in uo_ret.read().decode().splitlines():
        tag, value = line.split(',')
        d[tag] = value
    redis_d = dict(update=datetime.now().isoformat('T'), tags=d)
    DB.main.set_as_json('json:gsheet', redis_d, ex=2*3600)


@catch_log_except()
def img_gmap_traffic_job():
    # http request
    uo_ret = urlopen(GMAP_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # crop image
    pil_img = pil_img.crop((0, 0, 560, 328))
    # png encode
    img_io = io.BytesIO()
    pil_img.save(img_io, format='PNG')
    # store RAW PNG to redis key
    DB.main.set('img:traffic-map:png', img_io.getvalue(), ex=2*3600)


@catch_log_except()
def img_cam_gate_job():
    # http request
    uo_ret = urlopen(CAM_GATE_IMG_URL, timeout=5.0)
    # convert RAW img format (bytes) to Pillow image
    pil_img = PIL.Image.open(io.BytesIO(uo_ret.read()))
    # transform image
    pil_img = pil_img.crop((0, 0, 640, 440))
    pil_img.thumbnail((339, 228))
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
    pil_img.thumbnail((339, 228))
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
    pil_img.thumbnail((339, 228))
    img_io = io.BytesIO()
    pil_img.save(img_io, format='JPEG')
    # store RAW jpeg to redis key
    DB.main.set('img:cam-door-2:jpg', img_io.getvalue(), ex=120)


@catch_log_except()
def local_info_job():
    # http request
    rss_url = 'https://france3-regions.francetvinfo.fr/societe/rss?r=hauts-de-france'
    with urlopen(rss_url, timeout=5.0) as uo_ret:
        # parse RSS
        l_titles = []
        feed = feedparser.parse(uo_ret.read())
        for entrie in feed.entries:
            title = str(entrie.title).strip().replace('\n', ' ')
            l_titles.append(title)
        DB.main.set_as_json('json:news', l_titles, ex=2*3600)


@catch_log_except()
def openweathermap_forecast_job():
    # build url
    ow_url = 'http://api.openweathermap.org/data/2.5/forecast?'
    ow_url += 'q=Loos,fr&appid=%s&units=metric&lang=fr' % OW_APP_ID
    # do request
    uo_ret = urlopen(ow_url, timeout=5.0)
    ow_d = json.load(uo_ret)
    # decode json
    t_today = None
    d_days = {}
    for i in range(0, 5):
        d_days[i] = dict(t_min=50.0, t_max=-50.0, main='', description='', icon='')
    # parse json
    for item in ow_d['list']:
        # for day-0 to day-4
        for i_day in range(5):
            txt_date, txt_time = item['dt_txt'].split(' ')
            # search today
            if txt_date == (datetime.now() + timedelta(days=i_day)).date().strftime('%Y-%m-%d'):
                # search min/max temp
                d_days[i_day]['t_min'] = min(d_days[i_day]['t_min'], item['main']['temp_min'])
                d_days[i_day]['t_max'] = max(d_days[i_day]['t_max'], item['main']['temp_max'])
                # main and icon in 12h item
                if txt_time == '12:00:00' or t_today is None:
                    d_days[i_day]['main'] = item['weather'][0]['main']
                    d_days[i_day]['icon'] = item['weather'][0]['icon']
                    d_days[i_day]['description'] = item['weather'][0]['description']
                    if t_today is None:
                        t_today = item['main']['temp']
                        d_days[0]['t'] = t_today
    # store to redis
    DB.main.set_as_json('json:weather:forecast:loos', d_days, ex=2 * 3600)


@catch_log_except()
def sftp_updated_job():
    """ Check if the sftp directories index has been updated (start sync funcs if need). """
    # connect to SFTP server and check index update status
    with SftpFileIndex(hostname=SFTP_HOSTNAME, username=SFTP_USERNAME) as sftp_index:
        try_sync_img.run(sftp_index, on_sync_func=sync_sftp_img)
        try_sync_doc.run(sftp_index, on_sync_func=sync_sftp_doc)


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
    js_update_dt = dateutil.parser.parse(js_update_iso_str)
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
def weather_today_job():
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
        dt_utc = obs.time.replace(tzinfo=timezone.utc)
        d_today['update_iso'] = dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        d_today['update_fr'] = dt_utc.astimezone().strftime('%H:%M %d/%m')
    # current temperature
    if obs.temp:
        d_today['temp'] = round(obs.temp.value('C'))
    # current dew point
    if obs.dewpt:
        d_today['dewpt'] = round(obs.dewpt.value('C'))
    # current pressure
    if obs.press:
        d_today['press'] = round(obs.press.value('HPA'))
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
    DB.main.set_as_json('json:weather:today:loos', d_today, ex=2*3600)


# main
if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    # parse
    args = parser.parse_args()
    # logging setup
    log_lvl = logging.DEBUG if args.debug else logging.INFO
    log_fmt = '%(asctime)s - %(name)-24s - %(levelname)-8s - %(message)s'
    logging.basicConfig(format=log_fmt, level=log_lvl)
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.INFO)
    logger.info('board-import-app started')

    # inits
    try_sync_img = TrySync(SFTP_IMG_DIR)
    try_sync_doc = TrySync(SFTP_DOC_DIR)

    # init scheduler
    schedule.every(5).minutes.do(sftp_updated_job)
    schedule.every(60).minutes.do(air_quality_atmo_hdf_job)
    schedule.every(5).minutes.at(':15').do(flyspray_job)
    schedule.every(5).minutes.do(gsheet_job)
    schedule.every(2).minutes.do(img_gmap_traffic_job)
    schedule.every(2).seconds.do(img_cam_gate_job)
    schedule.every(2).seconds.do(img_cam_door_1_job)
    schedule.every(2).seconds.do(img_cam_door_2_job)
    schedule.every(5).minutes.do(local_info_job)
    schedule.every(15).minutes.do(openweathermap_forecast_job)
    schedule.every(5).minutes.do(vigilance_job)
    schedule.every(5).minutes.do(weather_today_job)

    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # first call
    #  TODO remove this
    if None:
        air_quality_atmo_hdf_job()
        flyspray_job()
        gsheet_job()
        img_gmap_traffic_job()
        local_info_job()
        openweathermap_forecast_job()
        vigilance_job()
        weather_today_job()
    sftp_updated_job()
    exit()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
