# loop_move_x.py - Continuous Axis Motion Controller

## Overview

`loop_move_x.py` is a Klipper extra (plugin) that provides continuous looping motion control for 3D printer axes (X, Y, and Z) with integrated fan control capabilities. It enables automated drip-feed style motion between configurable bounds and supports dynamic fan speed adjustment and cycling.

## Purpose

This module allows you to:
- **Execute continuous looping motion** on X, Y, and Z axes simultaneously between predefined bounds
- **Control motion parameters** (step distance and speed) using preset levels (0-5)
- **Manage cooling fans** including part cooling fan and hotend heater fan
- **Cycle fans on/off** with configurable timing for testing purposes

## Key Components

### Motion Presets

The module includes two main motion preset dictionaries:

- **`MOTION_SETTINGS_XY`**: Defines step distance (mm) and speed (mm/s) for X and Y axes
  - Presets 0-5 range from stationary (0.0 mm, 0.0 mm/s) to maximum motion (5.0 mm, 100 mm/s)
  
- **`MOTION_SETTINGS_Z`**: Defines step distance (mm) and speed (mm/s) for Z axis
  - Presets 0-5 range from stationary (0.0 mm, 0.0 mm/s) to maximum motion (0.25 mm, 5 mm/s)
  - Z motions are typically smaller and slower due to precision requirements

- **`FAN_SPEED_PRESETS`**: Defines fan speed levels (0-5) mapped to speed values (0.0-1.0)

### Classes

#### `AxisConfig`
Manages configuration and state for a single axis (X, Y, or Z).
- Stores axis bounds, preset settings, current step distance, and speed
- Tracks movement direction (1 = forward, -1 = backward)
- Computes next position with automatic boundary detection and direction reversal

#### `FanController` (static utility class)
Provides static methods for fan control:
- `set_part_cooling_fan()`: Sets part cooling fan speed asynchronously
- `set_hotend_fan_state()`: Toggles hotend heater fan on/off
- `get_hotend_fan_state()`: Queries current hotend fan state

#### `FanPlayController` (base class)
Manages fan cycling logic with timer-based on/off cycles.
- Implements timer registration and callback handling
- Provides common cycling logic shared by fan types

#### `PartCoolingFanPlay`
Extends `FanPlayController` to cycle the part cooling fan at a specified speed.

#### `HotendFanPlay`
Extends `FanPlayController` to cycle the hotend fan between on/off states.

#### `LoopMoveX` (main class)
The core Klipper extra that orchestrates all functionality:
- Manages three `AxisConfig` instances for X, Y, and Z axes
- Controls `PartCoolingFanPlay` and `HotendFanPlay` instances
- Registers and handles G-code commands
- Implements the drip-move scheduling loop

## G-Code Commands

### Motion Control

**`START_MOTION [X=level] [Y=level] [Z=level]`**
- Starts continuous looping motion on specified axes
- Parameters: X, Y, Z levels (0-5, where 0 = stationary, 5 = maximum speed)
- Determines initial direction based on current position and axis bounds
- Returns axis configuration information

**`STOP_MOTION`**
- Immediately stops any active looping motion
- Can be called anytime during active motion

**`CHANGE_MOTION [X=level] [Y=level] [Z=level]`**
- Modifies motion parameters while loop is running
- Continues from current position without interruption
- Optional parameters; only specified axes are updated

### Fan Control

**`TUNE_FAN S=level`**
- Sets part cooling fan to a static speed level (0-5)
- Stops any active fan cycling
- Level 0 = off, Level 5 = maximum speed

**`TOGGLE_HOTEND_FAN`**
- Toggles hotend heater fan between on and off
- Stops any active hotend fan cycling

**`PLAY_FAN S=level U=on_ms D=off_ms`**
- Cycles part cooling fan on/off with specified timing
- Parameters:
  - `S`: Speed level (0-5)
  - `U`: On-time duration (milliseconds)
  - `D`: Off-time duration (milliseconds)
- Minimum cycle time: 50ms (enforced to prevent system issues)
- Edge cases:
  - If U < 50ms: Fan turns off (no cycling)
  - If D < 50ms: Fan stays at specified speed (no cycling)
  - If S = 0: Fan turns off (no cycling)

**`PLAY_HOTEND_FAN U=on_ms D=off_ms`**
- Cycles hotend fan on/off with specified timing
- Parameters:
  - `U`: On-time duration (milliseconds)
  - `D`: Off-time duration (milliseconds)
- Minimum cycle time: 50ms (enforced)
- Edge cases:
  - If U < 50ms: Fan turns off (no cycling)
  - If D < 50ms: Fan turns on (no cycling)

## Technical Details

### Motion Algorithm

1. **Initialization**: When `START_MOTION` is called, the module:
   - Records the current toolhead position as origin
   - Applies preset motion parameters for each axis
   - Determines initial movement direction based on proximity to bounds

2. **Drip-Feed Loop**: Continuously:
   - Computes next position for each axis based on step distance and direction
   - Reverses direction when axis reaches upper or lower bound
   - Executes drip move to next position at the maximum speed across all active axes
   - Schedules next move via reactor callback

