//! The performance overlay over the guest screen (FPS, Basic, Full), MangoHud style: one hue per subsystem.
use super::theme;
use eframe::egui::{self, Align2, Color32, CornerRadius, FontId, Pos2, Rect, Stroke, Ui, pos2, vec2};
use lighttouch_model::Model;
use lighttouch_model::session::FrameStats;
use lighttouch_model::settings::{Corner, OverlayGraph, OverlayLevel};

pub fn show(ui: &Ui, model: &Model, screen: Rect, scale: f32) {
    let level = model.settings.overlay;
    let Some(session) = &model.session else { return };
    if level == OverlayLevel::Off {
        return;
    }
    let stats = &session.stats;
    let summary = stats.summary();
    let mono = |size: f32| FontId::new(size * scale, egui::FontFamily::Monospace);
    let bold = |size: f32| FontId::new(size * scale, theme::semibold());
    let mut rows: Vec<Vec<(String, Color32, FontId)>> = Vec::new();
    let fps_text = if summary.idle { "idle".to_string() } else { format!("{:.0}", summary.fps) };
    let graph_size = match level {
        OverlayLevel::Basic => Some(vec2(132.0, 26.0) * scale),
        OverlayLevel::Full => Some(vec2(168.0, 34.0) * scale),
        _ => None,
    };
    match level {
        OverlayLevel::Fps => {
            rows.push(vec![(fps_text, theme::FPS, bold(22.0)), (" FPS".into(), theme::LABEL, bold(11.0))])
        }
        OverlayLevel::Basic => rows.push(vec![
            ("FPS  ".into(), theme::FPS, bold(11.0)),
            (fps_text, Color32::WHITE, mono(12.0)),
            (format!("  {:.1} ms", summary.frame_ms), theme::LABEL, mono(11.0)),
        ]),
        OverlayLevel::Full => {
            rows.push(vec![
                ("FPS  ".into(), theme::FPS, bold(11.0)),
                (fps_text, Color32::WHITE, mono(13.0)),
                (format!("  {:.1} ms", summary.frame_ms), theme::LABEL, mono(11.0)),
            ]);
            rows.push(vec![
                ("1%   ".into(), theme::FPS, bold(11.0)),
                (format!("{:.0}", summary.low_1_percent), Color32::WHITE, mono(12.0)),
                (format!("  max {:.0} ms", summary.worst_ms), theme::LABEL, mono(11.0)),
            ]);
            // As on Android: the guest's vCPU and GL, lock and run time per frame come from detailed timing.
            let live = session.live_timing;
            rows.push(vec![
                ("CPU  ".into(), theme::CPU, bold(11.0)),
                ("guest ".into(), theme::LABEL, mono(10.0)),
                (
                    live.and_then(|l| l.vcpu_percent).map_or("–".into(), |p| format!("{p:.0}%")),
                    Color32::WHITE,
                    mono(12.0),
                ),
            ]);
            rows.push(vec![
                ("     ".into(), theme::CPU, bold(11.0)),
                ("engine ".into(), theme::LABEL, mono(10.0)),
                (format!("{:.0}%", stats.cpu_percent), Color32::WHITE, mono(12.0)),
            ]);
            if let Some(l) = live {
                rows.push(vec![
                    ("GPU  ".into(), theme::GPU, bold(11.0)),
                    ("GL ".into(), theme::LABEL, mono(10.0)),
                    (format!("{:.1} ms/frame", l.gl_ms_per_frame), Color32::WHITE, mono(12.0)),
                ]);
                rows.push(vec![
                    ("LOCK ".into(), theme::GPU, bold(11.0)),
                    ("BQL ".into(), theme::LABEL, mono(10.0)),
                    (format!("{:.1} ms/frame", l.bql_ms_per_frame), Color32::WHITE, mono(12.0)),
                ]);
                rows.push(vec![
                    ("RUN  ".into(), theme::CPU, bold(11.0)),
                    ("vCPU ".into(), theme::LABEL, mono(10.0)),
                    (format!("{:.0}% of wall", l.run_percent), Color32::WHITE, mono(12.0)),
                ]);
            } else {
                let why = if !model.settings.detailed_timing { "detailed timing off" } else { "waiting for frames…" };
                rows.push(vec![("GPU  ".into(), theme::GPU, bold(11.0)), (why.into(), theme::LABEL, mono(10.0))]);
            }
            rows.push(vec![
                ("PRES ".into(), theme::LABEL, bold(11.0)),
                (format!("{:.1} ms", stats.latency_ms), Color32::WHITE, mono(12.0)),
                ("  latency".into(), theme::LABEL, mono(10.0)),
            ]);
            if let Some(bytes) = session.status.as_ref().map(|s| s.resident_bytes).filter(|b| *b > 0) {
                rows.push(vec![
                    ("MEM  ".into(), theme::MEM, bold(11.0)),
                    (format!("{:.0} MiB", bytes as f64 / (1u64 << 20) as f64), Color32::WHITE, mono(12.0)),
                ]);
            }
        }
        OverlayLevel::Off => {}
    }
    let painter = ui.painter().with_clip_rect(screen);
    let line_height = 17.0 * scale;
    let pad = 7.0 * scale;
    let galleys: Vec<Vec<(std::sync::Arc<egui::Galley>, Color32)>> = rows
        .into_iter()
        .map(|row| row.into_iter().map(|(t, c, f)| (painter.layout_no_wrap(t, f, c), c)).collect())
        .collect();
    let text_width = galleys.iter().map(|r| r.iter().map(|(g, _)| g.size().x).sum::<f32>()).fold(0.0, f32::max);
    let first_height =
        galleys.first().and_then(|r| r.iter().map(|(g, _)| g.size().y).reduce(f32::max)).unwrap_or(line_height);
    let rows_height = first_height + line_height * galleys.len().saturating_sub(1) as f32;
    let width = text_width.max(graph_size.map_or(0.0, |g| g.x)) + pad * 2.0;
    let height = rows_height + graph_size.map_or(0.0, |g| g.y + 6.0 * scale) + pad * 2.0;
    let margin = 8.0 * scale;
    let min = match model.settings.overlay_corner {
        Corner::TopLeft => screen.min + vec2(margin, margin),
        Corner::TopRight => pos2(screen.right() - width - margin, screen.top() + margin),
        Corner::BottomLeft => pos2(screen.left() + margin, screen.bottom() - height - margin),
        Corner::BottomRight => screen.max - vec2(width + margin, height + margin),
    };
    let rect = Rect::from_min_size(min, vec2(width, height));
    let alpha = (model.settings.overlay_opacity.clamp(0.0, 0.9) * 255.0) as u8;
    painter.rect_filled(rect, CornerRadius::same((6.0 * scale) as u8), Color32::from_black_alpha(alpha));
    let mut y = rect.top() + pad;
    for (index, row) in galleys.iter().enumerate() {
        let mut x = rect.left() + pad;
        let row_height = if index == 0 { first_height } else { line_height };
        for (galley, color) in row {
            let gy = y + row_height - galley.size().y;
            painter.galley(pos2(x, gy), galley.clone(), *color);
            x += galley.size().x;
        }
        y += row_height;
    }
    if let Some(size) = graph_size {
        let area = Rect::from_min_size(pos2(rect.left() + pad, y + 6.0 * scale), size);
        graph(&painter, area, stats, model.settings.overlay_graph, scale);
    }
    let _ = Align2::LEFT_TOP;
}

