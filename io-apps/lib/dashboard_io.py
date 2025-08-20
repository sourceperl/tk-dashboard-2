#!/usr/bin/env python3

import functools
import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Set, Tuple, Type, Union

import pdf2image
import PIL.Image
import PIL.ImageDraw
from lib.sftp import SftpFileIndex

import redis

logger = logging.getLogger(__name__)


# some function
def catch_log_except(catch: Union[Type[Exception], Tuple[Type[Exception]]] = None,
                     log_lvl: int = logging.ERROR, limit_arg_len: int = 40):
    """
    A decorator factory to catch exceptions in a wrapped function and log them.

    This decorator allows you to gracefully handle specific exceptions (or all
    exceptions by default) that occur within a decorated function. Instead of
    letting the exception propagate, it catches the exception, logs a concise
    message, and then typically suppresses the re-raising of the exception (the
    decorated function will effectively return `None` or whatever its normal
    return value would be if an exception occurred, but the exception itself
    is not re-raised by the decorator).

    The logged message includes information about the exception type, the function
    call (with arguments and keyword arguments truncated to prevent overly long
    log lines), and the exception message itself.

    Args:
        catch (Union[Type[Exception], Tuple[Type[Exception], ...]], optional):
            The type of exception(s) to catch. If `None` (default), it will catch
            all `Exception` types. Can be a single exception class or a tuple of
            exception classes.
        log_lvl (int, optional): The logging level at which to log the caught
            exception. Defaults to `logging.ERROR`.
        limit_arg_len (int, optional): The maximum length for the string
            representation of individual arguments and keyword argument values
            in the logged function call string. Longer representations will be
            truncated. Defaults to 40 characters.

    Returns:
        Callable: The actual decorator function, which takes a function as
        input and returns the wrapped function.

    Example:
        ```python
        @catch_log_except(catch=ValueError, log_lvl=logging.WARNING)
        def process_data(data_str: str, debug_mode: bool = False):
            if not data_str:
                raise ValueError("Data cannot be empty")
            if debug_mode:
                print("Debugging mode active")
            return f"Processed: {data_str}"

        process_data("") # This will log a WARNING but not raise an error
        ```
    """
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
                logger.log(log_lvl, f'except {type(e)} in {func_call}: {e}')

        return wrapper

    return _catch_log_except


def to_png_thumbnail(filename: str, raw_data: bytes, size: Tuple[int, int] = (655, 453)) -> bytes:
    """
    Converts raw image/PDF data to a resized PNG thumbnail.

    Args:
        filename (str): The original filename, used to determine the file type and for error logging.
        raw_data (bytes): The raw byte data of the image or PDF.
        size (Tuple[int, int], optional): The target size (width, height) for the thumbnail.
                                          Defaults to (655, 453).

    Returns:
        bytes: The raw bytes of the generated PNG thumbnail.
    """
    img_to_redis = PIL.Image.new('RGB', size, color=(255, 255, 255))
    draw = PIL.ImageDraw.Draw(img_to_redis)
    draw.text((0, 0), f'loading error (src: "{filename}")', (0, 0, 0))

    try:
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            img_to_redis = PIL.Image.open(io.BytesIO(raw_data))
        elif filename.lower().endswith('.pdf'):
            # Convert only the first page of the PDF
            img_to_redis = pdf2image.convert_from_bytes(raw_data, first_page=1, last_page=1)[0]
    except Exception as e:
        logger.warning(f'failed to convert "{filename}" (error: {e})')
        # default error image already set

    # resize using thumbnail (maintains aspect ratio)
    img_to_redis.thumbnail(size)

    # get process result as raw bytes
    io_to_redis = io.BytesIO()
    img_to_redis.save(io_to_redis, format='PNG')
    return io_to_redis.getvalue()


def wait_uptime(min_s: float):
    """Waits until the system's uptime exceeds a specified minimum duration.

    Note: This function is specific to Linux/Unix-like operating systems that
    expose uptime information via `/proc/uptime`. It will not work on Windows
    or other systems without this file.

    Args:
        min_s (float): The minimum uptime duration in seconds that must be
                       reached before the function returns.
    """
    while True:
        uptime = float(open('/proc/uptime', 'r').readline().split()[0])
        if uptime > min_s:
            break
        time.sleep(0.1)


# def byte_xor(data_1: bytes, data_2: bytes) -> bytes:
#     return bytes([a ^ b for a, b in zip(data_1, data_2)])


