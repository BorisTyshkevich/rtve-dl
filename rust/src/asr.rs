use crate::subs::{cues_to_srt, parse_srt, Cue};
use anyhow::{bail, Context, Result};
use hound::WavReader;
use regex::Regex;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use whisper_rs::{
    convert_integer_to_float_audio, install_logging_hooks, FullParams, SamplingStrategy,
    WhisperContext, WhisperContextParameters,
};

#[derive(Debug, Clone)]
pub struct AsrOptions {
    pub backend: String,
    pub model: String,
    pub device: String,
    pub compute_type: String,
    pub batch_size: usize,
    pub vad_method: String,
}

pub trait TranscriptionBackend {
    fn media_to_srt(&self, media_path: &Path, out_srt: &Path) -> Result<()>;
}

#[derive(Debug, Clone)]
pub struct WhisperxBackend {
    opts: AsrOptions,
}

impl WhisperxBackend {
    pub fn new(opts: AsrOptions) -> Self {
        Self { opts }
    }
}

impl TranscriptionBackend for WhisperxBackend {
    fn media_to_srt(&self, media_path: &Path, out_srt: &Path) -> Result<()> {
        let out_dir = out_srt.parent().context("missing whisperx output dir")?;
        fs::create_dir_all(out_dir)?;
        let output = Command::new("whisperx")
            .arg(media_path)
            .arg("--language")
            .arg("es")
            .arg("--task")
            .arg("transcribe")
            .arg("--model")
            .arg(&self.opts.model)
            .arg("--device")
            .arg(&self.opts.device)
            .arg("--compute_type")
            .arg(&self.opts.compute_type)
            .arg("--batch_size")
            .arg(self.opts.batch_size.to_string())
            .arg("--vad_method")
            .arg(&self.opts.vad_method)
            .arg("--output_format")
            .arg("srt")
            .arg("--output_dir")
            .arg(out_dir)
            .output()
            .context("spawn whisperx for ASR")?;
        if !output.status.success() {
            bail!("whisperx failed for ASR");
        }
        let produced = out_dir.join(format!(
            "{}.srt",
            media_path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("audio")
        ));
        if produced != out_srt {
            fs::rename(produced, out_srt)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct WhisperRsBackend {
    opts: AsrOptions,
}

impl WhisperRsBackend {
    pub fn new(opts: AsrOptions) -> Self {
        Self { opts }
    }
}

fn temp_wav_path(prefix: &str) -> std::path::PathBuf {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    std::env::temp_dir().join(format!("{prefix}.{now}.wav"))
}

fn temp_srt_path(prefix: &str) -> std::path::PathBuf {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    std::env::temp_dir().join(format!("{prefix}.{now}.srt"))
}

fn ensure_wav_16k_mono(media_path: &Path) -> Result<(std::path::PathBuf, bool)> {
    if media_path.extension().and_then(|s| s.to_str()) == Some("wav") {
        return Ok((media_path.to_path_buf(), false));
    }
    let wav = temp_wav_path("rtve-whisper");
    let output = Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-nostdin")
        .arg("-loglevel")
        .arg("error")
        .arg("-y")
        .arg("-i")
        .arg(media_path)
        .arg("-vn")
        .arg("-ac")
        .arg("1")
        .arg("-ar")
        .arg("16000")
        .arg("-f")
        .arg("wav")
        .arg(&wav)
        .output()
        .context("convert media to wav for whisper-rs")?;
    if !output.status.success() {
        bail!("ffmpeg failed to prepare audio for whisper-rs");
    }
    Ok((wav, true))
}

fn load_wav_16k_mono_f32(wav_path: &Path) -> Result<Vec<f32>> {
    let mut reader = WavReader::open(wav_path).context("open wav for whisper-rs")?;
    let spec = reader.spec();
    if spec.channels != 1 {
        bail!("whisper-rs wav must be mono, got {}", spec.channels);
    }
    if spec.sample_rate != 16_000 {
        bail!("whisper-rs wav must be 16kHz, got {}", spec.sample_rate);
    }

    let audio = match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Int, 16) => {
            let samples: Vec<i16> = reader
                .samples::<i16>()
                .collect::<std::result::Result<Vec<_>, _>>()
                .context("read 16-bit wav samples for whisper-rs")?;
            let mut audio = vec![0.0f32; samples.len()];
            convert_integer_to_float_audio(&samples, &mut audio)
                .context("convert wav to f32 for whisper-rs")?;
            audio
        }
        (hound::SampleFormat::Float, 32) => reader
            .samples::<f32>()
            .collect::<std::result::Result<Vec<_>, _>>()
            .context("read float wav samples for whisper-rs")?,
        (format, bits) => bail!(
            "unsupported wav format for whisper-rs: {:?} {}-bit",
            format,
            bits
        ),
    };
    Ok(audio)
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn repo_relative_base(path: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join(path)
}

fn candidate_model_paths(model: &str) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    let path = Path::new(model);
    if path.is_absolute() || model.contains('/') {
        candidates.push(path.to_path_buf());
        return candidates;
    }

    if let Ok(model_path) = std::env::var("WHISPER_MODEL_PATH") {
        candidates.push(PathBuf::from(model_path));
    }
    if let Ok(model_dir) = std::env::var("WHISPER_MODEL_DIR") {
        for leaf in [
            format!("ggml-{model}.bin"),
            format!("ggml-{model}.gguf"),
            format!("{model}.bin"),
            format!("{model}.gguf"),
        ] {
            candidates.push(PathBuf::from(&model_dir).join(leaf));
        }
    }
    for base in [
        PathBuf::from("models"),
        PathBuf::from("tmp/models"),
        repo_relative_base("models"),
        repo_relative_base("tmp/models"),
        home_dir().unwrap_or_default().join(".cache/whisper"),
        home_dir().unwrap_or_default().join(".cache/whisper.cpp"),
        home_dir().unwrap_or_default().join("models"),
    ] {
        if base.as_os_str().is_empty() {
            continue;
        }
        for leaf in [
            format!("ggml-{model}.bin"),
            format!("ggml-{model}.gguf"),
            format!("{model}.bin"),
            format!("{model}.gguf"),
        ] {
            candidates.push(base.join(leaf));
        }
    }
    candidates
}

pub fn detect_native_whisper_model_path(model: &str) -> Option<PathBuf> {
    candidate_model_paths(model)
        .into_iter()
        .find(|p| p.exists())
}

fn resolve_whisper_model_path(model: &str) -> Result<PathBuf> {
    if let Some(path) = detect_native_whisper_model_path(model) {
        return Ok(path);
    }
    bail!(
        "native whisper backend requires a local model file; tried resolving {}",
        model
    )
}

pub fn resolve_asr_backend_name(backend: &str, _model: &str) -> String {
    match backend {
        "auto" => "whisperx".to_string(),
        other => other.to_string(),
    }
}

impl TranscriptionBackend for WhisperRsBackend {
    fn media_to_srt(&self, media_path: &Path, out_srt: &Path) -> Result<()> {
        install_logging_hooks();
        let model_path = resolve_whisper_model_path(&self.opts.model)?;
        let (wav_path, cleanup_wav) = ensure_wav_16k_mono(media_path)?;
        let result = (|| -> Result<()> {
            let audio = load_wav_16k_mono_f32(&wav_path)?;
            let ctx = WhisperContext::new_with_params(
                model_path
                    .to_str()
                    .context("native whisper model path is not valid UTF-8")?,
                WhisperContextParameters::default(),
            )
            .context("load whisper-rs model")?;
            let mut state = ctx.create_state().context("create whisper-rs state")?;
            let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 0 });
            let threads = std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1);
            params.set_n_threads(threads.min(8) as i32);
            params.set_translate(false);
            params.set_language(Some("es"));
            params.set_print_special(false);
            params.set_print_progress(false);
            params.set_print_realtime(false);
            params.set_print_timestamps(false);
            state
                .full(params, &audio)
                .context("run whisper-rs transcription")?;

