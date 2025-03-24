# Omni-Assist: Home Assistant Integration Breakdown

Omni-Assist is a custom Home Assistant integration that extends the functionality of Home Assistant's built-in voice assistant capabilities. It's designed to create a more interactive and responsive voice assistant experience.

## Core Functionality

1. **Enhanced Voice Assistant Pipeline**:
   - Builds upon Home Assistant's assist_pipeline with improved state tracking
   - Manages a complete voice interaction pipeline: wake word, speech-to-text (STT), intent processing, and text-to-speech (TTS)
   - Provides explicit state tracking for each stage of the pipeline

2. **Continued Conversation Support**:
   - Preserves conversation_id between interactions
   - Implements a 300-second timeout for conversations
   - Allows for more natural, multi-turn interactions

3. **Audio Feedback**:
   - Customizable audio cues for different pipeline stages
   - Supports feedback for:
     - STT start and end
     - STT errors (like no recognized text)
     - Voice interaction cancellation

4. **Advanced Pipeline Control**:
   - Cancellation phrase detection ("nevermind", "cancel that", etc.)
   - Google STT pipeline optimization to prevent early VAD timeout
   - Accurate TTS playback state tracking (shows duration rather than instantly ending)

## Technical Implementation

1. **Component Architecture**:
   - Core logic in `core/__init__.py` handling the voice assistant pipeline
   - Uses Home Assistant's entity system (switches and sensors)
   - Implements configuration flow for easy setup in the UI

2. **Entity Types**:
   - **Switch**: Main control entity to start/stop the voice assistant
   - **Sensors**: Track different pipeline states (wake, stt, intent, tts)

3. **Pipeline Events**:
   - Custom event handling for standard pipeline events
   - Additional custom events for state management:
     - `reset-after-tts`: Resets entity states after TTS playback completes
     - `reset-after-cancellation`: Resets entity states when cancellation detected
     - `tts-running`: Explicitly tracks TTS playback

4. **Media Handling**:
   - Supports streaming from cameras for input
   - Media player integration for TTS and feedback sounds
   - Measures TTS audio duration to accurately represent playback time

## Configuration Options

The integration provides numerous configuration options:
- Input source (camera or stream URL)
- Output media player
- Custom audio feedback for various events
- Pipeline selection
- Advanced pipeline configuration

## Future Plans

As mentioned in the README:
- Globally continued conversations
- Wake word skipping on follow-up interactions
- Automation integration via Virtual Satellite support

This integration significantly enhances Home Assistant's voice assistant capabilities with better state tracking, audio feedback, and continued conversation support, making voice interactions more natural and responsive.



# Omni-Assist: Detailed Home Assistant Integration Analysis

## Core Framework Integration

Omni-Assist deeply integrates with Home Assistant's component architecture following the [Integration Architecture](https://developers.home-assistant.io/docs/architecture_components) guidelines.

### 1. Component Registration & Initialization

Omni-Assist follows Home Assistant's [integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest) pattern. In `manifest.json`, it:

```json
{
  "domain": "omni_assist",
  "dependencies": [
    "stream",
    "assist_pipeline"
  ],
  "config_flow": true,
  "iot_class": "calculated"
}
```

This declares explicit dependencies on the `stream` and `assist_pipeline` components, ensuring they're loaded first. The integration initializes through:

- `async_setup`: Registers the `run` service and prepares the environment
- `async_setup_entry`: Loads platforms and converts legacy config data to options

## 2. Assist Pipeline Integration

Omni-Assist extends Home Assistant's [Assist Pipeline](https://developers.home-assistant.io/docs/voice/pipelines) API, which is a core part of Home Assistant's voice capabilities:

```python
pipeline_run = PipelineRun(
    hass,
    context=context,
    pipeline=pipeline,
    event_callback=internal_event_callback,
    ...
)
```

The integration:

1. Retrieves pipelines from Home Assistant's assist_pipeline system
2. Uses `Pipeline.from_json` to create custom pipelines or `assist_pipeline.async_get_pipeline` for default ones
3. Subscribes to pipeline events through the event callback system
4. Manages pipeline stage transitions with enhanced state tracking

