#!/usr/bin/env python2
# Main code for host side printer firmware
#
# Copyright (C) 2016-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, gc, optparse, logging, time, collections, importlib
import util, reactor, queuelogger, msgproto
import gcode, configfile, pins, mcu, toolhead, webhooks

message_ready = "Printer is ready"

message_startup = """
Printer is not ready
The klippy host software is attempting to connect.  Please
retry in a few moments.
"""

message_restart = """
Once the underlying issue is corrected, use the "RESTART"
command to reload the config and restart the host software.
Printer is halted
"""

class Printer:
    config_error = configfile.error
    command_error = gcode.CommandError
    def __init__(self, main_reactor, bglogger, start_args):
        self.bglogger = bglogger
        self.start_args = start_args
        self.reactor = main_reactor
        self.reactor.register_callback(self._connect)
        self.state_message = message_startup
        self.in_shutdown_state = False
        self.run_result = None
        self.event_handlers = {}
        self.objects = collections.OrderedDict()
        # Init printer components that must be setup prior to config
        for m in [gcode, webhooks]:
            m.add_early_printer_objects(self)
    def get_start_args(self):
        return self.start_args
    def get_reactor(self):
        return self.reactor
    def get_state_message(self):
        if self.state_message == message_ready:
            category = "ready"
        elif self.state_message == message_startup:
            category = "startup"
        elif self.in_shutdown_state:
            category = "shutdown"
        else:
            category = "error"
        return self.state_message, category
    def is_shutdown(self):
        return self.in_shutdown_state
    def _set_state(self, msg):
        if self.state_message in (message_ready, message_startup):
            self.state_message = msg
        if (msg != message_ready
            and self.start_args.get('debuginput') is not None):
            self.request_exit('error_exit')
    def update_error_msg(self, oldmsg, newmsg):
        if (self.state_message != oldmsg
            or self.state_message in (message_ready, message_startup)
            or newmsg in (message_ready, message_startup)):
            return
        self.state_message = newmsg
        logging.error(newmsg)
    def add_object(self, name, obj):
        if name in self.objects:
            raise self.config_error(
                "Printer object '%s' already created" % (name,))
        self.objects[name] = obj
    def lookup_object(self, name, default=configfile.sentinel):
        if name in self.objects:
            return self.objects[name]
        if default is configfile.sentinel:
            raise self.config_error("Unknown config object '%s'" % (name,))
        return default
    def lookup_objects(self, module=None):
        if module is None:
            return list(self.objects.items())
        prefix = module + ' '
        objs = [(n, self.objects[n])
                for n in self.objects if n.startswith(prefix)]
        if module in self.objects:
            return [(module, self.objects[module])] + objs
        return objs
    def load_object(self, config, section, default=configfile.sentinel):
        if section in self.objects:
            return self.objects[section]
        module_parts = section.split()
        module_name = module_parts[0]
        py_name = os.path.join(os.path.dirname(__file__),
                               'extras', module_name + '.py')
        py_dirname = os.path.join(os.path.dirname(__file__),
                                  'extras', module_name, '__init__.py')
        if not os.path.exists(py_name) and not os.path.exists(py_dirname):
            if default is not configfile.sentinel:
                return default
            raise self.config_error("Unable to load module '%s'" % (section,))
        mod = importlib.import_module('extras.' + module_name)
        init_func = 'load_config'
        if len(module_parts) > 1:
            init_func = 'load_config_prefix'
        init_func = getattr(mod, init_func, None)
        if init_func is None:
            if default is not configfile.sentinel:
                return default
            raise self.config_error("Unable to load module '%s'" % (section,))
        self.objects[section] = init_func(config.getsection(section))
        return self.objects[section]
    def _read_config(self):
        self.objects['configfile'] = pconfig = configfile.PrinterConfig(self)
        config = pconfig.read_main_config()
        if self.bglogger is not None:
            pconfig.log_config(config)
        # Create printer components
        for m in [pins, mcu]:
            m.add_printer_objects(config)
        for section_config in config.get_prefix_sections(''):
            self.load_object(config, section_config.get_name(), None)
        for m in [toolhead]:
            m.add_printer_objects(config)
        # Validate that there are no undefined parameters in the config file
        pconfig.check_unused_options(config)
    def _connect(self, eventtime):
        try:
            self._read_config()
            self.send_event("klippy:mcu_identify")
            for cb in self.event_handlers.get("klippy:connect", []):
                if self.state_message is not message_startup:
                    return
                cb()
        except (self.config_error, pins.error) as e:
            logging.exception("Config error")
            self._set_state("%s\n%s" % (str(e), message_restart))
            return
        except msgproto.error as e:
            msg = "Protocol error"
            logging.exception(msg)
            self._set_state(msg)
            self.send_event("klippy:notify_mcu_error", msg, {"error": str(e)})
            util.dump_mcu_build()
            return
        except mcu.error as e:
            msg = "MCU error during connect"
            logging.exception(msg)
            self._set_state(msg)
            self.send_event("klippy:notify_mcu_error", msg, {"error": str(e)})
            util.dump_mcu_build()
            return
        except Exception as e:
            logging.exception("Unhandled exception during connect")
            self._set_state("Internal error during connect: %s\n%s"
                            % (str(e), message_restart,))
            return
        try:
            self._set_state(message_ready)
            with self.reactor.assert_no_pause():
                for cb in self.event_handlers.get("klippy:ready", []):
                    if self.state_message is not message_ready:
                        return
                    cb()
        except Exception as e:
            logging.exception("Unhandled exception during ready callback")
            self.invoke_shutdown("Internal error during ready callback: %s"
                                 % (str(e),))
    def run(self):
        systime = time.time()
        monotime = self.reactor.monotonic()
        logging.info("Start printer at %s (%.1f %.1f)",
                     time.asctime(time.localtime(systime)), systime, monotime)
        # Enter main reactor loop
        try:
            self.reactor.run()
        except:
            msg = "Unhandled exception during run"
            logging.exception(msg)
            # Exception from a reactor callback - try to shutdown
            try:
                self.reactor.register_callback((lambda e:
                                                self.invoke_shutdown(msg)))
                self.reactor.run()
            except:
                logging.exception("Repeat unhandled exception during run")
                # Another exception - try to exit
                self.run_result = "error_exit"
        # Check restart flags
        run_result = self.run_result
        try:
            if run_result == 'firmware_restart':
                self.send_event("klippy:firmware_restart")
            self.send_event("klippy:disconnect")
        except:
            logging.exception("Unhandled exception during post run")
        return run_result
    def set_rollover_info(self, name, info, log=True):
        if log:
            logging.info(info)
        if self.bglogger is not None:
            self.bglogger.set_rollover_info(name, info)
    def invoke_shutdown(self, msg, details={}):
        if self.in_shutdown_state:
            return
        logging.error("Transition to shutdown state: %s", msg)
        self.in_shutdown_state = True
        self._set_state(msg)
        with self.reactor.assert_no_pause():
            for cb in self.event_handlers.get("klippy:shutdown", []):
                try:
                    cb()
                except:
                    logging.exception("Exception during shutdown handler")
            for cb in self.event_handlers.get("klippy:analyze_shutdown", []):
                try:
                    cb(msg, details)
                except:
                    logging.exception("Exception in analyze_shutdown handler")
    def invoke_async_shutdown(self, msg, details={}):
        self.reactor.register_async_callback(
            (lambda e: self.invoke_shutdown(msg, details)))
    def register_event_handler(self, event, callback):
        self.event_handlers.setdefault(event, []).append(callback)
    def send_event(self, event, *params):
        return [cb(*params) for cb in self.event_handlers.get(event, [])]
    def request_exit(self, result):
        if self.run_result is None:
            self.run_result = result
        self.reactor.end()


######################################################################
# Startup
######################################################################

def import_test():
    # Import all optional modules (used as a build test)
    dname = os.path.dirname(__file__)
    for mname in ['extras', 'kinematics']:
        for fname in os.listdir(os.path.join(dname, mname)):
            if fname.endswith('.py') and fname != '__init__.py':
                module_name = fname[:-3]
            else:
                iname = os.path.join(dname, mname, fname, '__init__.py')
                if not os.path.exists(iname):
                    continue
                module_name = fname
            importlib.import_module(mname + '.' + module_name)
    sys.exit(0)

def arg_dictionary(option, opt_str, value, parser):
    key, fname = "dictionary", value
    if '=' in value:
        mcu_name, fname = value.split('=', 1)
        key = "dictionary_" + mcu_name
    if parser.values.dictionary is None:
        parser.values.dictionary = {}
    parser.values.dictionary[key] = fname

def main():
    usage = "%prog [options] <config file>"
    opts = optparse.OptionParser(usage)
    opts.add_option("-i", "--debuginput", dest="debuginput",
                    help="read commands from file instead of from tty port")
    opts.add_option("-I", "--input-tty", dest="inputtty",
                    default='/tmp/printer',
                    help="input tty name (default is /tmp/printer)")
    opts.add_option("-a", "--api-server", dest="apiserver",
                    help="api server unix domain socket filename")
    opts.add_option("-l", "--logfile", dest="logfile",
                    help="write log to file instead of stderr")
    opts.add_option("-v", action="store_true", dest="verbose",
                    help="enable debug messages")
    opts.add_option("-o", "--debugoutput", dest="debugoutput",
                    help="write output to file instead of to serial port")
    opts.add_option("-d", "--dictionary", dest="dictionary", type="string",
                    action="callback", callback=arg_dictionary,
                    help="file to read for mcu protocol dictionary")
    opts.add_option("--import-test", action="store_true",
                    help="perform an import module test")
    options, args = opts.parse_args()
    if options.import_test:
        import_test()
    if len(args) != 1:
        opts.error("Incorrect number of arguments")
    start_args = {'config_file': args[0], 'apiserver': options.apiserver,
                  'start_reason': 'startup'}

    debuglevel = logging.INFO
    if options.verbose:
        debuglevel = logging.DEBUG
    if options.debuginput:
        start_args['debuginput'] = options.debuginput
        debuginput = open(options.debuginput, 'rb')
        start_args['gcode_fd'] = debuginput.fileno()
    else:
        start_args['gcode_fd'] = util.create_pty(options.inputtty)
    if options.debugoutput:
        start_args['debugoutput'] = options.debugoutput
        start_args.update(options.dictionary)
    bglogger = None
    if options.logfile:
        start_args['log_file'] = options.logfile
        bglogger = queuelogger.setup_bg_logging(options.logfile, debuglevel)
    else:
        logging.getLogger().setLevel(debuglevel)
    logging.info("Starting Klippy...")
    git_info = util.get_git_version()
    git_vers = git_info["version"]
    extra_files = [fname for code, fname in git_info["file_status"]
                   if (code in ('??', '!!') and fname.endswith('.py')
                       and (fname.startswith('klippy/kinematics/')
                            or fname.startswith('klippy/extras/')))]
    modified_files = [fname for code, fname in git_info["file_status"]
                      if code == 'M']
    extra_git_desc = ""
    if extra_files:
        if not git_vers.endswith('-dirty'):
            git_vers = git_vers + '-dirty'
        if len(extra_files) > 10:
            extra_files[10:] = ["(+%d files)" % (len(extra_files) - 10,)]
        extra_git_desc += "\nUntracked files: %s" % (', '.join(extra_files),)
    if modified_files:
        if len(modified_files) > 10:
            modified_files[10:] = ["(+%d files)" % (len(modified_files) - 10,)]
        extra_git_desc += "\nModified files: %s" % (', '.join(modified_files),)
    extra_git_desc += "\nBranch: %s" % (git_info["branch"])
    extra_git_desc += "\nRemote: %s" % (git_info["remote"])
    extra_git_desc += "\nTracked URL: %s" % (git_info["url"])
    start_args['software_version'] = git_vers
    start_args['cpu_info'] = util.get_cpu_info()
    start_args['device'] = util.get_device_info()
    start_args['linux_version'] = util.get_linux_version()
    if bglogger is not None:
        versions = "\n".join([
            "Args: %s" % (sys.argv,),
            "Git version: %s%s" % (repr(start_args['software_version']),
                                   extra_git_desc),
            "CPU: %s" % (start_args['cpu_info'],),
            "Device: %s" % (start_args['device']),
            "Linux: %s" % (start_args['linux_version']),
            "Python: %s" % (repr(sys.version),)])
        logging.info(versions)
    elif not options.debugoutput:
        logging.warning("No log file specified!"
                        " Severe timing issues may result!")
    gc.disable()

    # Start Printer() class
    while 1:
        if bglogger is not None:
            bglogger.clear_rollover_info()
            bglogger.set_rollover_info('versions', versions)
        gc.collect()
        main_reactor = reactor.Reactor(gc_checking=True)
        printer = Printer(main_reactor, bglogger, start_args)
        res = printer.run()
        if res in ['exit', 'error_exit']:
            break
        time.sleep(1.)
        main_reactor.finalize()
        main_reactor = printer = None
        logging.info("Restarting printer")
        start_args['start_reason'] = res

    if bglogger is not None:
        bglogger.stop()

    if res == 'error_exit':
        sys.exit(-1)

if __name__ == '__main__':
    main()

# Code for coordinating events on the printer toolhead
#
# Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging, importlib
import mcu, chelper, kinematics.extruder

# Common suffixes: _d is distance (in mm), _v is velocity (in
#   mm/second), _v2 is velocity squared (mm^2/s^2), _t is time (in
#   seconds), _r is ratio (scalar between 0.0 and 1.0)

# Class to track each move request
class Move:
    def __init__(self, toolhead, start_pos, end_pos, speed):
        self.toolhead = toolhead
        self.start_pos = tuple(start_pos)
        self.end_pos = tuple(end_pos)
        self.accel = toolhead.max_accel
        self.junction_deviation = toolhead.junction_deviation
        self.timing_callbacks = []
        velocity = min(speed, toolhead.max_velocity)
        self.is_kinematic_move = True
        self.axes_d = axes_d = [ep - sp for sp, ep in zip(start_pos, end_pos)]
        self.move_d = move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
        if move_d < .000000001:
            # Extrude only move
            self.end_pos = ((start_pos[0], start_pos[1], start_pos[2])
                            + self.end_pos[3:])
            axes_d[0] = axes_d[1] = axes_d[2] = 0.
            self.move_d = move_d = max([abs(ad) for ad in axes_d[3:]])
            inv_move_d = 0.
            if move_d:
                inv_move_d = 1. / move_d
            self.accel = 99999999.9
            velocity = speed
            self.is_kinematic_move = False
        else:
            inv_move_d = 1. / move_d
        self.axes_r = [d * inv_move_d for d in axes_d]
        self.min_move_t = move_d / velocity
        # Junction speeds are tracked in velocity squared.  The
        # delta_v2 is the maximum amount of this squared-velocity that
        # can change in this move.
        self.max_start_v2 = 0.
        self.max_cruise_v2 = velocity**2
        self.delta_v2 = 2.0 * move_d * self.accel
        self.next_junction_v2 = 999999999.9
        # Setup for minimum_cruise_ratio checks
        self.max_mcr_start_v2 = 0.
        self.mcr_delta_v2 = 2.0 * move_d * toolhead.mcr_pseudo_accel
    def limit_speed(self, speed, accel):
        speed2 = speed**2
        if speed2 < self.max_cruise_v2:
            self.max_cruise_v2 = speed2
            self.min_move_t = self.move_d / speed
        self.accel = min(self.accel, accel)
        self.delta_v2 = 2.0 * self.move_d * self.accel
        self.mcr_delta_v2 = min(self.mcr_delta_v2, self.delta_v2)
    def limit_next_junction_speed(self, speed):
        self.next_junction_v2 = min(self.next_junction_v2, speed**2)
    def move_error(self, msg="Move out of range"):
        ep = self.end_pos
        m = "%s: %.3f %.3f %.3f [%.3f]" % (msg, ep[0], ep[1], ep[2], ep[3])
        return self.toolhead.printer.command_error(m)
    def calc_junction(self, prev_move):
        if not self.is_kinematic_move or not prev_move.is_kinematic_move:
            return
        # Allow extra axes to calculate maximum junction
        ea_v2 = [ea.calc_junction(prev_move, self, e_index+3)
                 for e_index, ea in enumerate(self.toolhead.extra_axes)]
        max_start_v2 = min([self.max_cruise_v2,
                            prev_move.max_cruise_v2, prev_move.next_junction_v2,
                            prev_move.max_start_v2 + prev_move.delta_v2]
                           + ea_v2)
        # Find max velocity using "approximated centripetal velocity"
        axes_r = self.axes_r
        prev_axes_r = prev_move.axes_r
        junction_cos_theta = -(axes_r[0] * prev_axes_r[0]
                               + axes_r[1] * prev_axes_r[1]
                               + axes_r[2] * prev_axes_r[2])
        sin_theta_d2 = math.sqrt(max(0.5*(1.0-junction_cos_theta), 0.))
        cos_theta_d2 = math.sqrt(max(0.5*(1.0+junction_cos_theta), 0.))
        one_minus_sin_theta_d2 = 1. - sin_theta_d2
        if one_minus_sin_theta_d2 > 0. and cos_theta_d2 > 0.:
            R_jd = sin_theta_d2 / one_minus_sin_theta_d2
            move_jd_v2 = R_jd * self.junction_deviation * self.accel
            pmove_jd_v2 = R_jd * prev_move.junction_deviation * prev_move.accel
            # Approximated circle must contact moves no further than mid-move
            #   centripetal_v2 = .5 * self.move_d * self.accel * tan_theta_d2
            quarter_tan_theta_d2 = .25 * sin_theta_d2 / cos_theta_d2
            move_centripetal_v2 = self.delta_v2 * quarter_tan_theta_d2
            pmove_centripetal_v2 = prev_move.delta_v2 * quarter_tan_theta_d2
            max_start_v2 = min(max_start_v2, move_jd_v2, pmove_jd_v2,
                               move_centripetal_v2, pmove_centripetal_v2)
        # Apply limits
        self.max_start_v2 = max_start_v2
        self.max_mcr_start_v2 = min(
            max_start_v2, prev_move.max_mcr_start_v2 + prev_move.mcr_delta_v2)
    def set_junction(self, start_v2, cruise_v2, end_v2):
        # Determine accel, cruise, and decel portions of the move distance
        half_inv_accel = .5 / self.accel
        accel_d = (cruise_v2 - start_v2) * half_inv_accel
        decel_d = (cruise_v2 - end_v2) * half_inv_accel
        cruise_d = self.move_d - accel_d - decel_d
        # Determine move velocities
        self.start_v = start_v = math.sqrt(start_v2)
        self.cruise_v = cruise_v = math.sqrt(cruise_v2)
        self.end_v = end_v = math.sqrt(end_v2)
        # Determine time spent in each portion of move (time is the
        # distance divided by average velocity)
        self.accel_t = accel_d / ((start_v + cruise_v) * 0.5)
        self.cruise_t = cruise_d / cruise_v
        self.decel_t = decel_d / ((end_v + cruise_v) * 0.5)

