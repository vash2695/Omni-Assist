import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall, Context
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .core import DOMAIN, get_stream_source, assist_run, stream_run
from .core.stream import Stream

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.SENSOR, Platform.SWITCH)

# Global registry to track all Omni-Assist devices
OMNI_ASSIST_REGISTRY = {}


async def async_setup(hass: HomeAssistant, config: ConfigType):
    async def run(call: ServiceCall) -> ServiceResponse:
        stt_stream = Stream()
        device_id = call.data.get("device_id")
        start_stage = call.data.get("start_stage")
        request_followup = call.data.get("request_followup", False)
        conversation_id = call.data.get("conversation_id")
        text_input = call.data.get("text_input")
        
        # Get run options - either from specified device or from call data
        run_options = call.data.copy()
        
        # If a device ID is specified, use that device's configuration as a base
        # and merge any explicitly specified options
        if device_id and device_id in OMNI_ASSIST_REGISTRY:
            device_data = OMNI_ASSIST_REGISTRY[device_id]
            # Start with the device's own options
            base_options = device_data["options"].copy()
            # Override with any explicitly provided options
            base_options.update(run_options)
            run_options = base_options
            
            # Get the device's UID for proper event routing
            run_options["device_uid"] = device_data["uid"]
            
            _LOGGER.debug(f"Running pipeline for device {device_id} with UID {device_data['uid']}")
        
        # Create a new context instead of trying to copy the existing one
        # Home Assistant's Context doesn't have a copy method
        context = Context()
        
        # Store extra data as attributes on the context object
        if not hasattr(context, "extra_data"):
            context.extra_data = {}
        
        # Store our control parameters in the context
        context.extra_data.update({
            "start_stage": start_stage,
            "request_followup": request_followup,
        })
        
        # If text_input is provided and we're starting at intent stage, add it to assist options
        if text_input and start_stage == "intent":
            _LOGGER.debug(f"Setting text input for intent stage: {text_input}")
            # Make sure we have an assist dictionary
            if "assist" not in run_options:
                run_options["assist"] = {}
            
            # Add intent_input directly to the assist options
            run_options["assist"]["intent_input"] = text_input

        try:
            # Only start stream if needed based on start_stage
            if not start_stage or start_stage in ("wake_word", "stt"):
                coro = stream_run(hass, run_options, stt_stream=stt_stream)
                hass.async_create_task(coro)

            return await assist_run(
                hass, 
                run_options, 
                context=context, 
                stt_stream=stt_stream,
                conversation_id=conversation_id
            )
        except Exception as e:
            _LOGGER.error("omni_assist.run", exc_info=e)
            return {"error": {"type": str(type(e)), "message": str(e)}}
        finally:
            stt_stream.close()

    hass.services.async_register(
        DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    pass
