#!/usr/bin/python3
import sys
import os
import signal
import time
import logging
import re

sys.path.append(os.path.abspath('..'))
import vxi11_server as Vxi11
import ctypes as ctypes
from gpib_ctypes import gpib  # typing: ignore
from gpib_ctypes.Gpib import Gpib  # typing: ignore
from gpib_ctypes.gpib.gpib import _lib as gpib_lib  # typing: ignore

# Add some extra binding not available by default
extra_funcs = [
    ("ibcac", [ctypes.c_int, ctypes.c_int], ctypes.c_int),
    ("ibgts", [ctypes.c_int, ctypes.c_int], ctypes.c_int),
    ("ibpct", [ctypes.c_int], ctypes.c_int),
]
for name, argtypes, restype in extra_funcs:
    libfunction = gpib_lib[name]
    libfunction.argtypes = argtypes
    libfunction.restype = restype

def signal_handler(signal, frame):
    logger.info('Handling Ctrl+C!')
    instr_server.close()
    sys.exit(0)

class ProxyDevice(Vxi11.InstrumentDevice):
    def device_init(self):
        addr = re.match('gpib(\d+),(\d+)', self.name())
        board = addr.group(1)
        unit = addr.group(2)
        self.inst = gpib.dev(int(board), int(unit), 0, 14, 1, 0x40a)
        return

    def device_write(self, opaque_data, flags, io_timeout):
        error = Vxi11.Error.NO_ERROR
        reason = Vxi11.ReadRespReason.END
        try:
            sta = gpib.write(self.inst, opaque_data)
            logger.debug(f'sta={sta:x}')
        except: 
            error = 17
        return error

    def device_read(self, request_size, term_char, flags, io_timeout):
        '''respond to the device_read rpc: refer to section (B.6.4) 
        of the VXI-11 TCP/IP Instrument Protocol Specification''' 
        error = Vxi11.Error.NO_ERROR
        reason = Vxi11.ReadRespReason.END
        # opaque_data is a bytes array, so encode correctly!
        try:
            opaque_data = gpib.read(self.inst, 1000)
        except:
            opaque_data = b''
            error = 17
        logger.debug("read [%s]", opaque_data)
        return error, reason, opaque_data

    def device_clear(self, flags, io_timeout):
        error = Vxi11.Error.NO_ERROR
        reason = Vxi11.ReadRespReason.END
        try:
            gpib.clear(self.inst)
        except:
            error = 17
        return error

class PrimaryDevice(Vxi11.InstrumentDevice):
    def get_line_state(self, mask):
         if gpib.lines(0) & mask:
             return b"\x01\x00"
         else:
             return b"\x00\x00"

    def handle_bus_status(self, cmd):
        if cmd == 1: # REMOTE
            return self.get_line_state(0x1000)
#       elif cmd == 2: # SRQ
        elif cmd == 3: # NDAC
            return self.get_line_state(0x200)
#       elif cmd == 4: # SYSTEM CONTROLLER
#       elif cmd == 5: # CIC
#       elif cmd == 6: # TALKER
#       elif cmd == 7: # LISTENER
        elif cmd == 8: # BUSADDR
            return b"\x00\x00"
        else:
            logger.info("unimplemented bus status %d", cmd)
            return b'\x00\x00'

    def device_docmd(self, flags, io_timeout, cmd, network_order, data_size, opaque_data_in):
        opaque_data_out = b''
        error = Vxi11.vxi11.ERR_NO_ERROR
        if cmd == Vxi11.vxi11.CMD_SEND_COMMAND:
            logger.debug("CMD_SEND_COMMAND %d [%s]", data_size, " ".join(hex(n) for n in opaque_data_in))
            gpib.command(0, opaque_data_in)
            opaque_data_out = opaque_data_in
        elif cmd == Vxi11.vxi11.CMD_BUS_STATUS:
            logger.debug(f'CMD_BUS_STATUS {opaque_data_in}')
            opaque_data_out = self.handle_bus_status(opaque_data_in[0])
        elif cmd == Vxi11.vxi11.CMD_ATN_CTRL:
            logger.debug(f'CMD_ATN_CTRL {opaque_data_in}')
            if opaque_data_in[0] == 0:
                gpib_lib.ibgts(0)
            else:
                gpib_lib.ibcac(0)
            opaque_data_out = opaque_data_in
        elif cmd == Vxi11.vxi11.CMD_REN_CTRL:
            logger.debug(f'CMD_REN_CTRL {opaque_data_in}')
            if opaque_data_in[0] == 0:
                gpib.remote_enable(0, 0)
            else:
                gpib.remote_enable(0, 1)
            opaque_data_out = opaque_data_in
        else:
            logger.info("unimplemented cmd %x", cmd)
            error = Vxi11.ERR_OPERATION_NOT_SUPPORTED
        return error, opaque_data_out

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    signal.signal(signal.SIGINT, signal_handler)
    print('Press Ctrl+C to exit')
    logger.info('starting vxiserver')

    # create a server, attach a device, and start a thread to listen for requests
    instr_server = Vxi11.InstrumentServer('gpib0', PrimaryDevice)

    for i in range(31):
        instr_server.add_device_handler(ProxyDevice, f'gpib0,{i}')
    instr_server.listen()

    # sleep (or do foreground work) while the Instrument threads do their job
    while True:
        time.sleep(1)