3. **Axis Bounds** (configurable in code):
   - X: -10 to 234 mm
   - Y: -8 to 234 mm
   - Z: 2 to 270 mm

### Motion Control vs. Fan Control: Two Different Architectures

#### Stepper Motors: Coordinated, Queue-Based Control

Klipper's stepper motor control follows a **strictly synchronized, queue-based architecture**:

- **Single motion queue**: Klipper maintains one motion queue for the toolhead. Individual stepper motors (X, Y, Z) cannot be driven independently or asynchronously.
- **Coordinated moves only**: Every position update must be expressed as a single combined move. The module computes the next XYZ target position on each iteration and submits all three coordinates together via `toolhead.drip_move(new_pos, move_speed, completion)`.
- **Queue ordering**: Moves execute in strict FIFO order through the queue, preserving timing synchronization across all axes. There is no way to "jump ahead" or control one axis while another is moving.
- **Blocking behavior**: Each `drip_move()` call requires a `completion` object (from `reactor.completion()`) to signal when the move finishes, enabling the next move to be chained. The reactor callback (`reactor.register_callback()`) schedules subsequent moves immediately (`reactor.NOW`), creating a continuous loop.
- **Bypassing high-level layers**: Calling `drip_move()` directly skips several higher-level processing stages that normal G-code commands go through:
  - **G-code parsing**: Normal moves use `G0`/`G1` commands that must be parsed and validated
  - **Lookahead planning**: Standard moves participate in lookahead buffering to optimize corner velocities and smooth junctions between moves
  - **Acceleration profiling**: Normal moves calculate full acceleration/deceleration trapezoids based on configured limits
  - **drip_move() instead**: Provides a lower-level interface for bounded, rate-controlled moves without automatic acceleration planning. This allows precise, immediate control over each incremental step in the loop, which is essential for the continuous drip-feed motion pattern.
- **Result**: X, Y, and Z axes always move in lockstep coordination, never diverging into separate unsynchronized motions.

#### Fans: Independent, Asynchronous Control

Unlike stepper motors, **fans operate completely independently** and do not use the motion queue:

- **Part cooling fan**: Controlled via `fan.fan.gcrq.send_async_request(speed)`, which updates the fan speed immediately without blocking or coordinating with motion commands. The request bypasses the motion queue entirely.
- **Hotend heater fan**: Controlled by directly manipulating object properties (`heater_temp`, `last_speed`) and calling `fan.set_speed()`, with no interaction with the toolhead or motion system.
- **Fan cycling**: Uses reactor timers (`reactor.register_timer()`) that fire independently of motion, allowing fans to cycle on/off while axes continue their coordinated movement.
- **Result**: Fan speed changes happen **asynchronously** and **immediately**, without waiting for motion commands to complete or affecting toolhead synchronization. You can change fan speeds or start/stop fan cycling at any time, even during active motion loops.

### Klipper API Touchpoints

- **toolhead**: The module calls `toolhead.drip_move()` to execute each step of the loop. `drip_move` performs a bounded, rate-controlled move and is chained repeatedly to create continuous motion. The toolhead object also exposes current position via `get_position()`.
- **reactor**: Used for scheduling asynchronous work. `reactor.register_callback()` chains the next move immediately (`reactor.NOW`), while `reactor.register_timer()` drives fan cycling with on/off durations. `reactor.completion()` supplies a completion object required by `drip_move` for coordinated motion.
- **gcode**: `gcode.register_command()` wires user-facing G-code commands (START_MOTION, STOP_MOTION, etc.) to the handlers in `LoopMoveX`.
- **fan / heater_fan**: Retrieved with `printer.lookup_object()`. The part cooling fan is driven through `fan.fan.gcrq.send_async_request()`, and the hotend heater fan is forced on/off by manipulating `heater_temp` and `last_speed` on the `heater_fan hotend_fan` object.

### Fan Cycling

- Uses Klipper's reactor timer system for precise timing
- Alternates between on and off states based on specified durations
- Minimum cycle time (50ms) prevents system resource exhaustion
- Cycling is stopped when static speed commands are issued

## Usage Example

```gcode
; Start continuous motion with X and Y at mid-speed, Z stationary
START_MOTION X=3 Y=3 Z=0

; Change motion parameters while running (Z now active)
CHANGE_MOTION Z=2

; Stop all motion
STOP_MOTION

; Set part cooling fan to medium speed
TUNE_FAN S=3

; Cycle part cooling fan on/off: 500ms on, 500ms off at level 4
PLAY_FAN S=4 U=500 D=500

; Cycle hotend fan: 1000ms on, 1000ms off
PLAY_HOTEND_FAN U=1000 D=1000
```

## Integration

This module is loaded as a Klipper extra via the `load_config()` function. It requires:
- Access to the printer's toolhead object for motion control
- Access to fan objects for fan control
- Klipper's reactor system for timer-based operations

## Logging

The module uses Python's `logging` module for debug and error reporting:
- Debug messages log each drip move position and direction
- Warning messages report invalid preset values
- Exception logging captures and reports errors in callbacks
