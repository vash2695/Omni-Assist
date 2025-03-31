import asyncio
import io
import logging
import time
import re
from typing import Callable
from mutagen.mp3 import MP3

from homeassistant.components import assist_pipeline
from homeassistant.components import media_player
from homeassistant.components import stt
from homeassistant.components.assist_pipeline import (
    AudioSettings,
    Pipeline,
    PipelineEvent,
    PipelineEventCallback,
    PipelineEventType,
    PipelineInput,
    PipelineStage,
    PipelineRun,
    WakeWordSettings,
)
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Context, ServiceResponse, SupportsResponse, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceEntry
from homeassistant.helpers.entity import Entity, DeviceInfo
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.network import get_url
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import Platform
from homeassistant.helpers.typing import ConfigType

from .stream import Stream
from ..google_stt_settings import configure_google_stt_pipeline
# REMOVED: from .. import OMNI_ASSIST_REGISTRY


_LOGGER = logging.getLogger(__name__)

DOMAIN = "omni_assist" # Keep domain defined here if needed locally
EVENTS = ["wake", "stt", "intent", "tts"]

CANCELLATION_PHRASES = [
    r'\bnevermind\b', r'\bnever mind\b', r'\bthank you\b', r'\bcancel that\b',
    r'\bcancel\b', r'\babort\b', r'\bquit\b', r'\bexit\b', r'\bend\b', r'\bforget it\b',
    r'\bthat\'s all\b', r'\bthat is all\b'
]


def new(cls, kwargs: dict):
    if not kwargs:
        return cls()
    kwargs = {k: v for k, v in kwargs.items() if hasattr(cls, k)}
    return cls(**kwargs)


def init_entity(entity: Entity, key: str, config_entry: ConfigEntry) -> str:
    unique_id = config_entry.entry_id[:7]
    num = 1 + EVENTS.index(key) if key in EVENTS else 0

    entity._attr_unique_id = f"{unique_id}-{key}"
    entity._attr_name = config_entry.title + " " + key.upper().replace("_", " ")
    entity._attr_icon = f"mdi:numeric-{num}"
    entity._attr_device_info = DeviceInfo(
        name=config_entry.title,
        identifiers={(DOMAIN, unique_id)},
        entry_type=DeviceEntryType.SERVICE,
    )

    return unique_id


async def get_stream_source(hass: HomeAssistant, entity: str) -> str | None:
    try:
        component: EntityComponent = hass.data["camera"]
        camera: Camera = next(e for e in component.entities if e.entity_id == entity)
        return await camera.stream_source()
    except Exception as e:
        _LOGGER.error("get_stream_source", exc_info=e)
        return None


def play_media(hass: HomeAssistant, entity_id: str, media_id: str, media_type: str):
    service_data = {
        "entity_id": entity_id,
        "media_content_id": media_player.async_process_play_media_url(hass, media_id),
        "media_content_type": media_type,
    }

    # hass.services.call will block Hass
    coro = hass.services.async_call("media_player", "play_media", service_data)
    hass.async_create_background_task(coro, "omni_assist_play_media")


async def get_tts_duration(hass: HomeAssistant, tts_url: str) -> float:
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
        # Wrap mutagen call in executor job to avoid blocking
        def get_duration_sync(audio_content: bytes) -> float:
            try:
                audio = MP3(io.BytesIO(audio_content))
                return audio.info.length
            except Exception as mutagen_err:
                _LOGGER.error(f"Error decoding audio with mutagen: {mutagen_err}")
                return 0.0 # Return 0 duration on error

        duration = await hass.async_add_executor_job(get_duration_sync, content)
        return duration

    except Exception as e:
        _LOGGER.error(f"Error getting TTS duration: {e}")
        return 0


