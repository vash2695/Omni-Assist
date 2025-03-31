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

from .core import run_forever, init_entity, EVENTS, DOMAIN # Keep DOMAIN import

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug(f"Setting up OmniAssistSwitch for entry {config_entry.entry_id}")
    # Ensure registry exists in hass.data
    hass.data.setdefault(DOMAIN, {})
    async_add_entities([OmniAssistSwitch(config_entry)])

class OmniAssistSwitch(SwitchEntity):
    _on_close: Callable | None = None
    _config_entry: ConfigEntry

    def __init__(self, config_entry: ConfigEntry):
        _LOGGER.debug(f"Initializing OmniAssistSwitch for entry {config_entry.entry_id}")
        self._config_entry = config_entry
        self._attr_is_on = False
        self._attr_should_poll = False
        self.options = config_entry.options.copy()
        self.uid = init_entity(self, "mic", config_entry)
        _LOGGER.debug(f"OmniAssistSwitch initialized with UID: {self.uid}")

    @property
    def _omni_assist_registry(self) -> dict:
        """Helper to access the registry via hass.data."""
        return self.hass.data.get(DOMAIN, {})

    def _reset_entity_states(self, wake_state="start"):
        _LOGGER.debug(f"Resetting entity states. Setting wake to '{wake_state}'")
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send, self.hass, f"{self.uid}-wake", wake_state
        )
        for entity in ["stt", "intent", "tts"]:
            _LOGGER.debug(f"Resetting {entity} entity to idle")
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{self.uid}-{entity}", None
            )

    def event_callback(self, event: PipelineEvent):
        _LOGGER.debug(f"Received pipeline event: {event.type} for UID {self.uid}")
        target_uid = None
        if event.data and "device_uid" in event.data:
            target_uid = event.data.get("device_uid")
            if target_uid != self.uid:
                _LOGGER.debug(f"Ignoring event for different device UID: {target_uid} (expected {self.uid})")
                return

        # Use string representation for mapping keys
        event_type_str = str(event.type)

        event_type_mapping = {
            str(PipelineEventType.WAKE_WORD_START): "wake-start",
            str(PipelineEventType.WAKE_WORD_END): "wake-end",
            str(PipelineEventType.STT_START): "stt-start",
            str(PipelineEventType.STT_END): "stt-end",
            str(PipelineEventType.INTENT_START): "intent-start",
            str(PipelineEventType.INTENT_END): "intent-end",
            str(PipelineEventType.TTS_START): "tts-start",
            str(PipelineEventType.TTS_END): "tts-end",
            "run-start": "run-start",
            "run-end": "run-end",
            "reset-after-tts": "reset-after-tts",
            "reset-after-cancellation": "reset-after-cancellation",
            str(PipelineEventType.ERROR): "error",
        }

        mapped_event_action = event_type_mapping.get(event_type_str)
        if not mapped_event_action:
            _LOGGER.warning(f"Unhandled event type received: {event.type}")
            return

        # --- Event Handling Logic (mostly unchanged, uses self._omni_assist_registry) ---

        if mapped_event_action == "reset-after-tts":
            _LOGGER.debug("TTS playback complete, resetting entity states")
            start_followup = False
            registry_entry = self._omni_assist_registry.get(self.device_entry.id) if self.device_entry else None

            if event.data and "request_followup" in event.data:
                start_followup = event.data.get("request_followup", False)
            elif registry_entry:
                 start_followup = registry_entry.get("request_followup", False)
                 _LOGGER.debug(f"Retrieved request_followup={start_followup} from device registry")

            _LOGGER.debug(f"Follow-up request flag from combined sources: {start_followup}")

            if registry_entry and "request_followup" in registry_entry:
                registry_entry["request_followup"] = False
                # Note: No need to write back here if just reading/clearing flag locally
                # self.hass.data[DOMAIN][self.device_entry.id] = registry_entry
                _LOGGER.debug("Reset request_followup flag in registry state")

            if start_followup:
                _LOGGER.debug("Starting follow-up conversation (skipping wake word)")
                self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-wake", "end")
                self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-stt", "start")

                conversation_id = None
                if event.data: conversation_id = event.data.get("conversation_id")
                elif registry_entry and "last_conversation_id" in registry_entry:
                    current_time = time.time()
                    last_update_time = registry_entry.get("conversation_timestamp", 0)
                    if current_time - last_update_time <= 300:
                        conversation_id = registry_entry["last_conversation_id"]
                        _LOGGER.debug(f"Using last conversation_id from registry: {conversation_id}")
                    else:
                        _LOGGER.debug(f"Conversation timed out (age: {current_time - last_update_time:.1f}s > 300s)")

                service_data = {"device_id": self.device_entry.id, "start_stage": "stt", "request_followup": False}
                if conversation_id:
                    service_data["conversation_id"] = conversation_id
                    _LOGGER.debug(f"Continuing follow-up conversation with ID: {conversation_id}")
                else:
                    _LOGGER.debug("No conversation ID available for follow-up, will start new conversation")

                async def start_follow_up(_now):
                    _LOGGER.debug(f"Calling omni_assist.run service for follow-up with data: {service_data}")
                    try:
                        await self.hass.services.async_call(DOMAIN, "run", service_data, blocking=False)
                        _LOGGER.debug("Follow-up service call triggered")
                    except Exception as err:
                        _LOGGER.error(f"Error in follow-up service call: {err}", exc_info=True)

                async_call_later(self.hass, 0.5, start_follow_up)
                return

            self._reset_entity_states("start")
            return

        if mapped_event_action == "reset-after-cancellation":
            _LOGGER.debug("Cancellation detected, resetting entity states")
            self._reset_entity_states("start")
            return

        if mapped_event_action in ("run-start", "run-end"):
            return

        if mapped_event_action == "error":
            code = event.data.get("code", "error") if event.data else "error"
            stage = "error"
            if isinstance(code, str):
                if "wake_word" in code: stage = "wake"
                elif "stt" in code: stage = "stt"
                elif "intent" in code: stage = "intent"
                elif "tts" in code: stage = "tts"

            _LOGGER.debug(f"Error in stage {stage}: {code}")
            self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "error", event.data)

            if stage != "wake":
                self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-wake", "start")
            return

        if mapped_event_action == "tts-start":
            _LOGGER.debug("TTS started, setting TTS entity to start state")
            registry_entry = self._omni_assist_registry.get(self.device_entry.id) if self.device_entry else None
            if event.data and "request_followup" in event.data:
                follow_up_flag = event.data.get("request_followup", False)
                if registry_entry:
                    registry_entry["request_followup"] = follow_up_flag
                    # self.hass.data[DOMAIN][self.device_entry.id] = registry_entry # Update hass.data
                    _LOGGER.debug(f"Stored request_followup={follow_up_flag} in device registry state")

            self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-tts", "start", event.data)
            return

        if mapped_event_action == "tts-end":
            _LOGGER.debug("TTS processing ended, setting TTS entity to running state during playback")
            self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-tts", "running", event.data)
            return

        try:
            stage, state = mapped_event_action.split("-", 1)
            if stage == "wake":
                if state == "end":
                    _LOGGER.debug(f"Wake word detected, setting wake to end state")
                    self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-{stage}", "end", event.data)
                return

            if stage == "intent" and state == "end" and event.data:
                intent_output = event.data.get("intent_output", {})
                if isinstance(intent_output, dict) and "conversation_id" in intent_output:
                    conversation_id = intent_output.get("conversation_id")
                    registry_entry = self._omni_assist_registry.get(self.device_entry.id) if self.device_entry else None
                    if conversation_id and registry_entry:
                        current_id = registry_entry.get("last_conversation_id")
                        if current_id != conversation_id:
                            _LOGGER.debug(f"Storing new conversation_id: {conversation_id} (previous: {current_id or 'None'})")
                        else:
                            _LOGGER.debug(f"Conversation ID unchanged: {conversation_id}")

                        registry_entry["last_conversation_id"] = conversation_id
                        registry_entry["conversation_timestamp"] = time.time()
                        # self.hass.data[DOMAIN][self.device_entry.id] = registry_entry # Update hass.data

            _LOGGER.debug(f"Dispatching mapped event: {self.uid}-{stage}, state: {state}")
            self.hass.loop.call_soon_threadsafe(async_dispatcher_send, self.hass, f"{self.uid}-{stage}", state, event.data)
        except ValueError:
            _LOGGER.warning(f"Could not parse stage/state from mapped event action: {mapped_event_action}")
        except Exception as e:
            _LOGGER.error(f"Error processing event {event.type}: {e}", exc_info=True)

    async def async_added_to_hass(self) -> None:
        _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} added to HASS")
        # Device ID should exist on device_entry by now
        if not self.device_entry:
             _LOGGER.error(f"Device entry not available for {self.unique_id} during add")
             return

        self.options["assist"] = self.options.get("assist", {})
        self.options["assist"]["device_id"] = self.device_entry.id
        _LOGGER.debug(f"Set device_id in options: {self.device_entry.id}")

        # Register this switch instance in hass.data
        registry = self._omni_assist_registry # Access via property
        registry[self.device_entry.id] = {
            "switch": self, # Store the entity instance itself if needed elsewhere
            "uid": self.uid,
            "options": self.options.copy(),
            "request_followup": False,
            "conversation_timestamp": 0
        }
        _LOGGER.debug(f"Registered device in hass.data[{DOMAIN}] with ID: {self.device_entry.id}")

    async def async_turn_on(self, **kwargs) -> None:
        _LOGGER.debug(f"Attempting to turn on OmniAssistSwitch {self.unique_id}")
        if self._attr_is_on:
            _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is already on")
            return

        self._attr_is_on = True
        self._async_write_ha_state()
        _LOGGER.debug(f"Set OmniAssistSwitch {self.unique_id} state to on")
        self._reset_entity_states("start")

        try:
            _LOGGER.debug(f"Calling run_forever for {self.unique_id}")
            self._on_close = run_forever(
                self.hass, self._config_entry, self.options,
                context=self._context, event_callback=self.event_callback,
            )
            _LOGGER.debug(f"run_forever called successfully for {self.unique_id}")

            # Update registry options if needed
            registry_entry = self._omni_assist_registry.get(self.device_entry.id) if self.device_entry else None
            if registry_entry:
                registry_entry["options"] = self.options.copy()
                # self.hass.data[DOMAIN][self.device_entry.id] = registry_entry

        except Exception as e:
            _LOGGER.error(f"Error turning on OmniAssist {self.unique_id}: {e}", exc_info=True)
            self._attr_is_on = False
            self._on_close = None
            self._async_write_ha_state()
            self._reset_entity_states(None)

    async def async_turn_off(self, **kwargs) -> None:
        _LOGGER.debug(f"Attempting to turn off OmniAssistSwitch {self.unique_id}")
        if not self._attr_is_on:
            _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is already off")
            return

        was_on = self._attr_is_on
        self._attr_is_on = False
        self._async_write_ha_state()
        _LOGGER.debug(f"Set OmniAssistSwitch {self.unique_id} state to off")

        if was_on and self._on_close is not None:
            try:
                _LOGGER.debug(f"Calling on_close function for {self.unique_id}")
                self._on_close()
                _LOGGER.debug(f"on_close function completed successfully for {self.unique_id}")
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist {self.unique_id}: {e}", exc_info=True)
            finally:
                self._on_close = None
                _LOGGER.debug(f"Reset on_close to None for {self.unique_id}")

        self._reset_entity_states(None)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        _LOGGER.debug(f"OmniAssistSwitch {self.unique_id} is being removed from HASS")

        # Ensure the background task is stopped
        if self._attr_is_on and self._on_close is not None:
            _LOGGER.debug(f"Calling on_close function during removal for {self.unique_id}")
            try:
                self._on_close()
            except Exception as e:
                _LOGGER.error(f"Error closing OmniAssist during removal for {self.unique_id}: {e}", exc_info=True)
            finally:
                self._on_close = None
                self._attr_is_on = False

        # Remove from registry in hass.data
        registry = self._omni_assist_registry
        if self.device_entry and self.device_entry.id in registry:
            del registry[self.device_entry.id]
            _LOGGER.debug(f"Removed device {self.device_entry.id} from hass.data[{DOMAIN}]")

        await super().async_will_remove_from_hass()