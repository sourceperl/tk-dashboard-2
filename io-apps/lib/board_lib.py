#!/usr/bin/env python3

import base64
from datetime import datetime
import functools
import json
import logging
import math
import secrets
import time
import zlib
import redis


# some function
def byte_xor(bytes_1, bytes_2):
    return bytes([_a ^ _b for _a, _b in zip(bytes_1, bytes_2)])


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


def dweet_encode(bytes_data: bytes, dweet_key: str):
    # compress data
    c_data = zlib.compress(bytes_data)
    # generate a random token
    token = secrets.token_bytes(64)
    # xor random token and private key
    key = dweet_key.encode('utf8')
    key_mask = key * math.ceil(len(token) / len(key))
    xor_token = byte_xor(token, key_mask)
    # xor data and token
    token_mask = token * math.ceil(len(c_data) / len(token))
    xor_data = byte_xor(c_data, token_mask)
    # concatenate xor random token and xor data
    msg_block = xor_token + xor_data
    # encode binary data with base64
    return base64.b64encode(msg_block)


def dweet_decode(b64_msg_block: bytes, dweet_key: str):
    # decode base64 msg
    msg_block = base64.b64decode(b64_msg_block)
    # split message: [xor_token part : xor_data part]
    xor_token = msg_block[:64]
    xor_data = msg_block[64:]
    # token = xor_token xor private key
    key = dweet_key.encode('utf8')
    key_mask = key * math.ceil(len(xor_token) / len(key))
    token = byte_xor(xor_token, key_mask)
    # compressed data = xor_data xor token
    token_mask = token * math.ceil(len(xor_data) / len(token))
    c_data = byte_xor(xor_data, token_mask)
    # return decompress data
    return zlib.decompress(c_data)


# some class
class CustomRedis(redis.Redis):
    LOG_LEVEL = logging.ERROR

    @catch_log_except(catch=redis.RedisError, log_lvl=LOG_LEVEL)
    def execute_command(self, *args, **options):
        return super().execute_command(*args, **options)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def set_as_json(self, name, obj, ex=None, px=None, nx=False, xx=False, keepttl=False):
        return super().set(name=name, value=json.dumps(obj), ex=ex, px=px, nx=nx, xx=xx, keepttl=keepttl)

    @catch_log_except(catch=(redis.RedisError, AttributeError, json.decoder.JSONDecodeError), log_lvl=LOG_LEVEL)
    def get_from_json(self, name):
        return json.loads(super().get(name).decode('utf-8'))
