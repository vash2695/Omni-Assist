import logging
from typing import Callable
import time

from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType, PipelineStage
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .core import run_forever, init_entity, EVENTS, DOMAIN
from . import OMNI_ASSIST_REGISTRY

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

    def _reset_entity_states(self, wake_state="start"):
        """Reset all entity states to their default values.
        
        Args:
            wake_state: State to set the wake entity to (default: "start")
        """
        _LOGGER.debug(f"Resetting entity states. Setting wake to '{wake_state}'")
        
        # Set wake to specified state
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send, self.hass, f"{self.uid}-wake", wake_state
        )
        
        # Reset all other entities to idle
        for entity in ["stt", "intent", "tts"]:
            _LOGGER.debug(f"Resetting {entity} entity to idle")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{entity}", None
            )

    def event_callback(self, event: PipelineEvent):
        _LOGGER.debug(f"Received pipeline event: {event.type}")
        
        # Check if this event is for a specific device
        if event.data and "device_uid" in event.data:
            target_uid = event.data.get("device_uid")
            if target_uid != self.uid:
                # Event is not for this device, ignore it
                _LOGGER.debug(f"Ignoring event for different device UID: {target_uid}")
                return
            
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
            "run-start": "run-start",
            "run-end": "run-end",
        }
        
        # Handle the custom reset-after-tts event
        if event.type == "reset-after-tts":
            _LOGGER.debug("TTS playback complete, resetting all entity states")
            
            # Check if we need to start a follow-up conversation
            start_followup = False
            
            # First try to get it from event data
            if event.data and "request_followup" in event.data:
                start_followup = event.data.get("request_followup", False)
            
            # If not found or false, check the device registry as a fallback
            if not start_followup and self.device_entry.id in OMNI_ASSIST_REGISTRY:
                if "request_followup" in OMNI_ASSIST_REGISTRY[self.device_entry.id]:
                    start_followup = OMNI_ASSIST_REGISTRY[self.device_entry.id]["request_followup"]
                    _LOGGER.debug(f"Retrieved request_followup={start_followup} from device registry")
            
            _LOGGER.debug(f"Follow-up request flag from combined sources: {start_followup}")
            
            # Clear the flag in registry after using it (to prevent it from persisting accidentally)
            if self.device_entry.id in OMNI_ASSIST_REGISTRY and "request_followup" in OMNI_ASSIST_REGISTRY[self.device_entry.id]:
                OMNI_ASSIST_REGISTRY[self.device_entry.id]["request_followup"] = False
                _LOGGER.debug("Reset request_followup flag in registry to prevent unintended persistence")

            # Handle follow-up if requested
            if start_followup:
                _LOGGER.debug("Starting follow-up conversation (skipping wake word)")
                
                # Set wake to "end" and STT to "start" to indicate we're bypassing wake detection
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-wake", "end"
                )
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-stt", "start"
                )
                
                # Get conversation_id from event data if available
                conversation_id = None
                if event.data:
                    conversation_id = event.data.get("conversation_id")
                    
                # If no conversation_id in event but we have one in registry, use that
                if not conversation_id and self.device_entry.id in OMNI_ASSIST_REGISTRY:
                    device_data = OMNI_ASSIST_REGISTRY[self.device_entry.id]
                    if "last_conversation_id" in device_data:
                        # Check if the conversation has timed out (300 seconds)
                        current_time = time.time()
                        last_update_time = device_data.get("conversation_timestamp", 0)
                        
                        if current_time - last_update_time <= 300:  # 5 minutes timeout
                            conversation_id = device_data["last_conversation_id"]
                            _LOGGER.debug(f"Using last conversation_id from registry: {conversation_id}")
                        else:
                            _LOGGER.debug(f"Conversation timed out (age: {current_time - last_update_time}s > 300s)")
                
                # Prepare service call to start new pipeline
                service_data = {
                    "device_id": self.device_entry.id,
                    "start_stage": "stt",  # Skip wake word detection
                    "request_followup": False  # Don't chain multiple follow-ups automatically
                }
                
                # Only add conversation_id if it exists
                if conversation_id:
                    service_data["conversation_id"] = conversation_id
                    _LOGGER.debug(f"Continuing conversation with ID: {conversation_id}")
                else:
                    _LOGGER.debug("No conversation ID available for follow-up, will start new conversation")
                
                # Call the omni_assist.run service to start a new pipeline
                try:
                    _LOGGER.debug(f"Calling omni_assist.run service for follow-up with data: {service_data}")
                    # Use call_later to make sure reset event is fully processed before starting new pipeline
                    
                    # Define a proper async callback to handle the service call
                    async def start_follow_up(_):
                        """Start the follow-up pipeline with proper async handling."""
                        try:
                            await self.hass.services.async_call(
                                DOMAIN, "run", service_data, blocking=True
                            )
                            _LOGGER.debug("Follow-up service call completed")
                        except Exception as err:
                            _LOGGER.error(f"Error in follow-up service call: {err}")
                    
                    # Schedule the async follow-up with proper awaiting
                    async_call_later(
                        self.hass,
                        0.5,  # Half-second delay
                        start_follow_up
                    )
                except Exception as e:
                    _LOGGER.error(f"Error calling follow-up service: {e}")
                return
            
            # If no follow-up, reset entities to default states
            self._reset_entity_states("start")
            return
        
        # Handle the custom reset-after-cancellation event
        if event.type == "reset-after-cancellation":
            _LOGGER.debug("Cancellation detected, resetting entity states")
            self._reset_entity_states("start")
            return
        
        # Handle run-start and run-end events - used for overall pipeline state tracking
        if event.type == "run-start" or event.type == "run-end":
            # We don't need to show these events in the UI, they're for internal tracking only
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
            
            # After an error, wake entity should return to "start" state (except for wake errors)
            if stage != "wake":
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
                )
            return
        
        # Special handling for TTS start - explicitly show the start state
        if event.type == PipelineEventType.TTS_START:
            _LOGGER.debug("TTS started, setting TTS entity to start state")
            
            # If this is a TTS_START event with follow-up information, store it in the registry
            if event.data and "request_followup" in event.data:
                follow_up_flag = event.data.get("request_followup", False)
                if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                    OMNI_ASSIST_REGISTRY[self.device_entry.id]["request_followup"] = follow_up_flag
                    _LOGGER.debug(f"Stored request_followup={follow_up_flag} in device registry")
            
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
            
            # Store conversation_id if this is intent-end event
            if stage == "intent" and state == "end" and event.data:
                intent_output = event.data.get("intent_output", {})
                if isinstance(intent_output, dict) and "conversation_id" in intent_output:
                    conversation_id = intent_output.get("conversation_id")
                    if conversation_id:
                        # Only log if we got a new conversation ID different from what's stored
                        current_id = None
                        if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                            current_id = OMNI_ASSIST_REGISTRY[self.device_entry.id].get("last_conversation_id")
                            
                        if current_id != conversation_id:
                            _LOGGER.debug(f"Storing new conversation_id: {conversation_id} (previous: {current_id or 'None'})")
                        else:
                            _LOGGER.debug(f"Conversation ID unchanged: {conversation_id}")
                        
                        # Update the registry with the latest conversation_id
                        if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                            OMNI_ASSIST_REGISTRY[self.device_entry.id]["last_conversation_id"] = conversation_id
                            # Also store the timestamp when this conversation_id was updated
                            OMNI_ASSIST_REGISTRY[self.device_entry.id]["conversation_timestamp"] = time.time()
                
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
                    
                    # Use the same special handling for wake events that we use above
                    if stage == "wake":
                        if state == "end":
                            _LOGGER.debug(f"Wake word detected (from raw event), setting wake to end state")
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
        
        # Register this switch in the global registry
        OMNI_ASSIST_REGISTRY[self.device_entry.id] = {
            "switch": self,
            "uid": self.uid,
            "options": self.options.copy(),
            "request_followup": False,  # Initialize follow-up flag
            "conversation_timestamp": 0  # Initialize conversation timestamp
        }
        _LOGGER.debug(f"Registered device in OMNI_ASSIST_REGISTRY with ID: {self.device_entry.id}")

    async def async_turn_on(self) -> None:
        _LOGGER.debug("Attempting to turn on OmniAssistSwitch")
        if self._attr_is_on:
            _LOGGER.debug("OmniAssistSwitch is already on")
            return

        self._attr_is_on = True
        self._async_write_ha_state()
        _LOGGER.debug("Set OmniAssistSwitch state to on")

        # Set wake to "start" and other entities to idle initially
        self._reset_entity_states("start")

        try:
            _LOGGER.debug("Calling run_forever")
            self.on_close = run_forever(
                self.hass,
                self.options,
                context=self._context,
                event_callback=self.event_callback,
            )
            _LOGGER.debug("run_forever completed successfully")
            
            # Update registry with latest options
            if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                OMNI_ASSIST_REGISTRY[self.device_entry.id]["options"] = self.options.copy()
                
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
        self._reset_entity_states(None)  # Set wake to None (idle) when turning off

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
        
        # Remove from registry
        if self.device_entry.id in OMNI_ASSIST_REGISTRY:
            del OMNI_ASSIST_REGISTRY[self.device_entry.id]
            _LOGGER.debug(f"Removed device from registry: {self.device_entry.id}")
            
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
