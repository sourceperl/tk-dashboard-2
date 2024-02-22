#!/usr/bin/env python3

import argparse
import logging
import tkinter as tk
from tkinter import ttk
from lib.dashboard_ui import \
    CustomRedis, Tag, TagsBase, TilesTab, PdfTilesTab, wait_uptime, \
    AirQualityTile, ClockTile, DaysAccTileLoos, EmptyTile, GaugeTile, NewsBannerTile, \
    FlysprayTile, ImageRawTile, ImageRawCarouselTile, VigilanceTile, WattsTile, WeatherTile
from conf.private_loos import REDIS_USER, REDIS_PASS


class DB:
    # create connector
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


class Tags(TagsBase):
    # create all tags here
    # WARNs: -> all tags with io_every set are manage by an independent (of tk mainloop) IO thread
    #           this thread periodically update tag value and avoid tk GUI loop do this and lose time on DB IO
    #        -> tags callbacks (read/write methods) are call by this IO thread (not by tkinter main thread)
    D_GSHEET_GRT = Tag(read=lambda: DB.main.get_js('json:gsheet'), io_every=2.0)
    D_ATMO_QUALITY = Tag(read=lambda: DB.main.get_js('json:atmo'), io_every=2.0)
    D_W_TODAY_LOOS = Tag(read=lambda: DB.main.get_js('json:weather:today:loos'), io_every=2.0)
    D_W_FORECAST_LOOS = Tag(read=lambda: DB.main.get_js('json:weather:forecast:loos'), io_every=2.0)
    D_WEATHER_VIG = Tag(read=lambda: DB.main.get_js('json:vigilance'), io_every=2.0)
    D_NEWS_LOCAL = Tag(read=lambda: DB.main.get_js('json:news'), io_every=2.0)
    MET_PWR_ACT = Tag(read=lambda: DB.main.get_js('int:loos_elec:pwr_act'), io_every=1.0)
    MET_TODAY_WH = Tag(read=lambda: DB.main.get_js('float:loos_elec:today_wh'), io_every=2.0)
    MET_YESTERDAY_WH = Tag(read=lambda: DB.main.get_js('float:loos_elec:yesterday_wh'), io_every=2.0)
    L_FLYSPRAY_RSS = Tag(read=lambda: DB.main.get_js('json:flyspray-nord'), io_every=2.0)
    IMG_ATMO_HDF = Tag(read=lambda: DB.main.get('img:static:logo-atmo-hdf:png'), io_every=10.0)
    IMG_LOGO_GRT = Tag(read=lambda: DB.main.get('img:static:logo-grt:png'), io_every=10.0)
    IMG_TRAFFIC_MAP = Tag(read=lambda: DB.main.get('img:traffic-map:png'), io_every=10.0)
    DIR_CAROUSEL_RAW = Tag(read=lambda: DB.main.hgetall('dir:carousel:raw:min-png'), io_every=10.0)
    DIR_PDF_DOC_LIST = Tag(read=lambda: map(bytes.decode, DB.main.hkeys('dir:doc:raw')))
    RAW_PDF_DOC_CONTENT = Tag(read=lambda file: DB.main.hget('dir:doc:raw', file))