# def data_encode(data: bytes, key: bytes) -> bytes:
#     # compress data
#     data_zip = zlib.compress(data)
#     # generate a random token
#     rand_token = secrets.token_bytes(128)
#     # xor the random token and the private key
#     key_mask = key * math.ceil(len(rand_token) / len(key))
#     token_part = byte_xor(rand_token, key_mask)
#     # xor data and token
#     token_mask = rand_token * math.ceil(len(data_zip) / len(rand_token))
#     data_part = byte_xor(data_zip, token_mask)
#     # concatenate xor random token and xor data
#     bin_msg = token_part + data_part
#     # encode binary message with base64
#     return base64.b64encode(bin_msg)


# def data_decode(data: bytes, key: bytes) -> bytes:
#     # decode base64 msg
#     bin_msg = base64.b64decode(data)
#     # split message: [xor_token part : xor_data part]
#     token_part = bin_msg[:128]
#     data_part = bin_msg[128:]
#     # token = xor_token xor private key
#     key_mask = key * math.ceil(len(token_part) / len(key))
#     token = byte_xor(token_part, key_mask)
#     # compressed data = xor_data xor token
#     token_mask = token * math.ceil(len(data_part) / len(token))
#     c_data = byte_xor(data_part, token_mask)
#     # return decompress data
#     return zlib.decompress(c_data)


# some class
@dataclass
class FileInfos:
    """Represents essential information for file integrity and identification.

    Attributes:
        sha256 (str): The SHA256 checksum of the file's content, typically
                      represented as a hexadecimal string. Used for integrity
                      verification.
        size (int): The size of the file in bytes.
    """
    sha256: str
    size: int


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


class RedisFile:
    def __init__(self, redis: CustomRedis, infos_key: str, raw_key: str, check: bool = True):
        # args
        self.redis = redis
        self.infos_key = infos_key
        self.raw_key = raw_key
        # integrity check at initialization
        if check:
            self.remove_orphan()

    def add_file(self, filename: str, raw_data: bytes, as_png_thumb: bool = False):
        """
        Calculates SHA256, optionaly converts image, and atomically stores carousel info and raw PNG.
        """
        sha256 = hashlib.sha256(raw_data).hexdigest()
        js_infos = json.dumps(dict(size=len(raw_data), sha256=sha256))

        if as_png_thumb:
            raw_data = to_png_thumbnail(filename, raw_data)

        pipe = self.redis.pipeline()
        pipe.hset(self.infos_key, mapping={filename: js_infos})
        pipe.hset(self.raw_key, mapping={filename: raw_data})
        pipe.execute()
        logger.debug(f'add "{filename}" to redis')

    def delete_file(self, filename: str):
        """
        Calculates SHA256, optionaly converts image, and atomically stores carousel info and raw PNG.
        """
        pipe = self.redis.pipeline()
        pipe.hdel(self.infos_key, filename)
        pipe.hdel(self.raw_key, filename)
        pipe.execute()
        logger.debug(f'delete "{filename}" from redis')

    def get_file_infos_as_dict(self) -> Dict[str, FileInfos]:
        """
        Loads file informations from Redis.
        """
        local_files: Dict[str, FileInfos] = {}
        redis_infos: Dict[bytes, bytes] = self.redis.hgetall(self.infos_key)  # type: ignore

        for filename_bytes, json_bytes in redis_infos.items():
            try:
                # decode filename from bytes to string
                filename = filename_bytes.decode('utf-8')
                js_data = json.loads(json_bytes.decode('utf-8'))

                # basic validation
                if 'sha256' in js_data and 'size' in js_data and isinstance(js_data['size'], int):
                    local_files[filename] = FileInfos(sha256=js_data['sha256'], size=js_data['size'])
                else:
                    logger.warning(f"skipping malformed info record for '{filename}' (data: {js_data})")
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as e:
                msg = f"failed to parse info record for \"{filename_bytes.decode('utf-8', errors='ignore')}\". Error: {e}"
                logger.warning(msg)
        return local_files

    def remove_orphan(self):
        """
        Cleans up inconsistent (orphan) file records in Redis HASHes.

        An 'orphan' record is defined as a filename that exists as a field in
        either the 'infos' HASH (`self.infos_key`) or the 'raw' HASH
        (`self.raw_key`), but not in both. This method identifies such
        inconsistencies and removes the orphaned entries to maintain data integrity
        and prevent stale or broken references.
        """
        # list set of files in raw and infos keys
        raw_keys_set = {f.decode('utf-8') for f in self.redis.hkeys(self.raw_key)}
        redis_info_set = {f.decode('utf-8') for f in self.redis.hkeys(self.infos_key)}
        # files in infos hash but not in raw hash
        infos_only = redis_info_set - raw_keys_set
        for filename in infos_only:
            logger.warning(f'removing orphan "{filename}" file record in "{self.infos_key}"')
            self.redis.hdel(self.infos_key, filename)
            # remove from our local dict as well
            redis_info_set.remove(filename)
        # files in raw hash but not in infos hash
        raw_only = raw_keys_set - redis_info_set
        for filename in raw_only:
            logger.warning(f'removing orphan "{filename}" file record in "{self.raw_key}"')
            self.redis.hdel(self.raw_key, filename)

    def sync_with_sftp(self, sftp_index: SftpFileIndex, to_sync_d: Dict[str, str], to_png_thumb: bool = False):
        """Synchronizes local Redis-stored files with a remote SFTP index.

        Args:
            sftp_index (SftpFileIndex): An object providing access to SFTP file 
                index operations.
            to_sync_d (Dict[str, str]): A dictionary representing the current
                state of relevant remote files on SFTP. Keys are filenames (str),
                and values are their SHA256 checksums (str). This acts as the
                source of truth for remote file information during the sync.
            to_png_thumb (bool, optional): If `True`, downloaded raw file data
                will be converted to a PNG thumbnail before being stored/updated
                in Redis. If `False`, the raw data will be stored as is.
                Defaults to `False`
        """
        # get remote and local files
        local_file_infos_d = self.get_file_infos_as_dict()
        # sync actions
        local_filenames_set: Set[str] = set(local_file_infos_d.keys())
        remote_filenames_set: Set[str] = set(to_sync_d.keys())
        # files to remove from local (exist only on local)
        to_remove = local_filenames_set - remote_filenames_set
        for filename in to_remove:
            logger.info(f'"{filename}" exists only locally -> removing from redis')
            self.delete_file(filename)
        # files to download (exist only on remote or hash mismatch)
        to_download_set = remote_filenames_set - local_filenames_set
        # check for files existing on both sides but with differing SHA256
        for filename in local_filenames_set.intersection(remote_filenames_set):
            local_sha256 = local_file_infos_d[filename].sha256
            remote_sha256 = to_sync_d[filename]
            msg = f'checking "{filename}" remote SHA256 [{remote_sha256[:7]}] vs. local SHA256 [{local_sha256[:7]}]'
            logger.debug(msg)
            if local_sha256 != remote_sha256:
                logger.info(f'"{filename}" SHA256 mismatch -> adding to download list')
                to_download_set.add(filename)
        # process downloads
        for filename in to_download_set:
            logger.info(f'downloading and processing "{filename}" from SFTP')
            raw_data = sftp_index.get_file_as_bytes(filename)
            # ensure download was successful
            if raw_data:
                self.add_file(filename, raw_data, as_png_thumb=to_png_thumb)
            else:
                logger.warning(f'failed to download raw data for "{filename}"')


