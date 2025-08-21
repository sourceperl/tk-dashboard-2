#!/usr/bin/env python3

import argparse
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

from conf.private_loos import (
    REDIS_PASS,
    REDIS_USER,
    REM_REDIS_HOST,
    REM_REDIS_PASS,
    REM_REDIS_PORT,
    REM_REDIS_USER,
)
from lib.dashboard_ui import (
    AirQualityTile,
    AsyncTask,
    ClockTile,
    CustomRedis,
    DaysAccTileLoos,
    FlysprayTile,
    GaugeTile,
    ImageRawCarouselTile,
    ImageRawTile,
    NewsBannerTile,
    PdfTilesTab,
    Tag,
    TagsBase,
    TilesTab,
    VigilanceTile,
    WattsTile,
    WeatherTile,
    wait_uptime,
)

logger = logging.getLogger(__name__)


class DB:
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)
    remote = CustomRedis(host=REM_REDIS_HOST, port=REM_REDIS_PORT, username=REM_REDIS_USER, password=REM_REDIS_PASS,
                         socket_timeout=4, socket_keepalive=True)


class Tags(TagsBase):
    # create all tags here
    # WARNs: -> all tags with io_every set are manage by an independent (of tk mainloop) IO thread
    #           this thread periodically update tag value and avoid tk GUI loop do this and lose time on DB IO
    #        -> tags callbacks (read/write methods) are call by this IO thread (not by tkinter main thread)
    D_GSHEET_GRT = Tag(read=lambda: DB.main.get_js('json:gsheet'), io_every=2.0)
    D_ATMO_QUALITY = Tag(read=lambda: DB.main.get_js('json:atmo-hdf'), io_every=2.0)
    D_W_TODAY_LOOS = Tag(read=lambda: DB.main.get_js('json:weather:today:loos'), io_every=2.0)
    D_W_FORECAST_LOOS = Tag(read=lambda: DB.main.get_js('json:weather:forecast:loos'), io_every=2.0)
    D_WEATHER_VIG = Tag(read=lambda: DB.main.get_js('json:vigilance'), io_every=2.0)
    D_NEWS_LOCAL = Tag(read=lambda: DB.main.get_js('json:news'), io_every=2.0)
    MET_PWR_ACT = Tag(read=lambda: DB.main.get_js('int:loos_elec:pwr_act'), io_every=1.0)
    MET_TODAY_WH = Tag(read=lambda: DB.main.get_js('float:loos_elec:today_wh'), io_every=2.0)
    MET_YESTERDAY_WH = Tag(read=lambda: DB.main.get_js('float:loos_elec:yesterday_wh'), io_every=2.0)
    L_FLYSPRAY_RSS = Tag(read=lambda: DB.main.get_js('json:flyspray-nord'), io_every=2.0)
    IMG_LOGO_ATMO = Tag(read=lambda: DB.main.get('img:static:logo-atmo:png'), io_every=10.0)
    IMG_LOGO_GRT = Tag(read=lambda: DB.main.get('img:static:logo-grt:png'), io_every=10.0)
    IMG_TRAFFIC_MAP = Tag(read=lambda: DB.main.get('img:traffic-map-nord:png'), io_every=10.0)
    IMG_CAM_GATE = Tag(read=lambda: DB.main.get('img:cam-gate:jpg'), io_every=2.0)
    IMG_CAM_DOOR_1 = Tag(read=lambda: DB.main.get('img:cam-door-1:jpg'), io_every=2.0)
    IMG_CAM_DOOR_2 = Tag(read=lambda: DB.main.get('img:cam-door-2:jpg'), io_every=2.0)
    DIR_CAROUSEL_RAW = Tag(read=lambda: DB.main.hgetall('dir:carousel:raw:min-png'), io_every=10.0)
    PDF_FILENAMES_L = Tag(read=lambda: map(bytes.decode, DB.main.hkeys('dir:doc:raw')))
    PDF_CONTENT = Tag(read=lambda file: DB.main.hget('dir:doc:raw', file))


class RemRedisActionsTask(AsyncTask):
    def do(self, item: Any):
        logger.info(f'request "{item}" action')
        DB.remote.publish(channel='pub:actions', message=item)


