"""Smart Heating: zone and priority-list heating control for Home Assistant.

0.1.0 ships the controller (controller.py), data model (model.py) and seed
configuration (seed.py) with their tests. The Home Assistant side (config flow,
store, coordinator, actuator, entities) arrives in the dry-run stage; until
then this package is not meant to be installed on a live instance.
"""

DOMAIN = "smart_heating"
