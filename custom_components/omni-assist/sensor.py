from homeassistant.components import assist_pipeline
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import EVENTS, init_entity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    pipeline_id = config_entry.options.get("pipeline_id")
    pipeline = assist_pipeline.async_get_pipeline(hass, pipeline_id)

    entities = []

    for event in EVENTS:
        if event == "wake" and not pipeline.wake_word_entity:
            continue
        if event == "stt" and not pipeline.stt_engine:
            break
        if event == "tts" and not pipeline.tts_engine:
            continue
        entities.append(OmniAssistSensor(config_entry, event))

    async_add_entities(entities)


class OmniAssistSensor(SensorEntity):
    _attr_native_value = STATE_IDLE
    _attr_has_entity_name = True
    
    # Define the order mapping for sensors - this controls display order
    SENSOR_ORDER = {
        "wake": 1,
        "stt": 2,
        "intent": 3,
        "tts": 4
    }

    def __init__(self, config_entry: ConfigEntry, key: str):
        # First call the standard init function
        init_entity(self, key, config_entry)
        
        # Set the sorting order to match the processing sequence
        order_num = self.SENSOR_ORDER.get(key, 9)
        
        # Simply prepend a number to the name to control sort order
        self._attr_name = f"{order_num} {key.upper()}"
        
        # Override the icon to match our custom ordering
        self._attr_icon = f"mdi:numeric-{order_num}"

    async def async_added_to_hass(self) -> None:
        remove = async_dispatcher_connect(self.hass, self.unique_id, self.signal)
        self.async_on_remove(remove)

    def signal(self, value: str, extra: dict = None):
        self._attr_native_value = value or STATE_IDLE
        self._attr_extra_state_attributes = extra
        self.schedule_update_ha_state()