async def stream_run(hass: HomeAssistant, data: dict, stt_stream: Stream) -> None:
    stream_kwargs = data.get("stream", {})

    if "file" not in stream_kwargs:
        if url := data.get("stream_source"):
            stream_kwargs["file"] = url
        elif entity := data.get("camera_entity_id"):
            # Ensure get_stream_source is awaited
            stream_source = await get_stream_source(hass, entity)
            if stream_source:
                 stream_kwargs["file"] = stream_source
            else:
                 _LOGGER.error(f"Could not get stream source for camera {entity}")
                 stt_stream.close() # Ensure stream is closed if source fails
                 return
        else:
            _LOGGER.error("No stream source (file, stream_source, or camera_entity_id) provided.")
            stt_stream.close() # Ensure stream is closed if no source
            return # Cannot proceed without a source

    try:
        # Open the stream within a try block in case it fails
        stt_stream.open(**stream_kwargs)
    except Exception as e:
        _LOGGER.error(f"Failed to open stream {stream_kwargs.get('file')}: {e}", exc_info=e)
        # Ensure stream is marked closed if open fails
        stt_stream.close()
        return

    try:
        # Run the stream processing, ensuring it's awaited
        await hass.async_add_executor_job(stt_stream.run)
    except Exception as e:
        # Log exceptions during the stream run itself
        _LOGGER.error(f"Error during stream processing: {e}", exc_info=e)
    finally:
        # Ensure PyAV container is closed (stt_stream.run handles this internally now)
        pass


