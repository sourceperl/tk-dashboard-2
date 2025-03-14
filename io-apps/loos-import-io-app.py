#!/opt/tk-dashboard/virtualenvs/loos/venv/bin/python

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
import os
import re
import ssl
import time
from urllib.request import Request, urlopen
import dateutil.parser
import feedparser
import schedule
import PIL.Image
from metar.Metar import Metar
import pdf2image
import PIL.Image
import PIL.ImageDraw
from lib.dashboard_io import CustomRedis, catch_log_except, dt_utc_to_local, wait_uptime
from lib.webdav import WebDAV
from conf.private_loos import REDIS_USER, REDIS_PASS, GMAP_IMG_URL, CAM_GATE_IMG_URL, CAM_DOOR_1_IMG_URL, CAM_DOOR_2_IMG_URL, \
    GSHEET_URL, OW_APP_ID, VIGILANCE_KEY, WEBDAV_URL, WEBDAV_USER, WEBDAV_PASS, WEBDAV_REGLEMENT_DOC_DIR, WEBDAV_CAROUSEL_IMG_DIR


# some const
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:2.0.1) Gecko/20100101 Firefox/4.0.1'

# some var
owc_doc_dir_last_sync = 0
owc_car_dir_last_sync = 0


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
def owc_updated_job():
    # check if the owncloud directories has been updated by users (start sync jobs if need)
    global owc_doc_dir_last_sync, owc_car_dir_last_sync

    for f in wdv.ls():
        item = f['file_path']
        item_last_modified = int(f['dt_last_modified'].timestamp())
        # document update ?
        if item == WEBDAV_REGLEMENT_DOC_DIR:
            # update need
            if item_last_modified > owc_doc_dir_last_sync:
                logging.debug(f'"{WEBDAV_REGLEMENT_DOC_DIR}" seem updated: run "owncloud_sync_doc_job"')
                owc_sync_doc_job()
                owc_doc_dir_last_sync = item_last_modified
        # carousel update ?
        elif item == WEBDAV_CAROUSEL_IMG_DIR:
            # update need
            if item_last_modified > owc_car_dir_last_sync:
                logging.debug(f'"{WEBDAV_CAROUSEL_IMG_DIR}" seem updated: run "owncloud_sync_carousel_job"')
                owc_sync_carousel_job()
                owc_car_dir_last_sync = item_last_modified


@catch_log_except()
def owc_sync_carousel_job():
    # sync owncloud carousel directory with local
    # local constants
    DIR_CAR_INFOS = 'dir:carousel:infos'
    DIR_CAR_RAW = 'dir:carousel:raw:min-png'

    # local functions
    def update_carousel_raw_data(filename, raw_data):
        # build json infos record
        md5 = hashlib.md5(raw_data).hexdigest()
        js_infos = json.dumps(dict(size=len(raw_data), md5=md5))
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
    logging.info('start of sync for owncloud carousel')
    # list local redis files
    local_files_d = {}
    for f_name, js_infos in DB.main.hgetall(DIR_CAR_INFOS).items():
        try:
            filename = f_name.decode()
            size = json.loads(js_infos)['size']
            local_files_d[filename] = size
        except ValueError:
            pass
    # check "dir:carousel:raw:min-png" consistency
    raw_file_l = [f.decode() for f in DB.main.hkeys(DIR_CAR_RAW)]
    # remove orphan infos record
    for f in list(set(local_files_d) - set(raw_file_l)):
        logging.debug(f'remove orphan "{f}" record in hash "{DIR_CAR_INFOS}"')
        DB.main.hdel(DIR_CAR_INFOS, f)
        del local_files_d[f]
    # remove orphan raw-png record
    for f in list(set(raw_file_l) - set(local_files_d)):
        logging.debug(f'remove orphan "{f}" record in hash "{DIR_CAR_RAW}"')
        DB.main.hdel(DIR_CAR_RAW, f)
    # list owncloud files (disallow directory)
    own_files_d = {}
    for f_d in wdv.ls(WEBDAV_CAROUSEL_IMG_DIR):
        file_path = f_d['file_path']
        size = f_d['content_length']
        if file_path and not file_path.endswith('/'):
            # search site only tags (_@loos_, _@messein_...) in filename
            # site id is 16 chars max
            site_tag_l = re.findall(r'_@([a-zA-Z0-9\-]{1,16})', file_path)
            site_tag_l = [s.strip().lower() for s in site_tag_l]
            site_tag_ok = 'loos' in site_tag_l or not site_tag_l
            # download filter: ignore txt file or heavy fie (>10 MB)
            filter_ok = not file_path.lower().endswith('.txt') \
                and (size < 10 * 1024 * 1024) \
                and site_tag_ok
            # add file to owncloud dict
            if filter_ok:
                own_files_d[f_d['file_path']] = size
    # exist only on local redis
    for f in list(set(local_files_d) - set(own_files_d)):
        logging.info(f'"{f}" exist only on local -> remove it')
        # redis remove (atomic)
        pipe = DB.main.pipeline()
        pipe.hdel(DIR_CAR_INFOS, f)
        pipe.hdel(DIR_CAR_RAW, f)
        pipe.execute()
    # exist only on remote owncloud
    for f in list(set(own_files_d) - set(local_files_d)):
        logging.info('"%s" exist only on remote -> download it' % f)
        data = wdv.download(os.path.join(WEBDAV_CAROUSEL_IMG_DIR, f))
        if data:
            update_carousel_raw_data(f, data)
    # exist at both side (update only if file size change)
    for f in list(set(local_files_d).intersection(own_files_d)):
        local_size = local_files_d[f]
        remote_size = own_files_d[f]
        logging.debug(f'check "{f}" remote size [{remote_size}]/local size [{local_size}]')
        if local_size != remote_size:
            logging.info(f'"{f}" size mismatch -> download it')
            data = wdv.download(os.path.join(WEBDAV_CAROUSEL_IMG_DIR, f))
            if data:
                update_carousel_raw_data(f, data)
    # log sync end
    logging.info('end of sync for owncloud carousel')


