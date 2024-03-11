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
    def __init__(self):
        # public
        self.raw = b''

    def __str__(self) -> str:
        return hexlify(self.raw, sep='-').decode()

    def __repr__(self) -> str:
        return self.__str__()

    def create(self, frame: bytes) -> "IiyamaFrame":
        self.raw = frame + bytes([self.get_csum(frame)])
        return self

    def load(self, frame: bytes) -> "IiyamaFrame":
        self.raw = frame
        return self

    @staticmethod
    def get_csum(data: bytes) -> int:
        csum = 0
        for b in data:
            csum ^= b
        return csum

    @property
    def is_valid(self):
        try:
            return self.get_csum(self.raw[:-1]) == self.csum
        except IndexError:
            return False

    @property
    def is_command(self):
        return self.raw[0] == 0xa6

    @property
    def header(self):
        return self.raw[0]

    @property
    def monitor_id(self):
        return self.raw[1]

    @property
    def category(self):
        try:
            return self.raw[2]
        except IndexError:
            return

    @property
    def code_0(self):
        try:
            return self.raw[3]
        except IndexError:
            return

    @property
    def code_1(self):
        try:
            if self.is_command:
                return self.raw[4]
            else:
                return
        except IndexError:
            return

    @property
    def length(self):
        try:
            if self.is_command:
                return self.raw[5]
            else:
                return self.raw[4]
        except IndexError:
            return

    @property
    def data_control(self):
        try:
            if self.is_command:
                return self.raw[6]
            else:
                return self.raw[5]
        except IndexError:
            return

    @property
    def data_body(self):
        try:
            if self.is_command:
                return self.raw[7:7+self.length-2]
            else:
                return self.raw[6:6+self.length-2]
        except IndexError:
            return

    @property
    def csum(self) -> int:
        return self.raw[-1]


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
        serial_port.write(frame.raw)
        # wait response (return when timeout occur)
        return IiyamaFrame().load(serial_port.read(255))


@catch_log_except()
def screen_op_hours_job():
    logging.info(f'request screen operation time in hours')
    tx_frame = IiyamaFrame().create(b'\xa6\x01\x00\x00\x00\x04\x01\x0F\x02')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.debug(f'read success (return: "{rx_frame}")')
        try:
            op_hours = int.from_bytes(rx_frame.data_body[1:], byteorder='big')
            logging.info(f'screen operating hours = {op_hours}h')
        except TypeError:
            logging.warning(f'unable to decode op hours part in rx frame :"{rx_frame}"')
    else:
        logging.debug('error of checksum in receive frame')


@catch_log_except()
def screen_turn_on_job():
    logging.info(f'request to power on screen')
    tx_frame = IiyamaFrame().create(b'\xa6\x01\x00\x00\x00\x04\x01\x18\x02')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.info('success')
    else:
        logging.info('error of checksum in receive frame')


@catch_log_except()
def screen_turn_off_job():
    logging.info(f'request to power off screen')
    tx_frame = IiyamaFrame().create(b'\xa6\x01\x00\x00\x00\x04\x01\x18\x01')
    rx_frame = serial_port.iiyama_request(tx_frame)
    if rx_frame.is_valid:
        logging.info('success')
    else:
        logging.info('error of checksum in receive frame')


# main
if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('-p', '--parity', type=str, default='N', help='serial parity (default is "N")')
    parser.add_argument('-s', '--stop', type=float, default=1, help='serial stop bits (default is 1)')
    parser.add_argument('-t', '--timeout', type=float, default=0.5, help='timeout delay (default is 0.5 s)')
    parser.add_argument('--turn-on', action='store_true', help='turn on the screen immediately and exit')
    parser.add_argument('--turn-off', action='store_true', help='turn off the screen immediately and exit')
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

        # immediate tasks
        if args.turn_on:
            screen_turn_on_job()
            exit(0)
        if args.turn_off:
            screen_turn_off_job()
            exit(0)

        # init scheduler
        schedule.every().hours.do(screen_op_hours_job)
        schedule.every().monday.at('07:00').do(screen_turn_on_job)
        schedule.every().monday.at('19:00').do(screen_turn_off_job)
        schedule.every().tuesday.at('07:00').do(screen_turn_on_job)
        schedule.every().tuesday.at('19:00').do(screen_turn_off_job)
        schedule.every().wednesday.at('07:00').do(screen_turn_on_job)
        schedule.every().wednesday.at('19:00').do(screen_turn_off_job)
        schedule.every().thursday.at('07:00').do(screen_turn_on_job)
        schedule.every().thursday.at('19:00').do(screen_turn_off_job)
        schedule.every().friday.at('07:00').do(screen_turn_on_job)
        schedule.every().friday.at('19:00').do(screen_turn_off_job)

        # startup call
        screen_turn_on_job()
        screen_op_hours_job()

        # main loop
        while True:
            schedule.run_pending()
            time.sleep(1)
    except serialutil.SerialException as e:
        logging.critical('serial device error: %r', e)
        exit(1)
