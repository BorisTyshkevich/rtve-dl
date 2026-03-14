use crate::subs::{cues_to_srt, parse_srt, Cue};
use anyhow::{bail, Result};
use regex::Regex;
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

pub const TRACK_ES: &str = "es";
pub const TRACK_EN: &str = "en";
pub const TRACK_RU: &str = "ru";
pub const TRACK_REFS: &str = "refs";
pub const TRACK_RU_DUAL: &str = "ru_dual";

#[derive(Debug, Clone)]
pub struct ProducedTrack {
    pub id: String,
    pub path: PathBuf,
    pub lang: String,
    pub title: String,
}

#[derive(Debug, Clone)]
pub struct TrackPolicy {
    pub modes: BTreeMap<String, String>,
}

impl TrackPolicy {
    pub fn mode(&self, track: &str) -> &str {
        self.modes.get(track).map(|x| x.as_str()).unwrap_or("off")
    }

    pub fn enabled(&self, track: &str) -> bool {
        self.mode(track) != "off"
    }
}

pub fn parse_track_policy(entries: &[String]) -> Result<TrackPolicy> {
    let mut modes = BTreeMap::from([
        ("es".to_string(), "on".to_string()),
        ("en".to_string(), "on".to_string()),
        ("ru".to_string(), "require".to_string()),
        ("ru-dual".to_string(), "on".to_string()),
        ("refs".to_string(), "on".to_string()),
    ]);
    let tracks = HashSet::from(["es", "en", "ru", "ru-dual", "refs"]);
    let modes_allowed = HashSet::from(["off", "on", "require"]);
    for raw in entries {
        let Some((track_raw, mode_raw)) = raw.split_once('=') else {
            bail!("invalid --sub value: {raw:?}. Expected <track>=<off|on|require>.");
        };
        let track = track_raw.trim().to_lowercase();
        let mode = mode_raw.trim().to_lowercase();
        if !tracks.contains(track.as_str()) {
            bail!("invalid --sub track: {track:?}");
        }
        if !modes_allowed.contains(mode.as_str()) {
            bail!("invalid --sub mode for {track:?}: {mode:?}");
        }
        modes.insert(track, mode);
    }
    if modes.get("ru-dual").map(|x| x.as_str()) != Some("off")
        && modes.get("ru").map(|x| x.as_str()) == Some("off")
    {
        modes.insert("ru".to_string(), "on".to_string());
    }
    Ok(TrackPolicy { modes })
}

pub fn resolve_default_subtitle_title(subs: &[ProducedTrack], requested: &str) -> Result<String> {
    let wanted = match requested {
        "es" => [TRACK_ES],
        "en" => [TRACK_EN],
        "ru" => [TRACK_RU],
        "refs" => [TRACK_REFS],
        "ru-dual" => [TRACK_RU_DUAL],
        _ => bail!("invalid default subtitle: {requested}"),
    };
    for sub in subs {
        if wanted.contains(&sub.id.as_str()) {
            return Ok(sub.title.clone());
        }
    }
    bail!("default subtitle '{requested}' is not available in produced tracks")
}

fn normalize_refs_candidate(raw: &str) -> String {
    let compact = raw.trim().replace('\t', " ");
    let ws_re = Regex::new(r"\s+").unwrap();
    ws_re.replace_all(&compact, " ").trim().to_string()
}

fn spanish_tokens(s: &str) -> HashSet<String> {
    let re = Regex::new(r"[a-záéíóúñü]+").unwrap();
    re.find_iter(&s.to_lowercase())
        .map(|m| m.as_str().to_string())
        .collect()
}

fn looks_like_inline_annotated_spanish(es_text: &str, candidate: &str) -> bool {
    if candidate.is_empty() {
        return false;
    }
    if candidate.contains(';') && !candidate.contains('(') && !candidate.contains(')') {
        return false;
    }
    let es_tokens = spanish_tokens(es_text);
    let out_tokens = spanish_tokens(candidate);
    let overlap = es_tokens.intersection(&out_tokens).count();
    let min_overlap = if es_tokens.len() <= 3 { 1 } else { 2 };
    if overlap < min_overlap {
        return false;
    }
    if (candidate.contains('(') || candidate.contains(')'))
        && !Regex::new(r"\([^\)]*[А-Яа-яЁё][^\)]*\)")
            .unwrap()
            .is_match(candidate)
    {
        return false;
    }
    true
}

pub fn compose_ref_text(es_text: &str, ru_refs: &str) -> String {
    let candidate = normalize_refs_candidate(ru_refs);
    if candidate.is_empty() {
        return es_text.trim().to_string();
    }
    if looks_like_inline_annotated_spanish(es_text, &candidate) {
        candidate
    } else {
        es_text.trim().to_string()
    }
}

pub fn build_ru_srt(path: &Path, cues: &[Cue], ru_map: &BTreeMap<String, String>) -> Result<()> {
    let ru_cues: Vec<Cue> = cues
        .iter()
        .enumerate()
        .map(|(i, cue)| Cue {
            start_ms: cue.start_ms,
            end_ms: cue.end_ms,
            text: ru_map.get(&i.to_string()).cloned().unwrap_or_default(),
        })
        .collect();
    fs::write(path, cues_to_srt(&ru_cues))?;
    Ok(())
}

pub fn build_refs_srt(
    path: &Path,
    cues: &[Cue],
    refs_map: &BTreeMap<String, String>,
) -> Result<()> {
    let ref_cues: Vec<Cue> = cues
        .iter()
        .enumerate()
        .map(|(i, cue)| Cue {
            start_ms: cue.start_ms,
            end_ms: cue.end_ms,
            text: compose_ref_text(
                &cue.text,
                refs_map
                    .get(&i.to_string())
                    .map(String::as_str)
                    .unwrap_or(""),
            ),
        })
        .collect();
    fs::write(path, cues_to_srt(&ref_cues))?;
    Ok(())
}

pub fn build_ru_dual_srt(
    path: &Path,
    cues: &[Cue],
    ru_map: &BTreeMap<String, String>,
    ru_fallback: &Path,
) -> Result<()> {
    let map_local: BTreeMap<String, String> = if ru_map.is_empty() {
        parse_srt(&fs::read_to_string(ru_fallback)?)
            .into_iter()
            .enumerate()
            .map(|(i, cue)| (i.to_string(), cue.text))
            .collect()
    } else {
        ru_map.clone()
    };
    let dual_cues: Vec<Cue> = cues
        .iter()
        .enumerate()
        .map(|(i, cue)| Cue {
            start_ms: cue.start_ms,
            end_ms: cue.end_ms,
            text: format!(
                "{}\n{}",
                cue.text.trim(),
                map_local
                    .get(&i.to_string())
                    .map(String::as_str)
                    .unwrap_or("")
            )
            .trim()
            .to_string(),
        })
        .collect();
    fs::write(path, cues_to_srt(&dual_cues))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn track_policy_defaults() {
        let policy = parse_track_policy(&[]).unwrap();
        assert_eq!(policy.mode("ru"), "require");
        assert_eq!(policy.mode("es"), "on");
    }

    #[test]
    fn ru_dual_promotes_ru() {
        let policy = parse_track_policy(&["ru=off".into(), "ru-dual=on".into()]).unwrap();
        assert_eq!(policy.mode("ru"), "on");
    }
}
