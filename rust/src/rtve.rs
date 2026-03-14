use crate::http::HttpClient;
use crate::logging::debug;
use anyhow::{bail, Context, Result};
use base64::Engine as _;
use regex::Regex;
use serde::Deserialize;
use serde_json::Value;
use sha1::{Digest as _, Sha1};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const CATALOG_CACHE_TTL_S: u64 = 7 * 24 * 60 * 60;

#[derive(Debug, Clone)]
pub struct SeriesAsset {
    pub asset_id: String,
    pub episode_url: Option<String>,
    pub title: Option<String>,
    pub short_description: Option<String>,
    pub description: Option<String>,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub has_drm: bool,
}

#[derive(Debug, Clone)]
pub struct ResolvedAsset {
    pub asset_id: String,
    pub title: Option<String>,
    pub video_urls: Vec<String>,
    pub subtitles_es_vtt: Option<String>,
    pub subtitles_en_vtt: Option<String>,
}

#[derive(Deserialize)]
struct PageItems {
    page: Option<Page>,
}

#[derive(Deserialize)]
struct Page {
    items: Option<Vec<Value>>,
    #[serde(rename = "totalPages")]
    total_pages: Option<i32>,
}

pub fn parse_selector(selector: &str) -> Result<(i32, Option<i32>)> {
    let re = Regex::new(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$").unwrap();
    let caps = re
        .captures(selector.trim())
        .context("selector must look like T7 or T7S5")?;
    let season = caps["t"].parse()?;
    let episode = caps.name("s").map(|m| m.as_str().parse()).transpose()?;
    Ok((season, episode))
}

fn clean_text(s: Option<&str>) -> Option<String> {
    let s = s?.trim();
    if s.is_empty() {
        return None;
    }
    let tag_re = Regex::new(r"<[^>]+>").unwrap();
    let ws_re = Regex::new(r"\s+").unwrap();
    Some(
        ws_re
            .replace_all(&tag_re.replace_all(s, " "), " ")
            .trim()
            .to_string(),
    )
}

fn catalog_cache_path(series_url: &str, cache_dir: &Path) -> PathBuf {
    let mut hasher = Sha1::new();
    hasher.update(series_url.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    cache_dir.join(format!("catalog_{}.json", &digest[..16]))
}

fn read_catalog_cache(path: &Path) -> Result<Option<Vec<Value>>> {
    if !path.exists() {
        return Ok(None);
    }
    let obj: Value = serde_json::from_str(&fs::read_to_string(path)?)?;
    let fetched_at = obj.get("fetched_at").and_then(Value::as_u64).unwrap_or(0);
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    if now.saturating_sub(fetched_at) > CATALOG_CACHE_TTL_S {
        return Ok(None);
    }
    let items = obj
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    Ok(Some(items))
}

fn write_catalog_cache(
    path: &Path,
    series_url: &str,
    program_id: &str,
    items: &[Value],
) -> Result<()> {
    fs::create_dir_all(path.parent().unwrap())?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let payload = serde_json::json!({
        "series_url": series_url,
        "program_id": program_id,
        "fetched_at": now,
        "items": items,
    });
    fs::write(path, serde_json::to_string(&payload)?)?;
    Ok(())
}

fn extract_program_id_from_html(html: &str) -> Option<String> {
    let re = Regex::new(r"/api/programas/(\d+)/").unwrap();
    re.captures(html).map(|c| c[1].to_string())
}

fn iter_program_videos(program_id: &str, http: &HttpClient) -> Result<Vec<Value>> {
    let mut page = 1;
    let mut items = Vec::new();
    loop {
        let url = format!(
            "https://www.rtve.es/api/programas/{program_id}/videos.json?size=60&page={page}"
        );
        let data: PageItems = http.get_json(&url)?;
        if let Some(page_data) = data.page {
            if let Some(mut new_items) = page_data.items {
                items.append(&mut new_items);
            }
            let total_pages = page_data.total_pages.unwrap_or(1);
            if page >= total_pages {
                break;
            }
            page += 1;
        } else {
            break;
        }
    }
    Ok(items)
}

pub fn list_assets_for_selector(
    series_url: &str,
    selector: &str,
    http: &HttpClient,
    cache_dir: &Path,
) -> Result<Vec<SeriesAsset>> {
    let (season, episode) = parse_selector(selector)?;
    let cache_path = catalog_cache_path(series_url, cache_dir);
    let items = if let Some(items) = read_catalog_cache(&cache_path)? {
        items
    } else {
        let html = http.get_text(series_url)?;
        let program_id = extract_program_id_from_html(&html)
            .context("could not find program id on series page")?;
        let items = iter_program_videos(&program_id, http)?;
        write_catalog_cache(&cache_path, series_url, &program_id, &items)?;
        items
    };

    let mut assets = Vec::new();
    for item in items {
        let Some(obj) = item.as_object() else {
            continue;
        };
        if obj
            .get("type")
            .and_then(Value::as_object)
            .and_then(|x| x.get("name"))
            .and_then(Value::as_str)
            != Some("Completo")
        {
            continue;
        }
        let content_type = obj
            .get("assetType")
            .and_then(Value::as_str)
            .or_else(|| obj.get("contentType").and_then(Value::as_str));
        if content_type != Some("video") {
            continue;
        }
        let temp = obj
            .get("temporadaOrden")
            .and_then(Value::as_i64)
            .unwrap_or(0) as i32;
        let ep = obj.get("episode").and_then(Value::as_i64).unwrap_or(0) as i32;
        if temp != season || ep <= 0 {
            continue;
        }
        if let Some(wanted) = episode {
            if ep != wanted {
                continue;
            }
        }
        assets.push(SeriesAsset {
            asset_id: obj
                .get("id")
                .map(|v| v.to_string().trim_matches('"').to_string())
                .unwrap_or_default(),
            episode_url: obj
                .get("htmlUrl")
                .and_then(Value::as_str)
                .map(|s| s.to_string()),
            title: clean_text(
                obj.get("title")
                    .and_then(Value::as_str)
                    .or_else(|| obj.get("longTitle").and_then(Value::as_str))
                    .or_else(|| obj.get("shortTitle").and_then(Value::as_str)),
            ),
            short_description: clean_text(obj.get("shortDescription").and_then(Value::as_str)),
            description: clean_text(obj.get("description").and_then(Value::as_str)),
            season: Some(temp),
            episode: Some(ep),
            has_drm: obj.get("hasDRM").and_then(Value::as_bool).unwrap_or(false),
        });
    }
    assets.sort_by(|a, b| {
        (a.season.unwrap_or(0), a.episode.unwrap_or(0), &a.asset_id).cmp(&(
            b.season.unwrap_or(0),
            b.episode.unwrap_or(0),
            &b.asset_id,
        ))
    });
    if assets.is_empty() {
        bail!("no matching assets found for selector");
    }
    Ok(assets)
}

fn decode_rtve_source(item: &str) -> Option<String> {
    let (left, right) = item.split_once('#')?;
    let mut alphabet = String::new();
    let mut e = 0usize;
    let mut n = 0usize;
    for ch in left.chars() {
        if n == 0 {
            alphabet.push(ch);
            e = (e + 1) % 4;
            n = e;
        } else {
            n -= 1;
        }
    }
    let chars: Vec<char> = alphabet.chars().collect();
    let mut out = String::new();
    let mut a = 0usize;
    let mut n = 0usize;
    let mut s = 3usize;
    let mut h = 1usize;
    for ch in right.chars() {
        if n == 0 {
            a = 10 * ch.to_digit(10)? as usize;
            n = 1;
        } else if s == 0 {
            a += ch.to_digit(10)? as usize;
            if a < chars.len() {
                out.push(chars[a]);
            }
            s = (h + 3) % 4;
            n = 0;
            h += 1;
        } else {
            s -= 1;
        }
    }
    Some(out)
}

fn extract_rtve_urls_from_thumbnail_png(png_bytes: &[u8]) -> Vec<String> {
    let bytes = if !png_bytes.starts_with(b"\x89PNG")
        && png_bytes
            .get(..16)
            .map(|x| String::from_utf8_lossy(x).trim().starts_with("iVBOR"))
            .unwrap_or(false)
    {
        base64::engine::general_purpose::STANDARD
            .decode(png_bytes)
            .unwrap_or_else(|_| png_bytes.to_vec())
    } else {
        png_bytes.to_vec()
    };
    if bytes.len() < 8 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" {
        return Vec::new();
    }
    let mut off = 8usize;
    let mut urls = Vec::new();
    while off + 8 <= bytes.len() {
        let length =
            u32::from_be_bytes([bytes[off], bytes[off + 1], bytes[off + 2], bytes[off + 3]])
                as usize;
        let ctype = &bytes[off + 4..off + 8];
        off += 8;
        if off + length + 4 > bytes.len() {
            break;
        }
        let data = &bytes[off..off + length];
        off += length + 4;
        if ctype == b"tEXt" {
            let data2: Vec<u8> = data.iter().copied().filter(|b| *b != 0).collect();
            let mut h = String::from_utf8_lossy(&data2).to_string();
            if let Some((left, right)) = h.split_once('#') {
                if let Some((_, trimmed)) = right.split_once("%%") {
                    h = format!("{left}#{trimmed}");
                }
            }
            if let Some(url) = decode_rtve_source(&h) {
                urls.push(url);
            }
        }
        if ctype == b"IEND" {
            break;
        }
    }
    urls.sort();
    urls.dedup();
    urls
}

fn thumbnail_urls(asset_id: &str) -> [String; 2] {
    [
        format!("https://ztnr.rtve.es/ztnr/movil/thumbnail/rtveplayw/videos/{asset_id}.png?q=v2"),
        format!("https://ztnr.rtve.es/ztnr/movil/thumbnail/default/videos/{asset_id}.png"),
    ]
}

pub fn resolve_asset(asset_id: &str, http: &HttpClient) -> Result<ResolvedAsset> {
    let meta_url = format!("https://api-ztnr.rtve.es/api/videos/{asset_id}.json");
    let meta: Value = http.get_json(&meta_url)?;
    let item = meta
        .get("page")
        .and_then(|x| x.get("items"))
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .cloned()
        .or_else(|| meta.as_object().map(|_| meta.clone()))
        .context("unexpected meta payload")?;
    if item.get("hasDRM").and_then(Value::as_bool).unwrap_or(false) {
        debug(format!("resolve_asset: asset {asset_id} reports DRM flag; attempting public URL resolution anyway"));
    }

    let mut subtitles_es_vtt = None;
    let mut subtitles_en_vtt = None;
    for url in [
        format!("https://api2.rtve.es/api/videos/{asset_id}/subtitulos.json"),
        format!("https://www.rtve.es/api/videos/{asset_id}/subtitulos.json"),
    ] {
        let subs: Result<Value> = http.get_json(&url);
        if let Ok(data) = subs {
            if let Some(items) = data
                .get("page")
                .and_then(|x| x.get("items"))
                .and_then(Value::as_array)
            {
                for item in items {
                    let lang = item
                        .get("lang")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_lowercase();
                    let src = item
                        .get("src")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string());
                    if lang == "es" && subtitles_es_vtt.is_none() {
                        subtitles_es_vtt = src.clone();
                    }
                    if (lang == "en" || lang == "eng") && subtitles_en_vtt.is_none() {
                        subtitles_en_vtt = src.clone();
                    }
                }
            }
        }
    }

    let mut video_urls = Vec::new();
    for thumb in thumbnail_urls(asset_id) {
        let bytes = http.get_bytes(&thumb).unwrap_or_default();
        for url in extract_rtve_urls_from_thumbnail_png(&bytes) {
            if url.contains(".mpd") || url.contains("/tomcat/") {
                continue;
            }
            if !video_urls.contains(&url) {
                video_urls.push(url);
            }
        }
    }
    video_urls.sort_by(|a, b| {
        let sa = if a.ends_with(".m3u8") && a.contains("video.m3u8") {
            0
        } else if a.contains(".m3u8") {
            1
        } else {
            2
        };
        let sb = if b.ends_with(".m3u8") && b.contains("video.m3u8") {
            0
        } else if b.contains(".m3u8") {
            1
        } else {
            2
        };
        sa.cmp(&sb).then_with(|| a.cmp(b))
    });
    if video_urls.is_empty() {
        bail!("could not resolve video urls for asset {asset_id}");
    }

    Ok(ResolvedAsset {
        asset_id: asset_id.to_string(),
        title: clean_text(item.get("title").and_then(Value::as_str)),
        video_urls,
        subtitles_es_vtt,
        subtitles_en_vtt,
    })
}

pub fn base_from_asset(asset: &SeriesAsset) -> String {
    let title = asset
        .title
        .clone()
        .unwrap_or_else(|| asset.asset_id.clone())
        .to_lowercase();
    let re = Regex::new(r"[^a-z0-9]+").unwrap();
    let normalized = re.replace_all(&title, "_").trim_matches('_').to_string();
    format!(
        "S{:02}E{:02}_{}",
        asset.season.unwrap_or(0),
        asset.episode.unwrap_or(0),
        if normalized.is_empty() {
            "episode"
        } else {
            &normalized[..normalized.len().min(80)]
        }
    )
}

pub fn pick_video_url(urls: &[String], quality: &str) -> Result<String> {
    if quality == "mp4" {
        if let Some(url) = urls
            .iter()
            .find(|u| u.contains("rtve-mediavod-lote3.rtve.es") && u.contains(".mp4"))
        {
            return Ok(url.clone());
        }
        if let Some(url) = urls.iter().find(|u| u.contains(".mp4")) {
            return Ok(url.clone());
        }
    }
    urls.first().cloned().context("no video URLs")
}
