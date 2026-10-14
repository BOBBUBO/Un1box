# Android PCM across pause and focus changes

Flushing the native capture ring/domain queue cannot revoke PCM already returned
to the Android reader. The reader now captures a sink ticket before
`Native.audio`, retains it through the format lookup, then checks the ticket
and current pause/focus/stop admission under the same monitor as AudioTrack
write. A pause closes/advances that epoch and pauses/flushes the retained track;
resume reopens a new epoch after `play`. Reads started while closed have ticket0
and cannot become eligible after resume. Repeated play with already held focus
does not invalidate an otherwise current read.

Explicit user pause and focus loss publish the sink fence before native pause
admission. Owner-scoped backgrounding first verifies native ownership, then
closes the sink before abandoning focus, so an obsolete Activity cannot mute a
new owner. A write already holding the monitor linearizes before the pause
fence; the subsequent track flush handles its queued platform data. Native PCM
read/format calls never hold the sink monitor. Stop keeps its independent
deadline path and stopping flag; this patch adds no audio lock to that path.

The existing play-before-new-focus publication fix remains: retained track
`play`, epoch reopen and focus assignment share the guard. Runtime's internal
pause barrier must separately prevent fresh domain packets until actual paused
then running/capture-restart acknowledgement. Android does not treat return
from asynchronous native Resume as proof of that acknowledgement; until the
native queue permits fresh packets, reads yield no PCM to write.

`audio.packetGeneration` and `packetAdmissionOpen` report the sink's admission
state, not guest timestamps or audible playback. `discardedReadPackets` and
`discardedReadSamples` count positive returned buffers rejected before writing;
they are not claims about their actual audible content. Pulled sample counters
still count actual native reads, including discarded buffers. Written/played
counters retain their distinct meaning.

Three source regressions exercise the production epoch/monitor: a read blocked
across complete pause/resume cannot write, a closed read cannot become eligible,
and a blocked sink write prevents pause publication until that critical section
finishes. They use symbolic sink writes, not fake PCM or a guest audio pass.
Compilation/target validation remains required. No new Portable or JNI API,
sample format, focus policy, renderer or gyro behavior is introduced here.
