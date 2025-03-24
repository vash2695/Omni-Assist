import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .core import DOMAIN, get_stream_source, assist_run, stream_run
from .core.stream import Stream
from .core.wyoming_server import setup_wyoming_server

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.SENSOR, Platform.SWITCH)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    async def run(call: ServiceCall) -> ServiceResponse:
        stt_stream = Stream()

        try:
            coro = stream_run(hass, call.data, stt_stream=stt_stream)
            hass.async_create_task(coro)

            return await assist_run(
                hass, call.data, context=call.context, stt_stream=stt_stream
            )
        except Exception as e:
            _LOGGER.error("omni_assist.run", exc_info=e)
            return {"error": {"type": str(type(e)), "message": str(e)}}
        finally:
            stt_stream.close()

    hass.services.async_register(
        DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
    )

    # Add a registry for virtual satellites
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("virtual_satellites", {})

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    # Store the config entry in the registry if it's a satellite
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        _LOGGER.debug(f"Setting up virtual satellite for entry {entry_id}")

        # Store the satellite configuration in the registry
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        if "virtual_satellites" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["virtual_satellites"] = {}
            
        # Create pipeline configuration
        pipeline_config = create_pipeline_config(config_entry.options)
        
        # Store satellite data
        hass.data[DOMAIN]["virtual_satellites"][entry_id] = {
            "config_entry": config_entry,
            "server": None,  # Will be set up after entities are created
            "pipeline_config": pipeline_config,
        }

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    # After the entities are set up, start the server for satellite mode
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        
        # Set up Wyoming server
        server = await setup_wyoming_server(
            hass, 
            entry_id, 
            hass.data[DOMAIN]["virtual_satellites"][entry_id]["pipeline_config"]
        )
        
        if server:
            _LOGGER.info(f"Wyoming server started for {entry_id}")
            hass.data[DOMAIN]["virtual_satellites"][entry_id]["server"] = server
        else:
            _LOGGER.error(f"Failed to start Wyoming server for {entry_id}")

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    # Clean up the satellite server if it exists
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        if (
            DOMAIN in hass.data
            and "virtual_satellites" in hass.data[DOMAIN]
            and entry_id in hass.data[DOMAIN]["virtual_satellites"]
        ):
            satellite_data = hass.data[DOMAIN]["virtual_satellites"][entry_id]
            if satellite_data["server"] is not None:
                await satellite_data["server"].stop()
            
            # Remove from registry
            del hass.data[DOMAIN]["virtual_satellites"][entry_id]

    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    # Update the satellite configuration if it's a satellite
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        if (
            DOMAIN in hass.data
            and "virtual_satellites" in hass.data[DOMAIN]
            and entry_id in hass.data[DOMAIN]["virtual_satellites"]
        ):
            # Update the configuration
            updated_config = create_pipeline_config(config_entry.options)
            hass.data[DOMAIN]["virtual_satellites"][entry_id]["pipeline_config"] = updated_config
            
            # Reconfigure the server if it exists
            if hass.data[DOMAIN]["virtual_satellites"][entry_id]["server"] is not None:
                await hass.data[DOMAIN]["virtual_satellites"][entry_id]["server"].reconfigure(
                    updated_config
                )
    
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    # Make sure to clean up the satellite server if it exists
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        if (
            DOMAIN in hass.data
            and "virtual_satellites" in hass.data[DOMAIN]
            and entry_id in hass.data[DOMAIN]["virtual_satellites"]
        ):
            satellite_data = hass.data[DOMAIN]["virtual_satellites"][entry_id]
            if satellite_data["server"] is not None:
                await satellite_data["server"].stop()


def create_pipeline_config(options):
    """Create a pipeline configuration from the options."""
    return {
        "pipeline_id": options.get("pipeline_id"),
        "satellite_mode": options.get("satellite_mode", False),
        "satellite_room": options.get("satellite_room", ""),
        "satellite_port": options.get("satellite_port", 10600),
        "noise_suppression_level": options.get("noise_suppression_level", 0),
        "auto_gain_dbfs": options.get("auto_gain_dbfs", 0),
        "volume_multiplier": options.get("volume_multiplier", 1.0),
        "stream_source": options.get("stream_source"),
        "camera_entity_id": options.get("camera_entity_id"),
        "player_entity_id": options.get("player_entity_id"),
    }
