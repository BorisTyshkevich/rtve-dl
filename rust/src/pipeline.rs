use crate::asr::{
    asr_model_display_name, deduplicate_asr_hallucinations, resolve_asr_backend_name,
    transcribe_media_to_srt, AsrOptions,
};
use crate::cli::Args;
use crate::delay::{inspect_episode_delay, shift_cues};
use crate::ffmpeg::{download_to_mp4, is_valid_mp4, mux_mkv};
use crate::http::HttpClient;
use crate::logging::{debug, set_debug};
use crate::rtve::{
    base_from_asset, list_assets_for_selector, pick_video_url, resolve_asset, SeriesAsset,
};
use crate::subs::{cues_to_srt, parse_vtt, Cue};
use crate::tracks::{
    build_refs_srt, build_ru_dual_srt, build_ru_srt, parse_track_policy,
    resolve_default_subtitle_title, ProducedTrack, TRACK_EN, TRACK_ES, TRACK_REFS, TRACK_RU,
    TRACK_RU_DUAL,
};
use crate::translate::{translate, TranslateOptions};
use anyhow::{bail, Context, Result};
use clap::Parser;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

const DEFAULT_SERIES_URL: &str = "https://www.rtve.es/play/videos/cuentame-como-paso/";

#[derive(Debug, Clone)]
struct TmpLayout {
    root: PathBuf,
    mp4: PathBuf,
    vtt: PathBuf,
    srt: PathBuf,
    codex_en: PathBuf,
    codex_ru: PathBuf,
    codex_ru_ref: PathBuf,
    meta: PathBuf,
}

impl TmpLayout {
    fn for_slug(root: PathBuf) -> Self {
        Self {
            mp4: root.join("mp4"),
            vtt: root.join("vtt"),
            srt: root.join("srt"),
            codex_en: root.join("codex").join("en"),
            codex_ru: root.join("codex").join("ru"),
            codex_ru_ref: root.join("codex").join("ru_ref"),
            meta: root.join("meta"),
            root,
        }
    }

    fn ensure_dirs(&self) -> Result<()> {
        for path in [
            &self.root,
            &self.mp4,
            &self.vtt,
            &self.srt,
            &self.codex_en,
            &self.codex_ru,
            &self.codex_ru_ref,
            &self.meta,
        ] {
            fs::create_dir_all(path)?;
        }
        Ok(())
    }

    fn mp4_file(&self, base: &str) -> PathBuf {
        self.mp4.join(format!("{base}.mp4"))
    }
    fn vtt_es_file(&self, asset_id: &str) -> PathBuf {
        self.vtt.join(format!("{asset_id}.es.vtt"))
    }
    fn vtt_en_file(&self, asset_id: &str) -> PathBuf {
        self.vtt.join(format!("{asset_id}.en.vtt"))
    }
    fn srt_es_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.spa.srt"))
    }
    fn srt_es_asr_raw_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.spa.asr_raw.srt"))
    }
    fn srt_en_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.eng.srt"))
    }
    fn srt_ru_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.rus.srt"))
    }
    fn srt_refs_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.spa_rus.srt"))
    }
    fn srt_bi_full_file(&self, base: &str) -> PathBuf {
        self.srt.join(format!("{base}.spa_rus_full.srt"))
    }
    fn codex_base(&self, base: &str, track: &str) -> PathBuf {
        match track {
            "en" => self.codex_en.join(format!("{base}.en")),
            "ru" => self.codex_ru.join(format!("{base}.ru")),
            "ru_ref" => self.codex_ru_ref.join(format!("{base}.ru_ref")),
            _ => self.meta.join(format!("{base}.{track}")),
        }
    }
}

fn slugify(s: &str) -> String {
    let lower = s.trim().to_lowercase();
    let no_scheme = Regex::new(r"https?://")
        .unwrap()
        .replace_all(&lower, "")
        .to_string();
    let compact = Regex::new(r"[^a-z0-9]+")
        .unwrap()
        .replace_all(&no_scheme, "-")
        .trim_matches('-')
        .to_string();
    if compact.is_empty() {
        "series".to_string()
    } else {
        compact.chars().take(80).collect()
    }
}

