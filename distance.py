
def max_distance_for_time(travel_time, max_velocity, max_accel):
    """
    Calculate the maximum distance achievable in a given time
    with start and end velocity = 0, using trapezoidal motion profile.

    Parameters:
        travel_time (float): Total time for the move (seconds)
        max_velocity (float): Maximum allowed velocity (mm/s)
        max_accel (float): Maximum allowed acceleration (mm/s^2)

    Returns:
        distance (float): Maximum distance in mm
        peak_velocity (float): Peak velocity reached in mm/s
    """
    # Split time into accel and decel phases (triangular profile)
    accel_time = travel_time / 2.0

    # Compute peak velocity based on acceleration
    peak_velocity = max_accel * accel_time

    # If peak velocity exceeds max_velocity, adjust for trapezoidal profile
    if peak_velocity > max_velocity:
        # Time to accelerate to max_velocity
        accel_time = max_velocity / max_accel
        decel_time = accel_time
        cruise_time = travel_time - accel_time - decel_time
        if cruise_time < 0:
            cruise_time = 0
        # Distance = accel + cruise + decel
        distance = (0.5 * max_accel * accel_time**2) \
                   + (max_velocity * cruise_time) \
                   + (0.5 * max_accel * decel_time**2)
        peak_velocity = max_velocity
    else:
        # Triangular profile: accelerate then decelerate
        distance = max_accel * accel_time**2  # accel + decel combined
    return distance, peak_velocity


# Example usage:
max_velocity = 300
max_accel = 2000
max_z_velocity = 5
max_z_accel = 100
delta_time = 0.1
# d, v_peak = max_distance_for_time(travel_time=0.05, max_velocity=max_z_velocity, max_accel=max_z_accel)
d, v_peak = max_distance_for_time(travel_time=delta_time, max_velocity=100, max_accel=max_z_accel)
print(f"Max distance: {d:.3f} mm, Peak velocity: {v_peak:.3f} mm/s")
d, v_peak = max_distance_for_time(travel_time=delta_time, max_velocity=10, max_accel=max_accel)
print(f"Max distance: {d:.3f} mm, Peak velocity: {v_peak:.3f} mm/s")