async def assist_run(
    hass: HomeAssistant,
    data: dict,
    context: Context = None,
    event_callback: PipelineEventCallback = None,
    stt_stream: Stream = None,
    conversation_id: str | None = None
) -> dict:
    _LOGGER.debug(f"assist_run called with conversation_id: {conversation_id}")

    # Access registry from hass.data (needed for storing follow-up flag?)
    # No, follow-up flag is read from data parameter `_request_followup`
    # Conversation ID comes as parameter. Let service handle registry updates.

    # 1. Process assist_pipeline settings
    assist = data.get("assist", {})

    # Apply Google STT optimized settings
    assist = configure_google_stt_pipeline(assist)
    _LOGGER.debug("Applied optimized Google STT settings to prevent early VAD timeout")

    pipeline = None # Initialize pipeline
    try:
        if pipeline_id := data.get("pipeline_id"):
            # get pipeline from pipeline ID
            pipeline = assist_pipeline.async_get_pipeline(hass, pipeline_id)
        elif pipeline_json := assist.get("pipeline"):
            # get pipeline from JSON
            pipeline = Pipeline.from_json(pipeline_json)
        else:
            # get default pipeline
            pipeline = assist_pipeline.async_get_pipeline(hass)

        if not pipeline:
             raise ValueError("Could not load assist pipeline.")

    except Exception as e:
         _LOGGER.error(f"Failed to load pipeline: {e}", exc_info=True)
         # Trigger error event if callback is provided
         if event_callback:
              error_event = PipelineEvent(PipelineEventType.ERROR, {"code": "pipeline_load_error", "message": str(e)})
              # Add device_uid if available in data
              if device_uid := data.get("device_uid"):
                   error_event.data["device_uid"] = device_uid
              hass.loop.call_soon_threadsafe(event_callback, error_event)
         return {"events": {}, "conversation_id": None, "tts_duration": 0}


    # Get start_stage from data parameters instead of context
    start_stage_str = data.get("_start_stage")
    if start_stage_str:
        _LOGGER.debug(f"Using start_stage from data: {start_stage_str}")

        # Map string stage name to PipelineStage enum
        if start_stage_str == "intent":
            assist["start_stage"] = PipelineStage.INTENT
            # Log intent input if present when starting from intent stage
            if intent_input := assist.get("intent_input"):
                _LOGGER.debug(f"Starting from intent stage with input: '{intent_input}'")
            else:
                _LOGGER.warning("Starting from intent stage but no intent_input provided")
        elif start_stage_str == "stt":
            assist["start_stage"] = PipelineStage.STT
            _LOGGER.debug("Starting from STT stage")
        elif start_stage_str == "wake_word":
            assist["start_stage"] = PipelineStage.WAKE_WORD
            _LOGGER.debug("Starting from wake_word stage")
        else:
            _LOGGER.warning(f"Unknown start_stage: {start_stage_str}, using default")
    else:
        _LOGGER.debug("No specific start_stage provided, using default based on pipeline capabilities")

    # Default start_stage handling if not overridden
    if "start_stage" not in assist:
        # auto select start stage
        if pipeline.wake_word_entity:
            assist["start_stage"] = PipelineStage.WAKE_WORD
        elif pipeline.stt_engine:
            assist["start_stage"] = PipelineStage.STT
        else:
            # If no wake word or STT engine, pipeline likely invalid for voice input
            _LOGGER.error("Pipeline misconfigured: No wake word or STT engine found, cannot determine start stage.")
            if event_callback:
                 error_event = PipelineEvent(PipelineEventType.ERROR, {"code": "pipeline_config_error", "message": "No wake word or STT engine found."})
                 if device_uid := data.get("device_uid"): error_event.data["device_uid"] = device_uid
                 hass.loop.call_soon_threadsafe(event_callback, error_event)
            return {"events": {}, "conversation_id": None, "tts_duration": 0}


    if "end_stage" not in assist:
        # auto select end stage
        if pipeline.tts_engine:
            assist["end_stage"] = PipelineStage.TTS
        else:
            assist["end_stage"] = PipelineStage.INTENT

    player_entity_id = data.get("player_entity_id")
    device_uid = data.get("device_uid")  # Get device_uid if provided
    request_followup = data.get("_request_followup", False)  # Get request_followup from data

    # 2. Setup Pipeline Run
    events = {}
    pipeline_run = None  # Define pipeline_run before the internal_event_callback
    tts_duration = 0

    def internal_event_callback(event: PipelineEvent):
        nonlocal pipeline_run, tts_duration # Ensure tts_duration is accessible
        _LOGGER.debug(f"Event: {event.type}, Data: {event.data}")

        # Add device_uid to event data if provided
        local_device_uid = device_uid # Use local copy
        if local_device_uid and (event.data is None or "device_uid" not in event.data):
            if event.data is None:
                event.data = {"device_uid": local_device_uid}
            else:
                # Make a copy to avoid modifying original event data if shared
                event.data = event.data.copy()
                event.data["device_uid"] = local_device_uid

        # Store event data safely
        events[event.type] = (
            {"data": event.data.copy() if event.data else None, "timestamp": event.timestamp}
            if event.data else {"timestamp": event.timestamp}
        )

        # Make sure to pass all events to the event callback if provided
        if event_callback:
            hass.loop.call_soon_threadsafe(event_callback, event)

        # Handle specific pipeline events
        if event.type == PipelineEventType.STT_START:
            if player_entity_id and (media_id := data.get("stt_start_media")):
                play_media(hass, player_entity_id, media_id, "music")
        elif event.type == PipelineEventType.STT_END:
            stt_text = ""
            if event.data and isinstance(event.data.get("stt_output"), dict):
                stt_text = event.data["stt_output"].get("text", "").lower()

            # Check if any cancellation phrase appears AT THE END of the text
            if any(stt_text.endswith(phrase.replace(r'\b', '').strip()) for phrase in CANCELLATION_PHRASES if isinstance(phrase, str)):
            # Simplified check for endswith, adjust regex if needed for more complex patterns
            # Example using regex: if any(re.search(pattern + r'$', stt_text) for pattern in CANCELLATION_PHRASES):
                _LOGGER.info(f"Cancellation phrase detected at end: '{stt_text}'")
                if player_entity_id and (media_id := data.get("cancellation_media")):
                    play_media(hass, player_entity_id, media_id, "music")

                if event_callback:
                    _LOGGER.debug("Cancellation detected, creating reset event")
                    reset_event = PipelineEvent(
                        "reset-after-cancellation",
                        {"message": "Cancellation phrase detected, resetting states", "timestamp": time.time()}
                    )
                    if local_device_uid: reset_event.data["device_uid"] = local_device_uid
                    hass.loop.call_soon_threadsafe(event_callback, reset_event)

                if pipeline_run:
                    _LOGGER.info("Stopping pipeline run due to cancellation phrase.")
                    pipeline_run.stop(PipelineStage.STT)
            elif player_entity_id and (media_id := data.get("stt_end_media")):
                play_media(hass, player_entity_id, media_id, "music")
        elif event.type == PipelineEventType.ERROR:
            err_data = event.data or {}
            if err_data.get("code") == "stt-no-text-recognized":
                if player_entity_id and (media_id := data.get("stt_error_media")):
                    play_media(hass, player_entity_id, media_id, "music")
            _LOGGER.error(f"Pipeline error: code={err_data.get('code')}, message={err_data.get('message')}")
        elif event.type == PipelineEventType.INTENT_START:
            _LOGGER.debug("Intent processing started")
        elif event.type == PipelineEventType.INTENT_END:
            _LOGGER.debug("Intent processing ended")
        elif event.type == PipelineEventType.TTS_START:
            _LOGGER.debug("TTS processing started")
        elif event.type == PipelineEventType.TTS_END:
            if player_entity_id and event.data and isinstance(event.data.get("tts_output"), dict):
                tts = event.data["tts_output"]
                tts_url = tts.get("url")

                if not tts_url:
                    _LOGGER.error("TTS_END event missing tts_output URL.")
                    return

                # Use local copy of request_followup flag
                local_request_followup = request_followup
                if local_request_followup and event.data:
                    # Event data is already copied above, safe to modify
                    event.data["request_followup"] = local_request_followup
                    _LOGGER.debug(f"Added request_followup={local_request_followup} to TTS_END event data")

                async def handle_tts_completion_and_followup():
                    nonlocal tts_duration # Need to modify the outer scope variable
                    duration = await get_tts_duration(hass, tts_url)
                    tts_duration = duration # Store duration for return value

                    if PipelineEventType.TTS_END in events and events[PipelineEventType.TTS_END].get("data"):
                        events[PipelineEventType.TTS_END]["data"]["tts_duration"] = duration
                    _LOGGER.debug(f"Stored TTS duration: {duration} seconds")

                    # Wait for TTS playback to complete
                    await asyncio.sleep(duration)
                    await asyncio.sleep(1)  # Additional small delay

                    if event_callback:
                        _LOGGER.debug(f"TTS playback of {duration} seconds completed, processing reset/followup")
                        local_conversation_id = None
                        if PipelineEventType.INTENT_END in events:
                            intent_data = events.get(PipelineEventType.INTENT_END, {}).get("data", {})
                            if isinstance(intent_data, dict):
                                intent_output = intent_data.get("intent_output", {})
                                if isinstance(intent_output, dict):
                                    local_conversation_id = intent_output.get("conversation_id")

                        reset_data = {
                            "message": "TTS playback complete, resetting states",
                            "timestamp": time.time(),
                            "request_followup": local_request_followup,
                        }
                        _LOGGER.debug(f"Using request_followup={local_request_followup} in reset-after-tts event")
                        if local_conversation_id: reset_data["conversation_id"] = local_conversation_id
                        if local_device_uid: reset_data["device_uid"] = local_device_uid

                        try:
                            reset_event = PipelineEvent("reset-after-tts", reset_data)
                            hass.loop.call_soon_threadsafe(event_callback, reset_event)
                            await asyncio.sleep(0.1) # Allow event processing
                        except Exception as e:
                            _LOGGER.error(f"Error sending reset-after-tts event: {e}")

                    _LOGGER.debug("TTS playback complete, assist_run sub-task finished.")

                # Schedule the task
                hass.async_create_background_task(
                    handle_tts_completion_and_followup(),
                    f"omni_assist_tts_completion_{local_device_uid or 'svc'}" # Add UID to task name
                )
                play_media(hass, player_entity_id, tts_url, tts.get("mime_type", "audio/mpeg"))

    pipeline_run = PipelineRun(
        hass,
        context=context,
        pipeline=pipeline,
        start_stage=assist["start_stage"],
        end_stage=assist["end_stage"],
        event_callback=internal_event_callback,
        tts_audio_output=assist.get("tts_audio_output"),
        wake_word_settings=new(WakeWordSettings, assist.get("wake_word_settings")),
        audio_settings=new(AudioSettings, assist.get("audio_settings")),
    )

    # 3. Setup Pipeline Input
    pipeline_input = PipelineInput(
        run=pipeline_run,
        stt_metadata=stt.SpeechMetadata(
            language="",  # set in async_pipeline_from_audio_stream
            format=stt.AudioFormats.WAV,
            codec=stt.AudioCodecs.PCM,
            bit_rate=stt.AudioBitRates.BITRATE_16,
            sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
            channel=stt.AudioChannels.CHANNEL_MONO,
        ),
        stt_stream=stt_stream,
        intent_input=assist.get("intent_input"),
        tts_input=assist.get("tts_input"),
        conversation_id=conversation_id,  # Pass the conversation_id
        device_id=assist.get("device_id"),
    )

    try:
        # 4. Validate Pipeline
        await pipeline_input.validate()

        # Create custom pipeline start event
        if event_callback:
            start_data = {
                "pipeline": pipeline.id,
                "language": pipeline.language,
                "conversation_id": conversation_id
            }
            if device_uid: start_data["device_uid"] = device_uid
            if "start_stage" in assist:
                start_stage_name = str(assist["start_stage"]).split(".")[-1].lower()
                _LOGGER.debug(f"Starting pipeline at stage: {start_stage_name}")
                start_data["start_stage"] = start_stage_name

            run_start_event = PipelineEvent("run-start", start_data)
            hass.loop.call_soon_threadsafe(event_callback, run_start_event)

            if assist["start_stage"] == PipelineStage.STT:
                stt_start_data = {"engine": pipeline.stt_engine, "metadata": pipeline_input.stt_metadata.__dict__}
                if device_uid: stt_start_data["device_uid"] = device_uid
                stt_start_event = PipelineEvent("stt-start", stt_start_data)
                _LOGGER.debug(f"Dispatching synthetic stt-start event")
                hass.loop.call_soon_threadsafe(event_callback, stt_start_event)
                await asyncio.sleep(0.05)

            elif assist["start_stage"] == PipelineStage.INTENT:
                intent_start_data = {
                    "engine": pipeline.conversation_engine, "language": pipeline.language,
                    "intent_input": assist.get("intent_input"), "conversation_id": conversation_id,
                    "device_id": assist.get("device_id"), "prefer_local_intents": False
                }
                if device_uid: intent_start_data["device_uid"] = device_uid
                intent_start_event = PipelineEvent("intent-start", intent_start_data)
                _LOGGER.debug(f"Dispatching synthetic intent-start event")
                hass.loop.call_soon_threadsafe(event_callback, intent_start_event)
                await asyncio.sleep(0.05)

        # 5. Run Stream (optional - start feeding audio to pipeline)
        if stt_stream:
            stt_stream.start()

        # 6. Run Pipeline
        _LOGGER.debug(f"Executing pipeline input for conv_id={conversation_id}")
        await pipeline_input.execute()
        _LOGGER.debug(f"Pipeline input execution finished for conv_id={conversation_id}")


        # Extract conversation_id from the INTENT_END event
        result_conversation_id = None
        if PipelineEventType.INTENT_END in events:
            intent_data = events[PipelineEventType.INTENT_END].get('data')
            if isinstance(intent_data, dict):
                 intent_output = intent_data.get('intent_output', {})
                 if isinstance(intent_output, dict):
                      result_conversation_id = intent_output.get('conversation_id')


        # Create custom pipeline end event
        if event_callback:
            end_data = {"device_uid": device_uid} if device_uid else None
            run_end_event = PipelineEvent("run-end", end_data)
            hass.loop.call_soon_threadsafe(event_callback, run_end_event)

        if result_conversation_id:
            _LOGGER.debug(f"Pipeline run completed with conversation_id: {result_conversation_id}")

        return {
            "events": events,
            "conversation_id": result_conversation_id,
            "tts_duration": tts_duration
        }

    except AttributeError as e:
        _LOGGER.warning(f"AttributeError during pipeline execution: {e}")
        pass
    except asyncio.CancelledError:
        _LOGGER.info("Pipeline run cancelled.")
        # Ensure event callback gets a cancellation/error event?
        if event_callback:
             error_event = PipelineEvent(PipelineEventType.ERROR, {"code": "pipeline_cancelled", "message": "Pipeline run was cancelled."})
             if device_uid: error_event.data["device_uid"] = device_uid
             hass.loop.call_soon_threadsafe(event_callback, error_event)
        raise # Re-raise cancellation error
    except Exception as e:
        _LOGGER.error(f"Exception during pipeline execution: {e}", exc_info=True)
        if event_callback and PipelineEventType.ERROR not in events:
            error_event = PipelineEvent(PipelineEventType.ERROR, {"code": "pipeline_error", "message": str(e)})
            if device_uid: error_event.data["device_uid"] = device_uid
            hass.loop.call_soon_threadsafe(event_callback, error_event)
    finally:
        if stt_stream:
            stt_stream.stop()

    return {"events": events, "conversation_id": None, "tts_duration": 0}


