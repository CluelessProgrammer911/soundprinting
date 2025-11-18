# MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"
import asyncio
import json
import websockets

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"

async def main():
    async with websockets.connect(MOONRAKER_WS) as ws:
        # Subscribe to printer objects (optional, e.g., temperatures)
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {"objects": {"toolhead": ["position"]}},
            "id": 1
        }))
        
        # Send G-code command
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "printer.gcode.script",
            "params": {"script": "G1 X40 F1000"},
            "id": 2
        }))

        # Listen for responses (loop for multiple messages)
        for _ in range(3):
            resp = await ws.recv()
            print(json.loads(resp))

asyncio.run(main())