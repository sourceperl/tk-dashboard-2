#!/usr/bin/env python3

import base64
from datetime import datetime
import functools
import json
import logging
import math
import secrets
import time
from typing import Any
import zlib
import redis


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


def dt_utc_to_local(utc_dt):
    now_ts = time.time()
    offset = datetime.fromtimestamp(now_ts) - datetime.utcfromtimestamp(now_ts)
    return utc_dt + offset


def wait_uptime(min_s: float):
    while True:
        uptime = float(open('/proc/uptime', 'r').readline().split()[0])
        if uptime > min_s:
            break
        time.sleep(0.1)


def byte_xor(data_1: bytes, data_2: bytes) -> bytes:
    return bytes([a ^ b for a, b in zip(data_1, data_2)])


def data_encode(data: bytes, key: bytes) -> bytes:
    # compress data
    data_zip = zlib.compress(data)
    # generate a random token
    rand_token = secrets.token_bytes(128)
    # xor the random token and the private key
    key_mask = key * math.ceil(len(rand_token) / len(key))
    token_part = byte_xor(rand_token, key_mask)
    # xor data and token
    token_mask = rand_token * math.ceil(len(data_zip) / len(rand_token))
    data_part = byte_xor(data_zip, token_mask)
    # concatenate xor random token and xor data
    bin_msg = token_part + data_part
    # encode binary message with base64
    return base64.b64encode(bin_msg)


def data_decode(data: bytes, key: bytes) -> bytes:
    # decode base64 msg
    bin_msg = base64.b64decode(data)
    # split message: [xor_token part : xor_data part]
    token_part = bin_msg[:128]
    data_part = bin_msg[128:]
    # token = xor_token xor private key
    key_mask = key * math.ceil(len(token_part) / len(key))
    token = byte_xor(token_part, key_mask)
    # compressed data = xor_data xor token
    token_mask = token * math.ceil(len(data_part) / len(token))
    c_data = byte_xor(data_part, token_mask)
    # return decompress data
    return zlib.decompress(c_data)


# some class
class CustomRedis(redis.Redis):
    LOG_LEVEL = logging.ERROR

    @catch_log_except(catch=redis.RedisError, log_lvl=LOG_LEVEL)
    def execute_command(self, *args, **options):
        return super().execute_command(*args, **options)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def set_as_json(self, name: str, obj: Any, ex=None, px=None, nx=False, xx=False, keepttl=False):
        return super().set(name=name, value=json.dumps(obj), ex=ex, px=px, nx=nx, xx=xx, keepttl=keepttl)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def get_from_json(self, name: str):
        js_as_bytes = super().get(name)
        if js_as_bytes is None:
            return
        else:
            return json.loads(js_as_bytes.decode('utf-8'))
