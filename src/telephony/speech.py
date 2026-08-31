"""Only send Gemini audio somebody is actually speaking into.

THE BUG THIS FIXES, IN GEMINI'S OWN WORDS

    1011: Input data processing failed. This is likely due to the client
    sending data too fast. Please review your flow control mechanism.

The bridge forwarded every media frame Twilio sent, unconditionally:

    elif event == "media":
        queue.send_realtime(types.Blob(...))

Twilio sends about fifty frames a second for the whole length of a call,
speech or not. A caller who thinks for three minutes is still fifty frames a
second of room tone. Over one six minute call that is roughly eighteen
thousand blobs, nearly all of them silence, and the live session eventually
gave up and closed with the message above. The caller heard nothing more.

WHY A GATE AND NOT A BUFFER

Rate limiting would still forward the silence, just later, which is worse: a
backlog means the model hears the room from ninety seconds ago. The right
answer is not to send it at all, because it carries nothing.

WHY IT KEEPS SENDING FOR A MOMENT AFTER SPEECH STOPS

This is the part that would be easy to get wrong. Gemini decides somebody has
finished talking by hearing them stop, so cutting the audio the instant the
energy drops removes the very signal it uses to know its turn has started.
The caller would finish a sentence and the desk would sit there waiting for
more.

So the gate has a hangover: once speech is detected it keeps forwarding for
HANGOVER_MS afterwards, which gives the model a real trailing silence to
endpoint on, and only then goes quiet. Speech is never clipped at the front
either, because the gate opens on the frame that crosses the threshold and
that frame is sent.

THE THRESHOLD IS DELIBERATELY LOW

A commercial kitchen at six in the evening is loud, and this product exists
for exactly that call. Being too eager costs a little bandwidth. Being too
strict clips the start of a word, which costs the caller a repetition and
this desk its credibility.
"""

from __future__ import annotations

import audioop

# Root-mean-square of a decoded 20ms frame, on the 16-bit scale. Room tone on
# a phone line sits well under this; a person speaking sits far above it.
# Low on purpose: a clipped word costs more than a wasted frame.
SPEAKING_ABOVE = 320

# How long to keep forwarding after the last frame that had speech in it.
# This is the silence Gemini needs in order to decide a turn has ended, so it
# is not slack: cut it and the desk stops answering when people stop talking.
HANGOVER_MS = 900

FRAME_MS = 20
HANGOVER_FRAMES = HANGOVER_MS // FRAME_MS

# Until this many frames have gone by, everything is forwarded. The first
# moments of a call carry the greeting and any immediate interruption, and
# they are the worst possible place to be clever.
WARMUP_FRAMES = 25


class Gate:
    """Decides whether a frame is worth sending to the model.

    Stateful on purpose: the decision depends on whether somebody was talking
    a moment ago, which a pure function of one frame cannot know.
    """

    def __init__(self) -> None:
        self._quiet_for = HANGOVER_FRAMES + 1     # start closed
        self._seen = 0
        self.sent = 0
        self.dropped = 0

    def open_for(self, ulaw: bytes) -> bool:
        """Should this frame go to the model.

        Args:
            ulaw: one frame of 8kHz mu-law, exactly as Twilio sent it.
        """
        self._seen += 1

        try:
            linear = audioop.ulaw2lin(ulaw, 2)
            level = audioop.rms(linear, 2)
        except Exception:
            # A frame we cannot measure is a frame we send. Never let the
            # optimisation be the reason somebody is not heard.
            self.sent += 1
            return True

        if level >= SPEAKING_ABOVE:
            self._quiet_for = 0
        else:
            self._quiet_for += 1

        wanted = self._seen <= WARMUP_FRAMES or self._quiet_for <= HANGOVER_FRAMES
        if wanted:
            self.sent += 1
        else:
            self.dropped += 1
        return wanted

    @property
    def summary(self) -> str:
        total = self.sent + self.dropped
        if not total:
            return "no audio"
        return (f"{self.sent} frames sent, {self.dropped} dropped as silence "
                f"({self.dropped * 100 // total}%)")
