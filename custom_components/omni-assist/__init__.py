import logging
import asyncio
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall, Context
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

# Import core functions needed here
from .core import DOMAIN, get_stream_source, assist_run, stream_run
from .core.stream import Stream

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.SENSOR, Platform.SWITCH)

# REMOVED: OMNI_ASSIST_REGISTRY = {} - Will use hass.data[DOMAIN] instead


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up the Omni-Assist integration."""
    # Ensure the domain data dictionary exists
    hass.data.setdefault(DOMAIN, {})

    async def run(call: ServiceCall) -> ServiceResponse:
        """Handle the omni_assist.run service call."""
        # Access registry from hass.data
        omni_assist_registry = hass.data.get(DOMAIN, {})
        stt_stream = Stream()
        try:
            device_id = call.data.get("device_id")
            start_stage = call.data.get("start_stage")
            request_followup = call.data.get("request_followup", False)
            conversation_id = call.data.get("conversation_id")
            text_input = call.data.get("text_input")

            # Get run options - either from specified device or from call data
            run_options = call.data.copy()

            # If a device ID is specified, use that device's configuration as a base
            # and merge any explicitly specified options
            if device_id and device_id in omni_assist_registry:
                device_data = omni_assist_registry[device_id]
                # Start with the device's own options
                base_options = device_data.get("options", {}).copy() # Use .get for safety
                # Override with any explicitly provided options
                base_options.update(run_options)
                run_options = base_options

                # Get the device's UID for proper event routing
                run_options["device_uid"] = device_data.get("uid")

                # If no conversation_id was provided but the device has a last_conversation_id, use it
                if not conversation_id and "last_conversation_id" in device_data:
                    # Check if the conversation has timed out (300 seconds)
                    current_time = time.time()
                    last_update_time = device_data.get("conversation_timestamp", 0)

                    if current_time - last_update_time <= 300:  # 5 minutes timeout
                        conversation_id = device_data["last_conversation_id"]
                        _LOGGER.debug(f"Using last known conversation_id for device: {conversation_id} (age: {current_time - last_update_time:.1f}s)")
                    else:
                        _LOGGER.debug(f"Conversation timed out (age: {current_time - last_update_time:.1f}s > 300s), starting new conversation")
                elif not conversation_id: # Added condition for clarity
                    _LOGGER.debug(f"No conversation_id provided and none in registry, starting new conversation")

                # Store follow-up flag in the registry for thread-safe access
                # Ensure device_data is updated back into the registry if modified
                device_data["request_followup"] = request_followup
                omni_assist_registry[device_id] = device_data # Ensure update is saved
                _LOGGER.debug(f"Stored request_followup={request_followup} in device registry for {device_id}")

                _LOGGER.debug(f"Running pipeline for device {device_id} with UID {device_data.get('uid')}")
            elif device_id:
                _LOGGER.error(f"Device ID {device_id} not found in registry")
                return {"error": {"type": "device_not_found", "message": f"Device ID {device_id} not found"}}

            # Create a new context
            context = Context()

            # Store the control parameters in the run_options dictionary
            run_options["_start_stage"] = start_stage
            run_options["_request_followup"] = request_followup

            # Log follow-up request if it's enabled
            if request_followup:
                _LOGGER.debug(f"Service call requesting follow-up after TTS (request_followup={request_followup})")

            # If text_input is provided and we're starting at intent stage, add it to assist options
            if text_input and start_stage == "intent":
                _LOGGER.debug(f"Setting text input for intent stage: {text_input}")
                # Make sure we have an assist dictionary
                if "assist" not in run_options:
                    run_options["assist"] = {}

                # Add intent_input directly to the assist options
                run_options["assist"]["intent_input"] = text_input
            elif text_input and start_stage != "intent":
                _LOGGER.warning(f"text_input provided but start_stage is '{start_stage}', not 'intent'. Ignoring text_input.")

            try:
                # Only start stream if needed based on start_stage
                stream_task = None
                if not start_stage or start_stage in ("wake_word", "stt"):
                    stream_task = hass.async_create_task(
                        stream_run(hass, run_options, stt_stream=stt_stream)
                    )

                result = await assist_run(
                    hass,
                    run_options,
                    context=context,
                    stt_stream=stt_stream,
                    conversation_id=conversation_id
                )

                # Ensure stream task is awaited or handled if it exists
                if stream_task and not stream_task.done():
                     # Wait briefly for stream to finish or cancel it if pipeline ended early
                     try:
                         await asyncio.wait_for(stream_task, timeout=1.0)
                     except asyncio.TimeoutError:
                         stream_task.cancel()
                         await asyncio.sleep(0) # Allow cancellation to propagate


                # Log the conversation ID from the result for debugging
                if result_conversation_id := result.get("conversation_id"):
                    _LOGGER.debug(f"Service call completed with conversation_id: {result_conversation_id}")

                    # If this is a new conversation ID and different from what we passed in, log that too
                    if result_conversation_id != conversation_id:
                        _LOGGER.debug(f"New conversation started (old: {conversation_id or 'None'}, new: {result_conversation_id})")

                    # Make sure it's saved in the registry if we have a device_id
                    if device_id and device_id in omni_assist_registry:
                        device_data = omni_assist_registry[device_id] # Fetch again before update
                        if device_data.get("last_conversation_id") != result_conversation_id:
                            device_data["last_conversation_id"] = result_conversation_id
                            device_data["conversation_timestamp"] = time.time()
                            omni_assist_registry[device_id] = device_data # Save update
                            _LOGGER.debug(f"Updated registry with conversation_id: {result_conversation_id}")

                return result
            except asyncio.CancelledError:
                _LOGGER.debug("Service call was cancelled before completion")
                return {"error": {"type": "cancelled", "message": "Service call was cancelled"}}
            except Exception as e:
                _LOGGER.error("Error in omni_assist.run service", exc_info=e)
                return {"error": {"type": str(type(e)), "message": str(e)}}
        finally:
            if stt_stream:
                stt_stream.close()

    hass.services.async_register(
        DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Set up Omni-Assist from a config entry."""
    # Ensure hass.data[DOMAIN] exists (redundant if async_setup ran, but safe)
    hass.data.setdefault(DOMAIN, {})

    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    # Forward unload to platforms
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)

    # Remove device from registry if it exists
    omni_assist_registry = hass.data.get(DOMAIN, {})
    # Iterate through devices associated with this config entry
    # (Need device registry helper to find devices for entry)
    # Simpler approach: Let switch.async_will_remove_from_hass handle removal.

    # If all entries are unloaded, clean up hass.data[DOMAIN]? Optional.
    # if not hass.config_entries.async_entries(DOMAIN):
    #     hass.data.pop(DOMAIN, None)

    return unload_ok


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove device association from config entry."""
    # Usually, no action needed here unless the integration needs to clean up device-specific resources
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle removal of a config entry."""
    # This is called after async_unload_entry when the entry is fully removed.
    # Perform any final cleanup if needed.
    pass