def run_forever(
    hass: HomeAssistant,
    config_entry: ConfigEntry, # Accept ConfigEntry
    data: dict,
    context: Context,
    event_callback: PipelineEventCallback,
) -> Callable:
    """
    Runs the audio stream and assist pipeline loops indefinitely.

    Args:
        hass: HomeAssistant instance.
        config_entry: The ConfigEntry associated with this instance.
        data: Configuration data for the run.
        context: The Home Assistant context.
        event_callback: Callback function for pipeline events.

    Returns:
        A callable function to close the stream and stop the loops.
    """
    _LOGGER.debug(f"Entering run_forever for entry {config_entry.entry_id}")
    stt_stream = Stream()
    omni_assist_registry = hass.data.get(DOMAIN, {}) # Get registry access

    # Flag to signal loops to stop
    _stop_event = asyncio.Event()

    async def run_stream_loop():
        """Task to manage the audio stream reading."""
        _LOGGER.debug(f"Starting run_stream_loop for {config_entry.entry_id}")
        while not _stop_event.is_set():
            try:
                # Check if stream source is available before running stream_run
                stream_kwargs = data.get("stream", {})
                source_available = False
                stream_source_url = None # Store the resolved URL for logging

                if file_url := stream_kwargs.get("file"):
                    source_available = True
                    stream_source_url = file_url
                elif stream_url := data.get("stream_source"):
                    source_available = True
                    stream_source_url = stream_url
                elif camera_entity_id := data.get("camera_entity_id"):
                    cam_source = await get_stream_source(hass, camera_entity_id)
                    if cam_source:
                        source_available = True
                        stream_source_url = f"camera:{camera_entity_id}" # Log source type
                        # Update stream_kwargs here if get_stream_source modifies it
                        data["stream"] = data.get("stream", {}) # Ensure exists
                        data["stream"]["file"] = cam_source # Add resolved URL for stream_run

                if not source_available:
                    _LOGGER.warning(f"Stream source unavailable for {config_entry.entry_id}, skipping stream_run attempt.")
                    await asyncio.sleep(30) # Wait before retrying source check
                    continue

                _LOGGER.debug(f"Attempting to run stream from {stream_source_url or 'unknown source'}")
                # Run the stream processing
                await stream_run(hass, data, stt_stream=stt_stream)

                # If stream_run finishes (e.g., stream disconnects), wait before retrying
                if not _stop_event.is_set():
                    _LOGGER.info(f"Stream {stream_source_url} ended for {config_entry.entry_id}. Waiting 30 seconds before attempting to reconnect.")
                    await asyncio.sleep(30)

            except asyncio.CancelledError:
                _LOGGER.debug(f"run_stream_loop cancelled for {config_entry.entry_id}.")
                break # Exit loop on cancellation
            except Exception as e:
                _LOGGER.error(f"run_stream_loop error for {config_entry.entry_id}: {e}", exc_info=True)
                # Wait before retrying after an error
                if not _stop_event.is_set():
                    await asyncio.sleep(30)
            finally:
                 # Ensure stream is closed if loop exits unexpectedly
                 if _stop_event.is_set():
                      stt_stream.close()
        _LOGGER.debug(f"Exiting run_stream_loop for {config_entry.entry_id}")


    async def run_assist_loop():
        """Task to manage the assist pipeline execution."""
        _LOGGER.debug(f"Starting run_assist_loop for {config_entry.entry_id}")
        device_id = data.get("assist", {}).get("device_id") # Get device_id from assist data

        while not _stop_event.is_set():
            try:
                current_time = time.time()
                updated_data = data.copy()
                conversation_id = None

                # Get latest data from registry if we have a device_id
                device_data = None # Define device_data
                if device_id and device_id in omni_assist_registry:
                    device_data = omni_assist_registry.get(device_id, {})

                    if device_uid := device_data.get("uid"):
                         updated_data["device_uid"] = device_uid

                    if "options" in device_data:
                        base_options = device_data["options"].copy()
                        # Update base_options with anything explicitly in `data` first
                        base_options.update(data)
                        updated_data = base_options
                        # Original logic was wrong:
                        # for key, value in device_data["options"].items():
                        #     if key not in updated_data: updated_data[key] = value

                    if "last_conversation_id" in device_data:
                        last_update_time = device_data.get("conversation_timestamp", 0)
                        if current_time - last_update_time <= 300:
                            conversation_id = device_data["last_conversation_id"]
                            _LOGGER.debug(f"Using conversation_id from registry: {conversation_id} (age: {current_time - last_update_time:.1f}s)")
                        else:
                            _LOGGER.debug(f"Conversation timed out (age: {current_time - last_update_time:.1f}s > 300s), starting new conversation")

                # Run the assist pipeline
                result = await assist_run(
                    hass,
                    updated_data,
                    context=context,
                    event_callback=event_callback,
                    stt_stream=stt_stream,
                    conversation_id=conversation_id
                )

                had_error = PipelineEventType.ERROR in result.get("events", {})
                if had_error:
                     error_data = result["events"][PipelineEventType.ERROR].get("data", {})
                     _LOGGER.warning(f"Pipeline run for {config_entry.entry_id} ended with error: {error_data.get('code')} - {error_data.get('message')}")
                     await asyncio.sleep(1) # Brief pause after error

                result_conversation_id = result.get("conversation_id")
                if result_conversation_id:
                    _LOGGER.debug(f"Pipeline run for {config_entry.entry_id} completed with conversation_id: {result_conversation_id}")
                    # Update registry if device_id exists and conversation changed
                    if device_id and device_data is not None and device_data.get("last_conversation_id") != result_conversation_id:
                         device_data["last_conversation_id"] = result_conversation_id
                         device_data["conversation_timestamp"] = time.time()
                         omni_assist_registry[device_id] = device_data # Save back to registry via hass.data
                         _LOGGER.debug(f"Updated registry for {device_id} with conv_id: {result_conversation_id}")


                # Prevent tight looping on success, allow other tasks
                if not had_error:
                    await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                _LOGGER.debug(f"run_assist_loop cancelled for {config_entry.entry_id}.")
                break
            except Exception as e:
                _LOGGER.exception(f"run_assist_loop error for {config_entry.entry_id}: {e}")
                await asyncio.sleep(5) # Wait after unexpected loop error
        _LOGGER.debug(f"Exiting run_assist_loop for {config_entry.entry_id}")


    # Create tasks using the config entry's helper for proper tracking
    stream_task = config_entry.async_create_background_task(
        hass, run_stream_loop(), name=f"omni_assist_stream_{config_entry.entry_id[:6]}"
    )
    assist_task = config_entry.async_create_background_task(
        hass, run_assist_loop(), name=f"omni_assist_assist_{config_entry.entry_id[:6]}"
    )

    # Define the close function
    def close_stream():
        _LOGGER.debug(f"Closing stream and signaling loops to stop for entry {config_entry.entry_id}")
        _stop_event.set()
        stt_stream.close()
        # Task cancellation is handled by HA on unload

    return close_stream