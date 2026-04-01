# Printer cooling fan
#
# Copyright (C) 2016-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import pulse_counter, output_pin

class Fan:
    def __init__(self, config, default_shutdown_speed=0.):
        self.printer = config.get_printer()
        self.last_fan_value = self.last_req_value = 0.
        # Read config
        self.max_power = config.getfloat('max_power', 1., above=0., maxval=1.)
        self.kick_start_time = config.getfloat('kick_start_time', 0.1,
                                               minval=0.)
        self.off_below = config.getfloat('off_below', default=0.,
                                         minval=0., maxval=1.)
        cycle_time = config.getfloat('cycle_time', 0.010, above=0.)
        hardware_pwm = config.getboolean('hardware_pwm', False)
        shutdown_speed = config.getfloat(
            'shutdown_speed', default_shutdown_speed, minval=0., maxval=1.)
        # Setup pwm object
        ppins = self.printer.lookup_object('pins')
        self.mcu_fan = ppins.setup_pin('pwm', config.get('pin'))
        self.mcu_fan.setup_max_duration(0.)
        self.mcu_fan.setup_cycle_time(cycle_time, hardware_pwm)
        shutdown_power = max(0., min(self.max_power, shutdown_speed))
        self.mcu_fan.setup_start_value(0., shutdown_power)

        self.enable_pin = None
        enable_pin = config.get('enable_pin', None)
        if enable_pin is not None:
            self.enable_pin = ppins.setup_pin('digital_out', enable_pin)
            self.enable_pin.setup_max_duration(0.)

        # Create gcode request queue
        self.gcrq = output_pin.GCodeRequestQueue(config, self.mcu_fan.get_mcu(),
                                                 self._apply_speed)

        # Setup tachometer
        self.tachometer = FanTachometer(config)

        # Register callbacks
        self.printer.register_event_handler("gcode:request_restart",
                                            self._handle_request_restart)

    def get_mcu(self):
        return self.mcu_fan.get_mcu()
    def _apply_speed(self, print_time, value):
        if value < self.off_below:
            value = 0.
        value = max(0., min(self.max_power, value * self.max_power))
        if value == self.last_fan_value:
            return "discard", 0.
        if self.enable_pin:
            if value > 0 and self.last_fan_value == 0:
                self.enable_pin.set_digital(print_time, 1)
            elif value == 0 and self.last_fan_value > 0:
                self.enable_pin.set_digital(print_time, 0)
        if (value and self.kick_start_time
            and (not self.last_fan_value or value - self.last_fan_value > .5)):
            # Run fan at full speed for specified kick_start_time
            self.last_req_value = value
            self.last_fan_value = self.max_power
            self.mcu_fan.set_pwm(print_time, self.max_power)
            return "delay", self.kick_start_time
        self.last_fan_value = self.last_req_value = value
        self.mcu_fan.set_pwm(print_time, value)
    def set_speed(self, value, print_time=None):
        self.gcrq.send_async_request(value, print_time)
    def set_speed_from_command(self, value):
        self.gcrq.queue_gcode_request(value)
    def _handle_request_restart(self, print_time):
        self.set_speed(0., print_time)

    def get_status(self, eventtime):
        tachometer_status = self.tachometer.get_status(eventtime)
        return {
            'speed': self.last_req_value,
            'rpm': tachometer_status['rpm'],
        }

class FanTachometer:
    def __init__(self, config):
        printer = config.get_printer()
        self._freq_counter = None

        pin = config.get('tachometer_pin', None)
        if pin is not None:
            self.ppr = config.getint('tachometer_ppr', 2, minval=1)
            poll_time = config.getfloat('tachometer_poll_interval',
                                        0.0015, above=0.)
            sample_time = 1.
            self._freq_counter = pulse_counter.FrequencyCounter(
                printer, pin, sample_time, poll_time)

    def get_status(self, eventtime):
        if self._freq_counter is not None:
            rpm = self._freq_counter.get_frequency() * 30. / self.ppr
        else:
            rpm = None
        return {'rpm': rpm}