class AsyncTasks:
    rem_redis_actions = RemRedisActionsTask(max_items=3)


class MainApp(tk.Tk):
    def __init__(self, *args, **kwargs):
        tk.Tk.__init__(self, *args, **kwargs)
        # tk stuff
        # define style to fix size of tab header
        self.style = ttk.Style()
        self.style.theme_settings('default', {'TNotebook.Tab': {'configure': {'padding': [17, 17]}}})
        # define notebook
        self.note = ttk.Notebook(self)
        self.tab1 = LiveTilesTab(self.note, tiles_size=(17, 9))
        self.tab2 = PdfTilesTab(self.note, tiles_size=(17, 12),
                                list_tag=Tags.PDF_FILENAMES_L, raw_tag=Tags.PDF_CONTENT)
        self.note.add(self.tab1, text='Tableau de bord')
        self.note.add(self.tab2, text='Affichage réglementaire')
        self.note.pack()
        # default tab
        self.note.select(self.tab1)
        # press Esc to quit
        self.bind('<Escape>', lambda evt: self.destroy())
        # bind function keys to tabs
        self.bind('<F1>', lambda evt: self.note.select(self.tab1))
        self.bind('<F2>', lambda evt: self.note.select(self.tab2))
        # add an user idle timer (timeout set to 15mn)
        self.user_idle_timeout_s = 15*60
        # init idle timer
        self._idle_timer = self.after(self.user_idle_timeout_s * 1000, self.on_user_idle)
        # bind function for manage user idle time
        self.bind_all('<Any-KeyPress>', self._trig_user_idle_t)
        self.bind_all('<Any-ButtonPress>', self._trig_user_idle_t)

    def _trig_user_idle_t(self, _evt):
        # cancel the previous event
        self.after_cancel(self._idle_timer)
        # create new timer
        self._idle_timer = self.after(self.user_idle_timeout_s * 1000, self.on_user_idle)

    def on_user_idle(self):
        # select first tab
        self.note.select(self.tab1)


