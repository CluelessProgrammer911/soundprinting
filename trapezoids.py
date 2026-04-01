import matplotlib.pyplot as plt
import numpy as np

# Constants for XY steppers
MAX_ACCEL_XY = 2000  # mm/s^2
DELTA_TIME = 0.1  # 100 ms in seconds

# Velocity mapping for XY steppers
VELOCITY_MAP_XY = {
    0: 0,
    1: 10,
    2: 20,
    3: 50,
    4: 80,
    5: 100
}

# Constants for Z stepper
MAX_ACCEL_Z = 100  # mm/s^2

# Velocity mapping for Z stepper
VELOCITY_MAP_Z = {
    0: 0.0,
    1: 1.0,
    2: 2.0,
    3: 3.0,
    4: 4.0,
    5: 5.0
}

# Legacy constants for backward compatibility
MAX_ACCEL = MAX_ACCEL_XY
VELOCITY_MAP = VELOCITY_MAP_XY

def generate_trapezoidal_profile(target_velocity, max_accel=None):
    """
    Generate a trapezoidal velocity profile.
    
    Parameters:
    - target_velocity: Target velocity in mm/s
    - max_accel: Maximum acceleration in mm/s^2 (default: MAX_ACCEL_XY)
    
    Returns:
    - time_array: Array of time points
    - velocity_array: Array of velocity values
    """
    if max_accel is None:
        max_accel = MAX_ACCEL_XY
    
    # Calculate time to accelerate and decelerate
    t_accel = target_velocity / max_accel
    t_decel = target_velocity / max_accel
    
    # Calculate cruise time
    t_cruise = DELTA_TIME - t_accel - t_decel
    
    # Check if profile is valid
    if t_cruise < 0:
        print(f"Warning: Target velocity {target_velocity} mm/s exceeds maximum (100 mm/s)")
        print("Using triangular profile at maximum velocity")
        target_velocity = 100
        t_accel = target_velocity / MAX_ACCEL
        t_decel = t_accel
        t_cruise = 0
    
    # Generate time array with high resolution
    time_points = []
    velocity_points = []
    
    # Acceleration phase
    t_end_accel = t_accel
    t = np.linspace(0, t_accel, 100)
    v = max_accel * t
    time_points.extend(t)
    velocity_points.extend(v)
    
    # Cruise phase
    if t_cruise > 0:
        t_end_cruise = t_end_accel + t_cruise
        t = np.linspace(t_end_accel, t_end_cruise, 100)
        v = np.ones_like(t) * target_velocity
        time_points.extend(t)
        velocity_points.extend(v)
    else:
        t_end_cruise = t_end_accel
    
    # Deceleration phase
    t_end_decel = t_end_cruise + t_decel
    t = np.linspace(t_end_cruise, t_end_decel, 100)
    v = target_velocity - max_accel * (t - t_end_cruise)
    time_points.extend(t)
    velocity_points.extend(v)
    
    return np.array(time_points), np.array(velocity_points)


def plot_trapezoidal_profile(target_velocity):
    """
    Plot the trapezoidal velocity profile.
    
    Parameters:
    - target_velocity: Target velocity in mm/s
    """
    time_array, velocity_array = generate_trapezoidal_profile(target_velocity)
    
    plt.figure(figsize=(10, 6))
    plt.plot(time_array * 1000, velocity_array, 'b-', linewidth=2)
    plt.xlabel('Time (ms)', fontsize=12)
    plt.ylabel('Velocity (mm/s)', fontsize=12)
    plt.title(f'Trapezoidal Velocity Profile (Target: {target_velocity} mm/s)', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.xlim(0, 100)
    plt.ylim(0, max(velocity_array) * 1.1)
    
    # Add annotations
    t_accel = target_velocity / MAX_ACCEL
    t_cruise = DELTA_TIME - 2 * t_accel
    
    plt.axhline(y=target_velocity, color='r', linestyle='--', alpha=0.5, label='Target velocity')
    plt.text(5, target_velocity + 5, f'v_target = {target_velocity} mm/s', fontsize=10)
    
    # Mark phases
    plt.axvline(x=t_accel * 1000, color='g', linestyle=':', alpha=0.5)
    if t_cruise > 0:
        plt.axvline(x=(t_accel + t_cruise) * 1000, color='g', linestyle=':', alpha=0.5)
        plt.text(t_accel * 500, -10, 'Accel', ha='center', fontsize=9)
        plt.text((t_accel + t_cruise/2) * 1000, -10, 'Cruise', ha='center', fontsize=9)
        plt.text((t_accel + t_cruise + t_accel/2) * 1000, -10, 'Decel', ha='center', fontsize=9)
    else:
        plt.text(t_accel * 500, -10, 'Accel', ha='center', fontsize=9)
        plt.text((t_accel + t_accel/2) * 1000, -10, 'Decel', ha='center', fontsize=9)
    
    plt.tight_layout()
    plt.show()


def plot_multiple_profiles():
    """
    Plot multiple trapezoidal profiles for comparison.
    """
    target_velocities = [30, 50, 70, 100]
    
    plt.figure(figsize=(12, 8))
    
    for v_target in target_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target)
        label = f'v_target = {v_target} mm/s'
        if v_target == 100:
            label += ' (triangle)'
        plt.plot(time_array * 1000, velocity_array, linewidth=2, label=label)
    
    plt.xlabel('Time (ms)', fontsize=12)
    plt.ylabel('Velocity (mm/s)', fontsize=12)
    plt.title('Trapezoidal Velocity Profiles Comparison', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=10)
    plt.xlim(0, 100)
    plt.ylim(0, 110)
    plt.tight_layout()
    plt.show()


