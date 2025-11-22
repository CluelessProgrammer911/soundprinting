
# Utility for manually moving two steppers independently for diagnostics
#
# Copyright (C) 2018-2025 Kevin O'Connor
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
import chelper

BUZZ_DISTANCE = 1.
BUZZ_VELOCITY = BUZZ_DISTANCE / .250
BUZZ_RADIANS_DISTANCE = math.radians(1.)
BUZZ_RADIANS_VELOCITY = BUZZ_RADIANS_DISTANCE / .250

# Calculate a move's accel_t, cruise_t, and cruise_v
def calc_move_time(dist, speed, accel):
    axis_r = 1.
    if dist < 0.:
        axis_r = -1.
        dist = -dist
    if not accel or not dist:
        return axis_r, 0., dist / speed, speed
    max_cruise_v2 = dist * accel
    if max_cruise_v2 < speed**2:
        speed = math.sqrt(max_cruise_v2)
    accel_t = speed / accel
    accel_decel_d = accel_t * speed
    cruise_t = (dist - accel_decel_d) / speed
    return axis_r, accel_t, cruise_t, speed

class ForceMoveDual:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.steppers = {}
        # Copy steppers from original force_move
        orig_force_move = self.printer.lookup_object('force_move', None)
        if orig_force_move:
            self.steppers = orig_force_move.steppers
        # Setup iterative solver
        self.motion_queuing = self.printer.load_object(config, 'motion_queuing')
        self.trapq1 = self.motion_queuing.allocate_trapq()
        self.trapq2 = self.motion_queuing.allocate_trapq()
        self.trapq_append = self.motion_queuing.lookup_trapq_append()
        ffi_main, ffi_lib = chelper.get_ffi()
        self.stepper_kinematics = ffi_main.gc(
            ffi_lib.cartesian_stepper_alloc(b'x'), ffi_lib.free)
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('STEPPER_BUZZ_DUAL', self.cmd_STEPPER_BUZZ_DUAL,
                               desc=self.cmd_STEPPER_BUZZ_DUAL_help)
        if config.getboolean("enable_force_move", False):
            gcode.register_command('FORCE_MOVE_DUAL', self.cmd_FORCE_MOVE_DUAL,
                                   desc=self.cmd_FORCE_MOVE_DUAL_help)
            gcode.register_command('SET_KINEMATIC_POSITION_DUAL',
                                   self.cmd_SET_KINEMATIC_POSITION_DUAL,
                                   desc=self.cmd_SET_KINEMATIC_POSITION_DUAL_help)

    def _lookup_stepper(self, name):
        if name not in self.steppers:
            raise self.printer.config_error("Unknown stepper %s" % (name,))
        return self.steppers[name]

    def _force_enable(self, stepper):
        stepper_name = stepper.get_name()
        stepper_enable = self.printer.lookup_object('stepper_enable')
        did_enable = stepper_enable.set_motors_enable([stepper_name], True)
        return did_enable

    def _restore_enable(self, stepper, did_enable):
        if not did_enable:
            return
        stepper_name = stepper.get_name()
        stepper_enable = self.printer.lookup_object('stepper_enable')
        stepper_enable.set_motors_enable([stepper_name], False)

    def manual_move_dual(self, stepper1, dist1, speed1, accel1,
                          stepper2, dist2, speed2, accel2):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        # Backup original kinematics and trapqs
        prev_sk1 = stepper1.set_stepper_kinematics(self.stepper_kinematics)
        prev_sk2 = stepper2.set_stepper_kinematics(self.stepper_kinematics)
        prev_trapq1 = stepper1.set_trapq(self.trapq1)
        prev_trapq2 = stepper2.set_trapq(self.trapq2)
        stepper1.set_position((0., 0., 0.))
        stepper2.set_position((0., 0., 0.))

        axis_r1, accel_t1, cruise_t1, cruise_v1 = calc_move_time(dist1, speed1, accel1)
        axis_r2, accel_t2, cruise_t2, cruise_v2 = calc_move_time(dist2, speed2, accel2)
        print_time = toolhead.get_last_move_time()

        # Append moves to both trapqs
        self.trapq_append(self.trapq1, print_time, accel_t1, cruise_t1, accel_t1,
                          0., 0., 0., axis_r1, 0., 0., 0., cruise_v1, accel1)
        self.trapq_append(self.trapq2, print_time, accel_t2, cruise_t2, accel_t2,
                          0., 0., 0., axis_r2, 0., 0., 0., cruise_v2, accel2)

        # Calculate total time for dwell
        total_time = max(accel_t1 + cruise_t1 + accel_t1,
                         accel_t2 + cruise_t2 + accel_t2)
        self.motion_queuing.note_mcu_movequeue_activity(print_time + total_time)
        toolhead.dwell(total_time)
        toolhead.flush_step_generation()

        # Restore original trapqs and kinematics
        stepper1.set_trapq(prev_trapq1)
        stepper2.set_trapq(prev_trapq2)
        stepper1.set_stepper_kinematics(prev_sk1)
        stepper2.set_stepper_kinematics(prev_sk2)
        self.motion_queuing.wipe_trapq(self.trapq1)
        self.motion_queuing.wipe_trapq(self.trapq2)

    cmd_FORCE_MOVE_DUAL_help = "[DUAL] Move two steppers independently"
    def cmd_FORCE_MOVE_DUAL(self, gcmd):
        stepper1 = self._lookup_stepper(gcmd.get('STEPPER1'))
        stepper2 = self._lookup_stepper(gcmd.get('STEPPER2'))
        dist1 = gcmd.get_float('DISTANCE1')
        speed1 = gcmd.get_float('VELOCITY1', above=0.)
        accel1 = gcmd.get_float('ACCEL1', 0., minval=0.)
        dist2 = gcmd.get_float('DISTANCE2')
        speed2 = gcmd.get_float('VELOCITY2', above=0.)
        accel2 = gcmd.get_float('ACCEL2', 0., minval=0.)
        logging.info("[DUAL] Moving %s and %s independently",
                     stepper1.get_name(), stepper2.get_name())
        self._force_enable(stepper1)
        self._force_enable(stepper2)
        self.manual_move_dual(stepper1, dist1, speed1, accel1,
                              stepper2, dist2, speed2, accel2)

    cmd_STEPPER_BUZZ_DUAL_help = "[DUAL] Oscillate a given stepper to help id it"
    def cmd_STEPPER_BUZZ_DUAL(self, gcmd):
        stepper = self._lookup_stepper(gcmd.get('STEPPER'))
        logging.info('[DUAL] Stepper buzz %s', stepper.get_name())
        did_enable = self._force_enable(stepper)
        toolhead = self.printer.lookup_object('toolhead')
        dist, speed = BUZZ_DISTANCE, BUZZ_VELOCITY
        if stepper.units_in_radians():
            dist, speed = BUZZ_RADIANS_DISTANCE, BUZZ_RADIANS_VELOCITY
        for i in range(10):
            self.manual_move_dual(stepper, dist, speed, 0.,
                                  stepper, -dist, speed, 0.)
            toolhead.dwell(.050)
        self._restore_enable(stepper, did_enable)

    cmd_SET_KINEMATIC_POSITION_DUAL_help = "[DUAL] Force a low-level kinematic position"
    def cmd_SET_KINEMATIC_POSITION_DUAL(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.get_last_move_time()
        curpos = toolhead.get_position()
        x = gcmd.get_float('X', curpos[0])
        y = gcmd.get_float('Y', curpos[1])
        z = gcmd.get_float('Z', curpos[2])
        set_homed = gcmd.get('SET_HOMED', 'xyz').lower()
        set_homed_axes = "".join([a for a in "xyz" if a in set_homed])
        clear_homed = gcmd.get('CLEAR_HOMED', '').lower()
        clear_homed_axes = "".join([a for a in "xyz" if a in clear_homed])
        logging.info("[DUAL] SET_KINEMATIC_POSITION pos=%.3f,%.3f,%.3f",
                     x, y, z)
        toolhead.set_position([x, y, z], homing_axes=set_homed_axes)
        toolhead.get_kinematics().clear_homing_state(clear_homed_axes)

def load_config(config):
    return ForceMoveDual(config)
