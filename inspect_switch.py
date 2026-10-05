import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker. Listening to device 0xa4c138286e74b9db...")
    client.subscribe("zigbee2mqtt/0xa4c138286e74b9db")

def on_message(client, userdata, msg):
    print(f"RAW PAYLOAD: {msg.payload.decode()}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.loop_forever()
