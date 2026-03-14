use crate::asr::{delay_transcription_backend, AsrOptions};
use crate::ffmpeg::{probe_duration_seconds, run_ffmpeg};
use crate::logging::debug;
use crate::subs::{parse_srt, Cue};
use anyhow::{Context, Result};
use regex::Regex;
use std::cmp::{max, min};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use strsim::normalized_levenshtein;

const DEFAULT_SUBTITLE_DELAY_MS: i64 = 450;
const AUTO_DELAY_ASR_SEGMENT_S: f64 = 300.0;
const ASR_MATCH_SIM_MIN: f64 = 0.66;
const ASR_MIN_MATCHES: usize = 12;
const ASR_SHORT_TEXT_CHARS: usize = 8;
const ASR_SHORT_TEXT_STRONG_SIM_MIN: f64 = 0.88;
const ASR_DELAY_CLUSTER_MS: i64 = 350;
const ENERGY_PCTL: f64 = 0.55;
const ENERGY_FLOOR: i32 = 400;

#[derive(Debug, Clone, Copy)]
pub struct DelayEstimate {
    pub delay_ms: i64,
    pub confidence: f64,
    pub method: &'static str,
    pub matched: usize,
}

#[derive(Debug, Clone)]
pub struct DelayInspection {
    pub energy: Option<DelayEstimate>,
    pub asr: Option<DelayEstimate>,
    pub final_estimate: Option<DelayEstimate>,
}

fn norm_text(s: &str) -> String {
    let lower = s.to_lowercase().replace('\n', " ");
    let re = Regex::new(r"[^a-z0-9а-яёñáéíóúü]+").unwrap();
    let ws = Regex::new(r"\s+").unwrap();
    ws.replace_all(&re.replace_all(&lower, " "), " ")
        .trim()
        .to_string()
}

fn text_char_len(s: &str) -> usize {
    s.chars().filter(|c| !c.is_whitespace()).count()
}

fn weighted_median(samples: &[(i64, f64)]) -> i64 {
    let mut ordered = samples.to_vec();
    ordered.sort_unstable_by_key(|(value, _)| *value);
    let total: f64 = ordered.iter().map(|(_, weight)| *weight).sum();
    if total <= 0.0 {
        return ordered[ordered.len() / 2].0;
    }
    let threshold = total / 2.0;
    let mut running = 0.0;
    for (value, weight) in ordered {
        running += weight;
        if running >= threshold {
            return value;
        }
    }
    samples[samples.len() - 1].0
}

fn select_delay_cluster(matches: &[(i64, f64)]) -> Option<(i64, usize, f64)> {
    if matches.len() < ASR_MIN_MATCHES {
        return None;
    }
    let total_weight: f64 = matches.iter().map(|(_, weight)| *weight).sum();
    if total_weight <= 0.0 {
        return None;
    }
    let mut best_center = 0i64;
    let mut best_score = -1.0f64;
    for (center, _) in matches {
        let score: f64 = matches
            .iter()
            .filter(|(delta, _)| (delta - center).abs() <= ASR_DELAY_CLUSTER_MS)
            .map(|(_, weight)| *weight)
            .sum();
        if score > best_score {
            best_score = score;
            best_center = *center;
        }
    }
    let inliers: Vec<(i64, f64)> = matches
        .iter()
        .copied()
        .filter(|(delta, _)| (delta - best_center).abs() <= ASR_DELAY_CLUSTER_MS)
        .collect();
    if inliers.len() < ASR_MIN_MATCHES {
        return None;
    }
    let delay_ms = weighted_median(&inliers);
    Some((delay_ms, inliers.len(), (best_score / total_weight).clamp(0.0, 1.0)))
}

fn activity_intervals_from_cues(cues: &[Cue], bin_ms: i64, n_bins: i64) -> Vec<(i64, i64)> {
    let mut out = Vec::new();
    for cue in cues {
        let s = max(0, min(n_bins, cue.start_ms / bin_ms));
        let e = max(0, min(n_bins, (cue.end_ms + bin_ms - 1) / bin_ms));
        if e > s {
            out.push((s, e));
        }
    }
    out.sort_unstable();
    out
}

fn merge_intervals(intervals: &[(i64, i64)]) -> Vec<(i64, i64)> {
    if intervals.is_empty() {
        return Vec::new();
    }
    let mut merged = vec![intervals[0]];
    for &(s, e) in &intervals[1..] {
        let last = merged.last_mut().unwrap();
        if s <= last.1 {
            last.1 = max(last.1, e);
        } else {
            merged.push((s, e));
        }
    }
    merged
}