def plot_sequential_profiles(velocity_indices):
    """
    Plot multiple trapezoidal profiles in sequence on a continuous timeline.
    
    Parameters:
    - velocity_indices: List of velocity indices (0-5) corresponding to VELOCITY_MAP
                        Example: [0, 3, 4, 1, 2, 3, 5]
    """
    # Convert indices to actual velocities
    target_velocities = [VELOCITY_MAP[idx] for idx in velocity_indices]
    
    # Generate all profiles
    all_time_points = []
    all_velocity_points = []
    current_time = 0
    
    for v_target in target_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target)
        
        # Offset time by current time
        time_array_offset = time_array + current_time
        all_time_points.extend(time_array_offset)
        all_velocity_points.extend(velocity_array)
        
        # Update current time for next profile
        current_time += DELTA_TIME
    
    # Create plot
    plt.figure(figsize=(14, 6))
    plt.plot(np.array(all_time_points) * 1000, all_velocity_points, 'b-', linewidth=2)
    
    # Add vertical lines to mark profile boundaries
    for i in range(1, len(target_velocities)):
        boundary_time = i * DELTA_TIME
        plt.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    # Labels and formatting
    plt.xlabel('Time (ms)', fontsize=12)
    plt.ylabel('Velocity (mm/s)', fontsize=12)
    plt.title('Sequential Trapezoidal Profiles', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.ylim(0, max(VELOCITY_MAP.values()) * 1.1)
    
    plt.tight_layout()
    plt.show()


def annotate_sequence_labels(ax, velocity_indices, axis_prefix, text_color):
    """Annotate each profile segment with an axis-prefixed index label at the top of an axis."""
    for i, idx in enumerate(velocity_indices):
        center_time_ms = (i + 0.5) * DELTA_TIME * 1000
        ax.text(
            center_time_ms,
            0.96,
            f'{axis_prefix}{idx}',
            transform=ax.get_xaxis_transform(),
            ha='center',
            va='top',
            fontsize=10,
            color=text_color
        )


def plot_dual_stepper_profiles(x_velocity_indices, y_velocity_indices):
    """
    Plot sequential trapezoidal profiles for two stepper motors (x and y) side by side.
    
    Parameters:
    - x_velocity_indices: List of velocity indices (0-5) for x_stepper
                          Example: [0, 3, 4, 1, 2, 3, 5]
    - y_velocity_indices: List of velocity indices (0-5) for y_stepper
                          Example: [1, 2, 3, 4, 5, 0, 1]
    """
    # Create figure with 2 subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    
    # Plot X stepper
    x_velocities = [VELOCITY_MAP_XY[idx] for idx in x_velocity_indices]
    x_time_points = []
    x_velocity_points = []
    current_time = 0
    
    for v_target in x_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target, MAX_ACCEL_XY)
        time_array_offset = time_array + current_time
        x_time_points.extend(time_array_offset)
        x_velocity_points.extend(velocity_array)
        current_time += DELTA_TIME
    
    ax1.plot(np.array(x_time_points) * 1000, x_velocity_points, 'b-', linewidth=2)
    for i in range(1, len(x_velocities)):
        boundary_time = i * DELTA_TIME
        ax1.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    ax1.set_ylabel('Velocity (mm/s)', fontsize=12)
    ax1.set_title('X Stepper', fontsize=14)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, max(VELOCITY_MAP_XY.values()) * 1.1)
    annotate_sequence_labels(ax1, x_velocity_indices, 'x', 'b')
    
    # Plot Y stepper
    y_velocities = [VELOCITY_MAP_XY[idx] for idx in y_velocity_indices]
    y_time_points = []
    y_velocity_points = []
    current_time = 0
    
    for v_target in y_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target, MAX_ACCEL_XY)
        time_array_offset = time_array + current_time
        y_time_points.extend(time_array_offset)
        y_velocity_points.extend(velocity_array)
        current_time += DELTA_TIME
    
    ax2.plot(np.array(y_time_points) * 1000, y_velocity_points, 'g-', linewidth=2)
    for i in range(1, len(y_velocities)):
        boundary_time = i * DELTA_TIME
        ax2.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    ax2.set_xlabel('Time (ms)', fontsize=12)
    ax2.set_ylabel('Velocity (mm/s)', fontsize=12)
    ax2.set_title('Y Stepper', fontsize=14)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, max(VELOCITY_MAP_XY.values()) * 1.1)
    annotate_sequence_labels(ax2, y_velocity_indices, 'y', 'g')

    plt.show()


