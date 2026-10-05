from tuya_connector import TuyaOpenAPI

ACCESS_ID = "w3fe7usndqww9myj49wm"
ACCESS_KEY = "a20edcf693f04003a9fe3ea38ba9b74b"
API_ENDPOINT = "https://openapi.tuyain.com"

openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
openapi.connect()

# Query all devices linked to this project
res = openapi.get('/v1.0/iot-01/associated-users/devices', {'page_no': 1, 'page_size': 20})
print(res)
