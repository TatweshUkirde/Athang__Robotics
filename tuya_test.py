from tuya_connector import TuyaOpenAPI

ACCESS_ID = "w3fe7usndqww9myj49wm"
ACCESS_KEY = "a20edcf693f04003a9fe3ea38ba9b74b"
API_ENDPOINT = "https://openapi.tuyain.com"

print("Testing connection with new project...")
openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
res = openapi.connect()
print(f"Connection Result: {res}")
