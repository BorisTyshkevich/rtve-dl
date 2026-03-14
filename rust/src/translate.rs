use crate::logging::debug;
use anyhow::{bail, Context, Result};
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone)]
pub struct TranslateOptions {
    pub base_path: PathBuf,
    pub chunk_size: usize,
    pub model: Option<String>,
    pub backend: String,
    pub io_tag: String,
    pub prompt_mode: String,
    pub jobs: usize,
    pub use_context: bool,
}

const EN_PROMPT: &str = include_str!("../../src/rtve_dl/prompts/en_mt.md");
const RU_PROMPT: &str = include_str!("../../src/rtve_dl/prompts/ru_full.md");
const RU_REFS_PROMPT: &str = include_str!("../../src/rtve_dl/prompts/ru_refs.md");

fn prompt_template(prompt_mode: &str) -> Result<&'static str> {
    match prompt_mode {
        "translate_en" => Ok(EN_PROMPT),
        "translate_ru" => Ok(RU_PROMPT),
        "ru_refs_b2plus" => Ok(RU_REFS_PROMPT),
        _ => bail!("unknown prompt mode: {prompt_mode}"),
    }
}

fn tsv_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\t', "\\t")
        .replace('\r', "")
        .replace('\n', "\\n")
}

fn tsv_unescape(value: &str) -> String {
    let mut out = String::new();
    let chars: Vec<char> = value.chars().collect();
    let mut i = 0usize;
    while i < chars.len() {
        if chars[i] != '\\' {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        if i + 1 >= chars.len() {
            out.push('\\');
            break;
        }
        match chars[i + 1] {
            'n' => out.push('\n'),
            't' => out.push('\t'),
            '\\' => out.push('\\'),
            other => out.push(other),
        }
        i += 2;
    }
    out
}

fn normalize_es_text(text: &str) -> String {
    let lower = text.to_lowercase();
    let re = Regex::new(r"[^\p{L}\p{N}\s]+").unwrap();
    let ws_re = Regex::new(r"\s+").unwrap();
    ws_re
        .replace_all(&re.replace_all(&lower, " "), " ")
        .trim()
        .to_string()
}

fn make_echo(text: &str) -> String {
    normalize_es_text(text)
        .chars()
        .take(16)
        .collect::<String>()
        .trim()
        .to_string()
}

fn model_id(cue_id: &str, text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(format!("{cue_id}|{text}").as_bytes());
    let digest = hasher.finalize();
    format!("{:x}", digest)[..8].to_string()
}

fn build_prompt(template: &str, tsv_payload: &str) -> String {
    template
        .replace("{{EPISODE_CONTEXT}}", "")
        .replace("{{PAYLOAD}}", tsv_payload)
}

#[derive(Debug, Clone)]
struct ChunkPaths {
    in_jsonl: PathBuf,
    out_jsonl: PathBuf,
    in_tsv: PathBuf,
    out_tsv: PathBuf,
}

fn chunk_cues(cues: &[(String, String)], opts: &TranslateOptions) -> Result<Vec<ChunkPaths>> {
    let mut chunks = Vec::new();
    let stem = format!("{}.c{}", opts.base_path.display(), opts.chunk_size);
    for i in (0..cues.len()).step_by(opts.chunk_size.max(1)) {
        let part = &cues[i..(i + opts.chunk_size).min(cues.len())];
        let idx = i / opts.chunk_size.max(1) + 1;
        let in_jsonl = PathBuf::from(format!("{stem}.{}.in.{idx:04}.jsonl", opts.io_tag));
        let out_jsonl = PathBuf::from(format!("{stem}.{}.out.{idx:04}.jsonl", opts.io_tag));
        let in_tsv = PathBuf::from(format!("{stem}.{}.in.{idx:04}.tsv", opts.io_tag));
        let out_tsv = PathBuf::from(format!("{stem}.{}.out.{idx:04}.tsv", opts.io_tag));
        fs::create_dir_all(in_jsonl.parent().unwrap())?;
        let mut jsonl = String::new();
        let mut tsv = String::new();
        for (j, (cue_id, text)) in part.iter().enumerate() {
            let mid = model_id(cue_id, text);
            let echo = make_echo(text);
            jsonl.push_str(&serde_json::json!({"id": cue_id, "text": text}).to_string());
            jsonl.push('\n');
            if opts.use_context {
                let global_idx = i + j;
                let left = if global_idx > 0 {
                    &cues[global_idx - 1].1
                } else {
                    ""
                };
                let right = if global_idx + 1 < cues.len() {
                    &cues[global_idx + 1].1
                } else {
                    ""
                };
                tsv.push_str(&format!(
                    "{}\t{}\t{}\t{}\t{}\n",
                    tsv_escape(&mid),
                    tsv_escape(text),
                    tsv_escape(left),
                    tsv_escape(right),
                    tsv_escape(&echo)
                ));
            } else {
                tsv.push_str(&format!(
                    "{}\t{}\t{}\n",
                    tsv_escape(&mid),
                    tsv_escape(text),
                    tsv_escape(&echo)
                ));
            }
        }
        fs::write(&in_jsonl, jsonl)?;
        fs::write(&in_tsv, tsv)?;
        chunks.push(ChunkPaths {
            in_jsonl,
            out_jsonl,
            in_tsv,
            out_tsv,
        });
    }
    Ok(chunks)
}

fn expected_map(chunk: &ChunkPaths) -> Result<BTreeMap<String, (String, String)>> {
    let mut expected = BTreeMap::new();
    for line in fs::read_to_string(&chunk.in_jsonl)?.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let obj: serde_json::Value = serde_json::from_str(line)?;
        let cue_id = obj
            .get("id")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let text = obj
            .get("text")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        expected.insert(model_id(&cue_id, &text), (cue_id, make_echo(&text)));
    }
    Ok(expected)
}

