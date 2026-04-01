
# fan_experiments_async.py
#
# A Klipper extra to set fan speed instantly using async requests.

import logging

class FanExperimentsAsync:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.fan = self.printer.lookup_object('fan')  # PrinterFan object
        # Register G-code command
        self.gcode.register_command('LIVE_FAN', self.cmd_LIVE_FAN,
                                    desc="Set fan speed instantly using async request")

    def cmd_LIVE_FAN(self, gcmd):
        speed = gcmd.get_float('S', 0.0, minval=0.0, maxval=1.0)
        logging.info(f"Setting fan speed to {speed} (async)")
        # Instant fan update without blocking motion queue
        self.fan.fan.gcrq.send_async_request(speed)
        gcmd.respond_info(f"Fan speed set to {speed}")

def load_config(config):
    return FanExperimentsAsync(config)
