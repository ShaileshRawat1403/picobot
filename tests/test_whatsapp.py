#!/usr/bin/env python3
"""Simple test to send a message to WhatsApp via the bridge.

Run manually: python tests/test_whatsapp.py
This requires the WhatsApp bridge to be running.
"""

import asyncio
import json

try:
    import pytest
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    import pytest
    WEBSOCKETS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not WEBSOCKETS_AVAILABLE, reason="websockets not installed")


async def main():
    if not WEBSOCKETS_AVAILABLE:
        print("❌ websockets not installed. Run: pip install websockets")
        return

    uri = "ws://localhost:3001"

    try:
        async with websockets.connect(uri, ping_interval=None) as ws:
            print("✅ Connected to WhatsApp bridge!")

            # Send a test message
            payload = {
                "type": "send",
                "to": "+919370449266",
                "text": "Hello from picobot! Test message."
            }

            await ws.send(json.dumps(payload))
            print(f"📤 Sent: {payload['text']}")

            # Wait for response with timeout
            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=10)
                    print(f"📥 Response: {response}")
                    data = json.loads(response)
                    if data.get('type') == 'sent' and data.get('success'):
                        print("✅ Message sent successfully!")
                        return
                    elif data.get('type') == 'error':
                        print(f"❌ Error: {data.get('message')}")
                        return
            except asyncio.TimeoutError:
                print("⏱️ Timeout waiting for response")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
