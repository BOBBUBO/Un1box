//! The frame inspector, as on Android: a session keeps when each guest frame was published and shown; pressing
//! Inspect pauses the guest and freezes the last two minutes into a `Snapshot`, whose ranges the UI charts and
//! `save_report` writes out.
use std::collections::VecDeque;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

/// A frame after a gap this long follows a static screen: its interval is idle time, not render time.
pub const IDLE_GAP_MS: f32 = 1000.0;
/// What a snapshot keeps; older frames are outside what anyone pressed the button for.
const WINDOW_NS: u64 = 120_000_000_000;

#[derive(Debug, Clone, Copy, PartialEq)]
struct Published {
    sequence: u64,
    published_ns: u64,
    /// When the window read it; 0 if a newer frame replaced it first.
    presented_ns: u64,
}

/// Frames as they happen, ~136 s at 60 fps.
#[derive(Default)]
pub struct Timeline {
    frames: VecDeque<Published>,
}

impl Timeline {
    const CAPACITY: usize = 8192;

    pub(crate) fn published(&mut self, sequence: u64, published_ns: u64) {
        if self.frames.len() == Self::CAPACITY {
            self.frames.pop_front();
        }
        self.frames.push_back(Published { sequence, published_ns, presented_ns: 0 });
    }

    pub(crate) fn presented(&mut self, sequence: u64, now_ns: u64) {
        if let Some(f) = self.frames.iter_mut().rev().take(8).find(|f| f.sequence == sequence) {
            f.presented_ns = now_ns;
        }
    }

    /// The last two minutes, frozen.
    pub fn snapshot(&self, device: &str, log: Option<PathBuf>) -> Snapshot {
        let snapshot_ns = lighttouch_core::monotonic_ns();
        let kept: Vec<&Published> =
            self.frames.iter().filter(|f| snapshot_ns.saturating_sub(f.published_ns) <= WINDOW_NS).collect();
        let frames = kept
            .windows(2)
            .map(|w| Frame {
                sequence: w[1].sequence,
                published_ns: w[1].published_ns,
                presented_ns: w[1].presented_ns,
                interval_ms: w[1].published_ns.saturating_sub(w[0].published_ns) as f32 / 1e6,
            })
            .collect();
        Snapshot { frames, snapshot_ns, device: device.to_string(), log }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    pub sequence: u64,
    pub published_ns: u64,
    pub presented_ns: u64,
    /// Since the previous frame.
    pub interval_ms: f32,
}

impl Frame {
    pub fn idle(&self) -> bool {
        self.interval_ms >= IDLE_GAP_MS
    }
}

pub struct Snapshot {
    pub frames: Vec<Frame>,
    pub snapshot_ns: u64,
    pub device: String,
    /// The session's log, whose end goes in a report.
    pub log: Option<PathBuf>,
}

impl Snapshot {
    pub fn start_ns(&self) -> u64 {
        self.frames.first().map_or(self.snapshot_ns, |f| f.published_ns)
    }

    pub fn end_ns(&self) -> u64 {
        self.frames.last().map_or(self.snapshot_ns, |f| f.published_ns)
    }

    /// The drawn (not idle) frames published in `from..=to`.
    pub fn frames_in(&self, from: u64, to: u64) -> Vec<Frame> {
        self.frames.iter().filter(|f| (from..=to).contains(&f.published_ns) && !f.idle()).copied().collect()
    }

    /// The worst frame of the last ten seconds, with a second either side.
    pub fn initial_range(&self) -> (u64, u64) {
        let (start, end) = (self.start_ns(), self.end_ns());
        let drawn: Vec<&Frame> = self.frames.iter().filter(|f| !f.idle()).collect();
        let recent: Vec<&Frame> = drawn.iter().copied().filter(|f| end - f.published_ns < 10_000_000_000).collect();
        let pool = if !recent.is_empty() {
            recent
        } else if !drawn.is_empty() {
            drawn
        } else {
            self.frames.iter().collect()
        };
        match pool.into_iter().max_by(|a, b| a.interval_ms.total_cmp(&b.interval_ms)) {
            Some(worst) => (
                worst.published_ns.saturating_sub(1_000_000_000).max(start),
                (worst.published_ns + 1_000_000_000).min(end),
            ),
            None => (start, end),
        }
    }
}

/// A range's statistics as the inspector shows them.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RangeStats {
    pub frames: usize,
    pub seconds: f32,
    pub avg_fps: f32,
    pub avg_ms: f32,
    pub low_fps: f32,
    pub worst_ms: f32,
    /// Frames over 50 ms.
    pub hitches: usize,
    pub buckets: [usize; 7],
}