class TrySync:
    """Manages conditional synchronization with an SFTP directory based on index changes.

    This class tracks the last known modification time of an SFTP directory's index.
    It provides a `run` method that, when called, checks if the SFTP directory
    has been updated since the last check. If changes are detected, a provided
    synchronization function is executed.
    """

    def __init__(self, sftp_dir: str) -> None:
        """Initializes the TrySync instance.

        Args:
            sftp_dir (str): The path to the SFTP directory to monitor for changes.
        """
        # args
        self.sftp_dir = sftp_dir
        # private
        self._last_sync_dt = datetime(year=2000, month=1, day=1, tzinfo=timezone.utc)

    def run(self, sftp_index: SftpFileIndex, on_sync_func: Callable):
        """Executes a synchronization function if the SFTP directory's index has changed.

        Args:
            sftp_index (SftpFileIndex): An instance of `SftpFileIndex` connected to the SFTP server.
                This object is used to access the SFTP directory's index and is
                passed to the `on_sync_func` if a sync is triggered.
            on_sync_func (Callable[[SftpFileIndex], Any]): A callable (function or method)
                that will be executed if a change in the SFTP directory's index
                is detected. This callable should accept one argument: the
                `SftpFileIndex` instance.
                Example signature: `def my_sync_function(sftp_index: SftpFileIndex) -> None: ...`
        """
        sftp_index.base_dir = self.sftp_dir
        idx_attrs = sftp_index.index_attributes()
        logger.debug(f'"{self.sftp_dir}" index size: {idx_attrs.size} bytes last update: {idx_attrs.mtime_dt}')
        if idx_attrs.mtime_dt > self._last_sync_dt:
            logger.info(f'index of "{self.sftp_dir}" change: run an SFTP sync')
            on_sync_func(sftp_index)
            self._last_sync_dt = idx_attrs.mtime_dt
        else:
            logger.debug(f'no change occur, skip sync')
