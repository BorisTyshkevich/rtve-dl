use crate::logging::debug;
use anyhow::{bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn require_tool(name: &str) -> Result<()> {
    let status = Command::new("sh")
        .arg("-lc")
        .arg(format!("command -v {name} >/dev/null"))
        .status()?;
    if !status.success() {
        bail!("{name} not found on PATH");
    }
    Ok(())
}

pub fn require_ffmpeg() -> Result<()> {
    require_tool("ffmpeg")
}

pub fn require_ffprobe() -> Result<()> {
    require_tool("ffprobe")
}

pub fn run_ffmpeg(args: &[String]) -> Result<()> {
    require_ffmpeg()?;
    debug(format!("ffmpeg {}", args.join(" ")));
    let status = Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-nostdin")
        .arg("-loglevel")
        .arg(if crate::logging::is_debug() {
            "warning"
        } else {
            "error"
        })
        .args(args)
        .status()
        .context("run ffmpeg")?;
    if !status.success() {
        bail!("ffmpeg failed");
    }
    Ok(())
}

pub fn ffmpeg_encoders_text() -> Result<String> {
    require_ffmpeg()?;
    let output = Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-encoders")
        .output()
        .context("ffmpeg -encoders")?;
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

pub fn pick_available_hevc_gpu_encoder() -> Result<Option<String>> {
    let encoders = ffmpeg_encoders_text()?;
    for name in [
        "hevc_videotoolbox",
        "hevc_nvenc",
        "hevc_qsv",
        "hevc_amf",
        "hevc_vaapi",
    ] {
        if encoders.contains(name) {
            return Ok(Some(name.to_string()));
        }
    }
    Ok(None)
}

pub fn probe_duration_seconds(path: &Path) -> Result<Option<f64>> {
    if !path.exists() || path.metadata()?.len() == 0 {
        return Ok(None);
    }
    require_ffprobe()?;
    let output = Command::new("ffprobe")
        .arg("-v")
        .arg("error")
        .arg("-show_entries")
        .arg("format=duration")
        .arg("-of")
        .arg("default=nokey=1:noprint_wrappers=1")
        .arg(path)
        .output()
        .context("ffprobe duration")?;
    if !output.status.success() {
        return Ok(None);
    }
    let out = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if out.is_empty() {
        return Ok(None);
    }
    Ok(out.parse::<f64>().ok().filter(|x| *x > 0.0))
}

pub fn is_valid_mp4(path: &Path) -> Result<bool> {
    Ok(probe_duration_seconds(path)?.is_some())
}

pub fn download_to_mp4(input_url: &str, out_mp4: &Path) -> Result<()> {
    out_mp4.parent().unwrap().mkdir_if_missing()?;
    if out_mp4.exists() && is_valid_mp4(out_mp4)? {
        return Ok(());
    }
    let part_mp4 = PathBuf::from(format!("{}.partial.mp4", out_mp4.display()));
    if input_url.contains(".mp4") {
        let status = Command::new("curl")
            .arg("--location")
            .arg("--fail")
            .arg("--silent")
            .arg("--show-error")
            .arg("--continue-at")
            .arg("-")
            .arg("--output")
            .arg(&part_mp4)
            .arg("--user-agent")
            .arg("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")
            .arg(input_url)
            .status();
        if let Ok(status) = status {
            if status.success() && part_mp4.exists() && part_mp4.metadata()?.len() > 0 {
                fs::rename(&part_mp4, out_mp4)?;
                return Ok(());
            }
        }
    }
    let args = vec![
        "-y".to_string(),
        "-i".to_string(),
        input_url.to_string(),
        "-c".to_string(),
        "copy".to_string(),
        part_mp4.display().to_string(),
    ];
    run_ffmpeg(&args)?;
    fs::rename(&part_mp4, out_mp4)?;
    Ok(())
}

pub fn mux_mkv(
    video_path: &Path,
    out_mkv: &Path,
    subs: &[(PathBuf, String, String)],
    default_subtitle_title: Option<&str>,
    video_codec_mode: &str,
    hevc_device: &str,
    hevc_crf: i32,
    hevc_preset: &str,
) -> Result<()> {
    out_mkv.parent().unwrap().mkdir_if_missing()?;
    let mut args = vec![
        "-y".to_string(),
        "-i".to_string(),
        video_path.display().to_string(),
    ];
    for (path, _, _) in subs {
        args.push("-itsoffset".to_string());
        args.push("0.000".to_string());
        args.push("-i".to_string());
        args.push(path.display().to_string());
    }
    args.push("-map".to_string());
    args.push("0".to_string());
    for i in 1..=subs.len() {
        args.push("-map".to_string());
        args.push(i.to_string());
    }
    let default_idx = default_subtitle_title
        .and_then(|wanted| subs.iter().position(|(_, _, title)| title == wanted));
    for (idx, (_, lang, title)) in subs.iter().enumerate() {
        args.push(format!("-metadata:s:s:{idx}"));
        args.push(format!("language={lang}"));
        args.push(format!("-metadata:s:s:{idx}"));
        args.push(format!("title={title}"));
        if Some(idx) != default_idx {
            args.push(format!("-disposition:s:{idx}"));
            args.push("0".to_string());
        }
    }
    if let Some(idx) = default_idx {
        args.push(format!("-disposition:s:{idx}"));
        args.push("default".to_string());
    }

    match video_codec_mode {
        "copy" => {
            args.extend([
                "-c:v".to_string(),
                "copy".to_string(),
                "-c:a".to_string(),
                "copy".to_string(),
                "-c:s".to_string(),
                "srt".to_string(),
                out_mkv.display().to_string(),
            ]);
            run_ffmpeg(&args)
        }
        "hevc" => {
            if hevc_device == "cpu" {
                let mut cpu_args = args.clone();
                cpu_args.extend([
                    "-c:v".to_string(),
                    "libx265".to_string(),
                    "-crf".to_string(),
                    hevc_crf.to_string(),
                    "-preset".to_string(),
                    hevc_preset.to_string(),
                    "-c:a".to_string(),
                    "copy".to_string(),
                    "-c:s".to_string(),
                    "srt".to_string(),
                    out_mkv.display().to_string(),
                ]);
                return run_ffmpeg(&cpu_args);
            }
            if let Some(gpu) = pick_available_hevc_gpu_encoder()? {
                let mut gpu_args = args.clone();
                gpu_args.extend([
                    "-c:v".to_string(),
                    gpu,
                    "-c:a".to_string(),
                    "copy".to_string(),
                    "-c:s".to_string(),
                    "srt".to_string(),
                    out_mkv.display().to_string(),
                ]);
                if run_ffmpeg(&gpu_args).is_ok() {
                    return Ok(());
                }
            }
            let mut cpu_args = args;
            cpu_args.extend([
                "-c:v".to_string(),
                "libx265".to_string(),
                "-crf".to_string(),
                hevc_crf.to_string(),
                "-preset".to_string(),
                hevc_preset.to_string(),
                "-c:a".to_string(),
                "copy".to_string(),
                "-c:s".to_string(),
                "srt".to_string(),
                out_mkv.display().to_string(),
            ]);
            run_ffmpeg(&cpu_args)
        }
        other => bail!("unsupported video codec mode: {other}"),
    }
}

trait DirCreate {
    fn mkdir_if_missing(&self) -> Result<()>;
}

impl DirCreate for Path {
    fn mkdir_if_missing(&self) -> Result<()> {
        fs::create_dir_all(self).context("create directory")
    }
}
