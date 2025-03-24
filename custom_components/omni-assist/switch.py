import logging
from typing import Callable, Dict, Any

from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import run_forever, init_entity, EVENTS, DOMAIN, Stream
from .core.stream import Stream

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the OpenAI conversation switch."""
    unique_id = config_entry.data.get("id", config_entry.entry_id)
    
    is_satellite = config_entry.options.get("satellite_mode", False)
    satellite_room = config_entry.options.get("satellite_room", "")
    
    # Create a list of entities to add
    entities = []
    
    # Add standard entities
    entities.append(ListeningSwitch(unique_id, config_entry))
    
    # Add satellite-specific entities if in satellite mode
    if is_satellite:
        entities.append(SatelliteActiveSwitch(unique_id, config_entry, satellite_room))
        
    async_add_entities(entities)


class OmniAssistSwitch(SwitchEntity):
    """Base switch for Omni-Assist"""
    
    def __init__(self, uid: str, config_entry: ConfigEntry):
        self._attr_is_on = False
        self.uid = init_entity(self, None, config_entry)
        self.entry_id = config_entry.entry_id
        self.hass = None

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.hass = self.platform.hass
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{self.uid}-switch_updated",
                self._update_state,
            )
        )
    
    @callback
    def _update_state(self, is_on: bool) -> None:
        """Update switch state."""
        self._attr_is_on = is_on
        self.async_write_ha_state()


class ListeningSwitch(OmniAssistSwitch):
    """Switch that controls if Omni-Assist is listening."""
    
    def __init__(self, uid: str, config_entry: ConfigEntry):
        super().__init__(uid, config_entry)
        self._attr_name = "Listening"
        self._attr_unique_id = f"{uid}-listening"
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on listening."""
        self._attr_is_on = True
        # Use hass.async_create_task to ensure dispatcher_send runs in the event loop
        if self.hass:
            self.hass.async_create_task(self._async_send_event("listening_on"))
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off listening."""
        self._attr_is_on = False
        # Use hass.async_create_task to ensure dispatcher_send runs in the event loop
        if self.hass:
            self.hass.async_create_task(self._async_send_event("listening_off"))
    
    async def _async_send_event(self, event: str) -> None:
        """Send event to dispatcher in a thread-safe way."""
        async_dispatcher_send(self.hass, f"{self.uid}-{event}", self.entry_id)


class SatelliteActiveSwitch(OmniAssistSwitch):
    """Switch that controls if the satellite is active."""
    
    def __init__(self, uid: str, config_entry: ConfigEntry, room_name: str = ""):
        super().__init__(uid, config_entry)
        self._attr_name = f"{room_name or 'Satellite'} Active"
        self._attr_unique_id = f"{uid}-satellite-active"
        self.room_name = room_name
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on satellite."""
        self._attr_is_on = True
        # Use hass.async_create_task to ensure dispatcher_send runs in the event loop
        if self.hass:
            self.hass.async_create_task(self._async_send_event("satellite_on"))
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off satellite."""
        self._attr_is_on = False
        # Use hass.async_create_task to ensure dispatcher_send runs in the event loop
        if self.hass:
            self.hass.async_create_task(self._async_send_event("satellite_off"))
    
    async def _async_send_event(self, event: str) -> None:
        """Send event to dispatcher in a thread-safe way."""
        async_dispatcher_send(self.hass, f"{self.uid}-{event}", {
            "room": self.room_name,
            "entry_id": self.entry_id,
        })


class OmniAssistSatelliteSwitch(OmniAssistSwitch):
    """Switch for satellite mode."""

    def __init__(self, config_entry: ConfigEntry):
        super().__init__(config_entry.entry_id, config_entry)
        _LOGGER.debug("Initializing OmniAssistSatelliteSwitch")
        self._attr_name = f"{config_entry.title} Satellite"
        self._attr_icon = "mdi:satellite-variant"
        
        # Store additional satellite properties
        satellite_room = config_entry.options.get("satellite_room", "")
        if satellite_room:
            self._attr_name = f"{satellite_room} Satellite"
        
        # Wyoming server reference
        self.wyoming_server = None
        
        # Audio stream for the pipeline
        self.stt_stream = None
        
        # Wyoming event callback remove function
        self.remove_wyoming_callback = None
        
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        
        # Get reference to the Wyoming server if it exists
        if (
            DOMAIN in self.hass.data
            and "virtual_satellites" in self.hass.data[DOMAIN]
            and self.entry_id in self.hass.data[DOMAIN]["virtual_satellites"]
        ):
            satellite_data = self.hass.data[DOMAIN]["virtual_satellites"][self.entry_id]
            self.wyoming_server = satellite_data["server"]
            
            if self.wyoming_server:
                _LOGGER.debug(f"Connected to Wyoming server for {self.entry_id}")
                
                # Register for Wyoming events
                self.remove_wyoming_callback = self.wyoming_server.register_event_callback(
                    self.wyoming_event_callback
                )
            else:
                _LOGGER.warning(f"Wyoming server not found for {self.entry_id}")
        
    async def wyoming_event_callback(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Handle Wyoming protocol events."""
        _LOGGER.debug(f"Received Wyoming event: {event_type}")
        
        # Handle Wyoming events
        if event_type == "wake-detected":
            # Wake word detected via Wyoming, create a pipeline event
            _LOGGER.debug("Wake word detected via Wyoming, creating pipeline event")
            
            # Update entity state
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-wake", "end", 
                {"wake_word_id": event_data.get("wake_word_id", "wyoming")}
            )
            
            # TODO: Start the pipeline
            
        elif event_type == "audio-start":
            # Audio stream started, set wake to "start" state
            _LOGGER.debug("Audio stream started via Wyoming")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
            )
            
        elif event_type == "audio-stop":
            # Audio stream stopped
            _LOGGER.debug("Audio stream stopped via Wyoming")
            
    async def tts_callback(self, audio_data: bytes) -> None:
        """Send TTS audio to connected Wyoming clients."""
        if self.wyoming_server:
            await self.wyoming_server.send_audio(audio_data)
        
    async def async_turn_on(self) -> None:
        """Turn on satellite mode."""
        _LOGGER.debug("Attempting to turn on OmniAssistSatelliteSwitch")
        if self._attr_is_on:
            _LOGGER.debug("OmniAssistSatelliteSwitch is already on")
            return

        self._attr_is_on = True
        self._async_write_ha_state()
        _LOGGER.debug("Set OmniAssistSatelliteSwitch state to on")

        # Set all entities to idle initially
        for event in EVENTS:
            _LOGGER.debug(f"Dispatching initial state for: {self.uid}-{event}")
            if event == "wake":
                # Wake entity should show "start" when satellite is on but pipeline isn't active
                async_dispatcher_send(self.hass, f"{self.uid}-{event}", "start")
            else:
                # Other entities remain idle
                async_dispatcher_send(self.hass, f"{self.uid}-{event}", None)
                
        # If we have a Wyoming server, make sure it's running
        if self.wyoming_server and not self.wyoming_server.is_running:
            try:
                _LOGGER.debug("Starting Wyoming server for satellite")
                await self.wyoming_server.start()
            except Exception as e:
                _LOGGER.error(f"Error starting Wyoming server: {e}")
        
        # Create stream for audio input
        self.stt_stream = Stream()
        
        # Start the pipeline
        try:
            _LOGGER.debug("Setting up run_forever for satellite mode")
            self.on_close = run_forever(
                self.hass,
                self.options,
                context=self._context,
                event_callback=self.event_callback,
                tts_audio_callback=self.tts_callback,
            )
            _LOGGER.debug("run_forever set up successfully")
        except Exception as e:
            _LOGGER.error(f"Error turning on OmniAssistSatelliteSwitch: {e}")
            self._attr_is_on = False
            self._async_write_ha_state()
            
    async def async_turn_off(self) -> None:
        """Turn off satellite mode."""
        _LOGGER.debug("Attempting to turn off OmniAssistSatelliteSwitch")
        if not self._attr_is_on:
            _LOGGER.debug("OmniAssistSatelliteSwitch is already off")
            return

        self._attr_is_on = False
        self._async_write_ha_state()
        _LOGGER.debug("Set OmniAssistSatelliteSwitch state to off")

        # Set all entities to off
        for event in EVENTS:
            _LOGGER.debug(f"Dispatching off state for: {self.uid}-{event}")
            async_dispatcher_send(self.hass, f"{self.uid}-{event}", None)

        # Close the pipeline stream
        if self.on_close:
            _LOGGER.debug("Calling on_close")
            self.on_close()
            self.on_close = None
            
        # Close the audio stream
        if self.stt_stream:
            self.stt_stream.close()
            self.stt_stream = None
            
    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        # Clean up Wyoming event callback
        if self.remove_wyoming_callback:
            self.remove_wyoming_callback()
            self.remove_wyoming_callback = None
            
        # Properly shut down
        if self._attr_is_on:
            await self.async_turn_off()
            
        await super().async_will_remove_from_hass()