fn graph(painter: &egui::Painter, area: Rect, stats: &FrameStats, kind: OverlayGraph, scale: f32) {
    let (max, guides): (f32, [f32; 2]) = match kind {
        OverlayGraph::FrameTime => (50.0, [16.7, 33.3]),
        OverlayGraph::FrameRate => (60.0, [30.0, 60.0]),
    };
    for g in guides {
        let y = area.bottom() - area.height() * (g / max).min(1.0);
        painter.line_segment(
            [pos2(area.left(), y), pos2(area.right(), y)],
            Stroke::new(1.0, Color32::from_white_alpha(28)),
        );
    }
    let samples: Vec<f32> = stats.intervals.iter().copied().collect();
    let n = FrameStats::GRAPH_SAMPLES as f32;
    let start = n - samples.len() as f32;
    let mut segment: Vec<Pos2> = Vec::new();
    let flush = |segment: &mut Vec<Pos2>| {
        if segment.len() > 1 {
            painter.add(egui::Shape::line(std::mem::take(segment), Stroke::new(1.3 * scale, theme::GPU)));
        }
        segment.clear();
    };
    for (i, ms) in samples.iter().enumerate() {
        if !ms.is_finite() {
            flush(&mut segment);
            continue;
        }
        let value = match kind {
            OverlayGraph::FrameTime => *ms,
            OverlayGraph::FrameRate => 1000.0 / ms.max(1.0),
        };
        let x = area.left() + area.width() * (start + i as f32) / n;
        let y = area.bottom() - area.height() * (value / max).clamp(0.0, 1.0);
        segment.push(pos2(x, y));
        if kind == OverlayGraph::FrameTime && *ms > 50.0 {
            painter.circle_filled(pos2(x, area.top() + 2.0), 1.6 * scale, theme::DANGER);
        }
    }
    flush(&mut segment);
}