def plot_triple_stepper_profiles(x_velocity_indices, y_velocity_indices, z_velocity_indices):
    """
    Plot sequential trapezoidal profiles for three stepper motors (x, y, and z).
    
    Parameters:
    - x_velocity_indices: List of velocity indices (0-5) for x_stepper
                          Example: [0, 3, 4, 1, 2, 3, 5]
    - y_velocity_indices: List of velocity indices (0-5) for y_stepper
                          Example: [1, 2, 3, 4, 5, 0, 1]
    - z_velocity_indices: List of velocity indices (0-5) for z_stepper
                          Example: [0, 1, 2, 3, 4, 5, 0]
    """
    # Create figure with 3 subplots
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12), sharex=True, constrained_layout=True)
    
    # Plot X stepper
    x_velocities = [VELOCITY_MAP_XY[idx] for idx in x_velocity_indices]
    x_time_points = []
    x_velocity_points = []
    current_time = 0
    
    for v_target in x_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target, MAX_ACCEL_XY)
        time_array_offset = time_array + current_time
        x_time_points.extend(time_array_offset)
        x_velocity_points.extend(velocity_array)
        current_time += DELTA_TIME
    
    ax1.plot(np.array(x_time_points) * 1000, x_velocity_points, 'b-', linewidth=2)
    for i in range(1, len(x_velocities)):
        boundary_time = i * DELTA_TIME
        ax1.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    ax1.set_ylabel('Velocity (mm/s)', fontsize=12)
    ax1.set_title('X Stepper', fontsize=14)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, max(VELOCITY_MAP_XY.values()) * 1.1)
    annotate_sequence_labels(ax1, x_velocity_indices, 'x', 'b')
    
    # Plot Y stepper
    y_velocities = [VELOCITY_MAP_XY[idx] for idx in y_velocity_indices]
    y_time_points = []
    y_velocity_points = []
    current_time = 0
    
    for v_target in y_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target, MAX_ACCEL_XY)
        time_array_offset = time_array + current_time
        y_time_points.extend(time_array_offset)
        y_velocity_points.extend(velocity_array)
        current_time += DELTA_TIME
    
    ax2.plot(np.array(y_time_points) * 1000, y_velocity_points, 'g-', linewidth=2)
    for i in range(1, len(y_velocities)):
        boundary_time = i * DELTA_TIME
        ax2.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    ax2.set_ylabel('Velocity (mm/s)', fontsize=12)
    ax2.set_title('Y Stepper', fontsize=14)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, max(VELOCITY_MAP_XY.values()) * 1.1)
    annotate_sequence_labels(ax2, y_velocity_indices, 'y', 'g')
    
    # Plot Z stepper
    z_velocities = [VELOCITY_MAP_Z[idx] for idx in z_velocity_indices]
    z_time_points = []
    z_velocity_points = []
    current_time = 0
    
    for v_target in z_velocities:
        time_array, velocity_array = generate_trapezoidal_profile(v_target, MAX_ACCEL_Z)
        time_array_offset = time_array + current_time
        z_time_points.extend(time_array_offset)
        z_velocity_points.extend(velocity_array)
        current_time += DELTA_TIME
    
    ax3.plot(np.array(z_time_points) * 1000, z_velocity_points, 'm-', linewidth=2)
    for i in range(1, len(z_velocities)):
        boundary_time = i * DELTA_TIME
        ax3.axvline(x=boundary_time * 1000, color='r', linestyle='--', alpha=0.3)
    
    ax3.set_xlabel('Time (ms)', fontsize=12)
    ax3.set_ylabel('Velocity (mm/s)', fontsize=12)
    ax3.set_title('Z Stepper', fontsize=14)
    ax3.grid(True, alpha=0.3)
    ax3.set_ylim(0, max(VELOCITY_MAP_Z.values()) * 1.2)
    annotate_sequence_labels(ax3, z_velocity_indices, 'z', 'm')

    plt.show()


# Example usage
if __name__ == "__main__":
    # Plot single profile
    # target_vel = 70  # Change this value to test different velocities
    # plot_trapezoidal_profile(target_vel)
    
    # Plot multiple profiles for comparison
    # plot_multiple_profiles()
    
    # Plot sequential profiles for single stepper
    # target_velocities = [0, 3, 4, 1, 2, 3, 5]
    # plot_sequential_profiles(target_velocities)
    
    # Plot sequential profiles for dual steppers (x and y)
    # x_velocity_indices = [0, 3, 4, 1, 2, 3, 5]
    # y_velocity_indices = [1, 2, 3, 4, 5, 0, 0]
    # plot_dual_stepper_profiles(x_velocity_indices, y_velocity_indices)
    
    # Plot sequential profiles for triple steppers (x, y, and z)
    x_velocity_indices = [0, 1, 2, 3, 4, 5, 5]
    y_velocity_indices = [1, 5, 4, 4, 3, 2, 0]
    z_velocity_indices = [0, 0, 3, 3, 4, 2, 0]
    plot_triple_stepper_profiles(x_velocity_indices, y_velocity_indices, z_velocity_indices)
