import functools
import json
import logging
from typing import Any

import redis

# a logger for this script
logger = logging.getLogger(__name__)


def catch_log_except(catch=None, log_lvl=logging.ERROR, limit_arg_len=40):
    """a decorator to catch exception and produce one line log message"""
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
                logger.log(log_lvl, f'except {type(e)} in {func_call}: {e}')

        return wrapper

    return _catch_log_except


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
