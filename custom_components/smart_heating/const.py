"""Constants for smart_heating."""

DOMAIN = "smart_heating"
STORAGE_KEY = "smart_heating"
STORAGE_VERSION = 1

CONF_BOILER = "boiler_switch"
CONF_FLOW_SOURCE = "flow_temperature_entity"
CONF_NOTIFY = "notify_service"

DEFAULT_BOILER = "switch.boiler"
DEFAULT_FLOW_SOURCE = "input_number.heating_flow_temperature"
DEFAULT_NOTIFY = "notify.mobile_app_honor_600"

TICK_SECONDS = 60
VERIFY_AFTER_SECONDS = 120
MAX_ATTEMPTS = 3

PLATFORMS = ["select", "switch", "sensor", "binary_sensor"]

SIGNAL_UPDATE = "smart_heating_update"

# Notification action ids (mobile_app_notification_action events)
ACTION_TURN_OFF = "SMART_HEATING_TURN_OFF"
ACTION_KEEP_GOING = "SMART_HEATING_KEEP_GOING"
ACTION_IGNORE_WINDOW = "SMART_HEATING_IGNORE_WINDOW_"  # + zone slug
