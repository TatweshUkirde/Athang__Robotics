import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"\n---> TOPIC: {msg.topic}")
    print(f"---> PAYLOAD: {msg.payload.decode()}")

client = mqtt.Client()
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.subscribe("zigbee2mqtt/#")

print("Listening to all Zigbee2MQTT traffic...")
print("Please press a physical button on your 4-gang switch now.")
client.loop_forever()
