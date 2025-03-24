import logging
from typing import Any

from homeassistant.components import assist_pipeline
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import EVENTS, init_entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    unique_id = init_entity(None, None, config_entry)
    
    # Create status sensors, with appropriate naming based on whether this is a satellite
    is_satellite = config_entry.options.get("satellite_mode", False)
    
    entities = []
    for key in EVENTS:
        entities.append(StatusSensor(unique_id, key, config_entry, is_satellite))

    async_add_entities(entities)


class StatusSensor(SensorEntity):
    def __init__(self, uid: str, key: str, config_entry: ConfigEntry, is_satellite: bool = False):
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self.uid = init_entity(self, key, config_entry)
        self.topic = f"{uid}-{key}"
        self.entry_id = config_entry.entry_id
        
        # Add satellite-specific attributes if in satellite mode
        if is_satellite:
            satellite_room = config_entry.options.get("satellite_room", "")
            if satellite_room and key == "wake":
                self._attr_name = f"{satellite_room} Wake Word"
            elif satellite_room and key == "stt":
                self._attr_name = f"{satellite_room} Speech"
            elif satellite_room and key == "intent":
                self._attr_name = f"{satellite_room} Intent"
            elif satellite_room and key == "tts":
                self._attr_name = f"{satellite_room} Response"
                
            # Add satellite room as an attribute for filtering in UI
            self._attr_extra_state_attributes["satellite_room"] = satellite_room
            self._attr_extra_state_attributes["is_satellite"] = True

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.topic, self.update_state)
        )

    def update_state(self, state: str, data: dict[str, Any] = None) -> None:
        self._attr_native_value = state

        if data:
            self._attr_extra_state_attributes = data
        elif state is None:
            self._attr_extra_state_attributes = {}

        self.async_write_ha_state()