fn normalize_reset_layers(raw: &[String]) -> Result<BTreeSet<String>> {
    let allowed = BTreeSet::from([
        "subs-es".to_string(),
        "subs-en".to_string(),
        "subs-ru".to_string(),
        "subs-refs".to_string(),
        "video".to_string(),
        "mkv".to_string(),
        "catalog".to_string(),
    ]);
    let mut out = BTreeSet::new();
    for raw_entry in raw {
        for part in raw_entry.split(',') {
            let value = part.trim().to_lowercase();
            if value.is_empty() {
                continue;
            }
            if !allowed.contains(&value) {
                bail!("unknown reset layer: {value}");
            }
            out.insert(value);
        }
    }
    Ok(out)
}

fn expand_reset_layers(user: &BTreeSet<String>) -> BTreeSet<String> {
    let mut expanded = user.clone();
    loop {
        let prev = expanded.clone();
        if expanded.contains("video") {
            expanded.extend(
                ["subs-es", "subs-en", "subs-ru", "subs-refs", "mkv"]
                    .into_iter()
                    .map(str::to_string),
            );
        }
        if expanded.contains("subs-es") {
            expanded.extend(
                ["subs-en", "subs-ru", "subs-refs", "mkv"]
                    .into_iter()
                    .map(str::to_string),
            );
        }
        if expanded.contains("subs-en")
            || expanded.contains("subs-ru")
            || expanded.contains("subs-refs")
        {
            expanded.insert("mkv".to_string());
        }
        if expanded == prev {
            break;
        }
    }
    expanded
}

fn remove_glob(directory: &Path, pattern: &str) -> Result<()> {
    let pattern = directory.join(pattern);
    let glob = glob_simple(&pattern.to_string_lossy());
    for path in glob {
        let _ = fs::remove_file(path);
    }
    Ok(())
}

fn glob_simple(pattern: &str) -> Vec<PathBuf> {
    let path = PathBuf::from(pattern);
    let parent = path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let file_pattern = path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string();
    let regex_pattern = format!("^{}$", regex::escape(&file_pattern).replace("\\*", ".*"));
    let matcher = Regex::new(&regex_pattern).unwrap();
    fs::read_dir(parent)
        .ok()
        .into_iter()
        .flat_map(|rd| rd.filter_map(|e| e.ok()))
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .map(|n| matcher.is_match(&n.to_string_lossy()))
                .unwrap_or(false)
        })
        .collect()
}

fn reset_selector_layers(
    layout: &TmpLayout,
    out_dir: &Path,
    assets: &[SeriesAsset],
    layers: &BTreeSet<String>,
) -> Result<()> {
    for asset in assets {
        let prefix = format!(
            "S{:02}E{:02}_",
            asset.season.unwrap_or(0),
            asset.episode.unwrap_or(0)
        );
        let asset_id = &asset.asset_id;
        if layers.contains("mkv") {
            remove_glob(out_dir, &format!("{prefix}*.mkv"))?;
        }
        if layers.contains("video") {
            remove_glob(&layout.mp4, &format!("{prefix}*.mp4"))?;
        }
        if layers.contains("subs-es") {
            remove_glob(&layout.srt, &format!("{prefix}*.spa.srt"))?;
            let _ = fs::remove_file(layout.vtt_es_file(asset_id));
        }
        if layers.contains("subs-en") {
            remove_glob(&layout.srt, &format!("{prefix}*.eng.srt"))?;
            let _ = fs::remove_file(layout.vtt_en_file(asset_id));
        }
        if layers.contains("subs-ru") {
            remove_glob(&layout.srt, &format!("{prefix}*.rus.srt"))?;
            remove_glob(&layout.srt, &format!("{prefix}*.spa_rus_full.srt"))?;
            remove_glob(&layout.codex_ru, &format!("{prefix}*.ru*"))?;
        }
        if layers.contains("subs-refs") {
            remove_glob(&layout.srt, &format!("{prefix}*.spa_rus.srt"))?;
            remove_glob(&layout.codex_ru_ref, &format!("{prefix}*.ru_ref*"))?;
        }
    }
    Ok(())
}