class PrinterFan:
    def __init__(self, config):
        self.fan = Fan(config)
        # Register commands
        gcode = config.get_printer().lookup_object('gcode')
        gcode.register_command("M106", self.cmd_M106)
        gcode.register_command("M107", self.cmd_M107)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_M106(self, gcmd):
        # Set fan speed
        value = gcmd.get_float('S', 255., minval=0.) / 255.
        self.fan.set_speed_from_command(value)
    def cmd_M107(self, gcmd):
        # Turn fan off
        self.fan.set_speed_from_command(0.)

def load_config(config):
    return PrinterFan(config)

# BLTouch support
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import probe

SIGNAL_PERIOD = 0.020
MIN_CMD_TIME = 5 * SIGNAL_PERIOD

TEST_TIME = 5 * 60.
RETRY_RESET_TIME = 1.
ENDSTOP_REST_TIME = .001
ENDSTOP_SAMPLE_TIME = .000015
ENDSTOP_SAMPLE_COUNT = 4

Commands = {
    'pin_down': 0.000650, 'touch_mode': 0.001165,
    'pin_up': 0.001475, 'self_test': 0.001780, 'reset': 0.002190,
    'set_5V_output_mode' : 0.001988, 'set_OD_output_mode' : 0.002091,
    'output_mode_store' : 0.001884,
}

