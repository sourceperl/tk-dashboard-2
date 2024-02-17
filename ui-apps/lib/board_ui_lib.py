#!/usr/bin/env python3

from datetime import datetime, timedelta
import copy
import glob
import functools
import io
import json
import math
import os
import subprocess
import tempfile
import locale
import logging
import threading
import time
import traceback
import tkinter as tk
import redis
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import PIL.ImageTk

# global configuration
# avoid PIL debug message
logging.getLogger('PIL').setLevel(logging.WARNING)


# some const as class
# default dashboard color (can be override)
class Colors:
    # colors
    WHITE = '#eff0f1'
    BLUE = '#4dbbdb'
    BLACK = '#100e0e'
    GREEN = '#00704f'
    YELLOW = '#dab02d'
    ORANGE = '#dd6c1e'
    PINK = '#b86d6c'
    RED = '#b22222'
    # dashboard
    BG = '#75adb1'
    TILE_BORDER = '#3c4f69'
    TXT = WHITE
    H_TXT = '#81424b'
    NA = PINK
    TWEET = BLUE
    NEWS_BG = '#f7e44f'
    NEWS_TXT = BLACK


# geometry
class Geometry:
    NB_TILE_W = 17
    NB_TILE_H = 9
    TAB_PAD_HEIGHT = 17
    TAB_PAD_WIDTH = 17
    NEWS_BANNER_HEIGHT = 90