class MainApp(tk.Tk):
    def __init__(self, *args, **kwargs):
        tk.Tk.__init__(self, *args, **kwargs)
        # tk stuff
        # remove mouse icon in touchscreen mode (default)
        if not app_conf.cursor:
            self.config(cursor='none')
        # define style to fix size of tab header
        self.style = ttk.Style()
        self.style.theme_settings('default', {'TNotebook.Tab': {'configure': {'padding': [17, 17]}}})
        # define notebook
        self.note = ttk.Notebook(self)
        self.tab1 = LiveTilesTab(self.note, tiles_size=(17, 9), update_ms=5000)
        self.tab2 = PdfTilesTab(self.note, tiles_size=(17, 12), update_ms=5000,
                                list_tag=Tags.DIR_PDF_DOC_LIST, raw_tag=Tags.RAW_PDF_DOC_CONTENT)
        self.note.add(self.tab1, text='Tableau de bord')
        self.note.add(self.tab2, text='Affichage réglementaire')
        self.note.pack()
        # default tab
        self.note.select(self.tab1)
        # press Esc to quit
        self.bind('<Escape>', lambda e: self.destroy())
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
        # logo Atmo HDF
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
        # empty
        self.tl_empty = EmptyTile(self)
        self.tl_empty.set_tile(row=2, column=5, rowspan=2, columnspan=8)
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
        # grt img
        self.tl_img_grt = ImageRawTile(self, bg='white')
        self.tl_img_grt.set_tile(row=6, column=13, rowspan=2, columnspan=4)
        # carousel
        self.tl_crl = ImageRawCarouselTile(self, bg='white', raw_img_tag_d=Tags.DIR_CAROUSEL_RAW)
        self.tl_crl.set_tile(row=4, column=7, rowspan=4, columnspan=6)
        # start auto-update
        self.start_cyclic_update()
        # at startup:
        # trig update after 1s to let Tags io_thread populate values
        self.after(ms=1000, func=self.update)

    def update(self):
        # GRT wordcloud
        # self.tl_img_cloud.raw_display = Tags.IMG_GRT_CLOUD.get()
        # traffic map
        self.tl_tf_map.raw_display = Tags.IMG_TRAFFIC_MAP.get()
        # atmo
        self.tl_img_atmo.raw_display = Tags.IMG_ATMO_HDF.get()
        # GRT
        self.tl_img_grt.raw_display = Tags.IMG_LOGO_GRT.get()
        # acc days stat
        self.tl_acc.acc_date_dts = Tags.D_GSHEET_GRT.get(('tags', 'DATE_ACC_DTS'))
        self.tl_acc.acc_date_digne = Tags.D_GSHEET_GRT.get(('tags', 'DATE_ACC_DIGNE'))
        # weather
        self.tl_weath.w_today_dict = Tags.D_W_TODAY_LOOS.get()
        self.tl_weath.w_forecast_dict = Tags.D_W_FORECAST_LOOS.get()
        # air Dunkerque
        self.tl_atmo_dunk.qlt_index = Tags.D_ATMO_QUALITY.get('dunkerque')
        # air Lille
        self.tl_atmo_lil.qlt_index = Tags.D_ATMO_QUALITY.get('lille')
        # air Maubeuge
        self.tl_atmo_maub.qlt_index = Tags.D_ATMO_QUALITY.get('maubeuge')
        # air Saint-Quentin
        self.tl_atmo_sque.qlt_index = Tags.D_ATMO_QUALITY.get('saint-quentin')
        # update news widget
        self.tl_news.l_titles = Tags.D_NEWS_LOCAL.get()
        # gauges update
        self.tl_g_veh.percent = Tags.D_GSHEET_GRT.get(('tags', 'IGP_VEH_JAUGE_DTS'))
        self.tl_g_veh.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'IGP_VEH_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'IGP_VEH_OBJ_DTS')))
        self.tl_g_loc.percent = Tags.D_GSHEET_GRT.get(('tags', 'IGP_LOC_JAUGE_DTS'))
        self.tl_g_loc.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'IGP_LOC_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'IGP_LOC_OBJ_DTS')))
        self.tl_g_req.percent = Tags.D_GSHEET_GRT.get(('tags', 'R_EQU_JAUGE_DTS'))
        self.tl_g_req.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'R_EQU_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'R_EQU_OBJ_DTS')))
        self.tl_g_vcs.percent = Tags.D_GSHEET_GRT.get(('tags', 'VCS_JAUGE_DTS'))
        self.tl_g_vcs.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'VCS_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'VCS_OBJ_DTS')))
        self.tl_g_vst.percent = Tags.D_GSHEET_GRT.get(('tags', 'VST_JAUGE_DTS'))
        self.tl_g_vst.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'VST_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'VST_OBJ_DTS')))
        self.tl_g_qsc.percent = Tags.D_GSHEET_GRT.get(('tags', 'Q_HRE_JAUGE_DTS'))
        self.tl_g_qsc.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(('tags', 'Q_HRE_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(('tags', 'Q_HRE_OBJ_DTS')))
        # vigilance
        self.tl_vig_59.vig_level = Tags.D_WEATHER_VIG.get(('department', '59', 'vig_level'))
        self.tl_vig_59.risk_ids = Tags.D_WEATHER_VIG.get(('department', '59', 'risk_id'))
        self.tl_vig_62.vig_level = Tags.D_WEATHER_VIG.get(('department', '62', 'vig_level'))
        self.tl_vig_62.risk_ids = Tags.D_WEATHER_VIG.get(('department', '62', 'risk_id'))
        self.tl_vig_80.vig_level = Tags.D_WEATHER_VIG.get(('department', '80', 'vig_level'))
        self.tl_vig_80.risk_ids = Tags.D_WEATHER_VIG.get(('department', '80', 'risk_id'))
        self.tl_vig_02.vig_level = Tags.D_WEATHER_VIG.get(('department', '02', 'vig_level'))
        self.tl_vig_02.risk_ids = Tags.D_WEATHER_VIG.get(('department', '02', 'risk_id'))
        self.tl_vig_60.vig_level = Tags.D_WEATHER_VIG.get(('department', '60', 'vig_level'))
        self.tl_vig_60.risk_ids = Tags.D_WEATHER_VIG.get(('department', '60', 'risk_id'))
        # Watts news
        self.tl_watts.pwr = Tags.MET_PWR_ACT.get()
        self.tl_watts.today_wh = Tags.MET_TODAY_WH.get()
        self.tl_watts.yesterday_wh = Tags.MET_YESTERDAY_WH.get()
        # flyspray
        self.tl_fly.l_items = Tags.L_FLYSPRAY_RSS.get()


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
    app_conf = parser.parse_args()
    # at startup: wait system ready (DB, display, RTC sync...)
    # set min uptime (default is 30s)
    wait_uptime(app_conf.wait_up)
    # logging setup
    lvl = logging.DEBUG if app_conf.debug else logging.INFO
    logging.basicConfig(format='%(asctime)s %(message)s', level=lvl)
    logging.info('board-hmi-app started')
    # init Tags
    Tags.init()
    # start tkinter
    app = MainApp()
    app.title('GRTgaz Dashboard')
    app.attributes('-fullscreen', not app_conf.skip_full)
    app.mainloop()
