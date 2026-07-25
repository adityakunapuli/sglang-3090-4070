"""Constants for the google_eta integration."""

DOMAIN = "google_eta"
MANUFACTURER = "Custom"

# Config entry keys
CONF_API_KEY = "api_key"
CONF_PERSON_A = "person_a"
CONF_PERSON_B = "person_b"
CONF_COOLDOWN = "cooldown"
CONF_THRESHOLD_M = "threshold_m"

# Defaults
DEFAULT_COOLDOWN = 1800  # 30 minutes
DEFAULT_THRESHOLD_M = 1000  # 1 km

# Route direction keys
ROUTE_A_TO_B = "adi_to_wife"
ROUTE_B_TO_A = "wife_to_adi"

# Sensor unique ID suffixes
SENSOR_A_TO_B = "eta_to_wife"
SENSOR_B_TO_A = "eta_to_me"

# Sensor state source values
SOURCE_FRESH = "fresh"
SOURCE_CACHED = "cached"
SOURCE_IDLE = "idle"

# Sensor attribute keys
ATTR_DISTANCE_M = "distance_m"
ATTR_SOURCE = "source"
ATTR_PERSON_A = "person_a"
ATTR_PERSON_B = "person_b"
ATTR_PERSON_A_COORDS = "person_a_coords"
ATTR_PERSON_B_COORDS = "person_b_coords"
ATTR_DURATION_TEXT = "duration_text"
ATTR_DISTANCE_TEXT = "distance_text"
ATTR_POLYLINE = "polyline"
ATTR_DIRECTION = "direction"
