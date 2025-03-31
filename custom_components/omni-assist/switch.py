import logging
from typing import Callable
import time
import asyncio # Import asyncio

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
    _LOGGER.debug(f"Setting up OmniAssistSwitch for entry {config_entry.entry_id}")
    async_add_entities([OmniAssistSwitch(config_entry)])

class OmniAssistSwitch(SwitchEntity):
    # _on_close should store the callable returned by run_forever
    _on_close: Callable | None = None

    # Store config entry for task management
    _config_entry: ConfigEntry

    def __init__(self, config_entry: ConfigEntry):
        _LOGGER.debug(f"Initializing OmniAssistSwitch for entry {config_entry.entry_id}")
        self._config_entry = config_entry # Store config entry
        self._attr_is_on = False
        self._attr_should_poll = False
        # Create a copy of options to avoid modifying the original entry data directly
        self.options = config_entry.options.copy()
        self.uid = init_entity(self, "mic", config_entry)
        _LOGGER.debug(f"OmniAssistSwitch initialized with UID: {self.uid}")

    def _reset_entity_states(self, wake_state="start"):
        """Reset all entity states to their default values.

        Args:
            wake_state: State to set the wake entity to (default: "start", None for idle)
        """
        _LOGGER.debug(f"Resetting entity states. Setting wake to '{wake_state}'")

        # Set wake to specified state (or idle if None)
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
        # This callback might be called from different threads or contexts.
        # Ensure operations interacting with HA state happen in the event loop.
        _LOGGER.debug(f"Received pipeline event: {event.type} for UID {self.uid}")

        # Check if this event is for a specific device
        target_uid = None
        if event.data and "device_uid" in event.data:
            target_uid = event.data.get("device_uid")
            if target_uid != self.uid:
                # Event is not for this device, ignore it
                _LOGGER.debug(f"Ignoring event for different device UID: {target_uid} (expected {self.uid})")
                return

        # Map pipeline event types to our sensor entity types
        event_type_mapping = {
            # Use full PipelineEventType enum names for clarity if possible
            str(PipelineEventType.WAKE_WORD_START): "wake-start", # Map full enum name
            str(PipelineEventType.WAKE_WORD_END): "wake-end",
            str(PipelineEventType.STT_START): "stt-start",
            str(PipelineEventType.STT_END): "stt-end",
            str(PipelineEventType.INTENT_START): "intent-start",
            str(PipelineEventType.INTENT_END): "intent-end",
            str(PipelineEventType.TTS_START): "tts-start",
            str(PipelineEventType.TTS_END): "tts-end",
            # Custom/internal events
            "run-start": "run-start",
            "run-end": "run-end",
            "reset-after-tts": "reset-after-tts",
            "reset-after-cancellation": "reset-after-cancellation",
            str(PipelineEventType.ERROR): "error", # Handle error event type
        }

        # Get the mapped event type or use the raw event type
        mapped_event_key = str(event.type) # Use string representation of the enum/type
        if mapped_event_key not in event_type_mapping:
            _LOGGER.warning(f"Unhandled event type received: {event.type}")
            return # Ignore unknown event types

        mapped_event_action = event_type_mapping[mapped_event_key]

        # Handle the custom reset-after-tts event
        if mapped_event_action == "reset-after-tts":
            _LOGGER.debug("TTS playback complete, resetting entity states")

            start_followup = False
            if event.data and "request_followup" in event.data:
                start_followup = event.data.get("request_followup", False)
            elif self.device_entry.id in OMNI_ASSIST_REGISTRY:
                 registry_data = OMNI_ASSIST_REGISTRY.get(self.device_entry.id, {})
                 start_followup = registry_data.get("request_followup", False)
                 _LOGGER.debug(f"Retrieved request_followup={start_followup} from device registry")

            _LOGGER.debug(f"Follow-up request flag from combined sources: {start_followup}")

            # Clear the flag in registry after using it
            if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                if "request_followup" in OMNI_ASSIST_REGISTRY[self.device_entry.id]:
                    OMNI_ASSIST_REGISTRY[self.device_entry.id]["request_followup"] = False
                    _LOGGER.debug("Reset request_followup flag in registry")

            if start_followup:
                _LOGGER.debug("Starting follow-up conversation (skipping wake word)")

                # Set UI states for follow-up
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-wake", "end"
                )
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-stt", "start"
                )

                conversation_id = None
                if event.data:
                    conversation_id = event.data.get("conversation_id")
                elif self.device_entry.id in OMNI_ASSIST_REGISTRY:
                    device_data = OMNI_ASSIST_REGISTRY.get(self.device_entry.id, {})
                    if "last_conversation_id" in device_data:
                        current_time = time.time()
                        last_update_time = device_data.get("conversation_timestamp", 0)
                        if current_time - last_update_time <= 300:
                            conversation_id = device_data["last_conversation_id"]
                            _LOGGER.debug(f"Using last conversation_id from registry: {conversation_id}")
                        else:
                            _LOGGER.debug(f"Conversation timed out (age: {current_time - last_update_time:.1f}s > 300s)")

                service_data = {
                    "device_id": self.device_entry.id,
                    "start_stage": "stt",
                    "request_followup": False
                }
                if conversation_id:
                    service_data["conversation_id"] = conversation_id
                    _LOGGER.debug(f"Continuing follow-up conversation with ID: {conversation_id}")
                else:
                    _LOGGER.debug("No conversation ID available for follow-up, will start new conversation")

                async def start_follow_up(_now): # Callback for async_call_later needs one arg
                    """Start the follow-up pipeline with proper async handling."""
                    _LOGGER.debug(f"Calling omni_assist.run service for follow-up with data: {service_data}")
                    try:
                        # Use blocking=False as we don't need the result here, just trigger it.
                        # Note: Service call now needs redesign to interact with the running loop.
                        #       For now, this call will likely run independently (undesired state).
                        #       This logic will be revised in later steps.
                        await self.hass.services.async_call(
                            DOMAIN, "run", service_data, blocking=False
                        )
                        _LOGGER.debug("Follow-up service call triggered")
                    except Exception as err:
                        _LOGGER.error(f"Error in follow-up service call: {err}", exc_info=True)

                async_call_later(self.hass, 0.5, start_follow_up)
                return # Return early, service call handles next step

            # If no follow-up, reset entities to default states
            self._reset_entity_states("start")
            return

        # Handle the custom reset-after-cancellation event
        if mapped_event_action == "reset-after-cancellation":
            _LOGGER.debug("Cancellation detected, resetting entity states")
            self._reset_entity_states("start")
            return

        # Handle run-start and run-end events - used for overall pipeline state tracking
        if mapped_event_action in ("run-start", "run-end"):
            # We don't need to show these events in the UI, they're for internal tracking only
            return

        # Handle error events specially
        if mapped_event_action == "error":
            code = event.data.get("code", "error") if event.data else "error"
            stage = "error" # Default stage
            if isinstance(code, str): # Ensure code is a string before checking substrings
                if "wake_word" in code: stage = "wake"
                elif "stt" in code: stage = "stt"
                elif "intent" in code: stage = "intent"
                elif "tts" in code: stage = "tts"

            _LOGGER.debug(f"Error in stage {stage}: {code}")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "error", event.data
            )

            # After an error, wake entity should return to "start" state (unless it was a wake error)
            if stage != "wake":
                self.hass.loop.call_soon_threadsafe(
                    async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
                )
            return

        # Special handling for TTS start - explicitly show the start state
        if mapped_event_action == "tts-start":
            _LOGGER.debug("TTS started, setting TTS entity to start state")
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
        if mapped_event_action == "tts-end":
            _LOGGER.debug("TTS processing ended, setting TTS entity to running state during playback")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-tts", "running", event.data
            )
            # State resets will be handled by reset-after-tts event after playback completes
            return

        # Process normal pipeline events based on the action determined from the mapping
        try:
            stage, state = mapped_event_action.split("-", 1)

            # Special handling for wake events
            if stage == "wake":
                if state == "end":
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
                    if conversation_id and self.device_entry.id in OMNI_ASSIST_REGISTRY:
                        registry_data = OMNI_ASSIST_REGISTRY.get(self.device_entry.id, {})
                        current_id = registry_data.get("last_conversation_id")
                        if current_id != conversation_id:
                            _LOGGER.debug(f"Storing new conversation_id: {conversation_id} (previous: {current_id or 'None'})")
                        else:
                            _LOGGER.debug(f"Conversation ID unchanged: {conversation_id}")

                        # Update the registry with the latest conversation_id and timestamp
                        registry_data["last_conversation_id"] = conversation_id
                        registry_data["conversation_timestamp"] = time.time()
                        # Ensure the updated dict is placed back if using .get() initially
                        OMNI_ASSIST_REGISTRY[self.device_entry.id] = registry_data


            _LOGGER.debug(f"Dispatching mapped event: {self.uid}-{stage}, state: {state}")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{stage}", state, event.data
            )
        except ValueError:
            _LOGGER.warning(f"Could not parse stage/state from mapped event action: {mapped_event_action}")
        except Exception as e:
            _LOGGER.error(f"Error processing event {event.type}: {e}", exc_info=True)

    async def async_added_to_hass(self) -> None:
        _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} added to HASS")
        # Ensure device_id is added to options *before* registering
        self.options["assist"] = self.options.get("assist", {}) # Ensure assist dict exists
        self.options["assist"]["device_id"] = self.device_entry.id
        _LOGGER.debug(f"Set device_id in options: {self.device_entry.id}")

        # Register this switch in the global registry
        OMNI_ASSIST_REGISTRY[self.device_entry.id] = {
            "switch": self,
            "uid": self.uid,
            "options": self.options.copy(), # Store a copy of current options
            "request_followup": False,
            "conversation_timestamp": 0
        }
        _LOGGER.debug(f"Registered device in OMNI_ASSIST_REGISTRY with ID: {self.device_entry.id}")

        # Dispatcher connections for sensors are handled by the sensors themselves

    async def async_turn_on(self, **kwargs) -> None: # Accept kwargs
        _LOGGER.debug(f"Attempting to turn on OmniAssistSwitch {self.unique_id}")
        if self._attr_is_on:
            _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is already on")
            return

        self._attr_is_on = True
        self._async_write_ha_state()
        _LOGGER.debug(f"Set OmniAssistSwitch {self.unique_id} state to on")

        # Set wake to "start" and other entities to idle initially
        self._reset_entity_states("start")

        try:
            _LOGGER.debug(f"Calling run_forever for {self.unique_id}")
            # Pass the config_entry to run_forever
            self._on_close = run_forever(
                self.hass,
                self._config_entry, # Pass config entry
                self.options,
                context=self._context,
                event_callback=self.event_callback,
            )
            _LOGGER.debug(f"run_forever called successfully for {self.unique_id}")

            # Update registry with latest options used at startup
            if self.device_entry.id in OMNI_ASSIST_REGISTRY:
                OMNI_ASSIST_REGISTRY[self.device_entry.id]["options"] = self.options.copy()

        except Exception as e:
            _LOGGER.error(f"Error turning on OmniAssist {self.unique_id}: {e}", exc_info=True)
            self._attr_is_on = False
            self._on_close = None # Ensure close callback is cleared on error
            self._async_write_ha_state()
            self._reset_entity_states(None) # Reset states to idle on failure


    async def async_turn_off(self, **kwargs) -> None: # Accept kwargs
        _LOGGER.debug(f"Attempting to turn off OmniAssistSwitch {self.unique_id}")
        if not self._attr_is_on:
            _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is already off")
            return

        # Store is_on state before attempting shutdown
        was_on = self._attr_is_on
        self._attr_is_on = False # Optimistically set state off
        self._async_write_ha_state()
        _LOGGER.debug(f"Set OmniAssistSwitch {self.unique_id} state to off")

        # Call the close function if it exists and the switch was on
        if was_on and self._on_close is not None:
            try:
                _LOGGER.debug(f"Calling on_close function for {self.unique_id}")
                self._on_close() # Call the close function returned by run_forever
                _LOGGER.debug(f"on_close function completed successfully for {self.unique_id}")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist {self.unique_id}: {e}", exc_info=True)
            finally:
                self._on_close = None # Clear the callback after calling
                _LOGGER.debug(f"Reset on_close to None for {self.unique_id}")

        # Reset all sensor entities to IDLE state when switch is off
        self._reset_entity_states(None)  # Set wake to None (idle)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is being removed from HASS")

        # Ensure the background task is stopped if it's running
        if self._attr_is_on and self._on_close is not None:
            _LOGGER.debug(f"Calling on_close function during removal for {self.unique_id}")
            try:
                self._on_close()
                _LOGGER.debug(f"on_close function completed successfully during removal for {self.unique_id}")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist during removal for {self.unique_id}: {e}", exc_info=True)
            finally:
                self._on_close = None
                self._attr_is_on = False # Ensure state reflects off

        # Remove from registry
        if self.device_entry and self.device_entry.id in OMNI_ASSIST_REGISTRY:
            del OMNI_ASSIST_REGISTRY[self.device_entry.id]
            _LOGGER.debug(f"Removed device {self.device_entry.id} from registry")

        # Parent class handles dispatcher removal etc.
        await super().async_will_remove_from_hass()