impl RangeStats {
    pub const BUCKET_LIMITS: [f32; 6] = [16.7, 20.0, 25.0, 33.4, 50.0, 100.0];
    pub const BUCKET_LABELS: [&'static str; 7] =
        ["≤16.7 ms", "≤20 ms", "≤25 ms", "≤33 ms", "≤50 ms", "≤100 ms", ">100 ms"];

    pub fn of(frames: &[Frame]) -> Self {
        if frames.is_empty() {
            return Self::default();
        }
        let ms: Vec<f32> = frames.iter().map(|f| f.interval_ms).collect();
        let total: f32 = ms.iter().sum();
        let mut sorted = ms.clone();
        sorted.sort_by(f32::total_cmp);
        let p99 = sorted[((sorted.len() - 1) as f32 * 0.99) as usize];
        let mut buckets = [0; 7];
        for v in &ms {
            buckets[Self::BUCKET_LIMITS.iter().position(|limit| v <= limit).unwrap_or(6)] += 1;
        }
        Self {
            frames: ms.len(),
            seconds: total / 1000.0,
            avg_fps: ms.len() as f32 * 1000.0 / total,
            avg_ms: total / ms.len() as f32,
            low_fps: 1000.0 / p99,
            worst_ms: *sorted.last().unwrap(),
            hitches: ms.iter().filter(|v| **v > 50.0).count(),
            buckets,
        }
    }
}

/// Write the range `from..=to` as a report folder in `dir`: summary.txt, selection.tsv (its frames), frames.tsv (the
/// whole snapshot), report.json, and the end of the session log. Returns the folder.
pub fn save_report(snapshot: &Snapshot, from: u64, to: u64, dir: &Path, stamp: &str) -> Result<PathBuf, String> {
    let fail = |e: std::io::Error| format!("Couldn't save the report: {e}");
    let name: String = snapshot.device.chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '-' }).collect();
    let name = name.split('-').filter(|s| !s.is_empty()).collect::<Vec<_>>().join("-");
    let folder = dir.join(format!("frames-{name}-{}", stamp.replace([' ', '.'], "-")));
    std::fs::create_dir_all(&folder).map_err(fail)?;
    let selected = snapshot.frames_in(from, to);
    let stats = RangeStats::of(&selected);
    let at = |ns: u64| ns.saturating_sub(snapshot.start_ns()) as f64 / 1e9;

    let mut summary = String::new();
    let _ = writeln!(summary, "LightTouch frame report  {stamp} UTC");
    let _ = writeln!(
        summary,
        "LightTouch {} on {}, device: {}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        snapshot.device
    );
    let _ =
        writeln!(summary, "Selection: {:.3} s to {:.3} s of the timeline (monotonic {from}..{to})", at(from), at(to));
    let _ = writeln!(
        summary,
        "Frames {} over {:.2} s: average {:.1} fps ({:.2} ms), 1% low {:.1} fps, worst {:.1} ms, {} frames over 50 ms",
        stats.frames, stats.seconds, stats.avg_fps, stats.avg_ms, stats.low_fps, stats.worst_ms, stats.hitches
    );
    let _ = writeln!(summary, "Frame times:");
    for (label, count) in RangeStats::BUCKET_LABELS.iter().zip(stats.buckets) {
        let _ = writeln!(summary, "  {label:<9} {count}");
    }
    std::fs::write(folder.join("summary.txt"), summary).map_err(fail)?;

    let table = |frames: &[Frame]| {
        let mut out = String::from("# sequence published-ns presented-ns interval-ms\n");
        for f in frames {
            let _ = writeln!(out, "{} {} {} {:.3}", f.sequence, f.published_ns, f.presented_ns, f.interval_ms);
        }
        out
    };
    std::fs::write(folder.join("selection.tsv"), table(&selected)).map_err(fail)?;
    std::fs::write(folder.join("frames.tsv"), table(&snapshot.frames)).map_err(fail)?;
    let report = serde_json::json!({"from": from, "to": to, "frames": stats.frames, "averageFps": stats.avg_fps,
        "lowFps": stats.low_fps, "worstMs": stats.worst_ms, "hitches": stats.hitches, "detailed": false});
    std::fs::write(folder.join("report.json"), serde_json::to_string_pretty(&report).unwrap_or_default() + "\n")
        .map_err(fail)?;
    if let Some(log) = snapshot.log.as_ref().and_then(|p| std::fs::read(p).ok()) {
        let tail = &log[log.len().saturating_sub(2 << 20)..];
        std::fs::write(folder.join("session-tail.log"), tail).map_err(fail)?;
    }
    Ok(folder)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot(intervals_ms: &[u64]) -> Snapshot {
        let mut timeline = Timeline::default();
        let mut t = lighttouch_core::monotonic_ns() - 30_000_000_000;
        timeline.published(1, t);
        for (i, ms) in intervals_ms.iter().enumerate() {
            t += ms * 1_000_000;
            timeline.published(i as u64 + 2, t);
        }
        timeline.snapshot("iPod touch 2G", None)
    }

    #[test]
    fn stats_and_range() {
        let s = snapshot(&[16, 17, 16, 70, 16, 2000, 17, 16]);
        assert_eq!(s.frames.len(), 8);
        let all = s.frames_in(s.start_ns(), s.end_ns());
        assert_eq!(all.len(), 7, "the frame after the 2 s gap is idle");
        let stats = RangeStats::of(&all);
        assert_eq!(stats.hitches, 1);
        assert_eq!(stats.worst_ms, 70.0);
        assert_eq!(stats.buckets.iter().sum::<usize>(), 7);
        let (from, to) = s.initial_range();
        let worst = s.frames.iter().find(|f| f.interval_ms == 70.0).unwrap();
        assert!(from <= worst.published_ns && worst.published_ns <= to);
    }

    #[test]
    fn report() {
        let s = snapshot(&[16, 17, 50, 16]);
        let dir = std::env::temp_dir().join(format!("lt-inspector-{}", std::process::id()));
        let folder = save_report(&s, s.start_ns(), s.end_ns(), &dir, "2026-10-25 at 06.30.00").unwrap();
        let summary = std::fs::read_to_string(folder.join("summary.txt")).unwrap();
        assert!(summary.contains("Frames 4 over"), "{summary}");
        assert_eq!(std::fs::read_to_string(folder.join("selection.tsv")).unwrap().lines().count(), 5);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
