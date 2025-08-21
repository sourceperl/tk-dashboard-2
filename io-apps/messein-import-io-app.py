#!/opt/tk-dashboard/virtualenvs/messein/venv/bin/python

import argparse
import io
import json
import logging
import time
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import dateutil.parser
import feedparser
import schedule
from conf.private_messein import (
    FLY_KEY,
    FLY_SHARE_URL,
    GMAP_IMG_URL,
    GSHEET_URL,
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
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# some const
SITE_ID = 'messein'
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
    filtered_index_d = sftp_index.get_index_as_dict_filtered(by_site=SITE_ID, by_max_size=FILE_MAX_SIZE)
    redis_file.sync_with_sftp(sftp_index, to_sync_d=filtered_index_d, to_png_thumb=True)
    logger.info('end of images carousel sync')


def sync_sftp_doc(sftp_index: SftpFileIndex):
    """
    Synchronizes of document from SFTP to local Redis storage.
    Handles adding new, updating changed, and removing deleted files.
    """
    # log sync start
    logger.info('start of document sync')
    redis_file = RedisFile(DB.main, infos_key=KEY_DOC_INFOS, raw_key=KEY_DOC_RAW)
    filtered_index_d = sftp_index.get_index_as_dict_filtered(by_site=SITE_ID, by_max_size=FILE_MAX_SIZE)
    redis_file.sync_with_sftp(sftp_index, to_sync_d=filtered_index_d, to_png_thumb=False)
    logger.info('end of document sync')


# define all jobs
@catch_log_except()
def air_quality_atmo_ge_job():
    # build url
    base_url = 'https://services3.arcgis.com/' \
               'Is0UwT37raQYl9Jj/arcgis/rest/services/ind_grandest_5j/FeatureServer/0/query'
    params = {
        "where": "code_zone IN (54395, 57463, 51454, 67482)",
        "outFields": "date_ech, code_qual, lib_qual, lib_zone, code_zone",
        "returnGeometry": "false",
        "resultRecordCount": "48",
        "orderByFields": "date_ech ASC",
        "f": "json"
    }
    url = f'{base_url}?{urlencode(params)}'
    # https request
    with urlopen(url, timeout=5.0) as uo_ret:
        body = uo_ret.read()
    # decode json message
    atmo_raw_d = json.loads(body.decode('utf-8'))
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
    d_air_quality = {'nancy': zones_d.get(54395, 0),
                     'metz': zones_d.get(57463, 0),
                     'reims': zones_d.get(51454, 0),
                     'strasbourg': zones_d.get(67482, 0)}
    # update redis
    DB.main.set_as_json('json:atmo-ge', d_air_quality, ex=6*3600)


@catch_log_except()
def flyspray_job():
    # request
    with urlopen(FLY_SHARE_URL, timeout=10.0) as uo_ret:
        body = uo_ret.read()
    # search your raw message
    data_d = json.loads(body.decode('utf-8'))
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
        titles_l = list(js_obj['est'])
    except (TypeError, KeyError):
        raise RuntimeError('key "est" is missing or have bad type in json message')
    # if all is ok: publish json to redis
    key = 'json:flyspray-est'
    logger.debug(f'update redis key {key} with {titles_l}')
    DB.main.set_as_json(key, titles_l, ex=3600)


@catch_log_except()
def gsheet_job():
    # https request
    with urlopen(GSHEET_URL, timeout=10.0) as uo_ret:
        body = uo_ret.read()
    # process response
    d = dict()
    gsheet_txt = body.decode('utf-8')
    for line in gsheet_txt.splitlines():
        tag, value = line.split(',')
        d[tag] = value
    redis_d = dict(update=datetime.now().isoformat('T'), tags=d)
    DB.main.set_as_json('json:gsheet', redis_d, ex=2*3600)


@catch_log_except()
def img_gmap_traffic_job():
    # http request
    with urlopen(GMAP_IMG_URL, timeout=5.0) as uo_ret:
        body = uo_ret.read()
    # convert RAW img format (bytes) to Pillow image
    pil_img = Image.open(io.BytesIO(body))
    pil_img = pil_img.crop((0, 0, 560, 328))
    # png encode
    img_io = io.BytesIO()
    pil_img.save(img_io, format='PNG')
    # store RAW PNG to redis key
    DB.main.set('img:traffic-map-est:png', img_io.getvalue(), ex=2*3600)


@catch_log_except()
def dir_est_img_job():
    # retrieve DIR-est webcams: Houdemont, Velaine-en-Haye, Saint-Nicolas, Côte de Flavigny
    for id_redis, lbl_cam, get_code in [('houdemont', 'Houdemont', '18'), ('velaine', 'Velaine', '53'),
                                        ('st-nicolas', 'Saint-Nicolas', '49'), ('flavigny', 'Flavigny', '5')]:
        url = f'https://webcam.dir-est.fr/app.php/lastimg/{get_code}'
        with urlopen(url, timeout=5.0) as uo_ret:
            body = uo_ret.read()
        # load image to PIL and resize it
        img = Image.open(io.BytesIO(body))
        img.thumbnail((224, 235))
        # add text to image
        time_str = datetime.now().strftime('%H:%M')
        txt_img = f'{lbl_cam} - {time_str}'
        # this font is in package "fonts-freefont-ttf"
        font = ImageFont.truetype('/usr/share/fonts/truetype/freefont/FreeMono.ttf', 16)
        draw = ImageDraw.Draw(img)
        draw.text((5, 5), txt_img, (0x10, 0x0e, 0x0e), font=font)
        # save image as PNG for redis
        redis_io = io.BytesIO()
        img.save(redis_io, format='PNG')
        # update redis
        DB.main.set('img:dir-est:%s:png' % id_redis, redis_io.getvalue(), ex=3600)


@catch_log_except()
def local_info_job():
    # http request
    rss_url = 'https://france3-regions.francetvinfo.fr/societe/rss?r=grand-est'
    with urlopen(rss_url, timeout=5.0) as uo_ret:
        # parse RSS
        l_titles = []
        feed = feedparser.parse(uo_ret.read())
        for entrie in feed.entries:
            title = str(entrie.title).strip().replace('\n', ' ')
            l_titles.append(title)
        DB.main.set_as_json('json:news', l_titles, ex=2*3600)


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
    with urlopen(request, timeout=10.0) as uo_ret:
        js_str = uo_ret.read().decode('utf-8')
    # decode json message
    vig_raw_d = json.loads(js_str)
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
    schedule.every(2).minutes.do(img_gmap_traffic_job)
    schedule.every(5).minutes.do(sftp_updated_job)
    schedule.every(5).minutes.at(':15').do(flyspray_job)
    schedule.every(5).minutes.do(gsheet_job)
    schedule.every(5).minutes.do(dir_est_img_job)
    schedule.every(5).minutes.do(local_info_job)
    schedule.every(5).minutes.do(vigilance_job)
    schedule.every(60).minutes.do(air_quality_atmo_ge_job)

    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    # first call
    img_gmap_traffic_job()
    sftp_updated_job()
    flyspray_job()
    gsheet_job()
    dir_est_img_job()
    local_info_job()
    vigilance_job()
    air_quality_atmo_ge_job()

    # main loop
    while True:
        schedule.run_pending()
        time.sleep(1)
