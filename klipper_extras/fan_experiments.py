
# klippy/extras/fan_experiments.py
#
# A simple Klipper extra to briefly turn the fan on and then off.

import logging

class FanExperiments:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.fan = self.printer.lookup_object('fan')  # This is PrinterFan
        # Register G-code command
        self.gcode.register_command('FAN_EXPERIMENT', self.cmd_FAN_EXPERIMENT,
                                    desc="Turn fan on for 2s then off")

    def cmd_FAN_EXPERIMENT(self, gcmd):
        logging.info("Running fan experiment: ON for 2s then OFF")
        # Access inner Fan object
        self.fan.fan.set_speed(1.0)  # Full speed
        # Schedule turning OFF after 2 seconds
        self.reactor.register_callback(self.turn_off_fan,
                                       self.reactor.monotonic() + 2.0)
        gcmd.respond_info("Fan turned ON for 2 seconds")

    def turn_off_fan(self, eventtime):
        logging.info("Turning fan OFF")
        self.fan.fan.set_speed(0.0)

def load_config(config):
    return FanExperiments(config)