fn download_sub_vtt(http: &HttpClient, url: &str, out_path: &Path) -> Result<()> {
    if out_path.exists() && out_path.metadata()?.len() > 0 {
        return Ok(());
    }
    fs::create_dir_all(out_path.parent().unwrap())?;
    fs::write(out_path, http.get_text(url)?)?;
    Ok(())
}

fn write_index_html(out_dir: &Path) -> Result<()> {
    let mut entries: Vec<_> = fs::read_dir(out_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|x| x.to_str()) == Some("mkv"))
        .collect();
    entries.sort();
    let mut html =
        String::from("<!doctype html><meta charset=\"utf-8\"><title>rtve-dl</title><ul>");
    for entry in entries {
        let name = entry.file_name().unwrap().to_string_lossy();
        html.push_str(&format!("<li><a href=\"{name}\">{name}</a></li>"));
    }
    html.push_str("</ul>");
    fs::write(out_dir.join("index.html"), html)?;
    Ok(())
}

fn translate_track(
    cues: &[Cue],
    base: &str,
    track: &str,
    model: Option<&str>,
    backend: &str,
    jobs: usize,
    layout: &TmpLayout,
) -> Result<BTreeMap<String, String>> {
    let pairs: Vec<(String, String)> = cues
        .iter()
        .enumerate()
        .map(|(i, c)| (i.to_string(), c.text.clone()))
        .collect();
    let opts = TranslateOptions {
        base_path: layout.codex_base(base, track),
        chunk_size: if pairs.len() > 1000 {
            500
        } else {
            pairs.len().max(1)
        },
        model: model.map(str::to_string),
        backend: backend.to_string(),
        io_tag: match track {
            "en" => "en".to_string(),
            "ru" => "ru".to_string(),
            "ru_ref" => "ruref".to_string(),
            _ => bail!("unsupported track {track}"),
        },
        prompt_mode: match track {
            "en" => "translate_en".to_string(),
            "ru" => "translate_ru".to_string(),
            "ru_ref" => "ru_refs_b2plus".to_string(),
            _ => bail!("unsupported track {track}"),
        },
        jobs,
        use_context: track != "ru_ref",
    };
    translate(&pairs, &opts)
}