LOOKAHEAD_FLUSH_TIME = 0.150

# Class to track a list of pending move requests and to facilitate
# "look-ahead" across moves to reduce acceleration between moves.
class LookAheadQueue:
    def __init__(self):
        self.queue = []
        self.junction_flush = LOOKAHEAD_FLUSH_TIME
    def reset(self):
        del self.queue[:]
        self.junction_flush = LOOKAHEAD_FLUSH_TIME
    def set_flush_time(self, flush_time):
        self.junction_flush = flush_time
    def is_empty(self):
        return not self.queue
    def get_last(self):
        if self.queue:
            return self.queue[-1]
        return None
    def flush(self, lazy=False):
        self.junction_flush = LOOKAHEAD_FLUSH_TIME
        update_flush_count = lazy
        queue = self.queue
        flush_count = len(queue)
        # Traverse queue from last to first move and determine maximum
        # junction speed assuming the robot comes to a complete stop
        # after the last move.
        junction_info = [None] * flush_count
        next_start_v2 = next_mcr_start_v2 = peak_cruise_v2 = 0.
        pending_cv2_assign = 0
        for i in range(flush_count-1, -1, -1):
            move = queue[i]
            reachable_start_v2 = next_start_v2 + move.delta_v2
            start_v2 = min(move.max_start_v2, reachable_start_v2)
            cruise_v2 = None
            pending_cv2_assign += 1
            reach_mcr_start_v2 = next_mcr_start_v2 + move.mcr_delta_v2
            mcr_start_v2 = min(move.max_mcr_start_v2, reach_mcr_start_v2)
            if mcr_start_v2 < reach_mcr_start_v2:
                # It's possible for this move to accelerate
                if (mcr_start_v2 + move.mcr_delta_v2 > next_mcr_start_v2
                    or pending_cv2_assign > 1):
                    # This move can both accel and decel, or this is a
                    # full accel move followed by a full decel move
                    if update_flush_count and peak_cruise_v2:
                        flush_count = i + pending_cv2_assign
                        update_flush_count = False
                    peak_cruise_v2 = (mcr_start_v2 + reach_mcr_start_v2) * .5
                cruise_v2 = min((start_v2 + reachable_start_v2) * .5
                                , move.max_cruise_v2, peak_cruise_v2)
                pending_cv2_assign = 0
            junction_info[i] = (move, start_v2, cruise_v2, next_start_v2)
            next_start_v2 = start_v2
            next_mcr_start_v2 = mcr_start_v2
        if update_flush_count or not flush_count:
            return []
        # Traverse queue in forward direction to propagate cruise_v2
        prev_cruise_v2 = 0.
        for i in range(flush_count):
            move, start_v2, cruise_v2, next_start_v2 = junction_info[i]
            if cruise_v2 is None:
                # This move can't accelerate - propagate cruise_v2 from previous
                cruise_v2 = min(prev_cruise_v2, start_v2)
            move.set_junction(min(start_v2, cruise_v2), cruise_v2
                              , min(next_start_v2, cruise_v2))
            prev_cruise_v2 = cruise_v2
        # Remove processed moves from the queue
        res = queue[:flush_count]
        del queue[:flush_count]
        return res
    def add_move(self, move):
        self.queue.append(move)
        if len(self.queue) == 1:
            return
        move.calc_junction(self.queue[-2])
        self.junction_flush -= move.min_move_t
        # Check if enough moves have been queued to reach the target flush time.
        return self.junction_flush <= 0.

BUFFER_TIME_HIGH = 1.0
BUFFER_TIME_START = 0.250
PRIMING_CMD_TIME = 0.100

# Main code to track events (and their timing) on the printer toolhead
class ToolHead:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.mcu = self.printer.lookup_object('mcu')
        self.lookahead = LookAheadQueue()
        self.lookahead.set_flush_time(BUFFER_TIME_HIGH)
        self.commanded_pos = [0., 0., 0., 0.]
        # Velocity and acceleration control
        self.max_velocity = config.getfloat('max_velocity', above=0.)
        self.max_accel = config.getfloat('max_accel', above=0.)
        self.min_cruise_ratio = config.getfloat('minimum_cruise_ratio',
                                                0.5, below=1., minval=0.)
        self.square_corner_velocity = config.getfloat(
            'square_corner_velocity', 5., minval=0.)
        self.junction_deviation = self.mcr_pseudo_accel = 0.
        self._calc_junction_deviation()
        # Input stall detection
        self.check_stall_time = 0.
        self.print_stall = 0
        # Input pause tracking
        self.can_pause = True
        if self.mcu.is_fileoutput():
            self.can_pause = False
        self.need_check_pause = -1.
        # Print time tracking
        self.print_time = 0.
        self.special_queuing_state = "NeedPrime"
        self.priming_timer = None
        # Setup for generating moves
        self.motion_queuing = self.printer.load_object(config, 'motion_queuing')
        self.motion_queuing.register_flush_callback(self._handle_step_flush,
                                                    can_add_trapq=True)
        self.trapq = self.motion_queuing.allocate_trapq()
        self.trapq_append = self.motion_queuing.lookup_trapq_append()
        # Create kinematics class
        gcode = self.printer.lookup_object('gcode')
        self.Coord = gcode.Coord
        extruder = kinematics.extruder.DummyExtruder(self.printer)
        self.extra_axes = [extruder]
        self.extra_axes_status = {}
        self._build_extra_axes_status()
        kin_name = config.get('kinematics')
        try:
            mod = importlib.import_module('kinematics.' + kin_name)
            self.kin = mod.load_kinematics(self, config)
        except config.error as e:
            raise
        except self.printer.lookup_object('pins').error as e:
            raise
        except:
            msg = "Error loading kinematics '%s'" % (kin_name,)
            logging.exception(msg)
            raise config.error(msg)
        # Register handlers
        self.printer.register_event_handler("klippy:shutdown",
                                            self._handle_shutdown)
    # Print time tracking
    def _advance_move_time(self, next_print_time):
        self.print_time = max(self.print_time, next_print_time)
    def _calc_print_time(self):
        curtime = self.reactor.monotonic()
        est_print_time = self.mcu.estimated_print_time(curtime)
        kin_time = self.motion_queuing.calc_step_gen_restart(est_print_time)
        min_print_time = max(est_print_time + BUFFER_TIME_START, kin_time)
        if min_print_time > self.print_time:
            self.print_time = min_print_time
            self.printer.send_event("toolhead:sync_print_time",
                                    curtime, est_print_time, self.print_time)
    def _process_lookahead(self, lazy=False):
        moves = self.lookahead.flush(lazy=lazy)
        if not moves:
            return
        # Resync print_time if necessary
        if self.special_queuing_state:
            # Transition from "NeedPrime"/"Priming" state to main state
            self.special_queuing_state = ""
            self.need_check_pause = -1.
            self._calc_print_time()
        # Queue moves into trapezoid motion queue (trapq)
        next_move_time = self.print_time
        with self.reactor.assert_no_pause():
            for move in moves:
                if move.is_kinematic_move:
                    self.trapq_append(
                        self.trapq, next_move_time,
                        move.accel_t, move.cruise_t, move.decel_t,
                        move.start_pos[0], move.start_pos[1], move.start_pos[2],
                        move.axes_r[0], move.axes_r[1], move.axes_r[2],
                        move.start_v, move.cruise_v, move.accel)
                for e_index, ea in enumerate(self.extra_axes):
                    if move.axes_d[e_index + 3]:
                        ea.process_move(next_move_time, move, e_index + 3)
                next_move_time = (next_move_time + move.accel_t
                                  + move.cruise_t + move.decel_t)
                for cb in move.timing_callbacks:
                    cb(next_move_time)
        # Generate steps for moves
        self._advance_move_time(next_move_time)
        self.motion_queuing.note_mcu_movequeue_activity(next_move_time)
    def _flush_lookahead(self, is_runout=False):
        # Transit from "NeedPrime"/"Priming"/main state to "NeedPrime"
        prev_print_time = self.print_time
        self._process_lookahead()
        self.special_queuing_state = "NeedPrime"
        self.need_check_pause = -1.
        self.lookahead.set_flush_time(BUFFER_TIME_HIGH)
        self.check_stall_time = 0.
        if is_runout and prev_print_time != self.print_time:
            self.check_stall_time = self.print_time
    def _handle_step_flush(self, flush_time, step_gen_time):
        if self.special_queuing_state:
            return
        # In "main" state - flush lookahead if buffer runs low
        kin_flush_delay = self.motion_queuing.get_kin_flush_delay()
        if step_gen_time >= self.print_time - kin_flush_delay - 0.001:
            self._flush_lookahead(is_runout=True)
    def flush_step_generation(self):
        self._flush_lookahead()
        self.motion_queuing.flush_all_steps()
    def get_last_move_time(self):
        if self.special_queuing_state:
            self._flush_lookahead()
            self._calc_print_time()
        else:
            self._process_lookahead()
        return self.print_time
    def _priming_handler(self, eventtime):
        self.reactor.unregister_timer(self.priming_timer)
        self.priming_timer = None
        try:
            if self.special_queuing_state == "Priming":
                self._flush_lookahead(is_runout=True)
        except:
            logging.exception("Exception in priming_handler")
            self.printer.invoke_shutdown("Exception in priming_handler")
        return self.reactor.NEVER
    def _check_priming_state(self, eventtime):
        if self.lookahead.is_empty():
            # In "NeedPrime" state and can remain there
            return
        est_print_time = self.mcu.estimated_print_time(eventtime)
        if self.check_stall_time:
            # Was in "NeedPrime" state and got there from idle input
            if est_print_time < self.check_stall_time:
                self.print_stall += 1
            self.check_stall_time = 0.
        # Transition from "NeedPrime"/"Priming" state to "Priming" state
        self.special_queuing_state = "Priming"
        self.need_check_pause = -1.
        if self.priming_timer is None:
            self.priming_timer = self.reactor.register_timer(
                self._priming_handler)
        will_pause_time = self.print_time - est_print_time - BUFFER_TIME_HIGH
        wtime = eventtime + max(0., will_pause_time) + PRIMING_CMD_TIME
        self.reactor.update_timer(self.priming_timer, wtime)
    def _check_pause(self):
        eventtime = self.reactor.monotonic()
        if self.special_queuing_state:
            # In "NeedPrime"/"Priming" state - update priming expiration timer
            self._check_priming_state(eventtime)
        # Check if there are lots of queued moves and pause if so
        did_pause = False
        while 1:
            est_print_time = self.mcu.estimated_print_time(eventtime)
            pause_time = self.print_time - est_print_time - BUFFER_TIME_HIGH
            if pause_time <= 0.:
                break
            if not self.can_pause:
                self.need_check_pause = self.reactor.NEVER
                return
            pause_time = max(.005, min(1., pause_time))
            eventtime = self.reactor.pause(eventtime + pause_time)
            did_pause = True
        if not self.special_queuing_state:
            # In main state - defer pause checking
            self.need_check_pause = self.print_time
            if not did_pause:
                # May be falling behind - yield to avoid starving other tasks
                self.reactor.pause(self.reactor.NOW)
    # Movement commands
    def get_position(self):
        return list(self.commanded_pos)
    def set_position(self, newpos, homing_axes=""):
        self.flush_step_generation()
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trapq_set_position(self.trapq, self.print_time,
                                   newpos[0], newpos[1], newpos[2])
        self.commanded_pos[:3] = newpos[:3]
        self.kin.set_position(newpos, homing_axes)
        self.printer.send_event("toolhead:set_position")
    def limit_next_junction_speed(self, speed):
        last_move = self.lookahead.get_last()
        if last_move is not None:
            last_move.limit_next_junction_speed(speed)
    def move(self, newpos, speed):
        move = Move(self, self.commanded_pos, newpos, speed)
        if not move.move_d:
            return
        if move.is_kinematic_move:
            self.kin.check_move(move)
        for e_index, ea in enumerate(self.extra_axes):
            if move.axes_d[e_index + 3]:
                ea.check_move(move, e_index + 3)
        self.commanded_pos[:] = move.end_pos
        want_flush = self.lookahead.add_move(move)
        if want_flush:
            self._process_lookahead(lazy=True)
        if self.print_time > self.need_check_pause:
            self._check_pause()
    def manual_move(self, coord, speed):
        curpos = list(self.commanded_pos)
        for i in range(len(coord)):
            if coord[i] is not None:
                curpos[i] = coord[i]
        self.move(curpos, speed)
        self.printer.send_event("toolhead:manual_move")
    def dwell(self, delay):
        self._flush_lookahead()
        next_print_time = self.get_last_move_time() + max(0., delay)
        self._advance_move_time(next_print_time)
        self._check_pause()
    def wait_moves(self):
        self._flush_lookahead()
        eventtime = self.reactor.monotonic()
        while (not self.special_queuing_state
               or self.print_time >= self.mcu.estimated_print_time(eventtime)):
            if not self.can_pause:
                break
            eventtime = self.reactor.pause(eventtime + 0.100)
    def _build_extra_axes_status(self):
        enames = [ea.get_name() for ea in self.extra_axes]
        self.extra_axes_status = {n: e_index + 3
                                  for e_index, n in enumerate(enames) if n}
    def set_extruder(self, extruder, extrude_pos):
        # XXX - should use add_extra_axis
        self.extra_axes[0] = extruder
        self.commanded_pos[3] = extrude_pos
        self._build_extra_axes_status()
    def get_extruder(self):
        return self.extra_axes[0]
    def add_extra_axis(self, ea, axis_pos):
        self._flush_lookahead()
        self.extra_axes.append(ea)
        self.commanded_pos.append(axis_pos)
        self._build_extra_axes_status()
        self.printer.send_event("toolhead:update_extra_axes")
    def remove_extra_axis(self, ea):
        self._flush_lookahead()
        if ea not in self.extra_axes:
            return
        ea_index = self.extra_axes.index(ea) + 3
        self.commanded_pos.pop(ea_index)
        self.extra_axes.pop(ea_index - 3)
        self._build_extra_axes_status()
        self.printer.send_event("toolhead:update_extra_axes")
    def get_extra_axes(self):
        return [None, None, None] + self.extra_axes
    # Homing "drip move" handling
    def _drip_load_trapq(self, submit_move):
        # Queue move into trapezoid motion queue (trapq)
        if submit_move.move_d:
            self.commanded_pos[:] = submit_move.end_pos
            self.lookahead.add_move(submit_move)
        moves = self.lookahead.flush()
        self._calc_print_time()
        start_time = end_time = self.print_time
        for move in moves:
            self.trapq_append(
                self.trapq, end_time,
                move.accel_t, move.cruise_t, move.decel_t,
                move.start_pos[0], move.start_pos[1], move.start_pos[2],
                move.axes_r[0], move.axes_r[1], move.axes_r[2],
                move.start_v, move.cruise_v, move.accel)
            end_time = end_time + move.accel_t + move.cruise_t + move.decel_t
        self.lookahead.reset()
        return start_time, end_time
    def drip_move(self, newpos, speed, drip_completion):
        # Create and verify move is valid
        newpos = newpos[:3] + self.commanded_pos[3:]
        move = Move(self, self.commanded_pos, newpos, speed)
        if move.move_d:
            self.kin.check_move(move)
        # Make sure stepper movement doesn't start before nominal start time
        kin_flush_delay = self.motion_queuing.get_kin_flush_delay()
        self.dwell(kin_flush_delay)
        # Transmit move in "drip" mode
        self._process_lookahead()
        start_time, end_time = self._drip_load_trapq(move)
        self.motion_queuing.drip_update_time(start_time, end_time,
                                             drip_completion)
        # Move finished; cleanup any remnants on trapq
        self.motion_queuing.wipe_trapq(self.trapq)
    # Misc commands
    def stats(self, eventtime):
        est_print_time = self.mcu.estimated_print_time(eventtime)
        buffer_time = self.print_time - est_print_time
        is_active = buffer_time > -60. or not self.special_queuing_state
        return is_active, "print_time=%.3f buffer_time=%.3f print_stall=%d" % (
            self.print_time, max(buffer_time, 0.), self.print_stall)
    def check_busy(self, eventtime):
        est_print_time = self.mcu.estimated_print_time(eventtime)
        return self.print_time, est_print_time, self.lookahead.is_empty()
    def get_status(self, eventtime):
        print_time = self.print_time
        estimated_print_time = self.mcu.estimated_print_time(eventtime)
        extruder = self.extra_axes[0]
        res = dict(self.kin.get_status(eventtime))
        res.update({ 'print_time': print_time,
                     'stalls': self.print_stall,
                     'estimated_print_time': estimated_print_time,
                     'extruder': extruder.get_name(),
                     'position': self.Coord(self.commanded_pos),
                     'max_velocity': self.max_velocity,
                     'max_accel': self.max_accel,
                     'minimum_cruise_ratio': self.min_cruise_ratio,
                     'square_corner_velocity': self.square_corner_velocity,
                     'extra_axes': self.extra_axes_status})
        return res
    def _handle_shutdown(self):
        self.can_pause = False
        self.lookahead.reset()
    def get_kinematics(self):
        return self.kin
    def get_trapq(self):
        return self.trapq
    def register_lookahead_callback(self, callback):
        last_move = self.lookahead.get_last()
        if last_move is None:
            callback(self.get_last_move_time())
            return
        last_move.timing_callbacks.append(callback)
    def get_max_velocity(self):
        return self.max_velocity, self.max_accel
    def _calc_junction_deviation(self):
        scv2 = self.square_corner_velocity**2
        self.junction_deviation = scv2 * (math.sqrt(2.) - 1.) / self.max_accel
        self.mcr_pseudo_accel = self.max_accel * (1. - self.min_cruise_ratio)
    def set_max_velocities(self, max_velocity, max_accel,
                           square_corner_velocity, min_cruise_ratio):
        if max_velocity is not None:
            self.max_velocity = max_velocity
        if max_accel is not None:
            self.max_accel = max_accel
        if square_corner_velocity is not None:
            self.square_corner_velocity = square_corner_velocity
        if min_cruise_ratio is not None:
            self.min_cruise_ratio = min_cruise_ratio
        self._calc_junction_deviation()
        return (self.max_velocity, self.max_accel,
                self.square_corner_velocity, self.min_cruise_ratio)

