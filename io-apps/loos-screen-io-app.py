#!/opt/tk-dashboard/io-apps/venv/bin/python

import argparse
from binascii import hexlify
import time
import logging
from serial import Serial, serialutil
import schedule
from lib.dashboard_io import catch_log_except, wait_uptime


# some class
class IiyamaFrame:
    def __init__(self, raw: bytes = b''):
        # public
        self.raw = raw

    def __str__(self) -> str:
        return hexlify(self.raw, sep='-').decode()

    @property
    def with_csum(self) -> bytes:
        csum = 0
        for b in self.raw[:-1]:
            csum ^= b
        return self.raw + bytes([csum])

    @property
    def without_csum(self) -> bytes:
        return self.raw[:-1]

    @property
    def is_valid(self):
        try:
            csum = 0
            for b in self.without_csum:
                csum ^= b
            return csum == self.raw[-1]
        except IndexError:
            return False

    @property
    def header_id(self):
        return self.raw[0]

    @property
    def monitor_id(self):
        return self.raw[1]

    @property
    def category(self):
        return self.raw[2]

    @property
    def code_0(self):
        return self.raw[3]

    @property
    def code_1(self):
        return self.raw[4]

    @property
    def length(self):
        return self.raw[5]

    @property
    def data_control(self):
        return self.raw[6]

    @property
    def data_body(self):
        data_body_len = self.length - 3
        return self.raw[7:7+data_body_len]


class CustomSerial(Serial):
    def read(self, size: int = 1) -> bytes:
        r_value = super().read(size)
        r_dump = hexlify(r_value, sep='-').decode().upper()
        logging.debug(f'dump app <- serial: "{r_dump}"')
        return r_value

    def write(self, data: bytes) -> int | None:
        w_dump = hexlify(data, sep='-').decode().upper()
        logging.debug(f'dump app -> serial: "{w_dump}"')
        return super().write(data)

    def iiyama_request(self, frame: IiyamaFrame) -> IiyamaFrame:
        # init a new request
        self.reset_input_buffer()
        # send frame request
        serial_port.write(frame.with_csum)
        # wait response (return when timeout occur)
        return IiyamaFrame(serial_port.read(255))


@catch_log_except()
def screen_op_hours_job():
    logging.debug(f'request screen operation time in hours')
    tx_frame = IiyamaFrame(b'\xa6\x01\x00\x00\x00\x04\x01\x0F\x02')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.debug(f'read success (return: "{rx_frame}")')
        op_hours = int.from_bytes(rx_frame.data_body[1:3])
        logging.info(f'screen operating hours = {op_hours}h')
    else:
        logging.debug('error of checksum in receive frame')


@catch_log_except()
def screen_turn_on_job():
    logging.debug(f'request to set screen power on')
    tx_frame = IiyamaFrame(b'\xa6\x01\x00\x00\x00\x04\x01\x18\x02')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.debug(f'set success (return: "{rx_frame}")')
    else:
        logging.debug('error of checksum in receive frame')


@catch_log_except()
def screen_turn_off_job():
    logging.debug(f'request to set screen power off')
    tx_frame = IiyamaFrame(b'\xa6\x01\x00\x00\x00\x04\x01\x18\x01')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.debug(f'set success (return: "{rx_frame}")')
    else:
        logging.debug('error of checksum in receive frame')


# main
if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('-p', '--parity', type=str, default='N', help='serial parity (default is "N")')
    parser.add_argument('-s', '--stop', type=float, default=1, help='serial stop bits (default is 1)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='timeout delay (default is 1.0 s)')
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    args = parser.parse_args()
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG if args.debug else logging.INFO)
    logging.info('board-screen-app started')

    # wait system ready (uptime > 25s)
    wait_uptime(min_s=25.0)

    try:
        # init serial port
        logging.info('open serial port %s at %d,%s,%d,%d', args.device, args.baudrate, args.parity, 8, args.stop)
        serial_port = CustomSerial(port=args.device, baudrate=args.baudrate, parity=args.parity, bytesize=8,
                                   stopbits=args.stop, timeout=args.timeout)

        # init scheduler
        schedule.every().hours.do(screen_op_hours_job)
        schedule.every().monday.at('06:00').do(screen_turn_on_job)
        schedule.every().monday.at('19:00').do(screen_turn_off_job)
        schedule.every().tuesday.at('06:00').do(screen_turn_on_job)
        schedule.every().tuesday.at('19:00').do(screen_turn_off_job)
        schedule.every().wednesday.at('06:00').do(screen_turn_on_job)
        schedule.every().wednesday.at('19:00').do(screen_turn_off_job)
        schedule.every().thursday.at('06:00').do(screen_turn_on_job)
        schedule.every().thursday.at('19:00').do(screen_turn_off_job)
        schedule.every().friday.at('06:00').do(screen_turn_on_job)
        schedule.every().friday.at('19:00').do(screen_turn_off_job)

        # first call
        screen_op_hours_job()

        # main loop
        while True:
            schedule.run_pending()
            time.sleep(1)
    except serialutil.SerialException as e:
        logging.critical('serial device error: %r', e)
        exit(1)