# some function
def catch_log_except(catch=None, log_lvl=logging.ERROR, limit_arg_len=40):
    # decorator to catch exception and produce one line log message
    if catch is None:
        catch = Exception

    def _catch_log_except(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except catch as e:
                # format function call "f_name(args..., kwargs...)" string (with arg/kwargs len limit)
                func_args = ''
                for arg in args:
                    func_args += ', ' if func_args else ''
                    func_args += repr(arg) if len(repr(arg)) < limit_arg_len else repr(arg)[:limit_arg_len - 2] + '..'
                for k, v in kwargs.items():
                    func_args += ', ' if func_args else ''
                    func_args += repr(k) + '='
                    func_args += repr(v) if len(repr(v)) < limit_arg_len else repr(v)[:limit_arg_len - 2] + '..'
                func_call = f'{func.__name__}({func_args})'
                # log message "except [except class] in f_name(args..., kwargs...): [except msg]"
                logging.log(log_lvl, f'except {type(e)} in {func_call}: {e}')

        return wrapper

    return _catch_log_except


def wait_uptime(min_s: float):
    while True:
        uptime = float(open('/proc/uptime', 'r').readline().split()[0])
        if uptime > min_s:
            break
        time.sleep(0.1)


# some class
class CustomRedis(redis.Redis):
    LOG_LEVEL = logging.DEBUG

    @catch_log_except(catch=redis.RedisError, log_lvl=LOG_LEVEL)
    def execute_command(self, *args, **options):
        return super().execute_command(*args, **options)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def set_js(self, name, obj, ex=None, px=None, nx=False, xx=False, keepttl=False):
        return super().set(name=name, value=json.dumps(obj), ex=ex, px=px, nx=nx, xx=xx, keepttl=keepttl)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def get_js(self, name):
        return json.loads(super().get(name).decode('utf-8'))


class Tag:
    def __init__(self, value=None, read=None, write=None, io_every=None):
        # private
        self._value = value
        self._read_cmd = read
        self._write_cmd = write
        self._lock = threading.Lock()
        self._th_io_every = io_every
        self._th_last_run = 0.0

    def io_update(self, ref=''):
        # method call by Tags io thread
        if self._th_io_every:
            t_now = time.monotonic()
            run_now = (t_now - self._th_last_run) > self._th_io_every
            # if read method is define, do it
            if run_now:
                self._th_last_run = t_now
                # if read method is define, do it
                if callable(self._read_cmd):
                    logging.debug(f'IO thread call read cmd' + f' [ref {ref}]' if ref else f'')
                    # secure call to read method callback, catch any exception
                    try:
                        cache_value = self._read_cmd()
                    except Exception:
                        cache_value = None
                    # update internal tag value
                    with self._lock:
                        self._value = cache_value
                # if write method is define, do it
                if callable(self._write_cmd):
                    logging.debug(f'IO thread call write cmd' + f' [ref {ref}]' if ref else f'')
                    # avoid lock thread during _write_cmd() IO stuff
                    # read internal tag value
                    with self._lock:
                        cached_value = self._value
                    # secure call to write method callback, catch any exception
                    try:
                        self._write_cmd(cached_value)
                    except Exception:
                        pass

    def set(self, value):
        with self._lock:
            self._value = value
        # if tag don't use io_thread, call _write_cmd immediately
        if not self._th_io_every:
            if callable(self._write_cmd):
                try:
                    self._write_cmd(value)
                except Exception:
                    pass

    def get(self, path=None, args=None):
        # process func args
        if args is None:
            args = {}
        # if this tag don't use io_thread, call _read_cmd now
        if not self._th_io_every:
            if callable(self._read_cmd):
                try:
                    cached_value = self._read_cmd(**args)
                except Exception:
                    cached_value = None
                with self._lock:
                    self._value = cached_value
        # if a path is define use it
        if path:
            # ensure path is an iterable
            if not type(path) in (tuple, list):
                path = [path]
            # explore path to retrieve item we want
            with self._lock:
                # ensure no reference to _value by copy
                item = copy.copy(self._value)
            try:
                for cur_lvl in path:
                    item = item[cur_lvl]
                return item
            # return None if path unavailable
            except (KeyError, TypeError, IndexError):
                return None
        else:
            # return simple value (avoid return reference with copy)
            with self._lock:
                # ensure no reference to _value by copy
                return copy.copy(self._value)


class TagsBase:
    # create all tags here
    # WARNs: -> all tags with io_every set are manage by an independent (of tk mainloop) IO thread
    #           this thread periodically update tag value and avoid tk GUI loop do this and lose time on DB IO
    #        -> tags callbacks (read/write methods) are call by this IO thread (not by tkinter main thread)
    __IO_THREAD_TAG_LIST = list()

    @classmethod
    def init(cls):
        # compile tag list for IO thread before starting it
        for name, attr in cls.__dict__.items():
            if not name.startswith('__') and isinstance(attr, Tag):
                cls.__IO_THREAD_TAG_LIST.append((name, attr))
        # start IO thread
        threading.Thread(target=cls._io_thread_task, daemon=True).start()

    @classmethod
    def _io_thread_task(cls):
        # IO thread main loop
        while True:
            for name, tag in cls.__IO_THREAD_TAG_LIST:
                tag.io_update(ref=name)
            time.sleep(1.0)


# Tab library
class Tab(tk.Frame):
    """
    Base Tab class, with a frame full of tile, can be derived as you need it
    """

    def __init__(self, *args, **kwargs):
        tk.Frame.__init__(self, *args, **kwargs)
        # public
        self._update_ms = None
        self.nb_tile_w = Geometry.NB_TILE_W
        self.nb_tile_h = Geometry.NB_TILE_H
        # private
        self._screen_w = self.winfo_screenwidth()
        self._screen_h = self.winfo_screenheight() - 60
        self._lbl__padx = round(self._screen_w / (self.nb_tile_w * 2))
        self._lbl_pady = round((self._screen_h - Geometry.TAB_PAD_HEIGHT) / (self.nb_tile_h * 2))
        # tk stuff
        # populate the grid with all tiles
        for c in range(0, self.nb_tile_w):
            for r in range(0, self.nb_tile_h):
                self.grid_rowconfigure(r, weight=1)
                # create Labels to space all of it
                tk.Label(self, pady=self._lbl_pady, padx=self._lbl__padx).grid(column=c, row=r)
                Tile(self).set_tile(row=r, column=c)
            self.grid_columnconfigure(c, weight=1)
        # init tab update
        self.bind('<Visibility>', lambda evt: self.update())

    def start_cyclic_update(self, update_ms=500):
        self._update_ms = update_ms
        # init loop
        self._do_cyclic_update()

    def _do_cyclic_update(self):
        if self.winfo_ismapped():
            self.update()
        self.after(self._update_ms, self._do_cyclic_update)

    def update(self):
        pass


class PdfTab(Tab):
    def __init__(self, *args, list_tag, raw_tag, **kwargs):
        Tab.__init__(self, *args, **kwargs)
        # public
        self.list_tag = list_tag
        self.raw_tag = raw_tag
        # private
        self._file_l = list()
        self._widgets_l = list()
        # auto-update every 5s
        self.start_cyclic_update(update_ms=5000)

    @property
    def file_list(self):
        return self._file_l

    @file_list.setter
    def file_list(self, value):
        # check type
        try:
            value = sorted(list(value))
        except (TypeError, ValueError):
            value = None
        # check change
        if self._file_l != value:
            # copy value to private cache
            self._file_l = value
            # notify change
            self._on_list_change()

    def update(self):
        # update PDF list from infos dict
        try:
            self.file_list = self.list_tag.get()
        except (AttributeError, ValueError):
            # notify error
            self.file_list = None

    def _on_list_change(self):
        # if file list change, reflect it on display
        try:
            # remove all existing tiles widgets
            for w in self._widgets_l:
                w.destroy()
            self._widgets_l.clear()
            # if file list is empty or None
            if not self._file_l:
                # display error message "n/a"
                msg_tl = MessageTile(self)
                msg_tl.set_tile(row=0, column=0, rowspan=self.nb_tile_h, columnspan=self.nb_tile_w)
                msg_tl.tk_str_msg.set('n/a')
                self._widgets_l.append(msg_tl)
            else:
                # populate with new file launcher
                # start at 0:1 pos
                (r, c) = (0, 1)
                for file_name in self._file_l:
                    # place PdfLauncherTile at (r,c)
                    launch_tile = PdfLauncherTile(self,
                                                  file=file_name,
                                                  raw_tag=self.raw_tag)
                    launch_tile.set_tile(row=r, column=c, columnspan=5, rowspan=1)
                    self._widgets_l.append(launch_tile)
                    # set next place
                    c += 5
                    if c >= self.nb_tile_w - 1:
                        r += 1
                        c = 1
        except Exception:
            logging.error(traceback.format_exc())


# Tiles library
class Tile(tk.Frame):
    """
    Source of all the tile here
    Default : a gray, black bordered, case
    """

    def __init__(self, *args, **kwargs):
        tk.Frame.__init__(self, *args, **kwargs)
        # public
        # private
        self._update_ms = None
        # tk stuff
        self.configure(highlightbackground=Colors.TILE_BORDER)
        self.configure(highlightthickness=3)
        self.configure(bd=0)
        # set default background color, if bg args is not set
        if not kwargs.get('bg'):
            self.configure(bg=Colors.BG)
        # deny frame resize
        self.pack_propagate(False)
        self.grid_propagate(False)

    def set_tile(self, row=0, column=0, rowspan=1, columnspan=1):
        # function to print a tile on the screen at the given coordonates
        self.grid(row=row, column=column, rowspan=rowspan, columnspan=columnspan, sticky=tk.NSEW)

    def start_cyclic_update(self, update_ms=500):
        self._update_ms = update_ms
        # first update
        self.update()
        # init loop
        self._do_cyclic_update()

    def _do_cyclic_update(self):
        if self.winfo_ismapped():
            self.update()
        self.after(self._update_ms, self._do_cyclic_update)

    def update(self):
        pass


class AirQualityTile(Tile):
    QUALITY_LVL = ('n/a', 'Bon', 'Moyen', 'Dégradé', 'Mauvais', 'Moyen',
                   'Très mauvais', 'Extrêmement mauvais')

    def __init__(self, *args, city, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.city = city
        # private
        self._qlt_index = 0
        self._index_str = tk.StringVar()
        self._status_str = tk.StringVar()
        self._index_str.set('N/A')
        self._status_str.set('N/A')
        # tk job
        tk.Label(self, text=city, font='bold', fg=Colors.TXT).pack()
        tk.Label(self).pack()
        tk.Label(self, textvariable=self._index_str, fg=Colors.TXT).pack()
        tk.Label(self, textvariable=self._status_str, fg=Colors.TXT).pack()

    @property
    def qlt_index(self):
        return self._qlt_index

    @qlt_index.setter
    def qlt_index(self, value):
        # check type
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._qlt_index != value:
            self._qlt_index = value
            self._on_data_change()

    def _on_data_change(self):
        try:
            self._index_str.set('%d/6' % self._qlt_index)
            self._status_str.set(AirQualityTile.QUALITY_LVL[self._qlt_index])
        except (TypeError, ZeroDivisionError):
            # set tk var
            self._index_str.set('N/A')
            self._status_str.set('N/A')
            # choose tile color
            tile_color = Colors.NA
        else:
            # choose tile color
            tile_color = Colors.GREEN
            if self._qlt_index > 4:
                tile_color = Colors.RED
            elif self._qlt_index > 2:
                tile_color = Colors.ORANGE
        # update tile and his childs color
        for w in self.winfo_children():
            w.configure(bg=tile_color)
        self.configure(bg=tile_color)


class ClockTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._date_str = tk.StringVar()
        self._time_str = tk.StringVar()
        # set locale (for french day name)
        locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')
        # tk stuff
        tk.Label(self, textvariable=self._date_str, font=('bold', 16), bg=self.cget('bg'), anchor=tk.W,
                 justify=tk.LEFT, fg=Colors.TXT).pack(expand=True)
        tk.Label(self, textvariable=self._time_str, font=('digital-7', 30), bg=self.cget('bg'),
                 fg=Colors.TXT).pack(expand=True)
        # auto-update clock
        self.start_cyclic_update(update_ms=500)

    def update(self):
        self._date_str.set(datetime.now().strftime('%A %d %B %Y'))
        self._time_str.set(datetime.now().strftime('%H:%M:%S'))


class DaysAccTileLoos(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._acc_date_dts = None
        self._acc_date_digne = None
        self._days_dts_str = tk.StringVar()
        self._days_digne_str = tk.StringVar()
        # tk stuff
        # populate tile with blank grid parts
        for c in range(3):
            for r in range(3):
                self.grid_rowconfigure(r, weight=1)
                if c > 0:
                    tk.Label(self, bg=self.cget('bg')).grid(row=r, column=c, )
            self.columnconfigure(c, weight=1)
        # add label
        tk.Label(self, text='La sécurité est notre priorité !',
                 font=('courier', 20, 'bold'), bg=self.cget('bg'),
                 fg=Colors.TXT).grid(row=0, column=0, columnspan=2)
        # DTS
        tk.Label(self, textvariable=self._days_dts_str, font=('courier', 24, 'bold'),
                 bg=self.cget('bg'), fg=Colors.H_TXT).grid(row=1, column=0)
        tk.Label(self, text='jours sans accident DTS',
                 font=('courier', 18, 'bold'), bg=self.cget('bg'), fg=Colors.TXT).grid(row=1, column=1, sticky=tk.W)
        # DIGNE
        tk.Label(self, textvariable=self._days_digne_str, font=('courier', 24, 'bold'),
                 bg=self.cget('bg'), fg=Colors.H_TXT).grid(row=2, column=0)
        tk.Label(self, text='jours sans accident DIGNE',
                 font=('courier', 18, 'bold'), bg=self.cget('bg'), fg=Colors.TXT).grid(row=2, column=1, sticky=tk.W)
        # auto-update acc day counter
        self.start_cyclic_update(update_ms=5000)

    @property
    def acc_date_dts(self):
        return self._acc_date_dts

    @acc_date_dts.setter
    def acc_date_dts(self, value):
        # check type
        try:
            value = str(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._acc_date_dts != value:
            # check range
            self._acc_date_dts = value
            # update widget
            self.update()

    @property
    def acc_date_digne(self):
        return self._acc_date_digne

    @acc_date_digne.setter
    def acc_date_digne(self, value):
        # check type
        try:
            value = str(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._acc_date_digne != value:
            # check range
            self._acc_date_digne = value
            # update widget
            self.update()

    def update(self):
        self._days_dts_str.set(self.day_from_now(self._acc_date_dts))
        self._days_digne_str.set(self.day_from_now(self._acc_date_digne))

    @staticmethod
    def day_from_now(date_str):
        try:
            day, month, year = map(int, str(date_str).split('/'))
            return str((datetime.now() - datetime(year, month, day)).days)
        except (TypeError, ValueError):
            return 'n/a'


class DaysAccTileMessein(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._acc_date_dts = None
        self._acc_date_digne = None
        self._days_dts_str = tk.StringVar()
        # tk stuff
        # populate tile with blank grid parts
        for c in range(3):
            for r in range(3):
                self.grid_rowconfigure(r, weight=1)
                if c > 0:
                    tk.Label(self, bg=self.cget('bg')).grid(row=r, column=c, )
            self.columnconfigure(c, weight=1)
        # add label
        tk.Label(self, text='La sécurité est notre priorité !',
                 font=('courier', 16, 'bold'), bg=self.cget('bg'),
                 fg=Colors.TXT).grid(row=0, column=0, columnspan=2)
        # DTS
        tk.Label(self, textvariable=self._days_dts_str, font=('courier', 22, 'bold'),
                 bg=self.cget('bg'), fg=Colors.H_TXT).grid(row=1, column=0)
        tk.Label(self, text='jours sans accident DTS',
                 font=('courier', 14, 'bold'), bg=self.cget('bg'), fg=Colors.TXT).grid(row=1, column=1, sticky=tk.W)
        # auto-update acc day counter
        self.start_cyclic_update(update_ms=5000)

    @property
    def acc_date_dts(self):
        return self._acc_date_dts

    @acc_date_dts.setter
    def acc_date_dts(self, value):
        # check type
        try:
            value = str(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._acc_date_dts != value:
            # check range
            self._acc_date_dts = value
            # update widget
            self.update()

    def update(self):
        self._days_dts_str.set(self.day_from_now(self._acc_date_dts))

    @staticmethod
    def day_from_now(date_str):
        try:
            day, month, year = map(int, str(date_str).split('/'))
            return str((datetime.now() - datetime(year, month, day)).days)
        except (TypeError, ValueError):
            return 'n/a'


class FlysprayTile(Tile):
    def __init__(self, *args, title='', **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.title = title
        # private
        self._l_items = None
        self._msg_text = tk.StringVar()
        self._msg_text.set('n/a')
        # tk job
        tk.Label(self, text=self.title, bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold', 'underline')).pack()
        tk.Label(self, textvariable=self._msg_text, bg=self.cget('bg'), fg=Colors.TXT,
                 wraplength=750, justify=tk.LEFT, font=('courier', 13, 'bold')).pack(expand=True)

    @property
    def l_items(self):
        return self._l_items

    @l_items.setter
    def l_items(self, value):
        # check type
        try:
            value = list(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._l_items != value:
            self._l_items = value
            self._on_data_change()

    def _on_data_change(self):
        TTE_MAX_NB = 12
        TTE_MAX_LEN = 75
        try:
            msg = ''
            # limit titles number
            for item in self._l_items[:TTE_MAX_NB]:
                # limit title length
                title = item['title']
                title = (title[:TTE_MAX_LEN - 2] + '..') if len(title) > TTE_MAX_LEN else title
                msg += '%s\n' % title
            self._msg_text.set(msg)
        except Exception:
            self._msg_text.set('n/a')


class GaugeTile(Tile):
    GAUGE_MIN = 0.0
    GAUGE_MAX = 100.0

    def __init__(self, *args, title, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.title = title
        self.th_orange = 70
        self.th_red = 40
        # private
        self._str_title = tk.StringVar()
        self._str_title.set(self.title)
        self._head_str = ''
        self._percent = None
        # tk build
        self.label = tk.Label(self, textvariable=self._str_title, font='bold', bg=Colors.BG, fg=Colors.TXT)
        self.label.grid(sticky=tk.NSEW)
        self.can = tk.Canvas(self, width=220, height=110, borderwidth=2, relief='sunken', bg='white')
        self.can.grid()
        self.can_arrow = self.can.create_line(100, 100, 10, 100, fill='grey', width=3, arrow='last')
        self.can.lower(self.can_arrow)
        self.can.create_arc(20, 10, 200, 200, extent=108, start=36, style='arc', outline='black')

    @property
    def percent(self):
        return self._percent

    @percent.setter
    def percent(self, value):
        # check type
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._percent != value:
            # check range
            self._percent = value
            # update widget
            self._on_data_change()

    @property
    def header_str(self):
        return self._head_str

    @header_str.setter
    def header_str(self, value):
        # check type
        try:
            value = str(value)
        except (TypeError, ValueError):
            value = ''
        # check change
        if self._head_str != value:
            # check range
            self._head_str = value
            # update widget
            self._on_data_change()

    def _on_data_change(self):
        # update widget
        try:
            # convert value
            ratio = (self._percent - self.GAUGE_MIN) / (self.GAUGE_MAX - self.GAUGE_MIN)
            ratio = min(ratio, 1.0)
            ratio = max(ratio, 0.0)
            # set arrow on widget
            self._set_arrow(ratio)
            # update alarm, warn, fine status
            if self._percent < self.th_red:
                self.can.configure(bg=Colors.RED)
            elif self._percent < self.th_orange:
                self.can.configure(bg=Colors.YELLOW)
            else:
                self.can.configure(bg=Colors.GREEN)
            if self._head_str:
                self._str_title.set('%s (%s)' % (self.title, self._head_str))
            else:
                self._str_title.set('%s (%.1f %%)' % (self.title, self.percent))
        except (TypeError, ZeroDivisionError):
            self._set_arrow(0.0)
            self.can.configure(bg=Colors.NA)
            self._str_title.set('%s (%s)' % (self.title, 'N/A'))

    def _set_arrow(self, ratio):
        # normalize ratio : 0.2 to 0.8
        ratio = ratio * 0.6 + 0.2
        # compute arrow head
        x = 112 - 90 * math.cos(ratio * math.pi)
        y = 100 - 90 * math.sin(ratio * math.pi)
        # update canvas
        self.can.coords(self.can_arrow, 112, 100, x, y)


class ImageTile(Tile):
    def __init__(self, *args, file='', img_ratio=1, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # tk job
        self.tk_img = tk.PhotoImage()
        self.lbl_img = tk.Label(self, bg=self.cget('bg'))
        self.lbl_img.pack(expand=True)
        # display current image file
        try:
            # set file path
            self.tk_img.configure(file=file)
            # set image with resize ratio (if need)
            self.tk_img = self.tk_img.subsample(img_ratio)
            self.lbl_img.configure(image=self.tk_img)
        except Exception:
            logging.error(traceback.format_exc())


class ImageRawTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # tk widget init
        self.tk_img = tk.PhotoImage()
        self.lbl_img = tk.Label(self, bg=self.cget('bg'))
        self.lbl_img.pack(expand=True)

    @property
    def raw_display(self):
        return self.raw_display

    @raw_display.setter
    def raw_display(self, value):
        try:
            widget_size = (self.winfo_width(), self.winfo_height())
            # display current image file if raw_img is set
            if value:
                # RAW img data to Pillow (PIL) image
                pil_img = PIL.Image.open(io.BytesIO(value))
                # force image size to widget size
                pil_img.thumbnail(widget_size)
            else:
                # create a replace 'n/a' image
                pil_img = PIL.Image.new('RGB', widget_size, Colors.PINK)
                txt = 'n/a'
                draw = PIL.ImageDraw.Draw(pil_img)
                font = PIL.ImageFont.truetype('/usr/share/fonts/truetype/freefont/FreeMono.ttf', 24)
                w, h = draw.textsize(txt, font=font)
                x = (widget_size[0] - w) / 2
                y = (widget_size[1] - h) / 2
                draw.text((x, y), txt, fill='black', font=font)
            # update image label
            self.tk_img = PIL.ImageTk.PhotoImage(pil_img)
            self.lbl_img.configure(image=self.tk_img)
        except Exception:
            logging.error(traceback.format_exc())


class ImageRefreshTile(Tile):
    def __init__(self, *args, file, img_ratio=1, refresh_rate=5000, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.file = file
        self.img_ratio = img_ratio
        # tk job
        self.tk_img = tk.PhotoImage()
        self.lbl_img = tk.Label(self, bg=self.cget('bg'))
        self.lbl_img.pack(expand=True)
        # auto-update clock
        self.start_cyclic_update(update_ms=refresh_rate)

    def update(self):
        # display current image file
        try:
            # set file path
            self.tk_img.configure(file=self.file)
            # set image with resize ratio (if need)
            self.tk_img = self.tk_img.subsample(self.img_ratio)
            self.lbl_img.configure(image=self.tk_img)
        except Exception:
            logging.error(traceback.format_exc())


class ImageCarouselTile(Tile):
    def __init__(self, *args, img_path, refresh_rate=20000, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.img_path = img_path
        # private
        self._img_index = 0
        self._img_files = list()
        self._skip_update_cnt = 0
        # tk job
        self.configure(bg='white')
        self.tk_img = tk.PhotoImage()
        self.lbl_img = tk.Label(self, image=self.tk_img)
        self.lbl_img.pack(expand=True)
        # first img load
        self._img_files_load()
        # bind function for skip update
        self.bind('<Button-1>', self._on_click)
        self.lbl_img.bind('<Button-1>', self._on_click)
        # auto-update carousel rotate
        self.start_cyclic_update(update_ms=refresh_rate)

    def update(self):
        # display next image or skip this if skip counter is set
        if self._skip_update_cnt <= 0:
            self._load_next_img()
        else:
            self._skip_update_cnt -= 1

    def _on_click(self, _evt):
        # on first click: skip the 8 next auto update cycle
        # on second one: also load the next image
        if self._skip_update_cnt > 0:
            self._load_next_img()
        self._skip_update_cnt = 8

    def _load_next_img(self):
        # next img file index
        self._img_index += 1
        if self._img_index >= len(self._img_files):
            self._img_index = 0
            self._img_files_load()
        # display current image file
        try:
            self.tk_img.configure(file=self._img_files[self._img_index])
        except Exception:
            logging.error(traceback.format_exc())

    def _img_files_load(self):
        self._img_files = glob.glob(os.path.join(self.img_path, '*.png'))
        self._img_files.sort()


class ImageRawCarouselTile(Tile):
    def __init__(self, *args, raw_img_tag_d, change_rate_s=20.0, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.raw_img_tag_d = raw_img_tag_d
        # private
        self._playlist = list()
        self._skip_update_cnt = 0
        # tk widget init
        # don't remove tk_img: keep a ref to avoid del by garbage collect
        self.tk_img = tk.PhotoImage()
        self.lbl_img = tk.Label(self, bg=self.cget('bg'))
        self.lbl_img.pack(expand=True)
        # bind function for skip update
        self.bind('<Button-1>', self._on_click)
        self.lbl_img.bind('<Button-1>', self._on_click)
        # auto-update carousel rotate
        self.start_cyclic_update(update_ms=round(change_rate_s * 1000))
        # force update after 3s at dashboard startup (redis init time)
        self.after(ms=3000, func=self.update)

    @property
    def raw_display(self):
        return self.raw_display

    @raw_display.setter
    def raw_display(self, value):
        try:
            widget_size = (self.winfo_width(), self.winfo_height())
            # display current image file if raw_img is set
            if value:
                # RAW img data to Pillow (PIL) image
                pil_img = PIL.Image.open(io.BytesIO(value))
                # force image size to widget size
                pil_img.thumbnail(widget_size)
            else:
                # create a replace 'n/a' image
                pil_img = PIL.Image.new('RGB', widget_size, Colors.PINK)
                txt = 'n/a'
                draw = PIL.ImageDraw.Draw(pil_img)
                font = PIL.ImageFont.truetype('/usr/share/fonts/truetype/freefont/FreeMono.ttf', 24)
                w, h = draw.textsize(txt, font=font)
                x = (widget_size[0] - w) / 2
                y = (widget_size[1] - h) / 2
                draw.text((x, y), txt, fill='black', font=font)
            # update image label
            self.tk_img = PIL.ImageTk.PhotoImage(pil_img)
            self.lbl_img.configure(image=self.tk_img)
        except Exception:
            logging.error(traceback.format_exc())

    def update(self):
        # display next image or skip this if skip counter is set
        if self._skip_update_cnt <= 0:
            self._load_next_img()
        else:
            self._skip_update_cnt -= 1

    def _load_next_img(self):
        try:
            # try to load next valid image
            while True:
                next_img_name = self._playlist.pop(0)
                raw_value = self.raw_img_tag_d.get(next_img_name)
                # load valid raw img or try next one
                if raw_value:
                    # load display and exit loop
                    self.raw_display = raw_value
                    break
        except IndexError:
            # refill playlist if empty
            self._fill_playlist()

    def _fill_playlist(self):
        # fill playlist with image filename to display
        try:
            img_raw_d = self.raw_img_tag_d.get()
            if not img_raw_d:
                raise ValueError
            self._playlist = list(img_raw_d.keys())
            self._playlist.sort()
        except ValueError:
            # clear playlist and force "n/a" on Tile display
            self.raw_display = None
            self._playlist.clear()

    def _on_click(self, _evt):
        # on first click: skip the 8 next auto update cycle
        # on second one: also load the next image
        if self._skip_update_cnt > 0:
            self._load_next_img()
        self._skip_update_cnt = 8


class MessageTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.tk_str_msg = tk.StringVar()
        # tk stuff
        tk.Label(self, textvariable=self.tk_str_msg, bg=self.cget('bg'),
                 fg=Colors.TXT, font=('courrier', 20, 'bold')).pack(expand=True)


class NewsBannerTile(Tile):
    BAN_MAX_NB_CHAR = 50

    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.ban_nb_char = NewsBannerTile.BAN_MAX_NB_CHAR
        # private
        self._l_titles = []
        self._lbl_ban = tk.StringVar()
        self._next_ban_str = ''
        self._disp_ban_str = ''
        self._disp_ban_pos = 0
        # tk stuff
        # set background for this tile
        self.configure(bg=Colors.NEWS_BG)
        # use a proportional font to handle spaces correctly, height is nb of lines
        tk.Label(self, textvariable=self._lbl_ban, height=1,
                 bg=self.cget('bg'), fg=Colors.NEWS_TXT, font=('courier', 51, 'bold')).pack(expand=True)
        # auto-update clock
        self.start_cyclic_update(update_ms=200)

    @property
    def l_titles(self):
        return self._l_titles

    @l_titles.setter
    def l_titles(self, value):
        # check type
        try:
            value = list(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._l_titles != value:
            # check range
            self._l_titles = value
            # update widget
            self._on_data_change()

    def update(self):
        # scroll text on screen
        # start a new scroll ?
        if self._disp_ban_pos >= len(self._disp_ban_str) - self.ban_nb_char:
            # update display scroll message
            self._disp_ban_str = self._next_ban_str
            self._disp_ban_pos = 0
        scroll_view = self._disp_ban_str[self._disp_ban_pos:self._disp_ban_pos + self.ban_nb_char]
        self._lbl_ban.set(scroll_view)
        self._disp_ban_pos += 1

    def _on_data_change(self):
        spaces_head = ' ' * self.ban_nb_char
        try:
            # update banner
            self._next_ban_str = spaces_head
            for title in self._l_titles:
                self._next_ban_str += title + spaces_head
        except TypeError:
            self._next_ban_str = spaces_head + 'n/a' + spaces_head
        except Exception:
            self._next_ban_str = spaces_head + 'n/a' + spaces_head
            logging.error(traceback.format_exc())


class PdfLauncherTile(Tile):
    def __init__(self, *args, file, raw_tag, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.file = file
        self.raw_tag = raw_tag
        # private
        self._front_name = os.path.splitext(self.file)[0].strip()
        self._ps_l = list()
        # tk stuff
        self._name_lbl = tk.Label(self, text=self._front_name, wraplength=550,
                                  bg=self.cget('bg'), fg=Colors.TXT, font=('courrier', 20, 'bold'))
        self._name_lbl.pack(expand=True)
        # bind function for open pdf file
        self.bind('<Button-1>', self._on_click)
        self._name_lbl.bind('<Button-1>', self._on_click)
        self.bind('<Destroy>', self._on_unmap)
        self.bind('<Unmap>', self._on_unmap)

    def _on_click(self, _evt):
        try:
            # build a temp file with RAW pdf data from redis hash
            raw_data = self.raw_tag.get(args={'file': self.file})
            if raw_data:
                tmp_f = tempfile.NamedTemporaryFile(prefix='board-', suffix='.pdf', delete=True)
                tmp_f.write(raw_data)
                # open it with xpdf
                xpdf_geometry = '-geometry %sx%s' % (self.master.winfo_width(), self.master.winfo_height() - 10)
                ps = subprocess.Popen(['/usr/bin/xpdf', xpdf_geometry, '-z page', '-cont', tmp_f.name],
                                      stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,
                                      close_fds=True)
                # keep process references for _on_unmap() job
                self._ps_l.append(ps)
                # remove temp file after xpdf startup
                self.after(ms=1000, func=tmp_f.close)
        except Exception:
            logging.error(traceback.format_exc())

    def _on_unmap(self, _evt):
        # terminate all xpdf process on tab exit
        # iterate on copy of process list
        for ps in list(self._ps_l):
            # terminate (ps wait for zombie process avoid)
            ps.terminate()
            ps.wait()
            # remove ps from original list
            self._ps_l.remove(ps)


class TwitterTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._l_tweet = None
        self._tw_text = tk.StringVar()
        self._tw_text.set('n/a')
        self._tw_index = 0
        # tk job
        self.configure(bg=Colors.TWEET)
        tk.Label(self, text='live twitter: GRTgaz', bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold', 'underline')).pack()
        tk.Label(self, textvariable=self._tw_text, bg=self.cget('bg'), fg=Colors.TXT,
                 wraplength=550, font=('courier', 14, 'bold')).pack(expand=True)
        # auto-update carousel rotate
        self.start_cyclic_update(update_ms=12000)

    @property
    def l_tweet(self):
        return self._l_tweet

    @l_tweet.setter
    def l_tweet(self, value):
        # check type
        try:
            value = list(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._l_tweet != value:
            self._l_tweet = value
            # priority fart last tweet
            self._tw_index = 0
            self.update()

    def update(self):
        if self.l_tweet:
            if self._tw_index >= len(self._l_tweet):
                self._tw_index = 0
            self._tw_text.set(self._l_tweet[self._tw_index])
            self._tw_index += 1
        else:
            self._tw_index = 0
            self._tw_text.set('n/a')


class VigilanceTile(Tile):
    VIG_LVL = ['verte', 'jaune', 'orange', 'rouge']
    VIG_COLOR = [Colors.GREEN, Colors.YELLOW, Colors.ORANGE, Colors.RED]
    ID_RISK = ['n/a', 'vent', 'pluie', 'orages', 'inondation', 'neige verglas',
               'canicule', 'grand froid', 'avalanches', 'submersion']

    def __init__(self, *args, department='', **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        self.department = department
        # private
        self._vig_level = None
        self._risk_ids = []
        self._level_str = tk.StringVar()
        self._risk_str = tk.StringVar()
        self._level_str.set('N/A')
        self._risk_str.set('')
        # tk job
        self.configure(bg=Colors.NA)
        tk.Label(self, text='Vigilance', font='bold', bg=Colors.NA, fg=Colors.TXT).pack()
        tk.Label(self, text=self.department, font='bold', bg=Colors.NA, fg=Colors.TXT).pack()
        tk.Label(self, font=('', 6), bg=Colors.NA, fg=Colors.TXT).pack()
        tk.Label(self, textvariable=self._level_str, font='bold', bg=Colors.NA, fg=Colors.TXT).pack()
        tk.Label(self, textvariable=self._risk_str, font=('', 8), bg=Colors.NA, fg=Colors.TXT).pack()

    @property
    def vig_level(self):
        return self._vig_level

    @vig_level.setter
    def vig_level(self, value):
        # check type
        try:
            value = int(value) - 1
        except (TypeError, ValueError):
            value = None
        # check change
        if self._vig_level != value:
            self._vig_level = value
            self._on_data_change()

    @property
    def risk_ids(self):
        return self._risk_ids

    @risk_ids.setter
    def risk_ids(self, value):
        # check type
        try:
            value = [int(i) for i in list(value)]
        except (TypeError, ValueError):
            value = []
        # check change
        if self._risk_ids != value:
            self._risk_ids = value
            self._on_data_change()

    def _on_data_change(self):
        # update color of tile and color str
        try:
            level_str = VigilanceTile.VIG_LVL[self._vig_level].upper()
            tile_color = VigilanceTile.VIG_COLOR[self._vig_level]
        except (IndexError, TypeError):
            level_str = 'N/A'
            tile_color = Colors.NA
        # apply to tk
        self._level_str.set(f'{level_str}')
        for w in self.winfo_children():
            w.configure(bg=tile_color)
        self.configure(bg=tile_color)
        # add risks str
        try:
            str_risk = ' '
            for id_risk in self._risk_ids[:2]:
                str_risk += VigilanceTile.ID_RISK[id_risk] + ' '
        except (IndexError, TypeError):
            str_risk = 'n/a'
        # apply to tk
        self._risk_str.set(f'{str_risk}')


class WattsTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._pwr = None
        self._tdy_wh = None
        self._ydy_wh = None
        self._pwr_text = tk.StringVar()
        self._tdy_text = tk.StringVar()
        self._ydy_text = tk.StringVar()
        # tk job
        tk.Label(self, text='Loos Watts news', bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold', 'underline')).pack()
        tk.Label(self, textvariable=self._pwr_text, bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold')).pack(expand=True)
        tk.Label(self, textvariable=self._tdy_text, bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold')).pack(expand=True)
        tk.Label(self, textvariable=self._ydy_text, bg=self.cget('bg'), fg=Colors.TXT,
                 font=('courier', 14, 'bold')).pack(expand=True)
        # public with accessor
        self.pwr = None
        self.today_wh = None
        self.yesterday_wh = None

    @property
    def pwr(self):
        return self._pwr

    @pwr.setter
    def pwr(self, value):
        # check type
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._pwr != value:
            self._pwr = value
        # update tk lbl
        self._pwr_text.set('  P %5s w  ' % ('n/a' if self._pwr is None else self._pwr))

    @property
    def today_wh(self):
        return self._tdy_wh

    @today_wh.setter
    def today_wh(self, value):
        # check type
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._tdy_wh != value:
            self._tdy_wh = value
        # update tk lbl
        self._tdy_text.set('  J %5s kwh' % ('n/a' if self._tdy_wh is None else round(self._tdy_wh / 1000)))

    @property
    def yesterday_wh(self):
        return self._ydy_wh

    @yesterday_wh.setter
    def yesterday_wh(self, value):
        # check type
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        # check change
        if self._ydy_wh != value:
            self._ydy_wh = value
        # update tk lbl
        self._ydy_text.set('J-1 %5s kwh' % ('n/a' if self._ydy_wh is None else round(self._ydy_wh / 1000)))


class WeatherTile(Tile):
    def __init__(self, *args, **kwargs):
        Tile.__init__(self, *args, **kwargs)
        # public
        # private
        self._w_today_dict = None
        self._w_forecast_dict = None
        self._days_f_l = list()
        self._days_lbl = list()
        # tk stuff
        # build 4x3 grid
        for c in range(4):
            for r in range(3):
                self.grid_rowconfigure(r, weight=1)
                tk.Label(self, pady=0, padx=0).grid(column=c, row=r)
            self.grid_columnconfigure(c, weight=1)
            # creation
            self._days_f_l.append(
                tk.LabelFrame(self, text='n/a', bg=self.cget('bg'), fg=Colors.TXT,
                              font=('bold', 10)))
            self._days_lbl.append(
                tk.Label(self._days_f_l[c], text='n/a', bg=self.cget('bg'), fg=Colors.TXT,
                         font='bold', anchor=tk.W, justify=tk.LEFT))
            # impression
            self._days_f_l[c].grid(row=2, column=c, sticky=tk.NSEW)
            self._days_f_l[c].grid_propagate(False)
            self._days_lbl[c].grid(sticky=tk.NSEW)
            self._days_lbl[c].grid_propagate(False)
        # today frame
        self.frm_today = tk.LabelFrame(self, bg=self.cget('bg'), fg=Colors.TXT, text='n/a', font=('bold', 18))
        self.lbl_today = tk.Label(self.frm_today, text='n/a', bg=self.cget('bg'), fg=Colors.TXT,
                                  font=('courier', 18, 'bold'), anchor=tk.W, justify=tk.LEFT)
        self.frm_today.grid(row=0, column=0, columnspan=4, rowspan=2, sticky=tk.NSEW)
        self.frm_today.grid_propagate(False)
        self.lbl_today.grid(column=0)
        self.lbl_today.grid_propagate(False)

    @property
    def w_today_dict(self):
        return self._w_today_dict

    @w_today_dict.setter
    def w_today_dict(self, value):
        # check change
        if self._w_today_dict != value:
            self._w_today_dict = value
            self._on_today_change()

    @property
    def w_forecast_dict(self):
        return self._w_forecast_dict

    @w_forecast_dict.setter
    def w_forecast_dict(self, value):
        # check change
        if self._w_forecast_dict != value:
            self._w_forecast_dict = value
            self._on_forecast_change()

    def _on_today_change(self):
        # set today frame label
        self.frm_today.configure(text=datetime.now().date().strftime('%d/%m/%Y'))
        # fill labels
        if self._w_today_dict:
            try:
                # today
                temp = '%s' % self._w_today_dict.get('temp', '--')
                dewpt = '%s' % self._w_today_dict.get('dewpt', '--')
                press = '%s' % self._w_today_dict.get('press', '----')
                w_speed = '%s' % self._w_today_dict.get('w_speed', '--')
                w_gust_msg = '%s' % self._w_today_dict.get('w_gust', '')
                w_gust_msg = '%9s' % ('(raf %s)' % w_gust_msg) if w_gust_msg else ''
                w_dir = self._w_today_dict.get('w_dir', '--')
                update_fr = self._w_today_dict.get('update_fr', '--')
                # today message
                msg = f'Température    : {temp:>4} °C\n' + \
                      f'Point de rosée : {dewpt:>4} °C\n' + \
                      f'Pression       : {press:>4} hPa\n' + \
                      f'Vent {w_gust_msg:9} : {w_speed:>4} km/h {w_dir}\n' + \
                      f'\n' + \
                      f'Mise à jour    : {update_fr}\n'
                self.lbl_today.configure(text=msg)
            except Exception:
                logging.error(traceback.format_exc())
                self.lbl_today.configure(text='error')
        else:
            self.lbl_today.configure(text='n/a')

    def _on_forecast_change(self):
        # set forecast frames labels
        for i in range(4):
            dt = datetime.now().date() + timedelta(days=i + 1)
            self._days_f_l[i].configure(text=dt.strftime('%d/%m/%Y'))
        # refresh forecast labels with new data if availables (or error msg if not)
        if self._w_forecast_dict:
            try:
                for i in range(4):
                    # set day message
                    d = str(i + 1)
                    day_desr = self._w_forecast_dict[d]['description']
                    day_t_min = self._w_forecast_dict[d]['t_min']
                    day_t_max = self._w_forecast_dict[d]['t_max']
                    msg = f'{day_desr}\n\nT min {day_t_min:.0f}°C\nT max {day_t_max:.0f}°C'
                    self._days_lbl[i].configure(text=msg)
            except Exception:
                logging.error(traceback.format_exc())
                # update days labels to 'n/a' error message
                for i in range(4):
                    self._days_lbl[i].configure(text='error')
        else:
            # update days labels to 'n/a' error message
            for i in range(4):
                self._days_lbl[i].configure(text='n/a')