# Support common G-Code commands relative to the toolhead
class ToolHeadCommandHelper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = self.printer.lookup_object("toolhead")
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('G4', self.cmd_G4)
        gcode.register_command('M400', self.cmd_M400)
        gcode.register_command('SET_VELOCITY_LIMIT',
                               self.cmd_SET_VELOCITY_LIMIT,
                               desc=self.cmd_SET_VELOCITY_LIMIT_help)
        gcode.register_command('M204', self.cmd_M204)
    def cmd_G4(self, gcmd):
        # Dwell
        delay = gcmd.get_float('P', 0., minval=0.) / 1000.
        self.toolhead.dwell(delay)
    def cmd_M400(self, gcmd):
        # Wait for current moves to finish
        self.toolhead.wait_moves()
    cmd_SET_VELOCITY_LIMIT_help = "Set printer velocity limits"
    def cmd_SET_VELOCITY_LIMIT(self, gcmd):
        max_velocity = gcmd.get_float('VELOCITY', None, above=0.)
        max_accel = gcmd.get_float('ACCEL', None, above=0.)
        square_corner_velocity = gcmd.get_float(
            'SQUARE_CORNER_VELOCITY', None, minval=0.)
        min_cruise_ratio = gcmd.get_float(
            'MINIMUM_CRUISE_RATIO', None, minval=0., below=1.)
        mv, ma, scv, mcr = self.toolhead.set_max_velocities(
            max_velocity, max_accel, square_corner_velocity, min_cruise_ratio)
        msg = ("max_velocity: %.6f\n"
               "max_accel: %.6f\n"
               "minimum_cruise_ratio: %.6f\n"
               "square_corner_velocity: %.6f" % (mv, ma, mcr, scv))
        self.printer.set_rollover_info("toolhead", "toolhead: %s" % (msg,))
        if (max_velocity is None and max_accel is None
            and square_corner_velocity is None and min_cruise_ratio is None):
            gcmd.respond_info(msg, log=False)
    def cmd_M204(self, gcmd):
        # Use S for accel
        accel = gcmd.get_float('S', None, above=0.)
        if accel is None:
            # Use minimum of P and T for accel
            p = gcmd.get_float('P', None, above=0.)
            t = gcmd.get_float('T', None, above=0.)
            if p is None or t is None:
                gcmd.respond_info('Invalid M204 command "%s"'
                                  % (gcmd.get_commandline(),))
                return
            accel = min(p, t)
        self.toolhead.set_max_velocities(None, accel, None, None)

def add_printer_objects(config):
    printer = config.get_printer()
    printer.add_object('toolhead', ToolHead(config))
    ToolHeadCommandHelper(config)
    # Load default extruder objects
    kinematics.extruder.add_printer_objects(config)
    # Load some default modules
    modules = ["gcode_move", "homing", "idle_timeout", "statistics",
               "manual_probe", "tuning_tower", "garbage_collection"]
    for module_name in modules:
        printer.load_object(config, module_name)

# File descriptor and timer event helper
#
# Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, gc, select, math, time, logging, queue
import greenlet
import chelper, util

_NOW = 0.
_NEVER = 9999999999999999.

class ReactorError(Exception):
    pass

class ReactorTimer:
    def __init__(self, callback, waketime):
        self.callback = callback
        self.waketime = waketime
        self.timer_is_running = False

class ReactorCompletion:
    class sentinel: pass
    def __init__(self, reactor):
        self.reactor = reactor
        self.result = self.sentinel
        self.waiting = []
    def test(self):
        return self.result is not self.sentinel
    def complete(self, result):
        self.result = result
        for wait in self.waiting:
            self.reactor.update_timer(wait.timer, self.reactor.NOW)
    def wait(self, waketime=_NEVER, waketime_result=None):
        if self.result is self.sentinel:
            wait = greenlet.getcurrent()
            self.waiting.append(wait)
            self.reactor.pause(waketime)
            self.waiting.remove(wait)
            if self.result is self.sentinel:
                return waketime_result
        return self.result

class ReactorCallback:
    def __init__(self, reactor, callback, waketime):
        self.reactor = reactor
        self.timer = reactor.register_timer(self.invoke, waketime)
        self.callback = callback
        self.completion = ReactorCompletion(reactor)
    def invoke(self, eventtime):
        self.reactor.unregister_timer(self.timer)
        res = self.callback(eventtime)
        self.completion.complete(res)
        return self.reactor.NEVER

class ReactorFileHandler:
    def __init__(self, fd, read_callback, write_callback):
        self.fd = fd
        self.read_callback = read_callback
        self.write_callback = write_callback

class ReactorGreenlet(greenlet.greenlet):
    def __init__(self, run):
        greenlet.greenlet.__init__(self, run=run)
        self.timer = None

class ReactorMutex:
    def __init__(self, reactor, is_locked):
        self.reactor = reactor
        self.is_locked = is_locked
        self.next_pending = False
        self.queue = []
        self.lock = self.__enter__
        self.unlock = self.__exit__
    def test(self):
        return self.is_locked
    def __enter__(self):
        if not self.is_locked:
            self.is_locked = True
            return
        g = greenlet.getcurrent()
        self.queue.append(g)
        while 1:
            self.reactor.pause(self.reactor.NEVER)
            if self.next_pending and self.queue[0] is g:
                self.next_pending = False
                self.queue.pop(0)
                return
    def __exit__(self, type=None, value=None, tb=None):
        if not self.queue:
            self.is_locked = False
            return
        self.next_pending = True
        self.reactor.update_timer(self.queue[0].timer, self.reactor.NOW)

class ReactorPreventPause:
    def __init__(self, reactor):
        self.reactor = reactor
    def __enter__(self):
        self.reactor._prevent_pause_count += 1
    def __exit__(self, type=None, value=None, tb=None):
        self.reactor._prevent_pause_count -= 1

class SelectReactor:
    NOW = _NOW
    NEVER = _NEVER
    def __init__(self, gc_checking=False):
        # Main code
        self._process = False
        self.monotonic = chelper.get_ffi()[1].get_monotonic
        # Python garbage collection
        self._check_gc = gc_checking
        self._last_gc_times = [0., 0., 0.]
        # Timers
        self._timers = []
        self._next_timer = self.NEVER
        # Callbacks
        self._pipe_fds = None
        self._async_queue = queue.Queue()
        # File descriptors
        self._dummy_fd_hdl = ReactorFileHandler(-1, (lambda e: None),
                                                (lambda e: None))
        self._fds = {}
        self._read_fds = []
        self._write_fds = []
        self._READ = 1
        self._WRITE = 2
        # Greenlets
        self._g_dispatch = None
        self._greenlets = []
        self._all_greenlets = []
        self._prevent_pause_count = 0
    def get_gc_stats(self):
        return tuple(self._last_gc_times)
    # Timers
    def update_timer(self, timer_handler, waketime):
        if timer_handler.timer_is_running:
            return
        timer_handler.waketime = waketime
        self._next_timer = min(self._next_timer, waketime)
    def register_timer(self, callback, waketime=NEVER):
        timer_handler = ReactorTimer(callback, waketime)
        timers = list(self._timers)
        timers.append(timer_handler)
        self._timers = timers
        self._next_timer = min(self._next_timer, waketime)
        return timer_handler
    def unregister_timer(self, timer_handler):
        timer_handler.waketime = self.NEVER
        timers = list(self._timers)
        timers.pop(timers.index(timer_handler))
        self._timers = timers
    def _check_timers(self, eventtime, busy):
        if eventtime < self._next_timer:
            if busy:
                return 0.
            if self._check_gc:
                gi = gc.get_count()
                if gi[0] >= 700:
                    # Reactor looks idle and gc is due - run it
                    gc_level = 0
                    if gi[1] >= 10:
                        gc_level = 1
                        if gi[2] >= 10:
                            gc_level = 2
                    self._last_gc_times[gc_level] = eventtime
                    gc.collect(gc_level)
                    return 0.
            return min(1., max(.001, self._next_timer - eventtime))
        self._next_timer = self.NEVER
        g_dispatch = self._g_dispatch
        for t in self._timers:
            waketime = t.waketime
            if eventtime >= waketime:
                t.waketime = self.NEVER
                t.timer_is_running = True
                t.waketime = waketime = t.callback(eventtime)
                t.timer_is_running = False
                if g_dispatch is not self._g_dispatch:
                    self._next_timer = min(self._next_timer, waketime)
                    self._end_greenlet(g_dispatch)
                    return 0.
            self._next_timer = min(self._next_timer, waketime)
        return 0.
    # Callbacks and Completions
    def completion(self):
        return ReactorCompletion(self)
    def register_callback(self, callback, waketime=NOW):
        rcb = ReactorCallback(self, callback, waketime)
        return rcb.completion
    # Asynchronous (from another thread) callbacks and completions
    def register_async_callback(self, callback, waketime=NOW):
        self._async_queue.put_nowait(
            (ReactorCallback, (self, callback, waketime)))
        try:
            os.write(self._pipe_fds[1], b'.')
        except os.error:
            pass
    def async_complete(self, completion, result):
        self._async_queue.put_nowait((completion.complete, (result,)))
        try:
            os.write(self._pipe_fds[1], b'.')
        except os.error:
            pass
    def _got_pipe_signal(self, eventtime):
        try:
            os.read(self._pipe_fds[0], 4096)
        except os.error:
            pass
        while 1:
            try:
                func, args = self._async_queue.get_nowait()
            except queue.Empty:
                break
            func(*args)
    def _setup_async_callbacks(self):
        self._pipe_fds = os.pipe()
        util.set_nonblock(self._pipe_fds[0])
        util.set_nonblock(self._pipe_fds[1])
        self.register_fd(self._pipe_fds[0], self._got_pipe_signal)
    # Greenlets
    def _sys_pause(self, waketime):
        # Pause using system sleep for when reactor not running
        delay = waketime - self.monotonic()
        if delay > 0.:
            time.sleep(delay)
        return self.monotonic()
    def pause(self, waketime):
        g = greenlet.getcurrent()
        if g is not self._g_dispatch:
            if self._g_dispatch is None:
                return self._sys_pause(waketime)
            # Switch to _check_timers (via g.timer.callback return)
            if self._prevent_pause_count:
                self.verify_can_pause()
            return self._g_dispatch.switch(waketime)
        # Pausing the dispatch greenlet - prepare a new greenlet to do dispatch
        if self._prevent_pause_count:
            self.verify_can_pause()
        if self._greenlets:
            g_next = self._greenlets.pop()
        else:
            g_next = ReactorGreenlet(run=self._dispatch_loop)
            self._all_greenlets.append(g_next)
        g_next.parent = g.parent
        g.timer = self.register_timer(g.switch, waketime)
        self._next_timer = self.NOW
        # Switch to _dispatch_loop (via _end_greenlet or direct)
        eventtime = g_next.switch()
        # This greenlet activated from g.timer.callback (via _check_timers)
        return eventtime
    def _end_greenlet(self, g_old):
        # Cache this greenlet for later use
        self._greenlets.append(g_old)
        self.unregister_timer(g_old.timer)
        g_old.timer = None
        # Switch to _check_timers (via g_old.timer.callback return)
        self._g_dispatch.switch(self.NEVER)
        # This greenlet reactivated from pause() - return to main dispatch loop
        self._g_dispatch = g_old
    # Support for temporarily disabling pauses
    def assert_no_pause(self):
        return ReactorPreventPause(self)
    def verify_can_pause(self):
        if self._prevent_pause_count:
            raise ReactorError("Internal error - reactor pause disabled")
    # Mutexes
    def mutex(self, is_locked=False):
        return ReactorMutex(self, is_locked)
    # File descriptors
    def register_fd(self, fd, read_callback, write_callback=None):
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self.set_fd_wake(file_handler, True, False)
        return file_handler
    def unregister_fd(self, file_handler):
        self.set_fd_wake(file_handler, False, False)
        del self._fds[file_handler.fd]
    def set_fd_wake(self, file_handler, is_readable=True, is_writeable=False):
        fd = file_handler.fd
        if fd in self._read_fds:
            if not is_readable:
                self._read_fds.remove(fd)
        elif is_readable:
            self._read_fds.append(fd)
        if fd in self._write_fds:
            if not is_writeable:
                self._write_fds.remove(fd)
        elif is_writeable:
            self._write_fds.append(fd)
    def _check_fds(self, eventtime, hdls):
        g_dispatch = self._g_dispatch
        for fd, event in hdls:
            hdl = self._fds.get(fd, self._dummy_fd_hdl)
            if event & self._READ:
                hdl.read_callback(eventtime)
                if g_dispatch is not self._g_dispatch:
                    self._end_greenlet(g_dispatch)
                    return self.monotonic()
            if event & self._WRITE:
                hdl.write_callback(eventtime)
                if g_dispatch is not self._g_dispatch:
                    self._end_greenlet(g_dispatch)
                    return self.monotonic()
        return eventtime
    # Main loop
    def _dispatch_loop(self):
        self._g_dispatch = greenlet.getcurrent()
        busy = True
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            res = select.select(self._read_fds, self._write_fds, [], timeout)
            eventtime = self.monotonic()
            if res[0] or res[1]:
                busy = True
                hdls = ([(fd, self._READ) for fd in res[0]]
                        + [(fd, self._WRITE) for fd in res[1]])
                eventtime = self._check_fds(eventtime, hdls)
        self._g_dispatch = None
    def run(self):
        if self._pipe_fds is None:
            self._setup_async_callbacks()
        self._process = True
        self._prevent_pause_count = 0
        g_next = ReactorGreenlet(run=self._dispatch_loop)
        self._all_greenlets.append(g_next)
        g_next.switch()
    def end(self):
        self._process = False
    def finalize(self):
        self._g_dispatch = None
        self._greenlets = []
        for g in self._all_greenlets:
            try:
                g.throw()
            except:
                logging.exception("reactor finalize greenlet terminate")
        self._all_greenlets = []
        if self._pipe_fds is not None:
            os.close(self._pipe_fds[0])
            os.close(self._pipe_fds[1])
            self._pipe_fds = None

class PollReactor(SelectReactor):
    def __init__(self, gc_checking=False):
        SelectReactor.__init__(self, gc_checking)
        self._poll = select.poll()
        self._READ = select.POLLIN | select.POLLHUP
        self._WRITE = select.POLLOUT
    # File descriptors
    def register_fd(self, fd, read_callback, write_callback=None):
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self._poll.register(file_handler.fd, select.POLLIN | select.POLLHUP)
        return file_handler
    def unregister_fd(self, file_handler):
        self._poll.unregister(file_handler.fd)
        del self._fds[file_handler.fd]
    def set_fd_wake(self, file_handler, is_readable=True, is_writeable=False):
        flags = select.POLLHUP
        if is_readable:
            flags |= select.POLLIN
        if is_writeable:
            flags |= select.POLLOUT
        self._poll.modify(file_handler.fd, flags)
    # Main loop
    def _dispatch_loop(self):
        self._g_dispatch = greenlet.getcurrent()
        busy = True
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            res = self._poll.poll(int(math.ceil(timeout * 1000.)))
            eventtime = self.monotonic()
            if res:
                busy = True
                eventtime = self._check_fds(eventtime, res)
        self._g_dispatch = None

class EPollReactor(SelectReactor):
    def __init__(self, gc_checking=False):
        SelectReactor.__init__(self, gc_checking)
        self._epoll = select.epoll()
        self._READ = select.EPOLLIN | select.EPOLLHUP
        self._WRITE = select.EPOLLOUT
    # File descriptors
    def register_fd(self, fd, read_callback, write_callback=None):
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self._epoll.register(fd, select.EPOLLIN | select.EPOLLHUP)
        return file_handler
    def unregister_fd(self, file_handler):
        self._epoll.unregister(file_handler.fd)
        del self._fds[file_handler.fd]
    def set_fd_wake(self, file_handler, is_readable=True, is_writeable=False):
        flags = select.EPOLLHUP
        if is_readable:
            flags |= select.EPOLLIN
        if is_writeable:
            flags |= select.EPOLLOUT
        self._epoll.modify(file_handler.fd, flags)
    # Main loop
    def _dispatch_loop(self):
        self._g_dispatch = greenlet.getcurrent()
        busy = True
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            res = self._epoll.poll(timeout)
            eventtime = self.monotonic()
            if res:
                busy = True
                eventtime = self._check_fds(eventtime, res)
        self._g_dispatch = None

# Use the poll based reactor if it is available
try:
    select.poll
    Reactor = PollReactor
except:
    Reactor = SelectReactor

# Interface to Klipper micro-controller code
#
# Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, zlib, logging, math
import serialhdl, msgproto, pins, chelper, clocksync

class error(Exception):
    pass

# Minimum time host needs to get scheduled events queued into mcu
MIN_SCHEDULE_TIME = 0.100
# The maximum number of clock cycles an MCU is expected
# to schedule into the future, due to the protocol and firmware.
MAX_SCHEDULE_TICKS = (1<<31) - 1
# Maximum time all MCUs can internally schedule into the future.
# Directly caused by the limitation of MAX_SCHEDULE_TICKS.
MAX_NOMINAL_DURATION = 3.0

######################################################################
# Command transmit helper classes
######################################################################