fn parse_tsv_with_echo(
    path: &Path,
    expected: &BTreeMap<String, (String, String)>,
) -> Result<BTreeMap<String, String>> {
    let mut out = BTreeMap::new();
    let mut pending = String::new();

    let flush_row = |row: &str, out: &mut BTreeMap<String, String>| {
        let parts: Vec<&str> = row.split('\t').collect();
        if parts.len() < 3 {
            return false;
        }
        let model_id = tsv_unescape(parts[0]).trim().to_string();
        let echo = tsv_unescape(parts.last().copied().unwrap_or(""))
            .trim()
            .to_string();
        let text = tsv_unescape(&parts[1..parts.len() - 1].join("\t"))
            .trim()
            .to_string();
        if let Some((_cue_id, expected_echo)) = expected.get(&model_id) {
            if &echo == expected_echo || echo == model_id {
                out.insert(model_id, text);
                return true;
            }
        }
        false
    };

    for line in fs::read_to_string(path)?.lines() {
        let row = line.trim_end_matches('\r');
        if row.trim().is_empty() {
            continue;
        }
        if flush_row(row, &mut out) {
            pending.clear();
            continue;
        }
        let candidate = if pending.is_empty() {
            row.to_string()
        } else {
            format!("{pending}\n{row}")
        };
        if flush_row(&candidate, &mut out) {
            pending.clear();
            continue;
        }
        pending = candidate;
    }
    Ok(out)
}

