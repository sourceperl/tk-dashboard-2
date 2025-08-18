#!/opt/tk-dashboard/virtualenvs/loos/venv/bin/python

import argparse
import hashlib
import io
import json
import logging
import re
import time
import zlib
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

import dateutil.parser
import feedparser
import pdf2image
import PIL.Image
import PIL.ImageDraw
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
from lib.dashboard_io import CustomRedis, catch_log_except, dt_utc_to_local, wait_uptime
from lib.sftp import FileInfos, SFTP_Indexed
from metar.Metar import Metar

# some const
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'

# some vars
sftp_doc_sync_dt = datetime(year=2000, month=1, day=1, tzinfo=timezone.utc)
sftp_img_sync_dt = datetime(year=2000, month=1, day=1, tzinfo=timezone.utc)


# some class
class DB:
    # create connector
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
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
    logging.debug(f'update redis key {key} with {titles_l}')
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


@catch_log_except()
def local_info_job():
    # http request
    rss_url = 'https://france3-regions.francetvinfo.fr/societe/rss?r=hauts-de-france'
    uo_ret = urlopen(rss_url, timeout=5.0)
    # parse RSS
    l_titles = []
    for post in feedparser.parse(uo_ret.read()).entries:
        title = post.title
        title = title.strip()
        title = title.replace('\n', ' ')
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
    """ Check if the sftp directories index has been updated (start sync jobs if need). """
    global sftp_doc_sync_dt, sftp_img_sync_dt

    with SFTP_Indexed(hostname=SFTP_HOSTNAME, username=SFTP_USERNAME) as sftp:
        # image carousel directory
        sftp.base_dir = SFTP_IMG_DIR
        idx_img_attrs = sftp.index_attributes()
        logging.debug(f'"{SFTP_IMG_DIR}" index size: {idx_img_attrs.size} bytes last update: {idx_img_attrs.mtime_dt}')
        if idx_img_attrs.mtime_dt > sftp_img_sync_dt:
            logging.info(f'index of "{SFTP_IMG_DIR}" change: run an SFTP sync')
            sftp_sync_img_job(sftp)
            sftp_img_sync_dt = idx_img_attrs.mtime_dt

        # doc directory
        sftp.base_dir = SFTP_DOC_DIR
        idx_doc_attrs = sftp.index_attributes()
        logging.debug(f'"{SFTP_IMG_DIR}" index size: {idx_doc_attrs.size} bytes last update: {idx_doc_attrs.mtime_dt}')
        if idx_doc_attrs.mtime_dt > sftp_doc_sync_dt:
            logging.info(f'index of "{SFTP_DOC_DIR}" change: run an SFTP sync')
            # sftp_sync_doc_job()
            sftp_doc_sync_dt = idx_doc_attrs.mtime_dt