# Class to retry sending of a query command until a given response is received
class RetryAsyncCommand:
    TIMEOUT_TIME = 5.0
    RETRY_TIME = 0.500
    def __init__(self, serial, name, oid=None):
        self.serial = serial
        self.name = name
        self.oid = oid
        self.reactor = serial.get_reactor()
        self.completion = self.reactor.completion()
        self.min_query_time = self.reactor.monotonic()
        self.need_response = True
        self.serial.register_response(self.handle_callback, name, oid)
    def handle_callback(self, params):
        if self.need_response and params['#sent_time'] >= self.min_query_time:
            self.need_response = False
            self.reactor.async_complete(self.completion, params)
    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True):
        cmd, = cmds
        self.serial.raw_send_wait_ack(cmd, minclock, reqclock, cmd_queue)
        self.min_query_time = 0.
        timeout_time = query_time = self.reactor.monotonic()
        if retry:
            timeout_time += self.TIMEOUT_TIME
        while 1:
            params = self.completion.wait(query_time + self.RETRY_TIME)
            if params is not None:
                self.serial.register_response(None, self.name, self.oid)
                return params
            query_time = self.reactor.monotonic()
            if query_time > timeout_time:
                self.serial.register_response(None, self.name, self.oid)
                raise serialhdl.error("Timeout on wait for '%s' response"
                                      % (self.name,))
            self.serial.raw_send(cmd, minclock, minclock, cmd_queue)

# Wrapper around query commands
class CommandQueryWrapper:
    def __init__(self, serial, msgformat, respformat, oid=None,
                 cmd_queue=None, is_async=False, error=serialhdl.error):
        self._serial = serial
        self._cmd = serial.get_msgparser().lookup_command(msgformat)
        serial.get_msgparser().lookup_command(respformat)
        self._response = respformat.split()[0]
        self._oid = oid
        self._error = error
        self._xmit_helper = serialhdl.SerialRetryCommand
        if is_async:
            self._xmit_helper = RetryAsyncCommand
        if cmd_queue is None:
            cmd_queue = serial.get_default_command_queue()
        self._cmd_queue = cmd_queue
    def _do_send(self, cmds, minclock, reqclock, retry):
        xh = self._xmit_helper(self._serial, self._response, self._oid)
        reqclock = max(minclock, reqclock)
        try:
            return xh.get_response(cmds, self._cmd_queue, minclock, reqclock,
                                   retry)
        except serialhdl.error as e:
            raise self._error(str(e))
    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        return self._do_send([self._cmd.encode(data)], minclock, reqclock,
                             retry)
    def send_with_preface(self, preface_cmd, preface_data=(), data=(),
                          minclock=0, reqclock=0, retry=True):
        cmds = [preface_cmd._cmd.encode(preface_data), self._cmd.encode(data)]
        return self._do_send(cmds, minclock, reqclock, retry)

# Wrapper around command sending
class CommandWrapper:
    def __init__(self, serial, msgformat, cmd_queue=None, debugoutput=False):
        self._serial = serial
        msgparser = serial.get_msgparser()
        self._cmd = msgparser.lookup_command(msgformat)
        if cmd_queue is None:
            cmd_queue = serial.get_default_command_queue()
        self._cmd_queue = cmd_queue
        self._msgtag = msgparser.lookup_msgid(msgformat) & 0xffffffff
        if debugoutput:
            # Can't use send_wait_ack when in debugging mode
            self.send_wait_ack = self.send
    def send(self, data=(), minclock=0, reqclock=0):
        cmd = self._cmd.encode(data)
        self._serial.raw_send(cmd, minclock, reqclock, self._cmd_queue)
    def send_wait_ack(self, data=(), minclock=0, reqclock=0):
        cmd = self._cmd.encode(data)
        self._serial.raw_send_wait_ack(cmd, minclock, reqclock, self._cmd_queue)
    def get_command_tag(self):
        return self._msgtag


######################################################################
# Wrapper classes for MCU pins
######################################################################

class MCU_trsync:
    REASON_ENDSTOP_HIT = 1
    REASON_HOST_REQUEST = 2
    REASON_PAST_END_TIME = 3
    REASON_COMMS_TIMEOUT = 4
    def __init__(self, mcu, trdispatch):
        self._mcu = mcu
        self._trdispatch = trdispatch
        self._reactor = mcu.get_printer().get_reactor()
        self._steppers = []
        self._trdispatch_mcu = None
        self._oid = mcu.create_oid()
        self._cmd_queue = mcu.alloc_command_queue()
        self._trsync_start_cmd = self._trsync_set_timeout_cmd = None
        self._trsync_trigger_cmd = self._trsync_query_cmd = None
        self._stepper_stop_cmd = None
        self._trigger_completion = None
        self._home_end_clock = None
        mcu.register_config_callback(self._build_config)
        printer = mcu.get_printer()
        printer.register_event_handler("klippy:shutdown", self._shutdown)
    def get_mcu(self):
        return self._mcu
    def get_oid(self):
        return self._oid
    def get_command_queue(self):
        return self._cmd_queue
    def add_stepper(self, stepper):
        if stepper in self._steppers:
            return
        self._steppers.append(stepper)
    def get_steppers(self):
        return list(self._steppers)
    def _build_config(self):
        mcu = self._mcu
        # Setup config
        mcu.add_config_cmd("config_trsync oid=%d" % (self._oid,))
        mcu.add_config_cmd(
            "trsync_start oid=%d report_clock=0 report_ticks=0 expire_reason=0"
            % (self._oid,), on_restart=True)
        # Lookup commands
        self._trsync_start_cmd = mcu.lookup_command(
            "trsync_start oid=%c report_clock=%u report_ticks=%u"
            " expire_reason=%c", cq=self._cmd_queue)
        self._trsync_set_timeout_cmd = mcu.lookup_command(
            "trsync_set_timeout oid=%c clock=%u", cq=self._cmd_queue)
        self._trsync_trigger_cmd = mcu.lookup_command(
            "trsync_trigger oid=%c reason=%c", cq=self._cmd_queue)
        self._trsync_query_cmd = mcu.lookup_query_command(
            "trsync_trigger oid=%c reason=%c",
            "trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u",
            oid=self._oid, cq=self._cmd_queue)
        self._stepper_stop_cmd = mcu.lookup_command(
            "stepper_stop_on_trigger oid=%c trsync_oid=%c", cq=self._cmd_queue)
        # Create trdispatch_mcu object
        set_timeout_tag = mcu.lookup_command(
            "trsync_set_timeout oid=%c clock=%u").get_command_tag()
        trigger_cmd = mcu.lookup_command("trsync_trigger oid=%c reason=%c")
        trigger_tag = trigger_cmd.get_command_tag()
        state_cmd = mcu.lookup_command(
            "trsync_state oid=%c can_trigger=%c trigger_reason=%c clock=%u")
        state_tag = state_cmd.get_command_tag()
        ffi_main, ffi_lib = chelper.get_ffi()
        self._trdispatch_mcu = ffi_main.gc(ffi_lib.trdispatch_mcu_alloc(
            self._trdispatch, mcu._serial.get_serialqueue(), # XXX
            self._cmd_queue, self._oid, set_timeout_tag, trigger_tag,
            state_tag), ffi_lib.free)
    def _shutdown(self):
        tc = self._trigger_completion
        if tc is not None:
            self._trigger_completion = None
            tc.complete(False)
    def _handle_trsync_state(self, params):
        if not params['can_trigger']:
            tc = self._trigger_completion
            if tc is not None:
                self._trigger_completion = None
                reason = params['trigger_reason']
                is_failure = (reason >= self.REASON_COMMS_TIMEOUT)
                self._reactor.async_complete(tc, is_failure)
        elif self._home_end_clock is not None:
            clock = self._mcu.clock32_to_clock64(params['clock'])
            if clock >= self._home_end_clock:
                self._home_end_clock = None
                self._trsync_trigger_cmd.send([self._oid,
                                               self.REASON_PAST_END_TIME])
    def start(self, print_time, report_offset,
              trigger_completion, expire_timeout):
        self._trigger_completion = trigger_completion
        self._home_end_clock = None
        clock = self._mcu.print_time_to_clock(print_time)
        expire_ticks = self._mcu.seconds_to_clock(expire_timeout)
        expire_clock = clock + expire_ticks
        report_ticks = self._mcu.seconds_to_clock(expire_timeout * .3)
        report_clock = clock + int(report_ticks * report_offset + .5)
        min_extend_ticks = int(report_ticks * .8 + .5)
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_mcu_setup(self._trdispatch_mcu, clock, expire_clock,
                                     expire_ticks, min_extend_ticks)
        self._mcu.register_response(self._handle_trsync_state,
                                    "trsync_state", self._oid)
        self._trsync_start_cmd.send([self._oid, report_clock, report_ticks,
                                     self.REASON_COMMS_TIMEOUT],
                                    reqclock=report_clock)
        for s in self._steppers:
            self._stepper_stop_cmd.send([s.get_oid(), self._oid])
        self._trsync_set_timeout_cmd.send([self._oid, expire_clock],
                                          reqclock=expire_clock)
    def set_home_end_time(self, home_end_time):
        self._home_end_clock = self._mcu.print_time_to_clock(home_end_time)
    def stop(self):
        self._mcu.register_response(None, "trsync_state", self._oid)
        self._trigger_completion = None
        if self._mcu.is_fileoutput():
            return self.REASON_ENDSTOP_HIT
        params = self._trsync_query_cmd.send([self._oid,
                                              self.REASON_HOST_REQUEST])
        for s in self._steppers:
            s.note_homing_end()
        return params['trigger_reason']

TRSYNC_TIMEOUT = 0.025
TRSYNC_SINGLE_MCU_TIMEOUT = 0.250

