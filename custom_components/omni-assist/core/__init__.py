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

_LOGGER = logging.getLogger(__name__)

DOMAIN = "omni_assist"
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
        audio = MP3(io.BytesIO(content))
        duration = audio.info.length
        
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
            stream_kwargs["file"] = await get_stream_source(hass, entity)
        else:
            return

    stt_stream.open(**stream_kwargs)

    await hass.async_add_executor_job(stt_stream.run)


async def assist_run(
    hass: HomeAssistant,
    data: dict,
    context: Context = None,
    event_callback: PipelineEventCallback = None,
    stt_stream: Stream = None,
    conversation_id: str | None = None
) -> dict:
    _LOGGER.debug(f"assist_run called with conversation_id: {conversation_id}")
    
    # 1. Process assist_pipeline settings
    assist = data.get("assist", {})

    # Apply Google STT optimized settings
    assist = configure_google_stt_pipeline(assist)
    _LOGGER.debug("Applied optimized Google STT settings to prevent early VAD timeout")

    if pipeline_id := data.get("pipeline_id"):
        # get pipeline from pipeline ID
        pipeline = assist_pipeline.async_get_pipeline(hass, pipeline_id)
    elif pipeline_json := assist.get("pipeline"):
        # get pipeline from JSON
        pipeline = Pipeline.from_json(pipeline_json)
    else:
        # get default pipeline
        pipeline = assist_pipeline.async_get_pipeline(hass)

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
            raise Exception("Unknown start_stage")

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
        nonlocal pipeline_run
        _LOGGER.debug(f"Event: {event.type}, Data: {event.data}")

        # Add device_uid to event data if provided
        if device_uid and (event.data is None or "device_uid" not in event.data):
            if event.data is None:
                event.data = {"device_uid": device_uid}
            else:
                event.data["device_uid"] = device_uid

        events[event.type] = (
            {"data": event.data, "timestamp": event.timestamp}
            if event.data
            else {"timestamp": event.timestamp}
        )

        # Make sure to pass all events to the event callback if provided
        if event_callback:
            event_callback(event)

        # Handle specific pipeline events
        if event.type == PipelineEventType.STT_START:
            if player_entity_id and (media_id := data.get("stt_start_media")):
                play_media(hass, player_entity_id, media_id, "music")
        elif event.type == PipelineEventType.STT_END:
            stt_text = event.data.get("stt_output", {}).get("text", "").lower()
            # Check if any cancellation phrase appears in the text (not just exact match)
            if any(re.search(pattern, stt_text) for pattern in CANCELLATION_PHRASES):
                _LOGGER.info(f"Cancellation phrase detected: {stt_text}")
                if player_entity_id and (media_id := data.get("cancellation_media")):
                    play_media(hass, player_entity_id, media_id, "music")
                
                # Immediately dispatch reset event before stopping the pipeline
                if event_callback:
                    _LOGGER.debug("Cancellation detected, creating reset event")
                    reset_event = PipelineEvent(
                        "reset-after-cancellation",
                        {
                            "message": "Cancellation phrase detected, resetting states",
                            "timestamp": time.time()
                        }
                    )
                    # Call event_callback directly to ensure it's processed
                    event_callback(reset_event)
                
                # Cancel the pipeline after reset event is processed
                pipeline_run.stop(PipelineStage.STT)
            elif player_entity_id and (media_id := data.get("stt_end_media")):
                play_media(hass, player_entity_id, media_id, "music")
        elif event.type == PipelineEventType.ERROR:
            if event.data.get("code") == "stt-no-text-recognized":
                if player_entity_id and (media_id := data.get("stt_error_media")):
                    play_media(hass, player_entity_id, media_id, "music")
        elif event.type == PipelineEventType.INTENT_START:
            # Just log this event - it's already passed to event_callback
            _LOGGER.debug("Intent processing started")
        elif event.type == PipelineEventType.INTENT_END:
            # Just log this event - it's already passed to event_callback
            _LOGGER.debug("Intent processing ended")
        elif event.type == PipelineEventType.TTS_START:
            # Just log this event - it's already passed to event_callback
            _LOGGER.debug("TTS processing started")
        elif event.type == PipelineEventType.TTS_END:
            if player_entity_id:
                tts = event.data["tts_output"]
                tts_url = tts["url"]

                # Ensure the TTS entity transitions to "running" state
                if event_callback:
                    # Forcefully dispatch TTS running state immediately
                    _LOGGER.debug("TTS ended, explicitly setting TTS entity to running state")
                    running_event_data = {
                        "message": "TTS playback in progress",
                        "timestamp": time.time(),
                        "tts_output": tts,
                    }
                    
                    # Make sure device_uid is included if available
                    if device_uid:
                        running_event_data["device_uid"] = device_uid
                        
                    # Include request_followup flag if it's set
                    if request_followup:
                        running_event_data["request_followup"] = request_followup
                        
                    running_event = PipelineEvent(
                        "tts-running",
                        running_event_data
                    )
                    event_callback(running_event)

                async def simulate_wake_word_and_continue():
                    duration = await get_tts_duration(hass, tts_url)
                    if PipelineEventType.TTS_END in events and events[PipelineEventType.TTS_END].get("data"):
                        events[PipelineEventType.TTS_END]["data"]["tts_duration"] = duration
                    _LOGGER.debug(f"Stored TTS duration: {duration} seconds")
                
                    # Set a timer to simulate wake word detection after TTS playback
                    await asyncio.sleep(duration)
                    await asyncio.sleep(1)  # Additional small delay
                
                    # After TTS playback completes, reset entity states
                    if event_callback:
                        _LOGGER.debug(f"TTS playback of {duration} seconds completed, resetting entity states")
                        # Get conversation ID if available
                        conversation_id = None
                        if PipelineEventType.INTENT_END in events:
                            intent_data = events.get(PipelineEventType.INTENT_END, {}).get("data", {})
                            if isinstance(intent_data, dict):
                                intent_output = intent_data.get("intent_output", {})
                                if isinstance(intent_output, dict):
                                    conversation_id = intent_output.get("conversation_id")
                        
                        # Create and pass a custom event for entity state management after TTS playback
                        reset_data = {
                            "message": "TTS playback complete, resetting states",
                            "timestamp": time.time(),
                            "request_followup": request_followup,
                        }
                        
                        # Log original follow-up flag value
                        _LOGGER.debug(f"Using original request_followup={request_followup} in reset-after-tts event")
                        
                        # Only add conversation_id if it exists
                        if conversation_id:
                            reset_data["conversation_id"] = conversation_id
                            
                        # Only add device_uid if it exists
                        if device_uid:
                            reset_data["device_uid"] = device_uid
                        
                        # Create actual event with a custom type for entity state management
                        reset_event = PipelineEvent(
                            "reset-after-tts", 
                            reset_data
                        )
                        event_callback(reset_event)
                
                    # No longer simulating wake word detection after TTS playback
                    # This allows the wake entity to remain in "start" state
                    _LOGGER.debug("TTS playback complete, system ready for next interaction")

                # Schedule an async task to simulate wake word and continue pipeline
                hass.create_task(simulate_wake_word_and_continue())
                play_media(hass, player_entity_id, tts["url"], tts["mime_type"])

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
            # Include device_uid in the run-start event
            start_data = {
                "pipeline": pipeline.id,
                "language": pipeline.language,
                "conversation_id": conversation_id
            }
            if device_uid:
                start_data["device_uid"] = device_uid

            # Log start stage for explicit tracking
            if "start_stage" in assist:
                start_stage_name = str(assist["start_stage"]).split(".")[-1].lower()
                _LOGGER.debug(f"Starting pipeline at stage: {start_stage_name}")
                # Add this to event data
                start_data["start_stage"] = start_stage_name

            run_start_event = PipelineEvent(
                "run-start",
                start_data
            )
            event_callback(run_start_event)

            # If starting from a specific stage, create synthetic events for that stage
            if assist["start_stage"] == PipelineStage.STT:
                # Create synthetic stt-start event for proper entity tracking
                stt_start_data = {
                    "engine": pipeline.stt_engine,
                    "metadata": pipeline_input.stt_metadata.__dict__
                }
                if device_uid:
                    stt_start_data["device_uid"] = device_uid
                
                stt_start_event = PipelineEvent(
                    "stt-start",
                    stt_start_data
                )
                # Make sure to process this event properly
                _LOGGER.debug(f"Dispatching synthetic stt-start event for service call")
                event_callback(stt_start_event)
                
                # Add brief delay to ensure events are processed in order
                await asyncio.sleep(0.05)
                
            elif assist["start_stage"] == PipelineStage.INTENT:
                # Create synthetic intent-start event for proper entity tracking
                intent_start_data = {
                    "engine": pipeline.conversation_engine,
                    "language": pipeline.language,
                    "intent_input": assist.get("intent_input"),
                    "conversation_id": conversation_id,
                    "device_id": assist.get("device_id"),
                    "prefer_local_intents": False
                }
                if device_uid:
                    intent_start_data["device_uid"] = device_uid
                
                intent_start_event = PipelineEvent(
                    "intent-start",
                    intent_start_data
                )
                # Make sure to process this event properly
                _LOGGER.debug(f"Dispatching synthetic intent-start event for service call")
                event_callback(intent_start_event)
                
                # Add brief delay to ensure events are processed in order
                await asyncio.sleep(0.05)

        # 5. Run Stream (optional)
        if stt_stream:
            stt_stream.start()

        # 6. Run Pipeline
        await pipeline_input.execute()

        # Extract conversation_id from the INTENT_END event
        result_conversation_id = None
        if PipelineEventType.INTENT_END in events:
            intent_output = events[PipelineEventType.INTENT_END].get('data', {}).get('intent_output', {})
            result_conversation_id = intent_output.get('conversation_id')

        # Create custom pipeline end event
        if event_callback:
            end_data = None
            if device_uid:
                end_data = {"device_uid": device_uid}
            
            run_end_event = PipelineEvent(
                "run-end",
                end_data
            )
            event_callback(run_end_event)

        return {
            "events": events, 
            "conversation_id": result_conversation_id,
            "tts_duration": tts_duration
        }

    except AttributeError:
        pass  # 'PipelineRun' object has no attribute 'stt_provider'
    finally:
        if stt_stream:
            stt_stream.stop()

    # If we reach here due to an exception, return a default dictionary
    return {"events": events, "conversation_id": None, "tts_duration": 0}
    

