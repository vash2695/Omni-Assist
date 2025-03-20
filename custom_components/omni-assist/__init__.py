"""Omni-Assist integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .core import DOMAIN
from .core.stream import Stream
from .core.audio_processor import stream_run
from .core.pipeline_manager import run_pipeline as assist_run

_LOGGER = logging.getLogger(__name__)

# Define the platforms that this integration provides
PLATFORMS = (Platform.SENSOR, Platform.SWITCH)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up the Omni-Assist integration."""
    # Register the run service
    async def run(call: ServiceCall) -> ServiceResponse:
        stt_stream = Stream()

        try:
            # Start the stream processing as a background task
            coro = stream_run(hass, call.data, stt_stream=stt_stream)
            hass.async_create_task(coro)

            # Run the pipeline and wait for the result
            return await assist_run(
                hass, call.data, context=call.context, stt_stream=stt_stream
            )
        except Exception as e:
            _LOGGER.error("Error in omni_assist.run service", exc_info=e)
            return {"error": {"type": str(type(e)), "message": str(e)}}
        finally:
            stt_stream.close()

    hass.services.async_register(
        DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Set up the integration from a config entry."""
    # Migrate data to options if needed
    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    # Set up listener for option changes
    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    # Set up the integration platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow device removal."""
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle removal of an entry."""
    pass
