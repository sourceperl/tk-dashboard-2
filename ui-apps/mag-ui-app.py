#!/usr/bin/env python3

import argparse
import logging
from typing import Any
import tkinter as tk
from tkinter import ttk
from lib.dashboard_ui import AsyncTask, Colors, CustomRedis, ImageRawTile, TilesTab, Tag, TagsBase, Tile, wait_uptime
from conf.private_mag import REDIS_USER, REDIS_PASS, REM_REDIS_HOST, REM_REDIS_PORT, REM_REDIS_USER, REM_REDIS_PASS


class DB:
    # create connector
    main = CustomRedis(host='localhost', username=REDIS_USER, password=REDIS_PASS,
                       socket_timeout=4, socket_keepalive=True)
    remote = CustomRedis(host=REM_REDIS_HOST, port=REM_REDIS_PORT, username=REM_REDIS_USER, password=REM_REDIS_PASS,
                         socket_timeout=4, socket_keepalive=True)


class Tags(TagsBase):
    # create all tags here
    # WARNs: -> all tags with io_every set are manage by an independent (of tk mainloop) IO thread
    #           this thread periodically update tag value and avoid tk GUI loop do this and lose time on DB IO
    #        -> tags callbacks (read/write methods) are call by this IO thread (not by tkinter main thread)
    IMG_CAM_GATE = Tag(read=lambda: DB.main.get('img:cam-gate:jpg'), io_every=2.0)
    IMG_CAM_DOOR_1 = Tag(read=lambda: DB.main.get('img:cam-door-1:jpg'), io_every=2.0)
    IMG_CAM_DOOR_2 = Tag(read=lambda: DB.main.get('img:cam-door-2:jpg'), io_every=2.0)


class RemRedisActionsTask(AsyncTask):
    def do(self, item: Any):
        logging.info(f'request "{item}" action')
        DB.remote.publish(channel='pub:actions', message=item)


class AsyncTasks:
    rem_redis_actions = RemRedisActionsTask(max_items=3)


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
        # loos gate cam
        self.tl_cam_gate = ImageRawTile(self)
        self.tl_cam_gate.set_tile(row=0, column=0, rowspan=2, columnspan=3)
        self.tl_cam_gate.add_on_click_cmd(self._on_click_gate_tile)
        # loos door 1 cam
        self.tl_cam_door_1 = ImageRawTile(self)
        self.tl_cam_door_1.set_tile(row=0, column=3, rowspan=2, columnspan=2)
        self.tl_cam_door_1.add_on_click_cmd(self._on_click_door_1_tile)
        # loos door 2 cam
        self.tl_cam_door_2 = ImageRawTile(self)
        self.tl_cam_door_2.set_tile(row=0, column=5, rowspan=2, columnspan=3)
        self.tl_cam_door_2.add_on_click_cmd(self._on_click_door_2_tile)
        # start auto-update
        self.init_cyclic_update(every_ms=5_000)
        # at startup:
        # trig update after 2s to let Tags io_thread populate values
        self.after(ms=2_000, func=self.update)

    def update(self):
        # cams
        self.tl_cam_gate.load(Tags.IMG_CAM_GATE.get())
        self.tl_cam_door_1.load(Tags.IMG_CAM_DOOR_1.get())
        self.tl_cam_door_2.load(Tags.IMG_CAM_DOOR_2.get())

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