@catch_log_except()
def sftp_sync_img_job(sftp: SFTP_Indexed):
    # sync owncloud carousel directory with local
    # local constants
    DIR_CAR_INFOS = 'dir:carousel:infos'
    DIR_CAR_RAW = 'dir:carousel:raw:min-png'
    FILE_MAX_SIZE = 10 * 1024 * 1024

    # local functions
    def update_carousel_raw_data(filename: str, raw_data: bytes):
        # build json infos record
        sha256 = hashlib.sha256(raw_data).hexdigest()
        js_infos = json.dumps(dict(size=len(raw_data), sha256=sha256))
        # convert raw data to PNG thumbnails
        # create default error image
        img_to_redis = PIL.Image.new('RGB', (655, 453), (255, 255, 255))
        draw = PIL.ImageDraw.Draw(img_to_redis)
        draw.text((0, 0), f'loading error (src: "{filename}")', (0, 0, 0))
        # replace default image by convert result
        try:
            # convert png and jpg file
            if filename.lower().endswith('.png') or filename.lower().endswith('.jpg'):
                # image to PIL
                img_to_redis = PIL.Image.open(io.BytesIO(raw_data))
            # convert pdf file
            elif filename.lower().endswith('.pdf'):
                # PDF to PIL: convert first page to PIL image
                img_to_redis = pdf2image.convert_from_bytes(raw_data)[0]
        except Exception:
            pass
        # resize and format as raw png
        img_to_redis.thumbnail([655, 453])
        io_to_redis = io.BytesIO()
        img_to_redis.save(io_to_redis, format='PNG')
        # redis add  (atomic write)
        pipe = DB.main.pipeline()
        pipe.hset(DIR_CAR_INFOS, filename, js_infos)
        pipe.hset(DIR_CAR_RAW, filename, io_to_redis.getvalue())
        pipe.execute()

    # log sync start
    logging.info('start of sync for images carousel')
    # populate local_files_d with redis files
    local_files_d: dict[str, FileInfos] = {}
    for filename_as_bytes, json_as_bytes in DB.main.hgetall(DIR_CAR_INFOS).items():
        try:
            js_data_d = json.loads(json_as_bytes)
            file_attrs = FileInfos(sha256=js_data_d['sha256'], size=js_data_d['size'])
            local_files_d[filename_as_bytes.decode()] = file_attrs
        except ValueError:
            pass
    # check "dir:carousel:raw:min-png" consistency
    raw_file_l = [f.decode() for f in DB.main.hkeys(DIR_CAR_RAW)]
    # remove orphan infos record
    for filename in list(set(local_files_d) - set(raw_file_l)):
        logging.debug(f'remove orphan "{filename}" record in hash "{DIR_CAR_INFOS}"')
        DB.main.hdel(DIR_CAR_INFOS, filename)
        del local_files_d[filename]
    # remove orphan raw-png record
    for filename in list(set(raw_file_l) - set(local_files_d)):
        logging.debug(f'remove orphan "{filename}" record in hash "{DIR_CAR_RAW}"')
        DB.main.hdel(DIR_CAR_RAW, filename)
    # list sftp files (disallow directory)
    remote_files_d: dict[str, FileInfos] = {}
    sftp_index_d = sftp.get_index_as_dict()
    for filename, sha256 in sftp_index_d.items():
        # search site id (_@loos_, _@messein_...) in filename (max 16 chars)
        site_id = None
        site_pattern = r'_@([a-zA-Z0-9]{1,16})_'
        match = re.search(site_pattern, filename)
        if match:
            site_id = match.group(1).lower()
        # keep file conditions
        file_attrs = sftp.get_file_attrs(filename)
        site_id_ok = site_id is None or site_id == 'loos'
        file_size_ok = file_attrs.size < FILE_MAX_SIZE
        file_ext_ok = not filename.lower().endswith('.txt')
        keep_file = site_id_ok and file_size_ok and file_ext_ok
        if keep_file:
            remote_files_d[filename] = FileInfos(sha256=sha256, size=file_attrs.size)
    # exist only on local redis
    for filename in list(set(local_files_d) - set(remote_files_d)):
        logging.info(f'"{filename}" exist only on local -> remove it')
        # redis remove (atomic)
        pipe = DB.main.pipeline()
        pipe.hdel(DIR_CAR_INFOS, filename)
        pipe.hdel(DIR_CAR_RAW, filename)
        pipe.execute()
    # exist only on remote sftp server
    for filename in list(set(remote_files_d) - set(local_files_d)):
        logging.info(f'"{filename}" exist only on remote -> download it')
        raw_data = sftp.get_file_as_bytes(filename)
        if raw_data:
            update_carousel_raw_data(filename, raw_data)
    # exist at both side (update only if hash change)
    for filename in list(set(local_files_d).intersection(remote_files_d)):
        local_sha256 = local_files_d[filename].sha256
        remote_sha256 = remote_files_d[filename].sha256
        logging.debug(f'check "{filename}" remote sha256 [{remote_sha256[:7]}]/local sha256 [{local_sha256[:7]}]')
        if local_sha256 != remote_sha256:
            logging.info(f'"{filename}" sha256 mismatch -> download it')
            raw_data = sftp.get_file_as_bytes(filename)
            if raw_data:
                update_carousel_raw_data(filename, raw_data)
    # log sync end
    logging.info('end of sync for owncloud carousel')


# @catch_log_except()
# def owc_sync_doc_job():
#     # sync owncloud document directory with local
#     # local constants
#     DIR_DOC_INFOS = 'dir:doc:infos'
#     DIR_DOC_RAW = 'dir:doc:raw'

