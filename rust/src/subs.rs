use regex::Regex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cue {
    pub start_ms: i64,
    pub end_ms: i64,
    pub text: String,
}

fn parse_vtt_ts(ts: &str) -> Option<i64> {
    let parts: Vec<&str> = ts.split(':').collect();
    match parts.len() {
        3 => {
            let hh: i64 = parts[0].parse().ok()?;
            let mm: i64 = parts[1].parse().ok()?;
            let sm: Vec<&str> = parts[2].split('.').collect();
            if sm.len() != 2 {
                return None;
            }
            let ss: i64 = sm[0].parse().ok()?;
            let ms: i64 = sm[1].parse().ok()?;
            Some(((hh * 60 + mm) * 60 + ss) * 1000 + ms)
        }
        2 => {
            let mm: i64 = parts[0].parse().ok()?;
            let sm: Vec<&str> = parts[1].split('.').collect();
            if sm.len() != 2 {
                return None;
            }
            let ss: i64 = sm[0].parse().ok()?;
            let ms: i64 = sm[1].parse().ok()?;
            Some((mm * 60 + ss) * 1000 + ms)
        }
        _ => None,
    }
}

fn parse_srt_ts(ts: &str) -> Option<i64> {
    let parts: Vec<&str> = ts.split(':').collect();
    if parts.len() != 3 {
        return None;
    }
    let hh: i64 = parts[0].parse().ok()?;
    let mm: i64 = parts[1].parse().ok()?;
    let sm: Vec<&str> = parts[2].split(',').collect();
    if sm.len() != 2 {
        return None;
    }
    let ss: i64 = sm[0].parse().ok()?;
    let ms: i64 = sm[1].parse().ok()?;
    Some(((hh * 60 + mm) * 60 + ss) * 1000 + ms)
}

fn strip_tags(s: &str) -> String {
    let tag_re = Regex::new(r"</?[^>]+>").unwrap();
    tag_re
        .replace_all(s, "")
        .replace("&nbsp;", " ")
        .trim()
        .to_string()
}

pub fn parse_vtt(vtt_text: &str) -> Vec<Cue> {
    let ts_re = Regex::new(
        r"^(?P<s>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})\s+-->\s+(?P<e>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})",
    )
    .unwrap();
    let normalized = vtt_text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = normalized.split('\n').collect();
    let mut cues = Vec::new();
    let mut i = 0usize;

    while i < lines.len() && lines[i].trim().is_empty() {
        i += 1;
    }
    if i < lines.len() && lines[i].trim().starts_with("WEBVTT") {
        i += 1;
    }

    while i < lines.len() {
        while i < lines.len() && lines[i].trim().is_empty() {
            i += 1;
        }
        if i >= lines.len() {
            break;
        }
        if i + 1 < lines.len() && ts_re.is_match(lines[i + 1].trim()) {
            i += 1;
        }
        let Some(caps) = ts_re.captures(lines[i].trim()) else {
            while i < lines.len() && !lines[i].trim().is_empty() {
                i += 1;
            }
            continue;
        };
        let start_ms = parse_vtt_ts(&caps["s"]).unwrap_or(0);
        let end_ms = parse_vtt_ts(&caps["e"]).unwrap_or(start_ms + 1);
        i += 1;
        let mut text_lines = Vec::new();
        while i < lines.len() && !lines[i].trim().is_empty() {
            text_lines.push(lines[i]);
            i += 1;
        }
        cues.push(Cue {
            start_ms,
            end_ms,
            text: strip_tags(&text_lines.join("\n")),
        });
    }

    cues
}

pub fn parse_srt(srt_text: &str) -> Vec<Cue> {
    let ts_re =
        Regex::new(r"^(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<e>\d{2}:\d{2}:\d{2},\d{3})")
            .unwrap();
    let normalized = srt_text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = normalized.split('\n').collect();
    let mut cues = Vec::new();
    let mut i = 0usize;
    while i < lines.len() {
        while i < lines.len() && lines[i].trim().is_empty() {
            i += 1;
        }
        if i >= lines.len() {
            break;
        }
        if i + 1 < lines.len()
            && lines[i].trim().chars().all(|c| c.is_ascii_digit())
            && ts_re.is_match(lines[i + 1].trim())
        {
            i += 1;
        }
        let Some(caps) = ts_re.captures(lines[i].trim()) else {
            while i < lines.len() && !lines[i].trim().is_empty() {
                i += 1;
            }
            continue;
        };
        let start_ms = parse_srt_ts(&caps["s"]).unwrap_or(0);
        let end_ms = parse_srt_ts(&caps["e"]).unwrap_or(start_ms + 1);
        i += 1;
        let mut text_lines = Vec::new();
        while i < lines.len() && !lines[i].trim().is_empty() {
            text_lines.push(lines[i]);
            i += 1;
        }
        cues.push(Cue {
            start_ms,
            end_ms,
            text: text_lines.join("\n").trim().to_string(),
        });
    }
    cues
}

fn fmt_ms(mut ms: i64) -> String {
    if ms < 0 {
        ms = 0;
    }
    let hh = ms / 3_600_000;
    ms -= hh * 3_600_000;
    let mm = ms / 60_000;
    ms -= mm * 60_000;
    let ss = ms / 1000;
    ms -= ss * 1000;
    format!("{hh:02}:{mm:02}:{ss:02},{ms:03}")
}

pub fn cues_to_srt(cues: &[Cue]) -> String {
    let mut out = String::new();
    for (idx, cue) in cues.iter().enumerate() {
        out.push_str(&(idx + 1).to_string());
        out.push('\n');
        out.push_str(&fmt_ms(cue.start_ms));
        out.push_str(" --> ");
        out.push_str(&fmt_ms(cue.end_ms));
        out.push('\n');
        out.push_str(&cue.text);
        out.push_str("\n\n");
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_vtt_basic() {
        let input = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n\n";
        let cues = parse_vtt(input);
        assert_eq!(cues.len(), 1);
        assert_eq!(cues[0].text, "Hola");
        assert_eq!(cues[0].start_ms, 1000);
    }

    #[test]
    fn srt_roundtrip() {
        let cues = vec![Cue {
            start_ms: 1000,
            end_ms: 2000,
            text: "hola".to_string(),
        }];
        let rendered = cues_to_srt(&cues);
        let parsed = parse_srt(&rendered);
        assert_eq!(parsed, cues);
    }
}