fn write_jsonl_map(path: &Path, mapping: &BTreeMap<String, String>) -> Result<()> {
    let mut out = String::new();
    for (cue_id, text) in mapping {
        out.push_str(&serde_json::json!({"id": cue_id, "text": text}).to_string());
        out.push('\n');
    }
    fs::write(path, out)?;
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct BackendCommandSpec {
    program: &'static str,
    args: Vec<String>,
    writes_stdout_to_tsv: bool,
}

fn backend_command_spec(opts: &TranslateOptions, chunk: &ChunkPaths) -> Result<BackendCommandSpec> {
    match opts.backend.as_str() {
        "claude" => {
            let mut args = vec![
                "-p".to_string(),
                "--print".to_string(),
                "--setting-sources".to_string(),
                "user".to_string(),
            ];
            if let Some(model) = opts.model.as_deref() {
                args.push("--model".to_string());
                args.push(model.to_string());
            }
            Ok(BackendCommandSpec {
                program: "claude",
                args,
                writes_stdout_to_tsv: true,
            })
        }
        "codex" => {
            let model = opts
                .model
                .as_deref()
                .context("codex backend requires a model")?;
            Ok(BackendCommandSpec {
                program: "codex",
                args: vec![
                    "exec".to_string(),
                    "-s".to_string(),
                    "read-only".to_string(),
                    "--output-last-message".to_string(),
                    chunk.out_tsv.display().to_string(),
                    "-m".to_string(),
                    model.to_string(),
                    "-".to_string(),
                ],
                writes_stdout_to_tsv: false,
            })
        }
        "gemini" => {
            let mut args = vec![
                "-p".to_string(),
                "".to_string(),
                "--output-format".to_string(),
                "text".to_string(),
                "--allowed-mcp-server-names".to_string(),
                "".to_string(),
            ];
            if let Some(model) = opts.model.as_deref() {
                args.push("--model".to_string());
                args.push(model.to_string());
            }
            Ok(BackendCommandSpec {
                program: "gemini",
                args,
                writes_stdout_to_tsv: true,
            })
        }
        other => bail!("unsupported translation backend: {other}"),
    }
}

fn run_chunk(chunk: &ChunkPaths, opts: &TranslateOptions) -> Result<()> {
    let payload = fs::read_to_string(&chunk.in_tsv)?;
    let prompt = build_prompt(prompt_template(&opts.prompt_mode)?, &payload);
    let spec = backend_command_spec(opts, chunk)?;
    debug(format!(
        "{} chunk {}",
        spec.program,
        chunk.out_jsonl.display()
    ));
    let mut command = Command::new(spec.program);
    command
        .args(&spec.args)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit());
    let output = command
        .spawn()
        .with_context(|| format!("spawn {}", spec.program))?;

    let mut child = output;
    use std::io::Write;
    {
        let stdin = child.stdin.as_mut().context("open child stdin")?;
        stdin.write_all(prompt.as_bytes())?;
    }
    let _ = child.stdin.take();
    let out = child.wait_with_output()?;
    if spec.writes_stdout_to_tsv && out.status.success() {
        fs::write(&chunk.out_tsv, &out.stdout)?;
    }
    if !out.status.success() {
        let log_path = PathBuf::from(format!("{}.log", chunk.out_jsonl.display()));
        fs::write(&log_path, &out.stdout)?;
        bail!(
            "{} failed for chunk {}; see {}",
            opts.backend,
            chunk.out_jsonl.display(),
            log_path.display()
        );
    }
    let expected = expected_map(chunk)?;
    let parsed = parse_tsv_with_echo(&chunk.out_tsv, &expected)?;
    if parsed.is_empty() {
        bail!(
            "{} returned empty/unparseable output for {}",
            opts.backend,
            chunk.out_tsv.display()
        );
    }
    let mut remapped = BTreeMap::new();
    for (mid, text) in parsed {
        if let Some((cue_id, _)) = expected.get(&mid) {
            remapped.insert(cue_id.clone(), text);
        }
    }
    write_jsonl_map(&chunk.out_jsonl, &remapped)?;
    Ok(())
}

