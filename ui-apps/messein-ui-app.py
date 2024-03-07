#!/usr/bin/env python3

import argparse
import logging
import tkinter as tk
from tkinter import ttk
from lib.dashboard_ui import \
    CustomRedis, EmptyTile, Tag, TagsBase, TilesTab, PdfTilesTab, wait_uptime, \
    AirQualityTile, ClockTile, DaysAccTileMessein, FlysprayTile, GaugeTile, \
    ImageRawTile, ImageRawCarouselTile, NewsBannerTile, VigilanceTile
from conf.private_messein import REDIS_USER, REDIS_PASS


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
    D_WEATHER_VIG = Tag(read=lambda: DB.main.get_js('json:vigilance'), io_every=2.0)
    D_NEWS_LOCAL = Tag(read=lambda: DB.main.get_js('json:news'), io_every=2.0)
    L_FLYSPRAY_RSS = Tag(read=lambda: DB.main.get_js('from:loos:json:flyspray-est'), io_every=2.0)
    IMG_ATMO_GE = Tag(read=lambda: DB.main.get('img:static:logo-atmo-ge:png'), io_every=10.0)
    IMG_LOGO_GRT = Tag(read=lambda: DB.main.get('img:static:logo-grt:png'), io_every=10.0)
    IMG_TRAFFIC_MAP = Tag(read=lambda: DB.main.get('img:traffic-map:png'), io_every=10.0)
    IMG_DIR_CAM_HOUDEMONT = Tag(read=lambda: DB.main.get('img:dir-est:houdemont:png'), io_every=10.0)
    IMG_DIR_CAM_VELAINE = Tag(read=lambda: DB.main.get('img:dir-est:velaine:png'), io_every=10.0)
    IMG_DIR_CAM_ST_NICOLAS = Tag(read=lambda: DB.main.get('img:dir-est:st-nicolas:png'), io_every=10.0)
    IMG_DIR_CAM_FLAVIGNY = Tag(read=lambda: DB.main.get('img:dir-est:flavigny:png'), io_every=10.0)
    DIR_CAROUSEL_RAW = Tag(read=lambda: DB.main.hgetall('dir:carousel:raw:min-png'), io_every=10.0)
    PDF_FILENAMES_L = Tag(read=lambda: map(bytes.decode, DB.main.hkeys('dir:doc:raw')))
    PDF_CONTENT = Tag(read=lambda file: DB.main.hget('dir:doc:raw', file))


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
        self.tab1 = LiveTilesTab(self.note, tiles_size=(17, 9))
        self.tab2 = PdfTilesTab(self.note, tiles_size=(17, 9),
                                list_tag=Tags.PDF_FILENAMES_L, raw_tag=Tags.PDF_CONTENT)
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
        # logo Atmo EST
        self.tl_img_atmo = ImageRawTile(self, bg='white')
        self.tl_img_atmo.set_tile(row=0, column=0)
        # air quality Nancy
        self.tl_atmo_nancy = AirQualityTile(self, city='Nancy')
        self.tl_atmo_nancy.set_tile(row=0, column=1)
        # air quality Metz
        self.tl_atmo_metz = AirQualityTile(self, city='Metz')
        self.tl_atmo_metz.set_tile(row=0, column=2)
        # air quality Reims
        self.tl_atmo_reims = AirQualityTile(self, city='Reims')
        self.tl_atmo_reims.set_tile(row=0, column=3)
        # air quality Strasbourg
        self.tl_atmo_stras = AirQualityTile(self, city='Strasbourg')
        self.tl_atmo_stras.set_tile(row=0, column=4)
        # traffic map
        self.tl_tf_map = ImageRawTile(self, bg='#bbe2c6')
        self.tl_tf_map.set_tile(row=1, column=0, rowspan=3, columnspan=5)
        # DIR-est Houdemont
        self.tl_img_houdemont = ImageRawTile(self)
        self.tl_img_houdemont.set_tile(row=0, column=5, rowspan=2, columnspan=2)
        # DIR-est Velaine-en-Haye
        self.tl_img_velaine = ImageRawTile(self)
        self.tl_img_velaine.set_tile(row=0, column=7, rowspan=2, columnspan=2)
        # DIR-est Saint-Nicolas
        self.tl_img_st_nicolas = ImageRawTile(self)
        self.tl_img_st_nicolas.set_tile(row=0, column=9, rowspan=2, columnspan=2)
        # DIR-est Côte de Flavigny
        self.tl_img_flavigny = ImageRawTile(self)
        self.tl_img_flavigny.set_tile(row=0, column=11, rowspan=2, columnspan=2)
        # clock
        self.tl_clock = ClockTile(self)
        self.tl_clock.set_tile(row=0, column=13, rowspan=2, columnspan=4)
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
        self.tl_vig_54 = VigilanceTile(self, department='Meurthe & M')
        self.tl_vig_54.set_tile(row=4, column=0)
        self.tl_vig_55 = VigilanceTile(self, department='Meuse')
        self.tl_vig_55.set_tile(row=4, column=1)
        self.tl_vig_57 = VigilanceTile(self, department='Moselle')
        self.tl_vig_57.set_tile(row=4, column=2)
        self.tl_vig_88 = VigilanceTile(self, department='Vosges')
        self.tl_vig_88.set_tile(row=4, column=3)
        self.tl_vig_67 = VigilanceTile(self, department='Bas-Rhin')
        self.tl_vig_67.set_tile(row=4, column=4)
        # flyspray
        self.tl_fly = FlysprayTile(self, title='live Flyspray DTS Est')
        self.tl_fly.set_tile(row=5, column=0, rowspan=3, columnspan=7)
        # acc days stat
        self.tl_acc = DaysAccTileMessein(self)
        self.tl_acc.set_tile(row=2, column=13, columnspan=4, rowspan=1)
        # logo img
        self.tl_img_grt = ImageRawTile(self, bg='white')
        self.tl_img_grt.set_tile(row=6, column=13, rowspan=2, columnspan=4)
        # carousel
        self.tl_crl = ImageRawCarouselTile(self, bg='white', raw_img_tag_d=Tags.DIR_CAROUSEL_RAW)
        self.tl_crl.set_tile(row=4, column=7, rowspan=4, columnspan=6)
        # start auto-update
        self.init_cyclic_update(every_ms=5_000)
        # at startup:
        # trig update after 1s to let Tags io_thread populate values
        self.after(ms=1000, func=self.update)

    def update(self):
        # traffic map
        self.tl_tf_map.load(Tags.IMG_TRAFFIC_MAP.get())
        # atmo
        self.tl_img_atmo.load(Tags.IMG_ATMO_GE.get())
        # GRT
        self.tl_img_grt.load(Tags.IMG_LOGO_GRT.get())
        # DIR-Est webcams
        self.tl_img_houdemont.load = Tags.IMG_DIR_CAM_HOUDEMONT.get()
        self.tl_img_velaine.load = Tags.IMG_DIR_CAM_VELAINE.get()
        self.tl_img_st_nicolas.load = Tags.IMG_DIR_CAM_ST_NICOLAS.get()
        self.tl_img_flavigny.load = Tags.IMG_DIR_CAM_FLAVIGNY.get()
        # acc days stat
        self.tl_acc.acc_date_dts = Tags.D_GSHEET_GRT.get(path=('tags', 'DATE_ACC_DTS'))
        # air Nancy
        self.tl_atmo_nancy.level = Tags.D_ATMO_QUALITY.get(path='nancy')
        # air Metz
        self.tl_atmo_metz.level = Tags.D_ATMO_QUALITY.get(path='metz')
        # air Reims
        self.tl_atmo_reims.level = Tags.D_ATMO_QUALITY.get(path='reims')
        # air Strasbourg
        self.tl_atmo_stras.level = Tags.D_ATMO_QUALITY.get(path='strasbourg')
        # empty area(s)
        self.tl_empty1 = EmptyTile(self)
        self.tl_empty1.set_tile(row=2, column=5, rowspan=2, columnspan=8)
        self.tl_empty2 = EmptyTile(self)
        self.tl_empty2.set_tile(row=4, column=5, rowspan=1, columnspan=2)
        # news banner
        self.tl_news.l_titles = Tags.D_NEWS_LOCAL.get()
        # gauges update
        self.tl_g_veh.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_JAUGE_DTS'))
        self.tl_g_veh.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_VEH_OBJ_DTS')))
        self.tl_g_loc.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_JAUGE_DTS'))
        self.tl_g_loc.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'IGP_LOC_OBJ_DTS')))
        self.tl_g_req.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_JAUGE_DTS'))
        self.tl_g_req.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'R_EQU_OBJ_DTS')))
        self.tl_g_vcs.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_JAUGE_DTS'))
        self.tl_g_vcs.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'VCS_OBJ_DTS')))
        self.tl_g_vst.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'VST_JAUGE_DTS'))
        self.tl_g_vst.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'VST_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'VST_OBJ_DTS')))
        self.tl_g_qsc.percent = Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_JAUGE_DTS'))
        self.tl_g_qsc.header_str = '%s/%s' % (Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_REAL_DTS')),
                                              Tags.D_GSHEET_GRT.get(path=('tags', 'Q_HRE_OBJ_DTS')))
        # weather vigilance
        self.tl_vig_54.level = Tags.D_WEATHER_VIG.get(path=('department', '54', 'vig_level'))
        self.tl_vig_54.risk_ids_l = Tags.D_WEATHER_VIG.get(path=('department', '54', 'risk_id'))
        self.tl_vig_55.level = Tags.D_WEATHER_VIG.get(path=('department', '55', 'vig_level'))
        self.tl_vig_55.risk_ids_l = Tags.D_WEATHER_VIG.get(path=('department', '55', 'risk_id'))
        self.tl_vig_57.level = Tags.D_WEATHER_VIG.get(path=('department', '57', 'vig_level'))
        self.tl_vig_57.risk_ids_l = Tags.D_WEATHER_VIG.get(path=('department', '57', 'risk_id'))
        self.tl_vig_88.level = Tags.D_WEATHER_VIG.get(path=('department', '88', 'vig_level'))
        self.tl_vig_88.risk_ids_l = Tags.D_WEATHER_VIG.get(path=('department', '88', 'risk_id'))
        self.tl_vig_67.level = Tags.D_WEATHER_VIG.get(path=('department', '67', 'vig_level'))
        self.tl_vig_67.risk_ids_l = Tags.D_WEATHER_VIG.get(path=('department', '67', 'risk_id'))
        # flyspray
        self.tl_fly.load(Tags.L_FLYSPRAY_RSS.get())


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