# BLTouch "endstop" wrapper
class BLTouchProbe:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.position_endstop = config.getfloat('z_offset', minval=0.)
        self.stow_on_each_sample = config.getboolean('stow_on_each_sample',
                                                     True)
        self.probe_touch_mode = config.getboolean('probe_with_touch_mode',
                                                  False)
        # Create a pwm object to handle the control pin
        ppins = self.printer.lookup_object('pins')
        self.mcu_pwm = ppins.setup_pin('pwm', config.get('control_pin'))
        self.mcu_pwm.setup_max_duration(0.)
        self.mcu_pwm.setup_cycle_time(SIGNAL_PERIOD)
        # Command timing
        self.next_cmd_time = self.action_end_time = 0.
        self.finish_home_complete = self.wait_trigger_complete = None
        # Create an "endstop" object to handle the sensor pin
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('sensor_pin'))
        # output mode
        omodes = ['5V', 'OD', None]
        self.output_mode = config.getchoice('set_output_mode', omodes, None)
        # Setup for sensor test
        self.next_test_time = 0.
        self.pin_up_not_triggered = config.getboolean(
            'pin_up_reports_not_triggered', True)
        self.pin_up_touch_triggered = config.getboolean(
            'pin_up_touch_mode_reports_triggered', True)
        # Calculate pin move time
        self.pin_move_time = config.getfloat('pin_move_time', 0.680, above=0.)
        # Wrappers
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_wait = self.mcu_endstop.home_wait
        self.query_endstop = self.mcu_endstop.query_endstop
        # multi probes state
        self.multi = 'OFF'
        # Common probe implementation helpers
        self.cmd_helper = probe.ProbeCommandHelper(
            config, self, self.mcu_endstop.query_endstop)
        self.probe_offsets = probe.ProbeOffsetsHelper(config)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.homing_helper = probe.HomingViaProbeHelper(config, self,
                                                        self.param_helper)
        self.probe_session = probe.ProbeSessionHelper(
            config, self.param_helper, self.homing_helper.start_probe_session)
        # Register BLTOUCH_DEBUG command
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command("BLTOUCH_DEBUG", self.cmd_BLTOUCH_DEBUG,
                                    desc=self.cmd_BLTOUCH_DEBUG_help)
        self.gcode.register_command("BLTOUCH_STORE", self.cmd_BLTOUCH_STORE,
                                    desc=self.cmd_BLTOUCH_STORE_help)
        # Register events
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)
    def get_offsets(self):
        return self.probe_offsets.get_offsets()
    def get_status(self, eventtime):
        return self.cmd_helper.get_status(eventtime)
    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)
    def handle_connect(self):
        self.sync_mcu_print_time()
        self.next_cmd_time += 0.200
        self.set_output_mode(self.output_mode)
        try:
            self.raise_probe()
            self.verify_raise_probe()
        except self.printer.command_error as e:
            logging.warning("BLTouch raise probe error: %s", str(e))
    def sync_mcu_print_time(self):
        curtime = self.printer.get_reactor().monotonic()
        est_time = self.mcu_pwm.get_mcu().estimated_print_time(curtime)
        self.next_cmd_time = max(self.next_cmd_time, est_time + MIN_CMD_TIME)
    def sync_print_time(self):
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        if self.next_cmd_time > print_time:
            toolhead.dwell(self.next_cmd_time - print_time)
        else:
            self.next_cmd_time = print_time
    def send_cmd(self, cmd, duration=MIN_CMD_TIME):
        # Translate duration to ticks to avoid any secondary mcu clock skew
        mcu = self.mcu_pwm.get_mcu()
        cmd_clock = mcu.print_time_to_clock(self.next_cmd_time)
        pulse = int((duration - MIN_CMD_TIME) / SIGNAL_PERIOD) * SIGNAL_PERIOD
        cmd_clock += mcu.seconds_to_clock(max(MIN_CMD_TIME, pulse))
        end_time = mcu.clock_to_print_time(cmd_clock)
        # Schedule command followed by PWM disable
        self.mcu_pwm.set_pwm(self.next_cmd_time, Commands[cmd] / SIGNAL_PERIOD)
        self.mcu_pwm.set_pwm(end_time, 0.)
        # Update time tracking
        self.action_end_time = self.next_cmd_time + duration
        self.next_cmd_time = max(self.action_end_time, end_time + MIN_CMD_TIME)
    def verify_state(self, triggered):
        # Perform endstop check to verify bltouch reports desired state
        self.mcu_endstop.home_start(self.action_end_time, ENDSTOP_SAMPLE_TIME,
                                    ENDSTOP_SAMPLE_COUNT, ENDSTOP_REST_TIME,
                                    triggered=triggered)
        try:
            trigger_time = self.mcu_endstop.home_wait(
                self.action_end_time + 0.100)
        except self.printer.command_error as e:
            return False
        return trigger_time > 0.
    def raise_probe(self):
        self.sync_mcu_print_time()
        if not self.pin_up_not_triggered:
            self.send_cmd('reset')
        self.send_cmd('pin_up', duration=self.pin_move_time)
    def verify_raise_probe(self):
        if not self.pin_up_not_triggered:
            # No way to verify raise attempt
            return
        for retry in range(3):
            success = self.verify_state(False)
            if success:
                # The "probe raised" test completed successfully
                break
            if retry >= 2:
                raise self.printer.command_error(
                    "BLTouch failed to raise probe")
            msg = "Failed to verify BLTouch probe is raised; retrying."
            self.gcode.respond_info(msg)
            self.sync_mcu_print_time()
            self.send_cmd('reset', duration=RETRY_RESET_TIME)
            self.send_cmd('pin_up', duration=self.pin_move_time)
    def lower_probe(self):
        self.test_sensor()
        self.sync_print_time()
        self.send_cmd('pin_down', duration=self.pin_move_time)
        if self.probe_touch_mode:
            self.send_cmd('touch_mode')
    def test_sensor(self):
        if not self.pin_up_touch_triggered:
            # Nothing to test
            return
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        if print_time < self.next_test_time:
            self.next_test_time = print_time + TEST_TIME
            return
        # Raise the bltouch probe and test if probe is raised
        self.sync_print_time()
        for retry in range(3):
            self.send_cmd('pin_up', duration=self.pin_move_time)
            self.send_cmd('touch_mode')
            success = self.verify_state(True)
            self.sync_print_time()
            if success:
                # The "bltouch connection" test completed successfully
                self.next_test_time = print_time + TEST_TIME
                return
            msg = "BLTouch failed to verify sensor state"
            if retry >= 2:
                raise self.printer.command_error(msg)
            self.gcode.respond_info(msg + '; retrying.')
            self.send_cmd('reset', duration=RETRY_RESET_TIME)
    def multi_probe_begin(self):
        if self.stow_on_each_sample:
            return
        self.multi = 'FIRST'
    def multi_probe_end(self):
        if self.stow_on_each_sample:
            return
        self.sync_print_time()
        self.raise_probe()
        self.verify_raise_probe()
        self.sync_print_time()
        self.multi = 'OFF'
    def probe_prepare(self, hmove):
        if self.multi == 'OFF' or self.multi == 'FIRST':
            self.lower_probe()
            if self.multi == 'FIRST':
                self.multi = 'ON'
        self.sync_print_time()
    def home_start(self, print_time, sample_time, sample_count, rest_time,
                   triggered=True):
        rest_time = min(rest_time, ENDSTOP_REST_TIME)
        self.finish_home_complete = self.mcu_endstop.home_start(
            print_time, sample_time, sample_count, rest_time, triggered)
        # Schedule wait_for_trigger callback
        r = self.printer.get_reactor()
        self.wait_trigger_complete = r.register_callback(self.wait_for_trigger)
        return self.finish_home_complete
    def wait_for_trigger(self, eventtime):
        self.finish_home_complete.wait()
        if self.multi == 'OFF':
            self.raise_probe()
    def probe_finish(self, hmove):
        self.wait_trigger_complete.wait()
        if self.multi == 'OFF':
            self.verify_raise_probe()
        self.sync_print_time()
        if hmove.check_no_movement() is not None:
            raise self.printer.command_error("BLTouch failed to deploy")
    def get_position_endstop(self):
        return self.position_endstop
    def set_output_mode(self, mode):
        # If this is inadvertently/purposely issued for a
        # BLTOUCH pre V3.0 and clones:
        #   No reaction at all.
        # BLTOUCH V3.0 and V3.1:
        #   This will set the mode.
        if mode is None:
            return
        logging.info("BLTouch set output mode: %s", mode)
        self.sync_mcu_print_time()
        if mode == '5V':
            self.send_cmd('set_5V_output_mode')
        if mode == 'OD':
            self.send_cmd('set_OD_output_mode')
    def store_output_mode(self, mode):
        # If this command is inadvertently/purposely issued for a
        # BLTOUCH pre V3.0 and clones:
        #   No reaction at all to this sequence apart from a pin-down/pin-up
        # BLTOUCH V3.0:
        #   This will set the mode (twice) and sadly, a pin-up is needed at
        #   the end, because of the pin-down
        # BLTOUCH V3.1:
        #   This will set the mode and store it in the eeprom.
        #   The pin-up is not needed but does not hurt
        logging.info("BLTouch store output mode: %s", mode)
        self.sync_print_time()
        self.send_cmd('pin_down')
        if mode == '5V':
            self.send_cmd('set_5V_output_mode')
        else:
            self.send_cmd('set_OD_output_mode')
        self.send_cmd('output_mode_store')
        if mode == '5V':
            self.send_cmd('set_5V_output_mode')
        else:
            self.send_cmd('set_OD_output_mode')
        self.send_cmd('pin_up')
    cmd_BLTOUCH_DEBUG_help = "Send a command to the bltouch for debugging"
    def cmd_BLTOUCH_DEBUG(self, gcmd):
        cmd = gcmd.get('COMMAND', None)
        if cmd is None or cmd not in Commands:
            gcmd.respond_info("BLTouch commands: %s" % (
                ", ".join(sorted([c for c in Commands if c is not None]))))
            return
        gcmd.respond_info("Sending BLTOUCH_DEBUG COMMAND=%s" % (cmd,))
        self.sync_print_time()
        self.send_cmd(cmd, duration=self.pin_move_time)
        self.sync_print_time()
    cmd_BLTOUCH_STORE_help = "Store an output mode in the BLTouch EEPROM"
    def cmd_BLTOUCH_STORE(self, gcmd):
        cmd = gcmd.get('MODE', None)
        if cmd is None or cmd not in ['5V', 'OD']:
            gcmd.respond_info("BLTouch output modes: 5V, OD")
            return
        gcmd.respond_info("Storing BLTouch output mode: %s" % (cmd,))
        self.sync_print_time()
        self.store_output_mode(cmd)
        self.sync_print_time()

def load_config(config):
    blt = BLTouchProbe(config)
    config.get_printer().add_object('probe', blt)
    return blt