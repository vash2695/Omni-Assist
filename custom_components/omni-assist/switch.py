"""Switch platform for Omni-Assist integration."""

import logging
from typing import Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import run_forever, init_entity
from .core.state_machine import EVENTS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Omni-Assist switch entity."""
    _LOGGER.debug("Setting up OmniAssistSwitch")
    async_add_entities([OmniAssistSwitch(config_entry)])


class OmniAssistSwitch(SwitchEntity):
    """Switch entity to control the Omni-Assist voice assistant."""
    
    on_close: Callable | None = None

    def __init__(self, config_entry: ConfigEntry):
        """Initialize the switch entity."""
        _LOGGER.debug("Initializing OmniAssistSwitch")
        self._attr_is_on = False
        self._attr_should_poll = False
        self.options = config_entry.options.copy()
        self.uid = init_entity(self, "mic", config_entry)
        _LOGGER.debug(f"OmniAssistSwitch initialized with UID: {self.uid}")

    def event_callback(self, event):
        """Process pipeline events.
        
        This callback is passed to run_forever, which:
        1. Creates a state machine
        2. Processes events through the state machine first
        3. Then calls this method
        
        All state updates are handled by the state machine, so this method
        can be used for switch-specific event handling if needed.
        """
        # We don't need additional processing here since the state machine
        # handles dispatching state updates to the sensor entities
        pass

    async def async_added_to_hass(self) -> None:
        """Set up the entity when added to Home Assistant."""
        _LOGGER.debug("OmniAssistSwitch added to HASS")
        self.options["assist"] = {"device_id": self.device_entry.id}
        _LOGGER.debug(f"Set device_id in options: {self.device_entry.id}")

    async def async_turn_on(self) -> None:
        """Turn on the voice assistant (start listening)."""
        _LOGGER.debug("Attempting to turn on OmniAssistSwitch")
        if self._attr_is_on:
            _LOGGER.debug("OmniAssistSwitch is already on")
            return

        self._attr_is_on = True
        self._async_write_ha_state()
        _LOGGER.debug("Set OmniAssistSwitch state to on")

        # Set all entities to idle initially
        for event in EVENTS:
            _LOGGER.debug(f"Dispatching initial state for: {self.uid}-{event}")
            if event == "wake":
                # Wake entity should show "start" when mic is on but pipeline isn't active
                async_dispatcher_send(self.hass, f"{self.uid}-{event}", "start")
            else:
                # Other entities remain idle
                async_dispatcher_send(self.hass, f"{self.uid}-{event}", None)

        try:
            _LOGGER.debug("Starting continuous pipeline processing")
            self.on_close = run_forever(
                self.hass,
                self.options,
                context=self._context,
                event_callback=self.event_callback,
            )
            _LOGGER.debug("Pipeline started successfully")
        except Exception as e:
            _LOGGER.error(f"Error turning on OmniAssist: {e}")
            self._attr_is_on = False
            self._async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn off the voice assistant (stop listening)."""
        _LOGGER.debug("Attempting to turn off OmniAssistSwitch")
        if not self._attr_is_on:
            _LOGGER.debug("OmniAssistSwitch is already off")
            return

        self._attr_is_on = False
        self._async_write_ha_state()
        _LOGGER.debug("Set OmniAssistSwitch state to off")

        # Reset all sensor entities to IDLE state when switch is off
        for event in EVENTS:
            _LOGGER.debug(f"Resetting entity state for: {self.uid}-{event}")
            async_dispatcher_send(self.hass, f"{self.uid}-{event}", None)

        if self.on_close is not None:
            try:
                _LOGGER.debug("Closing audio stream")
                self.on_close()
                _LOGGER.debug("Audio stream closed successfully")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist: {e}")
            finally:
                self.on_close = None
                _LOGGER.debug("Reset on_close to None")

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed from Home Assistant."""
        _LOGGER.debug("OmniAssistSwitch is being removed from HASS")
        if self._attr_is_on and self.on_close is not None:
            try:
                _LOGGER.debug("Closing audio stream during removal")
                self.on_close()
                _LOGGER.debug("Audio stream closed successfully during removal")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist during removal: {e}")
            finally:
                self.on_close = None
