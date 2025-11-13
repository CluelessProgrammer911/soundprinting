import requests
import json

MOONRAKER_URL = "http://192.168.101.8:7125"

# response = requests.get(f"{MOONRAKER_URL}/printer/info")
# print(json.dumps(response.json(), indent=2))

response = requests.post(
    f"{MOONRAKER_URL}/printer/gcode/script",
    json={"script": "G28 X"}  # just the G-code itself
)

# response = requests.post(
#     f"{MOONRAKER_URL}/printer/gcode/script",
#     json={"script": "M106 S0"}  # S255 = full speed (0–255 range)
# )



# distance_mm = -40
# response = requests.post(
#     f"{MOONRAKER_URL}/printer/gcode/script",
#     json={"script": f"FORCE_MOVE STEPPER=stepper_x DISTANCE={distance_mm} VELOCITY=10"}
# )

print(response.json())