pub fn translate(
    cues: &[(String, String)],
    opts: &TranslateOptions,
) -> Result<BTreeMap<String, String>> {
    if cues.is_empty() {
        return Ok(BTreeMap::new());
    }
    let mut merged = BTreeMap::new();
    let mut remaining: Vec<(String, String)> = cues.to_vec();
    let mut chunk_sizes = vec![opts.chunk_size];
    for sz in [opts.chunk_size.min(50), 10, 1] {
        if !chunk_sizes.contains(&sz) {
            chunk_sizes.push(sz);
        }
    }

    for (attempt, chunk_size) in chunk_sizes.into_iter().enumerate() {
        if remaining.is_empty() {
            break;
        }
        let retry_opts = TranslateOptions {
            base_path: if attempt == 0 {
                opts.base_path.clone()
            } else {
                PathBuf::from(format!("{}.retry{}", opts.base_path.display(), attempt))
            },
            chunk_size,
            model: opts.model.clone(),
            backend: opts.backend.clone(),
            io_tag: opts.io_tag.clone(),
            prompt_mode: opts.prompt_mode.clone(),
            jobs: opts.jobs,
            use_context: opts.use_context,
        };
        let chunks = chunk_cues(&remaining, &retry_opts)?;
        for chunk in &chunks {
            let _ = fs::remove_file(&chunk.out_jsonl);
            let _ = fs::remove_file(&chunk.out_tsv);
            let _ = fs::remove_file(PathBuf::from(format!("{}.log", chunk.out_jsonl.display())));
        }
        for chunk in &chunks {
            run_chunk(chunk, &retry_opts)?;
        }
        for chunk in &chunks {
            for line in fs::read_to_string(&chunk.out_jsonl)?.lines() {
                if line.trim().is_empty() {
                    continue;
                }
                let obj: serde_json::Value = serde_json::from_str(line)?;
                let cue_id = obj
                    .get("id")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string();
                let text = obj
                    .get("text")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string();
                merged.insert(cue_id, text);
            }
        }
        remaining = remaining
            .into_iter()
            .filter(|(cue_id, _)| !merged.contains_key(cue_id))
            .collect();
    }

    let missing: Vec<_> = cues
        .iter()
        .filter(|(cue_id, _)| !merged.contains_key(cue_id))
        .map(|(id, _)| id.clone())
        .collect();
    if !missing.is_empty() {
        bail!(
            "translation output missing {} ids (example: {:?})",
            missing.len(),
            missing.iter().take(5).collect::<Vec<_>>()
        );
    }
    Ok(merged)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn test_chunk(dir: &Path) -> ChunkPaths {
        ChunkPaths {
            in_jsonl: dir.join("in.jsonl"),
            out_jsonl: dir.join("out.jsonl"),
            in_tsv: dir.join("in.tsv"),
            out_tsv: dir.join("out.tsv"),
        }
    }

    #[test]
    fn parse_tsv_with_echo_skips_noise_and_accepts_multiline_rows() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("out.tsv");
        let cue_id = "0001".to_string();
        let source = "Hola,\nqué tal?".to_string();
        let mid = model_id(&cue_id, &source);
        let echo = make_echo(&source);
        let translated = "Hi,\nhow are you?";
        fs::write(
            &path,
            format!(
                "Here is the translated TSV output.\n{mid}\t{}\t{echo}\n",
                translated.replace('\n', "\\n")
            ),
        )
        .unwrap();

        let mut expected = BTreeMap::new();
        expected.insert(mid.clone(), (cue_id, echo));

        let parsed = parse_tsv_with_echo(&path, &expected).unwrap();
        assert_eq!(parsed.get(&mid).map(String::as_str), Some(translated));
    }

    #[test]
    fn parse_tsv_with_echo_rejects_wrong_echo() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("out.tsv");
        let cue_id = "0001".to_string();
        let source = "Buenos dias".to_string();
        let mid = model_id(&cue_id, &source);
        fs::write(&path, format!("{mid}\tGood morning\twrong echo\n")).unwrap();

        let mut expected = BTreeMap::new();
        expected.insert(mid, (cue_id, make_echo(&source)));

        let parsed = parse_tsv_with_echo(&path, &expected).unwrap();
        assert!(parsed.is_empty());
    }

    #[test]
    fn backend_command_spec_builds_gemini_command() {
        let dir = tempdir().unwrap();
        let chunk = test_chunk(dir.path());
        let opts = TranslateOptions {
            base_path: dir.path().join("base"),
            chunk_size: 10,
            model: Some("gemini-2.5-pro".to_string()),
            backend: "gemini".to_string(),
            io_tag: "ru".to_string(),
            prompt_mode: "translate_ru".to_string(),
            jobs: 1,
            use_context: true,
        };

        let spec = backend_command_spec(&opts, &chunk).unwrap();
        assert_eq!(spec.program, "gemini");
        assert!(spec.writes_stdout_to_tsv);
        assert_eq!(
            spec.args,
            vec![
                "-p".to_string(),
                "".to_string(),
                "--output-format".to_string(),
                "text".to_string(),
                "--allowed-mcp-server-names".to_string(),
                "".to_string(),
                "--model".to_string(),
                "gemini-2.5-pro".to_string(),
            ]
        );
    }

    #[test]
    fn backend_command_spec_allows_gemini_default_model() {
        let dir = tempdir().unwrap();
        let chunk = test_chunk(dir.path());
        let opts = TranslateOptions {
            base_path: dir.path().join("base"),
            chunk_size: 10,
            model: None,
            backend: "gemini".to_string(),
            io_tag: "ru".to_string(),
            prompt_mode: "translate_ru".to_string(),
            jobs: 1,
            use_context: true,
        };

        let spec = backend_command_spec(&opts, &chunk).unwrap();
        assert_eq!(
            spec.args,
            vec![
                "-p".to_string(),
                "".to_string(),
                "--output-format".to_string(),
                "text".to_string(),
                "--allowed-mcp-server-names".to_string(),
                "".to_string(),
            ]
        );
    }

    #[test]
    fn backend_command_spec_rejects_unknown_backend() {
        let dir = tempdir().unwrap();
        let chunk = test_chunk(dir.path());
        let opts = TranslateOptions {
            base_path: dir.path().join("base"),
            chunk_size: 10,
            model: None,
            backend: "unknown".to_string(),
            io_tag: "ru".to_string(),
            prompt_mode: "translate_ru".to_string(),
            jobs: 1,
            use_context: true,
        };

        let err = backend_command_spec(&opts, &chunk).unwrap_err();
        assert!(err.to_string().contains("unsupported translation backend"));
    }
}