fn process_asset(
    args: &Args,
    http: &HttpClient,
    layout: &TmpLayout,
    out_dir: &Path,
    asset: &SeriesAsset,
) -> Result<PathBuf> {
    let base = base_from_asset(asset);
    let out_mkv = out_dir.join(format!("{base}.mkv"));
    let mp4_path = layout.mp4_file(&base);
    let srt_es = layout.srt_es_file(&base);
    let srt_es_raw = layout.srt_es_asr_raw_file(&base);
    let srt_en = layout.srt_en_file(&base);
    let srt_ru = layout.srt_ru_file(&base);
    let srt_refs = layout.srt_refs_file(&base);
    let srt_dual = layout.srt_bi_full_file(&base);

    let resolved = resolve_asset(&asset.asset_id, http)?;
    let video_url = pick_video_url(&resolved.video_urls, &args.quality)?;
    download_to_mp4(&video_url, &mp4_path)?;
    if !is_valid_mp4(&mp4_path)? {
        bail!("downloaded MP4 is invalid: {}", mp4_path.display());
    }

    let existing_es_before = srt_es.exists() && srt_es.metadata().map(|m| m.len()).unwrap_or(0) > 0;
    let resolved_asr_backend = resolve_asr_backend_name(&args.asr_backend, &args.asr_model);
    if args.asr_backend == "auto" {
        debug(format!("selected ASR backend: {resolved_asr_backend}"));
    }
    let asr_opts = AsrOptions {
        backend: resolved_asr_backend.clone(),
        model: args.asr_model.clone(),
        device: args.asr_device.clone(),
        compute_type: args.asr_compute_type.clone(),
        batch_size: args.asr_batch_size,
        vad_method: args.asr_vad_method.clone(),
    };
    let (mut es_cues, es_title, es_from_asr) =
        if let Some(es_vtt_url) = resolved.subtitles_es_vtt.as_deref() {
            let es_vtt = layout.vtt_es_file(&asset.asset_id);
            download_sub_vtt(http, es_vtt_url, &es_vtt)?;
            (
                parse_vtt(&fs::read_to_string(&es_vtt)?),
                "RTVE".to_string(),
                false,
            )
        } else if existing_es_before {
            (
                crate::subs::parse_srt(&fs::read_to_string(&srt_es)?),
                "RTVE".to_string(),
                false,
            )
        } else if args.asr_if_missing {
            debug(format!(
                "missing Spanish subtitles for asset {}; falling back to ASR",
                asset.asset_id
            ));
            if !(srt_es_raw.exists() && srt_es_raw.metadata().map(|m| m.len()).unwrap_or(0) > 0) {
                transcribe_media_to_srt(&mp4_path, &srt_es_raw, &asr_opts)?;
            } else {
                debug(format!(
                    "reusing cached raw ASR subtitles: {}",
                    srt_es_raw.display()
                ));
            }
            let raw_cues = crate::subs::parse_srt(&fs::read_to_string(&srt_es_raw)?);
            let deduped = deduplicate_asr_hallucinations(&raw_cues);
            (deduped, asr_model_display_name(&asr_opts), true)
        } else {
            bail!("Rust pipeline currently requires RTVE Spanish subtitles for this path")
        };

    let episode_delay_ms = if args.subtitle_delay == "auto" {
        if existing_es_before {
            debug("subtitle delay auto skipped: spa.srt already exists");
            0
        } else if es_from_asr {
            debug("subtitle delay auto skipped: ASR-generated subtitles are already audio-aligned");
            0
        } else {
            let save_asr_srt = args
                .save_auto_delay_asr_dir
                .as_ref()
                .map(|dir| Path::new(dir).join(format!("{base}.auto_delay.{resolved_asr_backend}.srt")));
            let inspection = inspect_episode_delay(
                &es_cues,
                &mp4_path,
                &layout.meta,
                &base,
                15_000,
                &asr_opts,
                true,
                true,
                save_asr_srt.as_deref(),
            )?;
            let estimate = inspection
                .final_estimate
                .context("subtitle auto-delay produced no estimate")?;
            let delay_ms = estimate.delay_ms;
            debug(format!("subtitle delay computed (episode): {delay_ms}ms"));
            debug(format!(
                "subtitle delay details (episode): confidence={:.3} method={} matched={}",
                estimate.confidence, estimate.method, estimate.matched
            ));
            delay_ms
        }
    } else {
        args.subtitle_delay
            .parse::<i64>()
            .context("invalid --subtitle-delay value")?
    };
    es_cues = shift_cues(&es_cues, episode_delay_ms);
    fs::write(&srt_es, cues_to_srt(&es_cues))?;

    let policy = parse_track_policy(&args.sub_modes)?;
    let model = args.selected_model();
    let model_label = model
        .clone()
        .unwrap_or_else(|| args.translation_backend.clone());

    let mut subs = Vec::<ProducedTrack>::new();
    if policy.enabled("es") {
        subs.push(ProducedTrack {
            id: TRACK_ES.to_string(),
            path: srt_es.clone(),
            lang: "spa".to_string(),
            title: es_title.clone(),
        });
    }

    if policy.enabled("en") {
        if let Some(en_vtt_url) = resolved.subtitles_en_vtt.as_deref() {
            let en_vtt = layout.vtt_en_file(&asset.asset_id);
            download_sub_vtt(http, en_vtt_url, &en_vtt)?;
            let en_cues = shift_cues(&parse_vtt(&fs::read_to_string(&en_vtt)?), episode_delay_ms);
            fs::write(&srt_en, cues_to_srt(&en_cues))?;
            subs.push(ProducedTrack {
                id: TRACK_EN.to_string(),
                path: srt_en.clone(),
                lang: "eng".to_string(),
                title: "RTVE".to_string(),
            });
        } else {
            let en_map = translate_track(
                &es_cues,
                &base,
                "en",
                model.as_deref(),
                &args.translation_backend,
                args.jobs_codex_chunks,
                layout,
            )?;
            let en_cues: Vec<Cue> = es_cues
                .iter()
                .enumerate()
                .map(|(i, cue)| Cue {
                    start_ms: cue.start_ms,
                    end_ms: cue.end_ms,
                    text: en_map.get(&i.to_string()).cloned().unwrap_or_default(),
                })
                .collect();
            fs::write(&srt_en, cues_to_srt(&en_cues))?;
            subs.push(ProducedTrack {
                id: TRACK_EN.to_string(),
                path: srt_en.clone(),
                lang: "eng".to_string(),
                title: format!("{model_label} MT"),
            });
        }
    }

    let mut ru_map = BTreeMap::new();
    if policy.enabled("ru") || policy.enabled("ru-dual") {
        ru_map = translate_track(
            &es_cues,
            &base,
            "ru",
            model.as_deref(),
            &args.translation_backend,
            args.jobs_codex_chunks,
            layout,
        )?;
        build_ru_srt(&srt_ru, &es_cues, &ru_map)?;
        if policy.enabled("ru") {
            subs.push(ProducedTrack {
                id: TRACK_RU.to_string(),
                path: srt_ru.clone(),
                lang: "rus".to_string(),
                title: format!("{model_label} MT"),
            });
        }
    }

    if policy.enabled("refs") {
        let refs_map = translate_track(
            &es_cues,
            &base,
            "ru_ref",
            model.as_deref(),
            &args.translation_backend,
            args.jobs_codex_chunks.min(2).max(1),
            layout,
        )?;
        build_refs_srt(&srt_refs, &es_cues, &refs_map)?;
        subs.push(ProducedTrack {
            id: TRACK_REFS.to_string(),
            path: srt_refs.clone(),
            lang: "und".to_string(),
            title: "ES+RU refs".to_string(),
        });
    }

    if policy.enabled("ru-dual") {
        build_ru_dual_srt(&srt_dual, &es_cues, &ru_map, &srt_ru)?;
        subs.push(ProducedTrack {
            id: TRACK_RU_DUAL.to_string(),
            path: srt_dual.clone(),
            lang: "mul".to_string(),
            title: "ES+RU".to_string(),
        });
    }

    let default_title = resolve_default_subtitle_title(&subs, &args.default_subtitle)?;
    let tmp_out = PathBuf::from(format!("{}.partial.mkv", out_mkv.display()));
    let mux_subs: Vec<_> = subs
        .into_iter()
        .map(|t| (t.path, t.lang, t.title))
        .collect();
    mux_mkv(
        &mp4_path,
        &tmp_out,
        &mux_subs,
        Some(default_title.as_str()),
        &args.video_codec,
        &args.hevc_device,
        args.hevc_crf,
        &args.hevc_preset,
    )?;
    fs::rename(tmp_out, &out_mkv)?;
    Ok(out_mkv)
}

