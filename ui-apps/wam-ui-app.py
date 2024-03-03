#!/usr/bin/env python3

import argparse
import logging
from typing import Any
import tkinter as tk
from tkinter import ttk
from lib.dashboard_ui import \
    Colors, CustomRedis, Tag, TagsBase, Tile, TilesTab, wait_uptime, \
    AirQualityTile, ClockTile, EmptyTile,  ImageRawTile, VigilanceTile
from conf.private_wam import REDIS_USER, REDIS_PASS


class DB:
    # create connector
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)


class Tags(TagsBase):
    # create all tags here
    # WARNs: -> all tags with io_every set are manage by an independent (of tk mainloop) IO thread
    #           this thread periodically update tag value and avoid tk GUI loop do this and lose time on DB IO
    #        -> tags callbacks (read/write methods) are call by this IO thread (not by tkinter main thread)
    D_ATMO_QUALITY = Tag(read=lambda: DB.main.get_js('json:atmo'), io_every=2.0)
    D_WEATHER_VIG = Tag(read=lambda: DB.main.get_js('json:vigilance'), io_every=2.0)
    BLE_SENSOR_DATA = Tag(read=lambda: DB.main.get_js('json:ble-data'), io_every=2.0)
    IMG_ATMO_HDF = Tag(read=lambda: DB.main.get('img:static:logo-atmo-hdf:png'), io_every=10.0)
    IMG_MF = Tag(read=lambda: DB.main.get('img:static:logo-mf:png'), io_every=10.0)
    IMG_TRAFFIC_MAP = Tag(read=lambda: DB.main.get('img:traffic-map:png'), io_every=10.0)


class CustomLabelTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        self.str_var = tk.StringVar()
        tk.Label(self, textvariable=self.str_var, font=('bold', 14), bg=self.cget('bg'),
                 anchor=tk.W, justify=tk.LEFT, fg=Colors.TXT).pack(expand=True)


class MainApp(tk.Tk):
    def __init__(self, *args, **kwargs):
        tk.Tk.__init__(self, *args, **kwargs)
        # tk stuff
        # remove mouse icon in touchscreen mode (default)
        if not app_conf.cursor:
            self.config(cursor='none')
        # define style to fix size of tab header
        self.style = ttk.Style()
        self.style.theme_settings('default', {'TNotebook.Tab': {'configure': {'padding': [8, 8]}}})
        # define notebook
        self.note = ttk.Notebook(self)
        self.tab1 = LiveTilesTab(self.note, tiles_size=(8, 4))
        self.note.add(self.tab1, text='Tableau de bord')
        self.note.pack()
        # default tab
        self.note.select(self.tab1)
        # press Esc to quit
        self.bind('<Escape>', lambda evt: self.destroy())
        # bind function keys to tabs
        self.bind('<F1>', lambda evt: self.note.select(self.tab1))
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
        # air quality Lille
        self.tl_atmo_lil = AirQualityTile(self, city='Lille')
        self.tl_atmo_lil.set_tile(row=0, column=1)
        # logo MF
        self.tl_img_mf = ImageRawTile(self, bg='#005892')
        self.tl_img_mf.set_tile(row=0, column=2)
        # weather vigilance
        self.tl_vig_59 = VigilanceTile(self, department='59')
        self.tl_vig_59.set_tile(row=0, column=3)
        self.tl_vig_62 = VigilanceTile(self, department='62')
        self.tl_vig_62.set_tile(row=0, column=4)
        # traffic map
        self.tl_tf_map = ImageRawTile(self, bg='#bbe2c6')
        self.tl_tf_map.set_tile(row=1, column=0, rowspan=3, columnspan=5)
        # clock
        self.tl_clock = ClockTile(self)
        self.tl_clock.set_tile(row=0, column=5, rowspan=2, columnspan=3)
        # ext weather
        self.tl_ext = CustomLabelTile(self)
        self.tl_ext.set_tile(row=2, column=5, rowspan=1, columnspan=1)
        # kitchen weather
        self.tl_kit = CustomLabelTile(self)
        self.tl_kit.set_tile(row=2, column=6, rowspan=1, columnspan=1)
        # start auto-update
        self.init_cyclic_update(every_ms=5_000)
        # at startup:
        # trig update after 2s to let Tags io_thread populate values
        self.after(ms=2_000, func=self.update)

    def update(self):
        # atmo
        self.tl_img_atmo.raw_display = Tags.IMG_ATMO_HDF.get()
        # air Lille
        self.tl_atmo_lil.qlt_index = Tags.D_ATMO_QUALITY.get(path='lille')
        # mf
        self.tl_img_mf.raw_display = Tags.IMG_MF.get()
        # vigilance
        self.tl_vig_59.vig_level = Tags.D_WEATHER_VIG.get(path=('department', '59', 'vig_level'))
        self.tl_vig_59.risk_ids = Tags.D_WEATHER_VIG.get(path=('department', '59', 'risk_id'))
        self.tl_vig_62.vig_level = Tags.D_WEATHER_VIG.get(path=('department', '62', 'vig_level'))
        self.tl_vig_62.risk_ids = Tags.D_WEATHER_VIG.get(path=('department', '62', 'risk_id'))
        # traffic map
        self.tl_tf_map.raw_display = Tags.IMG_TRAFFIC_MAP.get()
        # outdoor ble data
        temp_c = Tags.BLE_SENSOR_DATA.get(path=('outdoor', 'temp_c'))
        temp_c_str = f'{temp_c:>6.1f}' if temp_c is not None else 'n/a'
        hum_p = Tags.BLE_SENSOR_DATA.get(path=('outdoor', 'hum_p'))
        hum_p_str = f'{hum_p:>6.1f}' if hum_p is not None else 'n/a'
        self.tl_ext.str_var.set(f'Extérieur\n\n\N{THERMOMETER} {temp_c_str} °C\n\N{BLACK DROPLET} {hum_p_str} %')
        # kitchen ble data
        temp_c = Tags.BLE_SENSOR_DATA.get(path=('kitchen', 'temp_c'))
        temp_c_str = f'{temp_c:>6.1f}' if temp_c is not None else 'n/a'
        hum_p = Tags.BLE_SENSOR_DATA.get(path=('kitchen', 'hum_p'))
        hum_p_str = f'{hum_p:>6.1f}' if hum_p is not None else 'n/a'
        self.tl_kit.str_var.set(f'Cuisine\n\n\N{THERMOMETER} {temp_c_str} °C\n\N{BLACK DROPLET} {hum_p_str} %')


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
    app.title('My dashboard')
    app.geometry('800x480')
    app.attributes('-fullscreen', not app_conf.skip_full)
    app.mainloop()
