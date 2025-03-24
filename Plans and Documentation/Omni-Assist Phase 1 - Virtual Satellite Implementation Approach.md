Given the detailed breakdown of the current Omni-Assist integration and the feasibility analysis previously conducted, here's the recommended path forward to achieve your desired functionality (calling any pipeline via service calls or automations, including custom system messages upon invocation):

## Recommended Approach:

### **Implement a Virtual Wyoming Satellite Server within Omni-Assist:**

This solution aligns best with your current implementation and architecture, providing the most flexibility and native integration with Home Assistant's voice pipeline system.

### Reasoning:
- Omni-Assist already deeply integrates with Home Assistant's native pipeline API (`assist_pipeline`) and manages pipeline events extensively.
- Your integration currently provides excellent state tracking, audio feedback, and media handling, which aligns well with the requirements for emulating a satellite.
- By embedding a Wyoming satellite server, Omni-Assist can leverage the full capabilities of Home Assistant’s native voice infrastructure while appearing as a physical satellite device. This achieves your goal of arbitrary pipeline invocation, explicit state management, and custom system messages.

---

## Implementation Steps:

### 1. Include Wyoming Protocol Support:
- Integrate the official Wyoming protocol Python library (`rhasspy-wyoming`) as a dependency.
- Start a TCP server on a dedicated port within Omni-Assist (e.g., port `10700`) to listen for incoming connections from Home Assistant.

### 2. Satellite Discovery and Registration:
- Utilize Python’s Zeroconf (`zeroconf`) library to broadcast a `_wyoming._tcp.local` service from Omni-Assist, allowing Home Assistant to automatically discover the virtual satellite.
- If Zeroconf auto-discovery proves problematic in containerized setups, provide users the option for manual setup (pointing directly to the host:port from the Wyoming integration UI).

### 3. Pipeline Invocation and Custom Messages:
- Upon connection, Home Assistant will send a `RunSatellite` event; Omni-Assist should respond by continuously streaming audio input (remote wake mode).
- Allow invoking specific Assist pipelines via Home Assistant’s integration UI by assigning pipelines to the discovered satellite device.
- For automation/service invocation, use Home Assistant’s built-in service calls (`assist_pipeline.run`) explicitly specifying `device_id` corresponding to the virtual satellite device and `pipeline_id` for the chosen pipeline.
- Leverage the Omni-Assist existing `run` service to trigger the pipeline explicitly. Ensure this service call can accept custom system messages (extra context) by expanding your current service schema to include optional parameters passed as context to the pipeline invocation.

Example enhanced service schema for pipeline invocation:
```yaml
service: omni_assist.run
data:
  pipeline_id: "default_pipeline"
  device_id: "virtual_satellite_device_id"
  extra_system_message: "Custom instructions or context for this interaction"
```

In your service implementation (`run` service in Omni-Assist), forward these parameters through Home Assistant's pipeline context (the `PipelineRun` API already supports custom context information).

### 4. Audio Streaming and State Handling:
- Continuously stream audio input events (`AudioStart`, `AudioChunk`, `AudioStop`) using the microphone or camera stream source, already managed within Omni-Assist.
- React to pipeline events from Home Assistant (e.g., transcription completion, TTS events, errors) and reflect them using Omni-Assist’s existing sensor entities for precise state tracking.
- Integrate your existing audio feedback system to play start, end, and error sounds upon receiving relevant events (e.g., `stt-no-text-recognized`, `tts-started`, `tts-completed`).

### 5. Advanced Configuration:
- Provide UI configuration (via existing Config Flow) allowing users to configure:
  - Audio input/output devices.
  - Pipeline selection.
  - Custom audio feedback sounds.
  - Remote wake configuration (default since local wake isn't needed).

---

## Key Advantages of This Approach:

- **Leverages Omni-Assist's Existing Strengths**:
  - Superior state tracking, advanced pipeline control, continued conversations, and audio feedback handling.
  - Minimal additional overhead due to reuse of your existing robust integration.

- **Maximum Compatibility with Home Assistant**:
  - Appears as a fully-fledged physical satellite, gaining the advantages you've identified (pipeline flexibility, extra message passing, optimized STT behavior).
  - Fully utilizes Home Assistant’s pipeline management, eliminating custom implementations of voice handling logic beyond the Omni-Assist improvements.

- **Automation and Service Flexibility**:
  - Easy integration with Home Assistant automations and scripts.
  - Fully supports calling arbitrary pipelines with additional context, crucial for customized voice interactions and complex automations.

---

## Alternative (Less Recommended) Approaches:

- **Direct Assist Pipeline WebSocket Calls**:  
  This method (using Home Assistant’s WebSocket API directly) would achieve similar functionality but would require extensive additional custom logic for WebSocket connection management, authentication handling, and audio stream management, making it less efficient given your already mature integration.

- **External Satellite Scripts**:  
  While possible, running an external Wyoming satellite script would add complexity for configuration management, lifecycle management, and reduce tight integration benefits already present in Omni-Assist.

---

## Final Recommendation:

The best and most integrated approach, given your current state of development and requirements, is clearly to embed a Wyoming-compatible **Virtual Satellite server directly into Omni-Assist**. This leverages your existing deep integration, significantly enhances flexibility, and aligns seamlessly with Home Assistant’s voice architecture.

This strategy will provide the functionality you desire—invoking any pipeline via automations/services and sending custom system messages—while maximizing maintainability and user experience.