pub fn run() -> Result<()> {
    let args = Args::parse();
    set_debug(args.debug);
    let selector = args.selector().context("selector required (T7 or T7S5)")?;
    let series_url = args
        .resolved_series_url()
        .or_else(|| {
            if selector.starts_with('T') {
                Some(DEFAULT_SERIES_URL.to_string())
            } else {
                None
            }
        })
        .context("series_url required (positional arg or RTVE_SERIES_URL env var)")?;
    if args.subtitle_align != "off" {
        bail!("Rust pipeline does not implement subtitle alignment yet");
    }
    let slug = args
        .resolved_series_slug()
        .unwrap_or_else(|| slugify(&series_url));
    let out_dir = PathBuf::from("data").join(&slug);
    let layout = TmpLayout::for_slug(PathBuf::from("tmp").join(&slug));
    layout.ensure_dirs()?;
    fs::create_dir_all(&out_dir)?;
    let http = HttpClient::new()?;
    let assets = list_assets_for_selector(&series_url, &selector, &http, &layout.meta)?;
    let expanded_reset = expand_reset_layers(&normalize_reset_layers(&args.reset_layers)?);
    if !expanded_reset.is_empty() {
        debug(format!(
            "active reset layers: {}",
            expanded_reset
                .iter()
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        ));
        reset_selector_layers(&layout, &out_dir, &assets, &expanded_reset)?;
    }
    for asset in &assets {
        let out = process_asset(&args, &http, &layout, &out_dir, asset)
            .with_context(|| format!("process asset {}", asset.asset_id))?;
        println!("{}", out.display());
    }
    write_index_html(&out_dir)?;
    Ok(())
}