def run_forever(
    hass: HomeAssistant,
    data: dict,
    context: Context,
    event_callback: PipelineEventCallback,
) -> Callable:
    _LOGGER.debug("Entering run_forever function")
    stt_stream = Stream()

    async def run_stream():
        while not stt_stream.closed:
            try:
                await stream_run(hass, data, stt_stream=stt_stream)
            except Exception as e:
                _LOGGER.debug(f"run_stream error {type(e)}: {e}")
            await asyncio.sleep(30)

    async def run_assist():
        conversation_id = None
        last_interaction_time = None
        waiting_for_next_interaction = True  # Flag to track if waiting for next interaction
        consecutive_errors = 0  # Track consecutive errors
        last_error_time = None  # Track when the last error occurred
        
        while not stt_stream.closed:
            try:
                current_time = time.time()
                
                # Reset conversation context if too much time has passed
                if last_interaction_time and current_time - last_interaction_time > 300:
                    conversation_id = None
                
                # Implement progressive backoff for errors
                if consecutive_errors > 0:
                    # Calculate wait time based on number of consecutive errors (capped at 30 seconds)
                    wait_seconds = min(2 ** consecutive_errors, 30)
                    
                    # Only proceed if enough time has passed since last error
                    if last_error_time and current_time - last_error_time < wait_seconds:
                        # Not enough time has passed, wait a bit and continue the loop
                        await asyncio.sleep(0.5)
                        continue
                    
                    _LOGGER.debug(f"Retrying after {consecutive_errors} consecutive errors, waited {wait_seconds}s")
                
                # Run the assist pipeline
                result = await assist_run(
                    hass,
                    data,
                    context=context,
                    event_callback=event_callback,
                    stt_stream=stt_stream,
                    conversation_id=conversation_id
                )
                
                # Check if an error occurred
                had_error = False
                for event_type in result["events"]:
                    if event_type == PipelineEventType.ERROR:
                        had_error = True
                        consecutive_errors += 1
                        last_error_time = current_time
                        error_code = result["events"][event_type].get("data", {}).get("code", "unknown")
                        _LOGGER.debug(f"Pipeline error detected: {error_code}, consecutive errors: {consecutive_errors}")
                        break
                
                # If no error, reset counter
                if not had_error:
                    consecutive_errors = 0
                
                # Update conversation tracking
                new_conversation_id = result.get("conversation_id")
                if new_conversation_id:
                    conversation_id = new_conversation_id
                    last_interaction_time = current_time
                    waiting_for_next_interaction = False  # Actively in a conversation
                
                _LOGGER.debug(f"Conversation ID: {conversation_id}")
                
                # Short delay before next processing cycle
                # This allows other asyncio tasks to run
                await asyncio.sleep(0.1)
                
            except Exception as e:
                _LOGGER.exception(f"run_assist error: {e}")
                consecutive_errors += 1
                last_error_time = time.time()
                await asyncio.sleep(min(2 ** consecutive_errors, 30))  # Exponential backoff

    # Create coroutines
    run_stream_coro = run_stream()
    run_assist_coro = run_assist()

    # Schedule the coroutines as background tasks
    hass.loop.create_task(run_stream_coro, name="omni_assist_run_stream")
    hass.loop.create_task(run_assist_coro, name="omni_assist_run_assist")

    return stt_stream.close
