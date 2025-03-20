"""Sensor platform for Omni-Assist integration."""

from homeassistant.components import assist_pipeline
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import init_entity
from .core.state_machine import EVENTS, PipelineState


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Omni-Assist sensor entities."""
    # Get the pipeline to determine which sensors to set up
    pipeline_id = config_entry.options.get("pipeline_id")
    pipeline = assist_pipeline.async_get_pipeline(hass, pipeline_id)

    entities = []

    # Only set up sensors for pipeline stages that are enabled
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
    """Sensor entity that displays pipeline state for a specific stage."""
    
    _attr_native_value = STATE_IDLE
    _attr_has_entity_name = True
    
    # Define the order mapping for sensors - this controls display order
    SENSOR_ORDER = {
        "wake": 1,
        "stt": 2,
        "intent": 3,
        "tts": 4
    }

    # Define friendly names for the pipeline states
    STATE_NAMES = {
        PipelineState.IDLE.value: STATE_IDLE,
        PipelineState.START.value: "start",
        PipelineState.RUNNING.value: "running",
        PipelineState.END.value: "end",
        PipelineState.ERROR.value: "error",
    }

    def __init__(self, config_entry: ConfigEntry, key: str):
        """Initialize the sensor entity."""
        # First call the standard init function
        init_entity(self, key, config_entry)
        
        # Set the sorting order to match the processing sequence
        order_num = self.SENSOR_ORDER.get(key, 9)
        
        # Simply prepend a number to the name to control sort order
        self._attr_name = f"{order_num} {key.upper()}"
        
        # Override the icon to match our custom ordering
        self._attr_icon = f"mdi:numeric-{order_num}"

    async def async_added_to_hass(self) -> None:
        """Set up a listener for state changes when added to Home Assistant."""
        remove = async_dispatcher_connect(self.hass, self.unique_id, self.signal)
        self.async_on_remove(remove)

    def signal(self, value: str, extra: dict = None):
        """Handle updates to the entity state.
        
        This is called by the state machine through the dispatcher when
        the pipeline state changes.
        
        Args:
            value: The pipeline state value, which can be a PipelineState enum
                value or a string.
            extra: Optional extra state attributes.
        """
        if value is None:
            self._attr_native_value = STATE_IDLE
        else:
            # Convert enum values to their string representation
            # Handle both direct enum instances and string values that match enum values
            self._attr_native_value = self.STATE_NAMES.get(value, value)
            
        self._attr_extra_state_attributes = extra
        self.schedule_update_ha_state()
