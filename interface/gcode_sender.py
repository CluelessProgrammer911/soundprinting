import asyncio
import json
import websockets

# MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"
MOONRAKER_WS = "ws://localhost:7125/websocket"


debug_mode = False


async def send_gcode(script):
    """Send G-code command via Moonraker WebSocket"""
    try:
        async with websockets.connect(MOONRAKER_WS) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "printer.gcode.script",
                "params": {"script": script},
                "id": 1
            }))
            resp = await ws.recv()
            if debug_mode:
                print(f"G-code response: {json.loads(resp)}")
    except Exception as e:
        print(f"Error sending G-code: {e}")
