import os
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

email = "akunapuli@gmail.com"
password = "rE6jN&TtpaY!HMNX"

vue = PyEmVue()
vue.login(username=email, password=password)

devices = vue.get_devices()
for device in devices:
    if device.model == 'VUE02' or device.model == 'VUE03': # Vue 2 or Utility Connect
        print(f"Device: {device.device_name} ({device.device_gid})")
        # Get usage for the last 1 minute
        usage = vue.get_device_usage_variable(device.device_gid, None, scale=Scale.MINUTE.value, unit=Unit.WATT.value)
        print(f"Usage (W): {usage}")

