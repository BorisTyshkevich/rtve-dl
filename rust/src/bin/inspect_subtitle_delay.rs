use anyhow::{Context, Result};
use clap::Parser;
use rtve_dl_rust::asr::AsrOptions;
use rtve_dl_rust::delay::{inspect_episode_delay, DelayEstimate};
use rtve_dl_rust::logging::set_debug;
use rtve_dl_rust::subs::{parse_srt, parse_vtt};
use std::fs;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "inspect-subtitle-delay", about = "Inspect Rust subtitle auto-delay for one local MP4 + SRT pair")]
struct Args {
    #[arg(long)]
    mp4: PathBuf,

    #[arg(long)]
    srt: Option<PathBuf>,

    #[arg(long)]
    vtt: Option<PathBuf>,

    #[arg(long, default_value = "tmp/subtitle-delay-inspect-rust")]
    tmp_dir: PathBuf,

    #[arg(long)]
    base: Option<String>,

    #[arg(long, default_value_t = 15_000)]
    max_ms: i64,

    #[arg(long, default_value = "auto")]
    asr_backend: String,

    #[arg(long, default_value = "small")]
    asr_model: String,

    #[arg(long, default_value = "cpu")]
    asr_device: String,

    #[arg(long, default_value = "int8")]
    asr_compute_type: String,

    #[arg(long, default_value_t = 8)]
    asr_batch_size: usize,

    #[arg(long, default_value = "silero")]
    asr_vad_method: String,

    #[arg(long)]
    skip_energy: bool,

    #[arg(long)]
    skip_asr: bool,

    #[arg(long)]
    save_asr_srt: Option<PathBuf>,

    #[arg(short = 'd', long)]
    debug: bool,
}

fn format_estimate(label: &str, est: Option<DelayEstimate>) -> String {
    match est {
        Some(est) => format!(
            "{label}: delay_ms={} confidence={:.3} method={} matched={}",
            est.delay_ms, est.confidence, est.method, est.matched
        ),
        None => format!("{label}: none"),
    }
}

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args = Args::parse();
    set_debug(args.debug);

    if !args.mp4.exists() {
        anyhow::bail!("missing mp4 file: {}", args.mp4.display());
    }
    if args.srt.is_none() && args.vtt.is_none() {
        anyhow::bail!("one of --vtt or --srt is required");
    }
    let (cues, source_label) = if let Some(vtt) = &args.vtt {
        if !vtt.exists() {
            anyhow::bail!("missing vtt file: {}", vtt.display());
        }
        (
            parse_vtt(&fs::read_to_string(vtt).context("read vtt file")?),
            format!("vtt: {}", vtt.display()),
        )
    } else {
        let srt = args.srt.as_ref().context("missing srt argument")?;
        if !srt.exists() {
            anyhow::bail!("missing srt file: {}", srt.display());
        }
        (
            parse_srt(&fs::read_to_string(srt).context("read srt file")?),
            format!("srt: {}", srt.display()),
        )
    };
    if cues.is_empty() {
        anyhow::bail!("no cues found in {source_label}");
    }

    let base = args
        .base
        .clone()
        .unwrap_or_else(|| args.mp4.file_stem().and_then(|s| s.to_str()).unwrap_or("episode").to_string());
    let tmp_dir = args.tmp_dir;
    fs::create_dir_all(&tmp_dir).context("create temp dir")?;

    let asr_opts = AsrOptions {
        backend: args.asr_backend,
        model: args.asr_model,
        device: args.asr_device,
        compute_type: args.asr_compute_type,
        batch_size: args.asr_batch_size,
        vad_method: args.asr_vad_method,
    };

    let inspection = inspect_episode_delay(
        &cues,
        &args.mp4,
        &tmp_dir,
        &base,
        args.max_ms.max(1),
        &asr_opts,
        !args.skip_energy,
        !args.skip_asr,
        args.save_asr_srt.as_deref(),
    )?;

    println!("mp4: {}", args.mp4.display());
    println!("{source_label}");
    println!("cues: {}", cues.len());
    println!("{}", format_estimate("energy", inspection.energy));
    println!("{}", format_estimate("asr", inspection.asr));
    println!("{}", format_estimate("final", inspection.final_estimate));
    Ok(())
}
