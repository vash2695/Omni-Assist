from homeassistant.components import assist_pipeline
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import logging

from .core import EVENTS, init_entity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug("Setting up OmniAssist sensors")
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
    _LOGGER.debug(f"Added {len(entities)} OmniAssist sensors")


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
        self.uid = init_entity(self, key, config_entry)
        self.key = key
        
        # Set the sorting order to match the processing sequence
        order_num = self.SENSOR_ORDER.get(key, 9)
        
        # Simply prepend a number to the name to control sort order
        self._attr_name = f"{order_num} {key.upper()}"
        
        # Override the icon to match our custom ordering
        self._attr_icon = f"mdi:numeric-{order_num}"
        _LOGGER.debug(f"Initialized sensor {self.unique_id} for stage {key}")

    async def async_added_to_hass(self) -> None:
        _LOGGER.debug(f"Adding dispatcher connection for {self.unique_id}")
        # Connect to the dispatcher signal for this specific entity
        remove = async_dispatcher_connect(self.hass, self.unique_id, self.signal)
        self.async_on_remove(remove)
        
        # Also connect to dispatcher signal with just the stage name for direct service call events
        stage_signal = f"{self.uid}-{self.key}"
        _LOGGER.debug(f"Adding stage-specific dispatcher for {stage_signal}")
        remove_stage = async_dispatcher_connect(self.hass, stage_signal, self.signal)
        self.async_on_remove(remove_stage)

    def signal(self, value: str, extra: dict = None):
        _LOGGER.debug(f"Sensor {self.unique_id} received signal: {value} with extra: {extra}")
        self._attr_native_value = value or STATE_IDLE
        self._attr_extra_state_attributes = extra
        self.schedule_update_ha_state()