class LiveTilesTab(TilesTab):
    """ Main dynamic Tab """

    def __init__(self, *args, **kwargs):
        TilesTab.__init__(self, *args, **kwargs)
        # create all tiles for this tab here
        # logo Atmo
        self.tl_img_atmo = ImageRawTile(self, bg='white')
        self.tl_img_atmo.set_tile(row=0, column=0)
        # air quality Dunkerque
        self.tl_atmo_dunk = AirQualityTile(self, city='Dunkerque')
        self.tl_atmo_dunk.set_tile(row=0, column=1)
        # air quality Lille
        self.tl_atmo_lil = AirQualityTile(self, city='Lille')
        self.tl_atmo_lil.set_tile(row=0, column=2)
        # air quality Maubeuge
        self.tl_atmo_maub = AirQualityTile(self, city='Maubeuge')
        self.tl_atmo_maub.set_tile(row=0, column=3)
        # air quality Saint-Quentin
        self.tl_atmo_sque = AirQualityTile(self, city='Saint-Quentin')
        self.tl_atmo_sque.set_tile(row=0, column=4)
        # traffic map
        self.tl_tf_map = ImageRawTile(self, bg='#bbe2c6')
        self.tl_tf_map.set_tile(row=1, column=0, rowspan=3, columnspan=5)
        # weather
        self.tl_weath = WeatherTile(self)
        self.tl_weath.set_tile(row=0, column=13, rowspan=3, columnspan=4)
        # clock
        self.tl_clock = ClockTile(self)
        self.tl_clock.set_tile(row=0, column=5, rowspan=2, columnspan=3)
        # loos gate cam
        self.tl_cam_gate = ImageRawTile(self)
        self.tl_cam_gate.set_tile(row=2, column=5, rowspan=2, columnspan=3)
        self.tl_cam_gate.add_on_click_cmd(self._on_click_gate_tile)
        # loos door 1 cam
        self.tl_cam_door_1 = ImageRawTile(self)
        self.tl_cam_door_1.set_tile(row=2, column=8, rowspan=2, columnspan=2)
        self.tl_cam_door_1.add_on_click_cmd(self._on_click_door_1_tile)
        # loos door 2 cam
        self.tl_cam_door_2 = ImageRawTile(self)
        self.tl_cam_door_2.set_tile(row=2, column=10, rowspan=2, columnspan=3)
        self.tl_cam_door_2.add_on_click_cmd(self._on_click_door_2_tile)
        # news banner
        self.tl_news = NewsBannerTile(self)
        self.tl_news.set_tile(row=8, column=0, columnspan=17)
        # all Gauges
        self.tl_g_veh = GaugeTile(self, title='IGP véhicule')
        self.tl_g_veh.set_tile(row=3, column=13, columnspan=2)
        self.tl_g_loc = GaugeTile(self, title='IGP locaux')
        self.tl_g_loc.set_tile(row=3, column=15, columnspan=2)
        self.tl_g_req = GaugeTile(self, title='Réunion équipe')
        self.tl_g_req.set_tile(row=4, column=13, columnspan=2)
        self.tl_g_vcs = GaugeTile(self, title='VCS')
        self.tl_g_vcs.set_tile(row=4, column=15, columnspan=2)
        self.tl_g_vst = GaugeTile(self, title='VST')
        self.tl_g_vst.set_tile(row=5, column=13, columnspan=2)
        self.tl_g_qsc = GaugeTile(self, title='1/4h sécurité')
        self.tl_g_qsc.set_tile(row=5, column=15, columnspan=2)
        # weather vigilance
        self.tl_vig_59 = VigilanceTile(self, department='Nord')
        self.tl_vig_59.set_tile(row=4, column=0)
        self.tl_vig_62 = VigilanceTile(self, department='Pas-de-Calais')
        self.tl_vig_62.set_tile(row=4, column=1)
        self.tl_vig_80 = VigilanceTile(self, department='Somme')
        self.tl_vig_80.set_tile(row=4, column=2)
        self.tl_vig_02 = VigilanceTile(self, department='Aisnes')
        self.tl_vig_02.set_tile(row=4, column=3)
        self.tl_vig_60 = VigilanceTile(self, department='Oise')
        self.tl_vig_60.set_tile(row=4, column=4)
        # Watts news
        self.tl_watts = WattsTile(self)
        self.tl_watts.set_tile(row=4, column=5, columnspan=2)
        # flyspray
        self.tl_fly = FlysprayTile(self, title='live Flyspray DTS Nord')
        self.tl_fly.set_tile(row=5, column=0, rowspan=3, columnspan=7)
        # acc days stat
        self.tl_acc = DaysAccTileLoos(self)
        self.tl_acc.set_tile(row=0, column=8, columnspan=5, rowspan=2)
        # grt logo img
        self.tl_img_grt = ImageRawTile(self, bg='white')
        self.tl_img_grt.set_tile(row=6, column=13, rowspan=2, columnspan=4)
        # carousel
        self.tl_crl = ImageRawCarouselTile(self, bg='white', raw_img_tag_d=Tags.DIR_CAROUSEL_RAW)
        self.tl_crl.set_tile(row=4, column=7, rowspan=4, columnspan=6)
        # start auto-update
        self.init_cyclic_update(every_ms=5_000)
        # at startup:
        # trig update after 2s to let Tags io_thread populate values
        self.after(ms=2_000, func=self.update)

    def update(self):
        # traffic map
        self.tl_tf_map.load(Tags.IMG_TRAFFIC_MAP.get())
        # atmo
        self.tl_img_atmo.load(Tags.IMG_LOGO_ATMO.get())
        # GRT
        self.tl_img_grt.load(Tags.IMG_LOGO_GRT.get())
        # acc days stat
        self.tl_acc.load(date_dts=Tags.D_GSHEET_GRT.get(path=('tags', 'DATE_ACC_DTS')),
                         date_digne=Tags.D_GSHEET_GRT.get(path=('tags', 'DATE_ACC_DIGNE')))
        # weather
        self.tl_weath.load(w_today_dict=Tags.D_W_TODAY_LOOS.get(),
                           w_forecast_dict=Tags.D_W_FORECAST_LOOS.get())
        # air Dunkerque
        self.tl_atmo_dunk.load(level=Tags.D_ATMO_QUALITY.get(path='dunkerque'))
        # air Lille
        self.tl_atmo_lil.load(level=Tags.D_ATMO_QUALITY.get(path='lille'))
        # air Maubeuge
        self.tl_atmo_maub.load(level=Tags.D_ATMO_QUALITY.get(path='maubeuge'))
        # air Saint-Quentin
        self.tl_atmo_sque.load(level=Tags.D_ATMO_QUALITY.get(path='saint-quentin'))
        # cams
        self.tl_cam_gate.load(Tags.IMG_CAM_GATE.get())
        self.tl_cam_door_1.load(Tags.IMG_CAM_DOOR_1.get())
        self.tl_cam_door_2.load(Tags.IMG_CAM_DOOR_2.get())
        # gauges update
        self.tl_g_veh.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_OBJ_DTS'))))
        self.tl_g_loc.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_OBJ_DTS'))))
        self.tl_g_req.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_OBJ_DTS'))))
        self.tl_g_vcs.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_OBJ_DTS'))))
        self.tl_g_vst.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'VST_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'VST_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'VST_OBJ_DTS'))))
        self.tl_g_qsc.load(percent=Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_JAUGE_DTS')),
                           head_str='%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_REAL_DTS')),
                                               Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_OBJ_DTS'))))
        # vigilance
        self.tl_vig_59.load(level=Tags.D_WEATHER_VIG.get(path=('department', '59', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '59', 'risk_id')))
        self.tl_vig_62.load(level=Tags.D_WEATHER_VIG.get(path=('department', '62', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '62', 'risk_id')))
        self.tl_vig_80.load(level=Tags.D_WEATHER_VIG.get(path=('department', '80', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '80', 'risk_id')))
        self.tl_vig_02.load(level=Tags.D_WEATHER_VIG.get(path=('department', '02', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '02', 'risk_id')))
        self.tl_vig_60.load(level=Tags.D_WEATHER_VIG.get(path=('department', '60', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '60', 'risk_id')))
        # Watts news
        self.tl_watts.load(pwr=Tags.MET_PWR_ACT.get(),
                           today_wh=Tags.MET_TODAY_WH.get(),
                           yesterday_wh=Tags.MET_YESTERDAY_WH.get())
        # flyspray
        self.tl_fly.load(task_l=Tags.L_FLYSPRAY_RSS.get())
        # update news widget
        self.tl_news.load(titles_l=Tags.D_NEWS_LOCAL.get())

    def _on_click_gate_tile(self):
        AsyncTasks.rem_redis_actions.add(item='open-car')

    def _on_click_door_1_tile(self):
        AsyncTasks.rem_redis_actions.add(item='open-hall')

    def _on_click_door_2_tile(self):
        AsyncTasks.rem_redis_actions.add(item='open-delivery')


# main
if __name__ == '__main__':
    # parse command line args
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--cursor', action='store_true', default=False,
                        help='display mouse cursor')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='debug mode')
    parser.add_argument('-s', '--skip-full', action='store_true', default=False,
                        help='skip fullscreen mode')
    parser.add_argument('-w', '--wait-up', action='store', type=float, default=30.0,
                        help='wait min sys uptime before tk start (default is 30s)')
    # populate global app_conf
    args = parser.parse_args()
    # at startup: wait system ready (DB, display, RTC sync...)
    # set min uptime (default is 30s)
    wait_uptime(args.wait_up)
    # logging setup
    log_lvl = logging.DEBUG if args.debug else logging.INFO
    log_fmt = '%(asctime)s - %(name)-24s - %(levelname)-8s - %(message)s'
    logging.basicConfig(format=log_fmt, level=log_lvl)
    logger.info('board-hmi-app started')
    # init Tags
    Tags.init()
    # start tkinter
    app = MainApp()
    app.title('Dashboard')
    app.attributes('-fullscreen', not args.skip_full)
    # remove mouse icon in touchscreen mode (default)
    app.config(cursor='' if args.cursor else 'none')
    app.mainloop()
