#!/usr/bin/env python3

import argparse
import logging
import tkinter as tk
from tkinter import ttk

from conf.private_wam import REDIS_PASS, REDIS_USER
from lib.dashboard_ui import (
    AirQualityTile,
    ClockTile,
    Colors,
    CustomRedis,
    EmptyTile,
    ImageRawTile,
    Tag,
    TagsBase,
    Tile,
    TilesTab,
    VigilanceTile,
    fmt_value,
    wait_uptime,
)


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
    BLE_OUTDOOR_TEMP_C = Tag(read=lambda: DB.main.get_js('json:ble:outdoor:temp_c'), io_every=2.0)
    BLE_OUTDOOR_HUM_P = Tag(read=lambda: DB.main.get_js('json:ble:outdoor:hum_p'), io_every=2.0)
    BLE_KITCHEN_TEMP_C = Tag(read=lambda: DB.main.get_js('json:ble:kitchen:temp_c'), io_every=2.0)
    BLE_KITCHEN_HUM_P = Tag(read=lambda: DB.main.get_js('json:ble:kitchen:hum_p'), io_every=2.0)
    METAR_DATA = Tag(read=lambda: DB.main.get_js('json:metar:lesquin'), io_every=2.0)
    IMG_ATMO_HDF = Tag(read=lambda: DB.main.get('img:static:logo-atmo-hdf:png'), io_every=10.0)
    IMG_MF = Tag(read=lambda: DB.main.get('img:static:logo-mf:png'), io_every=10.0)
    IMG_TRAFFIC_MAP = Tag(read=lambda: DB.main.get('img:traffic-map:png'), io_every=10.0)


class CustomLabelTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        self.str_var = tk.StringVar()
        tk.Label(self, textvariable=self.str_var, font=('bold', 12), bg=self.cget('bg'),
                 anchor=tk.W, justify=tk.LEFT, fg=Colors.TXT).place(relx=0, rely=0)

    def load(self, txt: str) -> None:
        # enforce type
        txt = str(txt)
        # update widget
        self.str_var.set(txt)


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
        self.tl_kit.set_tile(row=2, column=7, rowspan=1, columnspan=1)
        # kitchen weather
        self.tl_metar = CustomLabelTile(self)
        self.tl_metar.set_tile(row=3, column=5, rowspan=1, columnspan=3)
        # start auto-update
        self.init_cyclic_update(every_ms=5_000)
        # at startup:
        # trig update after 2s to let Tags io_thread populate values
        self.after(ms=2_000, func=self.update)

    def update(self):
        # atmo
        self.tl_img_atmo.load(Tags.IMG_ATMO_HDF.get())
        # air Lille
        self.tl_atmo_lil.load(level=Tags.D_ATMO_QUALITY.get(path='lille'))
        # mf
        self.tl_img_mf.load(Tags.IMG_MF.get())
        # vigilance
        self.tl_vig_59.load(level=Tags.D_WEATHER_VIG.get(path=('department', '59', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '59', 'risk_id')))
        self.tl_vig_62.load(level=Tags.D_WEATHER_VIG.get(path=('department', '62', 'vig_level')),
                            risk_id_l=Tags.D_WEATHER_VIG.get(path=('department', '62', 'risk_id')))
        # traffic map
        self.tl_tf_map.load(Tags.IMG_TRAFFIC_MAP.get(), crop=(30, 0, 530, 328))
        # outdoor ble data
        temp_c = fmt_value(Tags.BLE_OUTDOOR_TEMP_C.get(path='value'), fmt='>6.1f')
        hum_p = fmt_value(Tags.BLE_OUTDOOR_HUM_P.get(path='value'), fmt='>6.1f')
        self.tl_ext.load(txt=f'Extérieur\n\n\N{THERMOMETER} {temp_c} °C\n\N{BLACK DROPLET} {hum_p} %')
        # kitchen ble data
        temp_c = fmt_value(Tags.BLE_KITCHEN_TEMP_C.get(path='value'), fmt='>6.1f')
        hum_p = fmt_value(Tags.BLE_KITCHEN_HUM_P.get(path='value'), fmt='>6.1f')
        self.tl_kit.load(txt=f'Cuisine\n\n\N{THERMOMETER} {temp_c} °C\n\N{BLACK DROPLET} {hum_p} %')
        # metar data
        update_fr = fmt_value(Tags.METAR_DATA.get(path='update_fr'), fmt='', alt_str='\t')
        press_hpa = fmt_value(Tags.METAR_DATA.get(path='press'), fmt='>5.0f')
        temp_c = fmt_value(Tags.METAR_DATA.get(path='temp'), fmt='>8.1f', alt_str='   n/a')
        dewpt_c = fmt_value(Tags.METAR_DATA.get(path='dewpt'), fmt='>6.1f')
        w_speed_kmh = fmt_value(Tags.METAR_DATA.get(path='w_speed'), fmt='>3.0f')
        w_dir = fmt_value(Tags.METAR_DATA.get(path='w_dir'), fmt='')
        w_gust_kmh = fmt_value(Tags.METAR_DATA.get(path='w_gust'), fmt='>3.0f', alt_str='')
        w_gust_kmh_str = f'(\N{LEAF FLUTTERING IN WIND} {w_gust_kmh} km/h)' if w_gust_kmh else ''
        self.tl_metar.load(txt=f'Lesquin (station Météo-France)\n'
                           f'{update_fr}\t\N{TIMER CLOCK} {press_hpa} hPa\n'
                           f'\N{THERMOMETER} {temp_c} °C'
                           f'\t\N{WIND BLOWING FACE} {w_speed_kmh} km/h\n'
                           f'\N{THERMOMETER}\N{BLACK DROPLET} {dewpt_c} °C  '
                           f'\t\N{WHITE-FEATHERED RIGHTWARDS ARROW}   {w_dir}'
                           f' {w_gust_kmh_str}')


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
