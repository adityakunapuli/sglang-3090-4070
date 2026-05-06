import os
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Config from environment
url = os.environ.get("INFLUXDB_URL", "http://localhost:8888")
token = os.environ.get("INFLUXDB_TOKEN")
org = os.environ.get("INFLUXDB_ORG", "home")
bucket = os.environ.get("INFLUXDB_BUCKET", "telemetry")

client = InfluxDBClient(url=url, token=token, org=org)
query_api = client.query_api()
write_api = client.write_api(write_options=SYNCHRONOUS)

# Query historical vuegraf data (last 90 days of 'False' detailed records)
# We use abs() to ensure consumption is positive.
query = f'''
from(bucket: "{bucket}")
  |> range(start: -90d)
  |> filter(fn: (r) => r._measurement == "energy_usage" and r._field == "usage" and r.detailed == "False")
'''

tables = query_api.query(query)
count = 0

for table in tables:
    for record in table.records:
        val = abs(float(record.get_value()))
        # Create a point in the new 'energy' measurement
        point = (Point("energy")
                 .tag("circuit", "total")
                 .field("power_w", val)
                 .time(record.get_time()))
        write_api.write(bucket, org, point)
        count += 1

print(f"Backfilled {count} records from energy_usage to energy.")
client.close()