            let mut cues = Vec::new();
            for segment in state.as_iter() {
                let text = segment.to_str_lossy()?.trim().to_string();
                if text.is_empty() {
                    continue;
                }
                let start_ms = segment.start_timestamp() * 10;
                let end_ms = (segment.end_timestamp() * 10).max(start_ms + 1);
                cues.push(Cue {
                    start_ms,
                    end_ms,
                    text,
                });
            }
            if cues.is_empty() {
                bail!("whisper-rs produced no transcript segments");
            }
            fs::write(out_srt, cues_to_srt(&cues)).context("write whisper-rs srt output")?;
            Ok(())
        })();
        if cleanup_wav {
            let _ = fs::remove_file(&wav_path);
        }
        result
    }
}

pub fn transcribe_media_to_srt(media_path: &Path, out_srt: &Path, opts: &AsrOptions) -> Result<()> {
    let backend = build_transcription_backend(opts)?;
    backend.media_to_srt(media_path, out_srt)
}

pub fn transcribe_media_to_cues(media_path: &Path, opts: &AsrOptions) -> Result<Vec<Cue>> {
    let out_srt = temp_srt_path("rtve-asr");
    transcribe_media_to_srt(media_path, &out_srt, opts)?;
    let result = parse_srt(&fs::read_to_string(&out_srt)?);
    let _ = fs::remove_file(out_srt);
    Ok(result)
}

