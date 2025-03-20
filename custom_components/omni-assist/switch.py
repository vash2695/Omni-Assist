import logging
from typing import Callable

from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import run_forever, init_entity, EVENTS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug("Setting up OmniAssistSwitch")
    async_add_entities([OmniAssistSwitch(config_entry)])

class OmniAssistSwitch(SwitchEntity):
    on_close: Callable | None = None

    def __init__(self, config_entry: ConfigEntry):
        _LOGGER.debug("Initializing OmniAssistSwitch")
        self._attr_is_on = False
        self._attr_should_poll = False
        self.options = config_entry.options.copy()
        self.uid = init_entity(self, "mic", config_entry)
        _LOGGER.debug(f"OmniAssistSwitch initialized with UID: {self.uid}")

    def event_callback(self, event: PipelineEvent):
        _LOGGER.debug(f"Received pipeline event: {event.type}")
        
        # Map pipeline event types to our sensor entity types
        event_type_mapping = {
            "wake_word-start": "wake-start",
            "wake_word-end": "wake-end", 
            "stt-start": "stt-start",
            "stt-end": "stt-end",
            "intent-start": "intent-start",
            "intent-end": "intent-end",
            "tts-start": "tts-start",
            "tts-end": "tts-end",
        }
        
        # Handle the custom reset-after-tts event
        if event.type == "reset-after-tts":
            _LOGGER.debug("TTS playback complete, resetting all entity states")
            
            # Reset wake to "start" state after TTS playback completes
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
            )
            
            # Reset all other entities to idle, including TTS
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-stt", None
            )
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-intent", None
            )
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-tts", None
            )
            return
        
        # Handle error events specially
        if event.type == PipelineEventType.ERROR:
            code = event.data.get("code", "error")
            # Determine which stage had the error
            if "wake_word" in code:
                stage = "wake"
            elif "stt" in code:
                stage = "stt"
            elif "intent" in code:
                stage = "intent"
            elif "tts" in code:
                stage = "tts"
            else:
                stage = "error"
                
            _LOGGER.debug(f"Error in stage {stage}: {code}")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "error", event.data
            )
            
            # After an error, wake entity should return to "start" state
            if stage != "wake":  # Only if the error wasn't in the wake stage
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
                )
            return
        
        # Special handling for TTS start - explicitly show the start state
        if event.type == PipelineEventType.TTS_START:
            _LOGGER.debug("TTS started, setting TTS entity to start state")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-tts", "start", event.data
            )
            return
            
        # Special handling for TTS end to set TTS entity to "running" state during playback
        if event.type == PipelineEventType.TTS_END:
            _LOGGER.debug("TTS processing ended, setting TTS entity to running state during playback")
            
            # Dispatch the tts-end event but use "running" instead of "end"
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-tts", "running", event.data
            )
            
            # State resets will be handled by reset-after-tts event after playback completes
            return
            
        # Process normal pipeline events
        evt_type = event.type
        if evt_type in event_type_mapping:
            # Use our mapping for standard events
            mapped_event = event_type_mapping[evt_type]
            stage, state = mapped_event.split("-", 1)
            
            # Special handling for wake events
            if stage == "wake":
                if state == "end":
                    # When wake word is detected (wake-end), set wake to "end" state
                    _LOGGER.debug(f"Wake word detected, setting wake to end state")
                    self.hass.loop.call_soon_threadsafe(
                        async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "end", event.data
                    )
                # Ignore wake-start events as we manage wake state differently
                return
                
            _LOGGER.debug(f"Dispatching mapped event: {self.uid}-{stage}, state: {state}")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{stage}", state, event.data
            )
        else:
            # For any other event types, try to parse them directly
            try:
                # Try to split standard format "stage-state"
                if "-" in evt_type:
                    raw_stage, state = evt_type.split("-", 1)
                    
                    # Convert wake_word to wake
                    stage = "wake" if raw_stage == "wake_word" else raw_stage
                    
                    # Special handling for wake events
                    if stage == "wake":
                        if state == "end":
                            _LOGGER.debug(f"Wake word detected (raw event), setting wake to end state")
                            self.hass.loop.call_soon_threadsafe(
                                async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "end", event.data
                            )
                        # Ignore wake-start events as we manage wake state differently
                        return
                        
                    _LOGGER.debug(f"Dispatching parsed event: {self.uid}-{stage}, state: {state}")
                    self.hass.loop.call_soon_threadsafe(
                        async_dispatcher_send, self.hass, f"{self.uid}-{stage}", state, event.data
                    )
                else:
                    _LOGGER.warning(f"Unhandled event type: {evt_type}")
            except Exception as e:
                _LOGGER.error(f"Error processing event {evt_type}: {e}")

    async def async_added_to_hass(self) -> None:
        _LOGGER.debug("OmniAssistSwitch added to HASS")
        self.options["assist"] = {"device_id": self.device_entry.id}
        _LOGGER.debug(f"Set device_id in options: {self.device_entry.id}")

    async def async_turn_on(self) -> None:
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
            _LOGGER.debug("Calling run_forever")
            self.on_close = run_forever(
                self.hass,
                self.options,
                context=self._context,
                event_callback=self.event_callback,
            )
            _LOGGER.debug("run_forever completed successfully")
        except Exception as e:
            _LOGGER.error(f"Error turning on OmniAssist: {e}")
            self._attr_is_on = False
            self._async_write_ha_state()

    async def async_turn_off(self) -> None:
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
                _LOGGER.debug("Calling on_close function")
                self.on_close()  # Changed from await self.on_close()
                _LOGGER.debug("on_close function completed successfully")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist: {e}")
            finally:
                self.on_close = None
                _LOGGER.debug("Reset on_close to None")

    async def async_will_remove_from_hass(self) -> None:
        _LOGGER.debug("OmniAssistSwitch is being removed from HASS")
        if self._attr_is_on and self.on_close is not None:
            try:
                _LOGGER.debug("Calling on_close function during removal")
                self.on_close()  # Changed from await self.on_close()
                _LOGGER.debug("on_close function completed successfully during removal")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist during removal: {e}")
            finally:
                self.on_close = None
                _LOGGER.debug("Reset on_close to None during removal")
