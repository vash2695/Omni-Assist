"""Audio processing module for Omni-Assist.

This module handles audio stream processing, including:
- Fetching audio from cameras or stream sources
- Processing media for TTS playback
- Managing audio stream lifecycles
"""

import io
import logging
from typing import Optional, Dict, Any

from mutagen.mp3 import MP3

from homeassistant.components import media_player
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.network import get_url
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .stream import Stream

_LOGGER = logging.getLogger(__name__)


async def get_stream_source(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Get the stream source URL from a camera entity."""
    try:
        component: EntityComponent = hass.data["camera"]
        camera: Camera = next(e for e in component.entities if e.entity_id == entity_id)
        return await camera.stream_source()
    except Exception as e:
        _LOGGER.error("Failed to get stream source", exc_info=e)
        return None


def play_media(
    hass: HomeAssistant, 
    entity_id: str, 
    media_id: str, 
    media_type: str = "music"
):
    """Play media on a media player entity."""
    service_data = {
        "entity_id": entity_id,
        "media_content_id": media_player.async_process_play_media_url(hass, media_id),
        "media_content_type": media_type,
    }

    # hass.services.call would block Hass, so we use a background task
    coro = hass.services.async_call("media_player", "play_media", service_data)
    hass.async_create_background_task(coro, "omni_assist_play_media")


async def get_tts_duration(hass: HomeAssistant, tts_url: str) -> float:
    """Calculate the duration of a TTS audio file in seconds."""
    try:
        # Ensure we have the full URL
        if tts_url.startswith('/'):
            base_url = get_url(hass)
            full_url = f"{base_url}{tts_url}"
        else:
            full_url = tts_url

        # Use Home Assistant's aiohttp client session
        session = async_get_clientsession(hass)
        async with session.get(full_url) as response:
            if response.status != 200:
                _LOGGER.error(f"Failed to fetch TTS audio: HTTP {response.status}")
                return 0
            
            content = await response.read()

        # Use mutagen to get the duration
        audio = MP3(io.BytesIO(content))
        duration = audio.info.length
        
        return duration

    except Exception as e:
        _LOGGER.error(f"Error getting TTS duration: {e}")
        return 0


async def stream_run(hass: HomeAssistant, config: Dict[str, Any], stt_stream: Stream) -> None:
    """Configure and run the audio stream capture process."""
    stream_kwargs = config.get("stream", {})

    # Determine stream source
    if "file" not in stream_kwargs:
        if url := config.get("stream_source"):
            stream_kwargs["file"] = url
        elif entity_id := config.get("camera_entity_id"):
            stream_kwargs["file"] = await get_stream_source(hass, entity_id)
        else:
            # No stream source available
            return

    # Open and start the stream
    stt_stream.open(**stream_kwargs)
    await hass.async_add_executor_job(stt_stream.run) 