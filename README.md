# Omni-Assist

# NOTE: I make changes to this frequently that may break things! Use at your own risk.

This fork is an attempt at implementing continued conversation and other features within Stream Assist.

Features added so far:
* Continued conversation: conversation_id is preserved between interactions for a given entry with a timeout of 300 seconds, though for now the timeout doesn't seem to work consistently
* STT End media: Now both the start and end of the STT phase have options for audio feedback. Also added a field for this in the config options
* STT Error media: When there is an error in the STT phase (like no-text-recognized), a distinct error sound can be played
* More initial config options: All available options are now in the initial config screen
* Added cancel phrases like: "nevermind", "never mind", "thank you", "cancel that", "cancel",
    "abort", "quit", "exit", "end", "forget it", "that's all", "that is all"
* Improved pipeline state tracking: Each stage of the pipeline is more accurately represented by the state sensors (wake, stt, intent, tts), with tts accurately representing the duration of playback instead of jumping immediately to 'end'

Future goals:
* Globally continued conversations: Add the option to pass conversation IDs across all integration entries OR use a single static conversation ID
* Wake Word skip: Allow the wake word phase to be skipped on follow-up interactions, possibly based on LLM response content
* Expose the integration to automations - Will attempt to do this via Virtual Satellite support