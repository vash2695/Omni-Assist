import logging
import tempfile
import wave
import os
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.components.assist_pipeline import (
    Pipeline, 
    PipelineRun,
    PipelineStage,
    PipelineRunOptions,
    async_pipeline_from_audio_stream
)

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
    """Set up Omni-Assist from a config entry."""
    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    # Initialize the component data structure
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {})
    hass.data[DOMAIN].setdefault("virtual_satellites", {})

    # Store the config entry in the registry if it's a satellite
    if config_entry.options.get("satellite_mode", False):
        entry_id = config_entry.entry_id
        _LOGGER.debug(f"Setting up virtual satellite for entry {entry_id}")
            
        # Create pipeline configuration
        pipeline_config = create_pipeline_config(config_entry.options)
        
        # Store satellite data
        hass.data[DOMAIN]["virtual_satellites"][entry_id] = {
            "config_entry": config_entry,
            "server": None,  # Will be set up after entities are created
            "pipeline_config": pipeline_config,
        }

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    # Set up Wyoming server for satellite mode if enabled
    if config_entry.options.get("satellite_mode", False):
        _LOGGER.debug(f"Setting up Wyoming server for entry {config_entry.entry_id}")
        port = config_entry.options.get("satellite_port", 10600)
        
        async def process_audio(audio_data: bytes) -> None:
            """Process audio from Wyoming server and route to pipeline."""
            try:
                _LOGGER.debug(f"Received {len(audio_data)} bytes of audio from Wyoming server")
                # Create a temporary file for the audio data
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_path = temp_file.name
                    
                    # Create a WAV file with the audio data
                    with wave.open(temp_path, "wb") as wav_file:
                        wav_file.setnchannels(1)  # Mono
                        wav_file.setsampwidth(2)  # 16-bit
                        wav_file.setframerate(16000)  # 16kHz
                        wav_file.writeframes(audio_data)
                    
                    # Get the audio data back as bytes for the pipeline
                    with open(temp_path, "rb") as f:
                        wav_data = f.read()
                    
                    # Ensure we clean up the temporary file
                    os.unlink(temp_path)
                    
                # Create a media source for the pipeline
                media = Pipeline.MediaSource(
                    name="Wyoming Audio", 
                    content_type="audio/wav",
                    buffer=wav_data,
                )
                
                # Process audio at the "stt" stage of the pipeline
                try:
                    pipeline_id = config_entry.options.get("pipeline_id")
                    
                    if pipeline_id:
                        _LOGGER.debug(f"Processing Wyoming audio with pipeline {pipeline_id}")
                        
                        # Create a new run context
                        run_id = str(uuid.uuid4())
                        
                        # Get the pipeline
                        pipeline = await hass.components.assist_pipeline.async_get_pipeline(hass, pipeline_id)
                        
                        if not pipeline:
                            _LOGGER.warning(f"Pipeline {pipeline_id} not found")
                            return
                            
                        # Set up the pipeline options
                        options = PipelineRunOptions(
                            pipeline=pipeline,
                            start_stage=PipelineStage.STT,
                            end_stage=PipelineStage.TTS,
                            runner_data={
                                "source": "wyoming",
                                "entry_id": config_entry.entry_id,
                            }
                        )
                        
                        # Run the pipeline
                        await async_pipeline_from_audio_stream(
                            hass=hass,
                            media=media,
                            options=options,
                            run_id=run_id,
                            context=None,  # No context needed
                            device_id=None,  # Will use the default device
                            event_callback=None,  # We'll handle events through the pipeline run
                        )
                    else:
                        _LOGGER.warning("No pipeline ID set for Wyoming audio processing")
                except Exception as e:
                    _LOGGER.error(f"Error processing Wyoming audio: {e}")
            except Exception as e:
                _LOGGER.error(f"Error handling Wyoming audio data: {e}")
                
        # Initialize the Wyoming server
        wyoming_server = await setup_wyoming_server(
            hass, 
            config_entry.entry_id,
            {
                "audio_callback": process_audio,
                "port": port
            }
        )
        
        if wyoming_server:
            _LOGGER.info(f"Wyoming server started successfully for entry {config_entry.entry_id}")
            
            # Store the server in data for later cleanup
            hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})["wyoming_server"] = wyoming_server
        else:
            _LOGGER.error(f"Failed to start Wyoming server for entry {config_entry.entry_id}")

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    _LOGGER.debug(f"Unloading Omni-Assist: {config_entry.entry_id}")
    
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

    # Stop Wyoming server if running
    if (
        DOMAIN in hass.data 
        and config_entry.entry_id in hass.data[DOMAIN]
        and "wyoming_server" in hass.data[DOMAIN][config_entry.entry_id]
    ):
        _LOGGER.debug(f"Stopping Wyoming server for entry {config_entry.entry_id}")
        wyoming_server = hass.data[DOMAIN][config_entry.entry_id]["wyoming_server"]
        await wyoming_server.stop()
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    
    if unload_ok:
        _LOGGER.debug(f"Successfully unloaded Omni-Assist: {config_entry.entry_id}")
        if DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(config_entry.entry_id)
    
    return unload_ok


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