@catch_log_except()
def owc_sync_doc_job():
    # sync owncloud document directory with local
    # local constants
    DIR_DOC_INFOS = 'dir:doc:infos'
    DIR_DOC_RAW = 'dir:doc:raw'

    # local functions
    def update_doc_raw_data(filename, raw_data):
        # build json infos record
        md5 = hashlib.md5(raw_data).hexdigest()
        js_infos = json.dumps(dict(size=len(raw_data), md5=md5))
        # redis add  (atomic write)
        pipe = DB.main.pipeline()
        pipe.hset(DIR_DOC_INFOS, filename, js_infos)
        pipe.hset(DIR_DOC_RAW, filename, raw_data)
        pipe.execute()

    # log sync start
    logging.info('start of sync for owncloud doc')
    # list local redis files
    local_files_d = {}
    for f_name, js_infos in DB.main.hgetall(DIR_DOC_INFOS).items():
        try:
            filename = f_name.decode()
            size = json.loads(js_infos)['size']
            local_files_d[filename] = size
        except ValueError:
            pass
    # check "dir:doc:raw:min-png" consistency
    raw_file_l = [f.decode() for f in DB.main.hkeys(DIR_DOC_RAW)]
    # remove orphan infos record
    for f in list(set(local_files_d) - set(raw_file_l)):
        logging.debug(f'remove orphan "{f}" record in hash "{DIR_DOC_INFOS}"')
        DB.main.hdel(DIR_DOC_INFOS, f)
        del local_files_d[f]
    # remove orphan raw-png record
    for f in list(set(raw_file_l) - set(local_files_d)):
        logging.debug(f'remove orphan "{f}" record in hash "{DIR_DOC_RAW}"')
        DB.main.hdel(DIR_DOC_RAW, f)
    # list owncloud files (disallow directory)
    own_files_d = {}
    for f_d in wdv.ls(WEBDAV_REGLEMENT_DOC_DIR):
        file_path = f_d['file_path']
        size = f_d['content_length']
        if file_path and not file_path.endswith('/'):
            # download filter: ignore txt file or heavy fie (>10 MB)
            ok_load = not file_path.lower().endswith('.txt') \
                and (size < 10 * 1024 * 1024)
            if ok_load:
                own_files_d[f_d['file_path']] = size
    # exist only on local redis
    for f in list(set(local_files_d) - set(own_files_d)):
        logging.info(f'"{f}" exist only on local -> remove it')
        # redis remove (atomic)
        pipe = DB.main.pipeline()
        pipe.hdel(DIR_DOC_INFOS, f)
        pipe.hdel(DIR_DOC_RAW, f)
        pipe.execute()
    # exist only on remote owncloud
    for f in list(set(own_files_d) - set(local_files_d)):
        logging.info(f'"{f}" exist only on remote -> download it')
        data = wdv.download(os.path.join(WEBDAV_REGLEMENT_DOC_DIR, f))
        if data:
            update_doc_raw_data(f, data)
    # exist at both side (update only if file size change)
    for f in list(set(local_files_d).intersection(own_files_d)):
        local_size = local_files_d[f]
        remote_size = own_files_d[f]
        logging.debug(f'check "{f}" remote size [{remote_size}]/local size [{local_size}]')
        if local_size != remote_size:
            logging.info(f'"{f}" size mismatch -> download it')
            data = wdv.download(os.path.join(WEBDAV_REGLEMENT_DOC_DIR, f))
            if data:
                update_doc_raw_data(f, data)
    # log sync end
    logging.info('end of sync for owncloud doc')


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
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)
    logging.info('board-import-app started')

    # init webdav client (with specific SSL context)
    wdv_ssl_ctx = ssl.create_default_context()
    wdv_ssl_ctx.check_hostname = False
    wdv_ssl_ctx.verify_mode = ssl.CERT_NONE
    # TODO replace above SSL context by self-signed server cert
    # wdv_ssl_ctx.load_verify_locations('conf/cert/my-srv-cert.pem')
    wdv = WebDAV(WEBDAV_URL, username=WEBDAV_USER, password=WEBDAV_PASS, ssl_ctx=wdv_ssl_ctx)

    # init scheduler
    schedule.every(5).minutes.do(owc_updated_job)
    schedule.every(1).hours.do(owc_sync_carousel_job)
    schedule.every(1).hours.do(owc_sync_doc_job)
    schedule.every(60).minutes.do(air_quality_atmo_hdf_job)
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
    air_quality_atmo_hdf_job()
    gsheet_job()
    img_gmap_traffic_job()
    local_info_job()
    openweathermap_forecast_job()
    vigilance_job()
    weather_today_job()
    owc_updated_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
