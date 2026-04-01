 
# bltouch_experiments.py
#
# Minimal experimental module for Klipper to test BLTouch pin movement.
# Usage: Add [bltouch_experiments] to printer.cfg and run BLTOUCH_EXPERIMENT.

class BLTouchExperiments:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        # Get existing BLTouch object (registered as 'probe')
        self.bltouch = self.printer.lookup_object('probe')
        # Register G-code command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('BLTOUCH_EXPERIMENT', self.cmd_experiment,
                               desc="Deploy BLTouch pin, wait, then stow")

    def cmd_experiment(self, gcmd):
        gcmd.respond_info("BLTouch experiment: pin_down → wait → pin_up")
        # Deploy pin
        self.bltouch.send_cmd('pin_down', duration=self.bltouch.pin_move_time)
        # Schedule stow after 2 seconds using reactor
        self.reactor.register_callback(self._stow_probe,
                                       self.reactor.monotonic() + 2.0)

    def _stow_probe(self, eventtime):
        self.bltouch.send_cmd('pin_up', duration=self.bltouch.pin_move_time)
        gcode = self.printer.lookup_object('gcode')
        gcode.respond_info("BLTouch experiment complete")
        return self.reactor.NEVER  # No repeat

def load_config(config):
    return BLTouchExperiments(config)