#     # local functions
#     def update_doc_raw_data(filename, raw_data):
#         # build json infos record
#         md5 = hashlib.md5(raw_data).hexdigest()
#         js_infos = json.dumps(dict(size=len(raw_data), md5=md5))
#         # redis add  (atomic write)
#         pipe = DB.main.pipeline()
#         pipe.hset(DIR_DOC_INFOS, filename, js_infos)
#         pipe.hset(DIR_DOC_RAW, filename, raw_data)
#         pipe.execute()

#     # log sync start
#     logging.info('start of sync for owncloud doc')
#     # list local redis files
#     local_files_d = {}
#     for f_name, js_infos in DB.main.hgetall(DIR_DOC_INFOS).items():
#         try:
#             filename = f_name.decode()
#             size = json.loads(js_infos)['size']
#             local_files_d[filename] = size
#         except ValueError:
#             pass
#     # check "dir:doc:raw:min-png" consistency
#     raw_file_l = [f.decode() for f in DB.main.hkeys(DIR_DOC_RAW)]
#     # remove orphan infos record
#     for f in list(set(local_files_d) - set(raw_file_l)):
#         logging.debug(f'remove orphan "{f}" record in hash "{DIR_DOC_INFOS}"')
#         DB.main.hdel(DIR_DOC_INFOS, f)
#         del local_files_d[f]
#     # remove orphan raw-png record
#     for f in list(set(raw_file_l) - set(local_files_d)):
#         logging.debug(f'remove orphan "{f}" record in hash "{DIR_DOC_RAW}"')
#         DB.main.hdel(DIR_DOC_RAW, f)
#     # list owncloud files (disallow directory)
#     own_files_d = {}
#     for f_d in wdv.ls(SFTP_DOC_DIR):
#         file_path = f_d['file_path']
#         size = f_d['content_length']
#         if file_path and not file_path.endswith('/'):
#             # download filter: ignore txt file or heavy fie (>10 MB)
#             ok_load = not file_path.lower().endswith('.txt') \
#                 and (size < 10 * 1024 * 1024)
#             if ok_load:
#                 own_files_d[f_d['file_path']] = size
#     # exist only on local redis
#     for f in list(set(local_files_d) - set(own_files_d)):
#         logging.info(f'"{f}" exist only on local -> remove it')
#         # redis remove (atomic)
#         pipe = DB.main.pipeline()
#         pipe.hdel(DIR_DOC_INFOS, f)
#         pipe.hdel(DIR_DOC_RAW, f)
#         pipe.execute()
#     # exist only on remote owncloud
#     for f in list(set(own_files_d) - set(local_files_d)):
#         logging.info(f'"{f}" exist only on remote -> download it')
#         data = wdv.download(os.path.join(SFTP_DOC_DIR, f))
#         if data:
#             update_doc_raw_data(f, data)
#     # exist at both side (update only if file size change)
#     for f in list(set(local_files_d).intersection(own_files_d)):
#         local_size = local_files_d[f]
#         remote_size = own_files_d[f]
#         logging.debug(f'check "{f}" remote size [{remote_size}]/local size [{local_size}]')
#         if local_size != remote_size:
#             logging.info(f'"{f}" size mismatch -> download it')
#             data = wdv.download(os.path.join(SFTP_DOC_DIR, f))
#             if data:
#                 update_doc_raw_data(f, data)
#     # log sync end
#     logging.info('end of sync for owncloud doc')


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
    logging.info('board-import-app started')

    # init scheduler
    schedule.every(5).minutes.do(sftp_updated_job)
    # schedule.every(1).hours.do(owc_sync_carousel_job)
    # schedule.every(1).hours.do(owc_sync_doc_job)
    schedule.every(60).minutes.do(air_quality_atmo_hdf_job)
    schedule.every(5).minutes.at(':15').do(flyspray_job)
    schedule.every(5).minutes.do(gsheet_job)
    schedule.every(2).minutes.do(img_gmap_traffic_job)
    # schedule.every(2).seconds.do(img_cam_gate_job)
    # schedule.every(2).seconds.do(img_cam_door_1_job)
    # schedule.every(2).seconds.do(img_cam_door_2_job)
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