class TriggerDispatch:
    def __init__(self, mcu):
        self._mcu = mcu
        self._trigger_completion = None
        ffi_main, ffi_lib = chelper.get_ffi()
        self._trdispatch = ffi_main.gc(ffi_lib.trdispatch_alloc(), ffi_lib.free)
        self._trsyncs = [MCU_trsync(mcu, self._trdispatch)]
    def get_oid(self):
        return self._trsyncs[0].get_oid()
    def get_command_queue(self):
        return self._trsyncs[0].get_command_queue()
    def add_stepper(self, stepper):
        trsyncs = {trsync.get_mcu(): trsync for trsync in self._trsyncs}
        trsync = trsyncs.get(stepper.get_mcu())
        if trsync is None:
            trsync = MCU_trsync(stepper.get_mcu(), self._trdispatch)
            self._trsyncs.append(trsync)
        trsync.add_stepper(stepper)
        # Check for unsupported multi-mcu shared stepper rails
        sname = stepper.get_name()
        if sname.startswith('stepper_'):
            for ot in self._trsyncs:
                for s in ot.get_steppers():
                    if ot is not trsync and s.get_name().startswith(sname[:9]):
                        cerror = self._mcu.get_printer().config_error
                        raise cerror("Multi-mcu homing not supported on"
                                     " multi-mcu shared axis")
    def get_steppers(self):
        return [s for trsync in self._trsyncs for s in trsync.get_steppers()]
    def start(self, print_time):
        reactor = self._mcu.get_printer().get_reactor()
        self._trigger_completion = reactor.completion()
        expire_timeout = TRSYNC_TIMEOUT
        if len(self._trsyncs) == 1:
            expire_timeout = TRSYNC_SINGLE_MCU_TIMEOUT
        for i, trsync in enumerate(self._trsyncs):
            report_offset = float(i) / len(self._trsyncs)
            trsync.start(print_time, report_offset,
                         self._trigger_completion, expire_timeout)
        etrsync = self._trsyncs[0]
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_start(self._trdispatch, etrsync.REASON_HOST_REQUEST)
        return self._trigger_completion
    def wait_end(self, end_time):
        etrsync = self._trsyncs[0]
        etrsync.set_home_end_time(end_time)
        if self._mcu.is_fileoutput():
            self._trigger_completion.complete(True)
        self._trigger_completion.wait()
    def stop(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        ffi_lib.trdispatch_stop(self._trdispatch)
        res = [trsync.stop() for trsync in self._trsyncs]
        err_res = [r for r in res if r >= MCU_trsync.REASON_COMMS_TIMEOUT]
        if err_res:
            return err_res[0]
        return res[0]

class MCU_endstop:
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._pin = pin_params['pin']
        self._pullup = pin_params['pullup']
        self._invert = pin_params['invert']
        self._oid = self._mcu.create_oid()
        self._home_cmd = self._query_cmd = None
        self._mcu.register_config_callback(self._build_config)
        self._rest_ticks = 0
        self._dispatch = TriggerDispatch(mcu)
    def get_mcu(self):
        return self._mcu
    def add_stepper(self, stepper):
        self._dispatch.add_stepper(stepper)
    def get_steppers(self):
        return self._dispatch.get_steppers()
    def _build_config(self):
        # Setup config
        self._mcu.add_config_cmd("config_endstop oid=%d pin=%s pull_up=%d"
                                 % (self._oid, self._pin, self._pullup))
        self._mcu.add_config_cmd(
            "endstop_home oid=%d clock=0 sample_ticks=0 sample_count=0"
            " rest_ticks=0 pin_value=0 trsync_oid=0 trigger_reason=0"
            % (self._oid,), on_restart=True)
        # Lookup commands
        cmd_queue = self._dispatch.get_command_queue()
        self._home_cmd = self._mcu.lookup_command(
            "endstop_home oid=%c clock=%u sample_ticks=%u sample_count=%c"
            " rest_ticks=%u pin_value=%c trsync_oid=%c trigger_reason=%c",
            cq=cmd_queue)
        self._query_cmd = self._mcu.lookup_query_command(
            "endstop_query_state oid=%c",
            "endstop_state oid=%c homing=%c next_clock=%u pin_value=%c",
            oid=self._oid, cq=cmd_queue)
    def home_start(self, print_time, sample_time, sample_count, rest_time,
                   triggered=True):
        clock = self._mcu.print_time_to_clock(print_time)
        rest_ticks = self._mcu.print_time_to_clock(print_time+rest_time) - clock
        self._rest_ticks = rest_ticks
        trigger_completion = self._dispatch.start(print_time)
        self._home_cmd.send(
            [self._oid, clock, self._mcu.seconds_to_clock(sample_time),
             sample_count, rest_ticks, triggered ^ self._invert,
             self._dispatch.get_oid(), MCU_trsync.REASON_ENDSTOP_HIT],
            reqclock=clock)
        return trigger_completion
    def home_wait(self, home_end_time):
        self._dispatch.wait_end(home_end_time)
        self._home_cmd.send([self._oid, 0, 0, 0, 0, 0, 0, 0])
        res = self._dispatch.stop()
        if res >= MCU_trsync.REASON_COMMS_TIMEOUT:
            cmderr = self._mcu.get_printer().command_error
            raise cmderr("Communication timeout during homing")
        if res != MCU_trsync.REASON_ENDSTOP_HIT:
            return 0.
        if self._mcu.is_fileoutput():
            return home_end_time
        params = self._query_cmd.send([self._oid])
        next_clock = self._mcu.clock32_to_clock64(params['next_clock'])
        return self._mcu.clock_to_print_time(next_clock - self._rest_ticks)
    def query_endstop(self, print_time):
        clock = self._mcu.print_time_to_clock(print_time)
        if self._mcu.is_fileoutput():
            return 0
        params = self._query_cmd.send([self._oid], minclock=clock)
        return params['pin_value'] ^ self._invert

class MCU_digital_out:
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._oid = None
        self._mcu.register_config_callback(self._build_config)
        self._pin = pin_params['pin']
        self._invert = pin_params['invert']
        self._start_value = self._shutdown_value = self._invert
        self._max_duration = 2.
        self._last_clock = 0
        self._set_cmd = None
    def get_mcu(self):
        return self._mcu
    def setup_max_duration(self, max_duration):
        self._max_duration = max_duration
    def setup_start_value(self, start_value, shutdown_value):
        self._start_value = (not not start_value) ^ self._invert
        self._shutdown_value = (not not shutdown_value) ^ self._invert
    def _build_config(self):
        if self._max_duration and self._start_value != self._shutdown_value:
            raise pins.error("Pin with max duration must have start"
                             " value equal to shutdown value")
        mdur_ticks = self._mcu.seconds_to_clock(self._max_duration)
        if mdur_ticks > MAX_SCHEDULE_TICKS:
            raise pins.error("Digital pin max duration too large")
        self._mcu.request_move_queue_slot()
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd(
            "config_digital_out oid=%d pin=%s value=%d default_value=%d"
            " max_duration=%d" % (self._oid, self._pin, self._start_value,
                                  self._shutdown_value, mdur_ticks))
        self._mcu.add_config_cmd("update_digital_out oid=%d value=%d"
                                 % (self._oid, self._start_value),
                                 on_restart=True)
        cmd_queue = self._mcu.alloc_command_queue()
        self._set_cmd = self._mcu.lookup_command(
            "queue_digital_out oid=%c clock=%u on_ticks=%u", cq=cmd_queue)
    def set_digital(self, print_time, value):
        clock = self._mcu.print_time_to_clock(print_time)
        self._set_cmd.send([self._oid, clock, (not not value) ^ self._invert],
                           minclock=self._last_clock, reqclock=clock)
        self._last_clock = clock

class MCU_pwm:
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._hardware_pwm = False
        self._cycle_time = 0.100
        self._max_duration = 2.
        self._oid = None
        self._mcu.register_config_callback(self._build_config)
        self._pin = pin_params['pin']
        self._invert = pin_params['invert']
        self._start_value = self._shutdown_value = float(self._invert)
        self._last_clock = 0
        self._pwm_max = 0.
        self._set_cmd = None
    def get_mcu(self):
        return self._mcu
    def setup_max_duration(self, max_duration):
        self._max_duration = max_duration
    def setup_cycle_time(self, cycle_time, hardware_pwm=False):
        self._cycle_time = cycle_time
        self._hardware_pwm = hardware_pwm
    def setup_start_value(self, start_value, shutdown_value):
        if self._invert:
            start_value = 1. - start_value
            shutdown_value = 1. - shutdown_value
        self._start_value = max(0., min(1., start_value))
        self._shutdown_value = max(0., min(1., shutdown_value))
    def _build_config(self):
        if self._max_duration and self._start_value != self._shutdown_value:
            raise pins.error("Pin with max duration must have start"
                             " value equal to shutdown value")
        cmd_queue = self._mcu.alloc_command_queue()
        curtime = self._mcu.get_printer().get_reactor().monotonic()
        printtime = self._mcu.estimated_print_time(curtime)
        self._last_clock = self._mcu.print_time_to_clock(printtime + 0.200)
        cycle_ticks = self._mcu.seconds_to_clock(self._cycle_time)
        mdur_ticks = self._mcu.seconds_to_clock(self._max_duration)
        if mdur_ticks > MAX_SCHEDULE_TICKS:
            raise pins.error("PWM pin max duration too large")
        if self._hardware_pwm:
            self._pwm_max = self._mcu.get_constant_float("PWM_MAX")
            self._mcu.request_move_queue_slot()
            self._oid = self._mcu.create_oid()
            self._mcu.add_config_cmd(
                "config_pwm_out oid=%d pin=%s cycle_ticks=%d value=%d"
                " default_value=%d max_duration=%d"
                % (self._oid, self._pin, cycle_ticks,
                   self._start_value * self._pwm_max,
                   self._shutdown_value * self._pwm_max, mdur_ticks))
            svalue = int(self._start_value * self._pwm_max + 0.5)
            self._mcu.add_config_cmd("queue_pwm_out oid=%d clock=%d value=%d"
                                     % (self._oid, self._last_clock, svalue),
                                     on_restart=True)
            self._set_cmd = self._mcu.lookup_command(
                "queue_pwm_out oid=%c clock=%u value=%hu", cq=cmd_queue)
            return
        # Software PWM
        if self._shutdown_value not in [0., 1.]:
            raise pins.error("shutdown value must be 0.0 or 1.0 on soft pwm")
        if cycle_ticks > MAX_SCHEDULE_TICKS:
            raise pins.error("PWM pin cycle time too large")
        self._mcu.request_move_queue_slot()
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd(
            "config_digital_out oid=%d pin=%s value=%d"
            " default_value=%d max_duration=%d"
            % (self._oid, self._pin, self._start_value >= 1.0,
               self._shutdown_value >= 0.5, mdur_ticks))
        self._mcu.add_config_cmd(
            "set_digital_out_pwm_cycle oid=%d cycle_ticks=%d"
            % (self._oid, cycle_ticks))
        self._pwm_max = float(cycle_ticks)
        svalue = int(self._start_value * cycle_ticks + 0.5)
        self._mcu.add_config_cmd(
            "queue_digital_out oid=%d clock=%d on_ticks=%d"
            % (self._oid, self._last_clock, svalue), is_init=True)
        self._set_cmd = self._mcu.lookup_command(
            "queue_digital_out oid=%c clock=%u on_ticks=%u", cq=cmd_queue)
    def set_pwm(self, print_time, value):
        if self._invert:
            value = 1. - value
        v = int(max(0., min(1., value)) * self._pwm_max + 0.5)
        clock = self._mcu.print_time_to_clock(print_time)
        self._set_cmd.send([self._oid, clock, v],
                           minclock=self._last_clock, reqclock=clock)
        self._last_clock = clock

class MCU_adc:
    def __init__(self, mcu, pin_params):
        self._mcu = mcu
        self._pin = pin_params['pin']
        self._min_sample = self._max_sample = 0.
        self._sample_time = self._report_time = 0.
        self._sample_count = self._range_check_count = 0
        self._report_clock = 0
        self._last_state = (0., 0.)
        self._oid = self._callback = None
        self._mcu.register_config_callback(self._build_config)
        self._inv_max_adc = 0.
    def get_mcu(self):
        return self._mcu
    def setup_adc_sample(self, sample_time, sample_count,
                         minval=0., maxval=1., range_check_count=0):
        self._sample_time = sample_time
        self._sample_count = sample_count
        self._min_sample = minval
        self._max_sample = maxval
        self._range_check_count = range_check_count
    def setup_adc_callback(self, report_time, callback):
        self._report_time = report_time
        self._callback = callback
    def get_last_value(self):
        return self._last_state
    def _build_config(self):
        if not self._sample_count:
            return
        self._oid = self._mcu.create_oid()
        self._mcu.add_config_cmd("config_analog_in oid=%d pin=%s" % (
            self._oid, self._pin))
        clock = self._mcu.get_query_slot(self._oid)
        sample_ticks = self._mcu.seconds_to_clock(self._sample_time)
        mcu_adc_max = self._mcu.get_constant_float("ADC_MAX")
        max_adc = self._sample_count * mcu_adc_max
        self._inv_max_adc = 1.0 / max_adc
        self._report_clock = self._mcu.seconds_to_clock(self._report_time)
        min_sample = max(0, min(0xffff, int(self._min_sample * max_adc)))
        max_sample = max(0, min(0xffff, int(
            math.ceil(self._max_sample * max_adc))))
        self._mcu.add_config_cmd(
            "query_analog_in oid=%d clock=%d sample_ticks=%d sample_count=%d"
            " rest_ticks=%d min_value=%d max_value=%d range_check_count=%d" % (
                self._oid, clock, sample_ticks, self._sample_count,
                self._report_clock, min_sample, max_sample,
                self._range_check_count), is_init=True)
        self._mcu.register_response(self._handle_analog_in_state,
                                    "analog_in_state", self._oid)
    def _handle_analog_in_state(self, params):
        last_value = params['value'] * self._inv_max_adc
        next_clock = self._mcu.clock32_to_clock64(params['next_clock'])
        last_read_clock = next_clock - self._report_clock
        last_read_time = self._mcu.clock_to_print_time(last_read_clock)
        self._last_state = (last_value, last_read_time)
        if self._callback is not None:
            self._callback(last_read_time, last_value)


######################################################################
# Main MCU class (and its helper classes)
######################################################################

# Support for restarting a micro-controller
class MCURestartHelper:
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._conn_helper = conn_helper
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        # Restart tracking
        restart_methods = [None, 'arduino', 'cheetah', 'command', 'rpi_usb']
        self._restart_method = 'command'
        serialport, baud = conn_helper.get_serialport()
        if baud:
            self._restart_method = config.getchoice('restart_method',
                                                    restart_methods, None)
        self._reset_cmd = self._config_reset_cmd = None
        self._is_mcu_bridge = False
        # Register handlers
        printer.register_event_handler("klippy:firmware_restart",
                                       self._firmware_restart)
        printer.register_event_handler("klippy:disconnect", self._disconnect)
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
    # Connection phase
    def _check_restart(self, reason):
        start_reason = self._printer.get_start_args().get("start_reason")
        if start_reason == 'firmware_restart':
            return
        logging.info("Attempting automated MCU '%s' restart: %s",
                     self._name, reason)
        self._printer.request_exit('firmware_restart')
        self._reactor.pause(self._reactor.monotonic() + 2.000)
        raise error("Attempt MCU '%s' restart failed" % (self._name,))
    def check_restart_on_crc_mismatch(self):
        self._check_restart("CRC mismatch")
    def check_restart_on_send_config(self):
        if self._restart_method == 'rpi_usb':
            # Only configure mcu after usb power reset
            self._check_restart("full reset before config")
    def check_restart_on_attach(self):
        resmeth = self._restart_method
        serialport, baud = self._conn_helper.get_serialport()
        if resmeth == 'rpi_usb' and not os.path.exists(serialport):
            # Try toggling usb power
            self._check_restart("enable power")
    def lookup_attach_uart_rts(self):
        # Cheetah boards require RTS to be deasserted
        # else a reset will trigger the built-in bootloader.
        return (self._restart_method != "cheetah")
    def _mcu_identify(self):
        self._reset_cmd = self._mcu.try_lookup_command("reset")
        self._config_reset_cmd = self._mcu.try_lookup_command("config_reset")
        ext_only = self._reset_cmd is None and self._config_reset_cmd is None
        msgparser = self._serial.get_msgparser()
        mbaud = msgparser.get_constant('SERIAL_BAUD', None)
        if self._restart_method is None and mbaud is None and not ext_only:
            self._restart_method = 'command'
        if msgparser.get_constant('CANBUS_BRIDGE', 0):
            self._is_mcu_bridge = True
            self._printer.register_event_handler("klippy:firmware_restart",
                                                 self._firmware_restart_bridge)
    def _disconnect(self):
        self._serial.disconnect()
    def _restart_arduino(self):
        logging.info("Attempting MCU '%s' reset", self._name)
        self._disconnect()
        serialport, baud = self._conn_helper.get_serialport()
        serialhdl.arduino_reset(serialport, self._reactor)
    def _restart_cheetah(self):
        logging.info("Attempting MCU '%s' Cheetah-style reset", self._name)
        self._disconnect()
        serialport, baud = self._conn_helper.get_serialport()
        serialhdl.cheetah_reset(serialport, self._reactor)
    def _restart_via_command(self):
        if ((self._reset_cmd is None and self._config_reset_cmd is None)
            or not self._clocksync.is_active()):
            logging.info("Unable to issue reset command on MCU '%s'",
                         self._name)
            return
        if self._reset_cmd is None:
            # Attempt reset via config_reset command
            logging.info("Attempting MCU '%s' config_reset command", self._name)
            self._conn_helper.force_local_shutdown()
            self._reactor.pause(self._reactor.monotonic() + 0.015)
            self._config_reset_cmd.send()
        else:
            # Attempt reset via reset command
            logging.info("Attempting MCU '%s' reset command", self._name)
            self._reset_cmd.send()
        self._reactor.pause(self._reactor.monotonic() + 0.015)
        self._disconnect()
    def _restart_rpi_usb(self):
        logging.info("Attempting MCU '%s' reset via rpi usb power", self._name)
        self._disconnect()
        chelper.run_hub_ctrl(0)
        self._reactor.pause(self._reactor.monotonic() + 2.)
        chelper.run_hub_ctrl(1)
    def _firmware_restart(self, force=False):
        if self._is_mcu_bridge and not force:
            return
        if self._restart_method == 'rpi_usb':
            self._restart_rpi_usb()
        elif self._restart_method == 'command':
            self._restart_via_command()
        elif self._restart_method == 'cheetah':
            self._restart_cheetah()
        else:
            self._restart_arduino()
    def _firmware_restart_bridge(self):
        self._firmware_restart(True)

# Low-level mcu connection management helper
class MCUConnectHelper:
    def __init__(self, config, mcu, clocksync):
        self._mcu = mcu
        self._clocksync = clocksync
        self._printer = printer = config.get_printer()
        self._reactor = printer.get_reactor()
        self._name = name = mcu.get_name()
        # Serial port
        self._serial = serialhdl.SerialReader(self._reactor, mcu_name=name)
        self._baud = 0
        self._canbus_iface = None
        canbus_uuid = config.get('canbus_uuid', None)
        if canbus_uuid is not None:
            self._serialport = canbus_uuid
            self._canbus_iface = config.get('canbus_interface', 'can0')
            cbid = self._printer.load_object(config, 'canbus_ids')
            cbid.add_uuid(config, canbus_uuid, self._canbus_iface)
            self._printer.load_object(config, 'canbus_stats %s' % (name,))
        else:
            self._serialport = config.get('serial')
            if not (self._serialport.startswith("/dev/rpmsg_")
                    or self._serialport.startswith("/tmp/klipper_host_")):
                self._baud = config.getint('baud', 250000, minval=2400)
        # Shutdown tracking
        self._emergency_stop_cmd = None
        self._is_shutdown = self._is_timeout = False
        self._shutdown_msg = ""
        # Register handlers
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
        self._restart_helper = MCURestartHelper(config, self)
        printer.register_event_handler("klippy:shutdown", self._shutdown)
        printer.register_event_handler("klippy:analyze_shutdown",
                                       self._analyze_shutdown)
    def get_mcu(self):
        return self._mcu
    def get_serial(self):
        return self._serial
    def get_clocksync(self):
        return self._clocksync
    def get_serialport(self):
        return self._serialport, self._baud
    def get_restart_helper(self):
        return self._restart_helper
    def _handle_shutdown(self, params):
        if self._is_shutdown:
            return
        self._is_shutdown = True
        self._shutdown_msg = msg = params['static_string_id']
        shutdown_clock = params.get("clock")
        if shutdown_clock is not None:
            shutdown_clock = self._mcu.clock32_to_clock64(shutdown_clock)
        event_type = params['#name']
        self._printer.invoke_async_shutdown(
            "MCU shutdown", {"reason": msg, "mcu": self._name,
                             "event_type": event_type,
                             "shutdown_clock": shutdown_clock})
    def _handle_starting(self, params):
        if not self._is_shutdown:
            self._printer.invoke_async_shutdown("MCU '%s' spontaneous restart"
                                                % (self._name,))
    def log_info(self):
        msgparser = self._serial.get_msgparser()
        message_count = len(msgparser.get_messages())
        version, build_versions = msgparser.get_version_info()
        log_info = [
            "Loaded MCU '%s' %d commands (%s / %s)"
            % (self._name, message_count, version, build_versions),
            "MCU '%s' config: %s" % (self._name, " ".join(
                ["%s=%s" % (k, v)
                 for k, v in msgparser.get_constants().items()]))]
        return "\n".join(log_info)
    def _attach_file(self):
        # In a debugging mode.  Open debug output file and read data dictionary
        start_args = self._printer.get_start_args()
        if self._name == 'mcu':
            out_fname = start_args.get('debugoutput')
            dict_fname = start_args.get('dictionary')
        else:
            out_fname = start_args.get('debugoutput') + "-" + self._name
            dict_fname = start_args.get('dictionary_' + self._name)
        outfile = open(out_fname, 'wb')
        dfile = open(dict_fname, 'rb')
        dict_data = dfile.read()
        dfile.close()
        self._serial.connect_file(outfile, dict_data)
        self._clocksync.connect_file(self._serial)
    def _attach(self):
        self._restart_helper.check_restart_on_attach()
        try:
            if self._canbus_iface is not None:
                cbid = self._printer.lookup_object('canbus_ids')
                nodeid = cbid.get_nodeid(self._serialport)
                self._serial.connect_canbus(self._serialport, nodeid,
                                            self._canbus_iface)
            elif self._baud:
                rts = self._restart_helper.lookup_attach_uart_rts()
                self._serial.connect_uart(self._serialport, self._baud, rts)
            else:
                self._serial.connect_pipe(self._serialport)
            self._clocksync.connect(self._serial)
        except serialhdl.error as e:
            raise error(str(e))
    def _mcu_identify(self):
        if self._mcu.is_fileoutput():
            self._attach_file()
        else:
            self._attach()
        logging.info(self.log_info())
        # Setup shutdown handling
        self._emergency_stop_cmd = self._mcu.lookup_command("emergency_stop")
        self._mcu.register_response(self._handle_shutdown, 'shutdown')
        self._mcu.register_response(self._handle_shutdown, 'is_shutdown')
        self._mcu.register_response(self._handle_starting, 'starting')
    def _analyze_shutdown(self, msg, details):
        if self._mcu.is_fileoutput():
            return
        logging.info("MCU '%s' shutdown: %s\n%s\n%s", self._name,
                     self._shutdown_msg, self._clocksync.dump_debug(),
                     self._serial.dump_debug())
    def _shutdown(self, force=False):
        if (self._emergency_stop_cmd is None
            or (self._is_shutdown and not force)):
            return
        self._emergency_stop_cmd.send()
    def force_local_shutdown(self):
        self._is_shutdown = True
        self._shutdown(force=True)
    def check_timeout(self, eventtime):
        if (self._clocksync.is_active() or self._mcu.is_fileoutput()
            or self._is_timeout):
            return
        self._is_timeout = True
        logging.info("Timeout with MCU '%s' (eventtime=%f)",
                     self._name, eventtime)
        self._printer.invoke_shutdown("Lost communication with MCU '%s'" % (
            self._name,))
    def is_shutdown(self):
        return self._is_shutdown
    def get_shutdown_msg(self):
        return self._shutdown_msg

# Handle statistics reporting
class MCUStatsHelper:
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        # Statistics tracking
        self._mcu_freq = 0.
        self._get_status_info = {}
        self._stats_sumsq_base = 0.
        self._mcu_tick_avg = 0.
        self._mcu_tick_stddev = 0.
        self._mcu_tick_awake = 0.
        # Register handlers
        printer.register_event_handler("klippy:ready", self._ready)
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
    def _handle_mcu_stats(self, params):
        count = params['count']
        tick_sum = params['sum']
        c = 1.0 / (count * self._mcu_freq)
        self._mcu_tick_avg = tick_sum * c
        tick_sumsq = params['sumsq'] * self._stats_sumsq_base
        diff = count*tick_sumsq - tick_sum**2
        self._mcu_tick_stddev = c * math.sqrt(max(0., diff))
        self._mcu_tick_awake = tick_sum / self._mcu_freq
    def _mcu_identify(self):
        self._mcu_freq = self._mcu.get_constant_float('CLOCK_FREQ')
        self._stats_sumsq_base = self._mcu.get_constant_float(
            'STATS_SUMSQ_BASE')
        msgparser = self._serial.get_msgparser()
        version, build_versions = msgparser.get_version_info()
        self._get_status_info['mcu_version'] = version
        self._get_status_info['mcu_build_versions'] = build_versions
        self._get_status_info['mcu_constants'] = msgparser.get_constants()
        self._mcu.register_response(self._handle_mcu_stats, 'stats')
    def _ready(self):
        if self._mcu.is_fileoutput():
            return
        # Check that reported mcu frequency is in range
        mcu_freq = self._mcu_freq
        systime = self._reactor.monotonic()
        get_clock = self._clocksync.get_clock
        calc_freq = get_clock(systime + 1) - get_clock(systime)
        freq_diff = abs(mcu_freq - calc_freq)
        mcu_freq_mhz = int(mcu_freq / 1000000. + 0.5)
        calc_freq_mhz = int(calc_freq / 1000000. + 0.5)
        if freq_diff > mcu_freq*0.01 and mcu_freq_mhz != calc_freq_mhz:
            pconfig = self._printer.lookup_object('configfile')
            msg = ("MCU '%s' configured for %dMhz but running at %dMhz!"
                    % (self._name, mcu_freq_mhz, calc_freq_mhz))
            pconfig.runtime_warning(msg)
    def get_status(self, eventtime=None):
        return dict(self._get_status_info)
    def stats(self, eventtime):
        load = "mcu_awake=%.03f mcu_task_avg=%.06f mcu_task_stddev=%.06f" % (
            self._mcu_tick_awake, self._mcu_tick_avg, self._mcu_tick_stddev)
        stats = ' '.join([load, self._serial.stats(eventtime),
                          self._clocksync.stats(eventtime)])
        parts = [s.split('=', 1) for s in stats.split()]
        last_stats = {k:(float(v) if '.' in v else int(v)) for k, v in parts}
        self._get_status_info['last_stats'] = last_stats
        return False, '%s: %s' % (self._name, stats)

# Handle process of configuring an mcu
class MCUConfigHelper:
    def __init__(self, config, conn_helper):
        self._printer = printer = config.get_printer()
        self._conn_helper = conn_helper
        self._mcu = mcu = conn_helper.get_mcu()
        self._serial = conn_helper.get_serial()
        self._clocksync = conn_helper.get_clocksync()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        # Configuration tracking
        self._oid_count = 0
        self._config_callbacks = []
        self._config_cmds = []
        self._restart_cmds = []
        self._init_cmds = []
        self._mcu_freq = 0.
        self._reserved_move_slots = 0
        # Register handlers
        printer.lookup_object('pins').register_chip(self._name, mcu)
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
        printer.register_event_handler("klippy:connect", self._connect)
    def _send_config(self, prev_crc):
        # Build config commands
        for cb in self._config_callbacks:
            cb()
        self._config_cmds.insert(0, "allocate_oids count=%d"
                                 % (self._oid_count,))
        # Resolve pin names
        ppins = self._printer.lookup_object('pins')
        pin_resolver = ppins.get_pin_resolver(self._name)
        for cmdlist in (self._config_cmds, self._restart_cmds, self._init_cmds):
            for i, cmd in enumerate(cmdlist):
                cmdlist[i] = pin_resolver.update_command(cmd)
        # Calculate config CRC
        encoded_config = '\n'.join(self._config_cmds).encode()
        config_crc = zlib.crc32(encoded_config) & 0xffffffff
        self.add_config_cmd("finalize_config crc=%d" % (config_crc,))
        if prev_crc is not None and config_crc != prev_crc:
            restart_helper = self._conn_helper.get_restart_helper()
            restart_helper.check_restart_on_crc_mismatch()
            raise error("MCU '%s' CRC does not match config" % (self._name,))
        # Transmit config messages (if needed)
        try:
            if prev_crc is None:
                logging.info("Sending MCU '%s' printer configuration...",
                             self._name)
                for c in self._config_cmds:
                    self._serial.send(c)
            else:
                for c in self._restart_cmds:
                    self._serial.send(c)
            # Transmit init messages
            for c in self._init_cmds:
                self._serial.send(c)
        except msgproto.enumeration_error as e:
            enum_name, enum_value = e.get_enum_params()
            if enum_name == 'pin':
                # Raise pin name errors as a config error (not a protocol error)
                raise self._printer.config_error(
                    "Pin '%s' is not a valid pin name on mcu '%s'"
                    % (enum_value, self._name))
            raise
    def _send_get_config(self):
        get_config_cmd = self._mcu.lookup_query_command(
            "get_config",
            "config is_config=%c crc=%u is_shutdown=%c move_count=%hu")
        if self._mcu.is_fileoutput():
            return { 'is_config': 0, 'move_count': 500, 'crc': 0 }
        config_params = get_config_cmd.send()
        if self._conn_helper.is_shutdown():
            raise error("MCU '%s' error during config: %s" % (
                self._name, self._conn_helper.get_shutdown_msg()))
        if config_params['is_shutdown']:
            raise error("Can not update MCU '%s' config as it is shutdown" % (
                self._name,))
        return config_params
    def _connect(self):
        config_params = self._send_get_config()
        if not config_params['is_config']:
            restart_helper = self._conn_helper.get_restart_helper()
            restart_helper.check_restart_on_send_config()
            # Not configured - send config and issue get_config again
            self._send_config(None)
            config_params = self._send_get_config()
            if not config_params['is_config'] and not self._mcu.is_fileoutput():
                raise error("Unable to configure MCU '%s'" % (self._name,))
        else:
            start_reason = self._printer.get_start_args().get("start_reason")
            if start_reason == 'firmware_restart':
                raise error("Failed automated reset of MCU '%s'"
                            % (self._name,))
            # Already configured - send init commands
            self._send_config(config_params['crc'])
        # Setup steppersync with the move_count returned by get_config
        move_count = config_params['move_count']
        if move_count < self._reserved_move_slots:
            raise error("Too few moves available on MCU '%s'" % (self._name,))
        ss_move_count = move_count - self._reserved_move_slots
        motion_queuing = self._printer.lookup_object('motion_queuing')
        motion_queuing.setup_mcu_movequeue(
            self._mcu, self._serial.get_serialqueue(), ss_move_count)
        # Log config information
        move_msg = "Configured MCU '%s' (%d moves)" % (self._name, move_count)
        logging.info(move_msg)
        log_info = self._conn_helper.log_info() + "\n" + move_msg
        self._printer.set_rollover_info(self._name, log_info, log=False)
    def _mcu_identify(self):
        self._mcu_freq = self._mcu.get_constant_float('CLOCK_FREQ')
        ppins = self._printer.lookup_object('pins')
        pin_resolver = ppins.get_pin_resolver(self._name)
        for cname, value in self._mcu.get_constants().items():
            if cname.startswith("RESERVE_PINS_"):
                for pin in value.split(','):
                    pin_resolver.reserve_pin(pin, cname[13:])
        if MAX_NOMINAL_DURATION * self._mcu_freq > MAX_SCHEDULE_TICKS:
            max_possible = MAX_SCHEDULE_TICKS * 1 / self._mcu_freq
            raise error("Too high clock speed for MCU '%s'"
                        " to be able to resolve a maximum nominal duration"
                        " of %ds. Max possible duration: %ds"
                        % (self._name, MAX_NOMINAL_DURATION, max_possible))
    # Config creation helpers
    def setup_pin(self, pin_type, pin_params):
        pcs = {'endstop': MCU_endstop,
               'digital_out': MCU_digital_out, 'pwm': MCU_pwm, 'adc': MCU_adc}
        if pin_type not in pcs:
            raise pins.error("pin type %s not supported on mcu" % (pin_type,))
        return pcs[pin_type](self._mcu, pin_params)
    def create_oid(self):
        self._oid_count += 1
        return self._oid_count - 1
    def register_config_callback(self, cb):
        self._config_callbacks.append(cb)
    def add_config_cmd(self, cmd, is_init=False, on_restart=False):
        if is_init:
            self._init_cmds.append(cmd)
        elif on_restart:
            self._restart_cmds.append(cmd)
        else:
            self._config_cmds.append(cmd)
    def get_query_slot(self, oid):
        slot = self.seconds_to_clock(oid * .01)
        t = int(self._mcu.estimated_print_time(self._reactor.monotonic()) + 1.5)
        return self._mcu.print_time_to_clock(t) + slot
    def seconds_to_clock(self, time):
        return int(time * self._mcu_freq)
    def request_move_queue_slot(self):
        self._reserved_move_slots += 1

# Main MCU class
class MCU:
    error = error
    def __init__(self, config, clocksync):
        self._printer = printer = config.get_printer()
        self._clocksync = clocksync
        self._name = config.get_name()
        if self._name.startswith('mcu '):
            self._name = self._name[4:]
        # Low-level connection and helpers
        self._conn_helper = MCUConnectHelper(config, self, clocksync)
        self._serial = self._conn_helper.get_serial()
        self._config_helper = MCUConfigHelper(self, self._conn_helper)
        self._stats_helper = MCUStatsHelper(self, self._conn_helper)
        printer.load_object(config, "error_mcu")
        # Alter time reporting when debugging
        if self.is_fileoutput():
            def dummy_estimated_print_time(eventtime):
                return 0.
            self.estimated_print_time = dummy_estimated_print_time
    def get_name(self):
        return self._name
    def get_printer(self):
        return self._printer
    def is_fileoutput(self):
        return self._printer.get_start_args().get('debugoutput') is not None
    # MCU Configuration wrappers
    def setup_pin(self, pin_type, pin_params):
        return self._config_helper.setup_pin(pin_type, pin_params)
    def create_oid(self):
        return self._config_helper.create_oid()
    def register_config_callback(self, cb):
        self._config_helper.register_config_callback(cb)
    def add_config_cmd(self, cmd, is_init=False, on_restart=False):
        self._config_helper.add_config_cmd(cmd, is_init, on_restart)
    def request_move_queue_slot(self):
        self._config_helper.request_move_queue_slot()
    def get_query_slot(self, oid):
        return self._config_helper.get_query_slot(oid)
    def seconds_to_clock(self, time):
        return self._config_helper.seconds_to_clock(time)
    # Command Handler helpers
    def min_schedule_time(self):
        return MIN_SCHEDULE_TIME
    def max_nominal_duration(self):
        return MAX_NOMINAL_DURATION
    def lookup_command(self, msgformat, cq=None):
        return CommandWrapper(self._serial, msgformat, cq,
                              debugoutput=self.is_fileoutput())
    def lookup_query_command(self, msgformat, respformat, oid=None,
                             cq=None, is_async=False):
        return CommandQueryWrapper(self._serial, msgformat, respformat, oid,
                                   cq, is_async, self._printer.command_error)
    def try_lookup_command(self, msgformat):
        try:
            return self.lookup_command(msgformat)
        except self._serial.get_msgparser().error as e:
            return None
    # SerialHdl wrappers
    def register_response(self, cb, msg, oid=None):
        self._serial.register_response(cb, msg, oid)
    def alloc_command_queue(self):
        return self._serial.alloc_command_queue()
    # MsgParser wrappers
    def get_enumerations(self):
        return self._serial.get_msgparser().get_enumerations()
    def get_constants(self):
        return self._serial.get_msgparser().get_constants()
    def get_constant_float(self, name):
        return self._serial.get_msgparser().get_constant_float(name)
    # ClockSync wrappers
    def print_time_to_clock(self, print_time):
        return self._clocksync.print_time_to_clock(print_time)
    def clock_to_print_time(self, clock):
        return self._clocksync.clock_to_print_time(clock)
    def estimated_print_time(self, eventtime):
        return self._clocksync.estimated_print_time(eventtime)
    def clock32_to_clock64(self, clock32):
        return self._clocksync.clock32_to_clock64(clock32)
    def calibrate_clock(self, print_time, eventtime):
        offset, freq = self._clocksync.calibrate_clock(print_time, eventtime)
        self._conn_helper.check_timeout(eventtime)
        return offset, freq
    # Statistics wrappers
    def get_status(self, eventtime=None):
        return self._stats_helper.get_status(eventtime)
    def stats(self, eventtime):
        return self._stats_helper.stats(eventtime)

def add_printer_objects(config):
    printer = config.get_printer()
    reactor = printer.get_reactor()
    mainsync = clocksync.ClockSync(reactor)
    printer.add_object('mcu', MCU(config.getsection('mcu'), mainsync))
    for s in config.get_prefix_sections('mcu '):
        printer.add_object(s.section, MCU(
            s, clocksync.SecondarySync(reactor, mainsync)))

def get_printer_mcu(printer, name):
    if name == 'mcu':
        return printer.lookup_object(name)
    return printer.lookup_object('mcu ' + name)

# Helper code for low-level motion queuing and flushing
#
# Copyright (C) 2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import chelper

BGFLUSH_LOW_TIME = 0.200
BGFLUSH_HIGH_TIME = 0.400
BGFLUSH_SG_LOW_TIME = 0.450
BGFLUSH_SG_HIGH_TIME = 0.700
BGFLUSH_EXTRA_TIME = 0.250

MOVE_HISTORY_EXPIRE = 30.
MIN_KIN_TIME = 0.100
STEPCOMPRESS_FLUSH_TIME = 0.050
SDS_CHECK_TIME = 0.001 # step+dir+step filter in stepcompress.c

DRIP_SEGMENT_TIME = 0.050
DRIP_TIME = 0.100

class PrinterMotionQueuing:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        # C trapq tracking
        self.trapqs = []
        ffi_main, ffi_lib = chelper.get_ffi()
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves
        # C steppersync tracking
        self.steppersyncmgr = ffi_main.gc(ffi_lib.steppersyncmgr_alloc(),
                                          ffi_lib.steppersyncmgr_free)
        self.syncemitters = []
        self.steppersyncs = []
        self.steppersyncmgr_gen_steps = ffi_lib.steppersyncmgr_gen_steps
        # History expiration
        self.clear_history_time = 0.
        # Flush notification callbacks
        self.flush_callbacks = []
        # Kinematic step generation scan window time tracking
        self.kin_flush_delay = SDS_CHECK_TIME
        # MCU tracking
        self.all_mcus = [m for n, m in printer.lookup_objects(module='mcu')]
        self.mcu = self.all_mcus[0]
        self.can_pause = True
        if self.mcu.is_fileoutput():
            self.can_pause = False
        # Flush tracking
        flush_handler = self._flush_handler
        if not self.can_pause:
            flush_handler = self._flush_handler_debug
        self.flush_timer = self.reactor.register_timer(flush_handler)
        self.do_kick_flush_timer = True
        self.last_flush_time = self.last_step_gen_time = 0.
        self.need_flush_time = self.need_step_gen_time = 0.
        # "Drip" timing (for homing and probing moves)
        self.drip_start_times = []
        # Register handlers
        printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
    # C trapq tracking
    def allocate_trapq(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
        self.trapqs.append(trapq)
        return trapq
    def wipe_trapq(self, trapq):
        # Expire any remaining movement in the trapq (force to history list)
        self.trapq_finalize_moves(trapq, self.reactor.NEVER, 0.)
    def lookup_trapq_append(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        return ffi_lib.trapq_append
    # C steppersync tracking
    def _lookup_steppersync(self, mcu):
        for ss_mcu, ss in self.steppersyncs:
            if ss_mcu is mcu:
                return ss
        ffi_main, ffi_lib = chelper.get_ffi()
        ss = ffi_lib.steppersyncmgr_alloc_steppersync(self.steppersyncmgr)
        self.steppersyncs.append((mcu, ss))
        return ss
    def allocate_syncemitter(self, mcu, name, alloc_stepcompress=True):
        name = name.encode("utf-8")[:15]
        ss = self._lookup_steppersync(mcu)
        ffi_main, ffi_lib = chelper.get_ffi()
        se = ffi_lib.steppersync_alloc_syncemitter(ss, name, alloc_stepcompress)
        self.syncemitters.append(se)
        return se
    def setup_mcu_movequeue(self, mcu, serialqueue, move_count):
        # Setup steppersync object for the mcu's main movequeue
        ffi_main, ffi_lib = chelper.get_ffi()
        ss = self._lookup_steppersync(mcu)
        ffi_lib.steppersync_setup_movequeue(ss, serialqueue, move_count)
        mcu_freq = float(mcu.seconds_to_clock(1.))
        ffi_lib.steppersync_set_time(ss, 0., mcu_freq)
    def stats(self, eventtime):
        # Globally calibrate mcu clocks (and step generation clocks)
        sync_time = self.last_step_gen_time
        ffi_main, ffi_lib = chelper.get_ffi()
        for mcu, ss in self.steppersyncs:
            offset, freq = mcu.calibrate_clock(sync_time, eventtime)
            ffi_lib.steppersync_set_time(ss, offset, freq)
        # Calculate history expiration
        est_print_time = self.mcu.estimated_print_time(eventtime)
        self.clear_history_time = max(0., est_print_time - MOVE_HISTORY_EXPIRE)
        return False, ""
    # Flush notification callbacks
    def register_flush_callback(self, callback, can_add_trapq=False):
        if can_add_trapq:
            self.flush_callbacks = [callback] + self.flush_callbacks
        else:
            self.flush_callbacks = self.flush_callbacks + [callback]
    def unregister_flush_callback(self, callback):
        if callback in self.flush_callbacks:
            fcbs = list(self.flush_callbacks)
            fcbs.remove(callback)
            self.flush_callbacks = fcbs
    # Kinematic step generation scan window time tracking
    def get_kin_flush_delay(self):
        return self.kin_flush_delay
    def check_step_generation_scan_windows(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        kin_flush_delay = SDS_CHECK_TIME
        for se in self.syncemitters:
            sk = ffi_lib.syncemitter_get_stepper_kinematics(se)
            if sk == ffi_main.NULL:
                continue
            trapq = ffi_lib.itersolve_get_trapq(sk)
            if trapq == ffi_main.NULL:
                continue
            pre_active = ffi_lib.itersolve_get_gen_steps_pre_active(sk)
            post_active = ffi_lib.itersolve_get_gen_steps_post_active(sk)
            kin_flush_delay = max(kin_flush_delay, pre_active, post_active)
        self.kin_flush_delay = kin_flush_delay
    # Flush tracking
    def _handle_shutdown(self):
        self.can_pause = False
    def _advance_flush_time(self, want_flush_time, want_step_gen_time=0.):
        flush_time = max(want_flush_time, self.last_flush_time,
                         want_step_gen_time - STEPCOMPRESS_FLUSH_TIME)
        step_gen_time = max(want_step_gen_time, self.last_step_gen_time,
                            flush_time)
        # Invoke flush callbacks (if any)
        with self.reactor.assert_no_pause():
            for cb in self.flush_callbacks:
                cb(flush_time, step_gen_time)
        # Determine maximum history to keep
        trapq_free_time = step_gen_time - self.kin_flush_delay
        clear_history_time = self.clear_history_time
        if not self.can_pause:
            clear_history_time = max(0., trapq_free_time - MOVE_HISTORY_EXPIRE)
        # Generate stepper movement and transmit
        ret = self.steppersyncmgr_gen_steps(self.steppersyncmgr, flush_time,
                                            step_gen_time, clear_history_time)
        if ret:
            raise self.mcu.error("Internal error in stepcompress")
        self.last_flush_time = flush_time
        self.last_step_gen_time = step_gen_time
        # Move processed trapq entries to history list, and expire old history
        for trapq in self.trapqs:
            self.trapq_finalize_moves(trapq, trapq_free_time,
                                      clear_history_time)
    def _await_flush_time(self, want_flush_time):
        while 1:
            if self.last_flush_time >= want_flush_time or not self.can_pause:
                return
            systime = self.reactor.monotonic()
            est_print_time = self.mcu.estimated_print_time(systime)
            wait = want_flush_time - BGFLUSH_HIGH_TIME - est_print_time
            if wait <= 0.:
                return
            self.reactor.pause(systime + min(1., wait))
    def flush_all_steps(self):
        flush_time = self.need_step_gen_time
        self._await_flush_time(flush_time)
        self._advance_flush_time(flush_time)
    def calc_step_gen_restart(self, est_print_time):
        kin_time = max(est_print_time + MIN_KIN_TIME, self.last_step_gen_time)
        return kin_time + self.kin_flush_delay
    def _flush_handler(self, eventtime):
        try:
            est_print_time = self.mcu.estimated_print_time(eventtime)
            aggr_sg_time = self.need_step_gen_time - 2.*self.kin_flush_delay
            if self.last_step_gen_time < aggr_sg_time:
                # Actively stepping - want more aggressive flushing
                want_sg_time = est_print_time + BGFLUSH_SG_HIGH_TIME
                batch_time = BGFLUSH_SG_HIGH_TIME - BGFLUSH_SG_LOW_TIME
                next_batch_time = self.last_step_gen_time + batch_time
                if next_batch_time > est_print_time:
                    # Improve run-to-run reproducibility by batching from last
                    if next_batch_time > want_sg_time + 0.005:
                        # Delay flushing until next wakeup
                        next_batch_time = self.last_step_gen_time
                    want_sg_time = next_batch_time
                want_sg_time = min(want_sg_time, aggr_sg_time)
                # Flush motion queues (if needed)
                if want_sg_time > self.last_step_gen_time:
                    self._advance_flush_time(0., want_sg_time)
            else:
                # Not stepping (or only step remnants) - use relaxed flushing
                want_flush_time = est_print_time + BGFLUSH_HIGH_TIME
                max_flush_time = self.need_flush_time + BGFLUSH_EXTRA_TIME
                want_flush_time = min(want_flush_time, max_flush_time)
                # Flush motion queues (if needed)
                if want_flush_time > self.last_flush_time:
                    self._advance_flush_time(want_flush_time)
            # Reschedule timer
            aggr_sg_time = self.need_step_gen_time - 2.*self.kin_flush_delay
            if self.last_step_gen_time < aggr_sg_time:
                waketime = self.last_step_gen_time - BGFLUSH_SG_LOW_TIME
            else:
                self.do_kick_flush_timer = True
                max_flush_time = self.need_flush_time + BGFLUSH_EXTRA_TIME
                if self.last_flush_time >= max_flush_time:
                    return self.reactor.NEVER
                waketime = self.last_flush_time - BGFLUSH_LOW_TIME
            return eventtime + waketime - est_print_time
        except:
            logging.exception("Exception in flush_handler")
            self.printer.invoke_shutdown("Exception in flush_handler")
        return self.reactor.NEVER
    def _flush_handler_debug(self, eventtime):
        # Use custom flushing code when in batch output mode
        try:
            faux_time = self.need_flush_time - 1.5
            batch_time = BGFLUSH_SG_HIGH_TIME - BGFLUSH_SG_LOW_TIME
            flush_count = 0
            while self.last_step_gen_time < faux_time:
                target = self.last_step_gen_time + batch_time
                if flush_count > 100. and faux_time > target:
                    target += int((faux_time-target) / batch_time) * batch_time
                self._advance_flush_time(0., target)
                flush_count += 1
            if flush_count:
                return self.reactor.NOW
            self._advance_flush_time(self.need_flush_time + BGFLUSH_EXTRA_TIME)
            self.do_kick_flush_timer = True
            return self.reactor.NEVER
        except:
            logging.exception("Exception in flush_handler_debug")
            self.printer.invoke_shutdown("Exception in flush_handler_debug")
        return self.reactor.NEVER
    def note_mcu_movequeue_activity(self, mq_time, is_step_gen=True):
        if is_step_gen:
            mq_time += self.kin_flush_delay
            self.need_step_gen_time = max(self.need_step_gen_time, mq_time)
        self.need_flush_time = max(self.need_flush_time, mq_time)
        if self.do_kick_flush_timer:
            self.do_kick_flush_timer = False
            self.reactor.update_timer(self.flush_timer, self.reactor.NOW)
    # "Drip" timing (for homing and probing moves)
    def drip_update_time(self, start_time, end_time, drip_completion):
        self.drip_start_times.append(start_time)
        self._await_flush_time(start_time)
        # Disable background flushing from timer
        self.reactor.update_timer(self.flush_timer, self.reactor.NEVER)
        self.do_kick_flush_timer = False
        self._advance_flush_time(start_time - SDS_CHECK_TIME, start_time)
        # Flush in segments until drip_completion signal
        flush_time = start_time
        while flush_time < end_time:
            if drip_completion.test():
                break
            curtime = self.reactor.monotonic()
            est_print_time = self.mcu.estimated_print_time(curtime)
            wait_time = flush_time - est_print_time - DRIP_TIME
            if wait_time > 0. and self.can_pause:
                # Pause before sending more steps
                drip_completion.wait(curtime + wait_time)
                continue
            flush_time = min(flush_time + DRIP_SEGMENT_TIME, end_time)
            self.note_mcu_movequeue_activity(flush_time)
            self._advance_flush_time(flush_time - SDS_CHECK_TIME, flush_time)
        # Restore background flushing
        self.reactor.update_timer(self.flush_timer, self.reactor.NOW)
        self._advance_flush_time(flush_time + self.kin_flush_delay)
        self.drip_start_times.remove(start_time)
    def check_drip_timing(self):
        if not self.drip_start_times:
            return None
        return min(self.drip_start_times)

def load_config(config):
    return PrinterMotionQueuing(config)

# Parse gcode commands
#
# Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, re, logging, collections, shlex, operator

class CommandError(Exception):
    pass

# Custom "tuple" class for coordinates - add easy access to x, y, z components
class Coord(tuple):
    __slots__ = ()
    def __new__(cls, t):
        if len(t) < 4:
            t = tuple(t) + (0,) * (4 - len(t))
        return tuple.__new__(cls, t)
    x = property(operator.itemgetter(0))
    y = property(operator.itemgetter(1))
    z = property(operator.itemgetter(2))
    e = property(operator.itemgetter(3))

# Class for handling gcode command parameters (gcmd)
class GCodeCommand:
    error = CommandError
    def __init__(self, gcode, command, commandline, params, need_ack):
        self._command = command
        self._commandline = commandline
        self._params = params
        self._need_ack = need_ack
        # Method wrappers
        self.respond_info = gcode.respond_info
        self.respond_raw = gcode.respond_raw
    def get_command(self):
        return self._command
    def get_commandline(self):
        return self._commandline
    def get_command_parameters(self):
        return self._params
    def get_raw_command_parameters(self):
        command = self._command
        origline = self._commandline
        param_start = len(command)
        param_end = len(origline)
        if origline[:param_start].upper() != command:
            # Skip any gcode line-number and ignore any trailing checksum
            param_start += origline.upper().find(command)
            end = origline.rfind('*')
            if end >= 0 and origline[end+1:].isdigit():
                param_end = end
        if origline[param_start:param_start+1].isspace():
            param_start += 1
        return origline[param_start:param_end]
    def ack(self, msg=None):
        if not self._need_ack:
            return False
        ok_msg = "ok"
        if msg:
            ok_msg = "ok %s" % (msg,)
        self.respond_raw(ok_msg)
        self._need_ack = False
        return True
    # Parameter parsing helpers
    class sentinel: pass
    def get(self, name, default=sentinel, parser=str, minval=None, maxval=None,
            above=None, below=None):
        value = self._params.get(name)
        if value is None:
            if default is self.sentinel:
                raise self.error("Error on '%s': missing %s"
                                 % (self._commandline, name))
            return default
        try:
            value = parser(value)
        except:
            raise self.error("Error on '%s': unable to parse %s"
                             % (self._commandline, value))
        if minval is not None and value < minval:
            raise self.error("Error on '%s': %s must have minimum of %s"
                             % (self._commandline, name, minval))
        if maxval is not None and value > maxval:
            raise self.error("Error on '%s': %s must have maximum of %s"
                             % (self._commandline, name, maxval))
        if above is not None and value <= above:
            raise self.error("Error on '%s': %s must be above %s"
                             % (self._commandline, name, above))
        if below is not None and value >= below:
            raise self.error("Error on '%s': %s must be below %s"
                             % (self._commandline, name, below))
        return value
    def get_int(self, name, default=sentinel, minval=None, maxval=None):
        return self.get(name, default, parser=int, minval=minval, maxval=maxval)
    def get_float(self, name, default=sentinel, minval=None, maxval=None,
                  above=None, below=None):
        return self.get(name, default, parser=float, minval=minval,
                        maxval=maxval, above=above, below=below)

# Parse and dispatch G-Code commands
class GCodeDispatch:
    error = CommandError
    Coord = Coord
    def __init__(self, printer):
        self.printer = printer
        self.is_fileinput = not not printer.get_start_args().get("debuginput")
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        printer.register_event_handler("klippy:disconnect",
                                       self._handle_disconnect)
        # Command handling
        self.is_printer_ready = False
        self.mutex = printer.get_reactor().mutex()
        self.output_callbacks = []
        self.base_gcode_handlers = self.gcode_handlers = {}
        self.ready_gcode_handlers = {}
        self.mux_commands = {}
        self.gcode_help = {}
        self.status_commands = {}
        # Register commands needed before config file is loaded
        handlers = ['M110', 'M112', 'M115',
                    'RESTART', 'FIRMWARE_RESTART', 'ECHO', 'STATUS', 'HELP']
        for cmd in handlers:
            func = getattr(self, 'cmd_' + cmd)
            desc = getattr(self, 'cmd_' + cmd + '_help', None)
            self.register_command(cmd, func, True, desc)
    def is_traditional_gcode(self, cmd):
        # A "traditional" g-code command is a letter and followed by a number
        try:
            cmd = cmd.upper().split()[0]
            val = float(cmd[1:])
            return cmd[0].isupper() and cmd[1].isdigit()
        except:
            return False
    def register_command(self, cmd, func, when_not_ready=False, desc=None):
        if func is None:
            old_cmd = self.ready_gcode_handlers.get(cmd)
            if cmd in self.ready_gcode_handlers:
                del self.ready_gcode_handlers[cmd]
            if cmd in self.base_gcode_handlers:
                del self.base_gcode_handlers[cmd]
            self._build_status_commands()
            return old_cmd
        if cmd in self.ready_gcode_handlers:
            raise self.printer.config_error(
                "gcode command %s already registered" % (cmd,))
        if not self.is_traditional_gcode(cmd):
            if (cmd.upper() != cmd or not cmd.replace('_', 'A').isalnum()
                or cmd[0].isdigit() or cmd[1:2].isdigit()):
                raise self.printer.config_error(
                    "Can't register '%s' as it is an invalid name" % (cmd,))
            origfunc = func
            func = lambda params: origfunc(self._get_extended_params(params))
        self.ready_gcode_handlers[cmd] = func
        if when_not_ready:
            self.base_gcode_handlers[cmd] = func
        if desc is not None:
            self.gcode_help[cmd] = desc
        self._build_status_commands()
    def register_mux_command(self, cmd, key, value, func, desc=None):
        prev = self.mux_commands.get(cmd)
        if prev is None:
            handler = lambda gcmd: self._cmd_mux(cmd, gcmd)
            self.register_command(cmd, handler, desc=desc)
            self.mux_commands[cmd] = prev = (key, {})
        prev_key, prev_values = prev
        if prev_key != key:
            raise self.printer.config_error(
                "mux command %s %s %s may have only one key (%s)" % (
                    cmd, key, value, prev_key))
        if value in prev_values:
            raise self.printer.config_error(
                "mux command %s %s %s already registered (%s)" % (
                    cmd, key, value, prev_values))
        prev_values[value] = func
    def get_command_help(self):
        return dict(self.gcode_help)
    def get_status(self, eventtime):
        return {'commands': self.status_commands}
    def _build_status_commands(self):
        commands = {cmd: {} for cmd in self.gcode_handlers}
        for cmd in self.gcode_help:
            if cmd in commands:
                commands[cmd]['help'] = self.gcode_help[cmd]
        self.status_commands = commands
    def register_output_handler(self, cb):
        self.output_callbacks.append(cb)
    def _handle_shutdown(self):
        if not self.is_printer_ready:
            return
        self.is_printer_ready = False
        self.gcode_handlers = self.base_gcode_handlers
        self._build_status_commands()
        self._respond_state("Shutdown")
    def _handle_disconnect(self):
        self._respond_state("Disconnect")
    def _handle_ready(self):
        self.is_printer_ready = True
        self.gcode_handlers = self.ready_gcode_handlers
        self._build_status_commands()
        self._respond_state("Ready")
    # Parse input into commands
    args_r = re.compile('([A-Z_]+|[A-Z*])')
    def _process_commands(self, commands, need_ack=True):
        for line in commands:
            # Ignore comments and leading/trailing spaces
            line = origline = line.strip()
            cpos = line.find(';')
            if cpos >= 0:
                line = line[:cpos]
            # Break line into parts and determine command
            parts = self.args_r.split(line.upper())
            if ''.join(parts[:2]) == 'N':
                # Skip line number at start of command
                cmd = ''.join(parts[3:5]).strip()
            else:
                cmd = ''.join(parts[:3]).strip()
            # Build gcode "params" dictionary
            params = { parts[i]: parts[i+1].strip()
                       for i in range(1, len(parts), 2) }
            gcmd = GCodeCommand(self, cmd, origline, params, need_ack)
            # Invoke handler for command
            handler = self.gcode_handlers.get(cmd, self.cmd_default)
            try:
                handler(gcmd)
            except self.error as e:
                self._respond_error(str(e))
                self.printer.send_event("gcode:command_error")
                if not need_ack:
                    raise
            except:
                msg = 'Internal error on command:"%s"' % (cmd,)
                logging.exception(msg)
                self.printer.invoke_shutdown(msg)
                self._respond_error(msg)
                if not need_ack:
                    raise
            gcmd.ack()
    def run_script_from_command(self, script):
        self._process_commands(script.split('\n'), need_ack=False)
    def run_script(self, script):
        with self.mutex:
            self._process_commands(script.split('\n'), need_ack=False)
    def get_mutex(self):
        return self.mutex
    def create_gcode_command(self, command, commandline, params):
        return GCodeCommand(self, command, commandline, params, False)
    # Response handling
    def respond_raw(self, msg):
        for cb in self.output_callbacks:
            cb(msg)
    def respond_info(self, msg, log=True):
        if log:
            logging.info(msg)
        lines = [l.strip() for l in msg.strip().split('\n')]
        self.respond_raw("// " + "\n// ".join(lines))
    def _respond_error(self, msg):
        logging.warning(msg)
        lines = msg.strip().split('\n')
        if len(lines) > 1:
            self.respond_info("\n".join(lines), log=False)
        self.respond_raw('!! %s' % (lines[0].strip(),))
        if self.is_fileinput:
            self.printer.request_exit('error_exit')
    def _respond_state(self, state):
        self.respond_info("Klipper state: %s" % (state,), log=False)
    # Parameter parsing helpers
    def _get_extended_params(self, gcmd):
        rawparams = gcmd.get_raw_command_parameters()
        # Extract args while allowing shell style quoting
        s = shlex.shlex(rawparams, posix=True)
        s.whitespace_split = True
        s.commenters = '#;'
        try:
            eparams = [earg.split('=', 1) for earg in s]
            eparams = { k.upper(): v for k, v in eparams }
        except ValueError as e:
            raise self.error("Malformed command '%s'"
                             % (gcmd.get_commandline(),))
        # Update gcmd with new parameters
        gcmd._params.clear()
        gcmd._params.update(eparams)
        return gcmd
    # G-Code special command handlers
    def cmd_default(self, gcmd):
        cmd = gcmd.get_command()
        if cmd == 'M105':
            # Don't warn about temperature requests when not ready
            gcmd.ack("T:0")
            return
        if cmd == 'M21':
            # Don't warn about sd card init when not ready
            return
        if not self.is_printer_ready:
            raise gcmd.error(self.printer.get_state_message()[0])
            return
        if not cmd:
            cmdline = gcmd.get_commandline()
            if cmdline:
                logging.debug(cmdline)
            return
        if ' ' in cmd:
            # Handle M117/M118 gcode with numeric and special characters
            realcmd = cmd.split()[0]
            if realcmd in ["M117", "M118", "M23"]:
                handler = self.gcode_handlers.get(realcmd, None)
                if handler is not None:
                    gcmd._command = realcmd
                    handler(gcmd)
                    return
        elif cmd in ['M140', 'M104'] and not gcmd.get_float('S', 0.):
            # Don't warn about requests to turn off heaters when not present
            return
        elif cmd == 'M107' or (cmd == 'M106' and (
                not gcmd.get_float('S', 1.) or self.is_fileinput)):
            # Don't warn about requests to turn off fan when fan not present
            return
        gcmd.respond_info('Unknown command:"%s"' % (cmd,))
    def _cmd_mux(self, command, gcmd):
        key, values = self.mux_commands[command]
        if None in values:
            key_param = gcmd.get(key, None)
        else:
            key_param = gcmd.get(key)
        if key_param not in values:
            raise gcmd.error("The value '%s' is not valid for %s"
                             % (key_param, key))
        values[key_param](gcmd)
    # Low-level G-Code commands that are needed before the config file is loaded
    def cmd_M110(self, gcmd):
        # Set Current Line Number
        pass
    def cmd_M112(self, gcmd):
        # Emergency Stop
        self.printer.invoke_shutdown("Shutdown due to M112 command")
    def cmd_M115(self, gcmd):
        # Get Firmware Version and Capabilities
        software_version = self.printer.get_start_args().get('software_version')
        kw = {"FIRMWARE_NAME": "Klipper", "FIRMWARE_VERSION": software_version}
        msg = " ".join(["%s:%s" % (k, v) for k, v in kw.items()])
        did_ack = gcmd.ack(msg)
        if not did_ack:
            gcmd.respond_info(msg)
    def request_restart(self, result):
        if self.is_printer_ready:
            toolhead = self.printer.lookup_object('toolhead')
            print_time = toolhead.get_last_move_time()
            if result == 'exit':
                logging.info("Exiting (print time %.3fs)" % (print_time,))
            self.printer.send_event("gcode:request_restart", print_time)
            toolhead.dwell(0.500)
            toolhead.wait_moves()
        self.printer.request_exit(result)
    cmd_RESTART_help = "Reload config file and restart host software"
    def cmd_RESTART(self, gcmd):
        self.request_restart('restart')
    cmd_FIRMWARE_RESTART_help = "Restart firmware, host, and reload config"
    def cmd_FIRMWARE_RESTART(self, gcmd):
        self.request_restart('firmware_restart')
    def cmd_ECHO(self, gcmd):
        gcmd.respond_info(gcmd.get_commandline(), log=False)
    cmd_STATUS_help = "Report the printer status"
    def cmd_STATUS(self, gcmd):
        if self.is_printer_ready:
            self._respond_state("Ready")
            return
        msg = self.printer.get_state_message()[0]
        msg = msg.rstrip() + "\nKlipper state: Not ready"
        raise gcmd.error(msg)
    cmd_HELP_help = "Report the list of available extended G-Code commands"
    def cmd_HELP(self, gcmd):
        cmdhelp = []
        if not self.is_printer_ready:
            cmdhelp.append("Printer is not ready - not all commands available.")
        cmdhelp.append("Available extended commands:")
        for cmd in sorted(self.gcode_handlers):
            if cmd in self.gcode_help:
                cmdhelp.append("%-10s: %s" % (cmd, self.gcode_help[cmd]))
        gcmd.respond_info("\n".join(cmdhelp), log=False)

# Support reading gcode from a pseudo-tty interface
class GCodeIO:
    def __init__(self, printer):
        self.printer = printer
        self.gcode = printer.lookup_object('gcode')
        self.gcode_mutex = self.gcode.get_mutex()
        self.fd = printer.get_start_args().get("gcode_fd")
        self.reactor = printer.get_reactor()
        self.is_printer_ready = False
        self.is_processing_data = False
        self.is_fileinput = not not printer.get_start_args().get("debuginput")
        self.pipe_is_active = True
        self.fd_handle = None
        if not self.is_fileinput:
            self.gcode.register_output_handler(self._respond_raw)
            self.fd_handle = self.reactor.register_fd(self.fd,
                                                      self._process_data)
        self.partial_input = ""
        self.pending_commands = []
        self.bytes_read = 0
        self.input_log = collections.deque([], 50)
        # Register event handlers
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        printer.register_event_handler("klippy:analyze_shutdown",
                                       self._handle_analyze_shutdown)
    def _handle_ready(self):
        self.is_printer_ready = True
        if self.is_fileinput and self.fd_handle is None:
            self.fd_handle = self.reactor.register_fd(self.fd,
                                                      self._process_data)
    def _handle_analyze_shutdown(self, msg, details):
        out = []
        out.append("Dumping gcode input %d blocks" % (len(self.input_log),))
        for eventtime, data in self.input_log:
            out.append("Read %f: %s" % (eventtime, repr(data)))
        logging.info("\n".join(out))
    def _handle_shutdown(self):
        if not self.is_printer_ready:
            return
        self.is_printer_ready = False
        if self.is_fileinput:
            self.printer.request_exit('error_exit')
    m112_r = re.compile(r'^(?:[nN][0-9]+)?\s*[mM]112(?:\s|$)')
    def _process_data(self, eventtime):
        # Read input, separate by newline, and add to pending_commands
        try:
            data = str(os.read(self.fd, 4096).decode())
        except (os.error, UnicodeDecodeError):
            logging.exception("Read g-code")
            return
        self.input_log.append((eventtime, data))
        self.bytes_read += len(data)
        lines = data.split('\n')
        lines[0] = self.partial_input + lines[0]
        self.partial_input = lines.pop()
        pending_commands = self.pending_commands
        pending_commands.extend(lines)
        self.pipe_is_active = True
        # Special handling for debug file input EOF
        if not data and self.is_fileinput:
            if not self.is_processing_data:
                self.reactor.unregister_fd(self.fd_handle)
                self.fd_handle = None
                self.gcode.request_restart('exit')
            pending_commands.append("")
        # Handle case where multiple commands pending
        if len(pending_commands) < 20:
            # Check for M112 out-of-order
            for line in lines:
                if self.m112_r.match(line) is not None:
                    self.gcode.cmd_M112(None)
        if self.is_processing_data:
            if len(pending_commands) >= 20:
                # Stop reading input
                self.reactor.unregister_fd(self.fd_handle)
                self.fd_handle = None
            return
        # Process commands
        self.is_processing_data = True
        while pending_commands:
            self.pending_commands = []
            with self.gcode_mutex:
                self.gcode._process_commands(pending_commands)
            pending_commands = self.pending_commands
        self.is_processing_data = False
        if self.fd_handle is None:
            self.fd_handle = self.reactor.register_fd(self.fd,
                                                      self._process_data)
    def _respond_raw(self, msg):
        if self.pipe_is_active:
            try:
                os.write(self.fd, (msg+"\n").encode())
            except os.error:
                logging.exception("Write g-code response")
                self.pipe_is_active = False
    def stats(self, eventtime):
        return False, "gcodein=%d" % (self.bytes_read,)

def add_early_printer_objects(printer):
    printer.add_object('gcode', GCodeDispatch(printer))
    printer.add_object('gcode_io', GCodeIO(printer))

# Helper code for low-level motion queuing and flushing
#
# Copyright (C) 2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import chelper

BGFLUSH_LOW_TIME = 0.200
BGFLUSH_HIGH_TIME = 0.400
BGFLUSH_SG_LOW_TIME = 0.450
BGFLUSH_SG_HIGH_TIME = 0.700
BGFLUSH_EXTRA_TIME = 0.250

MOVE_HISTORY_EXPIRE = 30.
MIN_KIN_TIME = 0.100
STEPCOMPRESS_FLUSH_TIME = 0.050
SDS_CHECK_TIME = 0.001 # step+dir+step filter in stepcompress.c

DRIP_SEGMENT_TIME = 0.050
DRIP_TIME = 0.100

class PrinterMotionQueuing:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        # C trapq tracking
        self.trapqs = []
        ffi_main, ffi_lib = chelper.get_ffi()
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves
        # C steppersync tracking
        self.steppersyncmgr = ffi_main.gc(ffi_lib.steppersyncmgr_alloc(),
                                          ffi_lib.steppersyncmgr_free)
        self.syncemitters = []
        self.steppersyncs = []
        self.steppersyncmgr_gen_steps = ffi_lib.steppersyncmgr_gen_steps
        # History expiration
        self.clear_history_time = 0.
        # Flush notification callbacks
        self.flush_callbacks = []
        # Kinematic step generation scan window time tracking
        self.kin_flush_delay = SDS_CHECK_TIME
        # MCU tracking
        self.all_mcus = [m for n, m in printer.lookup_objects(module='mcu')]
        self.mcu = self.all_mcus[0]
        self.can_pause = True
        if self.mcu.is_fileoutput():
            self.can_pause = False
        # Flush tracking
        flush_handler = self._flush_handler
        if not self.can_pause:
            flush_handler = self._flush_handler_debug
        self.flush_timer = self.reactor.register_timer(flush_handler)
        self.do_kick_flush_timer = True
        self.last_flush_time = self.last_step_gen_time = 0.
        self.need_flush_time = self.need_step_gen_time = 0.
        # "Drip" timing (for homing and probing moves)
        self.drip_start_times = []
        # Register handlers
        printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
    # C trapq tracking
    def allocate_trapq(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
        self.trapqs.append(trapq)
        return trapq
    def wipe_trapq(self, trapq):
        # Expire any remaining movement in the trapq (force to history list)
        self.trapq_finalize_moves(trapq, self.reactor.NEVER, 0.)
    def lookup_trapq_append(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        return ffi_lib.trapq_append
    # C steppersync tracking
    def _lookup_steppersync(self, mcu):
        for ss_mcu, ss in self.steppersyncs:
            if ss_mcu is mcu:
                return ss
        ffi_main, ffi_lib = chelper.get_ffi()
        ss = ffi_lib.steppersyncmgr_alloc_steppersync(self.steppersyncmgr)
        self.steppersyncs.append((mcu, ss))
        return ss
    def allocate_syncemitter(self, mcu, name, alloc_stepcompress=True):
        name = name.encode("utf-8")[:15]
        ss = self._lookup_steppersync(mcu)
        ffi_main, ffi_lib = chelper.get_ffi()
        se = ffi_lib.steppersync_alloc_syncemitter(ss, name, alloc_stepcompress)
        self.syncemitters.append(se)
        return se
    def setup_mcu_movequeue(self, mcu, serialqueue, move_count):
        # Setup steppersync object for the mcu's main movequeue
        ffi_main, ffi_lib = chelper.get_ffi()
        ss = self._lookup_steppersync(mcu)
        ffi_lib.steppersync_setup_movequeue(ss, serialqueue, move_count)
        mcu_freq = float(mcu.seconds_to_clock(1.))
        ffi_lib.steppersync_set_time(ss, 0., mcu_freq)
    def stats(self, eventtime):
        # Globally calibrate mcu clocks (and step generation clocks)
        sync_time = self.last_step_gen_time
        ffi_main, ffi_lib = chelper.get_ffi()
        for mcu, ss in self.steppersyncs:
            offset, freq = mcu.calibrate_clock(sync_time, eventtime)
            ffi_lib.steppersync_set_time(ss, offset, freq)
        # Calculate history expiration
        est_print_time = self.mcu.estimated_print_time(eventtime)
        self.clear_history_time = max(0., est_print_time - MOVE_HISTORY_EXPIRE)
        return False, ""
    # Flush notification callbacks
    def register_flush_callback(self, callback, can_add_trapq=False):
        if can_add_trapq:
            self.flush_callbacks = [callback] + self.flush_callbacks
        else:
            self.flush_callbacks = self.flush_callbacks + [callback]
    def unregister_flush_callback(self, callback):
        if callback in self.flush_callbacks:
            fcbs = list(self.flush_callbacks)
            fcbs.remove(callback)
            self.flush_callbacks = fcbs
    # Kinematic step generation scan window time tracking
    def get_kin_flush_delay(self):
        return self.kin_flush_delay
    def check_step_generation_scan_windows(self):
        ffi_main, ffi_lib = chelper.get_ffi()
        kin_flush_delay = SDS_CHECK_TIME
        for se in self.syncemitters:
            sk = ffi_lib.syncemitter_get_stepper_kinematics(se)
            if sk == ffi_main.NULL:
                continue
            trapq = ffi_lib.itersolve_get_trapq(sk)
            if trapq == ffi_main.NULL:
                continue
            pre_active = ffi_lib.itersolve_get_gen_steps_pre_active(sk)
            post_active = ffi_lib.itersolve_get_gen_steps_post_active(sk)
            kin_flush_delay = max(kin_flush_delay, pre_active, post_active)
        self.kin_flush_delay = kin_flush_delay
    # Flush tracking
    def _handle_shutdown(self):
        self.can_pause = False
    def _advance_flush_time(self, want_flush_time, want_step_gen_time=0.):
        flush_time = max(want_flush_time, self.last_flush_time,
                         want_step_gen_time - STEPCOMPRESS_FLUSH_TIME)
        step_gen_time = max(want_step_gen_time, self.last_step_gen_time,
                            flush_time)
        # Invoke flush callbacks (if any)
        with self.reactor.assert_no_pause():
            for cb in self.flush_callbacks:
                cb(flush_time, step_gen_time)
        # Determine maximum history to keep
        trapq_free_time = step_gen_time - self.kin_flush_delay
        clear_history_time = self.clear_history_time
        if not self.can_pause:
            clear_history_time = max(0., trapq_free_time - MOVE_HISTORY_EXPIRE)
        # Generate stepper movement and transmit
        ret = self.steppersyncmgr_gen_steps(self.steppersyncmgr, flush_time,
                                            step_gen_time, clear_history_time)
        if ret:
            raise self.mcu.error("Internal error in stepcompress")
        self.last_flush_time = flush_time
        self.last_step_gen_time = step_gen_time
        # Move processed trapq entries to history list, and expire old history
        for trapq in self.trapqs:
            self.trapq_finalize_moves(trapq, trapq_free_time,
                                      clear_history_time)
    def _await_flush_time(self, want_flush_time):
        while 1:
            if self.last_flush_time >= want_flush_time or not self.can_pause:
                return
            systime = self.reactor.monotonic()
            est_print_time = self.mcu.estimated_print_time(systime)
            wait = want_flush_time - BGFLUSH_HIGH_TIME - est_print_time
            if wait <= 0.:
                return
            self.reactor.pause(systime + min(1., wait))
    def flush_all_steps(self):
        flush_time = self.need_step_gen_time
        self._await_flush_time(flush_time)
        self._advance_flush_time(flush_time)
    def calc_step_gen_restart(self, est_print_time):
        kin_time = max(est_print_time + MIN_KIN_TIME, self.last_step_gen_time)
        return kin_time + self.kin_flush_delay
    def _flush_handler(self, eventtime):
        try:
            est_print_time = self.mcu.estimated_print_time(eventtime)
            aggr_sg_time = self.need_step_gen_time - 2.*self.kin_flush_delay
            if self.last_step_gen_time < aggr_sg_time:
                # Actively stepping - want more aggressive flushing
                want_sg_time = est_print_time + BGFLUSH_SG_HIGH_TIME
                batch_time = BGFLUSH_SG_HIGH_TIME - BGFLUSH_SG_LOW_TIME
                next_batch_time = self.last_step_gen_time + batch_time
                if next_batch_time > est_print_time:
                    # Improve run-to-run reproducibility by batching from last
                    if next_batch_time > want_sg_time + 0.005:
                        # Delay flushing until next wakeup
                        next_batch_time = self.last_step_gen_time
                    want_sg_time = next_batch_time
                want_sg_time = min(want_sg_time, aggr_sg_time)
                # Flush motion queues (if needed)
                if want_sg_time > self.last_step_gen_time:
                    self._advance_flush_time(0., want_sg_time)
            else:
                # Not stepping (or only step remnants) - use relaxed flushing
                want_flush_time = est_print_time + BGFLUSH_HIGH_TIME
                max_flush_time = self.need_flush_time + BGFLUSH_EXTRA_TIME
                want_flush_time = min(want_flush_time, max_flush_time)
                # Flush motion queues (if needed)
                if want_flush_time > self.last_flush_time:
                    self._advance_flush_time(want_flush_time)
            # Reschedule timer
            aggr_sg_time = self.need_step_gen_time - 2.*self.kin_flush_delay
            if self.last_step_gen_time < aggr_sg_time:
                waketime = self.last_step_gen_time - BGFLUSH_SG_LOW_TIME
            else:
                self.do_kick_flush_timer = True
                max_flush_time = self.need_flush_time + BGFLUSH_EXTRA_TIME
                if self.last_flush_time >= max_flush_time:
                    return self.reactor.NEVER
                waketime = self.last_flush_time - BGFLUSH_LOW_TIME
            return eventtime + waketime - est_print_time
        except:
            logging.exception("Exception in flush_handler")
            self.printer.invoke_shutdown("Exception in flush_handler")
        return self.reactor.NEVER
    def _flush_handler_debug(self, eventtime):
        # Use custom flushing code when in batch output mode
        try:
            faux_time = self.need_flush_time - 1.5
            batch_time = BGFLUSH_SG_HIGH_TIME - BGFLUSH_SG_LOW_TIME
            flush_count = 0
            while self.last_step_gen_time < faux_time:
                target = self.last_step_gen_time + batch_time
                if flush_count > 100. and faux_time > target:
                    target += int((faux_time-target) / batch_time) * batch_time
                self._advance_flush_time(0., target)
                flush_count += 1
            if flush_count:
                return self.reactor.NOW
            self._advance_flush_time(self.need_flush_time + BGFLUSH_EXTRA_TIME)
            self.do_kick_flush_timer = True
            return self.reactor.NEVER
        except:
            logging.exception("Exception in flush_handler_debug")
            self.printer.invoke_shutdown("Exception in flush_handler_debug")
        return self.reactor.NEVER
    def note_mcu_movequeue_activity(self, mq_time, is_step_gen=True):
        if is_step_gen:
            mq_time += self.kin_flush_delay
            self.need_step_gen_time = max(self.need_step_gen_time, mq_time)
        self.need_flush_time = max(self.need_flush_time, mq_time)
        if self.do_kick_flush_timer:
            self.do_kick_flush_timer = False
            self.reactor.update_timer(self.flush_timer, self.reactor.NOW)
    # "Drip" timing (for homing and probing moves)
    def drip_update_time(self, start_time, end_time, drip_completion):
        self.drip_start_times.append(start_time)
        self._await_flush_time(start_time)
        # Disable background flushing from timer
        self.reactor.update_timer(self.flush_timer, self.reactor.NEVER)
        self.do_kick_flush_timer = False
        self._advance_flush_time(start_time - SDS_CHECK_TIME, start_time)
        # Flush in segments until drip_completion signal
        flush_time = start_time
        while flush_time < end_time:
            if drip_completion.test():
                break
            curtime = self.reactor.monotonic()
            est_print_time = self.mcu.estimated_print_time(curtime)
            wait_time = flush_time - est_print_time - DRIP_TIME
            if wait_time > 0. and self.can_pause:
                # Pause before sending more steps
                drip_completion.wait(curtime + wait_time)
                continue
            flush_time = min(flush_time + DRIP_SEGMENT_TIME, end_time)
            self.note_mcu_movequeue_activity(flush_time)
            self._advance_flush_time(flush_time - SDS_CHECK_TIME, flush_time)
        # Restore background flushing
        self.reactor.update_timer(self.flush_timer, self.reactor.NOW)
        self._advance_flush_time(flush_time + self.kin_flush_delay)
        self.drip_start_times.remove(start_time)
    def check_drip_timing(self):
        if not self.drip_start_times:
            return None
        return min(self.drip_start_times)

def load_config(config):
    return PrinterMotionQueuing(config)