pub fn normalize_text_for_compare(text: &str) -> String {
    let lower = text
        .to_lowercase()
        .replace(['á', 'à', 'ä', 'â'], "a")
        .replace(['é', 'è', 'ë', 'ê'], "e")
        .replace(['í', 'ì', 'ï', 'î'], "i")
        .replace(['ó', 'ò', 'ö', 'ô'], "o")
        .replace(['ú', 'ù', 'ü', 'û'], "u")
        .replace('ñ', "n");
    let re = Regex::new(r"[^\p{L}\p{N}\s]+").unwrap();
    let ws_re = Regex::new(r"\s+").unwrap();
    ws_re
        .replace_all(&re.replace_all(&lower, " "), " ")
        .trim()
        .to_string()
}

pub fn cues_text(cues: &[Cue]) -> String {
    cues.iter()
        .map(|cue| cue.text.trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn normalized_cue_text_similarity(left: &[Cue], right: &[Cue]) -> f64 {
    let left_text = normalize_text_for_compare(&cues_text(left));
    let right_text = normalize_text_for_compare(&cues_text(right));
    if left_text.is_empty() || right_text.is_empty() {
        return 0.0;
    }
    let mut left_counts = BTreeMap::<String, usize>::new();
    let mut right_counts = BTreeMap::<String, usize>::new();
    let left_tokens: Vec<&str> = left_text.split_whitespace().collect();
    let right_tokens: Vec<&str> = right_text.split_whitespace().collect();
    for token in &left_tokens {
        *left_counts.entry((*token).to_string()).or_default() += 1;
    }
    for token in &right_tokens {
        *right_counts.entry((*token).to_string()).or_default() += 1;
    }
    let overlap: usize = left_counts
        .iter()
        .map(|(token, left_count)| left_count.min(right_counts.get(token).unwrap_or(&0)))
        .sum();
    if overlap == 0 {
        return 0.0;
    }
    let dice = (2.0 * overlap as f64) / (left_tokens.len() + right_tokens.len()) as f64;
    let reference_coverage = overlap as f64 / right_tokens.len() as f64;
    dice.max(reference_coverage)
}

pub fn asr_model_display_name(opts: &AsrOptions) -> String {
    let backend = resolve_asr_backend_name(&opts.backend, &opts.model);
    let model = Path::new(&opts.model)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(&opts.model)
        .trim()
        .to_string();
    format!("{backend}-{model}")
}

pub fn deduplicate_repetitions(text: &str) -> (String, bool) {
    let token_re = Regex::new(r"\p{L}[\p{L}\p{N}_-]*").unwrap();
    let tokens: Vec<String> = token_re
        .find_iter(text)
        .map(|m| m.as_str().to_string())
        .collect();
    if tokens.len() < 4 {
        return (text.to_string(), false);
    }
    let first_norm = tokens[0].to_lowercase();
    if !tokens.iter().all(|t| t.to_lowercase() == first_norm) {
        return (text.to_string(), false);
    }
    (tokens[0].clone(), true)
}

fn normalize_for_duplicate_comparison(text: &str) -> String {
    text.trim()
        .to_lowercase()
        .trim_matches(|c: char| ".,!?¿¡;:\"'".contains(c))
        .to_string()
}

pub fn deduplicate_asr_hallucinations(cues: &[Cue]) -> Vec<Cue> {
    let mut cleaned = Vec::new();
    for cue in cues {
        let (text, _) = deduplicate_repetitions(cue.text.trim());
        cleaned.push(Cue {
            start_ms: cue.start_ms,
            end_ms: cue.end_ms,
            text,
        });
    }

    let mut collapsed = Vec::new();
    let mut i = 0usize;
    while i < cleaned.len() {
        let current = &cleaned[i];
        let current_norm = normalize_for_duplicate_comparison(&current.text);
        let mut run_end = i + 1;
        while run_end < cleaned.len()
            && normalize_for_duplicate_comparison(&cleaned[run_end].text) == current_norm
        {
            run_end += 1;
        }
        if run_end - i >= 4 && !current_norm.is_empty() {
            collapsed.push(Cue {
                start_ms: current.start_ms,
                end_ms: cleaned[run_end - 1].end_ms,
                text: current.text.clone(),
            });
            i = run_end;
        } else {
            collapsed.push(current.clone());
            i += 1;
        }
    }
    collapsed
}

pub fn build_transcription_backend(opts: &AsrOptions) -> Result<Box<dyn TranscriptionBackend>> {
    match resolve_asr_backend_name(&opts.backend, &opts.model).as_str() {
        "whisperx" => Ok(Box::new(WhisperxBackend::new(opts.clone()))),
        "whisper-rs" | "whisper" => Ok(Box::new(WhisperRsBackend::new(opts.clone()))),
        other => bail!("unsupported ASR backend in Rust: {other}"),
    }
}

pub fn delay_transcription_backend(
    opts: &AsrOptions,
) -> Result<Option<Box<dyn TranscriptionBackend>>> {
    match build_transcription_backend(opts) {
        Ok(backend) => Ok(Some(backend)),
        Err(_) => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixture_path(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/asr")
            .join(name)
    }

    #[test]
    fn build_whisperx_backend() {
        let backend = build_transcription_backend(&AsrOptions {
            backend: "whisperx".to_string(),
            model: "small".to_string(),
            device: "cpu".to_string(),
            compute_type: "int8".to_string(),
            batch_size: 8,
            vad_method: "silero".to_string(),
        });
        assert!(backend.is_ok());
    }

    #[test]
    fn reject_unknown_backend() {
        let backend = build_transcription_backend(&AsrOptions {
            backend: "mlx".to_string(),
            model: "small".to_string(),
            device: "cpu".to_string(),
            compute_type: "int8".to_string(),
            batch_size: 8,
            vad_method: "silero".to_string(),
        });
        assert!(backend.is_err());
    }

    #[test]
    fn build_native_whisper_backend() {
        let backend = build_transcription_backend(&AsrOptions {
            backend: "whisper-rs".to_string(),
            model: "/tmp/model.gguf".to_string(),
            device: "cpu".to_string(),
            compute_type: "int8".to_string(),
            batch_size: 8,
            vad_method: "silero".to_string(),
        });
        assert!(backend.is_ok());
    }

    #[test]
    fn auto_prefers_whisperx_even_when_native_model_exists() {
        let dir = tempfile::tempdir().unwrap();
        let models_dir = dir.path().join("models");
        fs::create_dir_all(&models_dir).unwrap();
        let model = models_dir.join("ggml-small.bin");
        fs::write(&model, "x").unwrap();
        let prev = std::env::current_dir().unwrap();
        std::env::set_current_dir(dir.path()).unwrap();
        assert_eq!(resolve_asr_backend_name("auto", "small"), "whisperx");
        std::env::set_current_dir(prev).unwrap();
    }

    #[test]
    fn normalize_text_for_compare_collapses_noise() {
        assert_eq!(
            normalize_text_for_compare("Hola,\n¿Qué tal?   Bien."),
            "hola que tal bien"
        );
    }

    #[test]
    fn normalized_similarity_ignores_case_punctuation_and_linebreaks() {
        let left = vec![Cue {
            start_ms: 0,
            end_ms: 1000,
            text: "Hola,\n¿Qué tal?".to_string(),
        }];
        let right = vec![Cue {
            start_ms: 0,
            end_ms: 1000,
            text: "hola que tal".to_string(),
        }];
        assert!(normalized_cue_text_similarity(&left, &right) > 0.95);
    }

    #[test]
    fn deduplicate_asr_hallucinations_reduces_repetition() {
        let cues = vec![
            Cue {
                start_ms: 0,
                end_ms: 1000,
                text: "no, no, no, no, no".to_string(),
            },
            Cue {
                start_ms: 1000,
                end_ms: 2000,
                text: "Sí.".to_string(),
            },
            Cue {
                start_ms: 2000,
                end_ms: 3000,
                text: "Sí.".to_string(),
            },
            Cue {
                start_ms: 3000,
                end_ms: 4000,
                text: "Sí.".to_string(),
            },
            Cue {
                start_ms: 4000,
                end_ms: 5000,
                text: "Sí.".to_string(),
            },
        ];
        let deduped = deduplicate_asr_hallucinations(&cues);
        assert_eq!(deduped[0].text, "no");
        assert_eq!(deduped.len(), 2);
        assert_eq!(deduped[1].start_ms, 1000);
        assert_eq!(deduped[1].end_ms, 5000);
    }

    #[test]
    fn asr_fixture_reference_is_trimmed_to_first_five_minutes() {
        let cues =
            parse_srt(&fs::read_to_string(fixture_path("s08e19_first5m.reference.srt")).unwrap());
        assert!(!cues.is_empty());
        assert!(cues.iter().all(|cue| cue.start_ms < 300_000));
        assert!(cues.iter().all(|cue| cue.end_ms <= 300_000));
    }

    #[test]
    fn asr_fixture_audio_is_flac_16k_mono() {
        let output = Command::new("ffprobe")
            .arg("-v")
            .arg("error")
            .arg("-select_streams")
            .arg("a:0")
            .arg("-show_entries")
            .arg("stream=codec_name,sample_rate,channels")
            .arg("-of")
            .arg("default=nokey=1:noprint_wrappers=1")
            .arg(fixture_path("s08e19_first5m.flac"))
            .output()
            .unwrap();
        assert!(output.status.success());
        let values: Vec<String> = String::from_utf8_lossy(&output.stdout)
            .lines()
            .map(|line| line.trim().to_string())
            .collect();
        assert!(values.iter().any(|v| v == "flac"));
        assert!(values.iter().any(|v| v == "16000"));
        assert!(values.iter().any(|v| v == "1"));
    }

    #[test]
    #[ignore = "slow acceptance test: requires local native whisper model"]
    fn native_asr_fixture_similarity_meets_threshold() {
        assert!(
            detect_native_whisper_model_path("small").is_some(),
            "missing native whisper model: install models/ggml-small.bin as documented in README.rust.md"
        );
        let cues = transcribe_media_to_cues(
            &fixture_path("s08e19_first5m.flac"),
            &AsrOptions {
                backend: "whisper-rs".to_string(),
                model: "small".to_string(),
                device: "cpu".to_string(),
                compute_type: "int8".to_string(),
                batch_size: 8,
                vad_method: "silero".to_string(),
            },
        )
        .unwrap();
        let reference =
            parse_srt(&fs::read_to_string(fixture_path("s08e19_first5m.reference.srt")).unwrap());
        let similarity = normalized_cue_text_similarity(&cues, &reference);
        assert!(
            similarity >= 0.60,
            "native ASR similarity too low: got {similarity:.3}, want >= 0.60"
        );
    }
}
