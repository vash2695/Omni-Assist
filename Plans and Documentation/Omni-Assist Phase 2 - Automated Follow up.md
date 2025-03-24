When Home Assistant directly triggers a pipeline via automation or event (`assist_pipeline.run` service), Omni-Assist's internal logic is bypassed. Instead, Home Assistant initiates a completely separate pipeline run outside Omni-Assist’s managed context.

To fix this, you must ensure that follow-up interactions **always remain within Omni-Assist’s managed pipeline runs**. Here’s how to achieve this explicitly:

---

## Why the Issue Occurs:

- When you call the native Home Assistant pipeline service directly (`assist_pipeline.run`), Home Assistant creates a new pipeline run independently.
- Omni-Assist internally manages pipeline runs through its custom event callbacks and context, meaning it only recognizes pipeline runs it explicitly initiates via its custom logic.
- Direct calls to Home Assistant pipelines bypass this logic, causing Omni-Assist to lose visibility and control.

---

## Correct Solution (Explicit Context Passing):

**You must always trigger follow-up interactions through Omni-Assist’s own logic**, not directly through the `assist_pipeline.run` service.

Specifically, this means:

### 1. Enhance Omni-Assist’s Internal "Run" Service:

Extend Omni-Assist’s own service (`omni_assist.run`) with explicit support for follow-up pipeline invocations. Your service must handle context explicitly to reuse or continue conversations:

Example `services.yaml`:
```yaml
run:
  description: Run an Omni-Assist voice pipeline
  fields:
    pipeline_id:
      description: "ID of the pipeline to run"
      example: "default_pipeline"
    device_id:
      description: "Device ID for pipeline run"
      example: "virtual_satellite_device_id"
    request_followup:
      description: "Trigger follow-up wake event after TTS completion"
      example: true
    conversation_id:
      description: "Optional existing conversation ID to continue"
      example: "existing_conversation_id"
    extra_system_message:
      description: "Optional additional context"
      example: "What else can I do for you?"
```

---

### 2. Handle Follow-up Internally in Omni-Assist:

Adjust your integration’s pipeline run management logic so it explicitly handles the follow-up context internally. Your pipeline context (`PipelineRun`) should clearly include whether the current run expects a follow-up.

Example Python snippet (`core/__init__.py`):

```python
async def handle_pipeline_completion(self, pipeline_run):
    if pipeline_run.context.extra_data.get('request_followup', False):
        # Initiate a new pipeline run immediately after TTS finishes
        await self.simulate_wake_word_and_continue(
            pipeline_id=pipeline_run.pipeline.id,
            device_id=pipeline_run.context.extra_data.get("device_id"),
            conversation_id=pipeline_run.context.conversation_id,
            extra_system_message=pipeline_run.context.extra_data.get("extra_system_message", "")
        )
```

**Important**:  
- Always ensure this logic is invoked within Omni-Assist itself (via internal callbacks), not through external pipeline triggers.
- The method `simulate_wake_word_and_continue` should initiate a new `PipelineRun` object that Omni-Assist explicitly manages, ensuring proper state tracking.

---

### 3. Using Custom Events (Recommended for External Integrations):

If external events (like an LLM tool call or external automation) must trigger follow-ups, implement an event listener explicitly in Omni-Assist’s setup logic (`async_setup_entry`):

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    async def followup_event_listener(event):
        data = event.data
        device_id = data["device_id"]
        conversation_id = data.get("conversation_id")
        extra_system_message = data.get("extra_system_message", "")

        # Initiate the pipeline explicitly through Omni-Assist logic
        await hass.services.async_call(
            DOMAIN, "run",
            {
                "pipeline_id": "default_pipeline",
                "device_id": device_id,
                "conversation_id": conversation_id,
                "extra_system_message": extra_system_message,
                "request_followup": False, # avoid infinite loop
            },
            blocking=False
        )

    hass.bus.async_listen("omni_assist_request_followup", followup_event_listener)
```

This ensures:
- The triggered pipeline run remains explicitly managed by Omni-Assist’s existing `run` service.
- Omni-Assist’s internal state management (including event callbacks, sensor states, etc.) correctly tracks this new pipeline invocation.
- It avoids pipeline runs being invisibly managed by Home Assistant’s native service call (`assist_pipeline.run`) alone.

---

### 4. Practical Flow (How It Should Work):

- **User initiates interaction** via Omni-Assist (voice or automation).
- **Pipeline runs and TTS completes.**
- **LLM tool call or automation requests follow-up** by firing your custom event (`omni_assist_request_followup`) or explicitly using your enhanced service (`omni_assist.run` with follow-up context).
- Omni-Assist explicitly creates a new pipeline run internally:
  - Omni-Assist sensors, events, and audio cues remain fully operational.
  - The integration tracks pipeline states, including continuation of the conversation ID.

---

## Crucial Pitfalls to Avoid:
- **Do NOT** invoke `assist_pipeline.run` directly outside Omni-Assist. It must always go through Omni-Assist’s own service layer.
- Ensure the event handling and pipeline triggering logic are tightly coupled within Omni-Assist’s pipeline management functions, so all state and context remain cohesive.

---

## Recommended Immediate Next Steps:
- **Clearly separate your Omni-Assist service (`omni_assist.run`) from the native Home Assistant pipeline run (`assist_pipeline.run`)** to ensure all interactions remain explicitly Omni-Assist-managed.
- **Implement and thoroughly test** the event-based follow-up invocation within your Omni-Assist integration to confirm the integration consistently maintains internal state and tracking.

---

Following this approach will ensure your integration explicitly controls all pipeline interactions, fully resolving the issue of pipeline runs occurring outside Omni-Assist’s defined logic.