fn overlap_len(a: &[(i64, i64)], b: &[(i64, i64)]) -> i64 {
    let mut i = 0usize;
    let mut j = 0usize;
    let mut total = 0i64;
    while i < a.len() && j < b.len() {
        let (s1, e1) = a[i];
        let (s2, e2) = b[j];
        let s = max(s1, s2);
        let e = min(e1, e2);
        if e > s {
            total += e - s;
        }
        if e1 <= e2 {
            i += 1;
        } else {
            j += 1;
        }
    }
    total
}

fn shift_intervals(intervals: &[(i64, i64)], shift_bins: i64, n_bins: i64) -> Vec<(i64, i64)> {
    let mut out = Vec::new();
    for &(s, e) in intervals {
        let ss = s + shift_bins;
        let ee = e + shift_bins;
        if ee <= 0 || ss >= n_bins {
            continue;
        }
        out.push((max(0, ss), min(n_bins, ee)));
    }
    out
}

fn audio_activity_intervals(mp4_path: &Path, bin_ms: i64) -> Result<Vec<(i64, i64)>> {
    let rate = 1000 / bin_ms;
    debug(format!(
        "subtitle auto-delay: ffmpeg extract audio {} (bin_ms={bin_ms})",
        mp4_path.display()
    ));
    let output = Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-nostdin")
        .arg("-loglevel")
        .arg("error")
        .arg("-i")
        .arg(mp4_path)
        .arg("-vn")
        .arg("-ac")
        .arg("1")
        .arg("-ar")
        .arg(rate.to_string())
        .arg("-f")
        .arg("s16le")
        .arg("-")
        .output()
        .context("ffmpeg audio extract for delay")?;
    if !output.status.success() {
        anyhow::bail!(
            "ffmpeg audio extract failed for delay auto: {}",
            mp4_path.display()
        );
    }
    let raw = output.stdout;
    if raw.len() < 2 {
        return Ok(Vec::new());
    }
    let mut vals = Vec::new();
    for chunk in raw.chunks_exact(2) {
        vals.push(i16::from_le_bytes([chunk[0], chunk[1]]).abs() as i32);
    }
    if vals.is_empty() {
        return Ok(Vec::new());
    }
    let mut sorted = vals.clone();
    sorted.sort_unstable();
    let idx = ((ENERGY_PCTL * ((sorted.len() - 1) as f64)) as usize).min(sorted.len() - 1);
    let thr = max(ENERGY_FLOOR, sorted[idx]);
    let mut intervals = Vec::new();
    let mut i = 0usize;
    while i < vals.len() {
        if vals[i] < thr {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < vals.len() && vals[j] >= thr {
            j += 1;
        }
        intervals.push((i as i64, j as i64));
        i = j;
    }
    Ok(merge_intervals(&intervals))
}

fn estimate_by_energy(cues: &[Cue], mp4_path: &Path, max_ms: i64) -> Result<Option<DelayEstimate>> {
    let bin_ms = 100i64;
    let speech = audio_activity_intervals(mp4_path, bin_ms)?;
    if speech.is_empty() {
        return Ok(None);
    }
    let n_bins = speech.last().unwrap().1;
    let subs = merge_intervals(&activity_intervals_from_cues(cues, bin_ms, n_bins));
    if subs.is_empty() {
        return Ok(None);
    }
    let max_bins = max_ms / bin_ms;
    let mut scores = Vec::new();
    for lag in -max_bins..=max_bins {
        let shifted = shift_intervals(&subs, lag, n_bins);
        scores.push((lag, overlap_len(&shifted, &speech)));
    }
    scores.sort_by(|a, b| b.1.cmp(&a.1));
    let (best_lag, best_score) = scores[0];
    let second_score = if scores.len() > 1 { scores[1].1 } else { 0 };
    let total_sub: i64 = subs.iter().map(|(s, e)| e - s).sum();
    if total_sub <= 0 {
        return Ok(None);
    }
    let confidence =
        ((best_score - second_score) as f64 / max(1, total_sub) as f64).clamp(0.0, 1.0);
    Ok(Some(DelayEstimate {
        delay_ms: best_lag * bin_ms,
        confidence,
        method: "energy",
        matched: subs.len(),
    }))
}

fn temp_path(dir: &Path, prefix: &str, suffix: &str) -> PathBuf {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    dir.join(format!("{prefix}.{now}.{suffix}"))
}

fn estimate_by_asr(
    cues: &[Cue],
    mp4_path: &Path,
    tmp_dir: &Path,
    base: &str,
    asr_opts: &AsrOptions,
    max_ms: i64,
    save_asr_srt: Option<&Path>,
) -> Result<Option<DelayEstimate>> {
    let Some(backend) = delay_transcription_backend(asr_opts)? else {
        debug(format!(
            "subtitle auto-delay: ASR fallback skipped, unsupported Rust backend: {}",
            asr_opts.backend
        ));
        return Ok(None);
    };
    fs::create_dir_all(tmp_dir)?;
    let asr_srt = temp_path(tmp_dir, &format!("auto_delay.{base}"), "srt");
    let mut clip_source = mp4_path.to_path_buf();
    let mut clip_path: Option<PathBuf> = None;
    let mut clip_start_ms = 0i64;
    let mut clip_end_ms = 0i64;
    if let Some(duration_s) = probe_duration_seconds(mp4_path)? {
        if duration_s > AUTO_DELAY_ASR_SEGMENT_S {
            let start_s = ((duration_s / 2.0) - (AUTO_DELAY_ASR_SEGMENT_S / 2.0)).max(0.0);
            clip_start_ms = (start_s * 1000.0) as i64;
            clip_end_ms = ((start_s + AUTO_DELAY_ASR_SEGMENT_S) * 1000.0) as i64;
            let wav = temp_path(tmp_dir, &format!("auto_delay.{base}"), "wav");
            let args = vec![
                "-y".to_string(),
                "-ss".to_string(),
                format!("{start_s:.3}"),
                "-t".to_string(),
                format!("{AUTO_DELAY_ASR_SEGMENT_S:.3}"),
                "-i".to_string(),
                mp4_path.display().to_string(),
                "-vn".to_string(),
                "-ac".to_string(),
                "1".to_string(),
                "-ar".to_string(),
                "16000".to_string(),
                "-f".to_string(),
                "wav".to_string(),
                wav.display().to_string(),
            ];
            if run_ffmpeg(&args).is_ok() {
                debug(format!(
                    "subtitle auto-delay ASR clip: start={start_s:.1}s dur={AUTO_DELAY_ASR_SEGMENT_S}s"
                ));
                clip_source = wav.clone();
                clip_path = Some(wav);
            }
        }
    }

    let result = (|| -> Result<Option<DelayEstimate>> {
        backend.media_to_srt(&clip_source, &asr_srt)?;
        if let Some(save_path) = save_asr_srt {
            if let Some(parent) = save_path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(&asr_srt, save_path)?;
            debug(format!(
                "subtitle auto-delay ASR transcript saved: {}",
                save_path.display()
            ));
        }
        let asr_cues = parse_srt(&fs::read_to_string(&asr_srt)?);
        if asr_cues.is_empty() {
            return Ok(None);
        }
        let sub_t: Vec<(String, i64)> = if clip_start_ms > 0 && clip_end_ms > clip_start_ms {
            cues.iter()
                .filter_map(|c| {
                    let t = norm_text(&c.text);
                    if t.is_empty() || c.start_ms < clip_start_ms || c.start_ms > clip_end_ms {
                        None
                    } else {
                        Some((t, c.start_ms))
                    }
                })
                .collect()
        } else {
            cues.iter()
                .filter_map(|c| {
                    let t = norm_text(&c.text);
                    if t.is_empty() {
                        None
                    } else {
                        Some((t, c.start_ms))
                    }
                })
                .collect()
        };
        let asr_t: Vec<(String, i64)> = asr_cues
            .iter()
            .filter_map(|c| {
                let t = norm_text(&c.text);
                if t.is_empty() {
                    None
                } else {
                    Some((t, c.start_ms + clip_start_ms))
                }
            })
            .collect();
        if sub_t.is_empty() || asr_t.is_empty() {
            return Ok(None);
        }
        let mut matches = Vec::new();
        let mut sims = Vec::new();
        let mut j = 0usize;
        for (st, s_ms) in &sub_t {
            let mut best_sim = 0.0;
            let mut best_j: Option<usize> = None;
            let hi = min(asr_t.len(), j + 25);
            for k in j..hi {
                let sim = normalized_levenshtein(st, &asr_t[k].0);
                if sim > best_sim {
                    best_sim = sim;
                    best_j = Some(k);
                }
            }
            if let Some(best_idx) = best_j {
                if best_sim >= ASR_MATCH_SIM_MIN {
                    let text_chars = min(text_char_len(st), text_char_len(&asr_t[best_idx].0));
                    if text_chars < ASR_SHORT_TEXT_CHARS && best_sim < ASR_SHORT_TEXT_STRONG_SIM_MIN
                    {
                        continue;
                    }
                    let delta = asr_t[best_idx].1 - *s_ms;
                    if delta.abs() <= max_ms {
                        let weight = best_sim * (text_chars.clamp(1, 32) as f64);
                        matches.push((delta, weight));
                        sims.push(best_sim);
                        j = best_idx;
                    }
                }
            }
        }
        let Some((delay_ms, matched, cluster_ratio)) = select_delay_cluster(&matches) else {
            return Ok(None);
        };
        debug(format!(
            "subtitle auto-delay ASR cluster: delay_ms={delay_ms} matched={matched}/{} cluster_ratio={cluster_ratio:.3}",
            matches.len()
        ));
        let avg_sim = sims.iter().sum::<f64>() / sims.len() as f64;
        let confidence = cluster_ratio * (matched as f64 / 40.0).min(1.0) * avg_sim;
        Ok(Some(DelayEstimate {
            delay_ms,
            confidence,
            method: "asr",
            matched,
        }))
    })();

    let _ = fs::remove_file(&asr_srt);
    if let Some(path) = clip_path {
        let _ = fs::remove_file(path);
    }
    result
}

pub fn inspect_episode_delay(
    cues: &[Cue],
    mp4_path: &Path,
    tmp_dir: &Path,
    base: &str,
    max_ms: i64,
    asr_opts: &AsrOptions,
    run_energy: bool,
    run_asr: bool,
    save_asr_srt: Option<&Path>,
) -> Result<DelayInspection> {
    let energy = if run_energy {
        estimate_by_energy(cues, mp4_path, max_ms)?
    } else {
        None
    };
    let mut final_estimate = energy;
    let mut asr = None;
    if run_asr
        && (final_estimate.is_none() || final_estimate.map(|e| e.confidence).unwrap_or(0.0) < 0.10)
    {
        if run_energy {
            debug(format!(
                "subtitle auto-delay: low-confidence energy on {base}, trying ASR"
            ));
        } else {
            debug(format!("subtitle auto-delay: energy skipped on {base}, trying ASR"));
        }
        asr = estimate_by_asr(cues, mp4_path, tmp_dir, base, asr_opts, max_ms, save_asr_srt)?;
        if asr.is_some() {
            final_estimate = asr;
        }
    }
    Ok(DelayInspection {
        energy,
        asr,
        final_estimate,
    })
}

pub fn estimate_episode_delay_ms(
    cues: &[Cue],
    mp4_path: &Path,
    tmp_dir: &Path,
    base: &str,
    max_ms: i64,
    asr_opts: &AsrOptions,
    save_asr_srt: Option<&Path>,
) -> Result<i64> {
    let inspection =
        inspect_episode_delay(cues, mp4_path, tmp_dir, base, max_ms, asr_opts, true, true, save_asr_srt)?;
    Ok(inspection
        .final_estimate
        .map(|e| e.delay_ms)
        .unwrap_or(DEFAULT_SUBTITLE_DELAY_MS))
}

pub fn shift_cues(cues: &[Cue], delay_ms: i64) -> Vec<Cue> {
    if delay_ms == 0 {
        return cues.to_vec();
    }
    cues.iter()
        .map(|c| {
            let start_ms = max(0, c.start_ms + delay_ms);
            let end_ms = max(start_ms + 1, c.end_ms + delay_ms);
            Cue {
                start_ms,
                end_ms,
                text: c.text.clone(),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::select_delay_cluster;

    #[test]
    fn select_delay_cluster_prefers_consistent_offset_over_noise() {
        let matches = vec![
            (-80, 4.0),
            (-60, 4.0),
            (-40, 3.5),
            (0, 3.0),
            (20, 2.5),
            (510, 16.0),
            (525, 18.0),
            (540, 19.0),
            (550, 24.0),
            (560, 20.0),
            (575, 18.0),
            (590, 16.0),
            (605, 13.0),
            (620, 12.0),
            (635, 11.0),
            (650, 10.0),
            (665, 9.0),
        ];

        let selected = select_delay_cluster(&matches).expect("cluster should be selected");
        assert_eq!(selected.0, 560);
        assert_eq!(selected.1, 12);
        assert!(selected.2 > 0.8);
    }
}