This closely follows the [Pipeline Flow Documentation](https://developers.home-assistant.io/docs/voice/pipelines/#pipeline-flow), handling all official pipeline stages:
- Wake Word detection
- Speech-to-Text
- Intent Recognition
- Text-to-Speech

## 3. Entity System Integration

Omni-Assist creates custom entities following the [Entity Component Architecture](https://developers.home-assistant.io/docs/core/entity):

### Switch Entity

```python
class OmniAssistSwitch(SwitchEntity):
    def __init__(self, config_entry: ConfigEntry):
        self._attr_is_on = False
        self._attr_should_poll = False
        self.uid = init_entity(self, "mic", config_entry)
```

This entity provides on/off control for the voice assistant, following Home Assistant's [Switch Entity](https://developers.home-assistant.io/docs/core/entity/switch) pattern.

### Sensor Entities

Sensor entities track the state of different pipeline stages. They follow Home Assistant's [Sensor Entity](https://developers.home-assistant.io/docs/core/entity/sensor) pattern for state reporting.

### Device Registry Integration

The integration creates a single device in the device registry with sensors and switch as child entities:

```python
entity._attr_device_info = DeviceInfo(
    name=config_entry.title,
    identifiers={(DOMAIN, unique_id)},
    entry_type=DeviceEntryType.SERVICE,
)
```

This follows the [Device Registry](https://developers.home-assistant.io/docs/device_registry_index) structure for organizing entities.

## 4. Media Subsystem Integration

Omni-Assist interacts with Home Assistant's media subsystems:

### Media Player Integration

```python
def play_media(hass: HomeAssistant, entity_id: str, media_id: str, media_type: str):
    service_data = {
        "entity_id": entity_id,
        "media_content_id": media_player.async_process_play_media_url(hass, media_id),
        "media_content_type": media_type,
    }
    coro = hass.services.async_call("media_player", "play_media", service_data)
    hass.async_create_background_task(coro, "omni_assist_play_media")
```

The integration:
1. Uses `async_process_play_media_url` to handle URL processing as recommended by [Media Player documentation](https://developers.home-assistant.io/docs/core/entity/media-player)
2. Calls the `play_media` service asynchronously using Home Assistant's service API
3. Creates properly named background tasks for non-blocking operation

### Camera/Stream Integration

```python
async def get_stream_source(hass: HomeAssistant, entity: str) -> str | None:
    try:
        component: EntityComponent = hass.data["camera"]
        camera: Camera = next(e for e in component.entities if e.entity_id == entity)
        return await camera.stream_source()
    except Exception as e:
        return None
```

This integrates with:
1. Home Assistant's camera registry in `hass.data["camera"]`
2. Uses the `stream_source()` method from [Camera Integration](https://developers.home-assistant.io/docs/core/entity/camera)

## 5. Configuration Flow

Omni-Assist implements a [Config Flow](https://developers.home-assistant.io/docs/config_entries_flow_handler) for UI configuration:

```python
class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        # Configuration logic
```

It provides:
1. Initial configuration through `async_step_user`
2. Options flow via `OptionsFlowHandler` for reconfiguration
3. UI forms with dynamic entity selection based on supported features

The integration follows Home Assistant's [Configuration Flow Best Practices](https://developers.home-assistant.io/docs/config_entries_config_flow_handler) by:
- Using the entity registry to find compatible devices
- Dynamically filtering entities based on supported features
- Implementing exclusive options to prevent conflicting settings

## 6. Service Registration

The integration exposes a custom service following the [Services API](https://developers.home-assistant.io/docs/dev_101_services):

```python
hass.services.async_register(
    DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
)
```

The service:
1. Is documented in `services.yaml` with descriptions and selectors
2. Uses the new `supports_response` API for service responses
3. Follows the service pattern for async execution

## 7. Dispatcher Integration

The integration uses Home Assistant's [Dispatcher System](https://developers.home-assistant.io/docs/integration_communicating#using-the-dispatcher) for entity communication:

```python
self.hass.loop.call_soon_threadsafe(
    async_dispatcher_send, self.hass, f"{self.uid}-wake", "start"
)
```

This allows:
1. Thread-safe communication between components
2. State updates across different entities
3. Coordination of pipeline stage transitions

## 8. Context & Task Management

Omni-Assist properly manages Home Assistant's [Context](https://developers.home-assistant.io/docs/development_hass_object#context) and task API:

```python
pipeline_run = PipelineRun(
    hass,
    context=context,
    # ...
)

hass.create_task(simulate_wake_word_and_continue())
```

It correctly:
1. Passes context between service calls and pipeline runs
2. Creates properly managed async tasks using Home Assistant's task API
3. Ensures background tasks are named for debugging

This integration demonstrates sophisticated use of Home Assistant's internal APIs, following recommended patterns for component development, entity management, and service integration.
